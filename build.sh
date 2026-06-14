#!/bin/bash
# 磁盘空间分析工具 — 跨平台打包脚本
# 用法: ./build.sh [win|mac|linux|all]

set -e

echo "=== 磁盘空间分析工具 打包脚本 ==="
echo ""

# 检查 Node.js
if ! command -v node &> /dev/null; then
    echo "错误: 未安装 Node.js，请先安装 Node.js 18+"
    exit 1
fi

echo "Node.js 版本: $(node -v)"
echo "npm 版本: $(npm -v)"
echo ""

# 安装依赖
if [ ! -d "node_modules" ]; then
    echo ">>> 安装依赖..."
    npm install
fi

# 构建
echo ">>> 构建项目..."
npm run build
echo "构建完成"
echo ""

# 打包
PLATFORM=${1:-all}

case $PLATFORM in
    win|windows)
        echo ">>> 打包 Windows 版本..."
        echo "注意: 在 Linux/macOS 上交叉编译 Windows 版本需要 wine"
        echo "      建议在 Windows 上直接运行此脚本，或使用 GitHub Actions"
        echo ""
        npx electron-forge make --platform win32
        ;;
    mac|macos|darwin)
        echo ">>> 打包 macOS 版本..."
        npx electron-forge make --platform darwin
        ;;
    linux)
        echo ">>> 打包 Linux 版本..."
        npx electron-forge make --platform linux
        ;;
    all)
        echo ">>> 打包当前平台版本..."
        npx electron-forge make
        ;;
    *)
        echo "用法: ./build.sh [win|mac|linux|all]"
        exit 1
        ;;
esac

echo ""
echo "=== 打包完成 ==="
echo "输出目录: out/"
ls -la out/make/ 2>/dev/null || echo "(查看 out/ 目录获取打包产物)"
