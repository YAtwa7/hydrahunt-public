# HydraHunt - optional Windows autostart via Task Scheduler.
#
# THIS SCRIPT MODIFIES YOUR MACHINE. It registers a scheduled task that runs
# HydraHunt silently at every logon. Read this before running it.
#
# What it does, exactly:
#   - creates (or REPLACES) a Task Scheduler task named "HydraHunt"
#   - that task runs:  <repo>\.venv\Scripts\python.exe -m app.main
#   - it starts a background server, a web dashboard and a job scheduler
#   - it runs at every logon, indefinitely (no execution time limit)
#
# What it does NOT do: change firewall rules, install software, run as SYSTEM,
# or touch any other scheduled task. To remove it:  .\scripts\install_autostart.ps1 -Uninstall
#
# You do not need this at all. HydraHunt runs fine by hand:
#   cd backend; ..\.venv\Scripts\python.exe -m app.main
# The server binds 127.0.0.1 only, so it is not reachable from your network.
#
# REQUIRES ADMINISTRATOR. Task Scheduler refuses Register/Unregister without an
# elevated prompt. Run it from "PowerShell (Administrator)" or prefix with:
#   Start-Process powershell -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File scripts\install_autostart.ps1"
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1 -Yes
#   powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1 -Uninstall

[CmdletBinding()]
param(
    # Skip the confirmation prompt (for scripted setup).
    [switch]$Yes,
    # Remove the task and exit.
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "HydraHunt"

function Get-ExistingTask {
    # A non-elevated shell cannot read other users' tasks; treat that as "not
    # ours to touch" rather than crashing.
    try {
        return Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    } catch {
        return $null
    }
}

if ($Uninstall) {
    $existing = Get-ExistingTask
    if (-not $existing) {
        Write-Host "[--] No readable scheduled task named '$TaskName'. Nothing to do."
        Write-Host "     (if it exists, re-run this from an Administrator prompt)"
        exit 0
    }
    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[OK] Removed scheduled task '$TaskName'."
    } catch {
        Write-Error "Could not remove '$TaskName': $($_.Exception.Message)"
        Write-Host "     Task Scheduler needs an Administrator prompt - re-run as Administrator."
        exit 1
    }
    exit 0
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Backend = Join-Path $RepoRoot "backend"

if (-not (Test-Path $Python)) {
    Write-Error "Venv python not found at $Python - run setup first (backend/requirements.txt)"
    exit 1
}

$existing = Get-ExistingTask
if ($existing) {
    Write-Warning "A scheduled task named '$TaskName' already exists and WILL BE REPLACED."
}

Write-Host ""
Write-Host "This will register a Windows scheduled task:"
Write-Host ""
Write-Host "  Task name    : $TaskName"
Write-Host "  Runs         : $Python -m app.main"
Write-Host "  Working dir  : $Backend"
Write-Host "  Trigger      : at every Windows logon"
Write-Host "  Duration     : unlimited (no execution time limit)"
Write-Host ""
Write-Host "It starts a background server, a local dashboard and a job scheduler."
Write-Host "It binds 127.0.0.1 only and is not reachable from your network."
Write-Host ""
Write-Host "Remove later with:  .\scripts\install_autostart.ps1 -Uninstall"
Write-Host ""

if (-not $Yes) {
    $reply = Read-Host "Register this scheduled task? [y/N]"
    if ($reply -notmatch '^(y|yes)$') {
        Write-Host "[--] Cancelled. Nothing was changed."
        exit 0
    }
}

$action = New-ScheduledTaskAction -Execute $Python -Argument "-m app.main" -WorkingDirectory $Backend
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Description "HydraHunt - dashboard + scheduled scans (http://127.0.0.1:8420/app)" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "[OK] HydraHunt registered. It will start at next logon."
Write-Host "     Start now with:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "     Dashboard:       http://127.0.0.1:8420/app/index.html"
Write-Host "     Remove with:     .\scripts\install_autostart.ps1 -Uninstall"