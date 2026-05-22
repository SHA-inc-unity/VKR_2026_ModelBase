# ── Build stage ──────────────────────────────────────────────────────────────
# syntax=docker/dockerfile:1
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src

# Copy only project file first — layer cache survives source-only changes
COPY DataService.sln global.json ./
COPY src/DataService.API/DataService.API.csproj src/DataService.API/
RUN --mount=type=cache,target=/root/.nuget/packages \
    dotnet restore src/DataService.API/DataService.API.csproj

# Now copy source — only this layer is rebuilt on code changes
COPY . .
RUN --mount=type=cache,target=/root/.nuget/packages \
    dotnet publish src/DataService.API/DataService.API.csproj \
    -c Release \
    -o /app/publish \
    --no-restore

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends wget && rm -rf /var/lib/apt/lists/*

RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
USER appuser

COPY --from=build /app/publish .

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q --spider http://localhost:8100/health || exit 1

ENV ASPNETCORE_URLS=http://+:8100
ENV ASPNETCORE_ENVIRONMENT=Production

ENTRYPOINT ["dotnet", "DataService.API.dll"]
