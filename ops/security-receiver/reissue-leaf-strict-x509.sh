#!/usr/bin/env bash
# Reissue ONLY the existing receiver TLS leaf using the original CA and leaf
# private keys. No Kubernetes mutations, no CA/key rotation, no plaintext
# secrets committed. Flux applies the reviewed SOPS Secret and Pod restart.
set -euo pipefail
umask 077

for tool in openssl kubectl sops git base64 cmp mktemp; do
  command -v "$tool" >/dev/null || {
    printf 'Missing required command: %s\n' "$tool" >&2
    exit 1
  }
done

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
OUT="$ROOT/.work/security-receiver-pki"
SECRET="secrets/security-receiver-tls.enc.yaml"
CA_SECRET="secrets/security-receiver-ca.enc.yaml"
DEPLOYMENT="infra/kubescape/receiver/workload.yaml"

for file in "$OUT/root.key" "$OUT/root.crt" "$OUT/tls.key" "$SECRET" "$CA_SECRET"; do
  [[ -s "$file" ]] || { printf 'Missing original PKI input: %s\n' "$file" >&2; exit 1; }
done
git diff --quiet -- "$SECRET" "$CA_SECRET" || {
  echo "Receiver SOPS Secrets already have uncommitted changes; review first." >&2
  exit 1
}
grep -Fq 'homelab.cooked.beer/tls-leaf-reissue: "strict-x509-aki-20261124-01"' "$DEPLOYMENT" || {
  echo "Check out the reviewed TLS leaf reissue branch before running this script." >&2
  exit 1
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Read only public certificates from Kubernetes to prevent accidental CA
# mismatch, old-key mismatch or a wrong-cluster rotation. Never fetch tls.key.
kubectl -n kubescape get secret security-receiver-ca \
  -o go-template='{{index .data "receiver-ca.crt"}}' \
  | base64 --decode > "$TMP/live-ca.crt"
kubectl -n kubescape get secret security-receiver-tls \
  -o jsonpath='{.data.tls\.crt}' \
  | base64 --decode > "$TMP/live-leaf.crt"

cmp -s \
  <(openssl x509 -in "$OUT/root.crt" -outform DER) \
  <(openssl x509 -in "$TMP/live-ca.crt" -outform DER) || {
    echo "Local root CA differs from deployed public CA. Refusing to reissue." >&2
    exit 1
  }
cmp -s \
  <(openssl pkey -in "$OUT/root.key" -pubout -outform DER) \
  <(openssl x509 -in "$OUT/root.crt" -pubkey -noout | openssl pkey -pubin -outform DER) || {
    echo "Local CA key does not match deployed CA certificate." >&2
    exit 1
  }
cmp -s \
  <(openssl pkey -in "$OUT/tls.key" -pubout -outform DER) \
  <(openssl x509 -in "$TMP/live-leaf.crt" -pubkey -noout | openssl pkey -pubin -outform DER) || {
    echo "Local leaf key does not match deployed leaf certificate." >&2
    exit 1
  }
openssl verify -CAfile "$OUT/root.crt" "$TMP/live-leaf.crt" >/dev/null || {
  echo "Deployed leaf does not validate against the current CA." >&2
  exit 1
}

openssl req -new -sha256 -key "$OUT/tls.key" \
  -out "$TMP/leaf.csr" -subj "/CN=security-receiver.kubescape.svc"
cat > "$TMP/leaf.ext" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:security-receiver.kubescape.svc,DNS:security-receiver.kubescape.svc.cluster.local
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF

openssl x509 -req -sha256 -days 825 \
  -in "$TMP/leaf.csr" -CA "$OUT/root.crt" -CAkey "$OUT/root.key" \
  -set_serial "0x$(openssl rand -hex 16)" \
  -out "$TMP/leaf.crt" -extfile "$TMP/leaf.ext"

# The failure seen in Python 3.15 was strict X.509 error 85 (missing AKI).
# Check strict chain + purpose + exact DNS names before touching ciphertext.
for host in security-receiver.kubescape.svc security-receiver.kubescape.svc.cluster.local; do
  openssl verify -x509_strict -purpose sslserver \
    -verify_hostname "$host" -CAfile "$OUT/root.crt" "$TMP/leaf.crt"
done

# Preserve the encrypted TLS private key and all other secret metadata.
# Set only the base64-encoded PUBLIC leaf certificate inside existing SOPS
# ciphertext. No plaintext Secret YAML is written to disk.
sops set "$SECRET" '["data"]["tls.crt"]' \
  "\"$(base64 -w 0 "$TMP/leaf.crt")\""

grep -Fq 'tls.crt: ENC[' "$SECRET" &&
grep -Fq 'tls.key: ENC[' "$SECRET" || {
  echo "SOPS encryption check failed: DO NOT COMMIT or merge." >&2
  exit 1
}

echo "Strict X.509 verification passed for both receiver service DNS names."
echo "Updated ONLY the existing SOPS-encrypted receiver leaf certificate."
echo "The original CA and both private keys were preserved."
echo "Review and commit $SECRET to this SAME PR before merging."
echo "Flux will replace the receiver Pod once (Recreate) using the same PVC."
git status --short -- "$SECRET" "$CA_SECRET" "$DEPLOYMENT"
