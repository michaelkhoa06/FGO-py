# Use the existing bundled runtime; do not invoke the portable updater.
$ErrorActionPreference = 'Stop'
$agentRuntime = $env:FGO_PYTHON
if (-not $agentRuntime) {
    $agentRuntime = Join-Path $PSScriptRoot '../../FGO-py.portable/Python311/python.exe'
}
if (-not (Test-Path -LiteralPath $agentRuntime -PathType Leaf)) {
    throw 'Python runtime not found. Set FGO_PYTHON to Python 3.11 with requirements.txt installed.'
}
$env:PYTHONUTF8 = '1'
& $agentRuntime (Join-Path $PSScriptRoot 'FGO-py/fgoAgent.py') @args
exit $LASTEXITCODE
