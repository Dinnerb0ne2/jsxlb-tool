@echo off
chcp 65001 >nul
net session >nul 2>&1
if errorlevel 1 (
    echo [i] Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process cmd -Verb RunAs -ArgumentList '/c \"%~f0\"'"
    exit /b
)
cd /d "%~dp0"
py -3 "%~dp0scripts\ctl_start.py"
echo.
echo [i] Press any key to close...
pause >nul
