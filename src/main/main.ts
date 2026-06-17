import { app, BrowserWindow, ipcMain, shell, clipboard, nativeTheme, dialog } from 'electron';
import * as fs from 'fs';
import * as path from 'path';
import { Scanner, ScanResult, ScanProgress } from './scanner';
import { ConfigManager, AppConfig } from './config';
import { IPC } from '../shared/types';

let mainWindow: BrowserWindow | null = null;
let currentScanner: Scanner | null = null;
const configManager = new ConfigManager();

function createWindow(): void {
  const geometry = configManager.getGeometry();
  const config = configManager.get();

  mainWindow = new BrowserWindow({
    width: geometry?.width || 1400,
    height: geometry?.height || 900,
    x: geometry?.x,
    y: geometry?.y,
    minWidth: 1000,
    minHeight: 600,
    title: '磁盘空间分析器',
    backgroundColor: config.theme === 'dark' ? '#0F172A' : '#F5F7FA',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, '..', '..', 'renderer', 'index.html'));

  mainWindow.on('resize', saveGeometry);
  mainWindow.on('move', saveGeometry);
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function saveGeometry(): void {
  if (!mainWindow) return;
  const bounds = mainWindow.getBounds();
  configManager.setGeometry({
    width: bounds.width,
    height: bounds.height,
    x: bounds.x,
    y: bounds.y,
  });
}

// ---- IPC Handlers ----

// Scan
ipcMain.on(IPC.SCAN_START, async (_event, scanPath: string) => {
  if (!scanPath || typeof scanPath !== 'string') {
    mainWindow?.webContents.send(IPC.SCAN_ERROR, '请输入有效的扫描路径');
    return;
  }

  try {
    const stat = await fs.promises.stat(scanPath);
    if (!stat.isDirectory()) {
      mainWindow?.webContents.send(IPC.SCAN_ERROR, '路径不是一个目录');
      return;
    }
  } catch {
    mainWindow?.webContents.send(IPC.SCAN_ERROR, `路径不存在或无法访问: ${scanPath}`);
    return;
  }

  // Save last scan path
  configManager.set({ lastScanPath: scanPath });

  // Abort previous scan
  currentScanner?.abort();

  const scanner = new Scanner(scanPath, (progress: ScanProgress) => {
    mainWindow?.webContents.send(IPC.SCAN_PROGRESS, progress);
  });

  currentScanner = scanner;

  scanner.scan().then((result: ScanResult) => {
    if (currentScanner === scanner) {
      mainWindow?.webContents.send(IPC.SCAN_RESULT, result);
      currentScanner = null;
    }
  }).catch((err: Error) => {
    if (currentScanner === scanner) {
      mainWindow?.webContents.send(IPC.SCAN_ERROR, err.message);
      currentScanner = null;
    }
  });
});

ipcMain.on(IPC.SCAN_STOP, () => {
  currentScanner?.abort();
  currentScanner = null;
});

// Disk info
ipcMain.handle(IPC.DISK_INFO, async () => {
  try {
    const mounts = await fs.promises.readFile('/proc/mounts', 'utf-8');
    const partitions: any[] = [];
    for (const line of mounts.split('\n')) {
      const parts = line.split(' ');
      if (parts.length < 3) continue;
      const device = parts[0];
      const mountPoint = parts[1];
      const fstype = parts[2];
      if (!device.startsWith('/dev/')) continue;
      try {
        const stat = await fs.promises.statfs(mountPoint);
        const total = stat.blocks * stat.bsize;
        const free = stat.bavail * stat.bsize;
        const used = total - free;
        partitions.push({
          mountpoint: mountPoint,
          device,
          fstype,
          total,
          used,
          free,
          percent: total > 0 ? Math.round((used / total) * 100) : 0,
        });
      } catch {
        // skip inaccessible
      }
    }
    return partitions;
  } catch {
    return [];
  }
});

// Dialog
ipcMain.handle(IPC.DIALOG_OPEN, async () => {
  const result = await dialog.showOpenDialog(mainWindow!, {
    properties: ['openDirectory'],
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});

// File operations
ipcMain.on(IPC.FILE_OPEN, (_event, filePath: string) => {
  shell.openPath(filePath);
});

ipcMain.on(IPC.FILE_OPEN_DIR, (_event, dirPath: string) => {
  shell.showItemInFolder(dirPath);
});

ipcMain.on(IPC.FILE_DELETE, async (_event, filePath: string, permanent?: boolean) => {
  try {
    if (permanent) {
      await fs.promises.rm(filePath, { recursive: true, force: true });
    } else {
      await shell.trashItem(filePath);
    }
  } catch (e: any) {
    console.error('Delete failed:', e);
  }
});

ipcMain.on(IPC.FILE_COPY_PATH, (_event, filePath: string) => {
  clipboard.writeText(filePath);
});

// Config
ipcMain.handle(IPC.CONFIG_GET, () => {
  return configManager.get();
});

ipcMain.on(IPC.CONFIG_SET, (_event, partial: Partial<AppConfig>) => {
  configManager.set(partial);
});

// Theme
ipcMain.handle(IPC.APP_THEME, (_event, theme: 'light' | 'dark') => {
  configManager.set({ theme });
  nativeTheme.themeSource = theme;
  return configManager.get();
});

// App lifecycle
app.whenReady().then(() => {
  createWindow();
  const config = configManager.get();
  nativeTheme.themeSource = config.theme;
});

app.on('window-all-closed', () => {
  currentScanner?.abort();
  app.quit();
});

app.on('will-quit', () => {
  currentScanner?.abort();
  currentScanner = null;
});
