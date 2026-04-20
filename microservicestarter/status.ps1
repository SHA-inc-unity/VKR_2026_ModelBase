# =============================================================================
# microservicestarter - status.ps1
#
# Показывает состояние контейнеров для всех или выбранного сервиса.
#
# Использование:
#   .\status.ps1                                   - все сервисы
#   .\status.ps1 -Service microservice_analitic    - конкретный сервис
# =============================================================================

param(
    [string]$Service = "all"
)

$ErrorActionPreference = "Continue"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot   = Split-Path -Parent $ScriptDir
$ConfFile   = Join-Path $ScriptDir "services.conf"

function Write-Info { param($m) Write-Host "[starter] $m" -ForegroundColor Cyan }
function Write-Fail { param($m) Write-Host "[starter] ERROR: $m" -ForegroundColor Red; exit 1 }

if (-not (Get-Command "docker" -ErrorAction SilentlyContinue)) { Write-Fail "docker не найден." }

$ServicePaths = @{}
Get-Content $ConfFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -match '^\s*#' -or $line -eq '') { return }
    $parts = $line -split '\s+', 2
    if ($parts.Count -eq 2) { $ServicePaths[$parts[0]] = $parts[1] }
}

function Show-ServiceStatus {
    param([string]$Name)
    if (-not $ServicePaths.ContainsKey($Name)) { Write-Fail "Сервис '$Name' не найден в services.conf" }
    $SvcDir = Join-Path $RepoRoot $ServicePaths[$Name]
    if (-not (Test-Path $SvcDir)) { Write-Host "[$Name] Директория не найдена: $SvcDir"; return }

    Write-Info "[$Name] Состояние контейнеров:"
    Push-Location $SvcDir
    docker compose ps
    Pop-Location
}

if ($Service -eq "all") {
    foreach ($svc in $ServicePaths.Keys) { Show-ServiceStatus -Name $svc }
} else {
    Show-ServiceStatus -Name $Service
}