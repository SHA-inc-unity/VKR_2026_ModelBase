# ── Stage 1: build ─────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /repo

# Copy solution and project files first for better layer caching
COPY GatewayService.sln global.json ./
COPY src/GatewayService.API/GatewayService.API.csproj ./src/GatewayService.API/

RUN dotnet restore src/GatewayService.API/GatewayService.API.csproj

COPY . .

RUN dotnet publish src/GatewayService.API/GatewayService.API.csproj \
    -c Release \
    -o /app/publish \
    --no-restore \
    /p:UseAppHost=false

# ── Stage 2: runtime ───────────────────────────────────────────────────────────
FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
WORKDIR /app

# Non-root user for security
RUN addgroup --system appgroup \
 && adduser --system --ingroup appgroup appuser
USER appuser

COPY --from=build /app/publish .

EXPOSE 5020
ENV ASPNETCORE_URLS=http://+:5020

ENTRYPOINT ["dotnet", "GatewayService.API.dll"]
