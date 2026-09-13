#!/usr/bin/env bash
# Read-only validation for Cilium's cluster-wide policy-audit preparation.
# It makes no Kubernetes, Talos, or filesystem changes.

set -euo pipefail

failures=0

pass() { printf 'PASS: %s\n' "$*"; }
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  failures=$((failures + 1))
}

usage() {
  cat <<'EOF'
Usage: scripts/cilium-policy-audit-health.sh

Validate that every Cilium agent has cluster-wide Policy Audit Mode enabled
while policy enforcement deliberately remains "never". The script uses the
active KUBECONFIG and is read-only.
EOF
}

case "${1:-}" in
  '') ;;
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

command -v kubectl >/dev/null 2>&1 || {
  printf '%s\n' 'kubectl is required.' >&2
  exit 1
}

node_count="$(kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sed '/^$/d' | wc -l | tr -d ' ')"
if [[ "$node_count" =~ ^[1-9][0-9]*$ ]]; then
  pass "Kubernetes reports $node_count node(s)."
else
  fail 'Kubernetes returned no nodes.'
fi

read -r desired available <<< "$(kubectl -n kube-system get daemonset cilium -o jsonpath='{.status.desiredNumberScheduled}{" "}{.status.numberAvailable}' 2>/dev/null || true)"
if [[ "$desired" == "$node_count" && "$available" == "$node_count" ]]; then
  pass "Cilium DaemonSet is fully available ($available/$desired)."
else
  fail "Cilium DaemonSet is not fully available (${available:-0}/${desired:-0}; expected $node_count/$node_count)."
fi

policy_enforcement="$(kubectl -n kube-system get configmap cilium-config -o go-template='{{index .data "enable-policy"}}' 2>/dev/null || true)"
policy_audit="$(kubectl -n kube-system get configmap cilium-config -o go-template='{{index .data "policy-audit-mode"}}' 2>/dev/null || true)"

if [[ "$policy_enforcement" == 'never' ]]; then
  pass 'Cilium policy enforcement remains deliberately set to never.'
else
  fail "Cilium policy enforcement is ${policy_enforcement:-<unset>}; expected never."
fi

if [[ "$policy_audit" == 'true' ]]; then
  pass 'Cilium ConfigMap enables Policy Audit Mode.'
else
  fail "Cilium ConfigMap policy-audit-mode is ${policy_audit:-<unset>}; expected true."
fi

agent_rows="$(kubectl -n kube-system get pods -l k8s-app=cilium --field-selector=status.phase=Running -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.nodeName}{"\n"}{end}' 2>/dev/null || true)"
agent_count=0
while IFS=$'\t' read -r pod node; do
  [[ -n "$pod" ]] || continue
  agent_count=$((agent_count + 1))
  if ! status="$(kubectl -n kube-system exec "$pod" -c cilium-agent -- cilium-dbg config 2>&1)"; then
    fail "Unable to read Cilium configuration from $pod on ${node:-<unknown-node>}."
    continue
  fi

  if grep -Eq '^PolicyAuditMode[[:space:]]*:[[:space:]]*Enabled[[:space:]]*$' <<< "$status"; then
    pass "Cilium agent $pod reports Policy Audit Mode Enabled."
  else
    fail "Cilium agent $pod does not report Policy Audit Mode Enabled."
  fi

  if grep -Eq '^PolicyEnforcement[[:space:]]*:[[:space:]]*never[[:space:]]*$' <<< "$status"; then
    pass "Cilium agent $pod reports policy enforcement never."
  else
    fail "Cilium agent $pod does not report policy enforcement never."
  fi
done <<< "$agent_rows"

if [[ "$agent_count" == "$node_count" ]]; then
  pass "Found one running Cilium agent on every node ($agent_count/$node_count)."
else
  fail "Found $agent_count running Cilium agents; expected $node_count."
fi

if ((failures > 0)); then
  printf 'Cilium policy-audit health check failed with %d issue(s). Do not proceed to an enforcement stage.\n' "$failures" >&2
  exit 1
fi

printf 'Cilium policy-audit health check passed. Traffic remains non-enforcing.\n'
