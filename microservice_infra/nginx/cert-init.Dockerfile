FROM alpine:3.20

RUN apk add --no-cache openssl

COPY nginx/cert-init.sh /usr/local/bin/cert-init.sh

ENTRYPOINT ["/bin/sh", "/usr/local/bin/cert-init.sh"]