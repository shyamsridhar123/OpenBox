#!/usr/bin/env bash
# generate-certs.sh — self-signed CA + server cert for kata-webhook
# SAN: kata-webhook.opensandbox-system.svc
# Works with OpenSSL on Linux/macOS/Git Bash (Windows).
set -euo pipefail

# Disable Git Bash / MSYS path conversion so -subj strings aren't mangled
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

OUT_DIR="${1:-.}"
mkdir -p "$OUT_DIR"

CA_KEY="$OUT_DIR/ca.key"
CA_CRT="$OUT_DIR/ca.crt"
SRV_KEY="$OUT_DIR/tls.key"
SRV_CSR="$OUT_DIR/tls.csr"
SRV_CRT="$OUT_DIR/tls.crt"
EXT_FILE="$OUT_DIR/san.ext"

SVC_DNS="kata-webhook.opensandbox-system.svc"

echo "==> Generating CA key + cert..."
openssl genrsa -out "$CA_KEY" 4096 2>/dev/null
openssl req -x509 -new -nodes \
  -key "$CA_KEY" \
  -sha256 -days 3650 \
  -subj "/CN=kata-webhook-ca/O=opensandbox" \
  -out "$CA_CRT"

echo "==> Generating server key + CSR..."
openssl genrsa -out "$SRV_KEY" 2048 2>/dev/null
openssl req -new \
  -key "$SRV_KEY" \
  -subj "/CN=${SVC_DNS}/O=opensandbox" \
  -out "$SRV_CSR"

echo "==> Writing SAN extension..."
cat > "$EXT_FILE" <<EOF
[req]
req_extensions = v3_req
[v3_req]
basicConstraints = CA:FALSE
keyUsage = nonRepudiation, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names
[alt_names]
DNS.1 = ${SVC_DNS}
DNS.2 = kata-webhook.opensandbox-system
DNS.3 = kata-webhook
EOF

echo "==> Signing server cert with CA..."
openssl x509 -req \
  -in "$SRV_CSR" \
  -CA "$CA_CRT" \
  -CAkey "$CA_KEY" \
  -CAcreateserial \
  -out "$SRV_CRT" \
  -days 3650 \
  -sha256 \
  -extensions v3_req \
  -extfile "$EXT_FILE"

# Cleanup intermediates
rm -f "$SRV_CSR" "$EXT_FILE" "$OUT_DIR/ca.srl"

echo ""
echo "==> Done. Files in $OUT_DIR:"
ls -lh "$OUT_DIR/ca.crt" "$OUT_DIR/tls.crt" "$OUT_DIR/tls.key"
echo ""
echo "==> Verifying SANs in tls.crt:"
openssl x509 -in "$SRV_CRT" -noout -text | grep -A1 "Subject Alternative Name"

echo ""
echo "==> CA bundle (base64, for caBundle in MutatingWebhookConfiguration):"
base64 < "$CA_CRT" | tr -d '\n'
echo ""
