<#
  BabbleAlarm.ps1 - rings a loud alarm on this PC when your Babble watcher sends an alert.

  It checks your private ntfy topic every 30 seconds. Install-BabbleAlarm.ps1 sets it up to
  start by itself every time you sign in to Windows.

  - Max-priority alert (the real "it's on sale" alarm): turns the volume up, plays a looping
    alarm sound and shows a big red window until you click it.
  - High-priority alert (page changed): one chime and a popup window.
  - Lower priority: ignored here (you still get it on your phone and in Gmail).

  If the PC was off when the alarm went out, it rings when you next turn the PC on
  (ntfy keeps messages for about 12 hours).

  Try it:  powershell -ExecutionPolicy Bypass -File BabbleAlarm.ps1 -Test
#>
param(
    [switch]$Test,     # ring the alarm right now so you know what it looks and sounds like
    [switch]$Once,     # check one time and exit (troubleshooting)
    [switch]$DryRun,   # print alerts instead of ringing (troubleshooting)
    [string]$ConfigPath = (Join-Path $env:LOCALAPPDATA 'BabbleAlarm\config.json')
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

$AppDir    = Split-Path -Parent $ConfigPath
$StatePath = Join-Path $AppDir 'last-message.txt'
$LogPath   = Join-Path $AppDir 'alarm.log'
$StoreUrl  = 'https://babble.diy/store/babble-tracker-pro/'
$AlarmMaxMinutes = 10   # stop the sound after this long (the window stays open)

function Write-Log([string]$Message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $Message
    Write-Host $line
    try {
        if (-not (Test-Path $AppDir)) { New-Item -ItemType Directory -Force $AppDir | Out-Null }
        if ((Test-Path $LogPath) -and (Get-Item $LogPath).Length -gt 1MB) { Remove-Item $LogPath -Force }
        Add-Content -Path $LogPath -Value $line -Encoding UTF8
    } catch { }
}

# --- Master volume control (Windows Core Audio) --------------------------------------------
if (-not ('BabbleAudio' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IBabbleAudioEndpointVolume {
    int f1(); int f2(); int f3(); int f4();
    int SetMasterVolumeLevelScalar(float fLevel, Guid pguidEventContext);
    int f6();
    int GetMasterVolumeLevelScalar(out float pfLevel);
    int f8(); int f9(); int f10(); int f11();
    int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, Guid pguidEventContext);
    int GetMute(out bool pbMute);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IBabbleMMDevice {
    int Activate(ref Guid id, int clsCtx, int activationParams, out IBabbleAudioEndpointVolume aev);
}
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IBabbleMMDeviceEnumerator {
    int f1();
    int GetDefaultAudioEndpoint(int dataFlow, int role, out IBabbleMMDevice endpoint);
}
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class BabbleMMDeviceEnumerator { }

public class BabbleAudio {
    static IBabbleAudioEndpointVolume Endpoint() {
        var enumerator = new BabbleMMDeviceEnumerator() as IBabbleMMDeviceEnumerator;
        IBabbleMMDevice dev = null;
        Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint(0, 1, out dev));
        IBabbleAudioEndpointVolume epv = null;
        var iid = typeof(IBabbleAudioEndpointVolume).GUID;
        Marshal.ThrowExceptionForHR(dev.Activate(ref iid, 23, 0, out epv));
        return epv;
    }
    public static float Volume {
        get { float v = -1; Marshal.ThrowExceptionForHR(Endpoint().GetMasterVolumeLevelScalar(out v)); return v; }
        set { Marshal.ThrowExceptionForHR(Endpoint().SetMasterVolumeLevelScalar(value, Guid.Empty)); }
    }
    public static bool Mute {
        get { bool m; Marshal.ThrowExceptionForHR(Endpoint().GetMute(out m)); return m; }
        set { Marshal.ThrowExceptionForHR(Endpoint().SetMute(value, Guid.Empty)); }
    }
}
'@
}

# --- The alarm window ------------------------------------------------------------------------
function Show-Alarm([string]$Title, [string]$Message, [string]$Url, [bool]$Loud, [string]$SentAt) {
    $player = $null
    $savedVolume = $null
    $savedMute = $null
    try {
        if ($Loud) {
            try {
                $savedVolume = [BabbleAudio]::Volume
                $savedMute = [BabbleAudio]::Mute
                [BabbleAudio]::Mute = $false
                if ($savedVolume -lt 0.8) { [BabbleAudio]::Volume = 0.8 }
            } catch { Write-Log "Could not change the volume: $($_.Exception.Message)" }
            $wav = Join-Path $env:WINDIR 'Media\Alarm01.wav'
            if (-not (Test-Path $wav)) { $wav = Join-Path $env:WINDIR 'Media\Windows Critical Stop.wav' }
            try {
                $player = New-Object System.Media.SoundPlayer $wav
                $player.PlayLooping()
            } catch { Write-Log "Could not play the alarm sound: $($_.Exception.Message)" }
        } else {
            [System.Media.SystemSounds]::Exclamation.Play()
        }

        $form = New-Object System.Windows.Forms.Form
        $form.Text = 'Babble watcher'
        $form.TopMost = $true
        $form.StartPosition = 'CenterScreen'
        $form.Size = New-Object System.Drawing.Size(680, 400)
        $form.FormBorderStyle = 'FixedDialog'
        $form.MaximizeBox = $false
        $form.MinimizeBox = $false
        $form.ShowInTaskbar = $true
        $form.BackColor = if ($Loud) { [System.Drawing.Color]::FromArgb(190, 25, 35) } else { [System.Drawing.Color]::FromArgb(35, 38, 48) }
        $form.ForeColor = [System.Drawing.Color]::White

        $titleLabel = New-Object System.Windows.Forms.Label
        $titleLabel.Text = $Title
        $titleLabel.Font = New-Object System.Drawing.Font('Segoe UI', 17, [System.Drawing.FontStyle]::Bold)
        $titleLabel.Dock = 'Top'
        $titleLabel.Height = 92
        $titleLabel.Padding = New-Object System.Windows.Forms.Padding(16, 14, 16, 0)

        $body = New-Object System.Windows.Forms.TextBox
        $body.Multiline = $true
        $body.ReadOnly = $true
        $body.ScrollBars = 'Vertical'
        $body.BorderStyle = 'None'
        $body.Font = New-Object System.Drawing.Font('Segoe UI', 11)
        $body.BackColor = $form.BackColor
        $body.ForeColor = [System.Drawing.Color]::White
        $text = $Message
        if ($SentAt) { $text = "$text`r`n`r`n(sent $SentAt)" }
        $body.Text = ($text -replace "`r?`n", "`r`n")
        $body.Dock = 'Fill'

        $bodyHolder = New-Object System.Windows.Forms.Panel
        $bodyHolder.Dock = 'Fill'
        $bodyHolder.Padding = New-Object System.Windows.Forms.Padding(18, 4, 18, 4)
        $bodyHolder.Controls.Add($body)

        $buttons = New-Object System.Windows.Forms.FlowLayoutPanel
        $buttons.Dock = 'Bottom'
        $buttons.Height = 64
        $buttons.FlowDirection = 'RightToLeft'
        $buttons.Padding = New-Object System.Windows.Forms.Padding(12, 10, 12, 10)

        $stop = New-Object System.Windows.Forms.Button
        $stop.Text = if ($Loud) { 'Stop alarm' } else { 'OK' }
        $stop.Font = New-Object System.Drawing.Font('Segoe UI', 11, [System.Drawing.FontStyle]::Bold)
        $stop.AutoSize = $true
        $stop.BackColor = [System.Drawing.Color]::White
        $stop.ForeColor = [System.Drawing.Color]::Black
        $stop.Add_Click({ $form.Close() })
        $buttons.Controls.Add($stop)

        if (-not $Url) { $Url = $StoreUrl }
        $open = New-Object System.Windows.Forms.Button
        $open.Text = 'Open store page'
        $open.Font = New-Object System.Drawing.Font('Segoe UI', 11, [System.Drawing.FontStyle]::Bold)
        $open.AutoSize = $true
        $open.BackColor = [System.Drawing.Color]::White
        $open.ForeColor = [System.Drawing.Color]::Black
        $open.Add_Click({ Start-Process $Url; $form.Close() })
        $buttons.Controls.Add($open)

        $form.Controls.Add($bodyHolder)
        $form.Controls.Add($titleLabel)
        $form.Controls.Add($buttons)
        $bodyHolder.BringToFront()   # fill the space between title and buttons
        $form.AcceptButton = $open
        $form.CancelButton = $stop

        if ($player) {
            $timer = New-Object System.Windows.Forms.Timer
            $timer.Interval = $AlarmMaxMinutes * 60 * 1000
            $timer.Add_Tick({ $player.Stop(); $timer.Stop() })
            $timer.Start()
        }
        $form.Add_Shown({ $form.Activate(); $open.Focus() })
        [void]$form.ShowDialog()
    } finally {
        if ($player) { $player.Stop(); $player.Dispose() }
        if ($null -ne $savedVolume) {
            try { [BabbleAudio]::Volume = $savedVolume; [BabbleAudio]::Mute = $savedMute } catch { }
        }
    }
}

if ($Test) {
    Show-Alarm 'TEST: Babble Steam Frame tracker alarm' `
        "This is what the real alarm looks and sounds like.`nWhen orders open you'll get this window, a loud phone alarm and a Gmail email." `
        $StoreUrl $true (Get-Date -Format 'HH:mm')
    return
}

# --- Listen for alerts ----------------------------------------------------------------------
if (-not (Test-Path $ConfigPath)) {
    Write-Log "No settings found at $ConfigPath. Run Install-BabbleAlarm.ps1 first."
    exit 1
}
$config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$server = if ($config.server) { ([string]$config.server).TrimEnd('/') } else { 'https://ntfy.sh' }
$topic  = [string]$config.topic
if ($topic -notmatch '^[-_A-Za-z0-9]{1,64}$') { Write-Log "The topic in $ConfigPath looks wrong: '$topic'"; exit 1 }

# Only one listener per Windows user.
$mutex = New-Object System.Threading.Mutex($false, 'Local\BabbleAlarmListener')
if (-not $Once -and -not $mutex.WaitOne(0)) { Write-Log 'Already running, exiting.'; exit 0 }

if (Test-Path $StatePath) {
    $since = (Get-Content $StatePath -Raw).Trim()
} else {
    $since = [string][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
}
if (-not $since) { $since = [string][DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }

Write-Log "Listening for Babble alerts on $server (topic hidden)."
while ($true) {
    try {
        $client = New-Object System.Net.WebClient
        $client.Encoding = [System.Text.Encoding]::UTF8
        $client.Headers['User-Agent'] = 'babble-alarm-pc/1.0'
        if ($config.token) { $client.Headers['Authorization'] = "Bearer $($config.token)" }
        $raw = $client.DownloadString("$server/$topic/json?poll=1&since=$since")
        $client.Dispose()

        foreach ($line in ($raw -split "`n")) {
            if (-not $line.Trim()) { continue }
            $msg = $line | ConvertFrom-Json
            if ($msg.event -ne 'message') { continue }

            $since = [string]$msg.id
            Set-Content -Path $StatePath -Value $since -Encoding ASCII

            $priority = if ($msg.priority) { [int]$msg.priority } else { 3 }
            $title = if ($msg.title) { [string]$msg.title } else { 'Babble watcher' }
            if ($priority -lt 4) { Write-Log "Skipped low-priority message: $title"; continue }

            $sentAt = ([DateTimeOffset]::FromUnixTimeSeconds([long]$msg.time)).LocalDateTime.ToString('ddd HH:mm')
            Write-Log "ALERT (priority $priority): $title"
            if ($DryRun) {
                Write-Host "[dry-run] would show $(if ($priority -ge 5) {'LOUD ALARM'} else {'popup'}): $title | $($msg.message) | sent $sentAt"
                continue
            }
            Show-Alarm $title ([string]$msg.message) ([string]$msg.click) ($priority -ge 5) $sentAt
        }
    } catch {
        Write-Log "Check failed (will retry): $($_.Exception.Message)"
    }
    if ($Once) { break }
    Start-Sleep -Seconds 30
}
