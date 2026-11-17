<#
  Removes the Babble alarm from this PC.
    powershell -ExecutionPolicy Bypass -File Uninstall-BabbleAlarm.ps1
#>
$ErrorActionPreference = 'Stop'

Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" |
    Where-Object { $_.CommandLine -like '*BabbleAlarm.ps1*' -and $_.ProcessId -ne $PID } |
    ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }

$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Babble Alarm.lnk'
if (Test-Path $shortcutPath) { Remove-Item $shortcutPath -Force }

$appDir = Join-Path $env:LOCALAPPDATA 'BabbleAlarm'
if (Test-Path $appDir) { Remove-Item $appDir -Recurse -Force }

Write-Host 'Babble alarm removed from this PC.' -ForegroundColor Green
