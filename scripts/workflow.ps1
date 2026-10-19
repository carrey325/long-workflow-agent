param(
    [Parameter(Mandatory=$true)][ValidateSet('start','pause','resume','status','bridge','tick')]
    [string]$Action,
    [Parameter(Mandatory=$true)][string]$Config,
    [Parameter(Mandatory=$true)][string]$RunId,
    [string]$Reason = 'User requested pause'
)
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$runtimeRoot = Split-Path -Parent $PSScriptRoot
$runtimePython = Join-Path $runtimeRoot '.venv\Scripts\python.exe'
$arguments = @('-m', 'orchestrator', '--config', (Resolve-Path -LiteralPath $Config).Path, $Action, $RunId)
if ($Action -eq 'pause') { $arguments += @('--reason', $Reason) }
& $runtimePython @arguments
exit $LASTEXITCODE
