#!/usr/bin/env bash
# Read-only preflight for the staged Flannel-to-Cilium dual-overlay migration.
# It intentionally makes no Kubernetes, Talos, or filesystem changes.

set -euo pipefail

readonly EXPECTED_FLANNEL_CIDR="10.244.0.0/16"
readonly EXPECTED_CILIUM_CIDR="10.245.0.0/16"
readonly EXPECTED_NODE_LAN_CIDR="192.168.1.0/24"
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

is_expected_cilium_ipv4() {
  local ip="$1"
  local octet3 octet4

  [[ "$ip" =~ ^10\.245\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  octet3="${BASH_REMATCH[1]}"
  octet4="${BASH_REMATCH[2]}"
  ((10#$octet3 <= 255 && 10#$octet4 <= 255))
}

is_expected_node_lan_ipv4() {
  local ip="$1"
  local octet4

  [[ "$ip" =~ ^192\.168\.1\.([0-9]{1,3})$ ]] || return 1
  octet4="${BASH_REMATCH[1]}"
  ((10#$octet4 <= 255))
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

node_internal_ip_rows="$(kubectl get nodes -o go-template='{{range .items}}{{ $name := .metadata.name }}{{range .status.addresses}}{{if eq .type "InternalIP"}}{{printf "%s\t%s\n" $name .address}}{{end}}{{end}}{{end}}')"
if [[ -z "$node_internal_ip_rows" ]]; then
  fail 'Kubernetes returned no Node InternalIP addresses.'
else
  while IFS=$'\t' read -r node internal_ip; do
    if is_expected_cilium_ipv4 "$internal_ip"; then
      fail "$node reports InternalIP $internal_ip inside $EXPECTED_CILIUM_CIDR; repair Talos kubelet node-IP selection before CNI migration."
    elif is_expected_node_lan_ipv4 "$internal_ip"; then
      pass "$node reports the expected LAN InternalIP $internal_ip."
    else
      fail "$node reports unexpected InternalIP $internal_ip; expected $EXPECTED_NODE_LAN_CIDR before CNI migration."
    fi
  done <<< "$node_internal_ip_rows"
fi

if ! kubernetes_api_endpoints="$(kubectl -n default get endpointslice \
  -l kubernetes.io/service-name=kubernetes \
  -o go-template='{{range .items}}{{ $addressType := .addressType }}{{range .endpoints}}{{if and (eq $addressType "IPv4") (eq .conditions.ready true)}}{{index .addresses 0}}{{"\n"}}{{end}}{{end}}{{end}}' \
  | sort -u)"; then
  fail 'Unable to discover ready IPv4 default/kubernetes EndpointSlice endpoints.'
elif [[ -z "$kubernetes_api_endpoints" ]]; then
  fail 'default/kubernetes has no ready IPv4 EndpointSlice endpoints.'
else
  talos_route_target=""
  routes=""

  while IFS= read -r endpoint; do
    [[ -n "$endpoint" ]] || continue

    if is_expected_cilium_ipv4 "$endpoint"; then
      fail "default/kubernetes endpoint $endpoint overlaps $EXPECTED_CILIUM_CIDR."
      continue
    fi

    if routes="$(talosctl get routes --nodes "$endpoint" 2>&1)"; then
      talos_route_target="$endpoint"
      break
    fi

    note "default/kubernetes endpoint $endpoint did not answer Talos; trying the next endpoint."
  done <<< "$kubernetes_api_endpoints"

  if [[ -z "$talos_route_target" ]]; then
    fail 'Unable to read routes through any ready default/kubernetes endpoint; check TALOSCONFIG and Talos access.'
  elif [[ "$phase" == "before-install" ]] \
    && grep -Eq '(^|[^0-9])10\.245\.' <<< "$routes"; then
    fail "$EXPECTED_CILIUM_CIDR overlaps a route visible on $talos_route_target."
  elif [[ "$phase" == "before-install" ]]; then
    pass "$EXPECTED_CILIUM_CIDR is not present in $talos_route_target route output."
  elif grep -Eq '(^|[^0-9])10\.245\.' <<< "$routes"; then
    pass "$EXPECTED_CILIUM_CIDR is present in $talos_route_target route output."
  else
    fail "The Cilium secondary overlay has no $EXPECTED_CILIUM_CIDR route visible on $talos_route_target."
  fi
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

  # Cilium learns Geneve transport addresses from CiliumNode InternalIP values.
  # These must agree with the Kubernetes Node LAN addresses before the first
  # workload node is switched to Cilium. A stale overlay address here makes the
  # secondary agents appear Ready while their peer mesh is unreachable.
  if ! cilium_node_internal_ip_rows="$(kubectl get ciliumnodes.cilium.io -o go-template='{{range .items}}{{ $name := .metadata.name }}{{range .spec.addresses}}{{if eq .type "InternalIP"}}{{printf "%s\t%s\n" $name .ip}}{{end}}{{end}}{{end}}')"; then
    fail 'Unable to read CiliumNode InternalIP addresses.'
  elif [[ -z "$cilium_node_internal_ip_rows" ]]; then
    fail 'Cilium returned no CiliumNode InternalIP addresses.'
  else
    while IFS=$'\t' read -r node internal_ip; do
      cilium_internal_ip="$(awk -F $'\t' -v target="$node" '$1 == target { print $2 }' <<< "$cilium_node_internal_ip_rows")"

      if [[ -z "$cilium_internal_ip" ]]; then
        fail "$node has no CiliumNode InternalIP."
      elif ! is_expected_node_lan_ipv4 "$cilium_internal_ip"; then
        fail "$node CiliumNode InternalIP $cilium_internal_ip is outside $EXPECTED_NODE_LAN_CIDR."
      elif [[ "$cilium_internal_ip" != "$internal_ip" ]]; then
        fail "$node CiliumNode InternalIP $cilium_internal_ip does not match Kubernetes Node InternalIP $internal_ip."
      else
        pass "$node CiliumNode transport address matches LAN InternalIP $internal_ip."
      fi
    done <<< "$node_internal_ip_rows"
  fi

  cilium_health_pod="$(kubectl -n kube-system get pod -l k8s-app=cilium -o jsonpath='{.items[0].metadata.name}')"
  if [[ -z "$cilium_health_pod" ]]; then
    fail 'No Cilium agent Pod is available for peer-health validation.'
  elif ! cilium_health_status="$(kubectl -n kube-system exec "$cilium_health_pod" -c cilium-agent -- cilium-health status 2>&1)"; then
    fail "Unable to query Cilium peer health from $cilium_health_pod."
  elif grep -Eq "Cluster health:[[:space:]]*${cilium_desired}/${cilium_desired}[[:space:]]+reachable" <<< "$cilium_health_status"; then
    pass "Cilium peer health is ${cilium_desired}/${cilium_desired} reachable."
  else
    health_summary="$(grep -m 1 'Cluster health:' <<< "$cilium_health_status" || true)"
    fail "Cilium peer health is incomplete: ${health_summary:-no cluster-health summary returned}."
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
