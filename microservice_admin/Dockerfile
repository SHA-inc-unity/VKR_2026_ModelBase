# syntax=docker/dockerfile:1
# ── Build stage ───────────────────────────────────────────────────────────────
FROM node:22-alpine AS build
WORKDIR /app

RUN apk upgrade --no-cache

COPY package.json package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --prefer-offline

COPY . .
RUN npm run build

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production

RUN apk upgrade --no-cache && \
    addgroup -S appgroup && adduser -S appuser -G appgroup

COPY --chown=appuser:appgroup --from=build /app/.next/standalone ./
COPY --chown=appuser:appgroup --from=build /app/.next/static ./.next/static
COPY --chown=appuser:appgroup --from=build /app/public ./public

USER appuser

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME=0.0.0.0

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO /dev/null http://127.0.0.1:3000/admin || exit 1

ENTRYPOINT ["node", "server.js"]
