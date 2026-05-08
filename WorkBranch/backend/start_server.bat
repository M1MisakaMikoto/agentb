@echo off
chcp 65001 >nul
title Backend Server (Auto-restart enabled)

:loop
echo ============================================
echo  Starting Backend Server
echo  Time: %date% %time%
echo ============================================

cd /d "%~dp0WorkBranch\backend"

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found!
    pause
    exit /b 1
)

python run_server.py

echo.
echo [INFO] Server exited with code: %errorlevel%
echo [INFO] Restarting in 5 seconds...
timeout /t 5 /nobreak >nul

goto loop
