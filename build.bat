@echo off
chcp 65001 >nul
title 磁盘分析器 v2 - 一键打包
setlocal enabledelayedexpansion

echo ============================================================
echo   磁盘空间分析工具 v2 — Windows 一键打包脚本
echo ============================================================
echo.

:: ---- 检查 Python ----
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.9+
    echo 下载地址: https://www.python.org/downloads/
    echo 安装时务必勾选 "Add Python to PATH"
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [✓] Python 版本: %PYVER%

:: ---- 创建虚拟环境 ----
if not exist "venv" (
    echo [1/4] 创建虚拟环境...
    python -m venv venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
    echo [✓] 虚拟环境创建完成
) else (
    echo [1/4] 虚拟环境已存在，跳过创建
)

:: ---- 激活虚拟环境 ----
call venv\Scripts\activate.bat

:: ---- 安装依赖 ----
echo [2/4] 安装依赖...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo [错误] 安装依赖失败
    pause
    exit /b 1
)
echo [✓] 依赖安装完成

:: ---- 清理旧构建 ----
if exist "dist" rmdir /s /q dist
if exist "build" rmdir /s /q build

:: ---- PyInstaller 打包 ----
echo [3/4] 正在打包，请稍候（约 1-3 分钟）...
pyinstaller ^
    --noconfirm ^
    --onefile ^
    --windowed ^
    --name "磁盘分析器" ^
    --add-data "README.md;." ^
    --hidden-import psutil ^
    --hidden-import send2trash ^
    --hidden-import xxhash ^
    --hidden-import requests ^
    --hidden-import requests.adapters ^
    --hidden-import urllib3 ^
    --collect-submodules requests ^
    disk_analyzer_v2.py

if errorlevel 1 (
    echo.
    echo [错误] 打包失败，请检查上方错误信息
    pause
    exit /b 1
)

:: ---- 完成 ----
echo.
echo [4/4] 打包完成！
echo ============================================================
echo.
echo   输出文件: dist\磁盘分析器.exe
echo.

:: 显示文件大小
for %%f in (dist\磁盘分析器.exe) do (
    set /a sizeMB=%%~zf / 1048576
    echo   文件大小: !sizeMB! MB
)
echo.
echo ============================================================
echo.

:: 询问是否打开输出目录
set /p OPEN="是否打开输出目录？(Y/N): "
if /i "%OPEN%"=="Y" (
    explorer dist
)

pause
