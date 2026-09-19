@echo off
chcp 65001 >nul
REM inject.bat -- 帧注入 CLI 转发壳 (用法: inject.bat banner "文本" --sender 王老师)
cd /d "%~dp0"
py -3 "%~dp0..\scripts\inject.py" %*
echo.
pause
