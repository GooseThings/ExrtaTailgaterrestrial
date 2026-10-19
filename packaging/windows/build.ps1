# build.ps1 - builds the Windows installer, dist\ExtraTailgaterrestrial-<version>-setup.exe.
#
# Run on Windows (PyInstaller can't cross-compile) from any directory, with
# Python 3.9+ and Inno Setup 6 installed. The release CI runs this too.
#
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
#
# The app opens in its own window via pywebview, which uses the Edge
# WebView2 runtime that ships with Windows 10/11.

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

$version = (Select-String -Path version.py -Pattern '^__version__ = "(.+)"$').Matches[0].Groups[1].Value
if (-not $version) { throw "Couldn't read __version__ from version.py" }
Write-Host "Building ExtraTailgaterrestrial $version"

python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt pywebview pyinstaller
if ($LASTEXITCODE) { throw "pip install failed" }

# Windows wants an .ico; render it from the committed PNG.
python -c "from PIL import Image; Image.open('packaging/icon.png').save('packaging/windows/icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
if ($LASTEXITCODE) { throw "icon conversion failed" }

python -m PyInstaller --noconfirm --clean --windowed `
    --name ExtraTailgaterrestrial `
    --icon packaging\windows\icon.ico `
    --add-data "templates;templates" `
    --add-data "static;static" `
    dish_app.py
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { throw "Inno Setup 6 not found at $iscc - install it from https://jrsoftware.org/isinfo.php" }
& $iscc /Qp "/DAppVersion=$version" packaging\windows\installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }

Write-Host "Done: dist\ExtraTailgaterrestrial-$version-setup.exe"
