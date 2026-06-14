@echo off
chcp 65001 >nul 2>&1
title 磁盘分析器 v2 - 打包工具
setlocal enabledelayedexpansion

echo.
echo  ==========================================
echo    磁盘分析器 v2  一键打包
echo  ==========================================
echo.

cd /d "%~dp0"
echo  [信息] 工作目录: %cd%
echo.

:: ---- 检查 Python ----
echo  [1/5] 检查 Python...
python --version
if errorlevel 1 (
    echo.
    echo  [错误] 未找到 Python！
    echo  请安装 Python 3.9+: https://www.python.org/downloads/
    echo  安装时勾选 "Add Python to PATH"
    goto :fail
)
echo.

:: ---- 跳过 venv，直接用当前 Python 安装 ----
echo  [2/5] 安装依赖包（使用当前 Python 环境）...
echo         正在安装 psutil, requests, send2trash, xxhash, pyinstaller...
echo         请耐心等待，可能需要 1~3 分钟...
echo.

python -m pip install --upgrade pip 2>nul
python -m pip install psutil requests send2trash xxhash pyinstaller
if errorlevel 1 (
    echo.
    echo  [错误] 安装依赖失败
    goto :fail
)
echo.
echo  [OK] 依赖安装完成
echo.

:: ---- 清理旧构建 ----
echo  [3/5] 清理旧构建...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "*.spec" del /q "*.spec"
echo  [OK] 清理完成
echo.

:: ---- PyInstaller 打包 ----
echo  [4/5] 正在打包为 exe，请耐心等待...
echo         这个过程通常需要 1~3 分钟。
echo.

pyinstaller --noconfirm --onefile --windowed --name "DiskAnalyzer" --hidden-import psutil --hidden-import send2trash --hidden-import xxhash --hidden-import requests --hidden-import requests.adapters --hidden-import urllib3 --collect-submodules requests disk_analyzer_v2.py

if errorlevel 1 (
    echo.
    echo  [错误] 打包失败
    goto :fail
)

:: ---- 完成 ----
echo.
echo  [5/5] 打包完成！
echo.
echo  ==========================================
echo    输出文件: dist\DiskAnalyzer.exe
echo  ==========================================
echo.

if exist "dist\DiskAnalyzer.exe" (
    for %%f in (dist\DiskAnalyzer.exe) do (
        set /a sizeKB=%%~zf / 1024
        echo    大小: !sizeKB! KB
    )
    echo.
    echo  正在打开输出目录...
    start explorer dist
)

echo.
echo  按任意键退出...
pause >nul
exit /b 0

:fail
echo.
echo  打包失败，请阅读上方错误信息。
echo  按任意键退出...
pause >nul
exit /b 1
