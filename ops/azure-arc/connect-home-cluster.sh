#!/usr/bin/env bash
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"

ACTION="${1:-}"

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-arc-homelab-au-east}"
CLUSTER_NAME="${CLUSTER_NAME:-arc-home-talos}"
LOCATION="${LOCATION:-australiaeast}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-}"
SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-}"
ONBOARDING_TIMEOUT="${ONBOARDING_TIMEOUT:-600}"
TAGS="${TAGS:-environment=homelab cluster=home management=azure-arc gitops=flux phase=inventory-only}"

readonly REQUIRED_PROVIDERS=(
  "Microsoft.Kubernetes"
  "Microsoft.KubernetesConfiguration"
  "Microsoft.ExtendedLocation"
)

log() {
  printf '[azure-arc] %s\n' "$*"
}

die() {
  printf '[azure-arc] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  connect-home-cluster.sh preflight
  connect-home-cluster.sh connect
  connect-home-cluster.sh status
  connect-home-cluster.sh delete

Environment overrides:
  RESOURCE_GROUP     Azure resource group name (default: rg-arc-homelab-au-east)
  CLUSTER_NAME       Arc connected cluster name (default: arc-home-talos)
  LOCATION           Azure region (default: australiaeast)
  KUBE_CONTEXT       kubeconfig context to target (optional but recommended)
  KUBECONFIG_PATH    custom kubeconfig path (optional)
  SUBSCRIPTION_ID    Azure subscription id/name (optional)
  ONBOARDING_TIMEOUT Timeout in seconds for az connectedk8s connect (default: 600)
  TAGS               Space-separated Azure tags (default: environment=homelab ...)
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

az_base() {
  local args=()
  if [[ -n "${SUBSCRIPTION_ID}" ]]; then
    args+=(--subscription "${SUBSCRIPTION_ID}")
  fi
  ((${#args[@]})) && printf '%s\n' "${args[@]}"
}

kubectl_args() {
  local args=()
  if [[ -n "${KUBECONFIG_PATH}" ]]; then
    args+=(--kubeconfig "${KUBECONFIG_PATH}")
  fi
  if [[ -n "${KUBE_CONTEXT}" ]]; then
    args+=(--context "${KUBE_CONTEXT}")
  fi
  ((${#args[@]})) && printf '%s\n' "${args[@]}"
}

run_az() {
  local args=()
  mapfile -t args < <(az_base)
  az "$@" "${args[@]}"
}

run_kubectl() {
  local args=()
  mapfile -t args < <(kubectl_args)
  kubectl "${args[@]}" "$@"
}

append_connect_target_args() {
  local -n out_ref="$1"
  if [[ -n "${KUBECONFIG_PATH}" ]]; then
    out_ref+=(--kube-config "${KUBECONFIG_PATH}")
  fi
  if [[ -n "${KUBE_CONTEXT}" ]]; then
    out_ref+=(--kube-context "${KUBE_CONTEXT}")
  fi
}

ensure_action() {
  case "${ACTION}" in
    preflight|connect|status|delete) ;;
    *)
      usage
      exit 1
      ;;
  esac
}

ensure_local_prereqs() {
  require_cmd az
  require_cmd kubectl

  if ! az account show >/dev/null 2>&1; then
    die "Azure CLI is not logged in. Run 'az login' first."
  fi

  if [[ -n "${SUBSCRIPTION_ID}" ]]; then
    log "setting Azure subscription to ${SUBSCRIPTION_ID}"
    az account set --subscription "${SUBSCRIPTION_ID}"
  fi

  if [[ -n "${KUBE_CONTEXT}" ]]; then
    run_kubectl config get-contexts "${KUBE_CONTEXT}" >/dev/null
  else
    log "KUBE_CONTEXT is not set; using kubectl's current context"
  fi

  run_kubectl version >/dev/null
  az extension add --name connectedk8s --upgrade --only-show-errors >/dev/null
}

check_cluster_shape() {
  log "checking cluster node architectures"
  local nodes
  nodes="$(run_kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.nodeInfo.architecture}{"\n"}{end}')"
  printf '%s\n' "${nodes}"

  if ! grep -q $'\tamd64' <<<"${nodes}"; then
    die "no linux/amd64 nodes found; Arc onboarding needs at least one amd64 node for broad extension support"
  fi

  log "checking node capacity with metrics-server if available"
  if ! run_kubectl top nodes; then
    log "kubectl top nodes failed; metrics-server may be unavailable. Continue after a manual capacity check."
  fi
}

check_endpoints_from_operator() {
  require_cmd curl

  local endpoints=(
    "https://management.azure.com"
    "https://login.microsoftonline.com"
    "https://mcr.microsoft.com"
    "https://${LOCATION}.dp.kubernetesconfiguration.azure.com"
  )

  log "checking Arc control-plane endpoints from the operator machine"
  local endpoint
  local status
  for endpoint in "${endpoints[@]}"; do
    printf '  - %s\n' "${endpoint}"
    status="$(curl -sSIL -o /dev/null -w '%{http_code}' --max-time 20 "${endpoint}" || true)"
    if [[ -z "${status}" || "${status}" == "000" ]]; then
      die "unable to reach ${endpoint} from the operator machine"
    fi
  done

  cat <<EOF
[azure-arc] NOTE: this verifies the operator machine can reach core Arc endpoints.
[azure-arc] Cluster-side egress should still be checked separately before onboarding if
[azure-arc] your node network policy or firewall path differs from the operator machine.
EOF
}

register_resource_providers() {
  log "registering required Azure resource providers"
  local provider
  for provider in "${REQUIRED_PROVIDERS[@]}"; do
    run_az provider register --namespace "${provider}" --wait --only-show-errors >/dev/null
  done
}

ensure_resource_group() {
  log "ensuring resource group ${RESOURCE_GROUP} exists in ${LOCATION}"
  run_az group create --name "${RESOURCE_GROUP}" --location "${LOCATION}" --tags ${TAGS} --only-show-errors >/dev/null
}

connect_cluster() {
  log "connecting cluster ${CLUSTER_NAME} to Azure Arc"
  local args=(
    connectedk8s connect
    --resource-group "${RESOURCE_GROUP}"
    --name "${CLUSTER_NAME}"
    --location "${LOCATION}"
    --distribution generic
    --infrastructure generic
    --onboarding-timeout "${ONBOARDING_TIMEOUT}"
    --tags
  )
  local tag
  for tag in ${TAGS}; do
    args+=("${tag}")
  done
  append_connect_target_args args
  args+=(--yes)

  run_az "${args[@]}"
}

show_status() {
  log "Azure connected cluster resource"
  run_az connectedk8s show --resource-group "${RESOURCE_GROUP}" --name "${CLUSTER_NAME}" --output table

  log "Arc namespace and agents"
  run_kubectl get namespace azure-arc
  run_kubectl get deployments,pods -n azure-arc

  log "Arc extensions on the connected cluster"
  run_az k8s-extension list \
    --cluster-type connectedClusters \
    --resource-group "${RESOURCE_GROUP}" \
    --cluster-name "${CLUSTER_NAME}" \
    --output table

  log "Flux kustomizations still present"
  run_kubectl get kustomizations.kustomize.toolkit.fluxcd.io -A
}

delete_cluster() {
  log "disconnecting cluster ${CLUSTER_NAME} from Azure Arc"
  local args=(
    connectedk8s delete
    --resource-group "${RESOURCE_GROUP}"
    --name "${CLUSTER_NAME}"
    --yes
  )
  append_connect_target_args args
  run_az "${args[@]}"
}

main() {
  ensure_action
  ensure_local_prereqs

  case "${ACTION}" in
    preflight)
      check_cluster_shape
      check_endpoints_from_operator
      register_resource_providers
      ;;
    connect)
      check_cluster_shape
      check_endpoints_from_operator
      register_resource_providers
      ensure_resource_group
      connect_cluster
      show_status
      ;;
    status)
      show_status
      ;;
    delete)
      delete_cluster
      ;;
  esac
}

main
