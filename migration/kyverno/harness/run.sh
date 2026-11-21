#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
# This entrypoint creates its own cluster. Never import an existing kubeconfig.
if [[ ${GITHUB_ACTIONS:-} != true && ${KYVERNO_REHEARSAL_LOCAL:-} != 1 ]]; then
  echo 'Use a disposable hosted runner or explicitly opt in to local kind.' >&2
  exit 2
fi
mkdir -p .work/cel/bin .work/cel/evidence
export PATH="$PWD/.work/cel/bin:$PATH"
export KUBECONFIG="$PWD/.work/cel/rehearsal.kubeconfig"
export REHEARSAL_CONTEXT=kind-kyverno-cel-rehearsal
if test -e "$KUBECONFIG"; then echo 'Refusing to reuse kubeconfig' >&2; exit 2; fi
fetch() { curl --fail --silent --show-error --location --connect-timeout 15 --max-time 120 --retry 2 "$1" -o "$2"; }
fetch https://github.com/kubernetes-sigs/kind/releases/download/v0.30.0/kind-linux-amd64 .work/cel/bin/kind
fetch https://github.com/kubernetes-sigs/kind/releases/download/v0.30.0/kind-linux-amd64.sha256sum .work/cel/kind.sha256
printf '%s  %s\n' "$(cut -d ' ' -f1 .work/cel/kind.sha256)" .work/cel/bin/kind | sha256sum -c -
chmod +x .work/cel/bin/kind
fetch https://dl.k8s.io/release/v1.34.0/bin/linux/amd64/kubectl .work/cel/bin/kubectl
fetch https://dl.k8s.io/release/v1.34.0/bin/linux/amd64/kubectl.sha256 .work/cel/kubectl.sha256
printf '%s  %s\n' "$(cat .work/cel/kubectl.sha256)" .work/cel/bin/kubectl | sha256sum -c -
chmod +x .work/cel/bin/kubectl
fetch https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz .work/cel/helm.tgz
fetch https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz.sha256sum .work/cel/helm.sha256
printf '%s  %s\n' "$(cut -d ' ' -f1 .work/cel/helm.sha256)" .work/cel/helm.tgz | sha256sum -c -
tar xzf .work/cel/helm.tgz -C .work/cel
cp .work/cel/linux-amd64/helm .work/cel/bin/helm
if [[ "$(kind get clusters 2>/dev/null)" == *kyverno-cel-rehearsal* ]]; then
  echo 'Refusing to reuse an existing cluster' >&2
  exit 2
fi
cleanup() {
  code=$?
  set +e
  if test -s "$KUBECONFIG"; then
    kubectl --context "$REHEARSAL_CONTEXT" --request-timeout=10s get pods -A -o wide > .work/cel/evidence/pods.txt 2>&1
    kubectl --context "$REHEARSAL_CONTEXT" --request-timeout=10s get events -A > .work/cel/evidence/events.txt 2>&1
    kubectl --context "$REHEARSAL_CONTEXT" --request-timeout=10s get validatingpolicies,policyexceptions.policies.kyverno.io,generatingpolicies -A -o json > .work/cel/evidence/cel-resources-final.json 2>&1
    for role in admission background cleanup reports; do
      kubectl --context "$REHEARSAL_CONTEXT" --request-timeout=10s logs -n kyverno -l app.kubernetes.io/component="$role-controller" --all-containers --tail=250 > ".work/cel/evidence/$role.log" 2>&1
    done
  fi
  timeout 60 kind delete cluster --name kyverno-cel-rehearsal > .work/cel/evidence/teardown.txt 2>&1
  exit "$code"
}
trap cleanup EXIT
trap 'exit 143' TERM INT
kind create cluster --name kyverno-cel-rehearsal --kubeconfig "$KUBECONFIG" --wait 120s \
  --image kindest/node:v1.34.0@sha256:7416a61b42b1662ca6ca89f02028ac133a309a2a30ba309614e8ec94d976dc5a
kubectl --context "$REHEARSAL_CONTEXT" get --raw /version > .work/cel/evidence/kubernetes.json
# Exact committed Flux v2.9.0 artifacts, including digest-pinned controllers.
# gotk-sync.yaml is never applied. There is no production GitRepository or Secret.
kubectl --context "$REHEARSAL_CONTEXT" apply --server-side -f clusters/home/flux-system/gotk-components.yaml
kubectl --context "$REHEARSAL_CONTEXT" -n flux-system rollout status deployment/source-controller --timeout=120s
kubectl --context "$REHEARSAL_CONTEXT" -n flux-system rollout status deployment/kustomize-controller --timeout=120s
python3 scripts/ci/check_kyverno_migration_inventory.py
python3 migration/kyverno/harness/build.py
git diff --exit-code -- migration/kyverno/policies migration/kyverno/exceptions migration/kyverno/policy-map.json
python3 -u migration/kyverno/harness/rehearse.py
