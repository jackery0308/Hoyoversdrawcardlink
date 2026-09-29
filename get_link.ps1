# HoYoverse draw (gacha) history link fetcher - no install needed.
#
# One-liner (PowerShell, opens a menu):
#   irm https://raw.githubusercontent.com/jackery0308/Hoyoversdrawcardlink/HEAD/get_link.ps1 | iex
#
# With options:
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/jackery0308/Hoyoversdrawcardlink/HEAD/get_link.ps1))) -Game hsr -Mode watch
#
# The script only reads files on your PC. The only network call is the optional
# check against HoYoverse's own API (skip it with -NoValidate).

param(
    [string]$Game = '',          # genshin | hsr | zzz (empty = ask)
    [string]$Mode = '',          # fetch | watch (empty = ask)
    [string]$Path = '',          # game folder or <Game>_Data folder (skip log lookup)
    [switch]$NoValidate,
    [switch]$NoCopy,
    [switch]$Clean,              # strip end_id / page so the link starts at page 1
    [double]$Interval = 1.5,
    [int]$Timeout = 0
)

$ErrorActionPreference = 'Stop'

function Get-Str([int[]]$codes) { -join ($codes | ForEach-Object { [char]$_ }) }
# CN folder names, built from code points so the file stays ASCII (PS 5.1 safe).
$cnGenshin = Get-Str 0x539F, 0x795E
$cnStarRail = Get-Str 0x5D29, 0x574F, 0xFF1A, 0x661F, 0x7A79, 0x94C1, 0x9053
$cnZzz = Get-Str 0x7EDD, 0x533A, 0x96F6

$Games = [ordered]@{
    genshin = @{
        Name = 'Genshin Impact'
        Logs = @('miHoYo/Genshin Impact/output_log.txt', 'miHoYo/Genshin Impact/output_log.txt.last',
                 "miHoYo/$cnGenshin/output_log.txt", "miHoYo/$cnGenshin/output_log.txt.last")
        DataDirs = @('GenshinImpact_Data', 'YuanShen_Data')
    }
    hsr = @{
        Name = 'Honkai: Star Rail'
        Logs = @('Cognosphere/Star Rail/Player.log', 'Cognosphere/Star Rail/Player-prev.log',
                 "miHoYo/$cnStarRail/Player.log", "miHoYo/$cnStarRail/Player-prev.log")
        DataDirs = @('StarRail_Data')
    }
    zzz = @{
        Name = 'Zenless Zone Zero'
        Logs = @('miHoYo/ZenlessZoneZero/Player.log', 'miHoYo/ZenlessZoneZero/Player-prev.log',
                 "miHoYo/$cnZzz/Player.log", "miHoYo/$cnZzz/Player-prev.log")
        DataDirs = @('ZenlessZoneZero_Data')
    }
}

function Fail([string]$msg) { throw [System.Exception]::new($msg) }

function Find-DataDir($g) {
    $locallow = Join-Path $env:USERPROFILE 'AppData/LocalLow'
    $names = ($g.DataDirs | ForEach-Object { [regex]::Escape($_) }) -join '|'
    $pattern = '([A-Za-z]:[\\/][^\r\n:*?"<>|]*?(?:' + $names + '))(?=[\\/]|\s|$)'
    $tried = @()
    foreach ($rel in $g.Logs) {
        $log = [IO.Path]::Combine($locallow, $rel)
        $tried += $log
        if (-not (Test-Path -LiteralPath $log -PathType Leaf)) { continue }
        $text = Read-Shared $log
        $text = [Text.Encoding]::UTF8.GetString($text)
        $found = [regex]::Matches($text, $pattern)
        if ($found.Count -eq 0) { continue }
        for ($i = $found.Count - 1; $i -ge 0; $i--) {
            $p = $found[$i].Groups[1].Value
            if (Test-Path -LiteralPath $p -PathType Container) { return $p }
        }
        return $found[$found.Count - 1].Groups[1].Value
    }
    Fail ("Could not find the $($g.Name) install folder from its log file.`n" +
          "Start the game at least once, or pass -Path.`nLooked in:`n  " + ($tried -join "`n  "))
}

function Resolve-DataDir([string]$p, $g) {
    if ($g.DataDirs -contains (Split-Path $p -Leaf) -or (Test-Path -LiteralPath ([IO.Path]::Combine($p, 'webCaches')))) { return $p }
    foreach ($n in $g.DataDirs) {
        $c = [IO.Path]::Combine($p, $n)
        if (Test-Path -LiteralPath $c -PathType Container) { return $c }
    }
    return $p
}

function Find-CacheFile([string]$dataDir) {
    $web = [IO.Path]::Combine($dataDir, 'webCaches')
    $candidates = @()
    if (Test-Path -LiteralPath $web) {
        $candidates += Get-ChildItem -LiteralPath $web -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { [IO.Path]::Combine($_.FullName, 'Cache/Cache_Data/data_2') }
    }
    $candidates += [IO.Path]::Combine($web, 'Cache/Cache_Data/data_2')
    $files = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | ForEach-Object { Get-Item -LiteralPath $_ }
    if (-not $files) { Fail "No web cache found under:`n  $web`nOpen the draw history page in-game at least once." }
    return ($files | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1).FullName
}

# Read a file the game may have open.
function Read-Shared([string]$file) {
    $fs = [IO.File]::Open($file, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]'ReadWrite, Delete')
    try {
        $ms = New-Object IO.MemoryStream
        $fs.CopyTo($ms)
        return , $ms.ToArray()
    } finally { $fs.Close() }
}

function Get-LatestLink([string]$cacheFile) {
    $text = [Text.Encoding]::GetEncoding(28591).GetString((Read-Shared $cacheFile))
    $urls = [regex]::Matches($text, 'https://[\x21-\x7e]+') | ForEach-Object { $_.Value } |
        Where-Object { $_ -like '*authkey=*' -and ($_ -like '*GachaLog*' -or $_ -like '*gacha*') }
    if (-not $urls) { return $null }
    $api = @($urls | Where-Object { $_ -like '*GachaLog*' })
    if ($api.Count) { return $api[-1] }
    return @($urls)[-1]
}

function Get-CleanUrl([string]$url) {
    $parts = $url -split '\?', 2
    if ($parts.Count -lt 2) { return $url }
    $keep = $parts[1] -split '&' | Where-Object { ($_ -split '=')[0] -notin @('end_id', 'begin_id', 'page') }
    return $parts[0] + '?' + ($keep -join '&')
}

function Test-Link([string]$url) {
    if ($url -notlike '*GachaLog*') { return 'skipped (not an API URL)' }
    try {
        $r = Invoke-RestMethod -Uri $url -UseBasicParsing -TimeoutSec 10
        if ($r.retcode -eq 0) { return 'valid' }
        return "INVALID (retcode $($r.retcode): $($r.message))"
    } catch { return "check failed: $($_.Exception.Message)" }
}

function Show-Link([string]$url) {
    if ($Clean) { $url = Get-CleanUrl $url }
    Write-Host "`nDraw history link:`n" -ForegroundColor Green
    Write-Host $url
    Write-Host ''
    if (-not $NoValidate) { Write-Host "API check : $(Test-Link $url)" }
    if (-not $NoCopy) {
        try { Set-Clipboard -Value $url; Write-Host 'Clipboard : copied' }
        catch { Write-Host 'Clipboard : unavailable' }
    }
}

function Read-Choice([string]$prompt, [string[]]$options) {
    for ($i = 0; $i -lt $options.Count; $i++) { Write-Host "  $($i + 1). $($options[$i])" }
    $c = Read-Host "`n$prompt [1]"
    $n = 0
    if ([int]::TryParse($c, [ref]$n) -and $n -ge 1 -and $n -le $options.Count) { return $n - 1 }
    return 0
}

# Uses parameters, not $ scope, so it also works when run as a scriptblock.
function Invoke-Main([string]$Game, [string]$Mode) {
    if (-not $Game) {
        Write-Host "HoYoverse draw history link fetcher`n" -ForegroundColor Cyan
        $keys = @($Games.Keys)
        $Game = $keys[(Read-Choice 'Select game' @($keys | ForEach-Object { $Games[$_].Name }))]
        Write-Host ''
        if (-not $Mode) {
            $m = Read-Choice 'Select mode' @('I already opened the history page -> fetch now',
                                             "Start watching, then I'll open the history page")
            $Mode = @('fetch', 'watch')[$m]
        }
    }
    if (-not $Mode) { $Mode = 'fetch' }
    if (-not $Games.Contains($Game)) { Fail "Unknown game '$($Game)'. Use genshin, hsr or zzz." }
    $g = $Games[$Game]

    if ($Path) { $dataDir = Resolve-DataDir $Path $g } else { $dataDir = Find-DataDir $g }
    Write-Host "Game data folder : $dataDir"

    if ($Mode -eq 'watch') {
        $baseline = $null
        try { $baseline = Get-LatestLink (Find-CacheFile $dataDir) } catch { }
        Write-Host "`nWaiting for you to open the $($g.Name) history page (or the next page)... Ctrl+C to stop." -ForegroundColor Yellow
        $start = Get-Date
        while ($Timeout -le 0 -or ((Get-Date) - $start).TotalSeconds -lt $Timeout) {
            Start-Sleep -Milliseconds ([int]($Interval * 1000))
            try { $url = Get-LatestLink (Find-CacheFile $dataDir) } catch { continue }
            if ($url -and $url -ne $baseline) { Show-Link $url; return }
        }
        Fail 'Timed out waiting for a new link.'
    }

    $cache = Find-CacheFile $dataDir
    Write-Host "Web cache file   : $cache"
    $url = Get-LatestLink $cache
    if (-not $url) {
        Fail "No draw history link in the cache yet.`nOpen the history page in-game, then run again (or use watch mode)."
    }
    Show-Link $url
}

try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
try { Invoke-Main $Game $Mode }
catch { Write-Host "`nError: $($_.Exception.Message)" -ForegroundColor Red }
