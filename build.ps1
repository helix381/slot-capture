param([string]$OutputDirectory = "$PSScriptRoot/dist")
$ErrorActionPreference = 'Stop'
$captureProject = $PSScriptRoot
Push-Location $captureProject
try {
    & "$captureProject/.venv/Scripts/python.exe" -m PyInstaller --noconfirm --onefile --name slot-capture --version-file "$captureProject/resources/windows-version.txt" --paths src --collect-submodules dpkt --distpath $OutputDirectory --workpath build --specpath packaging launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'Executable build failed' }
    & "$captureProject/.venv/Scripts/python.exe" -m PyInstaller --noconfirm --onefile --windowed --name slot-capture-gui --version-file "$captureProject/resources/windows-version.txt" --paths src --collect-all webview --collect-all pythonnet --collect-all clr_loader --collect-all tkinterdnd2 --add-data "$captureProject/src/slotcapture/webui;slotcapture/webui" --distpath $OutputDirectory --workpath build --specpath packaging gui_launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'GUI executable build failed' }
} finally {
    Pop-Location
}
