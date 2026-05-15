# syntax=docker/dockerfile:1
# ── Build stage ───────────────────────────────────────────────────────────────
FROM cgr.dev/chainguard/node:latest AS build
WORKDIR /app

COPY --chown=node:node package.json package-lock.json* ./
RUN --mount=type=cache,target=/home/node/.npm \
    npm ci --prefer-offline

COPY --chown=node:node . .
RUN npm run build

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM cgr.dev/chainguard/node:latest-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production

COPY --chown=node:node --from=build /app/.next/standalone ./
COPY --chown=node:node --from=build /app/.next/static ./.next/static
COPY --chown=node:node --from=build /app/public ./public

USER node

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME=0.0.0.0

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/admin').then((response) => process.exit(response.ok ? 0 : 1)).catch(() => process.exit(1))"]

ENTRYPOINT ["node", "server.js"]
