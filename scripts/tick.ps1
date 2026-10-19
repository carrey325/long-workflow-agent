param([Parameter(Mandatory)][string]$Config, [Parameter(Mandatory)][string]$RunId)
$ErrorActionPreference = 'Stop'
$runtimeRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $runtimeRoot '.venv\Scripts\python.exe') -m orchestrator --config $Config tick $RunId
exit $LASTEXITCODE
