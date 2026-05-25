@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [setup] creating venv...
    python -m venv .venv
    if errorlevel 1 (
        echo Python 3.10+ required. Install python-3.14.3-amd64.exe first.
        pause
        exit /b 1
    )
)

echo [setup] installing dependencies...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\pip.exe" install --quiet -r requirements.txt
".venv\Scripts\playwright.exe" install --with-deps chromium >nul 2>&1

echo [run] http://localhost:8765 (브라우저 자동으로 열림)
start "" http://localhost:8765
".venv\Scripts\python.exe" app.py

pause
