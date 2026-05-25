Set-Location -Path "C:\Users\user\Documents\qwen-t2v-batch"

$python = ".\.venv\Scripts\python.exe"
$images = "C:\Users\user\Downloads\easyflow_1779737001231\easyflow_output"
$prompts = "C:\Users\user\Documents\qwen-t2v-batch\prompts_simplified.txt"

Write-Host "===================================================="
Write-Host " Chrome #2 (port 9224) - scene 18"
Write-Host "===================================================="

& $python main.py --images $images --prompts $prompts --start 18 --limit 1 --tabs 1 --cdp http://localhost:9224

Write-Host ""
Write-Host "=== Finished. Press Enter to close. ==="
[void][System.Console]::ReadLine()
