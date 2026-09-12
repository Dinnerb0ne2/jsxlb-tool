@echo off
REM hijack_patch.bat  --  Patch client asar (run as Administrator)
REM Python-driven: extract -> patch -> repack -> copy back. Logged.
net session >nul 2>&1 || (echo [!] Please run as Administrator & pause & exit /b 1)

cd /d "%~dp0"
set "MAINLOG=%~dp0..\logs\hijack_patch.log"
echo hijack_patch start %date% %time% >> "%MAINLOG%"
py -3 "%~dp0..\src\patch_asar.py" >> "%MAINLOG%" 2>&1
if errorlevel 1 (
    echo [!] patch FAILED, see logs\hijack_patch.log
) else (
    echo [OK] patch complete
)
echo [patch] DONE %date% %time% >> "%MAINLOG%"
pause
