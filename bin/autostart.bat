@echo off
chcp 65001 >nul
REM autostart.bat -- 开机自启管理 (install / remove / status / run / print-command)
REM install / remove 需要管理员权限
cd /d "%~dp0"
py -3 "%~dp0..\scripts\autostart.py" %*
echo.
pause
