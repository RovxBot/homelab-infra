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
fetch https://tuf-repo-cdn.sigstore.dev/15.root.json .work/cel/trust/official-root.json
python3 migration/kyverno/harness/resolve.py ${KYVERNO_CANDIDATE_CHART:+--chart "$KYVERNO_CANDIDATE_CHART"}
version=$(jq -er .application .work/cel/trust/target.json)
chart=$(jq -er .chart .work/cel/trust/target.json)
source_commit=$(jq -er .source_commit .work/cel/trust/target.json)
issuer=https://token.actions.githubusercontent.com
identity=$(jq -er .identity .work/cel/trust/target.json)
base=https://github.com/kyverno/kyverno/releases/download/v$version
assets=("kyverno-cli_v${version}_linux_x86_64.tar.gz")
if [[ $(jq -r .chart_signed .work/cel/trust/target.json) == true ]]; then assets+=("kyverno-${chart}.tgz"); fi
for asset in "${assets[@]}"; do
  fetch "$base/$asset" ".work/cel/trust/$asset"
  fetch "$base/$asset.sigstore.json" ".work/cel/trust/$asset.sigstore.json"
  timeout 120 cosign verify-blob --bundle ".work/cel/trust/$asset.sigstore.json" \
    --certificate-identity "$identity" --certificate-oidc-issuer "$issuer" \
    ".work/cel/trust/$asset" > ".work/cel/trust/$asset.verification.json" 2> ".work/cel/trust/$asset.verification.log"
done
archive=".work/cel/trust/kyverno-cli_v${version}_linux_x86_64.tar.gz"
printf '%s  %s\n' "$(jq -r .cli_sha256 .work/cel/trust/target.json)" "$archive" | sha256sum -c -
tar xzf "$archive" -C .work/cel/bin kyverno
.work/cel/bin/kyverno version | tee .work/cel/trust/cli-version.txt
python3 - "$version" "$source_commit" <<'PYVERSION'
import pathlib, sys
lines=pathlib.Path('.work/cel/trust/cli-version.txt').read_text().splitlines()
assert any(line.removeprefix('Version: ').removeprefix('v') == sys.argv[1] for line in lines)
assert 'Git commit ID: '+sys.argv[2] in lines
PYVERSION
if [[ ${TRUST_SCOPE:-all} == cli ]]; then printf 'verified\n' > .work/cel/trust/status; exit 0; fi
# All runtime images used by the chart, including hooks. Pin verified digests below.
for image in kyverno kyvernopre background-controller cleanup-controller reports-controller kyverno-cli readiness-checker; do
  COSIGN_REPOSITORY=ghcr.io/kyverno/signatures timeout 120 cosign verify \
    --certificate-identity "$identity" --certificate-oidc-issuer "$issuer" \
    --certificate-github-workflow-sha "$source_commit" \
    -a "ref=$source_commit" \
    "ghcr.io/kyverno/$image:v$version" > ".work/cel/trust/$image.signature.json" 2> ".work/cel/trust/$image.signature.log"
  digest=$(jq -er '.[0].critical.image["docker-manifest-digest"]' ".work/cel/trust/$image.signature.json")
  timeout 120 cosign verify-attestation --type slsaprovenance \
    --certificate-identity https://github.com/slsa-framework/slsa-github-generator/.github/workflows/generator_container_slsa3.yml@refs/tags/v2.1.0 \
    --certificate-oidc-issuer "$issuer" "ghcr.io/kyverno/$image@$digest" \
    > ".work/cel/trust/$image.provenance.json" 2> ".work/cel/trust/$image.provenance.log"
done
python3 migration/kyverno/harness/trust-summary.py
printf 'verified\n' > .work/cel/trust/status
