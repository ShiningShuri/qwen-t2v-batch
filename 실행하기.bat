@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Qwen i2v Studio

rem ============================================================
rem  Qwen i2v Studio - end-user launcher (no Python install)
rem  Uses the embedded Python that ships inside this folder.
rem  First run: installs deps + downloads Chromium (2-3 min).
rem ============================================================

set "PYDIR=%~dp0python"
set "PY=%PYDIR%\python.exe"
set "MARKER=%~dp0python\.deps-installed"
set "PREBUILT=%~dp0python\.deps-prebuilt"

if not exist "%PY%" (
    echo [오류] python 폴더가 없습니다.
    echo 이 zip을 받은 그대로 압축을 풀고, 폴더 통째로 실행하세요.
    echo (python 폴더가 같이 들어있어야 합니다^)
    echo.
    pause
    exit /b 1
)

if not exist "%MARKER%" (
    echo ============================================
    echo  처음 실행 - 준비 중입니다 ^(한 번만^)
    echo  인터넷 연결이 필요합니다.
    echo ============================================
    echo.

    rem When the zip already bundles the Python libraries (.deps-prebuilt),
    rem skip pip + dependency install; only Chromium remains.
    if not exist "%PREBUILT%" (
        echo [1/2] 패키지 관리자 + 라이브러리 설치... ^(시간이 좀 걸립니다^)
        "%PY%" -m pip --version >nul 2>&1
        if errorlevel 1 "%PY%" "%~dp0get-pip.py" --no-warn-script-location
        "%PY%" -m pip install --no-warn-script-location -r "%~dp0requirements.txt"
        if errorlevel 1 (
            echo.
            echo [오류] 라이브러리 설치 실패. 인터넷 연결을 확인하고 다시 실행하세요.
            pause
            exit /b 1
        )
    ) else (
        echo [1/2] 라이브러리 준비됨 ^(포함되어 있어 건너뜀^)
    )

    echo [2/2] 브라우저 엔진 설치... ^(Chromium, 약 150MB^)
    "%PY%" -m playwright install chromium
    if errorlevel 1 (
        echo.
        echo [오류] 브라우저 엔진 설치 실패. 다시 실행하면 이어받습니다.
        pause
        exit /b 1
    )

    echo done> "%MARKER%"
    echo.
    echo  준비 완료!
    echo.
)

echo [실행] 브라우저에서 http://localhost:8765 가 열립니다.
echo 이 검은 창은 끄지 마세요 ^(끄면 프로그램도 종료됨^).
echo.
start "" http://localhost:8765
"%PY%" "%~dp0app.py"

echo.
echo 프로그램이 종료되었습니다.
pause
