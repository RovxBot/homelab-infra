#!/usr/bin/env bash
# Create, verify, then remove a narrowly scoped HostPort test Deployment.
# It never touches Flux-managed resources or application workloads.

set -euo pipefail

readonly namespace="wotlk"
readonly deployment_prefix="cilium-hostport-probe-"
readonly app_label="cilium-hostport-probe"
readonly host_port="39080"
readonly image="nginxinc/nginx-unprivileged:1.31-alpine@sha256:59ccf0943b0b8e8d9e6ea9039a39555730f544701a655c596f7df7d096c593f5"

node=""
deployment=""

usage() {
  cat <<'EOF'
Usage: scripts/cilium-hostport-probe.sh --node NODE

Validate Cilium's portmap HostPort chain on one selected node. The script
creates a temporary, digest-pinned Deployment in the wotlk namespace on TCP
39080, verifies it from the administrator workstation, then removes it.

The workstation must be able to reach the node's LAN InternalIP. This is a
maintenance diagnostic, not a Flux-managed application deployment.
EOF
}

cleanup() {
  [[ -n "$deployment" ]] || return
  kubectl -n "$namespace" delete deployment "$deployment" \
    --ignore-not-found --wait=true >/dev/null 2>&1 || true
}

while (($# > 0)); do
  case "$1" in
    --node)
      shift
      (($# > 0)) || { printf '%s\n' '--node requires a Kubernetes node name.' >&2; exit 2; }
      node="$1"
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

[[ -n "$node" ]] || { usage >&2; exit 2; }
command -v curl >/dev/null 2>&1 || { printf '%s\n' 'curl is required.' >&2; exit 1; }
command -v kubectl >/dev/null 2>&1 || { printf '%s\n' 'kubectl is required.' >&2; exit 1; }

node_ip="$(kubectl get node "$node" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}')"
[[ "$node_ip" =~ ^192\.168\.1\.[0-9]{1,3}$ ]] || {
  printf 'Node %s does not report a LAN InternalIP: %s\n' "$node" "${node_ip:-<none>}" >&2
  exit 1
}

trap cleanup EXIT

deployment="$(kubectl -n "$namespace" create -o name -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  generateName: $deployment_prefix
  labels:
    app: $app_label
    homelab.cooked.beer/owner: platform
spec:
  replicas: 1
  revisionHistoryLimit: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: $app_label
  template:
    metadata:
      labels:
        app: $app_label
        homelab.cooked.beer/owner: platform
    spec:
      automountServiceAccountToken: false
      nodeSelector:
        kubernetes.io/hostname: $node
      securityContext:
        runAsNonRoot: true
        runAsUser: 101
        runAsGroup: 101
        fsGroup: 101
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: probe
          image: $image
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8080
              hostPort: $host_port
              hostIP: $node_ip
              protocol: TCP
          readinessProbe:
            tcpSocket:
              port: http
            periodSeconds: 2
            failureThreshold: 30
          livenessProbe:
            tcpSocket:
              port: http
            periodSeconds: 5
            failureThreshold: 6
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop:
                - ALL
            seccompProfile:
              type: RuntimeDefault
          resources:
            requests:
              cpu: 10m
              memory: 16Mi
            limits:
              cpu: 100m
              memory: 64Mi
EOF
)"
deployment="${deployment#deployment.apps/}"

kubectl -n "$namespace" rollout status "deployment/$deployment" --timeout=180s
curl --fail --silent --show-error --max-time 10 "http://$node_ip:$host_port/" >/dev/null
printf 'PASS: Cilium portmap HostPort is reachable at %s:%s on %s.\n' \
  "$node_ip" "$host_port" "$node"
