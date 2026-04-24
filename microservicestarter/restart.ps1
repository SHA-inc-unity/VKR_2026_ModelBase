# =============================================================================
# microservicestarter - restart.ps1
#
# git pull + перезапускает один или все микросервисы.
#
# Использование:
#   .\restart.ps1                                                 - git pull + все сервисы
#   .\restart.ps1 -Service microservice_analitic                  - git pull + конкретный
#   .\restart.ps1 -Service all                                    - git pull + все
#   .\restart.ps1 -Service microservice_analitic -Mode full       - core + scheduler
#   .\restart.ps1 -Service microservice_analitic -Mode api        - только api
#   .\restart.ps1 -Service microservice_analitic -Mode deps       - пересобрать base
#   .\restart.ps1 -Service microservice_analitic -Mode postgres   - только postgres (без rebuild)
#   .\restart.ps1 -Service microservice_analitic -Mode redis      - только redis (без rebuild)
# =============================================================================

param(
    [string]$Service = "all",
    [ValidateSet("core","full","api","deps","postgres","redis","")]
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

$ServicePaths = @{}
$ServiceOrder = @()
Get-Content $ConfFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -match '^\s*#' -or $line -eq '') { return }
    $parts = $line -split '\s+', 2
    if ($parts.Count -eq 2) { $ServicePaths[$parts[0]] = $parts[1]; $script:ServiceOrder += $parts[0] }
}

function Remove-DanglingImages {
    $dangling = docker images -f "dangling=true" -q 2>$null
    if ($dangling) { docker image prune -f | Out-Null }
}

# git pull — выполняется один раз для всего репозитория
$gitPullDone = $false
function Invoke-GitPull {
    if ($script:gitPullDone) { return }
    Write-Info "git pull — загружаем последние изменения..."
    Push-Location $RepoRoot
    if (Get-Command "git" -ErrorAction SilentlyContinue) {
        git pull
        if ($LASTEXITCODE -eq 0) { Write-Ok "git pull завершён." }
        else { Write-Warn "git pull завершился с ошибкой — продолжаем с локальным кодом." }
    } else {
        Write-Warn "git не найден — пропускаем git pull."
    }
    Pop-Location
    $script:gitPullDone = $true
}

function Restart-Microservice {
    param([string]$Name, [string]$RunMode)
    if (-not $ServicePaths.ContainsKey($Name)) { Write-Fail "Сервис '$Name' не найден в services.conf" }
    $SvcDir = Join-Path $RepoRoot $ServicePaths[$Name]
    if (-not (Test-Path $SvcDir)) { Write-Fail "Директория не найдена: $SvcDir" }

    Write-Info "[$Name] Перезапуск (mode=$RunMode)..."
    Push-Location $SvcDir

    $composeFile    = Join-Path $SvcDir "docker-compose.yml"
    $composeContent = Get-Content $composeFile -Raw
    $hasBase      = $composeContent -match '(?m)^\s{2}base\s*:'
    $hasApi       = $composeContent -match '(?m)^\s{2}api\s*:'

    $baseFound = $false
    if ($hasBase) {
        $BaseTag = "${Name}-base:latest"
        try { docker image inspect $BaseTag 2>&1 | Out-Null; $baseFound = ($LASTEXITCODE -eq 0) } catch { $baseFound = $false }
    }

    # Nginx port forwarding prompt (only for microservice_infra)
    $composeProfile = @()
    if ($Name -eq 'microservice_infra') {
        Write-Host ""
        $ans = Read-Host "[nginx] Включить проброс порта в хост-сеть? [Y/N]"
        if ($ans -match '^[Yy]') {
            $port = ''
            while ($port -notin @('80','443')) {
                $port = Read-Host "[nginx] Выберите порт: 80 или 443"
            }
            $env:NGINX_PORT = $port
            $composeProfile = @('--profile', 'proxy')
            Write-Info "[nginx] Nginx будет запущен на порту $port."
        } else {
            Write-Info "[nginx] Nginx запущен без проброса в хост-сеть."
        }
    }

    switch ($RunMode) {
        "deps" {
            if ($hasBase) {
                Write-Info "[$Name] Пересборка base-образа (requirements.txt изменился)..."
                docker compose --profile build-base build --no-cache base
                if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Сборка base провалилась." }
                Remove-DanglingImages
            }
            docker compose $composeProfile build
            if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Build провалился." }
            Remove-DanglingImages
            docker compose $composeProfile up -d
            if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Запуск провалился." }
        }
        "api" {
            if ($hasApi) {
                docker compose $composeProfile up -d --no-deps --build api
            } else {
                docker compose $composeProfile up -d --build
            }
            if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Запуск api провалился." }
        }

        "postgres" {
            Write-Info "[$Name] Перезапуск postgres (применение новых параметров из docker-compose.yml)..."
            docker compose up -d --no-deps postgres
            if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Перезапуск postgres провалился." }
        }
        "redis" {
            Write-Info "[$Name] Перезапуск redis..."
            docker compose up -d --no-deps redis
            if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Перезапуск redis провалился." }
        }
        "full" {
            if ($hasBase -and -not $baseFound) {
                Write-Info "[$Name] Сборка base-образа..."
                docker compose --profile build-base build base
                if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Сборка base провалилась." }
                Remove-DanglingImages
            }
            docker compose $composeProfile --profile scheduler up -d --build
            if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Запуск провалился." }
        }
        default {
            if ($hasBase -and -not $baseFound) {
                Write-Info "[$Name] Сборка base-образа..."
                docker compose --profile build-base build base
                if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Сборка base провалилась." }
                Remove-DanglingImages
            }
            docker compose $composeProfile up -d --build
            if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Запуск провалился." }
        }
    }

    Pop-Location
    Write-Ok "[$Name] Перезапущен."
}

Invoke-GitPull

if ($Service -eq "all") {
    foreach ($svc in $ServiceOrder) { Restart-Microservice -Name $svc -RunMode $Mode }
} else {
    Restart-Microservice -Name $Service -RunMode $Mode
}