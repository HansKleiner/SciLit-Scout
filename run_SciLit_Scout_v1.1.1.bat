@echo off
setlocal
cd /d "%~dp0"
title SciLit Scout

echo ==========================================
echo   SciLit Scout
echo ==========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    py -3.14 -m venv .venv
    if errorlevel 1 (
        echo.
        echo ERROR: Could not create the Python 3.14 environment.
        echo Install Python 3.14 with the Python Launcher and try again.
        pause
        exit /b 1
    )
)

echo Checking SciLit Scout environment...
".venv\Scripts\python.exe" scripts\ensure_env.py
if errorlevel 1 (
    echo.
    echo ERROR: Dependency setup failed.
    echo Check your internet connection and try again.
    pause
    exit /b 1
)

echo.
echo Starting SciLit Scout...
echo Keep this window open while using the application.
echo Press Ctrl+C here to stop.
echo.
".venv\Scripts\python.exe" -m streamlit run app.py
echo.
echo SciLit Scout has stopped.
pause
exit /b 0
