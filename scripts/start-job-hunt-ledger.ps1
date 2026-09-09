[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendUrl = "http://127.0.0.1:8000/api/health"
$frontendUrl = "http://localhost:5173"
$logDirectory = Join-Path $env:LOCALAPPDATA "JobHuntLedger\logs"
$launcherLog = Join-Path $logDirectory "launcher.log"

function Write-LauncherLog {
    param([Parameter(Mandatory)][string]$Message)

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $launcherLog -Value "[$timestamp] $Message" -Encoding utf8
}

function Show-StartupFailure {
    param([Parameter(Mandatory)][string]$Message)

    $shell = New-Object -ComObject WScript.Shell
    $shell.Popup($Message, 0, "Job Hunt Ledger could not start", 16) | Out-Null
}

function Test-LocalUrl {
    param([Parameter(Mandatory)][string]$Url)

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

function Wait-ForLocalUrl {
    param([Parameter(Mandatory)][string]$Url)

    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (Test-LocalUrl -Url $Url) {
            return
        }
        Start-Sleep -Seconds 1
    }

    throw "Job Hunt Ledger did not become ready at $Url."
}

function Start-BackgroundProcess {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$ArgumentList,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$Name
    )

    Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDirectory "$Name-output.log") `
        -RedirectStandardError (Join-Path $logDirectory "$Name-error.log")
}

try {
    if (-not (Test-Path -LiteralPath $projectRoot)) {
        throw "Job Hunt Ledger project folder was not found: $projectRoot"
    }

    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    Write-LauncherLog "Starting readiness check."

    if (-not (Test-LocalUrl -Url $backendUrl)) {
        $python = Join-Path $projectRoot ".venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $python)) {
            throw "The dashboard Python environment was not found: $python"
        }
        Start-BackgroundProcess -FilePath $python -ArgumentList @(
            "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", "8000"
        ) -WorkingDirectory $projectRoot -Name "backend"
        Wait-ForLocalUrl -Url $backendUrl
    }

    if (-not (Test-LocalUrl -Url $frontendUrl)) {
        $npm = (Get-Command "npm.cmd" -ErrorAction Stop).Source
        Start-BackgroundProcess -FilePath $npm -ArgumentList @(
            "run", "dev", "--", "--host", "localhost", "--port", "5173", "--strictPort"
        ) -WorkingDirectory (Join-Path $projectRoot "frontend") -Name "frontend"
        Wait-ForLocalUrl -Url $frontendUrl
    }

    Write-LauncherLog "Dashboard is ready. Opening the browser."
    Start-Process $frontendUrl
}
catch {
    $message = "Job Hunt Ledger could not be prepared. $($_.Exception.Message) See $launcherLog for details."
    try {
        if (Test-Path -LiteralPath $logDirectory) {
            Write-LauncherLog "ERROR: $($_.Exception.Message)"
        }
    }
    catch {}
    Show-StartupFailure -Message $message
    exit 1
}
