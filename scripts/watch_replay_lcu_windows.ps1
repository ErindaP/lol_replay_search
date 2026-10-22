param(
  [Parameter(Mandatory=$true)][string]$GameId,
  [string]$RiotClientPath = "C:\Riot Games\Riot Client\RiotClientServices.exe",
  [string]$LeagueDir = "C:\Riot Games\League of Legends",
  [string]$ReplayDir = "",
  [string]$GameVersion = "",
  [string]$GameType = "",
  [string]$QueueId = "",
  [switch]$LaunchClient,
  [int]$LockfileTimeoutSec = 90,
  [int]$LcuReadyTimeoutSec = 120,
  [int]$DownloadWaitSec = 10,
  [int]$DownloadFileTimeoutSec = 180,
  [int]$WatchRetries = 12,
  [int]$WatchRetrySleepSec = 5
)

$ErrorActionPreference = "Stop"
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
[System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }

function Write-Log([string]$msg) {
  [Console]::Out.WriteLine("[lcu-win] $msg")
  [Console]::Out.Flush()
}

function Short([string]$s, [int]$max = 260) {
  if ($null -eq $s) { return "" }
  $t = [string]$s
  if ($t.Length -gt $max) { return $t.Substring(0, $max) }
  return $t
}

function Normalize-JsonString([string]$s) {
  if ([string]::IsNullOrWhiteSpace($s)) { return "" }
  $t = $s.Trim()
  if ($t.StartsWith('"') -and $t.EndsWith('"')) {
    try { return ($t | ConvertFrom-Json) } catch {}
  }
  return $t
}

function Get-LockData([string]$lockfilePath) {
  $raw = Get-Content -Path $lockfilePath -Raw
  $raw = $raw.Trim()
  $parts = $raw.Split(":")
  if ($parts.Count -ne 5) {
    throw "Unexpected lockfile format: $raw"
  }
  return [pscustomobject]@{
    Process = $parts[0]
    Pid = [int]$parts[1]
    Port = [int]$parts[2]
    Password = $parts[3]
    Protocol = $parts[4]
    BaseUrl = "$($parts[4])://127.0.0.1:$($parts[2])"
  }
}

function New-LcuHeaders([string]$password) {
  $pair = "riot:$password"
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($pair)
  $b64 = [Convert]::ToBase64String($bytes)
  return [pscustomobject]@{
    Authorization = "Basic $b64"
    Accept = "application/json"
    ContentType = "application/json"
  }
}

function Invoke-Lcu([string]$method, [string]$url, [hashtable]$headers, [string]$body = $null) {
  # curl first to avoid occasional Invoke-WebRequest hangs with local LCU TLS/proxy.
  try {
    if ([string]::IsNullOrWhiteSpace($url)) {
      return [pscustomobject]@{ Ok = $false; Status = 0; Body = "curl error: empty URL" }
    }
    $tmpBody = [System.IO.Path]::GetTempFileName()
    $tmpErr = [System.IO.Path]::GetTempFileName()
    $authHeader = "Authorization: $($headers.Authorization)"
    $acceptHeader = "Accept: $($headers.Accept)"
    $ctHeader = "Content-Type: $($headers.ContentType)"

    $curlArgs = @(
      "-k", "-sS",
      "--connect-timeout", "5",
      "--max-time", "12",
      "--noproxy", "*",
      "--ipv4",
      "--http1.1",
      "--ssl-no-revoke",
      "-X", $method,
      "-H", $authHeader,
      "-H", $acceptHeader,
      "-H", $ctHeader,
      "-o", $tmpBody,
      "-w", "%{http_code}"
    )
    if ($null -ne $body) {
      $curlArgs += @("--data", $body)
    }
    $curlArgs += @("--url", $url)
    Write-Log "curl $method $url"

    $codeText = (& curl.exe @curlArgs 2> $tmpErr)
    $codeStr = [string]$codeText
    if ($null -eq $codeStr) { $codeStr = "" }
    $codeStr = $codeStr.Trim()
    $code = 0
    [void][int]::TryParse($codeStr, [ref]$code)

    $respBody = ""
    if (Test-Path $tmpBody) { $respBody = [string](Get-Content -Path $tmpBody -Raw) }
    $cerr = ""
    if (Test-Path $tmpErr) {
      $cerr = [string](Get-Content -Path $tmpErr -Raw)
      if ($null -eq $cerr) { $cerr = "" }
      $cerr = $cerr.Trim()
    }
    Remove-Item -ErrorAction SilentlyContinue $tmpBody, $tmpErr

    if ($code -gt 0) {
      return [pscustomobject]@{ Ok = ($code -lt 400); Status = $code; Body = $respBody }
    }
    return [pscustomobject]@{ Ok = $false; Status = 0; Body = ("curl error: " + $cerr) }
  } catch {
    return [pscustomobject]@{ Ok = $false; Status = 0; Body = $_.Exception.Message }
  }
}

function Force-EnsureReplayMetadata([string]$baseUrl, [hashtable]$headers, [string]$gameId, [string]$gameVersion, [string]$gameType, [string]$queueId) {
  $url2 = "$baseUrl/lol-replays/v2/metadata/$gameId/create"
  $create2 = Invoke-Lcu -method "POST" -url $url2 -headers $headers
  Write-Log "metadata v2 create status=$($create2.Status) body=$(Short $create2.Body)"

  if ($create2.Status -notin 200,201,204 -and
      -not [string]::IsNullOrWhiteSpace($gameVersion) -and
      -not [string]::IsNullOrWhiteSpace($gameType) -and
      -not [string]::IsNullOrWhiteSpace($queueId)) {
    $gv = [uri]::EscapeDataString($gameVersion)
    $gt = [uri]::EscapeDataString($gameType)
    $qid = [uri]::EscapeDataString($queueId)
    $url1 = "$baseUrl/lol-replays/v1/metadata/$gameId/create/gameVersion/$gv/gameType/$gt/queueId/$qid"
    $create1 = Invoke-Lcu -method "POST" -url $url1 -headers $headers
    Write-Log "metadata v1 create status=$($create1.Status) body=$(Short $create1.Body)"
  }
}

function Scan-Rofls([string]$baseUrl, [hashtable]$headers) {
  $scan = Invoke-Lcu -method "POST" -url "$baseUrl/lol-replays/v1/rofls/scan" -headers $headers
  Write-Log "rofl scan status=$($scan.Status) body=$(Short $scan.Body)"
}

function Get-RoflDir([string]$baseUrl, [hashtable]$headers) {
  $r1 = Invoke-Lcu -method "GET" -url "$baseUrl/lol-replays/v1/rofls/path" -headers $headers
  if ($r1.Status -in 200,201,204 -and -not [string]::IsNullOrWhiteSpace([string]$r1.Body)) {
    return (Normalize-JsonString ([string]$r1.Body))
  }
  $r2 = Invoke-Lcu -method "GET" -url "$baseUrl/lol-replays/v1/rofls/path/default" -headers $headers
  if ($r2.Status -in 200,201,204 -and -not [string]::IsNullOrWhiteSpace([string]$r2.Body)) {
    return (Normalize-JsonString ([string]$r2.Body))
  }
  return ""
}

function Same-Pathish([string]$a, [string]$b) {
  if ([string]::IsNullOrWhiteSpace($a) -or [string]::IsNullOrWhiteSpace($b)) { return $false }
  $trimChars = [char[]]@('\', '/')
  $aa = $a.Trim().TrimEnd($trimChars).Replace("\", "/").ToLowerInvariant()
  $bb = $b.Trim().TrimEnd($trimChars).Replace("\", "/").ToLowerInvariant()
  return $aa -eq $bb
}

function Set-RoflDir([string]$baseUrl, [hashtable]$headers, [string]$replayDir) {
  if ([string]::IsNullOrWhiteSpace($replayDir)) { return }
  New-Item -ItemType Directory -Path $replayDir -Force | Out-Null
  Write-Log "forcing rofl dir=$replayDir"

  $currentBefore = Get-RoflDir -baseUrl $baseUrl -headers $headers
  if (Same-Pathish $currentBefore $replayDir) {
    Write-Log "rofl dir already configured=$currentBefore"
    return
  }

  $jsonString = $replayDir | ConvertTo-Json -Compress
  $jsonObject = @{ path = $replayDir } | ConvertTo-Json -Compress
  $attempts = @(
    @{ Method = "PATCH"; Url = "$baseUrl/lol-replays/v1/rofls/path"; Body = $jsonString },
    @{ Method = "PUT";  Url = "$baseUrl/lol-replays/v1/rofls/path"; Body = $jsonString },
    @{ Method = "POST"; Url = "$baseUrl/lol-replays/v1/rofls/path"; Body = $jsonString },
    @{ Method = "PATCH"; Url = "$baseUrl/lol-replays/v1/rofls/path"; Body = $jsonObject },
    @{ Method = "PUT";  Url = "$baseUrl/lol-replays/v1/rofls/path"; Body = $jsonObject },
    @{ Method = "POST"; Url = "$baseUrl/lol-replays/v1/rofls/path"; Body = $jsonObject }
  )

  foreach ($attempt in $attempts) {
    $r = Invoke-Lcu -method $attempt.Method -url $attempt.Url -headers $headers -body $attempt.Body
    Write-Log "set rofl dir $($attempt.Method) status=$($r.Status) body=$(Short $r.Body)"
    if ($r.Status -in 200,201,204) { break }
  }

  $current = Get-RoflDir -baseUrl $baseUrl -headers $headers
  if (Same-Pathish $current $replayDir) {
    Write-Log "rofl dir confirmed=$current"
  } else {
    Write-Log "rofl dir after set=$current (wanted $replayDir)"
  }
}

function Wait-ForRoflFile([string]$replayDir, [string]$gameId, [int]$timeoutSec = 90) {
  if ([string]::IsNullOrWhiteSpace($replayDir)) { return $null }
  if (-not (Test-Path -Path $replayDir)) { return $null }
  $deadline = (Get-Date).AddSeconds($timeoutSec)
  while ((Get-Date) -lt $deadline) {
    $candidates = Get-ChildItem -Path $replayDir -Filter "*.rofl" -ErrorAction SilentlyContinue | Where-Object {
      $_.Name -like "*$gameId*" -or $_.BaseName -match "[-_]$gameId$"
    }
    if (-not $candidates -or $candidates.Count -eq 0) {
      # Fallback recurse in case client uses subfolders in replay dir.
      $candidates = Get-ChildItem -Path $replayDir -Filter "*.rofl" -Recurse -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -like "*$gameId*" -or $_.BaseName -match "[-_]$gameId$"
      }
    }
    if ($candidates -and $candidates.Count -gt 0) {
      return $candidates[0].FullName
    }
    Start-Sleep -Seconds 2
  }
  return $null
}

function Get-RecentRofls([string]$replayDir, [int]$maxItems = 5) {
  if ([string]::IsNullOrWhiteSpace($replayDir)) { return @() }
  if (-not (Test-Path -Path $replayDir)) { return @() }
  $items = Get-ChildItem -Path $replayDir -Filter "*.rofl" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First $maxItems -ExpandProperty Name
  if ($null -eq $items) { return @() }
  return @($items)
}

function Try-WatchReplay([string]$baseUrl, [hashtable]$headers, [string]$gameId, [int]$attempt = 1) {
  $watchIds = @($gameId, "EUW1-$gameId", "EUW1_$gameId")
  foreach ($wid in $watchIds) {
    $watch = Invoke-Lcu -method "POST" -url "$baseUrl/lol-replays/v1/rofls/$wid/watch" -headers $headers -body "{}"
    Write-Log "watch try=$attempt id=$wid status=$($watch.Status) body=$(Short $watch.Body)"
    if ($watch.Status -in 200,201,204) {
      return [pscustomobject]@{ Ok = $true; WatchId = $wid; Status = $watch.Status; Body = $watch.Body }
    }
  }
  return [pscustomobject]@{ Ok = $false; WatchId = ""; Status = $watch.Status; Body = $watch.Body }
}

function Wait-ForLeagueReady([string]$lockfilePath, [int]$timeoutSec) {
  $deadline = (Get-Date).AddSeconds($timeoutSec)
  $attempt = 0
  while ((Get-Date) -lt $deadline) {
    $attempt++
    try {
      $lock = Get-LockData -lockfilePath $lockfilePath
    } catch {
      Write-Log "league-ready probe attempt=$attempt lockfile parse error: $($_.Exception.Message)"
      Start-Sleep -Seconds 2
      continue
    }
    $headersObj = New-LcuHeaders -password $lock.Password
    $headers = @{
      Authorization = $headersObj.Authorization
      Accept = $headersObj.Accept
      ContentType = $headersObj.ContentType
    }

    Write-Log "league-ready probe attempt=$attempt baseUrl=$($lock.BaseUrl) port=$($lock.Port)"
    Write-Log "GET /lol-login/v1/session"
    $r1 = Invoke-Lcu -method "GET" -url "$($lock.BaseUrl)/lol-login/v1/session" -headers $headers
    Write-Log "GET /lol-summoner/v1/current-summoner"
    $r2 = Invoke-Lcu -method "GET" -url "$($lock.BaseUrl)/lol-summoner/v1/current-summoner" -headers $headers
    Write-Log "login session status=$($r1.Status) err=$(Short $r1.Body 120)"
    Write-Log "current summoner status=$($r2.Status) err=$(Short $r2.Body 120)"
    if ($r2.Status -eq 200) {
      return [pscustomobject]@{
        Lock = $lock
        Headers = $headers
      }
    }
    Start-Sleep -Seconds 2
  }
  return $null
}

function Download-ReplayWithFallback([string]$baseUrl, [hashtable]$headers, [string]$gameId, [string]$gameVersion, [string]$gameType, [string]$queueId) {
  $emptyJson = "{}"
  $dlg = Invoke-Lcu -method "POST" -url "$baseUrl/lol-replays/v1/rofls/$gameId/download/graceful" -headers $headers -body $emptyJson
  Write-Log "download/graceful status=$($dlg.Status) body=$(Short $dlg.Body)"
  if ($dlg.Status -in 200,201,204) { return $dlg }

  $msg = [string]$dlg.Body
  if ($msg -match "contextData" -and -not [string]::IsNullOrWhiteSpace($gameVersion)) {
    $payload = @{
      contextData = @{
        gameVersion = $gameVersion
        gameType    = $gameType
        queueId     = [int]$queueId
      }
    } | ConvertTo-Json -Depth 5

    $dlg2 = Invoke-Lcu -method "POST" -url "$baseUrl/lol-replays/v1/rofls/$gameId/download/graceful" -headers $headers -body $payload
    Write-Log "download/graceful(contextData) status=$($dlg2.Status) body=$(Short $dlg2.Body)"
    if ($dlg2.Status -in 200,201,204) { return $dlg2 }
  }

  $dl = Invoke-Lcu -method "POST" -url "$baseUrl/lol-replays/v1/rofls/$gameId/download" -headers $headers
  Write-Log "download status=$($dl.Status) body=$(Short $dl.Body)"
  return $dl
}

if ($LaunchClient) {
  if (-not (Test-Path -Path $RiotClientPath)) {
    throw "RiotClientServices.exe not found: $RiotClientPath"
  }
  Write-Log "launching Riot client: $RiotClientPath"
  Start-Process -FilePath $RiotClientPath -ArgumentList "--launch-product=league_of_legends","--launch-patchline=live" | Out-Null
}

$lockfile = Join-Path $LeagueDir "lockfile"
Write-Log "waiting for lockfile: $lockfile"
$deadline = (Get-Date).AddSeconds($LockfileTimeoutSec)
$attempt = 0
$fallbackLaunched = $false
$leagueClientExe = Join-Path $LeagueDir "LeagueClient.exe"
while ((Get-Date) -lt $deadline) {
  $attempt++
  if (Test-Path -Path $lockfile) {
    Write-Log "lockfile found after ${attempt}s"
    break
  }
  if (($attempt % 5) -eq 0) {
    Write-Log "lockfile not found yet (waited ${attempt}s)"
  }
  if ($LaunchClient -and -not $fallbackLaunched -and $attempt -ge 20 -and (Test-Path -Path $leagueClientExe)) {
    Write-Log "fallback launch: $leagueClientExe"
    Start-Process -FilePath $leagueClientExe -ArgumentList "--launch-patchline=live" | Out-Null
    $fallbackLaunched = $true
  }
  Start-Sleep -Seconds 1
}
if (-not (Test-Path -Path $lockfile)) {
  throw "Lockfile not found after ${LockfileTimeoutSec}s: $lockfile. Open League client manually, then rerun with --no-launch-client."
}

Write-Log "starting league ready checks"
$readyCtx = Wait-ForLeagueReady -lockfilePath $lockfile -timeoutSec $LcuReadyTimeoutSec
if ($null -eq $readyCtx) {
  throw "League client not ready (current-summoner != 200)"
}
$lock = $readyCtx.Lock
$headers = $readyCtx.Headers
Write-Log "league ready confirmed on port=$($lock.Port)"

if (-not [string]::IsNullOrWhiteSpace($ReplayDir)) {
  Set-RoflDir -baseUrl $lock.BaseUrl -headers $headers -replayDir $ReplayDir
}

Force-EnsureReplayMetadata -baseUrl $lock.BaseUrl -headers $headers -gameId $GameId -gameVersion $GameVersion -gameType $GameType -queueId $QueueId

Write-Log "starting replay download flow"
$replayDir = Get-RoflDir -baseUrl $lock.BaseUrl -headers $headers
Write-Log "rofl dir=$replayDir"

$rofl = Wait-ForRoflFile -replayDir $replayDir -gameId $GameId -timeoutSec 3
if ($null -eq $rofl) {
  $dl = Download-ReplayWithFallback -baseUrl $lock.BaseUrl -headers $headers -gameId $GameId -gameVersion $GameVersion -gameType $GameType -queueId $QueueId
  if ($dl.Status -notin 200,201,204) {
    Write-Log "download still failing (status=$($dl.Status)), trying local scan/watch fallback"
  }

  Scan-Rofls -baseUrl $lock.BaseUrl -headers $headers
  Write-Log "trying immediate watch after download request gameId=$GameId"
  $earlyWatch = Try-WatchReplay -baseUrl $lock.BaseUrl -headers $headers -gameId $GameId -attempt 1
  if ($earlyWatch.Ok) {
    Write-Log "early watch accepted id=$($earlyWatch.WatchId); waiting for local rofl before final watch"
  }

  Start-Sleep -Seconds $DownloadWaitSec
  $pollDeadline = (Get-Date).AddSeconds($DownloadFileTimeoutSec)
  $pollAttempt = 0
  while ((Get-Date) -lt $pollDeadline) {
    $pollAttempt++
    $rofl = Wait-ForRoflFile -replayDir $replayDir -gameId $GameId -timeoutSec 2
    if ($null -ne $rofl) { break }

    if (($pollAttempt % 3) -eq 1) {
      Scan-Rofls -baseUrl $lock.BaseUrl -headers $headers
      $replayDirNow = Get-RoflDir -baseUrl $lock.BaseUrl -headers $headers
      if (-not [string]::IsNullOrWhiteSpace($replayDirNow) -and $replayDirNow -ne $replayDir) {
        Write-Log "replay dir changed: $replayDirNow"
        $replayDir = $replayDirNow
      }
    }

    if (($pollAttempt % 6) -eq 0) {
      $dl2 = Download-ReplayWithFallback -baseUrl $lock.BaseUrl -headers $headers -gameId $GameId -gameVersion $GameVersion -gameType $GameType -queueId $QueueId
      Write-Log "re-trigger download poll_attempt=$pollAttempt status=$($dl2.Status)"
    }
    Start-Sleep -Seconds 3
  }
}

$roflMissing = $false
$recentRofls = @()
if ($null -eq $rofl) {
  $roflMissing = $true
  $recentRofls = Get-RecentRofls -replayDir $replayDir -maxItems 5
  Write-Log "rofl file not found locally, proceeding with scan/watch fallback"
  Write-Log "recent rofls=$(($recentRofls -join ', '))"
} else {
  Write-Log "rofl file detected: $rofl"
}

Scan-Rofls -baseUrl $lock.BaseUrl -headers $headers
Start-Sleep -Seconds 2

Write-Log "trying watch gameId=$GameId"
for ($i = 0; $i -lt $WatchRetries; $i++) {
  $watch = Try-WatchReplay -baseUrl $lock.BaseUrl -headers $headers -gameId $GameId -attempt ($i + 1)
  if ($watch.Ok) {
    Write-Output (ConvertTo-Json @{
      status="ok";
      message="watch started after download";
      game_id=$GameId;
      watch_id=$watch.WatchId;
      rofl_missing_locally=$roflMissing;
      replay_dir=$replayDir
    })
    exit 0
  }
  Start-Sleep -Seconds $WatchRetrySleepSec
}

Write-Output (ConvertTo-Json @{
  status="error";
  message="watch failed after retries";
  game_id=$GameId;
  last_status=$watch.Status;
  last_body=$watch.Body;
  rofl_missing_locally=$roflMissing;
  replay_dir=$replayDir;
  recent_rofls=$recentRofls
})
exit 3
