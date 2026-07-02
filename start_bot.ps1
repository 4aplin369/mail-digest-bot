$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$stdoutLog = Join-Path $projectDir "bot.out.log"
$stderrLog = Join-Path $projectDir "bot.err.log"
$pidFile = Join-Path $projectDir "bot.pid"

$process = Start-Process `
    -FilePath "python" `
    -ArgumentList "mail_digest_bot.py" `
    -WorkingDirectory $projectDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

$process.Id | Set-Content -LiteralPath $pidFile -Encoding ASCII

Write-Output "Mail Digest Bot started. PID: $($process.Id)"
Write-Output "Logs:"
Write-Output "  $stdoutLog"
Write-Output "  $stderrLog"
Write-Output "PID file:"
Write-Output "  $pidFile"
