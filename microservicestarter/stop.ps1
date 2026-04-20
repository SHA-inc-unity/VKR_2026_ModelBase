# =============================================================================
# microservicestarter - stop.ps1
#
# Останавливает один или все микросервисы.
#
# Использование:
#   .\stop.ps1                                          - остановить все
#   .\stop.ps1 -Service microservice_analitic           - конкретный сервис
#   .\stop.ps1 -Service all                             - все сервисы
#   .\stop.ps1 -Service microservice_analitic -Mode clean  - остановить + удалить volumes
#   .\stop.ps1 -Service microservice_analitic -Mode prune  - остановить + удалить образы
# =============================================================================

param(
    [string]$Service = "all",
    [ValidateSet("stop","clean","prune","")]
    [string]$Mode = "stop"
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
if ($LASTEXITCODE -ne 0) { Write-Fail "Docker daemon не запущен." }

$ServicePaths = @{}
Get-Content $ConfFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -match '^\s*#' -or $line -eq '') { return }
    $parts = $line -split '\s+', 2
    if ($parts.Count -eq 2) { $ServicePaths[$parts[0]] = $parts[1] }
}

function Stop-Microservice {
    param([string]$Name, [string]$RunMode)
    if (-not $ServicePaths.ContainsKey($Name)) { Write-Fail "Сервис '$Name' не найден в services.conf" }
    $SvcDir = Join-Path $RepoRoot $ServicePaths[$Name]
    if (-not (Test-Path $SvcDir)) { Write-Fail "Директория не найдена: $SvcDir" }

    Write-Info "[$Name] Остановка (mode=$RunMode)..."
    Push-Location $SvcDir

    switch ($RunMode) {
        "clean" {
            Write-Warn "[$Name] ВНИМАНИЕ: будут удалены все volumes (БД, модели)!"
            $confirm = Read-Host "Подтвердите (yes/no)"
            if ($confirm -ne "yes") { Write-Host "Отменено."; Pop-Location; return }
            docker compose --profile scheduler down --volumes --remove-orphans
            Write-Ok "[$Name] Остановлен, volumes удалены."
        }
        "prune" {
            docker compose --profile scheduler down --rmi local --remove-orphans
            Write-Ok "[$Name] Остановлен, образы удалены."
        }
        default {
            docker compose --profile scheduler down --remove-orphans
            Write-Ok "[$Name] Остановлен."
        }
    }

    Pop-Location
}

if ($Service -eq "all") {
    foreach ($svc in $ServicePaths.Keys) { Stop-Microservice -Name $svc -RunMode $Mode }
} else {
    Stop-Microservice -Name $Service -RunMode $Mode
}