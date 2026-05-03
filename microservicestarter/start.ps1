# =============================================================================
# microservicestarter — start.ps1
#
# Запускает один или все микросервисы.
#
# Использование:
#   .\start.ps1                                          — все сервисы (core)
#   .\start.ps1 -Mode noadmin                            — все сервисы, кроме admin
#   .\start.ps1 -Mode onlyadmin                          — только admin-head в online mode
#   .\start.ps1 -Service microservice_analitic           — конкретный сервис
#   .\start.ps1 -Service microservice_admin -Mode onlyadmin — только admin-head в online mode
#   .\start.ps1 -Service microservice_analitic -Mode full    — core + scheduler
#   .\start.ps1 -Service microservice_analitic -Mode build   — пересборка + запуск
#   .\start.ps1 -Service microservice_analitic -Mode logs    — live-логи
# =============================================================================

param(
    [string]$Service = "all",
    [ValidateSet("core","full","scheduler","build","logs","noadmin","onlyadmin","")]
    [string]$Mode = "core",
    [string]$ResultFile = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LauncherScript = $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
$ConfFile  = Join-Path $ScriptDir "services.conf"

function Write-Info { param($m) Write-Host "[starter] $m" -ForegroundColor Cyan   }
function Write-Ok   { param($m) Write-Host "[starter] $m" -ForegroundColor Green  }
function Write-Warn { param($m) Write-Host "[starter] $m" -ForegroundColor Yellow }
function Set-InvocationResult {
    param([string]$Status)
    if ([string]::IsNullOrWhiteSpace($ResultFile)) { return }
    [System.IO.File]::WriteAllText($ResultFile, $Status, [System.Text.Encoding]::ASCII)
}
function Write-Fail { param($m) Set-InvocationResult -Status "FAIL"; Write-Host "[starter] ERROR: $m" -ForegroundColor Red; exit 1 }

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

function Get-ServiceDirectory {
    param([string]$Name)
    if (-not $ServicePaths.ContainsKey($Name)) { Write-Fail "Сервис '$Name' не найден в services.conf" }
    $svcDir = Join-Path $RepoRoot $ServicePaths[$Name]
    if (-not (Test-Path $svcDir)) { Write-Fail "Директория не найдена: $svcDir" }
    return $svcDir
}

function Prepare-StartSelection {
    param([string[]]$Services)
    foreach ($name in $Services) {
        $svcDir = Get-ServiceDirectory -Name $name
        Initialize-Env -Name $name -SvcDir $svcDir
    }
}

function Invoke-ParallelStartSelection {
    param(
        [string[]]$Services,
        [string]$RunMode
    )

    if (-not $Services -or $Services.Count -eq 0) { return }

    Write-Info ("Параллельный запуск: " + ($Services -join ', '))
    $children = @()
    foreach ($name in $Services) {
        $resultFile = [System.IO.Path]::GetTempFileName()
        $proc = Start-Process -FilePath "powershell.exe" `
            -ArgumentList @(
                '-NoLogo',
                '-NoProfile',
                '-ExecutionPolicy', 'Bypass',
                '-File', $LauncherScript,
                '-Service', $name,
                '-Mode', $RunMode,
                '-ResultFile', $resultFile
            ) `
            -WorkingDirectory $ScriptDir `
            -NoNewWindow `
            -PassThru
        $children += [pscustomobject]@{
            Service = $name
            Process = $proc
            Result  = $resultFile
        }
    }

    $failed = @()
    foreach ($child in $children) {
        try {
            $child.Process.WaitForExit()

            $result = if (Test-Path $child.Result) { (Get-Content $child.Result -Raw -ErrorAction SilentlyContinue).Trim() } else { '' }
            if ($result -eq 'OK') {
                Write-Ok "[$($child.Service)] Параллельный запуск завершён."
            } else {
                Write-Warn "[$($child.Service)] Параллельный запуск завершился с ошибкой."
                $failed += $child.Service
            }
        } finally {
            Remove-Item $child.Result -ErrorAction SilentlyContinue
        }
    }

    if ($failed.Count -gt 0) {
        Write-Fail ("Параллельный запуск завершился ошибкой для: " + ($failed -join ', '))
    }
}

# ── Запуск сервиса ────────────────────────────────────────────────────────────
function Start-Microservice {
    param([string]$Name, [string]$RunMode)
    $SvcDir = Get-ServiceDirectory -Name $Name

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

    # microservice_infra поднимает nginx-вход на host-порте 8501
    # автоматически. В local/full stack это browser-facing endpoint
    # платформы (admin /admin/* + signed downloads /modelline-blobs/*).
    # Никаких опциональных профилей и интерактивных prompt'ов: новая
    # топология должна стартовать штатно вместе с обычным `start`.

    switch ($RunMode) {
        "build"     { docker compose build --no-cache; Remove-DanglingImages; docker compose up -d }
        "full"      { docker compose --profile scheduler up -d }
        "scheduler" { docker compose --profile scheduler up -d scheduler }
        "logs"      { docker compose logs -f }
        "onlyadmin" {
            if ($Name -ne "microservice_admin") {
                Write-Fail "mode=onlyadmin поддерживается только для microservice_admin"
            }
            docker compose --profile online up -d --build admin-online
        }
        default     { docker compose up -d }
    }
    if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Запуск провалился." }

    Pop-Location
    Write-Ok "[$Name] Запущен."
    Set-InvocationResult -Status "OK"
}

if ($Mode -eq "onlyadmin") {
    if ($Service -ne "all" -and $Service -ne "microservice_admin") {
        Write-Fail "mode=onlyadmin поддерживается только для microservice_admin"
    }
    Start-Microservice -Name "microservice_admin" -RunMode "onlyadmin"
} else {
    $selectedServices = @()
    $dispatchMode = $Mode

    if ($Mode -eq "noadmin") {
        if ($Service -ne "all") {
            Write-Fail "mode=noadmin поддерживается только вместе с -Service all"
        }
        $dispatchMode = "core"
        foreach ($svc in $ServiceOrder) {
            if ($svc -ne "microservice_admin") { $selectedServices += $svc }
        }
    } elseif ($Service -eq "all") {
        $selectedServices = @($ServiceOrder)
    } else {
        $selectedServices = @($Service)
    }

    if ($selectedServices.Count -gt 1 -and $dispatchMode -ne "logs") {
        if ($selectedServices -contains "microservice_infra") {
            Start-Microservice -Name "microservice_infra" -RunMode $dispatchMode
            $selectedServices = @($selectedServices | Where-Object { $_ -ne "microservice_infra" })
        }

        if ($selectedServices.Count -gt 0) {
            Prepare-StartSelection -Services $selectedServices
        }

        if ($selectedServices.Count -gt 1) {
            Invoke-ParallelStartSelection -Services $selectedServices -RunMode $dispatchMode
        } elseif ($selectedServices.Count -eq 1) {
            Start-Microservice -Name $selectedServices[0] -RunMode $dispatchMode
        }
    } else {
        foreach ($svc in $selectedServices) {
            Start-Microservice -Name $svc -RunMode $dispatchMode
        }
    }
}