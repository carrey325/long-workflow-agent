param([string]$Directory = '.runs/demo')
$ErrorActionPreference = 'Stop'
$runtimeRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $runtimeRoot
& '.venv\Scripts\python.exe' -m orchestrator demo --directory $Directory
exit $LASTEXITCODE
