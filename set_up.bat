@echo off
title Pokemon Card Downloader Setup
cd /d "%~dp0"

echo ========================================================
echo         Installing Required Libraries
echo ========================================================
echo.

REM Use pip from the same interpreter that launches the downloader.
call python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: pip is not recognized. Please ensure Python is installed and added to PATH.
    pause
    exit /b 1
)

echo Installing dependencies...
call python -m pip install -r "%~dp0requirements.txt"

if %errorlevel% neq 0 (
    echo.
    echo Error: Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo ========================================================
echo Setup completed successfully!
echo You can now run 'run_scraper.bat' to start the downloader.
echo ========================================================
pause
