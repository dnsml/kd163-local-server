@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  KD163 开服脚本（Windows）
rem    start.bat            启动 游戏服 9000 + 资源服 80 + 网页后台 8899
rem    start.bat stop       停止
rem    start.bat restart    重启
rem    start.bat status     看端口
rem ============================================================

set PY=C:\Users\wzj\AppData\Local\Programs\Python\Python38\python.exe
if not exist "%PY%" set PY=D:\WorkBuddyData\binaries\python\versions\3.13.12\python.exe
if not exist "%PY%" set PY=python

if "%KD163_HOST%"=="" set KD163_HOST=127.0.0.1
set PYTHONIOENCODING=utf-8
if not exist logs md logs
if not exist run md run
if not exist backups md backups

set CMD=%~1
if "%CMD%"=="" set CMD=start

if /i "%CMD%"=="start" goto start
if /i "%CMD%"=="stop" goto stop
if /i "%CMD%"=="restart" goto restart
if /i "%CMD%"=="status" goto status
echo 用法：start.bat {start^|stop^|restart^|status}
exit /b 1

:start
echo == KD163 开服
call :port 9000
if not errorlevel 1 (echo [skip] game :9000 已在监听) else (
    start "kd163-game" /min "%PY%" local_server3.py 9000 patch\exact.pem patch\exact.key
    echo [start] game :9000
)
call :port 80
if not errorlevel 1 (echo [skip] http :80 已在监听) else (
    start "kd163-http" /min "%PY%" local_http.py 80 %KD163_HOST%
    echo [start] http :80
)
call :port 8899
if not errorlevel 1 (echo [skip] admin :8899 已在监听) else (
    start "kd163-admin" /min "%PY%" admin_server.py 8899
    echo [start] admin :8899
)
echo.
echo 网页后台：http://127.0.0.1:8899
echo 游戏端口：9000   资源端口：80
exit /b 0

:stop
echo == 停止 KD163
powershell -NoProfile -Command "foreach($p in 8899,80,9000){$c=Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue; if($c){$c.OwningProcess ^| Sort-Object -Unique ^| ForEach-Object {Stop-Process -Id $_ -Force; Write-Output ('[stop] :'+$p+' pid='+$_)}}}"
exit /b 0

:restart
call "%~f0" stop
timeout /t 2 >nul
call "%~f0" start
exit /b 0

:status
for %%P in (9000 80 8899) do (
    call :port %%P
    if errorlevel 1 (echo :%%P  -) else (echo :%%P  LISTEN)
)
exit /b 0

:port
netstat -ano | findstr LISTENING | findstr ":%~1 " >nul
exit /b %errorlevel%
