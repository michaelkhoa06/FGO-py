$ErrorActionPreference = 'Stop'
$agentRuntime = $env:FGO_PYTHON
if (-not $agentRuntime) {
    $agentRuntime = Join-Path $PSScriptRoot '../../FGO-py.portable/Python311/python.exe'
}
if (-not (Test-Path -LiteralPath $agentRuntime -PathType Leaf)) {
    throw 'Set FGO_PYTHON to a Python runtime with requirements.txt installed.'
}
$env:PYTHONUTF8 = '1'
& $agentRuntime (Join-Path $PSScriptRoot 'FGO-py/fgoAgentGui.py') @args
exit $LASTEXITCODE
