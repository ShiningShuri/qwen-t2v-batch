$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$dataDir = "C:\Users\user\Documents\qwen-t2v-batch\chrome-profile-2"
$port = 9224

if (-not (Test-Path $chrome)) {
    Write-Host "Chrome not found at $chrome" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

New-Item -ItemType Directory -Path $dataDir -Force | Out-Null

Write-Host "Launching Chrome #2 with remote debugging on port $port..."
Write-Host "User data dir: $dataDir"
Write-Host ""
Write-Host "Sign up / log in to chat.qwen.ai with a DIFFERENT account from Chrome #1."
Write-Host ""

& $chrome `
    "--remote-debugging-port=$port" `
    "--user-data-dir=$dataDir" `
    "--no-first-run" `
    "--no-default-browser-check" `
    "https://chat.qwen.ai/?inputFeature=t2v"
