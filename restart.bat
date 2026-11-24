@echo off
cd /d "%~dp0"
echo Stopping the tool (if running) ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -LocalPort 8601 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
timeout /t 3 >nul
echo Starting ...
wscript "%~dp0start_hidden.vbs"
timeout /t 8 >nul
start http://localhost:8601
