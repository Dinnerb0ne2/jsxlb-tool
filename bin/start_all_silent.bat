@echo off
REM Start proxy + client silently (no windows). Double-click friendly.
REM If first-time deploy (patch/CA/hosts not done), run hijack_on.bat as admin first.
cd /d "%~dp0"
py -3 "%~dp0..\scripts\hijack_daemon.py" start
py -3 "%~dp0..\scripts\run_client.py"
echo [i] proxy + client started silently.
echo [i] status:  py -3 "%~dp0..\scripts\hijack_daemon.py" status
echo [i] reload:  py -3 "%~dp0..\scripts\hijack_daemon.py" reload
echo [i] stop:    py -3 "%~dp0..\scripts\hijack_daemon.py" stop
pause
