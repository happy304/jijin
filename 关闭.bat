@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
if not defined PG_BIN set "PG_BIN=C:\Program Files\PostgreSQL\16\bin"
if not defined PG_DATA set "PG_DATA=D:\pgdata"
if not defined REDIS_SERVICE set "REDIS_SERVICE=Redis"
if not defined FRONTEND_PORT set "FRONTEND_PORT=5173"

echo ========================================
echo   Fund Quant Platform - Stop
echo ========================================
echo Project: %ROOT%
echo PG_BIN:  %PG_BIN%
echo PG_DATA: %PG_DATA%
echo Redis:   %REDIS_SERVICE%
echo.

echo [1/5] Stop backend / celery / frontend processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$patterns = @('uvicorn app.main:app', 'uvicorn.exe app.main:app', 'celery -A app.tasks.celery_app worker', 'celery -A app.tasks.celery_app beat', 'npm run dev'); Get-CimInstance Win32_Process | Where-Object { $cmd = $_.CommandLine; $cmd -and (($patterns | Where-Object { $cmd -like ('*' + $_ + '*') }).Count -gt 0) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
echo Done.

echo [2/5] Close named windows...
taskkill /F /FI "WINDOWTITLE eq Celery-Worker*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Celery-Ingest-Worker*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Celery-Compute-Worker*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Celery-Beat*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Frontend-%FRONTEND_PORT%*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Frontend-5173*" >nul 2>&1
echo Done.

echo [3/5] Stop Redis...
net stop "%REDIS_SERVICE%" >nul 2>&1
if errorlevel 1 (
    echo WARN: Redis is not running or service name is not "%REDIS_SERVICE%".
) else (
    echo Redis stopped.
)

echo [4/5] Stop PostgreSQL...
if exist "%PG_BIN%\pg_ctl.exe" if exist "%PG_DATA%" (
    "%PG_BIN%\pg_ctl.exe" stop -D "%PG_DATA%" -m fast >nul 2>&1
    if errorlevel 1 (
        echo WARN: PostgreSQL may not be running or PG_DATA is different.
    ) else (
        echo PostgreSQL stopped.
    )
) else (
    echo WARN: pg_ctl or PG_DATA not found. Skip PostgreSQL stop.
    echo       You can set PG_BIN and PG_DATA before running this script.
)

echo [5/5] Done.
echo.
echo ========================================
echo   Stop completed.
echo ========================================
pause
