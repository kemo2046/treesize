# 打包指南

## 前置条件

- **Node.js 18+** — https://nodejs.org/
- **Windows**: 直接在 Windows 上运行即可
- **macOS**: 直接在 macOS 上运行（需要 Xcode Command Line Tools）
- **Linux**: 需要 `fakeroot` 和 `dpkg`（`sudo apt install fakeroot dpkg`）

## 方法一：本地打包（推荐在 Windows 上）

### Windows 上打包 .exe

```cmd
:: 双击 build.bat 即可，或手动执行：
npm install
npm run build
npx electron-forge make --platform win32
```

产物位置：
- `out/make/squirrel.windows/x64/treesize-setup.exe` — 安装包
- `out/make/zip/win32/x64/treesize-win32-x64.zip` — 便携版

### macOS 上打包 .dmg

```bash
npm install
npm run build
npx electron-forge make --platform darwin
```

产物位置：
- `out/make/dmg/arm64/磁盘分析器.dmg`

### Linux 上打包 .deb

```bash
npm install
npm run build
npx electron-forge make --platform linux
```

产物位置：
- `out/make/deb/x64/treesize_3.0.0_amd64.deb`

## 方法二：GitHub Actions 自动打包（跨平台）

1. 将代码推送到 GitHub
2. 创建一个 tag：`git tag v3.0.0 && git push --tags`
3. GitHub Actions 会自动为 Windows/macOS/Linux 三个平台打包
4. 在 Actions 页面下载产物

配置文件已创建在 `.github/workflows/build.yml`。

## 方法三：交叉编译（Linux 上打包 Windows .exe）

> 注意：交叉编译需要安装 Wine，且不如原生打包稳定。

```bash
# Ubuntu/Debian 上安装 Wine
sudo dpkg --add-architecture i386
sudo apt update
sudo apt install wine64 wine32

# 然后正常打包
npm run build
npx electron-forge make --platform win32
```

## 产出文件说明

| 平台 | 格式 | 文件 | 说明 |
|------|------|------|------|
| Windows | Squirrel | `treesize-setup.exe` | 标准 Windows 安装包，支持自动更新 |
| Windows | ZIP | `treesize-win32-x64.zip` | 便携版，解压即用 |
| macOS | DMG | `磁盘分析器.dmg` | macOS 安装包 |
| Linux | DEB | `treesize_3.0.0_amd64.deb` | Debian/Ubuntu 安装包 |

## 添加自定义图标

1. 准备图标文件：
   - Windows: `assets/icon.ico`（256x256）
   - macOS: `assets/icon.icns`
   - Linux: `assets/icon.png`（512x512）

2. 取消 `forge.config.ts` 中 `icon` 行的注释：
   ```ts
   icon: './assets/icon',
   ```

3. 重新打包即可。

## 常见问题

**Q: Windows 上打包很慢？**
A: 首次打包需要下载 Electron 二进制文件（~100MB），后续会缓存。

**Q: 打包后体积很大？**
A: Electron 应用包含完整的 Chromium 浏览器引擎，最小约 80-120MB。可以通过以下方式减小：
   - 使用 `asar: true`（已启用）
   - 移除不必要的 devDependencies
   - 使用 `electron-builder` 替代 Forge（支持更好的压缩）

**Q: 如何添加自动更新？**
A: Squirrel.Windows 内置支持自动更新，需要配合 update server 使用。
   简单方案：使用 `electron-updater` + GitHub Releases。
