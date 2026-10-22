$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -B tools/verify_public_tree.py
if ($LASTEXITCODE -ne 0) { throw 'Public source checks failed' }
python -m PyInstaller --noconfirm --clean --onefile --windowed --name WoWOptimizer --add-data 'assets;assets' main_gui.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
Write-Host 'EXE: dist\WoWOptimizer.exe'
