@echo off
REM hijack_patch.bat -- 仅打 asar 补丁 (幂等; 已补丁会跳过)
net session >nul 2>&1 || (echo [!] 请以管理员身份运行 & pause & exit /b 1)
cd /d "%~dp0"
py -3 "%~dp0..\src\patch_asar.py"
echo.
pause
