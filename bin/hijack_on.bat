@echo off
REM hijack_on.bat -- 一键开启 (等价于根目录 start.bat)
net session >nul 2>&1 || (echo [!] 请以管理员身份运行 & pause & exit /b 1)
cd /d "%~dp0"
py -3 "%~dp0..\scripts\ctl_start.py"
echo.
pause
