@echo off
REM status.bat  --  quick health check of the hijack chain
cd /d "%~dp0"

echo === 1. proxy process ===
wmic process where "name='py.exe'" get processid,commandline /format:csv 2>nul | findstr /I "hijack_proxy.py" || echo [!] proxy NOT running

echo.
echo === 2. listening ports ===
netstat -ano | findstr :443 | findstr LISTEN
netstat -ano | findstr :8100 | findstr LISTEN

echo.
echo === 3. hosts entry ===
findstr /c:"xlb.810086.com" C:\Windows\System32\drivers\etc\hosts && echo [OK] hosts hijacked || echo [!] hosts NOT hijacked

echo.
echo === 4. client connections ===
netstat -ano | findstr :443 | findstr ESTABLISHED | findstr 127.0.0.1 && echo [OK] client is connected through proxy || echo [=] no active client connection right now

echo.
echo === 5. last rewrite events ===
powershell -NoProfile -Command "Get-Content '%~dp0..\logs\hijack_proxy.log' -Tail 200 | Select-String 'BANNER|DROPPED|TIMER|SEAT|STUDENTS|BLOCK|INJECT' | Select-Object -Last 8"

echo.
echo === 6. live status (rules API) ===
curl -s --max-time 3 http://127.0.0.1:8100/__status && echo. || echo [!] rules API unreachable
pause
