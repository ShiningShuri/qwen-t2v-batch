Set-Location -Path "C:\Users\user\Documents\qwen-t2v-batch"

$python = ".\.venv\Scripts\python.exe"
$images = "C:\Users\user\Downloads\easyflow_1779737001231\easyflow_output"
$prompts = "C:\Users\user\Documents\qwen-t2v-batch\prompts_simplified.txt"

Write-Host "===================================================="
Write-Host " Chrome #1 (port 9222) - scenes 13, 14, 15"
Write-Host "===================================================="

& $python main.py --images $images --prompts $prompts --start 13 --limit 3 --tabs 1 --cdp http://localhost:9222

Write-Host ""
Write-Host "=== Finished. Press Enter to close. ==="
[void][System.Console]::ReadLine()
