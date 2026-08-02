#!/usr/bin/env bash
# Read-only steady-state validation for the primary Cilium CNI.
# It intentionally makes no Kubernetes, Talos, or filesystem changes.

set -euo pipefail

readonly EXPECTED_CILIUM_CIDR="10.245.0.0/16"
readonly EXPECTED_NODE_LAN_CIDR="192.168.1.0/24"
readonly EXPECTED_CILIUM_CONFLIST="/etc/cni/net.d/05-cilium.conflist"

candidate_node=""
failures=0
declare -A node_ips=()
declare -A cilium_node_ips=()

usage() {
  cat <<'EOF'
Usage: scripts/cilium-primary-health.sh [--node NODE]

Validate the completed primary-Cilium operating state. The script uses the
active KUBECONFIG and TALOSCONFIG and is read-only.

  --node NODE  Also report Longhorn replica and attachment load for a
               prospective maintenance node. It never changes that node.
EOF
}

pass() { printf 'PASS: %s\n' "$*"; }
note() { printf 'NOTE: %s\n' "$*"; }
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  failures=$((failures + 1))
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

is_cilium_ipv4() {
  local ip="$1"
  local octet3 octet4
  [[ "$ip" =~ ^10\.245\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  octet3="${BASH_REMATCH[1]}"
  octet4="${BASH_REMATCH[2]}"
  ((10#$octet3 <= 255 && 10#$octet4 <= 255))
}

is_lan_ipv4() {
  local ip="$1"
  local octet4
  [[ "$ip" =~ ^192\.168\.1\.([0-9]{1,3})$ ]] || return 1
  octet4="${BASH_REMATCH[1]}"
  ((10#$octet4 <= 255))
}

check_flux_kind() {
  local namespace="$1" kind="$2" name ready names
  names="$(kubectl -n "$namespace" get "$kind" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)"
  if [[ -z "$names" ]]; then
    note "no Flux $kind resources are installed."
    return
  fi
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    ready="$(kubectl -n "$namespace" get "$kind" "$name" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
    [[ "$ready" == "True" ]] && pass "Flux $namespace/$kind/$name is Ready." || fail "Flux $namespace/$kind/$name is not Ready."
  done <<< "$names"
}

while (($# > 0)); do
  case "$1" in
    --node)
      shift
      (($# > 0)) || { printf '%s\n' '--node requires a Kubernetes node name.' >&2; exit 2; }
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

for command in awk grep kubectl talosctl; do
  require_command "$command"
done
((failures == 0)) || exit 1

node_rows="$(kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.conditions[?(@.type=="Ready")].status}{"\t"}{.status.addresses[?(@.type=="InternalIP")].address}{"\n"}{end}')"
node_count=0
while IFS=$'\t' read -r node ready internal_ip; do
  [[ -n "$node" ]] || continue
  node_count=$((node_count + 1))
  node_ips["$node"]="$internal_ip"
  if [[ "$ready" != "True" ]]; then
    fail "$node is not Ready."
  elif ! is_lan_ipv4 "$internal_ip"; then
    fail "$node reports InternalIP ${internal_ip:-<none>}; expected $EXPECTED_NODE_LAN_CIDR."
  else
    pass "$node is Ready with LAN InternalIP $internal_ip."
  fi
done <<< "$node_rows"
((node_count > 0)) || fail 'Kubernetes returned no nodes.'

cilium_counts="$(kubectl -n kube-system get daemonset cilium -o jsonpath='{.status.desiredNumberScheduled}{"\t"}{.status.numberAvailable}' 2>/dev/null || true)"
IFS=$'\t' read -r cilium_desired cilium_available <<< "$cilium_counts"
if [[ "$cilium_desired" == "$node_count" && "$cilium_available" == "$node_count" ]]; then
  pass "Cilium DaemonSet is fully available ($cilium_available/$cilium_desired)."
else
  fail "Cilium DaemonSet is not fully available (${cilium_available:-0}/${cilium_desired:-0}; expected $node_count/$node_count)."
fi

kube_proxy_counts="$(kubectl -n kube-system get daemonset kube-proxy -o jsonpath='{.status.desiredNumberScheduled}{"\t"}{.status.numberAvailable}' 2>/dev/null || true)"
IFS=$'\t' read -r kube_proxy_desired kube_proxy_available <<< "$kube_proxy_counts"
if [[ "$kube_proxy_desired" == "$node_count" && "$kube_proxy_available" == "$node_count" ]]; then
  pass "kube-proxy is fully available ($kube_proxy_available/$kube_proxy_desired)."
else
  fail "kube-proxy is not fully available (${kube_proxy_available:-0}/${kube_proxy_desired:-0}; expected $node_count/$node_count)."
fi

operator_counts="$(kubectl -n kube-system get deployment cilium-operator -o jsonpath='{.spec.replicas}{"\t"}{.status.availableReplicas}' 2>/dev/null || true)"
IFS=$'\t' read -r operator_desired operator_available <<< "$operator_counts"
if [[ -n "${operator_desired:-}" && "$operator_desired" == "$operator_available" ]]; then
  pass "Cilium Operator is fully available ($operator_available/$operator_desired)."
else
  fail "Cilium Operator is not fully available (${operator_available:-0}/${operator_desired:-0})."
fi

cilium_config="$(kubectl -n kube-system get configmap cilium-config -o go-template='{{printf "%s\t%s\t%s\t%s" (index .data "custom-cni-conf") (index .data "write-cni-conf-when-ready") (index .data "cni-exclusive") (index .data "enable-policy")}}' 2>/dev/null || true)"
IFS=$'\t' read -r custom_cni_conf write_cni_conf cni_exclusive policy_enforcement <<< "$cilium_config"
if [[ "$custom_cni_conf" == "false" && "$write_cni_conf" == "/host$EXPECTED_CILIUM_CONFLIST" && "$cni_exclusive" == "true" ]]; then
  pass 'Cilium ConfigMap has the expected primary-CNI settings.'
else
  fail 'Cilium ConfigMap does not have the expected primary-CNI settings.'
fi
[[ "$policy_enforcement" == "never" ]] && pass 'Cilium policy enforcement remains intentionally disabled.' || fail "Cilium policy enforcement is ${policy_enforcement:-<unset>}."

for resource in 'daemonset kube-flannel' 'configmap kube-flannel-cfg' 'serviceaccount flannel'; do
  read -r kind name <<< "$resource"
  if kubectl -n kube-system get "$kind" "$name" >/dev/null 2>&1; then
    fail "residual Flannel resource exists: kube-system/$kind/$name."
  else
    pass "Flannel resource is absent: kube-system/$kind/$name."
  fi
done
for resource in 'clusterrole flannel' 'clusterrolebinding flannel'; do
  read -r kind name <<< "$resource"
  if kubectl get "$kind" "$name" >/dev/null 2>&1; then
    fail "residual Flannel resource exists: $kind/$name."
  else
    pass "Flannel resource is absent: $kind/$name."
  fi
done

if kubectl -n kube-system get ciliumnodeconfigs.cilium.io cilium-default >/dev/null 2>&1; then
  fail 'The temporary CiliumNodeConfig migration selector still exists.'
else
  pass 'The temporary CiliumNodeConfig migration selector is absent.'
fi
migration_nodes="$(kubectl get nodes -l io.cilium.migration/cilium-default=true -o name 2>/dev/null || true)"
[[ -z "$migration_nodes" ]] && pass 'No Cilium migration labels remain.' || fail "Cilium migration labels remain: $migration_nodes"

cilium_node_rows="$(kubectl get ciliumnodes.cilium.io -o go-template='{{range .items}}{{ $name := .metadata.name }}{{range .spec.addresses}}{{if eq .type "InternalIP"}}{{printf "%s\t%s\n" $name .ip}}{{end}}{{end}}{{end}}' 2>/dev/null || true)"
while IFS=$'\t' read -r node internal_ip; do
  [[ -n "$node" ]] || continue
  cilium_node_ips["$node"]="$internal_ip"
  if ! is_lan_ipv4 "$internal_ip"; then
    fail "$node CiliumNode InternalIP $internal_ip is outside $EXPECTED_NODE_LAN_CIDR."
  elif [[ "${node_ips[$node]:-}" != "$internal_ip" ]]; then
    fail "$node CiliumNode InternalIP $internal_ip does not match Kubernetes Node InternalIP ${node_ips[$node]:-<missing>}."
  else
    pass "$node CiliumNode transport address matches LAN InternalIP $internal_ip."
  fi
done <<< "$cilium_node_rows"
for node in "${!node_ips[@]}"; do
  [[ -n "${cilium_node_ips[$node]:-}" ]] || fail "$node has no CiliumNode InternalIP."
  if talosctl read "$EXPECTED_CILIUM_CONFLIST" --nodes "${node_ips[$node]}" >/dev/null 2>&1; then
    pass "$node exposes $EXPECTED_CILIUM_CONFLIST through Talos."
  else
    fail "$node does not expose $EXPECTED_CILIUM_CONFLIST through Talos."
  fi
done

cilium_pod="$(kubectl -n kube-system get pods -l k8s-app=cilium --field-selector=status.phase=Running -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null | awk 'NR == 1 {print; exit}')"
if [[ -z "$cilium_pod" ]]; then
  fail 'No running Cilium agent Pod is available for peer-health validation.'
elif ! cilium_health="$(kubectl -n kube-system exec "$cilium_pod" -c cilium-agent -- cilium-health status 2>&1)"; then
  fail "Unable to query Cilium peer health from $cilium_pod."
elif grep -Eq "Cluster health:[[:space:]]*${node_count}/${node_count}[[:space:]]+reachable" <<< "$cilium_health"; then
  pass "Cilium peer health is $node_count/$node_count reachable."
else
  summary="$(grep -m 1 'Cluster health:' <<< "$cilium_health" || true)"
  fail "Cilium peer health is incomplete: ${summary:-no cluster-health summary returned}."
fi

workload_rows="$(kubectl get pods -A -o go-template='{{range .items}}{{if and (eq .status.phase "Running") (not .spec.hostNetwork)}}{{printf "%s/%s\t%s\n" .metadata.namespace .metadata.name .status.podIP}}{{end}}{{end}}' 2>/dev/null || true)"
bad_workloads="$(awk -F $'\t' '$2 !~ /^10\.245\.([0-9]{1,3})\.([0-9]{1,3})$/ {print $1 "=" $2}' <<< "$workload_rows")"
[[ -z "$bad_workloads" ]] && pass "All running non-hostNetwork workloads use $EXPECTED_CILIUM_CIDR." || fail "Non-Cilium workload IPs: $bad_workloads"

longhorn_nodes="$(kubectl -n longhorn-system get nodes.longhorn.io -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.conditions[?(@.type=="Ready")].status}{"\t"}{.status.conditions[?(@.type=="Schedulable")].status}{"\n"}{end}' 2>/dev/null || true)"
while IFS=$'\t' read -r node ready schedulable; do
  [[ -n "$node" ]] || continue
  if [[ "$ready" == "True" && "$schedulable" == "True" ]]; then
    pass "Longhorn node $node is Ready and Schedulable."
  else
    fail "Longhorn node $node is not Ready/Schedulable ($ready/$schedulable)."
  fi
done <<< "$longhorn_nodes"
volume_rows="$(kubectl -n longhorn-system get volumes.longhorn.io -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.robustness}{"\n"}{end}' 2>/dev/null || true)"
unhealthy_volumes="$(awk '$2 != "healthy" {print $1 "=" $2}' <<< "$volume_rows")"
[[ -z "$unhealthy_volumes" ]] && pass 'Every Longhorn volume is healthy.' || fail "Longhorn has non-healthy volumes: $unhealthy_volumes"

check_flux_kind flux-system kustomizations.kustomize.toolkit.fluxcd.io
check_flux_kind kube-system helmreleases.helm.toolkit.fluxcd.io

kube_proxy_args="$(kubectl -n kube-system get daemonset kube-proxy -o go-template='{{range .spec.template.spec.containers}}{{if eq .name "kube-proxy"}}{{range .args}}{{printf "%s\n" .}}{{end}}{{end}}{{end}}' 2>/dev/null || true)"
if grep -Fxq -- '--cluster-cidr=10.245.0.0/16' <<< "$kube_proxy_args"; then
  pass 'kube-proxy cluster CIDR matches the primary Cilium Pod CIDR.'
elif grep -Fxq -- '--cluster-cidr=10.244.0.0/16' <<< "$kube_proxy_args"; then
  note 'kube-proxy still reports retired 10.244.0.0/16; review this separately before changing Talos or kube-proxy configuration.'
else
  note 'kube-proxy cluster CIDR is not the expected Cilium CIDR; review its source of truth separately.'
fi

if [[ -n "$candidate_node" ]]; then
  if ! kubectl get node "$candidate_node" >/dev/null 2>&1; then
    fail "Candidate node does not exist: $candidate_node"
  else
    running_replicas="$(kubectl -n longhorn-system get replicas.longhorn.io -o jsonpath='{range .items[*]}{.spec.nodeID}{"\t"}{.status.currentState}{"\n"}{end}' | awk -v node="$candidate_node" '$1 == node && $2 == "running" {count++} END {print count + 0}')"
    attached_volumes="$(kubectl -n longhorn-system get volumes.longhorn.io -o jsonpath='{range .items[*]}{.status.currentNodeID}{"\n"}{end}' | awk -v node="$candidate_node" '$1 == node {count++} END {print count + 0}')"
    note "$candidate_node currently hosts $running_replicas running Longhorn replicas and $attached_volumes attached volumes."
  fi
fi

if ((failures > 0)); then
  printf 'Cilium primary health check failed with %d issue(s). Do not proceed with maintenance.\n' "$failures" >&2
  exit 1
fi
printf 'Cilium primary health check passed.\n'
