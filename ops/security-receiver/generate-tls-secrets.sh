#!/usr/bin/env bash
# One-time, LOCAL operator bootstrap. Does not touch Kubernetes or Flux.
# Outputs only SOPS-encrypted Kubernetes Secret manifests for manual GitOps review.
set -euo pipefail
for tool in openssl kubectl sops; do
  command -v "$tool" >/dev/null || { echo "Missing required local tool: $tool" >&2; exit 1; }
done
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
OUT="$ROOT/.work/security-receiver-pki"
umask 077
if [[ -e "$OUT/root.key" || -e secrets/security-receiver-tls.enc.yaml || -e secrets/security-receiver-ca.enc.yaml ]]; then
  echo "Existing TLS identity detected; refusing to overwrite or rotate it." >&2
  exit 1
fi
mkdir -p "$OUT"
openssl genrsa -out "$OUT/root.key" 3072
openssl req -new -x509 -sha256 -days 3650 \
  -key "$OUT/root.key" -out "$OUT/root.crt" \
  -subj "/CN=Homelab Kubescape Receiver Root" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign"
openssl genrsa -out "$OUT/tls.key" 3072
openssl req -new -sha256 -key "$OUT/tls.key" -out "$OUT/tls.csr" \
  -subj "/CN=security-receiver.kubescape.svc"
cat > "$OUT/tls.ext" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:security-receiver.kubescape.svc,DNS:security-receiver.kubescape.svc.cluster.local
# Python 3.13+ enables X509_STRICT and requires the issuer's key identifier.
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF
openssl x509 -req -sha256 -days 825 \
  -in "$OUT/tls.csr" -CA "$OUT/root.crt" -CAkey "$OUT/root.key" \
  -CAcreateserial -out "$OUT/tls.crt" -extfile "$OUT/tls.ext"
# Check the same strict chain + hostname validation used by Python's TLS client.
for hostname in security-receiver.kubescape.svc security-receiver.kubescape.svc.cluster.local; do
  openssl verify -x509_strict -purpose sslserver -verify_hostname "$hostname" \
    -CAfile "$OUT/root.crt" "$OUT/tls.crt"
done
# The CA Secret contains ONLY a PUBLIC CA certificate. The leaf private key
# remains exclusively inside the encrypted receiver-tls Secret and local .work.
kubectl -n kubescape create secret tls security-receiver-tls \
  --cert="$OUT/tls.crt" --key="$OUT/tls.key" \
  --dry-run=client -o yaml > secrets/security-receiver-tls.enc.yaml
kubectl -n kubescape create secret generic security-receiver-ca \
  --from-file=receiver-ca.crt="$OUT/root.crt" \
  --dry-run=client -o yaml > secrets/security-receiver-ca.enc.yaml
sops --encrypt --in-place secrets/security-receiver-tls.enc.yaml
sops --encrypt --in-place secrets/security-receiver-ca.enc.yaml
echo "SOPS secrets created; review the files and commit ONLY the .enc.yaml outputs."
echo "Keep $OUT/root.key backed up securely OFF repository; never publish plaintext key or certificate request."
echo "Do not activate the Helm exporter until the receiver and CA Secrets are deployed and validated."
