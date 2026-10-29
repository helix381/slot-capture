param([string]$OutputDirectory = "$PSScriptRoot/dist")
$ErrorActionPreference = 'Stop'
$captureProject = $PSScriptRoot
Push-Location $captureProject
try {
    # This Python runtime loads its supplemental CA file whenever SSLContext is created.
    $captureSslCa = & "$captureProject/.venv/Scripts/python.exe" -c "import pathlib,ssl; print(pathlib.Path(ssl.__file__).with_name('codex-rejected-cas.pem'))"
    $captureSslData = @()
    if (Test-Path -LiteralPath $captureSslCa) { $captureSslData = @('--add-data', "$captureSslCa;.") }
    & "$captureProject/.venv/Scripts/python.exe" -m PyInstaller @captureSslData --noconfirm --onefile --name slot-capture --version-file "$captureProject/resources/windows-version.txt" --paths src --collect-submodules dpkt --distpath $OutputDirectory --workpath build --specpath packaging launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'Executable build failed' }
    & "$captureProject/.venv/Scripts/python.exe" -m PyInstaller @captureSslData --noconfirm --onefile --windowed --name slot-capture-gui --version-file "$captureProject/resources/windows-version.txt" --paths src --collect-all webview --collect-all pythonnet --collect-all clr_loader --collect-all tkinterdnd2 --add-data "$captureProject/src/slotcapture/webui;slotcapture/webui" --distpath $OutputDirectory --workpath build --specpath packaging gui_launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'GUI executable build failed' }
} finally {
    Pop-Location
}
