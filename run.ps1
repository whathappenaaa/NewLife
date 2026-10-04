$ErrorActionPreference = 'Stop'
$playerRoot = $PSScriptRoot
$playerPython = Join-Path $playerRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $playerPython)) {
    Write-Host '首次使用请先运行 setup.ps1 建立 Python 环境。'
    exit 1
}
Set-Location -LiteralPath $playerRoot
& $playerPython -m niulai_player @args
exit $LASTEXITCODE
