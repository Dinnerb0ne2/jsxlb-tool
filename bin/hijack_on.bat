@echo off
REM hijack_on.bat  --  ONE-CLICK START (run as Administrator)
net session >nul 2>&1 || (echo [!] Please run as Administrator & pause & exit /b 1)

cd /d "%~dp0"
set "MAINLOG=%~dp0..\logs\hijack_on.log"
echo hijack_on start %date% %time% >> "%MAINLOG%"

echo [1/5] locate client
set "CLIENT_DIR="
for /f "delims=" %%i in ('py -3 "%~dp0..\scripts\install_info.py" 2^>nul') do set "CLIENT_DIR=%%i"
if not defined CLIENT_DIR (
    echo [!] client not found. Set env XLB_CLIENT_DIR and retry.
    echo [on] FAIL: client not found >> "%MAINLOG%"
    pause & exit /b 1
)
echo [+] found: %CLIENT_DIR%
echo [on] client dir: %CLIENT_DIR% >> "%MAINLOG%"

echo [2/5] patch client certificate check
set "NEED_PATCH=1"
if exist "%CLIENT_DIR%\resources\app.asar.bak" call :CheckPatch "%CLIENT_DIR%"
if "%NEED_PATCH%"=="0" (
    echo [=] asar already patched
    echo [on] asar already patched >> "%MAINLOG%"
    goto CACheck
)
echo [+] patching asar...
echo [on] patching asar... >> "%MAINLOG%"
call "%~dp0hijack_patch.bat" < nul >> "%MAINLOG%" 2>&1
echo [+] patch done (details in hijack_patch.log)

:CACheck
echo [3/5] trust our CA
certutil -store Root xlb-proxy-root-ca >nul 2>&1
if errorlevel 1 (
    certutil -addstore -f Root "%~dp0..\backup\hijack_ca.pem" >nul 2>&1
    if errorlevel 1 (
        echo [!] CA add failed
        echo [on] FAIL: CA add >> "%MAINLOG%"
    ) else (
        echo [+] CA added to root store
        echo [on] CA added >> "%MAINLOG%"
    )
) else (
    echo [=] CA already trusted
    echo [on] CA already trusted >> "%MAINLOG%"
)

echo [4/5] hosts hijack
findstr /c:"xlb.810086.com" C:\Windows\System32\drivers\etc\hosts >nul 2>&1
if errorlevel 1 (
    echo 127.0.0.1 xlb.810086.com>> C:\Windows\System32\drivers\etc\hosts
    echo [+] hosts entry added
    echo [on] hosts added >> "%MAINLOG%"
) else (
    echo [=] hosts entry already present
    echo [on] hosts already present >> "%MAINLOG%"
)
ipconfig /flushdns >nul

echo [5/5] start proxy silently
py -3 "%~dp0..\scripts\hijack_daemon.py" start
py -3 "%~dp0..\scripts\hijack_daemon.py" status >> "%MAINLOG%" 2>&1

echo.
echo [OK] ALL SET. Start client:  py -3 "%~dp0run_client.py"
echo [i]  Rules: edit rules\hijack_rules.json, then: py -3 "%~dp0..\scripts\hijack_daemon.py" reload
echo [i]  Full restore: bin\hijack_off.bat
echo [on] DONE %date% %time% >> "%MAINLOG%"
pause
exit /b 0

:CheckPatch
REM same size = original restored (need patch); different size = already patched
for %%a in ("%~1\resources\app.asar") do set "SZ_NOW=%%~za"
for %%b in ("%~1\resources\app.asar.bak") do set "SZ_BAK=%%~zb"
if not "%SZ_NOW%"=="%SZ_BAK%" set "NEED_PATCH=0"
goto :eof
