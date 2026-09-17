param(
    [string]$Rom,
    [string]$Emulator,
    [int]$Port = 8768,
    [string]$Out = ('runs/play-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    & py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
& ./.venv/Scripts/python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (-not $Rom) { $Rom = Read-Host 'Path to your local Super Mario Bros. PAL .nes file' }
if (-not $Emulator) { $Emulator = Read-Host 'Path to your separately installed EmuHawk.exe' }
& ./.venv/Scripts/python.exe jev.py doctor --rom $Rom --emulator $Emulator
if ($LASTEXITCODE -ne 0) { throw 'Fix the missing dependencies above before starting.' }
if (-not $env:TYPESAFE_API_KEY) {
    $jevSecret = Read-Host 'TypeSafe API key (hidden; not saved)' -AsSecureString
    $env:TYPESAFE_API_KEY = [System.Net.NetworkCredential]::new('', $jevSecret).Password
}
& ./.venv/Scripts/python.exe jev.py play --rom $Rom --emulator $Emulator --out $Out --watch --port $Port --allow-warps --wall-seconds 600 --max-decisions 100 --max-rewinds 100 --max-input-tokens 500000
exit $LASTEXITCODE
