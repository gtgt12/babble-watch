<#
  Installs the Babble alarm on this Windows PC (no admin needed).

  Run it once on each PC/laptop that should ring:
    powershell -ExecutionPolicy Bypass -File Install-BabbleAlarm.ps1 -Topic <your-ntfy-topic>

  It copies BabbleAlarm.ps1 to %LOCALAPPDATA%\BabbleAlarm, adds it to your Windows Startup
  folder so it starts every time you sign in, and starts it right away.
#>
param(
    [Parameter(Mandatory = $true)][string]$Topic,
    [string]$Server = 'https://ntfy.sh'
)
$ErrorActionPreference = 'Stop'

$Topic = $Topic.Trim()
if ($Topic -notmatch '^[-_A-Za-z0-9]{1,64}$') { throw "That doesn't look like an ntfy topic name: '$Topic'" }

$appDir = Join-Path $env:LOCALAPPDATA 'BabbleAlarm'
New-Item -ItemType Directory -Force $appDir | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'BabbleAlarm.ps1') (Join-Path $appDir 'BabbleAlarm.ps1') -Force
[ordered]@{ topic = $Topic; server = $Server.TrimEnd('/') } | ConvertTo-Json |
    Set-Content (Join-Path $appDir 'config.json') -Encoding UTF8

# Stop an older copy if one is running, so the new settings are used.
Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" |
    Where-Object { $_.CommandLine -like '*BabbleAlarm.ps1*' -and $_.ProcessId -ne $PID } |
    ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }

$psExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$appDir\BabbleAlarm.ps1`""

$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Babble Alarm.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $psExe
$shortcut.Arguments = $arguments
$shortcut.WindowStyle = 7
$shortcut.Description = 'Rings an alarm when the Babble Steam Frame tracker goes on sale'
$shortcut.Save()

Start-Process -FilePath $psExe -ArgumentList $arguments -WindowStyle Hidden

Write-Host ''
Write-Host 'Babble alarm installed and running.' -ForegroundColor Green
Write-Host "  Starts automatically when you sign in (shortcut: $shortcutPath)"
Write-Host "  Log file: $appDir\alarm.log"
Write-Host ''
Write-Host 'Hear what it sounds like:'
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$appDir\BabbleAlarm.ps1`" -Test"
