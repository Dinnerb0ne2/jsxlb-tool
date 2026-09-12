@echo off
REM Stop proxy silently (does NOT touch other python processes).
cd /d "%~dp0"
py -3 "%~dp0..\scripts\hijack_daemon.py" stop
echo [i] proxy stopped. Client keeps running; run hijack_off.bat for full restore.
