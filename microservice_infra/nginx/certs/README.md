# TLS certificates for the admin backend facade (port 8443)
#
# Place two files here for split-deployment mode:
#   tls.crt  — PEM certificate (chain)
#   tls.key  — PEM private key
#
# Generate a self-signed cert for internal/development use:
#   openssl req -x509 -newkey rsa:4096 -keyout tls.key -out tls.crt \
#     -days 365 -nodes -subj '/CN=modelline-backend'
#
# For production, use Let's Encrypt (certbot) or your CA.
# Point docker-compose to the cert directory via:
#   ADMIN_BACKEND_CERTS_DIR=/path/to/your/certs
#
# This directory is intentionally empty in source control.
# tls.crt and tls.key are git-ignored.
