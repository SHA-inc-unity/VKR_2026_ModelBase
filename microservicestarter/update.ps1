# =============================================================================
# microservicestarter - update.ps1
#
# Только git pull — без перезапуска контейнеров.
# Используйте restart.ps1 если нужен полный цикл обновления.
#
# Использование:
#   .\update.ps1   - git pull в корне репозитория
# =============================================================================

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir

function Write-Info  { param($m) Write-Host "[starter] $m" -ForegroundColor Cyan   }
function Write-Ok    { param($m) Write-Host "[starter] $m" -ForegroundColor Green  }
function Write-Warn  { param($m) Write-Host "[starter] $m" -ForegroundColor Yellow }
function Write-Fail  { param($m) Write-Host "[starter] ERROR: $m" -ForegroundColor Red; exit 1 }

if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) { Write-Fail "git не найден." }

Write-Info "git pull — загружаем последние изменения из репозитория..."
Push-Location $RepoRoot
git pull
if ($LASTEXITCODE -eq 0) {
    Write-Ok "Репозиторий обновлён."
} else {
    Write-Warn "git pull завершился с ошибкой."
}
Pop-Location
Write-Info "Для применения изменений запустите: .\restart.ps1 [-Service service|all]"