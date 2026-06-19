// Increase libuv thread pool for concurrent fs I/O during scanning
process.env.UV_THREADPOOL_SIZE = '64';

import { app, BrowserWindow, ipcMain, shell, clipboard, nativeTheme, dialog } from 'electron';
import * as fs from 'fs';
import * as path from 'path';
import { Scanner, ScanResult, ScanProgress } from './scanner';
import { ConfigManager, AppConfig, HistoryEntry } from './config';
import { IPC, TreeNodeEntry } from '../shared/types';
import { LLMAnalyzer } from './llm';

let mainWindow: BrowserWindow | null = null;
let currentScanner: Scanner | null = null;
let lastScanResult: ScanResult | null = null;
let currentLLM: LLMAnalyzer | null = null;
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
    autoHideMenuBar: true,
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

function csvEscape(val: string): string {
  if (/[",\r\n]/.test(val)) return '"' + val.replace(/"/g, '""') + '"';
  return val;
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

  const config = configManager.get();
  const scanner = new Scanner(
    scanPath,
    (progress: ScanProgress) => {
      mainWindow?.webContents.send(IPC.SCAN_PROGRESS, progress);
    },
    {
      excludeDirs: config.excludeDirs,
      customJunkDirs: config.customJunkDirs,
      topN: config.topN,
      enableDupDetection: config.duplicateDetection,
    },
  );

  currentScanner = scanner;

  scanner.scan().then((result: ScanResult) => {
    if (currentScanner === scanner) {
      lastScanResult = result;
      mainWindow?.webContents.send(IPC.SCAN_RESULT, result);
      currentScanner = null;

      // Auto-save to history
      const junkSize = result.junkDirs.reduce((sum, [, s]) => sum + s, 0);
      configManager.addHistory({
        timestamp: Date.now(),
        scanPath,
        totalUsed: result.totalUsed,
        scanTime: result.scanTime,
        scannedItems: result.scannedItems,
        junkSize,
      });
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

ipcMain.handle(IPC.FILE_DELETE, async (_event, filePath: string, permanent?: boolean) => {
  try {
    if (permanent) {
      await fs.promises.rm(filePath, { recursive: true, force: true });
    } else {
      await shell.trashItem(filePath);
    }
    return { ok: true };
  } catch (e: any) {
    console.error('Delete failed:', e);
    return { ok: false, error: e.message };
  }
});

ipcMain.on(IPC.FILE_COPY_PATH, (_event, filePath: string) => {
  clipboard.writeText(filePath);
});

// Config
ipcMain.handle(IPC.CONFIG_GET, () => {
  return configManager.get();
});

ipcMain.handle(IPC.CONFIG_SET, (_event, partial: Partial<AppConfig>) => {
  configManager.set(partial);
  return configManager.get();
});

// Theme
ipcMain.handle(IPC.APP_THEME, (_event, theme: 'light' | 'dark') => {
  configManager.set({ theme });
  nativeTheme.themeSource = theme;
  return configManager.get();
});

// History
ipcMain.handle(IPC.HISTORY_GET, () => {
  return configManager.getHistory();
});

ipcMain.on(IPC.HISTORY_ADD, (_event, entry: HistoryEntry) => {
  configManager.addHistory(entry);
});

// Export
ipcMain.handle(IPC.EXPORT_CSV, async () => {
  if (!lastScanResult) return { ok: false, error: '没有扫描数据' };
  const r = lastScanResult;

  const result = await dialog.showSaveDialog(mainWindow!, {
    title: '导出 CSV 报告',
    defaultPath: 'disk_report.csv',
    filters: [{ name: 'CSV', extensions: ['csv'] }],
  });
  if (result.canceled || !result.filePath) return { ok: false };

  const lines: string[] = [];
  lines.push('﻿' + ['类别', '大小', '路径/扩展名', '修改时间'].map(csvEscape).join(','));

  for (const [dir, size] of r.topDirs) {
    lines.push([csvEscape('大目录'), csvEscape(String(size)), csvEscape(dir), ''].join(','));
  }
  for (const [file, size, mtime] of r.topFiles) {
    const date = mtime ? new Date(mtime * 1000).toLocaleDateString('zh-CN') : '';
    lines.push([csvEscape('大文件'), csvEscape(String(size)), csvEscape(file), csvEscape(date)].join(','));
  }
  for (const [ext, size] of r.extStats) {
    lines.push([csvEscape('文件类型'), csvEscape(String(size)), csvEscape(ext), ''].join(','));
  }
  for (const [dir, size] of r.junkDirs) {
    lines.push([csvEscape('垃圾目录'), csvEscape(String(size)), csvEscape(dir), ''].join(','));
  }

  try {
    await fs.promises.writeFile(result.filePath, lines.join('\n'), 'utf-8');
    return { ok: true };
  } catch (e: any) {
    return { ok: false, error: e.message };
  }
});

ipcMain.handle(IPC.EXPORT_JSON, async () => {
  if (!lastScanResult) return { ok: false, error: '没有扫描数据' };
  const r = lastScanResult;

  const result = await dialog.showSaveDialog(mainWindow!, {
    title: '导出 JSON 报告',
    defaultPath: 'disk_report.json',
    filters: [{ name: 'JSON', extensions: ['json'] }],
  });
  if (result.canceled || !result.filePath) return { ok: false };

  const data = {
    scanTime: new Date().toISOString(),
    totalUsed: r.totalUsed,
    scannedItems: r.scannedItems,
    topDirs: r.topDirs.map(([p, s]) => ({ path: p, size: s })),
    topFiles: r.topFiles.map(([p, s, m]) => ({ path: p, size: s, mtime: m })),
    junkDirs: r.junkDirs.map(([p, s]) => ({ path: p, size: s })),
    extStats: r.extStats.map(([e, s]) => ({ ext: e, size: s })),
    duplicates: r.duplicates.map(([s, paths]) => ({ size: s, copies: paths.length, paths })),
  };

  try {
    await fs.promises.writeFile(result.filePath, JSON.stringify(data, null, 2), 'utf-8');
    return { ok: true };
  } catch (e: any) {
    return { ok: false, error: e.message };
  }
});

ipcMain.handle(IPC.EXPORT_MD, async () => {
  if (!lastScanResult) return { ok: false, error: '没有扫描数据' };
  const r = lastScanResult;

  const result = await dialog.showSaveDialog(mainWindow!, {
    title: '导出 Markdown 报告',
    defaultPath: 'disk_report.md',
    filters: [{ name: 'Markdown', extensions: ['md'] }],
  });
  if (result.canceled || !result.filePath) return { ok: false };

  const fmt = (b: number) => {
    if (!Number.isFinite(b) || b < 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let v = b;
    for (const u of units) { if (v < 1024) return v.toFixed(2) + ' ' + u; v /= 1024; }
    return v.toFixed(2) + ' PB';
  };

  const lines: string[] = [];
  lines.push(`# 磁盘空间分析报告`);
  lines.push('');
  lines.push(`- 扫描时间: ${new Date().toLocaleString('zh-CN')}`);
  lines.push(`- 总用量: ${fmt(r.totalUsed)}`);
  lines.push(`- 文件数: ${r.scannedItems.toLocaleString()}`);
  lines.push(`- 扫描耗时: ${r.scanTime.toFixed(2)}s`);
  lines.push('');

  lines.push('## 大目录 Top 15');
  lines.push('');
  lines.push('| # | 路径 | 大小 |');
  lines.push('|---|------|------|');
  r.topDirs.forEach(([p, s], i) => { lines.push(`| ${i + 1} | ${p} | ${fmt(s)} |`); });
  lines.push('');

  lines.push('## 大文件 Top 15');
  lines.push('');
  lines.push('| # | 文件 | 大小 | 修改时间 |');
  lines.push('|---|------|------|----------|');
  r.topFiles.forEach(([p, s, m], i) => {
    const d = m ? new Date(m * 1000).toLocaleDateString('zh-CN') : '-';
    lines.push(`| ${i + 1} | ${path.basename(p)} | ${fmt(s)} | ${d} |`);
  });
  lines.push('');

  lines.push('## 文件类型统计');
  lines.push('');
  lines.push('| 扩展名 | 大小 |');
  lines.push('|--------|------|');
  r.extStats.forEach(([e, s]) => { lines.push(`| ${e} | ${fmt(s)} |`); });
  lines.push('');

  if (r.junkDirs.length > 0) {
    lines.push('## 可清理目录');
    lines.push('');
    lines.push('| 路径 | 大小 |');
    lines.push('|------|------|');
    r.junkDirs.forEach(([p, s]) => { lines.push(`| ${p} | ${fmt(s)} |`); });
    lines.push('');
  }

  if (r.duplicates.length > 0) {
    const totalWaste = r.duplicates.reduce((sum, [s, paths]) => sum + s * (paths.length - 1), 0);
    lines.push('## 重复文件');
    lines.push('');
    lines.push(`- 分组数: ${r.duplicates.length}`);
    lines.push(`- 可回收空间: ${fmt(totalWaste)}`);
    lines.push('');
    r.duplicates.slice(0, 20).forEach(([s, paths], i) => {
      lines.push(`### 分组 ${i + 1} — ${fmt(s)} × ${paths.length} 份`);
      lines.push('');
      paths.forEach(p => { lines.push(`- \`${p}\``); });
      lines.push('');
    });
  }

  try {
    await fs.promises.writeFile(result.filePath, lines.join('\n'), 'utf-8');
    return { ok: true };
  } catch (e: any) {
    return { ok: false, error: e.message };
  }
});

// Window controls
ipcMain.on('win:minimize', () => mainWindow?.minimize());
ipcMain.on('win:maximize', () => {
  if (mainWindow?.isMaximized()) mainWindow.unmaximize();
  else mainWindow?.maximize();
});
ipcMain.on('win:close', () => mainWindow?.close());
ipcMain.handle('win:isMaximized', () => mainWindow?.isMaximized() ?? false);

// Tree: list directory children with cached sizes
ipcMain.handle(IPC.TREE_LIST_DIR, async (_event, dirPath: string) => {
  if (!dirPath || typeof dirPath !== 'string') return [];
  let entries: fs.Dirent[];
  try {
    entries = await fs.promises.readdir(dirPath, { withFileTypes: true });
  } catch {
    return [];
  }

  const dirCache = lastScanResult?.dirSizeCache || {};
  const dirs: TreeNodeEntry[] = [];
  const files: TreeNodeEntry[] = [];

  const fileEntries: { entry: fs.Dirent; fullPath: string }[] = [];

  for (const entry of entries) {
    if (entry.isSymbolicLink()) continue;
    const fullPath = path.join(dirPath, entry.name);
    if (entry.isDirectory()) {
      const norm = path.resolve(fullPath);
      const size = dirCache[norm] ?? -1;
      dirs.push({ name: entry.name, fullPath, isDir: true, size });
    } else if (entry.isFile()) {
      fileEntries.push({ entry, fullPath });
    }
  }

  // Batch stat files
  const stats = await Promise.all(
    fileEntries.map(({ fullPath }) => fs.promises.stat(fullPath).catch(() => null)),
  );
  for (let i = 0; i < stats.length; i++) {
    const stat = stats[i];
    if (!stat) continue;
    files.push({
      name: fileEntries[i].entry.name,
      fullPath: fileEntries[i].fullPath,
      isDir: false,
      size: stat.size,
    });
  }

  // Sort: dirs by size desc, then files by size desc
  dirs.sort((a, b) => b.size - a.size);
  files.sort((a, b) => b.size - a.size);
  return [...dirs, ...files];
});

// LLM Analysis
ipcMain.on(IPC.LLM_ANALYZE, async () => {
  if (!lastScanResult) {
    mainWindow?.webContents.send(IPC.LLM_ERROR, '请先扫描');
    return;
  }
  const config = configManager.get();
  if (!config.llmApiUrl || !config.llmModel) {
    mainWindow?.webContents.send(IPC.LLM_ERROR, '请先在设置中配置 LLM API 地址和模型');
    return;
  }

  currentLLM?.stop();
  const analyzer = new LLMAnalyzer();
  currentLLM = analyzer;

  const scanPath = config.lastScanPath || '/';
  await analyzer.analyze(
    scanPath,
    lastScanResult,
    config,
    (token) => mainWindow?.webContents.send(IPC.LLM_STREAM, token),
    (fullText, error) => {
      if (currentLLM === analyzer) {
        if (error) {
          mainWindow?.webContents.send(IPC.LLM_ERROR, error);
        } else {
          mainWindow?.webContents.send(IPC.LLM_DONE, fullText);
        }
        currentLLM = null;
      }
    },
  );
});

ipcMain.on(IPC.LLM_STOP, () => {
  currentLLM?.stop();
  currentLLM = null;
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
