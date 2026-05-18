# =============================================================================
# microservicestarter — start.ps1
#
# Запускает один или все микросервисы.
#
# Использование:
#   .\start.ps1                                          — все сервисы (core)
#   .\start.ps1 -Mode noadmin                            — все сервисы, кроме admin
#   .\start.ps1 -Mode onlyadmin -BackendHost 10.44.0.1   — только admin-head в online mode
#   .\start.ps1 -Service microservice_analitic           — конкретный сервис
#   .\start.ps1 -Service microservice_admin -Mode onlyadmin -BackendHost 10.44.0.1 — только admin-head в online mode
#   .\start.ps1 -Service microservice_analitic -Mode full    — core + scheduler
#   .\start.ps1 -Service microservice_analitic -Mode build   — пересборка + запуск
#   .\start.ps1 -Service microservice_analitic -Mode logs    — live-логи
# =============================================================================

param(
    [string]$Service = "all",
    [ValidateSet("core","full","scheduler","build","logs","noadmin","onlyadmin","")]
    [string]$Mode = "core",
    [string]$BackendHost = "",
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

function Test-BackendBaseUrl {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        Write-Fail "Base URL backend-фасада не может быть пустым."
    }
    if ($Value -match '\s') {
        Write-Fail "Base URL backend-фасада не должен содержать пробелы: $Value"
    }
    try {
        $uri = [System.Uri]$Value
    } catch {
        Write-Fail "Ожидается base URL без пути, например https://backend.example.com:8443"
    }
    if ($uri.Scheme -notin @('http', 'https') -or [string]::IsNullOrWhiteSpace($uri.Host)) {
        Write-Fail "Ожидается base URL без пути, например https://backend.example.com:8443"
    }
    if ($uri.AbsolutePath -and $uri.AbsolutePath -ne '/') {
        Write-Fail "ADMIN_BACKEND_BASE_URL / PUBLIC_DOWNLOAD_BASE_URL не должны содержать путь: $Value"
    }
    if (-not [string]::IsNullOrWhiteSpace($uri.Query) -or -not [string]::IsNullOrWhiteSpace($uri.Fragment)) {
        Write-Fail "ADMIN_BACKEND_BASE_URL / PUBLIC_DOWNLOAD_BASE_URL не должны содержать query или fragment: $Value"
    }
}

function Get-HttpUrlEffectivePort {
    param([string]$Value)
    $uri = [System.Uri]$Value
    if (-not $uri.IsDefaultPort) { return [string]$uri.Port }
    if ($uri.Scheme -eq 'https') { return '443' }
    return '80'
}

function Convert-SecureStringToPlainText {
    param([Security.SecureString]$Value)
    if ($null -eq $Value) { return '' }
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

function New-RandomHexToken {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToHexString($bytes).ToLowerInvariant()
}

function Resolve-SecretEnvValueOrGenerate {
    param([string]$EnvFile, [string]$Key, [string]$OwnerLabel)
    $currentValue = (Get-EnvValue -EnvFile $EnvFile -Key $Key).Trim()
    if (-not [string]::IsNullOrWhiteSpace($currentValue)) {
        return $currentValue
    }

    $generated = New-RandomHexToken
    if ([string]::IsNullOrWhiteSpace($generated)) {
        Write-Fail "[$OwnerLabel] Не удалось сгенерировать $Key автоматически."
    }

    Write-Ok "[$OwnerLabel] $Key не был задан — сгенерировали новое значение и сохраним его в $(Split-Path $EnvFile -Leaf). Передай этот токен на admin-host как ADMIN_BACKEND_SHARED_TOKEN."
    return $generated
}

function Resolve-RequiredSecretEnvValue {
    param([string]$EnvFile, [string]$Key, [string]$OwnerLabel)
    $currentValue = (Get-EnvValue -EnvFile $EnvFile -Key $Key).Trim()
    if (-not [string]::IsNullOrWhiteSpace($currentValue)) {
        return $currentValue
    }

    Write-Info "[$OwnerLabel] $Key не задан — укажи значение ADMIN_SHARED_TOKEN с backend-host."
    do {
        $secure = Read-Host "[$OwnerLabel] Введите $Key (значение с backend-host)" -AsSecureString
        $plain = (Convert-SecureStringToPlainText -Value $secure).Trim()
        if ([string]::IsNullOrWhiteSpace($plain)) {
            Write-Warn "[$OwnerLabel] $Key не может быть пустым."
        }
    } while ([string]::IsNullOrWhiteSpace($plain))

    return $plain
}

function Resolve-AdminBackendBaseUrl {
    param([string]$SvcDir, [string]$BackendHost)
    $envFile = Join-Path $SvcDir ".env"
    $currentUrl = (Get-EnvValue -EnvFile $envFile -Key "ADMIN_BACKEND_BASE_URL").Trim().TrimEnd('/')

    $scheme = 'https'
    $port = '8443'
    if (-not [string]::IsNullOrWhiteSpace($currentUrl)) {
        try {
            $currentUri = [System.Uri]$currentUrl
            if ($currentUri.Scheme -in @('http', 'https')) {
                $scheme = $currentUri.Scheme
                if (-not $currentUri.IsDefaultPort) {
                    $port = [string]$currentUri.Port
                }
                if ($currentUri.Host -eq $BackendHost -and ($currentUri.AbsolutePath -eq '/' -or [string]::IsNullOrWhiteSpace($currentUri.AbsolutePath))) {
                    return $currentUrl
                }
            }
        } catch {
        }
    }

    $derivedUrl = "${scheme}://${BackendHost}:${port}"
    if (-not [string]::IsNullOrWhiteSpace($currentUrl)) {
        Write-Info "[microservice_admin] Текущий ADMIN_BACKEND_BASE_URL: $currentUrl"
        $answer = Read-Host "[microservice_admin] Введите ADMIN_BACKEND_BASE_URL [$derivedUrl]"
        $resolvedUrl = if ([string]::IsNullOrWhiteSpace($answer)) { $derivedUrl } else { $answer.Trim() }
    } else {
        Write-Info "[microservice_admin] ADMIN_BACKEND_BASE_URL не задан — настроим split HTTPS endpoint."
        $answer = Read-Host "[microservice_admin] Введите ADMIN_BACKEND_BASE_URL [$derivedUrl]"
        $resolvedUrl = if ([string]::IsNullOrWhiteSpace($answer)) { $derivedUrl } else { $answer.Trim() }
    }

    $resolvedUrl = $resolvedUrl.TrimEnd('/')
    Test-BackendBaseUrl -Value $resolvedUrl
    return $resolvedUrl
}

function Resolve-BackendPublicBaseUrl {
    param([string]$EnvFile)
    $currentUrl = (Get-EnvValue -EnvFile $EnvFile -Key "PUBLIC_DOWNLOAD_BASE_URL").Trim().TrimEnd('/')
    if (-not [string]::IsNullOrWhiteSpace($currentUrl) -and $currentUrl -ne 'http://localhost:8501') {
        Test-BackendBaseUrl -Value $currentUrl
        return $currentUrl
    }

    Write-Info "[backend-host] Нужен внешний base URL backend-host для HTTPS admin facade и прямых downloads."
    do {
        $resolvedUrl = (Read-Host "[backend-host] Введите backend public base URL (например https://backend.example.com:8443)").Trim().TrimEnd('/')
        if ([string]::IsNullOrWhiteSpace($resolvedUrl)) {
            Write-Warn "[backend-host] Base URL не может быть пустым."
        }
    } while ([string]::IsNullOrWhiteSpace($resolvedUrl))

    Test-BackendBaseUrl -Value $resolvedUrl
    return $resolvedUrl
}

function Configure-BackendAdminFacadeEnv {
    $infraSvcDir = Get-ServiceDirectory -Name "microservice_infra"
    $gatewaySvcDir = Get-ServiceDirectory -Name "microservice_gateway"
    $dataSvcDir = Get-ServiceDirectory -Name "microservice_data"

    $infraEnv = Ensure-EnvFile -SvcDir $infraSvcDir
    $gatewayEnv = Ensure-EnvFile -SvcDir $gatewaySvcDir
    $dataEnv = Ensure-EnvFile -SvcDir $dataSvcDir

    $publicBaseUrl = Resolve-BackendPublicBaseUrl -EnvFile $dataEnv
    $sharedToken = Resolve-SecretEnvValueOrGenerate -EnvFile $gatewayEnv -Key "ADMIN_SHARED_TOKEN" -OwnerLabel "microservice_gateway"
    $backendPort = Get-HttpUrlEffectivePort -Value $publicBaseUrl

    Set-EnvValue -EnvFile $dataEnv -Key "PUBLIC_DOWNLOAD_BASE_URL" -Value $publicBaseUrl
    Set-EnvValue -EnvFile $gatewayEnv -Key "ADMIN_SHARED_TOKEN" -Value $sharedToken
    Set-EnvValue -EnvFile $infraEnv -Key "ADMIN_BACKEND_PORT" -Value $backendPort

    Write-Ok "[backend-host] HTTP admin facade env настроены: PUBLIC_DOWNLOAD_BASE_URL=$publicBaseUrl, ADMIN_BACKEND_PORT=$backendPort."
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

    if ($content -match '(?m)^(PGPASSWORD\s*=|POSTGRES_PASSWORD\s*=)|Password=your_strong_password_here|Password=your_password_here') {
        # Запрашиваем пароль PostgreSQL только если он действительно нужен в .env.example
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
        $content = $content -replace 'Password=your_strong_password_here',    "Password=$pgPass"
        $content = $content -replace 'Password=your_password_here',           "Password=$pgPass"
    }

    [System.IO.File]::WriteAllText($envFile, $content, [System.Text.Encoding]::UTF8)
    Write-Ok "[$Name] .env создан."
}

function Get-EnvValue {
    param([string]$EnvFile, [string]$Key)
    if (-not (Test-Path $EnvFile)) { return "" }
    $pattern = '^' + [regex]::Escape($Key) + '=(.*)$'
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match $pattern) { return $Matches[1].Trim() }
    }
    return ""
}

function Set-EnvValue {
    param([string]$EnvFile, [string]$Key, [string]$Value)
    $lines = if (Test-Path $EnvFile) { @(Get-Content $EnvFile) } else { @() }
    $pattern = '^' + [regex]::Escape($Key) + '='
    $updated = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $pattern) {
            $lines[$i] = "${Key}=${Value}"
            $updated = $true
            break
        }
    }
    if (-not $updated) { $lines += "${Key}=${Value}" }
    [System.IO.File]::WriteAllText($EnvFile, (($lines -join "`r`n") + "`r`n"), [System.Text.Encoding]::UTF8)
}

function Ensure-EnvFile {
    param([string]$SvcDir)
    $envFile = Join-Path $SvcDir ".env"
    $envExample = Join-Path $SvcDir ".env.example"
    if (-not (Test-Path $envFile) -and (Test-Path $envExample)) {
        Copy-Item $envExample $envFile
    }
    return $envFile
}

function Test-BackendHost {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        Write-Fail "Для mode=onlyadmin backend host/IP не может быть пустым."
    }
    if ($Value.Contains('://')) {
        Write-Fail "Для mode=onlyadmin указывай только host/IP без схемы: $Value"
    }
    if ($Value.Contains('/')) {
        Write-Fail "Для mode=onlyadmin указывай только host/IP без пути: $Value"
    }
    if ($Value -match '\s') {
        Write-Fail "Для mode=onlyadmin host/IP не должен содержать пробелы: $Value"
    }
}

function Resolve-AdminOnlineBackendHost {
    param([string]$SvcDir, [string]$ExplicitBackendHost)
    $envFile = Join-Path $SvcDir ".env"
    $envExample = Join-Path $SvcDir ".env.example"
    if (-not (Test-Path $envFile)) {
        if (-not (Test-Path $envExample)) {
            Write-Fail "[microservice_admin] .env.example не найден — не можем настроить admin-online."
        }
        Copy-Item $envExample $envFile
    }

    $currentBackendHost = (Get-EnvValue -EnvFile $envFile -Key "ONLINE_BACKEND_HOST").Trim()
    $resolvedBackendHost = [string]$ExplicitBackendHost
    $resolvedBackendHost = $resolvedBackendHost.Trim()

    if (-not [string]::IsNullOrWhiteSpace($resolvedBackendHost)) {
        Test-BackendHost -Value $resolvedBackendHost
        return $resolvedBackendHost
    }

    if (-not [string]::IsNullOrWhiteSpace($currentBackendHost)) {
        Write-Info "[microservice_admin] Текущий backend host/IP для admin-online: $currentBackendHost"
        $answer = Read-Host "[microservice_admin] Введите backend host/IP для admin-online [$currentBackendHost]"
        $resolvedBackendHost = if ([string]::IsNullOrWhiteSpace($answer)) { $currentBackendHost } else { $answer.Trim() }
    } else {
        Write-Info "[microservice_admin] ONLINE_BACKEND_HOST не задан — сейчас запросим backend host/IP для admin-online."
        do {
            $resolvedBackendHost = (Read-Host "[microservice_admin] Введите backend host/IP для admin-online").Trim()
            if ([string]::IsNullOrWhiteSpace($resolvedBackendHost)) {
                Write-Warn "Backend host/IP не может быть пустым."
            }
        } while ([string]::IsNullOrWhiteSpace($resolvedBackendHost))
    }

    $resolvedBackendHost = $resolvedBackendHost.Trim()
    Test-BackendHost -Value $resolvedBackendHost
    return $resolvedBackendHost
}

function Configure-AdminOnlineEnv {
    param([string]$SvcDir, [string]$ExplicitBackendHost)
    $envFile = Join-Path $SvcDir ".env"
    $envExample = Join-Path $SvcDir ".env.example"
    if (-not (Test-Path $envFile)) {
        if (-not (Test-Path $envExample)) {
            Write-Fail "[microservice_admin] .env.example не найден — не можем настроить admin-online."
        }
        Copy-Item $envExample $envFile
    }

    $resolvedBackendHost = Resolve-AdminOnlineBackendHost -SvcDir $SvcDir -ExplicitBackendHost $ExplicitBackendHost
    $resolvedBackendBaseUrl = Resolve-AdminBackendBaseUrl -SvcDir $SvcDir -BackendHost $resolvedBackendHost
    $resolvedSharedToken = Resolve-RequiredSecretEnvValue -EnvFile $envFile -Key "ADMIN_BACKEND_SHARED_TOKEN" -OwnerLabel "microservice_admin"

    Set-EnvValue -EnvFile $envFile -Key "ONLINE_BACKEND_HOST" -Value $resolvedBackendHost
    Set-EnvValue -EnvFile $envFile -Key "ONLINE_KAFKA_BOOTSTRAP_SERVERS" -Value "${resolvedBackendHost}:9092"
    Set-EnvValue -EnvFile $envFile -Key "ONLINE_REDPANDA_ADMIN_URL" -Value "${resolvedBackendHost}:9644"
    Set-EnvValue -EnvFile $envFile -Key "ONLINE_ACCOUNT_URL" -Value "${resolvedBackendHost}:7510"
    Set-EnvValue -EnvFile $envFile -Key "ONLINE_GATEWAY_URL" -Value "${resolvedBackendHost}:7520"
    Set-EnvValue -EnvFile $envFile -Key "ONLINE_MINIO_URL" -Value "${resolvedBackendHost}:9000"
    Set-EnvValue -EnvFile $envFile -Key "ADMIN_BACKEND_BASE_URL" -Value $resolvedBackendBaseUrl
    Set-EnvValue -EnvFile $envFile -Key "ADMIN_BACKEND_SHARED_TOKEN" -Value $resolvedSharedToken

    Write-Ok "[microservice_admin] Split env настроены: ONLINE_* + ADMIN_BACKEND_* для $resolvedBackendBaseUrl"
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
            Configure-AdminOnlineEnv -SvcDir $SvcDir -ExplicitBackendHost $BackendHost
            docker compose --profile online up -d admin-online
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
    $adminSvcDir = Get-ServiceDirectory -Name "microservice_admin"
    Initialize-Env -Name "microservice_admin" -SvcDir $adminSvcDir
    $BackendHost = Resolve-AdminOnlineBackendHost -SvcDir $adminSvcDir -ExplicitBackendHost $BackendHost
    Start-Microservice -Name "microservice_admin" -RunMode "onlyadmin"
} else {
    $selectedServices = @()
    $dispatchMode = $Mode

    if ($Mode -eq "noadmin") {
        if ($Service -ne "all") {
            Write-Fail "mode=noadmin поддерживается только вместе с -Service all"
        }
        $dispatchMode = "core"
        Configure-BackendAdminFacadeEnv
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