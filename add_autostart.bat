@echo off
cd /d "%~dp0"
echo Setting automatic start for Expo Data Tool ...
echo.
echo [1/2] Unblocking the tool files (so Windows does not show a security question at start-up) ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -File | Unblock-File"
echo [2/2] Creating the start-up shortcut ...
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ExpoDataTool.lnk"
if exist "%LNK%" del "%LNK%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:APPDATA+'\Microsoft\Windows\Start Menu\Programs\Startup\ExpoDataTool.lnk'); $s.TargetPath=$env:WINDIR+'\System32\wscript.exe'; $q=[char]34; $s.Arguments='//B //Nologo '+$q+(Join-Path '%~dp0' 'start_hidden.vbs')+$q; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
echo.
if exist "%LNK%" (
  echo [OK] Done. The tool will now start by itself when you sign in to Windows.
  echo      Wait about 30 seconds after sign-in, then open http://localhost:8601
  echo      Shortcut: %LNK%
) else (
  echo [X] Could not create the shortcut. Your office may block it.
  echo     Show this message to the person who is helping you.
)
echo.
pause
