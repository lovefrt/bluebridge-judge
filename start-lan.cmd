@echo off
setlocal
cd /d "%~dp0"

set "HOST=0.0.0.0"
set "PORT=8765"
set "LANIP="

for /f "delims=" %%i in ('powershell -NoProfile -Command "$cfg=Get-NetIPConfiguration; foreach($c in $cfg){if($c.IPv4DefaultGateway){$c.IPv4Address.IPAddress; break}}"') do set "LANIP=%%i"
if not defined LANIP set "LANIP=127.0.0.1"
set "OPEN_HOST=%LANIP%"

echo ============================================================
echo   Love-Coding LAN mode
echo ------------------------------------------------------------
echo   Student: http://%LANIP%:%PORT%/student
echo   Teacher: http://%LANIP%:%PORT%/teacher
echo   Others must be on the same Wi-Fi / LAN.
echo   If Windows Firewall asks, allow Python on Private networks.
echo ============================================================
echo.

call "%~dp0start.cmd"
endlocal
