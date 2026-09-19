param(
    [string]$PostgresBin = 'C:\Program Files\PostgreSQL\18\bin',
    [switch]$Restart
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Create the project virtual environment and install requirements first. See README.md.'
}
if (-not (Test-Path -LiteralPath '.env')) {
    throw 'Run the bootstrap command documented in README.md before starting the app.'
}
$nodeExe = (Get-Command node -ErrorAction Stop).Source
$nextBin = Join-Path $projectRoot 'web\node_modules\next\dist\bin\next'
if (-not (Test-Path -LiteralPath $nextBin)) { throw 'Run npm ci in the web directory first. See the Windows dependency repair instructions in README.md.' }
if (-not (Test-Path -LiteralPath '.local/gateway-service.secret') -or -not (Test-Path -LiteralPath '.local/approval-service.secret')) {
    throw 'Run .\.venv\Scripts\python.exe scripts/upgrade.py to configure the gateway and approval service first.'
}
function Assert-ProjectPort([int]$Port) {
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        if (-not $owner.CommandLine -or $owner.CommandLine.IndexOf("$projectRoot\", [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw "Port $Port belongs to another process ($($listener.OwningProcess)). Resolve the port conflict before starting Adjutant."
        }
    }
}
foreach ($port in @(8000, 8002, 8003, 3000)) { Assert-ProjectPort $port }
if ($Restart) { & (Join-Path $PSScriptRoot 'stop.ps1') -Service api,gateway,approval }
& (Join-Path $PostgresBin 'pg_ctl.exe') -D .local/postgres status 2>$null
if ($LASTEXITCODE -ne 0) {
    & (Join-Path $PostgresBin 'pg_ctl.exe') -D .local/postgres -l .local/postgres.log -o '-h 127.0.0.1 -p 55439' -w start
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL failed to start; inspect .local/postgres.log.' }
}
function Test-AdjutantApi {
    try {
        $result = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/healthz' -TimeoutSec 2
        return $result.service -eq 'adjutant-core'
    } catch {
        Write-Verbose "API is not ready: $($_.Exception.Message)"
        return $false
    }
}
function Test-AdjutantGateway {
    try {
        $result = Invoke-RestMethod -Uri 'http://127.0.0.1:8002/healthz' -TimeoutSec 2
        return $result.service -eq 'adjutant-gateway'
    } catch {
        Write-Verbose "Gateway is not ready: $($_.Exception.Message)"
        return $false
    }
}
function Test-AdjutantApproval {
    try {
        $result = Invoke-RestMethod -Uri 'http://127.0.0.1:8003/healthz' -TimeoutSec 2
        return $result.service -eq 'adjutant-approval'
    } catch {
        Write-Verbose "Approval service is not ready: $($_.Exception.Message)"
        return $false
    }
}
if (-not (Test-AdjutantApproval)) {
    Start-Process -FilePath $pythonExe -ArgumentList @('-m','uvicorn','adjutant.approval_api:create_app','--factory','--app-dir','src','--host','127.0.0.1','--port','8003') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput '.local/approval.stdout.log' -RedirectStandardError '.local/approval.stderr.log'
}
if (-not (Test-AdjutantGateway)) {
    Start-Process -FilePath $pythonExe -ArgumentList @('-m','uvicorn','adjutant.gateway_api:create_app','--factory','--app-dir','src','--host','127.0.0.1','--port','8002') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput '.local/gateway.stdout.log' -RedirectStandardError '.local/gateway.stderr.log'
}
if (-not (Test-AdjutantApi)) {
    Start-Process -FilePath $pythonExe -ArgumentList @('-m','uvicorn','adjutant.api:create_app','--factory','--app-dir','src','--host','127.0.0.1','--port','8000') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput '.local/api.stdout.log' -RedirectStandardError '.local/api.stderr.log'
}
$webListening = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
if (-not $webListening) {
    Start-Process -FilePath $nodeExe -ArgumentList @($nextBin,'dev','--hostname','127.0.0.1') -WorkingDirectory (Join-Path $projectRoot 'web') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot '.local/web.stdout.log') -RedirectStandardError (Join-Path $projectRoot '.local/web.stderr.log')
}
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if ((Test-AdjutantApi) -and (Test-AdjutantGateway) -and (Test-AdjutantApproval)) { $ready = $true; break }
    Start-Sleep -Milliseconds 500
}
if (-not $ready) { throw 'API, gateway, or approval service did not become healthy; inspect the matching .local/*.stderr.log.' }
$webReady = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:3000/' -TimeoutSec 3 -UseBasicParsing
        if ($response.StatusCode -eq 200 -and $response.Content -match 'Adjutant') { $webReady = $true; break }
    } catch { Write-Verbose "Console is not ready: $($_.Exception.Message)" }
    Start-Sleep -Milliseconds 500
}
if (-not $webReady) { throw 'Console did not become ready; inspect .local/web.stderr.log.' }
Write-Output 'Adjutant API, approval service, gateway, and console are ready. Open http://localhost:3000'
