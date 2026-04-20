# =============================================================================
# microservicestarter - start.ps1
#
# Запускает один или все микросервисы.
#
# Использование:
#   .\start.ps1                                     - все сервисы (core)
#   .\start.ps1 -Service microservice_analitic      - конкретный сервис
#   .\start.ps1 -Service all                        - все сервисы
#   .\start.ps1 -Service microservice_analitic -Mode full    - core + scheduler
#   .\start.ps1 -Service microservice_analitic -Mode build   - пересборка + запуск
#   .\start.ps1 -Service microservice_analitic -Mode logs    - live-логи
# =============================================================================

param(
    [string]$Service = "all",
    [ValidateSet("core","full","scheduler","build","logs","")]
    [string]$Mode = "core"
)

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot   = Split-Path -Parent $ScriptDir
$ConfFile   = Join-Path $ScriptDir "services.conf"

function Write-Info  { param($m) Write-Host "[starter] $m" -ForegroundColor Cyan   }
function Write-Ok    { param($m) Write-Host "[starter] $m" -ForegroundColor Green  }
function Write-Warn  { param($m) Write-Host "[starter] $m" -ForegroundColor Yellow }
function Write-Fail  { param($m) Write-Host "[starter] ERROR: $m" -ForegroundColor Red; exit 1 }

if (-not (Get-Command "docker" -ErrorAction SilentlyContinue)) { Write-Fail "docker не найден." }
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Fail "Docker daemon не запущен. Запустите Docker Desktop." }

# Загружаем services.conf
$ServicePaths = @{}
Get-Content $ConfFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -match '^\s*#' -or $line -eq '') { return }
    $parts = $line -split '\s+', 2
    if ($parts.Count -eq 2) { $ServicePaths[$parts[0]] = $parts[1] }
}

function Start-Microservice {
    param([string]$Name, [string]$RunMode)
    if (-not $ServicePaths.ContainsKey($Name)) { Write-Fail "Сервис '$Name' не найден в services.conf" }
    $SvcDir = Join-Path $RepoRoot $ServicePaths[$Name]
    if (-not (Test-Path $SvcDir)) { Write-Fail "Директория не найдена: $SvcDir" }

    Write-Info "[$Name] Запуск (mode=$RunMode)..."
    Push-Location $SvcDir

    if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
        Copy-Item ".env.example" ".env"
        Write-Warn "[$Name] .env создан из .env.example. Укажите PGPASSWORD."
    }

    $BaseTag = "${Name}-base:latest"
    $baseExists = docker image inspect $BaseTag 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Info "[$Name] Сборка base-образа (первый раз, ~2 мин)..."
        docker compose --profile build-base build base
        if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Сборка base-образа провалилась." }
        Write-Ok "[$Name] Base-образ готов."
    }

    switch ($RunMode) {
        "build"     { docker compose build --no-cache; docker compose up -d }
        "full"      { docker compose --profile scheduler up -d }
        "scheduler" { docker compose --profile scheduler up -d scheduler }
        "logs"      { docker compose logs -f }
        default     { docker compose up -d }
    }
    if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Запуск провалился." }

    Pop-Location
    Write-Ok "[$Name] Запущен."
}

if ($Service -eq "all") {
    foreach ($svc in $ServicePaths.Keys) { Start-Microservice -Name $svc -RunMode $Mode }
} else {
    Start-Microservice -Name $Service -RunMode $Mode
}