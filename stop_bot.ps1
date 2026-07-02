$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $projectDir "bot.pid"

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Output "No bot.pid file found. The bot may not be running."
    exit 0
}

$botPid = (Get-Content -LiteralPath $pidFile -Raw).Trim()
if (-not $botPid) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Output "Empty bot.pid removed."
    exit 0
}

$process = Get-Process -Id $botPid -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Output "Bot process $botPid was not running. Removed stale bot.pid."
    exit 0
}

Stop-Process -Id $botPid
Remove-Item -LiteralPath $pidFile -Force
Write-Output "Mail Digest Bot stopped. PID: $botPid"
