@echo off
setlocal
cd /d "%~dp0"
title SciLit Scout v1.1.1

echo ==========================================
echo   SciLit Scout v1.1.1
echo ==========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    py -3.14 -m venv .venv
    if errorlevel 1 (
        echo.
        echo ERROR: Python virtual environment creation failed.
        echo Python was found, but venv/pip bootstrap may be incomplete.
        echo Repair or reinstall Python 3.14 with pip and the standard library.
        echo Then delete the incomplete .venv folder and run this file again.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: pip is missing from the virtual environment.
    echo Delete the .venv folder, repair Python 3.14, and run this file again.
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

echo.
echo Starting SciLit Scout v1.1.1...
echo Keep this window open while using the application.
echo Press Ctrl+C here to stop.
echo.
".venv\Scripts\python.exe" -m streamlit run app.py
echo.
echo SciLit Scout has stopped.
pause
exit /b 0

:install_error
echo.
echo ERROR: Required package installation failed.
echo Check your internet connection and try again.
pause
exit /b 1
