<#
.SYNOPSIS
    Reconcile ModelLine Docker services: pull latest images, restart changed containers.

.DESCRIPTION
    Reads deploy/modelline-deploy.yml and for each service entry:
      1. Pulls the latest image (if pull: true).
      2. Compares the new digest with the running container's digest.
      3. Restarts (docker compose up -d) only if the image changed
         (or restart_policy is "always").
      4. Skips restart when restart_policy is "never".

.PARAMETER ConfigFile
    Path to modelline-deploy.yml. Defaults to $PSScriptRoot/modelline-deploy.yml.

.PARAMETER Service
    Optional: reconcile only this named service (as defined in the YAML 'name' field).

.PARAMETER DryRun
    Print what would be done without executing any Docker commands.

.EXAMPLE
    # Full reconcile
    .\reconcile.ps1

    # Single service
    .\reconcile.ps1 -Service gateway

    # Dry run
    .\reconcile.ps1 -DryRun
#>

param(
    [string]$ConfigFile = "$PSScriptRoot\modelline-deploy.yml",
    [string]$Service    = "",
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Helpers ───────────────────────────────────────────────────────────────────

function Write-Step([string]$msg) { Write-Host "  >> $msg" -ForegroundColor Cyan }
function Write-Ok  ([string]$msg) { Write-Host "  OK $msg"  -ForegroundColor Green }
function Write-Skip([string]$msg) { Write-Host "  -- $msg"  -ForegroundColor DarkGray }
function Write-Warn([string]$msg) { Write-Host "  !! $msg"  -ForegroundColor Yellow }

function Invoke-Docker {
    param([string[]]$Args)
    if ($DryRun) {
        Write-Host "  [DRY-RUN] docker $($Args -join ' ')" -ForegroundColor DarkYellow
        return ""
    }
    $result = & docker @Args 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Args -join ' ') failed (exit $LASTEXITCODE):`n$result"
    }
    return $result
}

function Get-RunningDigest([string]$composePath, [string]$svcName) {
    $composeDir = Split-Path $composePath -Parent
    try {
        $id = & docker compose -f $composePath ps -q $svcName 2>$null
        if (-not $id) { return $null }
        $digest = & docker inspect --format '{{index .Image}}' $id.Trim() 2>$null
        return $digest
    } catch {
        return $null
    }
}

function Get-LatestDigest([string]$composePath, [string]$svcName) {
    try {
        $image = & docker compose -f $composePath config --images 2>$null |
                 Select-String $svcName |
                 Select-Object -First 1
        if (-not $image) { return $null }
        $imageRef = $image.ToString().Trim()
        $digest   = & docker inspect --format '{{index .RepoDigests 0}}' $imageRef 2>$null
        return $digest
    } catch {
        return $null
    }
}

# ── Load config ───────────────────────────────────────────────────────────────

if (-not (Test-Path $ConfigFile)) {
    Write-Error "Config not found: $ConfigFile"
    exit 1
}

# Minimal YAML parser — handles our fixed schema without a module dependency.
# Parses top-level 'services' list into PSCustomObject array.
function Read-DeployConfig([string]$path) {
    $text    = Get-Content $path -Raw
    $entries = @()
    $current = $null

    foreach ($raw in ($text -split "`n")) {
        $line = $raw.TrimEnd()

        if ($line -match '^\s*-\s+name:\s*(.+)') {
            if ($current) { $entries += $current }
            $current = [PSCustomObject]@{
                name           = $Matches[1].Trim()
                compose_file   = ""
                services       = @()
                pull           = $true
                restart_policy = "if_changed"
            }
        } elseif ($current -and $line -match '^\s+compose_file:\s*(.+)') {
            $current.compose_file = $Matches[1].Trim()
        } elseif ($current -and $line -match '^\s+pull:\s*(true|false)') {
            $current.pull = ($Matches[1].Trim() -eq 'true')
        } elseif ($current -and $line -match '^\s+restart_policy:\s*(.+)') {
            $current.restart_policy = $Matches[1].Trim()
        } elseif ($current -and $line -match '^\s+-\s+(\w+)') {
            # list item under 'services:'
            if ($line -notmatch 'name:|compose_file:|pull:|restart_policy:') {
                $current.services += $Matches[1].Trim()
            }
        }
    }
    if ($current) { $entries += $current }
    return $entries
}

$config    = Read-DeployConfig $ConfigFile
$configDir = Split-Path $ConfigFile -Parent

# ── Filter by -Service argument ───────────────────────────────────────────────

if ($Service) {
    $config = $config | Where-Object { $_.name -eq $Service }
    if (-not $config) {
        Write-Error "No service named '$Service' found in $ConfigFile"
        exit 1
    }
}

# ── Reconcile loop ────────────────────────────────────────────────────────────

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Host "`n=== ModelLine Reconcile [$timestamp]$($DryRun ? ' (DRY-RUN)' : '') ===" `
    -ForegroundColor Magenta

foreach ($entry in $config) {
    $composePath = if ([System.IO.Path]::IsPathRooted($entry.compose_file)) {
        $entry.compose_file
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $configDir $entry.compose_file))
    }

    Write-Host "`n[Service: $($entry.name)]" -ForegroundColor White

    if (-not (Test-Path $composePath)) {
        Write-Warn "compose file not found: $composePath — skipping"
        continue
    }

    foreach ($svc in $entry.services) {
        Write-Step "Processing $svc"

        # 1. Pull
        if ($entry.pull) {
            Write-Step "Pulling $svc"
            Invoke-Docker @("compose", "-f", $composePath, "pull", $svc) | Out-Null
        }

        # 2. Decide restart
        $doRestart = $false

        switch ($entry.restart_policy) {
            "always" {
                $doRestart = $true
                Write-Step "restart_policy=always → will restart"
            }
            "never" {
                $doRestart = $false
                Write-Skip "restart_policy=never → skip restart"
            }
            default {
                # "if_changed" — compare digests
                if (-not $DryRun) {
                    $before = Get-RunningDigest $composePath $svc
                    $after  = Get-LatestDigest  $composePath $svc
                    if ($before -ne $after) {
                        $doRestart = $true
                        Write-Step "Image changed → restarting"
                    } else {
                        Write-Skip "Image unchanged — no restart needed"
                    }
                } else {
                    $doRestart = $true # in dry-run, show what we would do
                }
            }
        }

        # 3. Restart
        if ($doRestart) {
            Invoke-Docker @("compose", "-f", $composePath, "up", "-d", "--no-deps", $svc) | Out-Null
            Write-Ok "$svc restarted"
        }
    }
}

Write-Host "`n=== Reconcile complete ===" -ForegroundColor Magenta
