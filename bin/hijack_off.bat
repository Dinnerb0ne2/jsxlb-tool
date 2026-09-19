@echo off
REM hijack_off.bat -- 一键恢复 (等价于根目录 end.bat)
net session >nul 2>&1 || (echo [!] 请以管理员身份运行 & pause & exit /b 1)
cd /d "%~dp0"
py -3 "%~dp0..\scripts\ctl_end.py"
echo.
pause
