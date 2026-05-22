# syntax=docker/dockerfile:1
# ── Build stage ───────────────────────────────────────────────────────────────
FROM node:22-bookworm-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

COPY package.json package-lock.json* ./
RUN --mount=type=cache,id=microservice_admin_npm_cache,target=/root/.npm,sharing=locked \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_PROGRESS=false \
    NPM_CONFIG_MAXSOCKETS=1 \
    NODE_OPTIONS=--max-old-space-size=384 \
    npm ci --prefer-offline --no-audit --no-fund

COPY . .
RUN mkdir -p public
RUN --mount=type=cache,id=microservice_admin_next_cache,target=/app/.next/cache,sharing=locked \
    NODE_COMPILE_CACHE=/app/.next/cache/node-compile-cache \
    npm run build

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM node:22-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN mkdir -p /app/.runtime-data && chown -R node:node /app/.runtime-data

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
