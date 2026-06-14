@echo off
title 磁盘分析器 v2 - 打包工具
setlocal enabledelayedexpansion

echo ========================================
echo   磁盘分析器 v2  一键打包
echo ========================================
echo.

:: 切换到脚本所在目录（防止从其他位置双击运行时路径错误）
cd /d "%~dp0"
echo [信息] 工作目录: %cd%
echo.

:: ---- 第1步：检查 Python ----
echo [1/5] 检查 Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [错误] 未找到 Python！
    echo.
    echo 请先安装 Python 3.9 或更高版本：
    echo   https://www.python.org/downloads/
    echo.
    echo 安装时一定要勾选 "Add Python to PATH"
    echo.
    goto :fail
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER%
echo.

:: ---- 第2步：创建虚拟环境 ----
echo [2/5] 准备虚拟环境...
if not exist "venv\Scripts\activate.bat" (
    echo       正在创建，请稍候...
    python -m venv venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        goto :fail
    )
    echo [OK] 虚拟环境创建完成
) else (
    echo [OK] 虚拟环境已存在
)
echo.

:: 激活虚拟环境
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [错误] 激活虚拟环境失败
    goto :fail
)

:: ---- 第3步：安装依赖 ----
echo [3/5] 安装依赖包...
python -m pip install --upgrade pip -q 2>nul
pip install psutil requests send2trash xxhash pyinstaller -q
if errorlevel 1 (
    echo.
    echo [错误] 安装依赖失败，请检查网络连接
    goto :fail
)
echo [OK] 依赖安装完成
echo.

:: ---- 第4步：清理旧文件 ----
echo [4/5] 清理旧构建...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "*.spec" del /q "*.spec"
echo [OK] 清理完成
echo.

:: ---- 第5步：PyInstaller 打包 ----
echo [5/5] 正在打包，请耐心等待（约 1~3 分钟）...
echo.

pyinstaller --noconfirm --onefile --windowed --name "DiskAnalyzer" --hidden-import psutil --hidden-import send2trash --hidden-import xxhash --hidden-import requests --hidden-import requests.adapters --hidden-import urllib3 --collect-submodules requests disk_analyzer_v2.py

if errorlevel 1 (
    echo.
    echo [错误] 打包失败，请查看上方错误信息
    goto :fail
)

:: ---- 完成 ----
echo.
echo ========================================
echo   打包完成！
echo ========================================
echo.
echo   输出: dist\DiskAnalyzer.exe
echo.

if exist "dist\DiskAnalyzer.exe" (
    for %%f in (dist\DiskAnalyzer.exe) do (
        set /a sizeMB=%%~zf / 1048576
        echo   大小: !sizeMB! MB
    )
    echo.
    start explorer dist
) else (
    echo [警告] 未找到输出文件，请检查 dist 目录
)

echo.
pause
exit /b 0

:fail
echo.
echo 打包失败，请阅读上方错误信息。
echo 如有问题，将此窗口截图发给开发者。
echo.
pause
exit /b 1
