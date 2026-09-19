@echo off
chcp 65001 >nul
REM netcheck.bat -- 网络六步诊断 (hosts / DNS / DoH / 证书 / 出站 / 代理)
cd /d "%~dp0"
py -3 "%~dp0..\scripts\netcheck.py"
echo.
pause
