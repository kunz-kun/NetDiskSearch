$ErrorActionPreference = "Stop"
$Python = Join-Path $PSScriptRoot ".python-build\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    $Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}

& $Python -c "import tkinter; tkinter.Tcl()" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "当前 Python 缺少可用的 Tcl/Tk。请安装带 tcl/tk 组件的官方 Python 后重试。"
}
& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "缺少 PyInstaller。请先运行: python -m pip install pyinstaller"
}

& $Python -m unittest discover -s tests -v
& $Python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "NetDiskSearch" `
    app.py

Write-Host "Build complete: dist\NetDiskSearch.exe"

