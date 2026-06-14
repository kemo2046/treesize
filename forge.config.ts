import type { ForgeConfig } from '@electron-forge/shared-types';
import { MakerZIP } from '@electron-forge/maker-zip';

const makers: ForgeConfig['makers'] = [
  // ZIP 便携版（全平台）
  new MakerZIP({}, ['win32', 'darwin', 'linux']),
];

// Windows: Squirrel 安装包
try {
  const { MakerSquirrel } = require('@electron-forge/maker-squirrel');
  makers.push(new MakerSquirrel({
    setupExe: 'treesize-setup.exe',
    setupMsi: 'treesize-setup.msi',
  }));
} catch {}

// macOS: DMG 安装包
try {
  const { MakerDmg } = require('@electron-forge/maker-dmg');
  makers.push(new MakerDmg({ name: '磁盘分析器', format: 'ULFO' }));
} catch {}

// Linux: DEB 安装包
try {
  const { MakerDeb } = require('@electron-forge/maker-deb');
  makers.push(new MakerDeb({
    options: {
      maintainer: 'kemo2046',
      homepage: 'https://github.com/kemo2046/treesize',
    },
  }));
} catch {}

const config: ForgeConfig = {
  packagerConfig: {
    name: '磁盘分析器',
    executableName: 'treesize',
    asar: true,
  },
  makers,
};

export default config;
