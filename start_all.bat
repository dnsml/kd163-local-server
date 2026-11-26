@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  start_all.bat            一键起「游戏服 9000 + 资源服 80 + 网页后台 8899」
rem  start_all.bat 192.168.0.111   指定资源服对外广播的 IP（默认同左）
rem  已在监听的端口会自动跳过，可以反复双击。
rem ============================================================
set HOSTIP=%~1
if "%HOSTIP%"=="" set HOSTIP=192.168.0.111

set PY=C:/Users/wzj/AppData/Local/Programs/Python/Python38/python.exe
if not exist "%PY%" set PY=D:/WorkBuddyData/binaries/python/versions/3.13.12/python.exe
if not exist "%PY%" set PY=python

set PYTHONIOENCODING=utf-8

call :port 9000
if errorlevel 1 (
    echo [start] 9000 游戏服
    start "kd163-game" /min "%PY%" local_server3.py 9000 patch\exact.pem patch\exact.key
) else (
    echo [skip ] 9000 游戏服已在监听
)

call :port 80
if errorlevel 1 (
    echo [start] 80   资源服
    start "kd163-http" /min "%PY%" local_http.py 80 %HOSTIP%
) else (
    echo [skip ] 80   资源服已在监听
)

call :port 8899
if errorlevel 1 (
    echo [start] 8899 网页后台（对外，需登录 admin）
    set "KD163_ADMIN_HOST=0.0.0.0"
    start "kd163-admin" /min "%PY%" admin_server.py 8899
) else (
    echo [skip ] 8899 网页后台已在监听
)

echo.
echo   网页后台: http://127.0.0.1:8899 （局域网/公网用 http://本机IP:8899，需登录）
echo   游戏服  : 9000（TLS /auth + 游戏协议）
echo   资源服  : 80（广播 IP %HOSTIP%）
echo.
timeout /t 4 >nul
exit /b 0

:port
netstat -ano | findstr LISTENING | findstr ":%~1 " >nul
exit /b %errorlevel%
