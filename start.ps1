param(
    [switch]$Restart
)
$ErrorActionPreference = 'Stop'

# Resolve repository root automatically regardless of current working directory
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
$projectRoot = $scriptDir
if (-not (Test-Path (Join-Path $projectRoot "pyproject.toml"))) {
    $projectRoot = Split-Path -Parent $scriptDir
}
Set-Location -LiteralPath $projectRoot

Write-Host "`n>>> Starting Adjutant in $projectRoot..." -ForegroundColor Cyan

# 1. Check Python virtual environment
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    Write-Error "Virtual environment not found at .venv. Please create .venv first."
    exit 1
}

# 2. Locate and Start PostgreSQL
$pgBins = @(
    'C:\Program Files\PostgreSQL\18\bin',
    'C:\Program Files\PostgreSQL\17\bin',
    'C:\Program Files\PostgreSQL\16\bin',
    'C:\Program Files\PostgreSQL\15\bin'
)
$pgBin = $pgBins | Where-Object { Test-Path (Join-Path $_ 'pg_ctl.exe') } | Select-Object -First 1
if (-not $pgBin) {
    $pgCtlCmd = Get-Command pg_ctl.exe -ErrorAction SilentlyContinue
    if ($pgCtlCmd) { $pgBin = Split-Path -Parent $pgCtlCmd.Source }
}

if ($pgBin) {
    Write-Host ">>> Checking PostgreSQL..." -ForegroundColor Gray
    & (Join-Path $pgBin 'pg_ctl.exe') -D .local/postgres status 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host ">>> Starting PostgreSQL on port 55439..." -ForegroundColor Yellow
        & (Join-Path $pgBin 'pg_ctl.exe') -D .local/postgres -l .local/postgres.log -o '-h 127.0.0.1 -p 55439' -w start
    }
}

# Helper: safely kill only Adjutant processes bound to a specific port
function Kill-PortListener([int]$Port) {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $conns) {
        try {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)" -ErrorAction SilentlyContinue
            if ($proc -and $proc.CommandLine -and $proc.CommandLine.IndexOf("$projectRoot", [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
            }
        } catch {}
    }
}

# 3. Test HTTP health helpers
function Test-ServiceHealth([string]$Url, [string]$MatchField, [string]$MatchVal) {
    try {
        $res = Invoke-RestMethod -Uri $Url -TimeoutSec 1 -ErrorAction Stop
        return $res.$MatchField -eq $MatchVal
    } catch {
        return $false
    }
}

function Test-WebHealth {
    try {
        $res = Invoke-WebRequest -Uri 'http://127.0.0.1:3000/' -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
        return $res.StatusCode -eq 200
    } catch {
        return $false
    }
}

# 4. Start Approval Service (Port 8003)
if ($Restart -or -not (Test-ServiceHealth 'http://127.0.0.1:8003/healthz' 'service' 'adjutant-approval')) {
    Write-Host ">>> Starting Approval Service (Port 8003)..." -ForegroundColor Yellow
    Kill-PortListener 8003
    Start-Process -FilePath $pythonExe -ArgumentList @('-m','uvicorn','adjutant.approval_api:create_app','--factory','--app-dir','src','--host','127.0.0.1','--port','8003') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot '.local/approval.stdout.log') -RedirectStandardError (Join-Path $projectRoot '.local/approval.stderr.log')
}

# 5. Start Launch Gateway (Port 8002)
if ($Restart -or -not (Test-ServiceHealth 'http://127.0.0.1:8002/healthz' 'service' 'adjutant-gateway')) {
    Write-Host ">>> Starting Launch Gateway (Port 8002)..." -ForegroundColor Yellow
    Kill-PortListener 8002
    Start-Process -FilePath $pythonExe -ArgumentList @('-m','uvicorn','adjutant.gateway_api:create_app','--factory','--app-dir','src','--host','127.0.0.1','--port','8002') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot '.local/gateway.stdout.log') -RedirectStandardError (Join-Path $projectRoot '.local/gateway.stderr.log')
}

# 6. Start Core Backend API (Port 8000)
if ($Restart -or -not (Test-ServiceHealth 'http://127.0.0.1:8000/healthz' 'service' 'adjutant-core')) {
    Write-Host ">>> Starting Core Backend API (Port 8000)..." -ForegroundColor Yellow
    Kill-PortListener 8000
    Start-Process -FilePath $pythonExe -ArgumentList @('-m','uvicorn','adjutant.api:create_app','--factory','--app-dir','src','--host','127.0.0.1','--port','8000') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot '.local/api.stdout.log') -RedirectStandardError (Join-Path $projectRoot '.local/api.stderr.log')
}

# 7. Start Web Console (Port 3000)
if ($Restart -or -not (Test-WebHealth)) {
    Write-Host ">>> Starting Web Console (Port 3000)..." -ForegroundColor Yellow
    Kill-PortListener 3000
    $nodeExe = (Get-Command node -ErrorAction SilentlyContinue).Source
    $nextBin = Join-Path $projectRoot 'web\node_modules\next\dist\bin\next'
    if ($nodeExe -and (Test-Path $nextBin)) {
        Start-Process -FilePath $nodeExe -ArgumentList @($nextBin,'dev','--hostname','127.0.0.1','--port','3000') -WorkingDirectory (Join-Path $projectRoot 'web') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot '.local/web.stdout.log') -RedirectStandardError (Join-Path $projectRoot '.local/web.stderr.log')
    } else {
        Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c','npm','run','dev') -WorkingDirectory (Join-Path $projectRoot 'web') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot '.local/web.stdout.log') -RedirectStandardError (Join-Path $projectRoot '.local/web.stderr.log')
    }
}

# 8. Health Check Wait Loop
Write-Host ">>> Verifying health across all services..." -ForegroundColor Cyan
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    $apiOk = Test-ServiceHealth 'http://127.0.0.1:8000/healthz' 'service' 'adjutant-core'
    $gateOk = Test-ServiceHealth 'http://127.0.0.1:8002/healthz' 'service' 'adjutant-gateway'
    $appOk = Test-ServiceHealth 'http://127.0.0.1:8003/healthz' 'service' 'adjutant-approval'
    $webOk = Test-WebHealth

    if ($apiOk -and $gateOk -and $appOk -and $webOk) {
        $ready = $true
        break
    }
    Start-Sleep -Milliseconds 600
}

if ($ready) {
    Write-Host "`n========================================================" -ForegroundColor Green
    Write-Host " [SUCCESS] All Adjutant services are live and ready!   " -ForegroundColor Green
    Write-Host " Web Console: http://localhost:3000                    " -ForegroundColor Green
    Write-Host " Backend API: http://localhost:8000                    " -ForegroundColor Green
    Write-Host " Gateway:     http://localhost:8002                    " -ForegroundColor Green
    Write-Host " Approval:    http://localhost:8003                    " -ForegroundColor Green
    Write-Host "========================================================`n" -ForegroundColor Green
    Start-Process "http://localhost:3000"
} else {
    Write-Host "`n>>> Service Status:" -ForegroundColor Yellow
    Write-Host "    API (8000):      $(if ($apiOk) {'OK'} else {'FAILED - check .local/api.stderr.log'})"
    Write-Host "    Gateway (8002):  $(if ($gateOk) {'OK'} else {'FAILED - check .local/gateway.stderr.log'})"
    Write-Host "    Approval (8003): $(if ($appOk) {'OK'} else {'FAILED - check .local/approval.stderr.log'})"
    Write-Host "    Web (3000):      $(if ($webOk) {'OK'} else {'FAILED - check .local/web.stderr.log'})"
}
