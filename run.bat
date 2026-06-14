@echo off
chcp 65001 >nul 2>&1
set PYTHONUTF8=1
cd /d "%~dp0"
python disk_analyzer_v2.py
if errorlevel 1 (
    echo.
    echo  [错误] 运行失败，请检查 Python 是否已安装并添加到 PATH
    echo  下载 Python: https://www.python.org/downloads/
    echo.
    pause
)
