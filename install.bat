@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo   Expo Data Tool - one time install
echo ============================================
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo [X] Python is not installed. Install Python 3.12 from python.org and tick "Add python.exe to PATH".
  echo     Then run install.bat again.
  pause
  exit /b 1
)

if not exist config.json (
  copy config.example.json config.json >nul
  echo [!] config.json was created. Open it in Notepad and fill the 2 values, then save.
  start notepad config.json
  echo     After saving config.json, press any key here to continue.
  pause >nul
)

echo [1/4] Making private Python folder .venv ...
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  if errorlevel 1 ( echo [X] Could not create .venv & pause & exit /b 1 )
)

echo [2/4] Installing libraries (5 to 10 minutes, needs internet) ...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 ( echo [X] Library install failed. Check internet and run install.bat again. & pause & exit /b 1 )

echo [3/4] Installing the browser reader for JavaScript sites ...
.venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 ( echo [!] Browser reader install failed. Normal sites will still work. )

echo [4/4] Setting automatic start when you sign in to Windows ...
call "%~dp0add_autostart.bat"

if not exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
  echo.
  echo [!] Tesseract OCR is not installed. It is needed only for photos, visiting cards and scanned PDFs.
  echo     Install it later from: https://github.com/UB-Mannheim/tesseract/wiki
)

echo.
echo Starting the tool ...
wscript "%~dp0start_hidden.vbs"
timeout /t 8 >nul
start http://localhost:8601
echo.
echo Done. Bookmark http://localhost:8601 in your browser.
pause
