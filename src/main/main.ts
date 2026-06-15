import { app, BrowserWindow, ipcMain, shell, clipboard, dialog, nativeTheme } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import { Scanner } from './scanner';
import { ConfigManager } from './config';
import { LLMAnalyzer } from './llm';
import { IPC, ScanResult, DiskInfo, HistoryEntry } from '../shared/types';

let mainWindow: BrowserWindow | null = null;
let currentScanner: Scanner | null = null;
let llmAnalyzer: LLMAnalyzer | null = null;
const configManager = new ConfigManager();

function createWindow(): void {
  const geometry = configManager.getGeometry();
  const config = configManager.get();

  mainWindow = new BrowserWindow({
    width: (geometry.width as number) || 1280,
    height: (geometry.height as number) || 800,
    x: geometry.x as number | undefined,
    y: geometry.y as number | undefined,
    minWidth: 900,
    minHeight: 600,
    title: '磁盘空间分析工具',
    backgroundColor: config.theme === 'dark' ? '#1a1a2e' : '#f8f9fc',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    show: false,
  });

  // Load the renderer
  if (process.env.NODE_ENV === 'development') {
    mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', '..', 'renderer', 'index.html'));
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
  });

  mainWindow.on('close', () => {
    if (mainWindow) {
      const bounds = mainWindow.getBounds();
      configManager.saveGeometry({
        width: bounds.width,
        height: bounds.height,
        x: bounds.x,
        y: bounds.y,
      });
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// --- IPC Handlers ---

// App helpers
ipcMain.handle('app:homedir', () => os.homedir());
ipcMain.handle('app:getPath', (_event, name: string) => app.getPath(name as any));
ipcMain.handle('app:showOpenDialog', async (_event, options: Electron.OpenDialogOptions) => {
  if (!mainWindow) return { canceled: true, filePaths: [] };
  return dialog.showOpenDialog(mainWindow, options);
});

// Disk info
ipcMain.handle(IPC.DISK_INFO, async (): Promise<DiskInfo[]> => {
  const disks: DiskInfo[] = [];

  if (process.platform === 'win32') {
    // Windows: scan drive letters
    for (let i = 65; i <= 90; i++) {
      const letter = String.fromCharCode(i);
      const drive = `${letter}:\\`;
      try {
        const stat = await fs.promises.statfs(drive);
        const total = stat.blocks * stat.bsize;
        const free = stat.bavail * stat.bsize;
        const used = total - free;
        disks.push({
          mountpoint: drive,
          device: `${letter}:`,
          fstype: 'NTFS',
          total,
          used,
          free,
          percent: total > 0 ? Math.round((used / total) * 100) : 0,
        });
      } catch {
        // Drive doesn't exist
      }
    }
  } else {
    // Unix: use mountpoints
    try {
      const mounts = process.platform === 'darwin'
        ? ['/']
        : await getLinuxMounts();

      for (const mount of mounts) {
        try {
          const stat = await fs.promises.statfs(mount);
          const total = stat.blocks * stat.bsize;
          const free = stat.bavail * stat.bsize;
          const used = total - free;
          disks.push({
            mountpoint: mount,
            device: mount,
            fstype: '',
            total,
            used,
            free,
            percent: total > 0 ? Math.round((used / total) * 100) : 0,
          });
        } catch {
          // Skip
        }
      }
    } catch {
      // Fallback
      disks.push({
        mountpoint: '/',
        device: '/',
        fstype: '',
        total: 0,
        used: 0,
        free: 0,
        percent: 0,
      });
    }
  }

  return disks;
});

async function getLinuxMounts(): Promise<string[]> {
  try {
    const content = await fs.promises.readFile('/proc/mounts', 'utf-8');
    const seen = new Set<string>();
    const mounts: string[] = [];
    for (const line of content.split('\n')) {
      const parts = line.split(' ');
      if (parts.length >= 2) {
        // Decode kernel octal escapes (e.g. \040 for space)
        const mountPoint = parts[1].replace(/\\(\d{3})/g, (_, oct) => String.fromCharCode(parseInt(oct, 8)));
        if (mountPoint.startsWith('/') && !mountPoint.startsWith('/proc') && !mountPoint.startsWith('/sys') && !mountPoint.startsWith('/dev') && !seen.has(mountPoint)) {
          seen.add(mountPoint);
          mounts.push(mountPoint);
        }
      }
    }
    // Sort by path length descending so most-specific mountpoints match first
    mounts.sort((a, b) => b.length - a.length);
    return mounts.length > 0 ? mounts : ['/'];
  } catch {
    return ['/'];
  }
}

// Scan
ipcMain.on(IPC.SCAN_START, (event, scanPath: string) => {
  // Abort any in-progress scan (Bug #2)
  currentScanner?.abort();
  currentScanner = null;

  const config = configManager.get();

  currentScanner = new Scanner({
    topN: config.topN,
    excludeDirs: config.excludeDirs,
    enableDuplicateDetection: config.duplicateDetection,
    onProgress: (progress) => {
      mainWindow?.webContents.send(IPC.SCAN_PROGRESS, progress);
    },
  });

  currentScanner.scan(scanPath).then((result) => {
    // Save to history
    const entry: HistoryEntry = {
      timestamp: Date.now(),
      scanPath,
      totalUsed: result.totalUsed,
      scanTime: result.scanTime,
      scannedItems: result.scannedItems,
      cleanableSize: result.junkDirs.reduce((sum, [, size]) => sum + size, 0),
      duplicateGroups: result.duplicates.length,
    };
    configManager.addHistoryEntry(entry);
    configManager.set({ lastScanPath: scanPath });

    mainWindow?.webContents.send(IPC.SCAN_RESULT, result);
    currentScanner = null;
  }).catch((err) => {
    mainWindow?.webContents.send(IPC.SCAN_ERROR, err.message);
    currentScanner = null;
  });
});

ipcMain.on(IPC.SCAN_STOP, () => {
  currentScanner?.abort();
});

// File operations
ipcMain.handle(IPC.FILE_OPEN, async (_event, filePath: string) => {
  await shell.openPath(filePath);
});

ipcMain.handle(IPC.FILE_OPEN_DIR, async (_event, filePath: string) => {
  // showItemInFolder works on all platforms (Win: Explorer, Mac: Finder, Linux: file manager)
  shell.showItemInFolder(filePath);
});

ipcMain.handle(IPC.FILE_REVEAL, async (_event, filePath: string) => {
  shell.showItemInFolder(filePath);
});

ipcMain.handle(IPC.FILE_DELETE, async (_event, filePath: string, permanent: boolean) => {
  if (configManager.get().simulateMode) {
    return { success: true, simulated: true };
  }

  try {
    if (permanent) {
      // rm handles both files and directories — avoids TOCTOU race
      await fs.promises.rm(filePath, { recursive: true, force: true });
    } else {
      // Move to trash
      await shell.trashItem(filePath);
    }
    return { success: true };
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return { success: false, error: message };
  }
});

ipcMain.handle(IPC.FILE_COPY_PATH, async (_event, filePath: string) => {
  clipboard.writeText(filePath);
});

// LLM
ipcMain.on(IPC.LLM_ANALYZE, async (_event, scanResult: ScanResult) => {
  try {
    const config = configManager.get();
    if (!config.llmApiUrl || !config.llmApiKey) {
      mainWindow?.webContents.send(IPC.LLM_ERROR, '请先配置 LLM API 地址和密钥');
      return;
    }

    llmAnalyzer = new LLMAnalyzer();

    await llmAnalyzer.analyze(scanResult, {
      apiUrl: config.llmApiUrl,
      apiKey: config.llmApiKey,
      model: config.llmModel,
      temperature: config.llmTemperature,
      onToken: (token) => {
        mainWindow?.webContents.send(IPC.LLM_STREAM, token);
      },
      onDone: () => {
        mainWindow?.webContents.send(IPC.LLM_DONE);
        llmAnalyzer = null;
      },
      onError: (error) => {
        mainWindow?.webContents.send(IPC.LLM_ERROR, error);
        llmAnalyzer = null;
      },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    mainWindow?.webContents.send(IPC.LLM_ERROR, message);
    llmAnalyzer = null;
  }
});

ipcMain.on(IPC.LLM_STOP, () => {
  llmAnalyzer?.stop();
  llmAnalyzer = null;
});

ipcMain.handle(IPC.LLM_TEST, async () => {
  const config = configManager.get();
  if (!config.llmApiUrl) {
    return { ok: false, error: '请填写 API 地址' };
  }

  const analyzer = new LLMAnalyzer();
  return analyzer.testConnection(config.llmApiUrl, config.llmApiKey);
});

// Config
ipcMain.handle(IPC.CONFIG_GET, () => {
  return configManager.get();
});

ipcMain.handle(IPC.CONFIG_SET, (_event, updates: Record<string, unknown>) => {
  configManager.set(updates);
  return configManager.get();
});

// History
ipcMain.handle(IPC.HISTORY_GET, () => {
  return configManager.getHistory();
});

ipcMain.handle(IPC.HISTORY_CLEAR, () => {
  configManager.clearHistory();
});

// Export
ipcMain.handle(IPC.EXPORT_CSV, async (_event, scanResult: ScanResult) => {
  if (!mainWindow) return;
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: 'disk_analysis.csv',
    filters: [{ name: 'CSV Files', extensions: ['csv'] }],
  });

  if (result.canceled || !result.filePath) return;

  const lines: string[] = ['﻿类别,大小,路径或扩展名,修改时间'];

  for (const [dirPath, size] of scanResult.topDirs) {
    lines.push(`大目录,${formatSizeCSV(size)},${csvEscape(dirPath)},`);
  }
  for (const [filePath, size, mtime] of scanResult.topFiles) {
    const date = new Date(mtime * 1000).toLocaleDateString('zh-CN');
    lines.push(`大文件,${formatSizeCSV(size)},${csvEscape(filePath)},${date}`);
  }
  for (const [junkPath, size] of scanResult.junkDirs) {
    lines.push(`垃圾目录,${formatSizeCSV(size)},${csvEscape(junkPath)},`);
  }

  fs.writeFileSync(result.filePath, lines.join('\n'), 'utf-8');
});

ipcMain.handle(IPC.EXPORT_JSON, async (_event, scanResult: ScanResult) => {
  if (!mainWindow) return;
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: 'disk_analysis.json',
    filters: [{ name: 'JSON Files', extensions: ['json'] }],
  });

  if (result.canceled || !result.filePath) return;

  fs.writeFileSync(result.filePath, JSON.stringify(scanResult, null, 2), 'utf-8');
});

ipcMain.handle(IPC.EXPORT_MD, async (_event, content: string) => {
  if (!mainWindow) return;
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: 'ai_analysis.md',
    filters: [{ name: 'Markdown Files', extensions: ['md'] }],
  });

  if (result.canceled || !result.filePath) return;

  const header = `# AI 磁盘分析报告\n\n导出时间: ${new Date().toLocaleString('zh-CN')}\n\n---\n\n`;
  fs.writeFileSync(result.filePath, header + content, 'utf-8');
});

// Theme
ipcMain.handle(IPC.APP_THEME, (_event, theme: 'light' | 'dark') => {
  configManager.set({ theme });
  nativeTheme.themeSource = theme;
});

// App lifecycle
app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// Helpers
function formatSizeCSV(bytes: number): string {
  if (bytes >= 1024 ** 4) return `${(bytes / 1024 ** 4).toFixed(2)} TB`;
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(2)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(2)} KB`;
  return `${bytes} B`;
}

function csvEscape(s: string): string {
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}
