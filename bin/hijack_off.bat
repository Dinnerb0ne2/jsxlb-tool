@echo off
REM hijack_off.bat  --  ONE-CLICK FULL RESTORE (run as Administrator)
REM Full log: ..\logs\hijack_off.log
net session >nul 2>&1 || (echo [!] Please run as Administrator & pause & exit /b 1)

cd /d "%~dp0"
set "MAINLOG=%~dp0..\logs\hijack_off.log"
echo hijack_off start %date% %time% >> "%MAINLOG%"

echo [1/4] stop proxy
py -3 "%~dp0..\scripts\hijack_daemon.py" stop >> "%MAINLOG%" 2>&1
echo [+] proxy stop issued
echo [off] proxy stop >> "%MAINLOG%"

echo [2/4] clean hosts
powershell -NoProfile -Command "(Get-Content C:\Windows\System32\drivers\etc\hosts) | Where-Object {$_ -notmatch 'xlb\.810086\.com'} | Set-Content C:\Windows\System32\drivers\etc\hosts -Encoding ASCII" >> "%MAINLOG%" 2>&1
ipconfig /flushdns >nul
echo [+] hosts cleaned, DNS flushed
echo [off] hosts cleaned >> "%MAINLOG%"

echo [3/4] remove our CA
certutil -delstore Root xlb-proxy-root-ca >> "%MAINLOG%" 2>&1
if errorlevel 1 (echo [=] CA not present) else (echo [+] CA removed)
echo [off] CA removed >> "%MAINLOG%"

echo [4/4] restore client asar
set "CLIENT_DIR="
for /f "delims=" %%i in ('py -3 "%~dp0..\scripts\install_info.py" 2^>nul') do set "CLIENT_DIR=%%i"
if defined CLIENT_DIR if exist "%CLIENT_DIR%\resources\app.asar.bak" (
    copy /y "%CLIENT_DIR%\resources\app.asar.bak" "%CLIENT_DIR%\resources\app.asar" >nul
    echo [+] asar restored: %CLIENT_DIR%
    echo [off] asar restored: %CLIENT_DIR% >> "%MAINLOG%"
) else (
    echo [=] no asar backup found, nothing to restore
    echo [off] no asar backup >> "%MAINLOG%"
)

echo.
echo [OK] SYSTEM FULLY RESTORED. Client connects directly to real server.
echo [i]  Log: ..\logs\hijack_off.log
echo [off] DONE %date% %time% >> "%MAINLOG%"
pause
