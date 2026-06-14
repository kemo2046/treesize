import type { ForgeConfig } from '@electron-forge/shared-types';
import { MakerSquirrel } from '@electron-forge/maker-squirrel';
import { MakerZIP } from '@electron-forge/maker-zip';
import { MakerDeb } from '@electron-forge/maker-deb';
import { MakerDmg } from '@electron-forge/maker-dmg';

const config: ForgeConfig = {
  packagerConfig: {
    name: '磁盘分析器',
    executableName: 'treesize',
    // icon: './assets/icon',  // 取消注释并提供 .ico/.icns/.png 图标文件
    asar: true,  // 将源码打包为 asar 归档，加快启动
    extraResource: [],  // 如有额外资源文件可放这里
  },
  makers: [
    // Windows: Squirrel 安装包 (.exe)
    new MakerSquirrel({
      setupExe: 'treesize-setup.exe',
      setupMsi: 'treesize-setup.msi',
      // loadingGif: './assets/loading.gif',  // 安装时的加载动画
    }),
    // Windows/macOS: ZIP 便携版
    new MakerZIP({}, ['win32', 'darwin']),
    // macOS: DMG 安装包
    new MakerDmg({
      name: '磁盘分析器',
      format: 'ULFO',
    }),
    // Linux: DEB 安装包
    new MakerDeb({
      options: {
        maintainer: 'kemo2046',
        homepage: 'https://github.com/kemo2046/treesize',
      },
    }),
  ],
};

export default config;
