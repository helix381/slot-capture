param([string]$OutputDirectory = "$PSScriptRoot/dist")
$ErrorActionPreference = 'Stop'
$captureProject = $PSScriptRoot
Push-Location $captureProject
try {
    & "$captureProject/.venv/Scripts/python.exe" -m PyInstaller --noconfirm --onefile --name slot-capture --paths src --collect-submodules dpkt --distpath $OutputDirectory --workpath build --specpath packaging launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'Executable build failed' }
    & "$captureProject/.venv/Scripts/python.exe" -m PyInstaller --noconfirm --onefile --windowed --name slot-capture-gui --paths src --collect-all tkinterdnd2 --distpath $OutputDirectory --workpath build --specpath packaging gui_launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'GUI executable build failed' }
} finally {
    Pop-Location
}
