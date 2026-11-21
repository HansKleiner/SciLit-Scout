@echo off
setlocal
cd /d "%~dp0"
title SciLit Scout v1.1

echo ==========================================
echo   SciLit Scout v1.1
echo ==========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    py -3.14 -m venv .venv
    if errorlevel 1 (
        echo ERROR: Python 3.14 was not found or venv creation failed.
        echo Install Python 3.14, then run this file again.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -m pip show streamlit >nul 2>&1
if errorlevel 1 (
    echo Installing required packages. First launch may take several minutes...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Package installation failed. Check internet connection.
        pause
        exit /b 1
    )
)

echo.
echo Starting SciLit Scout v1.1...
echo Keep this window open while using the application.
echo Press Ctrl+C here to stop.
echo.
".venv\Scripts\python.exe" -m streamlit run app.py
echo.
echo SciLit Scout has stopped.
pause
