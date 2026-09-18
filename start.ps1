# Accept both PowerShell-style switches (-Port) and Python-style switches (--port).
# Keep values as separate arguments so spaces in asset paths are preserved.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$optionNames = @{
    '-Rom' = '--rom'; '-Emulator' = '--emulator'; '-Port' = '--port'
    '-Out' = '--out'; '-Smoke' = '--smoke'; '-Container' = '--container'
    '-Fast' = '--fast'; '-Full' = '--full'; '-Turbo' = '--turbo'
    '-UntilComplete' = '--until-complete'; '-StuckFrames' = '--stuck-frames'
}
$launcherArgs = @('start.py')
foreach ($argument in $args) {
    if ($optionNames.ContainsKey([string]$argument)) {
        $launcherArgs += $optionNames[[string]$argument]
    } else {
        $launcherArgs += $argument
    }
}
if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 @launcherArgs }
else { & python @launcherArgs }
exit $LASTEXITCODE
