$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$playerPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$playerAssets = Join-Path $PSScriptRoot 'assets'
$playerIcon = Join-Path $playerAssets 'app.ico'
$playerRelease = 'artifacts\release-v0.6.1'
& $playerPython 'scripts\fetch_ffmpeg.py'
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg 下载或校验失败。' }
& $playerPython 'scripts\archive_ffmpeg_sources.py'
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg 源码归档失败。' }
& $playerPython -m pytest -q
if ($LASTEXITCODE -ne 0) { throw '测试失败，停止打包。' }
# 隔离 DLL 查找路径，避免把其他工具（例如 Poppler）的同名 ICU/SSL 库装入发布包。
$playerOriginalPath = $env:PATH
try {
    $env:PATH = @((Split-Path $playerPython), "$env:WINDIR\System32", $env:WINDIR) -join ';'
    & $playerPython -m PyInstaller --noconfirm --clean --windowed --onedir --name NewLife --icon $playerIcon --add-data "$playerAssets;assets" --distpath $playerRelease --workpath artifacts\build-v0.6.1 --specpath artifacts --collect-all pyaudiowpatch --collect-all _soundfile_data --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets tools\package_entry.py
    $playerBuildExit = $LASTEXITCODE
} finally {
    $env:PATH = $playerOriginalPath
}
if ($playerBuildExit -ne 0) { throw '打包失败。' }
$playerApp = Join-Path $playerRelease 'NewLife'
& $playerPython 'tools\package_music.py' (Join-Path $PSScriptRoot 'MUSIC') (Join-Path $playerApp 'MUSIC')
if ($LASTEXITCODE -ne 0) { throw 'MUSIC 文件夹打包失败。' }
Copy-Item -LiteralPath 'docs\使用说明.txt' -Destination (Join-Path $playerApp '使用说明.txt') -Force
Copy-Item -LiteralPath 'THIRD-PARTY-NOTICES.txt' -Destination (Join-Path $playerApp 'THIRD-PARTY-NOTICES.txt') -Force
Copy-Item -LiteralPath 'LICENSE' -Destination (Join-Path $playerApp 'LICENSE') -Force
# Standalone subprocess binaries retain their complete, separate DLL directory.
$playerFfmpeg = Join-Path $playerApp 'vendor\ffmpeg'
New-Item -ItemType Directory -Path $playerFfmpeg -Force | Out-Null
Copy-Item -LiteralPath 'vendor\ffmpeg\bin' -Destination $playerFfmpeg -Recurse -Force
& $playerPython 'tools\collect_licenses.py' (Join-Path $playerApp 'licenses')
if ($LASTEXITCODE -ne 0) { throw '许可证收集失败。' }
Compress-Archive -LiteralPath $playerApp -DestinationPath 'artifacts\NewLife-v0.6.1-win-x64.zip' -Force
Write-Host '发布包：artifacts\NewLife-v0.6.1-win-x64.zip'
