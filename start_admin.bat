@echo off
setlocal
cd /d "%~dp0"

rem  start_admin.bat        open http://127.0.0.1:8899  (this PC only)
rem  start_admin.bat lan    also open to the LAN (phones / other PCs)
if /i "%~1"=="lan" set KD163_ADMIN_HOST=0.0.0.0

set PY=C:\Users\wzj\AppData\Local\Programs\Python\Python38\python.exe
if not exist "%PY%" set PY=D:\WorkBuddyData\binaries\python\versions\3.13.12\python.exe
if not exist "%PY%" set PY=python

echo ==========================================
echo  KD163 Admin Panel  (port 8899)
echo  URL: http://127.0.0.1:8899
echo  Keep this window open. Ctrl+C to stop.
echo ==========================================
echo.

"%PY%" admin_server.py 8899
echo.
echo [admin exited] code=%ERRORLEVEL%
pause
