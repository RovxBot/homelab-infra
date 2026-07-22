#!/usr/bin/env bash
# Read-only preflight for the staged Flannel-to-Cilium dual-overlay migration.
# It intentionally makes no Kubernetes, Talos, or filesystem changes.

set -euo pipefail

readonly EXPECTED_FLANNEL_CIDR="10.244.0.0/16"
readonly EXPECTED_CILIUM_CIDR="10.245.0.0/16"
readonly MINIMUM_KERNEL="5.10.0"

phase="before-install"
candidate_node=""
failures=0

usage() {
  cat <<'EOF'
Usage: scripts/cilium-preflight.sh [--secondary] [--node NODE]

  --secondary  Validate the cluster after the Cilium secondary overlay has
               reconciled, but before any node has the migration label.
  --node NODE  Report the current Longhorn replica and attachment load for a
               prospective migration node. This does not select or change it.

The script uses the active KUBECONFIG and TALOSCONFIG. It is read-only.
EOF
}

pass() {
  printf 'PASS: %s\n' "$*"
}

note() {
  printf 'NOTE: %s\n' "$*"
}

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  failures=$((failures + 1))
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "required command is unavailable: $1"
  fi
}

while (($# > 0)); do
  case "$1" in
    --secondary)
      phase="secondary"
      ;;
    --node)
      shift
      if (($# == 0)); then
        printf '%s\n' '--node requires a Kubernetes node name.' >&2
        exit 2
      fi
      candidate_node="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

for command in awk grep kubectl sort talosctl; do
  require_command "$command"
done

if ((failures > 0)); then
  exit 1
fi

node_rows="$(kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.conditions[?(@.type=="Ready")].status}{"\t"}{.status.nodeInfo.kernelVersion}{"\n"}{end}')"
if [[ -z "$node_rows" ]]; then
  fail 'Kubernetes returned no nodes.'
else
  while IFS=$'\t' read -r node ready kernel; do
    if [[ "$ready" != "True" ]]; then
      fail "$node is not Ready."
      continue
    fi

    kernel_version="${kernel%%-*}"
    if [[ "$(printf '%s\n%s\n' "$MINIMUM_KERNEL" "$kernel_version" | sort -V | head -n 1)" != "$MINIMUM_KERNEL" ]]; then
      fail "$node kernel $kernel is below Cilium's $MINIMUM_KERNEL minimum."
      continue
    fi
    pass "$node is Ready with kernel $kernel."
  done <<< "$node_rows"
fi

flannel_config="$(kubectl get configmap -n kube-system kube-flannel-cfg -o jsonpath='{.data.net-conf\.json}')"
if grep -Eq '"Network"[[:space:]]*:[[:space:]]*"10\.244\.0\.0/16"' <<< "$flannel_config"; then
  pass "Flannel pod CIDR is $EXPECTED_FLANNEL_CIDR."
else
  fail "Flannel pod CIDR is not the expected $EXPECTED_FLANNEL_CIDR."
fi

if grep -Eq '"Type"[[:space:]]*:[[:space:]]*"vxlan"' <<< "$flannel_config" \
  && grep -Eq '"Port"[[:space:]]*:[[:space:]]*4789' <<< "$flannel_config"; then
  pass 'Flannel uses VXLAN on UDP 4789; Cilium Geneve/6081 is distinct.'
else
  fail 'Flannel is not the expected VXLAN/4789 overlay.'
fi

kube_proxy_manifest="$(kubectl get daemonset -n kube-system kube-proxy -o yaml)"
kube_proxy_counts="$(kubectl get daemonset -n kube-system kube-proxy -o jsonpath='{.status.desiredNumberScheduled}{"\t"}{.status.numberAvailable}')"
IFS=$'\t' read -r kube_proxy_desired kube_proxy_available <<< "$kube_proxy_counts"
if [[ -n "$kube_proxy_desired" && "$kube_proxy_desired" == "$kube_proxy_available" ]] \
  && grep -Eq '^[[:space:]]*-[[:space:]]+--cluster-cidr=10\.244\.0\.0/16$' <<< "$kube_proxy_manifest" \
  && grep -Eq '^[[:space:]]*-[[:space:]]+--proxy-mode=nftables$' <<< "$kube_proxy_manifest"; then
  pass 'kube-proxy remains active in nftables mode.'
else
  fail 'kube-proxy is not in the expected active nftables configuration.'
fi

control_plane_ip="$(kubectl get nodes -l node-role.kubernetes.io/control-plane -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')"
if [[ -z "$control_plane_ip" ]]; then
  fail 'Unable to discover a control-plane InternalIP for the route check.'
elif routes="$(talosctl get routes --nodes "$control_plane_ip" 2>&1)"; then
  if [[ "$phase" == "before-install" ]] \
    && grep -Eq '(^|[^0-9])10\.245\.' <<< "$routes"; then
    fail "$EXPECTED_CILIUM_CIDR overlaps a route visible on $control_plane_ip."
  elif [[ "$phase" == "before-install" ]]; then
    pass "$EXPECTED_CILIUM_CIDR is not present in $control_plane_ip route output."
  elif grep -Eq '(^|[^0-9])10\.245\.' <<< "$routes"; then
    pass "$EXPECTED_CILIUM_CIDR is present in $control_plane_ip route output."
  else
    fail "The Cilium secondary overlay has no $EXPECTED_CILIUM_CIDR route visible on $control_plane_ip."
  fi
else
  fail "Unable to read routes from $control_plane_ip; check TALOSCONFIG and Talos access."
fi

volume_rows="$(kubectl get volumes.longhorn.io -n longhorn-system -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.robustness}{"\n"}{end}')"
unhealthy_volumes="$(awk '$2 != "healthy" { print $1 "=" $2 }' <<< "$volume_rows")"
if [[ -n "$unhealthy_volumes" ]]; then
  fail "Longhorn has non-healthy volumes: $unhealthy_volumes"
else
  pass 'Every Longhorn volume is healthy.'
fi

if [[ "$phase" == "before-install" ]]; then
  if kubectl get daemonset -n kube-system cilium >/dev/null 2>&1; then
    fail 'Cilium is already installed; re-run with --secondary for the next phase.'
  else
    pass 'Cilium is not installed yet.'
  fi
else
  cilium_counts="$(kubectl get daemonset -n kube-system cilium -o jsonpath='{.status.desiredNumberScheduled}{"\t"}{.status.numberAvailable}')"
  IFS=$'\t' read -r cilium_desired cilium_available <<< "$cilium_counts"
  if [[ -z "$cilium_desired" || "$cilium_desired" != "$cilium_available" ]]; then
    fail "Cilium DaemonSet is not fully available ($cilium_available/$cilium_desired)."
  else
    pass "Cilium DaemonSet is fully available ($cilium_available/$cilium_desired)."
  fi

  operator_counts="$(kubectl get deployment -n kube-system cilium-operator -o jsonpath='{.spec.replicas}{"\t"}{.status.availableReplicas}')"
  IFS=$'\t' read -r operator_desired operator_available <<< "$operator_counts"
  if [[ -z "$operator_desired" || "$operator_desired" != "$operator_available" ]]; then
    fail "Cilium Operator is not fully available ($operator_available/$operator_desired)."
  else
    pass "Cilium Operator is fully available ($operator_available/$operator_desired)."
  fi

  cilium_config="$(kubectl get configmap -n kube-system cilium-config -o json)"
  if grep -Eq '"custom-cni-conf"[[:space:]]*:[[:space:]]*"true"' <<< "$cilium_config" \
    && ! grep -Eq '"write-cni-conf-when-ready"[[:space:]]*:' <<< "$cilium_config"; then
    pass 'Cilium remains secondary: custom CNI configuration is enabled and no default CNI file is configured.'
  else
    fail 'Cilium does not have the expected secondary-overlay CNI safeguards.'
  fi

  if kubectl get ciliumnodeconfigs.cilium.io -n kube-system cilium-default >/dev/null 2>&1; then
    pass 'The CiliumNodeConfig migration selector exists.'
  else
    fail 'The CiliumNodeConfig migration selector is missing.'
  fi

  selected_nodes="$(kubectl get nodes -l io.cilium.migration/cilium-default=true --no-headers 2>/dev/null || true)"
  if [[ -n "$selected_nodes" ]]; then
    fail "Migration labels already exist; complete or roll back those nodes first: $selected_nodes"
  else
    pass 'No node has been selected for Cilium migration.'
  fi
fi

if [[ -n "$candidate_node" ]]; then
  if ! kubectl get node "$candidate_node" >/dev/null 2>&1; then
    fail "Candidate node does not exist: $candidate_node"
  else
    running_replicas="$(kubectl get replicas.longhorn.io -n longhorn-system -o jsonpath='{range .items[*]}{.spec.nodeID}{"\t"}{.status.currentState}{"\n"}{end}' | awk -v node="$candidate_node" '$1 == node && $2 == "running" { count++ } END { print count + 0 }')"
    attached_volumes="$(kubectl get volumes.longhorn.io -n longhorn-system -o jsonpath='{range .items[*]}{.status.currentNodeID}{"\n"}{end}' | awk -v node="$candidate_node" '$1 == node { count++ } END { print count + 0 }')"
    note "$candidate_node currently hosts $running_replicas running Longhorn replicas and $attached_volumes attached volumes."
  fi
fi

note 'Manually confirm that every node LAN address can exchange UDP/6081 before the secondary overlay is installed.'

if ((failures > 0)); then
  printf 'Cilium preflight failed with %d issue(s). Do not proceed.\n' "$failures" >&2
  exit 1
fi

printf 'Cilium preflight passed for phase: %s\n' "$phase"
