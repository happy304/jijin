@echo off
chcp 65001 >nul
setlocal EnableExtensions
title 基金量化平台 - 每日数据更新

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"

echo ============================================================
echo   基金量化平台 - 每日数据更新
echo ============================================================
echo 项目目录: %ROOT%
echo.

if not exist "%BACKEND%\app\tasks\ingest.py" (
    echo [错误] 未找到后端目录: %BACKEND%
    pause
    exit /b 1
)
cd /d "%BACKEND%"

echo [1/5] 更新基金元数据（状态、限额、是否可申购）...
python -c "from app.tasks.ingest import update_fund_meta; r = update_fund_meta(); print('完成:', r)"
if errorlevel 1 goto failed_meta
echo.

echo [2/5] 更新所有已采集基金的净值数据（含缺失数据回填）...
python -c "from app.tasks.ingest import update_daily_nav; r = update_daily_nav(); print('完成:', r)"
if errorlevel 1 goto failed_nav
echo.

echo [3/5] 更新分红数据...
python -c "from app.tasks.ingest import update_dividends; r = update_dividends(); print('完成:', r)"
if errorlevel 1 goto failed_dividends
echo.

echo [4/5] 生成策略信号...
python -c "from app.tasks.signals import generate_strategy_signals; r = generate_strategy_signals(); print('完成:', r)"
if errorlevel 1 goto failed_signals
echo.

echo [5/5] 生成交易建议...
python -c "from app.tasks.advisor import generate_daily_advice; r = generate_daily_advice(); print('完成:', r)"
if errorlevel 1 goto failed_advisor
echo.

echo ============================================================
echo   全部完成！打开 http://localhost:5173/advisor 查看建议
echo ============================================================
pause
exit /b 0

:failed_meta
echo [错误] 元数据更新失败！
goto failed
:failed_nav
echo [错误] 净值更新失败！
goto failed
:failed_dividends
echo [错误] 分红数据更新失败！
goto failed
:failed_signals
echo [错误] 策略信号生成失败！
goto failed
:failed_advisor
echo [错误] 交易建议生成失败！
goto failed
:failed
pause
exit /b 1
