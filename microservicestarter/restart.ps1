# =============================================================================
# microservicestarter - restart.ps1
#
# git pull + перезапускает один или все микросервисы.
#
# Использование:
#   .\restart.ps1                                                 - git pull + все сервисы
#   .\restart.ps1 -Mode noadmin                                   - git pull + все, кроме admin
#   .\restart.ps1 -Mode onlyadmin                                 - git pull + только admin-head
#   .\restart.ps1 -Service microservice_analitic                  - git pull + конкретный
#   .\restart.ps1 -Service all                                    - git pull + все
#   .\restart.ps1 -Service microservice_admin -Mode onlyadmin     - git pull + только admin-head
#   .\restart.ps1 -Service microservice_analitic -Mode full       - core + scheduler
#   .\restart.ps1 -Service microservice_analitic -Mode api        - только api
#   .\restart.ps1 -Service microservice_analitic -Mode deps       - пересобрать base
#   .\restart.ps1 -Service microservice_analitic -Mode postgres   - только postgres (без rebuild)
#   .\restart.ps1 -Service microservice_analitic -Mode redis      - только redis (без rebuild)
# =============================================================================

param(
    [string]$Service = "all",
    [ValidateSet("core","full","api","deps","postgres","redis","noadmin","onlyadmin","")]
    [string]$Mode = "core",
    [string]$ResultFile = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$LauncherScript = $MyInvocation.MyCommand.Path
$RepoRoot   = Split-Path -Parent $ScriptDir
$ConfFile   = Join-Path $ScriptDir "services.conf"

function Write-Info  { param($m) Write-Host "[starter] $m" -ForegroundColor Cyan   }
function Write-Ok    { param($m) Write-Host "[starter] $m" -ForegroundColor Green  }
function Write-Warn  { param($m) Write-Host "[starter] $m" -ForegroundColor Yellow }
function Set-InvocationResult {
    param([string]$Status)
    if ([string]::IsNullOrWhiteSpace($ResultFile)) { return }
    [System.IO.File]::WriteAllText($ResultFile, $Status, [System.Text.Encoding]::ASCII)
}
function Write-Fail  { param($m) Set-InvocationResult -Status "FAIL"; Write-Host "[starter] ERROR: $m" -ForegroundColor Red; exit 1 }

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

function Get-ServiceDirectory {
    param([string]$Name)
    if (-not $ServicePaths.ContainsKey($Name)) { Write-Fail "Сервис '$Name' не найден в services.conf" }
    $svcDir = Join-Path $RepoRoot $ServicePaths[$Name]
    if (-not (Test-Path $svcDir)) { Write-Fail "Директория не найдена: $svcDir" }
    return $svcDir
}

function Invoke-ParallelRestartSelection {
    param(
        [string[]]$Services,
        [string]$RunMode
    )

    if (-not $Services -or $Services.Count -eq 0) { return }

    Write-Info ("Параллельный перезапуск: " + ($Services -join ', '))
    $previousSkipGitPull = $env:MODELLINE_SKIP_GIT_PULL
    $env:MODELLINE_SKIP_GIT_PULL = "1"

    try {
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
                    Write-Ok "[$($child.Service)] Параллельный перезапуск завершён."
                } else {
                    Write-Warn "[$($child.Service)] Параллельный перезапуск завершился с ошибкой."
                    $failed += $child.Service
                }
            } finally {
                Remove-Item $child.Result -ErrorAction SilentlyContinue
            }
        }

        if ($failed.Count -gt 0) {
            Write-Fail ("Параллельный перезапуск завершился ошибкой для: " + ($failed -join ', '))
        }
    } finally {
        if ($null -eq $previousSkipGitPull) {
            Remove-Item Env:MODELLINE_SKIP_GIT_PULL -ErrorAction SilentlyContinue
        } else {
            $env:MODELLINE_SKIP_GIT_PULL = $previousSkipGitPull
        }
    }
}

# git pull — выполняется один раз для всего репозитория
$gitPullDone = $false
function Invoke-GitPull {
    if ($script:gitPullDone) { return }
    if ($env:MODELLINE_SKIP_GIT_PULL -eq "1") {
        $script:gitPullDone = $true
        return
    }
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
    $SvcDir = Get-ServiceDirectory -Name $Name

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

    # microservice_infra поднимает nginx-вход на host-порте 8501
    # автоматически. Никаких опциональных профилей proxy: единая
    # внешняя топология должна стартовать штатно при обычном restart.

    switch ($RunMode) {
        "onlyadmin" {
            if ($Name -ne "microservice_admin") { Write-Fail "mode=onlyadmin поддерживается только для microservice_admin" }
            docker compose --profile online up -d --build admin-online
            if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Запуск admin-online провалился." }
        }
        "deps" {
            if ($hasBase) {
                Write-Info "[$Name] Пересборка base-образа (requirements.txt изменился)..."
                docker compose --profile build-base build --no-cache base
                if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Сборка base провалилась." }
                Remove-DanglingImages
            }
            docker compose build
            if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Build провалился." }
            Remove-DanglingImages
            docker compose up -d
            if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Запуск провалился." }
        }
        "api" {
            if ($hasApi) {
                docker compose up -d --no-deps --build api
            } else {
                docker compose up -d --build
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
            docker compose --profile scheduler up -d --build
            if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Запуск провалился." }
        }
        default {
            if ($hasBase -and -not $baseFound) {
                Write-Info "[$Name] Сборка base-образа..."
                docker compose --profile build-base build base
                if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Сборка base провалилась." }
                Remove-DanglingImages
            }
            docker compose up -d --build
            if ($LASTEXITCODE -ne 0) { Write-Fail "[$Name] Запуск провалился." }
        }
    }

    Pop-Location
    Write-Ok "[$Name] Перезапущен."
    Set-InvocationResult -Status "OK"
}

Invoke-GitPull

if ($Mode -eq "onlyadmin") {
    if ($Service -ne "all" -and $Service -ne "microservice_admin") {
        Write-Fail "mode=onlyadmin поддерживается только для microservice_admin"
    }
    Restart-Microservice -Name "microservice_admin" -RunMode "onlyadmin"
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

    if ($selectedServices.Count -gt 1) {
        if ($selectedServices -contains "microservice_infra") {
            Restart-Microservice -Name "microservice_infra" -RunMode $dispatchMode
            $selectedServices = @($selectedServices | Where-Object { $_ -ne "microservice_infra" })
        }

        if ($selectedServices.Count -gt 1) {
            Invoke-ParallelRestartSelection -Services $selectedServices -RunMode $dispatchMode
        } elseif ($selectedServices.Count -eq 1) {
            Restart-Microservice -Name $selectedServices[0] -RunMode $dispatchMode
        }
    } else {
        foreach ($svc in $selectedServices) {
            Restart-Microservice -Name $svc -RunMode $dispatchMode
        }
    }
}