@echo off
setlocal
title Love-Coding Judge
cd /d "%~dp0"

set "CONTEST=contest.json"
set "DATA_DIR=webdata_demo"
set "PORT=8765"
if not defined HOST set "HOST=127.0.0.1"
if not defined OPEN_HOST set "OPEN_HOST=127.0.0.1"

if not exist "bbjudge\__main__.py" (
  echo [ERROR] bbjudge package not found in: %CD%
  echo Please put this script in the bluebridge-judge project folder.
  pause
  exit /b 1
)

set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
  where py >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ first.
    pause
    exit /b 1
  )
  set "PY=py"
)

rem If the service is already running, just open it instead of starting a duplicate.
powershell -NoProfile -WindowStyle Hidden -Command "try{$r=Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:%PORT%/health' -TimeoutSec 1; if($r.StatusCode -eq 200 -and $r.Content -like '*Love-Coding*'){exit 0}}catch{}; exit 1"
if not errorlevel 1 (
  echo Server is already running on port %PORT%.
  echo Student: http://%OPEN_HOST%:%PORT%/student
  echo Teacher: http://%OPEN_HOST%:%PORT%/teacher
  if not "%NO_BROWSER%"=="1" start "" "http://%OPEN_HOST%:%PORT%/student"
  ping -n 3 127.0.0.1 >nul
  exit /b 0
)

echo ============================================================
echo   Love-Coding Judge
echo ------------------------------------------------------------
echo   Student: http://%OPEN_HOST%:%PORT%/student
echo   Teacher: http://%OPEN_HOST%:%PORT%/teacher
echo   Data:    %DATA_DIR%
echo   Listen:  %HOST%:%PORT%
echo   Stop:    press Ctrl+C in this window
echo ============================================================
echo.

rem Wait until the server is healthy, then open the student page.
if not "%NO_BROWSER%"=="1" (
  start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 40;$i++){try{Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:%PORT%/health' -TimeoutSec 1 | Out-Null; Start-Process 'http://%OPEN_HOST%:%PORT%/student'; exit}catch{Start-Sleep -Milliseconds 500}}"
)

"%PY%" -m bbjudge web --contest "%CONTEST%" --data-dir "%DATA_DIR%" --host "%HOST%" --port %PORT%

echo.
echo Server stopped.
pause
endlocal
