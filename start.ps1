param([string]$Rom, [string]$Emulator, [int]$Port = 8768, [string]$Out,
      [switch]$Smoke, [switch]$Container, [switch]$Fast)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$launcherArgs = @('start.py', '--port', "$Port")
if ($Rom) { $launcherArgs += @('--rom', $Rom) }
if ($Emulator) { $launcherArgs += @('--emulator', $Emulator) }
if ($Out) { $launcherArgs += @('--out', $Out) }
if ($Smoke) { $launcherArgs += '--smoke' }
if ($Container) { $launcherArgs += '--container' }
if ($Fast) { $launcherArgs += '--fast' }
if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 @launcherArgs }
else { & python @launcherArgs }
exit $LASTEXITCODE
