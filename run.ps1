Set-Location -Path "C:\Users\user\Documents\qwen-t2v-batch"

$python = ".\.venv\Scripts\python.exe"
$images = "C:\Users\user\Downloads\easyflow_1779737001231\easyflow_output"
$prompts = "C:\Users\user\Documents\qwen-t2v-batch\prompts_simplified.txt"

Write-Host "===================================================="
Write-Host " Make sure you ran start-chrome.ps1 first and you"
Write-Host " are logged in to chat.qwen.ai in that Chrome window."
Write-Host "===================================================="
Write-Host ""

& $python main.py --images $images --prompts $prompts --limit 0 --tabs 1 --start 12 --cdp http://localhost:9223

Write-Host ""
Write-Host "=== Finished. Press Enter to close. ==="
[void][System.Console]::ReadLine()
