@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"

echo ========================================
echo   基金量化平台 - 数据采集
echo ========================================
echo 项目目录: %ROOT%
echo.
echo   请确保已经运行了「启动.bat」，或至少数据库可访问。
echo.
echo ----------------------------------------
echo   输入基金代码（6位数字），例如：
echo     110020  易方达沪深300ETF联接A
echo     000001  华夏成长
echo     519300  大成沪深300
echo     161725  招商中证白酒
echo     005827  易方达蓝筹精选
echo ----------------------------------------
echo.

if not exist "%BACKEND%\app\cli.py" (
    echo [错误] 未找到后端目录: %BACKEND%
    pause
    exit /b 1
)

set /p "code=请输入基金代码: "
if "%code%"=="" (
    echo [错误] 基金代码不能为空。
    pause
    exit /b 1
)

echo %code%| findstr /R "^[0-9][0-9][0-9][0-9][0-9][0-9]$" >nul
if errorlevel 1 (
    echo [错误] 基金代码必须是 6 位数字。
    pause
    exit /b 1
)

echo.
echo 正在采集基金 %code% 的数据，请稍候...
echo   当前使用统一采集链：共享任务逻辑 + 多源 Provider 降级
echo.

cd /d "%BACKEND%"
echo [1/2] 采集基本信息（统一任务链）...
python -m app.cli ingest meta -f "%code%" -s
if errorlevel 1 (
    echo [错误] 基本信息采集失败。
    pause
    exit /b 1
)

echo [2/2] 采集全量净值（统一任务链）...
python -m app.cli ingest nav -f "%code%" -s
if errorlevel 1 (
    echo [错误] 净值采集失败。
    pause
    exit /b 1
)

echo.
echo ========================================
echo   采集完成！可以在前端查看该基金数据了
echo ========================================
echo.
pause
