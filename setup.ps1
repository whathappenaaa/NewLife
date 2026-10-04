$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & py -3.13 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.13 x64。' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败。' }
& '.\.venv\Scripts\python.exe' -m pip install -e . --no-deps
if ($LASTEXITCODE -ne 0) { throw '项目安装失败。' }
& '.\.venv\Scripts\python.exe' 'scripts\fetch_ffmpeg.py'
if ($LASTEXITCODE -ne 0) { throw '媒体解码组件下载或校验失败。' }
Write-Host '环境已就绪。在 VS Code 选择 .venv 解释器，按 F5 运行。'
