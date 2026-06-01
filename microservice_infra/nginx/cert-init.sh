#!/bin/sh
set -eu

cert_dir="${CERT_DIR:-/certs}"
cert_file="${cert_dir}/tls.crt"
key_file="${cert_dir}/tls.key"
common_name="${CERT_COMMON_NAME:-modelline-backend}"
valid_days="${CERT_VALID_DAYS:-3650}"
# SAN so the admin head can verify the cert against the host it dials
# (ADMIN_BACKEND_BASE_URL=https://95.165.27.159:8443) instead of disabling TLS.
cert_san="${CERT_SAN:-IP:95.165.27.159,DNS:modelline-backend,DNS:localhost}"

if [ -s "$cert_file" ] && [ -s "$key_file" ]; then
  echo "[cert-init] reusing existing TLS certificate in $cert_dir"
  exit 0
fi

mkdir -p "$cert_dir"
rm -f "$cert_file" "$key_file"

umask 077
openssl req -x509 -newkey rsa:4096 -sha256 -nodes \
  -keyout "$key_file" \
  -out "$cert_file" \
  -days "$valid_days" \
  -subj "/CN=${common_name}" \
  -addext "subjectAltName=${cert_san}"

chmod 644 "$cert_file"
echo "[cert-init] generated self-signed TLS certificate in $cert_dir"