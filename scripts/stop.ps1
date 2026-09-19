param(
    [ValidateSet('api', 'gateway', 'web')]
    [string[]]$Service = @('api', 'gateway', 'web')
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$processes = Get-CimInstance Win32_Process
$selected = foreach ($process in $processes) {
    $command = $process.CommandLine
    if (-not $command -or $command.IndexOf("$projectRoot\", [StringComparison]::OrdinalIgnoreCase) -lt 0) { continue }
    $matchesService = (
        ($process.Name -in @('python.exe', 'pythonw.exe') -and (
            ('api' -in $Service -and $command -like '*uvicorn adjutant.api:create_app*') -or
            ('gateway' -in $Service -and $command -like '*uvicorn adjutant.gateway_api:create_app*')
        )) -or
        ($process.Name -eq 'node.exe' -and 'web' -in $Service -and $command -like "*$projectRoot\web\node_modules\next\*")
    )
    if ($matchesService) { $process }
}
foreach ($process in $selected) {
    $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.ProcessId)"
    if (-not $current) { continue }
    if ($current.CreationDate -ne $process.CreationDate -or $current.CommandLine -ne $process.CommandLine) {
        throw "Process $($process.ProcessId) changed identity; refusing to stop it."
    }
    Stop-Process -Id $process.ProcessId -ErrorAction Stop
    Wait-Process -Id $process.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
    if (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) {
        throw "Process $($process.ProcessId) did not exit."
    }
    Write-Output "Verified process exit: $($process.ProcessId)"
}
Write-Output "Stopped selected Adjutant services: $($Service -join ', ')."
