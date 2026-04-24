# =============================================================================
# microservicestarter — start.ps1
#
# Запускает один или все микросервисы.
#
# Использование:
#   .\start.ps1                                          — все сервисы (core)
#   .\start.ps1 -Service microservice_analitic           — конкретный сервис
#   .\start.ps1 -Service microservice_analitic -Mode full    — core + scheduler
#   .\start.ps1 -Service microservice_analitic -Mode build   — пересборка + запуск
#   .\start.ps1 -Service microservice_analitic -Mode logs    — live-логи
# =============================================================================

param(
    [string]$Service = "all",
    [ValidateSet("core","full","scheduler","build","logs","")]
    [string]$Mode = "core"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
$ConfFile  = Join-Path $ScriptDir "services.conf"

function Write-Info { param($m) Write-Host "[starter] $m" -ForegroundColor Cyan   }
function Write-Ok   { param($m) Write-Host "[starter] $m" -ForegroundColor Green  }
function Write-Warn { param($m) Write-Host "[starter] $m" -ForegroundColor Yellow }
function Write-Fail { param($m) Write-Host "[starter] ERROR: $m" -ForegroundColor Red; exit 1 }

if (-not (Get-Command "docker" -ErrorAction SilentlyContinue)) { Write-Fail "docker не найден." }
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Fail "Docker daemon не запущен. Запустите Docker Desktop." }

# ── Реестр сервисов ──────────────────────────────────────────────────────────
$ServicePaths = @{}
$ServiceOrder = @()
Get-Content $ConfFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -match '^\s*#' -or $line -eq '') { return }
    $parts = $line -split '\s+', 2
    if ($parts.Count -eq 2) { $ServicePaths[$parts[0]] = $parts[1]; $script:ServiceOrder += $parts[0] }
}

# ── Первичная настройка .env с интерактивным запросом паролей ────────────────
function Initialize-Env {
    param([string]$Name, [string]$SvcDir)
    $envFile    = Join-Path $SvcDir ".env"
    $envExample = Join-Path $SvcDir ".env.example"
    if (Test-Path $envFile) { return }
    if (-not (Test-Path $envExample)) {
        Write-Warn "[$Name] .env.example не найден — пропускаем создание .env."
        return
    }

    Write-Info "[$Name] Первый запуск — настройка .env..."
    $content = Get-Content $envExample -Raw

    # Запрашиваем пароль PostgreSQL
    $pgPass = ""
    while ($pgPass -eq "") {
        $s = Read-Host "[$Name] Введите пароль PostgreSQL (PGPASSWORD / POSTGRES_PASSWORD)" -AsSecureString
        $pgPass = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
        if ($pgPass -eq "") { Write-Warn "Пароль не может быть пустым." }
    }

    # Применяем пароль ко всем ключам, которые используют заглушку
    $content = $content -replace '(?m)^(PGPASSWORD\s*=\s*).*$',          "`${1}$pgPass"
    $content = $content -replace '(?m)^(POSTGRES_PASSWORD\s*=\s*).*$',   "`${1}$pgPass"
    # Если DATABASE_URL содержит заглушку — подставляем пароль
    $content = $content -replace 'Password=your_strong_password_here',    "Password=$pgPass"
    $content = $content -replace 'Password=your_password_here',           "Password=$pgPass"

    [System.IO.File]::WriteAllText($envFile, $content, [System.Text.Encoding]::UTF8)
    Write-Ok "[$Name] .env создан."
}

# ── Очистка dangling-образов после сборки ────────────────────────────────────
function Remove-DanglingImages {
    $dangling = docker images -f "dangling=true" -q 2>$null
    if ($dangling) {
        Write-Info "Удаляем dangling-образы Docker..."
        docker image prune -f | Out-Null
    }
}

# ── Запуск сервиса ────────────────────────────────────────────────────────────
function Start-Microservice {
    param([string]$Name, [string]$RunMode)
    if (-not $ServicePaths.ContainsKey($Name)) { Write-Fail "Сервис '$Name' не найден в services.conf" }
    $SvcDir = Join-Path $RepoRoot $ServicePaths[$Name]
    if (-not (Test-Path $SvcDir)) { Write-Fail "Директория не найдена: $SvcDir" }

    Write-Info "[$Name] Запуск (mode=$RunMode)..."
    Push-Location $SvcDir

    Initialize-Env -Name $Name -SvcDir $SvcDir

    # Сборка base-образа только если compose-файл содержит сервис 'base'
    $composeFile   = Join-Path $SvcDir "docker-compose.yml"
    $hasBaseService = (Get-Content $composeFile -Raw) -match '^\s{2}base\s*:'
    if ($hasBaseService) {
        $BaseTag   = "${Name}-base:latest"
        $baseExists = $false
        try { docker image inspect $BaseTag 2>&1 | Out-Null; $baseExists = ($LASTEXITCODE -eq 0) } catch { $baseExists = $false }
        if (-not $baseExists) {
            Write-Info "[$Name] Сборка base-образа (первый раз, ~2 мин)..."
            docker compose --profile build-base build base
            if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Сборка base-образа провалилась." }
            Remove-DanglingImages
            Write-Ok "[$Name] Base-образ готов."
        }
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
        "build"     { docker compose $composeProfile build --no-cache; Remove-DanglingImages; docker compose $composeProfile up -d }
        "full"      { docker compose $composeProfile --profile scheduler up -d }
        "scheduler" { docker compose --profile scheduler up -d scheduler }
        "logs"      { docker compose logs -f }
        default     { docker compose $composeProfile up -d }
    }
    if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Запуск провалился." }

    Pop-Location
    Write-Ok "[$Name] Запущен."
}

if ($Service -eq "all") {
    foreach ($svc in $ServiceOrder) { Start-Microservice -Name $svc -RunMode $Mode }
} else {
    Start-Microservice -Name $Service -RunMode $Mode
}