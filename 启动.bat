@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"
if not defined PG_BIN set "PG_BIN=C:\Program Files\PostgreSQL\16\bin"
if not defined PG_DATA set "PG_DATA=D:\pgdata"
if not defined REDIS_SERVICE set "REDIS_SERVICE=Redis"
if not defined BACKEND_PORT set "BACKEND_PORT=8000"
if not defined FRONTEND_PORT set "FRONTEND_PORT=5173"

echo ========================================
echo   Fund Quant Platform - Start
echo ========================================
echo Project:  %ROOT%
echo Backend:  %BACKEND%
echo Frontend: %FRONTEND%
echo PG_BIN:   %PG_BIN%
echo PG_DATA:  %PG_DATA%
echo Redis:    %REDIS_SERVICE%
echo.

if not exist "%BACKEND%\app\main.py" (
    echo ERROR: backend not found: %BACKEND%
    pause
    exit /b 1
)

if not exist "%FRONTEND%\package.json" (
    echo ERROR: frontend not found: %FRONTEND%
    pause
    exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: python not found in PATH.
    echo Please install Python 3.11+ or add it to PATH.
    pause
    exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
    echo ERROR: npm not found in PATH.
    echo Please install Node.js and npm first.
    pause
    exit /b 1
)

if not exist "%FRONTEND%\node_modules" (
    echo WARN: frontend node_modules not found. Run "npm install" in frontend first if startup fails.
)

echo [0/6] Stop old project processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$patterns = @('uvicorn app.main:app', 'uvicorn.exe app.main:app', 'celery -A app.tasks.celery_app worker', 'celery -A app.tasks.celery_app beat', 'npm run dev'); Get-CimInstance Win32_Process | Where-Object { $cmd = $_.CommandLine; $cmd -and (($patterns | Where-Object { $cmd -like ('*' + $_ + '*') }).Count -gt 0) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
echo Done.
echo.

echo [1/6] Check PostgreSQL...
if exist "%PG_BIN%\pg_isready.exe" (
    "%PG_BIN%\pg_isready.exe" >nul 2>&1
    if errorlevel 1 (
        if exist "%PG_BIN%\pg_ctl.exe" if exist "%PG_DATA%" (
            "%PG_BIN%\pg_ctl.exe" start -D "%PG_DATA%" -l "%PG_DATA%\postgresql.log" -w
            if errorlevel 1 (
                echo WARN: PostgreSQL start failed. Check PG_BIN or PG_DATA.
            ) else (
                echo PostgreSQL started.
            )
        ) else (
            echo WARN: pg_ctl or PG_DATA not found. Skip PostgreSQL auto start.
            echo       You can set PG_BIN and PG_DATA before running this script.
        )
    ) else (
        echo PostgreSQL is running.
    )
) else (
    echo WARN: pg_isready not found. Skip PostgreSQL check.
    echo       Current PG_BIN: %PG_BIN%
)
timeout /t 2 >nul

echo [2/6] Start Redis...
net start "%REDIS_SERVICE%" >nul 2>&1
if errorlevel 1 (
    echo WARN: Redis may already be running or service name is not "%REDIS_SERVICE%".
    echo       If your Redis service has another name, set REDIS_SERVICE first.
) else (
    echo Redis started.
)

echo [3/6] Start Celery workers...
start "Celery-Ingest-Worker" /min /D "%BACKEND%" cmd /k "python -m celery -A app.tasks.celery_app worker --loglevel=info --pool=solo -Q ingest -n ingest@%%h --prefetch-multiplier=1"
start "Celery-Compute-Worker" /min /D "%BACKEND%" cmd /k "python -m celery -A app.tasks.celery_app worker --loglevel=info --pool=solo -Q backtest,ai,notify -n compute@%%h --prefetch-multiplier=1"
timeout /t 2 >nul
echo Celery workers started.

echo [4/6] Start Celery Beat...
start "Celery-Beat" /min /D "%BACKEND%" cmd /k "python -m celery -A app.tasks.celery_app beat --loglevel=info"
timeout /t 2 >nul
echo Celery Beat started.

echo [5/6] Start frontend...
start "Frontend-%FRONTEND_PORT%" /min /D "%FRONTEND%" cmd /k "npm run dev -- --host 0.0.0.0 --port %FRONTEND_PORT%"
timeout /t 3 >nul

echo [6/6] Start backend API...
echo.
echo ========================================
echo   Frontend: http://localhost:%FRONTEND_PORT%
echo   Backend:  http://localhost:%BACKEND_PORT%
echo   API Docs: http://localhost:%BACKEND_PORT%/docs
echo ========================================
echo.
echo Windows opened:
echo   Celery-Ingest-Worker
echo   Celery-Compute-Worker
echo   Celery-Beat
echo   Frontend-%FRONTEND_PORT%
echo.
echo Tip: You can override paths before startup, for example:
echo   set PG_BIN=C:\Program Files\PostgreSQL\16\bin
echo   set PG_DATA=D:\pgdata
echo.

cd /d "%BACKEND%"
python -m uvicorn app.main:app --host 0.0.0.0 --port %BACKEND_PORT% --reload
pause
