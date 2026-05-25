Set-Location -Path "C:\Users\user\Documents\qwen-t2v-batch"

$python = ".\.venv\Scripts\python.exe"
$images = "C:\Users\user\Downloads\아는아이의 플로우플로우 1.3.0\easyflow\리메이크"
$prompts = "C:\Users\user\Documents\qwen-t2v-batch\prompts_remake.txt"

Write-Host "===================================================="
Write-Host " Chrome #3 (port 9226) - remake scenes (suffix=remake)"
Write-Host "===================================================="

& $python main.py --images $images --prompts $prompts --start 5 --limit 0 --tabs 1 --cdp http://localhost:9226 --suffix remake

Write-Host ""
Write-Host "=== Finished. Press Enter to close. ==="
[void][System.Console]::ReadLine()
