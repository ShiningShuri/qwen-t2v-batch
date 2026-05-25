$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$dataDir = "C:\Users\user\Documents\qwen-t2v-batch\chrome-profile"
$port = 9222

if (-not (Test-Path $chrome)) {
    Write-Host "Chrome not found at $chrome" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

New-Item -ItemType Directory -Path $dataDir -Force | Out-Null

Write-Host "Launching Chrome with remote debugging on port $port..."
Write-Host "User data dir: $dataDir"
Write-Host ""
Write-Host "Steps:"
Write-Host "  1) Sign up / log in to chat.qwen.ai (solve the CAPTCHA once)"
Write-Host "  2) Leave this Chrome window open"
Write-Host "  3) Run run.ps1 in another PowerShell to start the batch"
Write-Host ""

& $chrome `
    "--remote-debugging-port=$port" `
    "--user-data-dir=$dataDir" `
    "--no-first-run" `
    "--no-default-browser-check" `
    "https://chat.qwen.ai/?inputFeature=t2v"
