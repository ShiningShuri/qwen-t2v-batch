$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$dataDir = "C:\Users\user\Documents\qwen-t2v-batch\edge-profile"
$port = 9223

if (-not (Test-Path $edge)) {
    Write-Host "Edge not found at $edge" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

New-Item -ItemType Directory -Path $dataDir -Force | Out-Null

Write-Host "Launching Edge with remote debugging on port $port..."
Write-Host "User data dir: $dataDir"
Write-Host ""
Write-Host "Steps:"
Write-Host "  1) Sign up / log in to chat.qwen.ai (different account from Chrome)"
Write-Host "  2) Leave this Edge window open"
Write-Host "  3) In run.ps1, change --cdp to http://localhost:$port"
Write-Host ""

& $edge `
    "--remote-debugging-port=$port" `
    "--user-data-dir=$dataDir" `
    "--no-first-run" `
    "--no-default-browser-check" `
    "https://chat.qwen.ai/?inputFeature=t2v"
