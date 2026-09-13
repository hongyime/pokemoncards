@echo off
setlocal
title Pokemon Card Downloader
cd /d "%~dp0" || exit /b 1
REM Let Python own the menu and subsequent prompts on the same input stream.
python getall.py
exit /b %errorlevel%
