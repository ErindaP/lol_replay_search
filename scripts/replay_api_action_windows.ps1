param(
  [Parameter(Mandatory=$true)][double]$SeekTimeS,
  [string]$ReplayApiBaseUrl = "https://127.0.0.1:2999",
  [double]$CameraX = [double]::NaN,
  [double]$CameraY = [double]::NaN,
  [switch]$NoRecord,
  [double]$StartTimeS = 0.0,
  [double]$EndTimeS = 0.0,
  [string]$Codec = "webm",
  [string]$OutputPath = "",
  [int]$ReadyTimeoutSec = 20,
  [int]$RecordTimeoutSec = 360
)

$ErrorActionPreference = "Stop"
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
[System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }

function Write-Log([string]$msg) {
  [Console]::Out.WriteLine("[replay-win] $msg")
  [Console]::Out.Flush()
}

function Invoke-ReplayApi([string]$method, [string]$path, [object]$body = $null) {
  $url = "$ReplayApiBaseUrl$path"
  try {
    $tmpBody = [System.IO.Path]::GetTempFileName()
    $tmpErr = [System.IO.Path]::GetTempFileName()
    $tmpJson = $null
    $args = @(
      "-k", "-sS",
      "--connect-timeout", "3",
      "--max-time", "8",
      "--noproxy", "*",
      "--ipv4",
      "--http1.1",
      "--ssl-no-revoke",
      "-X", $method,
      "-H", "Content-Type: application/json",
      "-o", $tmpBody,
      "-w", "%{http_code}",
      "--url", $url
    )
    if ($null -ne $body) {
      $json = $body | ConvertTo-Json -Depth 8 -Compress
      $tmpJson = [System.IO.Path]::GetTempFileName()
      $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
      [System.IO.File]::WriteAllText($tmpJson, $json, $utf8NoBom)
      $args += @("--data-binary", "@$tmpJson")
    }

    $codeText = (& curl.exe @args 2> $tmpErr)
    $codeStr = [string]$codeText
    if ($null -eq $codeStr) { $codeStr = "" }
    $codeStr = $codeStr.Trim()
    $code = 0
    [void][int]::TryParse($codeStr, [ref]$code)

    $text = ""
    if (Test-Path $tmpBody) { $text = [string](Get-Content -Path $tmpBody -Raw) }
    $cerr = ""
    if (Test-Path $tmpErr) { $cerr = [string](Get-Content -Path $tmpErr -Raw) }
    if ($null -ne $tmpJson) {
      Remove-Item -ErrorAction SilentlyContinue $tmpJson
    }
    Remove-Item -ErrorAction SilentlyContinue $tmpBody, $tmpErr

    $payload = $null
    if (-not [string]::IsNullOrWhiteSpace($text)) {
      try { $payload = $text | ConvertFrom-Json } catch { $payload = $text }
    }
    if ($code -gt 0) {
      return [pscustomobject]@{ Ok = ($code -lt 400); Status = $code; Body = $payload; Text = $text }
    }
    return [pscustomobject]@{ Ok = $false; Status = 0; Body = $null; Text = ("curl error: " + $cerr) }
  } catch {
    return [pscustomobject]@{ Ok = $false; Status = 0; Body = $null; Text = [string]$_.Exception.Message }
  }
}

function Wait-ReplayReady([int]$timeoutSec) {
  $deadline = (Get-Date).AddSeconds($timeoutSec)
  $attempt = 0
  while ((Get-Date) -lt $deadline) {
    $attempt++
    $r = Invoke-ReplayApi -method "GET" -path "/replay/game"
    if ($r.Status -eq 200) {
      Write-Log "Replay API ready attempt=$attempt"
      return $true
    }
    Start-Sleep -Seconds 1
  }
  return $false
}

if (-not (Wait-ReplayReady -timeoutSec $ReadyTimeoutSec)) {
  Write-Output (ConvertTo-Json @{ status = "error"; message = "Replay API not ready"; base_url = $ReplayApiBaseUrl } -Compress)
  exit 2
}

function Get-PlaybackTimeSec() {
  $pb = Invoke-ReplayApi -method "GET" -path "/replay/playback"
  if ($pb.Status -ne 200 -or $null -eq $pb.Body) { return -1.0 }
  $candidates = @($pb.Body.time, $pb.Body.currentTime, $pb.Body.position)
  foreach ($v in $candidates) {
    if ($null -eq $v) { continue }
    $n = 0.0
    if ([double]::TryParse([string]$v, [ref]$n)) {
      if ($n -gt 100000.0) { return ($n / 1000.0) } # ms reported
      return $n
    }
  }
  return -1.0
}

function Get-GameLengthSec() {
  function Coerce-Sec([object]$v) {
    if ($null -eq $v) { return 0.0 }
    $n = 0.0
    if (-not [double]::TryParse([string]$v, [ref]$n)) { return 0.0 }
    if ($n -le 0.0) { return 0.0 }
    # Heuristic: values above 100k are almost certainly milliseconds.
    if ($n -gt 100000.0) { return ($n / 1000.0) }
    return $n
  }

  $vals = @()

  $g = Invoke-ReplayApi -method "GET" -path "/replay/game"
  if ($g.Status -eq 200 -and $null -ne $g.Body) {
    $vals += @(
      $g.Body.gameLength,
      $g.Body.gameLengthSeconds,
      $g.Body.length,
      $g.Body.duration
    )
  }

  # Fallback: some clients expose duration fields on playback endpoint.
  $pb = Invoke-ReplayApi -method "GET" -path "/replay/playback"
  if ($pb.Status -eq 200 -and $null -ne $pb.Body) {
    $vals += @(
      $pb.Body.length,
      $pb.Body.duration,
      $pb.Body.totalTime,
      $pb.Body.endTime
    )
  }

  foreach ($v in $vals) {
    $s = Coerce-Sec $v
    if ($s -gt 0.0) { return $s }
  }
  return 0.0
}

$gameLen = Get-GameLengthSec
$safeSeek = [double]$SeekTimeS
$safeStart = [double]$StartTimeS
$safeEnd = [double]$EndTimeS
if ($gameLen -gt 0.0) {
  $maxSeek = [Math]::Max(0.0, $gameLen - 1.0)
  if ($safeSeek -gt $maxSeek) { $safeSeek = $maxSeek }
  if ($safeStart -lt 0.0) { $safeStart = 0.0 }
  if ($safeStart -gt $maxSeek) { $safeStart = $maxSeek }
  if ($safeEnd -le $safeStart) { $safeEnd = $safeStart + 0.5 }
  if ($safeEnd -gt $gameLen) { $safeEnd = $gameLen }
  if (($safeEnd - $safeStart) -lt 0.5) {
    $safeStart = [Math]::Max(0.0, $gameLen - 2.0)
    $safeEnd = $gameLen
  }
}
Write-Log "game_length_s=$gameLen seek_s=$safeSeek start_s=$safeStart end_s=$safeEnd"

$timeMultiplier = 1.0
$setOk = $false
foreach ($mult in @(1.0, 1000.0)) {
  $playbackPayload = @{
    paused = $true
    time = [double]($safeSeek * $mult)
    speed = 1.0
  }
  $pb = Invoke-ReplayApi -method "POST" -path "/replay/playback" -body $playbackPayload
  Write-Log "set playback status=$($pb.Status) with_time=$($safeSeek * $mult)"
  if ($pb.Status -notin 200,201,204) { continue }

  $seekDeadline = (Get-Date).AddSeconds(8)
  $closest = 1e9
  $lastCur = -1.0
  while ((Get-Date) -lt $seekDeadline) {
    $cur = Get-PlaybackTimeSec
    if ($cur -ge 0.0) {
      $lastCur = $cur
      $delta = [Math]::Abs($cur - $safeSeek)
      if ($delta -lt $closest) { $closest = $delta }
      if ($delta -le 2.0) {
        $setOk = $true
        $timeMultiplier = $mult
        break
      }
    }
    Start-Sleep -Milliseconds 250
  }
  Write-Log "playback probe mult=$mult current_s=$lastCur closest_delta_s=$closest"
  if ($setOk) { break }
}
if (-not $setOk) {
  Write-Log "playback did not converge to target seek; continuing with best effort"
}
Write-Log "selected_time_multiplier=$timeMultiplier"

$cameraSet = $false
if (-not [double]::IsNaN($CameraX) -and -not [double]::IsNaN($CameraY)) {
  $renderPayload = @{
    cameraPosition = @{
      x = [double]$CameraX
      y = [double]$CameraY
      z = 0.0
    }
  }
  $cam = Invoke-ReplayApi -method "POST" -path "/replay/render" -body $renderPayload
  Write-Log "set camera status=$($cam.Status)"
  $cameraSet = $cam.Status -in 200,201,204
}

if ($NoRecord) {
  Write-Output (ConvertTo-Json @{
    status = "ok"
    message = "seek/camera done"
    seek_s = $SeekTimeS
    camera_set = $cameraSet
  } -Compress)
  exit 0
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
  $fallbackDir = Join-Path $env:TEMP "lol_replay_clips"
  New-Item -ItemType Directory -Path $fallbackDir -Force | Out-Null
  $OutputPath = Join-Path $fallbackDir ("clip_" + [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() + ".mp4")
}
$outDir = Split-Path -Path $OutputPath -Parent
if (-not [string]::IsNullOrWhiteSpace($outDir)) {
  New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}

$codecNorm = [string]$Codec
if ($codecNorm -eq "libx264" -or $codecNorm -eq "h264" -or $codecNorm -eq "mp4") { $codecNorm = "webm" }
$codecCandidates = @($codecNorm, "webm", "WEBM", "mkv", "MKV", "avi", "AVI")
$seen = @{}
$ordered = @()
foreach ($c in $codecCandidates) {
  if (-not [string]::IsNullOrWhiteSpace($c) -and -not $seen.ContainsKey($c)) {
    $seen[$c] = $true
    $ordered += $c
  }
}

$startRec = $null
$selectedCodec = $null
foreach ($candidate in $ordered) {
  $recordPayload = @{
    path = $OutputPath
    codec = $candidate
    startTime = [double]($safeStart * $timeMultiplier)
    endTime = [double]($safeEnd * $timeMultiplier)
  }
  $startRec = Invoke-ReplayApi -method "POST" -path "/replay/recording" -body $recordPayload
  Write-Log "start recording codec=$candidate status=$($startRec.Status)"
  if ($startRec.Status -in 200,201,204) {
    $selectedCodec = $candidate
    break
  }
  $msg = [string]$startRec.Text
  if ($startRec.Status -eq 400 -and $msg -match "AVContainer enumeration value") {
    continue
  }
  break
}

if ($null -eq $selectedCodec) {
  Write-Output (ConvertTo-Json @{
    status = "error"
    message = "recording start failed"
    http_status = $startRec.Status
    body = $startRec.Text
    codec_requested = $Codec
    codec_tried = $ordered
  } -Compress)
  exit 3
}

$deadline = (Get-Date).AddSeconds($RecordTimeoutSec)
$last = $null
while ((Get-Date) -lt $deadline) {
  $rs = Invoke-ReplayApi -method "GET" -path "/replay/recording"
  $last = $rs
  if ($rs.Status -eq 200) {
    $phase = ""
    $isRecording = $false
    if ($rs.Body) {
      try { $phase = [string]$rs.Body.phase } catch {}
      try { $isRecording = [bool]$rs.Body.recording } catch {}
    }
    $phaseLower = $phase.ToLowerInvariant()
    if (($phaseLower -in @("complete", "completed", "idle")) -and (-not $isRecording)) {
      Write-Output (ConvertTo-Json @{
        status = "ok"
        message = "recording complete"
        output = $OutputPath
        codec = $selectedCodec
        seek_s = $safeSeek
        start_s = $safeStart
        end_s = $safeEnd
        camera_set = $cameraSet
        recording = $rs.Body
      } -Compress)
      exit 0
    }
    if ($phaseLower -in @("error", "failed")) {
      Write-Output (ConvertTo-Json @{
        status = "error"
        message = "recording failed"
        output = $OutputPath
        codec = $selectedCodec
        recording = $rs.Body
      } -Compress)
      exit 4
    }
  }
  Start-Sleep -Seconds 1
}

Write-Output (ConvertTo-Json @{
  status = "error"
  message = "recording timeout"
  output = $OutputPath
  codec = $selectedCodec
  last_status = $last.Status
  last_body = $last.Text
} -Compress)
exit 5
