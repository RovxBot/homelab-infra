#!/usr/bin/env bash
# Replace the Gatus GitHub alert token without writing its plaintext to disk.
#
# The repository's .sops.yaml contains the public age recipient needed for
# encryption. Decryption remains restricted to Flux in the cluster.

set -euo pipefail
set +x

readonly target_rel='secrets/gatus-github-token.enc.yaml'

if ! command -v sops >/dev/null 2>&1; then
  printf '%s\n' 'sops is required. On Arch: sudo pacman -S --needed sops' >&2
  exit 1
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" || ! -f "$repo_root/.sops.yaml" ]]; then
  printf '%s\n' 'Run this script from inside the homelab-infra repository.' >&2
  exit 1
fi

cd "$repo_root"

if [[ ! -f "$target_rel" ]]; then
  printf 'Expected encrypted Secret is missing: %s\n' "$target_rel" >&2
  exit 1
fi

umask 077
encrypted_tmp="$(mktemp "${TMPDIR:-/tmp}/gatus-github-token.XXXXXX")"
trap 'unset token 2>/dev/null || true; rm -f "$encrypted_tmp"' EXIT HUP INT TERM

printf '%s' 'Enter the new fine-grained Gatus GitHub token (input hidden): ' >&2
IFS= read -r -s token
printf '\n' >&2

if [[ -z "$token" || "$token" =~ [[:space:]] ]]; then
  printf '%s\n' 'The token must be non-empty and contain no whitespace.' >&2
  exit 1
fi

if ! {
  printf '%s\n' 'apiVersion: v1'
  printf '%s\n' 'kind: Secret'
  printf '%s\n' 'metadata:'
  printf '%s\n' '  name: gatus-github-token'
  printf '%s\n' '  namespace: monitoring'
  printf '%s\n' 'type: Opaque'
  printf '%s\n' 'stringData:'
  printf '  token: %s\n' "$token"
} | sops encrypt \
  --filename-override "$target_rel" \
  --input-type yaml \
  --output-type yaml >"$encrypted_tmp"; then
  printf '%s\n' 'SOPS encryption failed; the existing encrypted Secret was not changed.' >&2
  exit 1
fi
unset token

if ! grep -Eq '^[[:space:]]+token: ENC\[AES256_GCM,' "$encrypted_tmp" || ! grep -q '^sops:' "$encrypted_tmp"; then
  printf '%s\n' 'Unexpected SOPS output; the existing encrypted Secret was not changed.' >&2
  exit 1
fi

mv -f "$encrypted_tmp" "$target_rel"
printf '%s\n' 'Encrypted Gatus Secret updated. Review only the ciphertext diff, then create the rollout PR.'
