# run-bot.ps1
Set-Location "C:\Users\bornt\Downloads\files"
Write-Host "Current Directory: $(Get-Location)" -ForegroundColor Yellow

$envVars = [ordered]@{
    "ZENEX_API_KEY"      = "ZNX_JTC08MA15EA8EO4LGIWZYZBI"
    "VOLTXSMS_API_KEY"   = "MSJ85HEVURM"
    "TELEGRAM_BOT_TOKEN" = "9031274566:AAFKfs4-FuUcRzYvudXpf-2cWIgt8YZdel0"
    "GITHUB_TOKEN"       = "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"
    "GITHUB_REPO"        = "tasfik222/voltx-data"
    "CHANNEL_ID"         = "-1003683460928"
}

foreach ($key in $envVars.Keys) {
    [System.Environment]::SetEnvironmentVariable($key, $envVars[$key], "Process")
    Write-Host "[OK] $key set" -ForegroundColor Green
}

Write-Host "`nসব environment variable সেট হয়েছে।`n" -ForegroundColor Cyan

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Python পাওয়া যায়নি!" -ForegroundColor Red; exit 1
}
if (-not (Test-Path ".\bot.py")) {
    Write-Host "[ERROR] bot.py পাওয়া যায়নি!" -ForegroundColor Red; exit 1
}

Write-Host "🚀 Bot চালু হচ্ছে... (বন্ধ করতে Ctrl+C)`n" -ForegroundColor Cyan
python bot.py