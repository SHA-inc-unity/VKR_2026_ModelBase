<#
.SYNOPSIS
    Show running container status vs modelline-deploy.yml configuration.

.DESCRIPTION
    For each service/container defined in modelline-deploy.yml, shows:
      - Container name
      - Expected image (from compose file)
      - Running image digest
      - Status (running / stopped / image-mismatch / not-found)

.EXAMPLE
    .\status.ps1
#>

param(
    [string]$ConfigFile = "$PSScriptRoot\modelline-deploy.yml"
)

Set-StrictMode -Version Latest

function Read-DeployConfig([string]$path) {
    $text    = Get-Content $path -Raw
    $entries = @()
    $current = $null

    foreach ($raw in ($text -split "`n")) {
        $line = $raw.TrimEnd()
        if ($line -match '^\s*-\s+name:\s*(.+)') {
            if ($current) { $entries += $current }
            $current = [PSCustomObject]@{ name="$($Matches[1].Trim())"; compose_file=""; services=@() }
        } elseif ($current -and $line -match '^\s+compose_file:\s*(.+)') {
            $current.compose_file = $Matches[1].Trim()
        } elseif ($current -and $line -match '^\s+-\s+(\w+)') {
            if ($line -notmatch 'name:|compose_file:|pull:|restart_policy:') {
                $current.services += $Matches[1].Trim()
            }
        }
    }
    if ($current) { $entries += $current }
    return $entries
}

if (-not (Test-Path $ConfigFile)) { Write-Error "Config not found: $ConfigFile"; exit 1 }

$config    = Read-DeployConfig $ConfigFile
$configDir = Split-Path $ConfigFile -Parent
$rows      = @()

foreach ($entry in $config) {
    $composePath = if ([System.IO.Path]::IsPathRooted($entry.compose_file)) {
        $entry.compose_file
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $configDir $entry.compose_file))
    }

    foreach ($svc in $entry.services) {
        $status  = "not-found"
        $running = ""
        $expected = ""

        try {
            if (Test-Path $composePath) {
                $expected = (& docker compose -f $composePath config --images 2>$null |
                    Select-String $svc | Select-Object -First 1).ToString().Trim()

                $id = (& docker compose -f $composePath ps -q $svc 2>$null).Trim()
                if ($id) {
                    $inspect = & docker inspect $id 2>$null | ConvertFrom-Json
                    $running = $inspect[0].Image
                    $state   = $inspect[0].State.Status
                    $status  = if ($state -ne "running") { "stopped" }
                               elseif ($running -ne $expected -and $expected) { "image-mismatch" }
                               else { "running" }
                }
            } else {
                $status = "compose-missing"
            }
        } catch { $status = "error" }

        $rows += [PSCustomObject]@{
            Service  = $entry.name
            Container = $svc
            Status   = $status
            Running  = $running
            Expected = $expected
        }
    }
}

Write-Host "`n=== ModelLine Container Status ===" -ForegroundColor Magenta
$rows | Format-Table -AutoSize
