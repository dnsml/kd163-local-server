@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=C:\Users\wzj\AppData\Local\Programs\Python\Python38\python.exe
if not exist "%PY%" set PY=python
echo ============================================
echo  KD163 Admin Panel
echo  Open in browser:  http://127.0.0.1:8899
echo  LAN access:  set KD163_ADMIN_HOST=0.0.0.0
echo  Keep this window open. Ctrl+C to stop.
echo ============================================
echo.
"%PY%" admin_server.py 8899
echo.
echo [admin stopped] press any key to close
pause >nul
