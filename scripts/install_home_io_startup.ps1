<#!
.SYNOPSIS
Installs a per-user Windows startup entry for the EcoNest Home I/O bridge.

.DESCRIPTION
The task starts only after the user signs into Windows.  This is intentional:
Home I/O is an interactive desktop application, and its optional visual preview
needs an active, unlocked desktop session.
#>

[CmdletBinding()]
param(
    [string]$BridgeDirectory = (Join-Path $env:USERPROFILE "EcoNestHomeIO"),
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311-32\python.exe"),
    [string]$HomeIOPath = "C:\Program Files (x86)\Real Games\Home IO\Home I/O.exe",
    [string]$TailscaleAddress = "100.79.150.85",
    [int]$Port = 8877
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $BridgeDirectory)) {
    throw "Bridge directory was not found: $BridgeDirectory"
}
if (-not (Test-Path $PythonPath)) {
    throw "32-bit Python was not found: $PythonPath"
}
if (-not (Test-Path $HomeIOPath)) {
    throw "Home I/O executable was not found: $HomeIOPath"
}
if (-not (Test-Path (Join-Path $BridgeDirectory "home_io_desktop_bridge.py"))) {
    throw "home_io_desktop_bridge.py was not found in $BridgeDirectory"
}

$secureToken = Read-Host "Paste the Home I/O bridge token" -AsSecureString
$tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try {
    $bridgeToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
}
if ([string]::IsNullOrWhiteSpace($bridgeToken)) {
    throw "A bridge token is required."
}

$environmentFile = Join-Path $BridgeDirectory "home-io-bridge.env"
@"
PYTHONNET_RUNTIME=netfx
HOME_IO_SDK_PATH=C:\Program Files (x86)\Real Games\Connect IO
HOME_IO_BRIDGE_MAP={""virtual_living_light"":{""address"":0,""memory_type"":""Output"",""writable"":true}}
HOME_IO_BRIDGE_TOKEN=$bridgeToken
HOME_IO_WINDOW_TITLE=Home I/O
"@ | Set-Content -Path $environmentFile -Encoding ascii

$launcherPath = Join-Path $BridgeDirectory "start-home-io-bridge.cmd"
$logPath = Join-Path $BridgeDirectory "home-io-bridge.log"
@"
@echo off
setlocal EnableExtensions
for /f "usebackq tokens=1,* delims==" %%A in ("$environmentFile") do (
  if not "%%A"=="" set "%%A=%%B"
)
tasklist /FI "IMAGENAME eq Home I/O.exe" /NH | find /I "Home I/O.exe" >nul || start "" "$HomeIOPath"
timeout /t 8 /nobreak >nul
"$PythonPath" -m uvicorn home_io_desktop_bridge:app --host $TailscaleAddress --port $Port >> "$logPath" 2>&1
"@ | Set-Content -Path $launcherPath -Encoding ascii

$startupDirectory = [Environment]::GetFolderPath("Startup")
$startupLauncher = Join-Path $startupDirectory "EcoNest Home I-O Bridge.vbs"
@"
' Start the long-running bridge without leaving a command window on screen.
CreateObject("Wscript.Shell").Run Chr(34) & "$launcherPath" & Chr(34), 0, False
"@ | Set-Content -Path $startupLauncher -Encoding ascii

Write-Host "EcoNest Home I/O startup entry installed:" -ForegroundColor Green
Write-Host "  $startupLauncher"
Write-Host "It will start Home I/O and the private bridge at your next Windows sign-in."
Write-Host "Bridge diagnostics are written to: $logPath"
Write-Host "The Home I/O window must remain restored for the on-page visual preview."
