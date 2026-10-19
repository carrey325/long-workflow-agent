$ErrorActionPreference = 'Stop'
$runtimeRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $runtimeRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& '.venv\Scripts\python.exe' -m pip install -e '.[dev]'
exit $LASTEXITCODE
