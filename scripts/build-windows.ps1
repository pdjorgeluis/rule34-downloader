# Windows twin of build-linux.sh: build the onedir app with PyInstaller,
# smoke-test it, and pack a zip.
# UNTESTED: this script was written on Linux and has never been run on Windows.
# Needs PowerShell 5.1 or newer and no external modules.
# Set R34_PYTHON to use another Python than .venv\Scripts\python.exe.
$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root

if ($env:R34_PYTHON) {
    $Py = $env:R34_PYTHON
} else {
    $Py = Join-Path $Root '.venv\Scripts\python.exe'
}

$HavePyInstaller = $false
try {
    $LASTEXITCODE = 0
    & $Py -m PyInstaller --version | Out-Null
    $HavePyInstaller = ($LASTEXITCODE -eq 0)
} catch {
    $HavePyInstaller = $false
}
if (-not $HavePyInstaller) {
    [Console]::Error.WriteLine("error: PyInstaller is not available in $Py")
    exit 1
}

$LASTEXITCODE = 0
& $Py -m PyInstaller --noconfirm --clean --distpath build\dist --workpath build\work packaging\rule34-downloader.spec
if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine('error: PyInstaller build failed')
    exit 1
}

# A windowed exe has no standard output, so the smoke test saves a screenshot.
$env:QT_QPA_PLATFORM = 'offscreen'
$Exe = Join-Path $Root 'build\dist\rule34-downloader\rule34-downloader.exe'
$Smoke = Join-Path $Root 'build\smoke.png'
if (Test-Path $Smoke) { Remove-Item $Smoke -Force; Write-Host 'removed stale build\smoke.png' }
$p = Start-Process -FilePath $Exe -ArgumentList '--screenshot', ('"' + $Smoke + '"') -Wait -PassThru
if ($p.ExitCode -ne 0) {
    [Console]::Error.WriteLine('error: frozen exe smoke test failed')
    exit 1
}
if (-not (Test-Path $Smoke)) {
    [Console]::Error.WriteLine('error: frozen exe smoke test failed')
    exit 1
}
if ((Get-Item $Smoke).Length -le 0) {
    [Console]::Error.WriteLine('error: frozen exe smoke test failed')
    exit 1
}
Write-Host 'SMOKE OK (frozen exe)'

$LASTEXITCODE = 0
$Version = (& $Py -c "from rule34_downloader.version import __version__ as v; print(v)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $Version) {
    [Console]::Error.WriteLine('error: cannot read the version')
    exit 1
}

if ($env:PROCESSOR_ARCHITECTURE -eq 'AMD64') {
    $Arch = 'x86_64'
} else {
    $Arch = $env:PROCESSOR_ARCHITECTURE.ToLower()
}

Compress-Archive -Path build\dist\rule34-downloader -DestinationPath build\rule34-downloader-$Version-windows-$Arch.zip -Force
Write-Host "BUILD OK build\rule34-downloader-$Version-windows-$Arch.zip"
