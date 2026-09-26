$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
& .\.venv\Scripts\python.exe -m ruff check src scripts
if ($LASTEXITCODE -ne 0) { throw 'Python lint failed.' }
& .\.venv\Scripts\python.exe -m ruff format --check src scripts
if ($LASTEXITCODE -ne 0) { throw 'Python formatting failed.' }
Push-Location web
$previousDist = $env:ADJUTANT_NEXT_DIST_DIR
try {
    $env:ADJUTANT_NEXT_DIST_DIR = '.next-check'
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Console production build failed.' }
} finally {
    $env:ADJUTANT_NEXT_DIST_DIR = $previousDist
    Pop-Location
}
