param([Parameter(Mandatory)][string]$Installer)

$ErrorActionPreference = "Stop"
$app = Join-Path $env:LOCALAPPDATA "Programs\SyncPlayU"
$regKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SyncPlayU"
$shortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "SyncPlayU.lnk"
$config = Join-Path $app "syncplay.ini"
$marker = Join-Path $app "keep-me.txt"

function Invoke-Setup {
    param([string]$Executable)
    $process = Start-Process -FilePath $Executable -ArgumentList "/S" -PassThru -Wait
    if ($process.ExitCode -ne 0) { throw "$Executable exited with code $($process.ExitCode)" }
}

if ((Test-Path $app) -or (Test-Path $regKey) -or (Test-Path $shortcut)) {
    throw "Installer smoke test requires a fresh Windows account"
}

Invoke-Setup (Resolve-Path $Installer).Path
if ((Get-ItemProperty $regKey).InstallLocation -ne $app -or -not (Test-Path $shortcut)) {
    throw "Missing per-user uninstall entry or Start menu shortcut"
}
$console = Join-Path $app "SyncPlayUConsole.exe"
$version = & $console --version-json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or
    (Get-ItemProperty $regKey).DisplayVersion -ne "$($version.upstream_version) (Night $($version.night_version))") {
    throw "Installed client and uninstall entry disagree on version"
}

python ci/windows-window-smoke.py (Join-Path $app "SyncPlayU.exe")
if ($LASTEXITCODE -ne 0) { throw "Installed GUI smoke test failed" }

$contents = "[general]" + [Environment]::NewLine + "language = en"
Set-Content -LiteralPath $config -Value $contents -NoNewline
Set-Content -LiteralPath $marker -Value "keep"
Invoke-Setup (Resolve-Path $Installer).Path
if ((Get-Content $config -Raw) -ne $contents) { throw "Reinstall changed the user's settings" }

Invoke-Setup (Join-Path $app "Uninstall.exe")
if ((Test-Path $console) -or (Test-Path (Join-Path $app "SyncPlayU.exe")) -or
    (Test-Path $regKey) -or (Test-Path $shortcut)) {
    throw "Uninstall left program files or registration behind"
}
if ((Get-Content $config -Raw) -ne $contents -or (Get-Content $marker) -ne "keep") {
    throw "Uninstall changed the user's files"
}
Write-Output "Install, GUI startup, reinstall, and uninstall passed"
