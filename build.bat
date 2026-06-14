@echo off
chcp 65001 >nul 2>&1
title 磁盘分析器 v3 - Electron 打包工具
setlocal enabledelayedexpansion

echo.
echo  ==========================================
echo    磁盘分析器 v3  Electron 打包
echo  ==========================================
echo.

cd /d "%~dp0"
echo  [信息] 工作目录: %cd%
echo.

:: ---- 检查 Node.js ----
echo  [1/5] 检查 Node.js...
where node >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo  [错误] 未找到 Node.js！
    echo  请安装 Node.js 18+: https://nodejs.org/
    goto :fail
)
for /f "tokens=*" %%i in ('node -v') do echo        Node.js: %%i
for /f "tokens=*" %%i in ('npm -v') do echo        npm: %%i
echo.

:: ---- 安装依赖 ----
echo  [2/5] 检查依赖...
if not exist "node_modules" (
    echo        正在安装依赖，请耐心等待...
    call npm install
    if %errorlevel% neq 0 (
        echo  [错误] 依赖安装失败
        goto :fail
    )
) else (
    echo        依赖已存在，跳过安装
)
echo  [OK] 依赖就绪
echo.

:: ---- 构建 TypeScript ----
echo  [3/5] 构建 TypeScript...
call npm run build
if %errorlevel% neq 0 (
    echo  [错误] 构建失败
    goto :fail
)
echo  [OK] 构建完成
echo.

:: ---- 清理旧构建 ----
echo  [4/5] 清理旧构建...
if exist "out" rmdir /s /q "out"
echo  [OK] 清理完成
echo.

:: ---- 打包 ----
echo  [5/5] 正在打包 Windows 版本...
echo         这个过程通常需要 2~5 分钟（首次较慢）。
echo.

call npx electron-forge make --platform win32
if %errorlevel% neq 0 (
    echo  [错误] 打包失败
    goto :fail
)

:: ---- 完成 ----
echo.
echo  ==========================================
echo    打包完成！
echo  ==========================================
echo.

if exist "out\make" (
    echo  输出文件:
    dir /s /b out\make\*.exe 2>nul
    dir /s /b out\make\*.msi 2>nul
    dir /s /b out\make\*.zip 2>nul
    dir /s /b out\make\*.deb 2>nul
    echo.
    echo  正在打开输出目录...
    start explorer out\make
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
