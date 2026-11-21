#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
mkdir -p .work/cel/trust .work/cel/bin
export PATH="$PWD/.work/cel/bin:$PATH"
fetch() { curl --fail --silent --show-error --location --connect-timeout 15 --max-time 120 --retry 2 "$1" -o "$2"; }
fetch https://github.com/sigstore/cosign/releases/download/v3.1.4/cosign-linux-amd64 .work/cel/bin/cosign
printf '%s  %s\n' 424e2c97d0b45fd7a6b608a87f090f35633f4c23ece5f092d48bba11aaf4bbfa .work/cel/bin/cosign | sha256sum -c -
chmod +x .work/cel/bin/cosign
# Use a fresh official TUF root, never a workstation mirror or cached trust store.
export XDG_CACHE_HOME="$PWD/.work/cel/trust/cache"
mkdir -p "$XDG_CACHE_HOME"
fetch https://tuf-repo-cdn.sigstore.dev/root.json .work/cel/trust/official-root.json
issuer=https://token.actions.githubusercontent.com
identity=https://github.com/kyverno/kyverno/.github/workflows/release-publish.yaml@refs/tags/v1.20.0
base=https://github.com/kyverno/kyverno/releases/download/v1.20.0
for asset in kyverno-3.10.0.tgz kyverno-cli_v1.20.0_linux_x86_64.tar.gz; do
  fetch "$base/$asset" ".work/cel/trust/$asset"
  fetch "$base/$asset.sigstore.json" ".work/cel/trust/$asset.sigstore.json"
  timeout 120 cosign verify-blob --bundle ".work/cel/trust/$asset.sigstore.json" \
    --certificate-identity "$identity" --certificate-oidc-issuer "$issuer" \
    ".work/cel/trust/$asset" > ".work/cel/trust/$asset.verification.json" 2> ".work/cel/trust/$asset.verification.log"
done
# All runtime images used by the chart, including hooks. Pin verified digests below.
for image in kyverno kyvernopre background-controller cleanup-controller reports-controller kyverno-cli readiness-checker; do
  COSIGN_REPOSITORY=ghcr.io/kyverno/signatures timeout 120 cosign verify \
    --certificate-identity "$identity" --certificate-oidc-issuer "$issuer" \
    --certificate-github-workflow-sha f666af06e9bd310c7e730f1523c0430e90371a89 \
    -a ref=f666af06e9bd310c7e730f1523c0430e90371a89 \
    "ghcr.io/kyverno/$image:v1.20.0" > ".work/cel/trust/$image.signature.json" 2> ".work/cel/trust/$image.signature.log"
  digest=$(jq -er '.[0].critical.image["docker-manifest-digest"]' ".work/cel/trust/$image.signature.json")
  timeout 120 cosign verify-attestation --type slsaprovenance \
    --certificate-identity https://github.com/slsa-framework/slsa-github-generator/.github/workflows/generator_container_slsa3.yml@refs/tags/v2.1.0 \
    --certificate-oidc-issuer "$issuer" "ghcr.io/kyverno/$image@$digest" \
    > ".work/cel/trust/$image.provenance.json" 2> ".work/cel/trust/$image.provenance.log"
done
printf 'verified\n' > .work/cel/trust/status
