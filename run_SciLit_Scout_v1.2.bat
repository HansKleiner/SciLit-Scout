@echo off
setlocal
cd /d "%~dp0"
title SciLit Scout v1.2.0

echo ==========================================
echo   SciLit Scout v1.2.0
echo ==========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    py -3.14 -m venv .venv
    if errorlevel 1 (
        echo ERROR: Python virtual environment creation failed.
        echo Repair or reinstall Python 3.14 with pip, then delete .venv and retry.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: pip is missing. Delete .venv, repair Python 3.14, and retry.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip show streamlit >nul 2>&1
if errorlevel 1 (
    echo Installing required packages. First launch may take several minutes...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 goto install_error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto install_error
)

echo Starting SciLit Scout v1.2.0...
echo Keep this window open while using the application.
echo Press Ctrl+C here to stop.
echo.
".venv\Scripts\python.exe" -m streamlit run app.py
echo.
echo SciLit Scout has stopped.
pause
exit /b 0

:install_error
echo ERROR: Required package installation failed.
echo Check your internet connection and try again.
pause
exit /b 1
