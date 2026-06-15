import { contextBridge, ipcRenderer } from 'electron';
import { IPC, ScanResult, ScanProgress, DiskInfo, AppConfig, HistoryEntry } from '../shared/types';

// Track listeners for cleanup
const listenerCleanups: Array<() => void> = [];

function safeOn(channel: string, callback: (...args: unknown[]) => void): () => void {
  const handler = (_event: Electron.IpcRendererEvent, ...args: unknown[]) => callback(...args);
  ipcRenderer.on(channel, handler);
  const cleanup = () => { ipcRenderer.removeListener(channel, handler); };
  listenerCleanups.push(cleanup);
  return cleanup;
}

function safeOnce(channel: string, callback: (...args: unknown[]) => void): void {
  ipcRenderer.once(channel, (_event, ...args) => callback(...args));
}

contextBridge.exposeInMainWorld('api', {
  // App
  getHomedir: (): Promise<string> => ipcRenderer.invoke('app:homedir'),
  getPath: (name: string): Promise<string> => ipcRenderer.invoke('app:getPath', name),
  showOpenDialog: (options: { properties: string[] }): Promise<{ canceled: boolean; filePaths: string[] }> =>
    ipcRenderer.invoke('app:showOpenDialog', options),

  // Disk
  getDiskInfo: (): Promise<DiskInfo[]> => ipcRenderer.invoke(IPC.DISK_INFO),

  // Scan
  startScan: (scanPath: string) => ipcRenderer.send(IPC.SCAN_START, scanPath),
  stopScan: () => ipcRenderer.send(IPC.SCAN_STOP),
  onScanProgress: (callback: (progress: ScanProgress) => void) => {
    safeOn(IPC.SCAN_PROGRESS, callback as (...args: unknown[]) => void);
  },
  onScanResult: (callback: (result: ScanResult) => void) => {
    safeOn(IPC.SCAN_RESULT, callback as (...args: unknown[]) => void);
  },
  onScanError: (callback: (error: string) => void) => {
    safeOn(IPC.SCAN_ERROR, callback as (...args: unknown[]) => void);
  },

  // File operations
  openFile: (filePath: string) => ipcRenderer.invoke(IPC.FILE_OPEN, filePath),
  openDir: (filePath: string) => ipcRenderer.invoke(IPC.FILE_OPEN_DIR, filePath),
  revealFile: (filePath: string) => ipcRenderer.invoke(IPC.FILE_REVEAL, filePath),
  deleteFile: (filePath: string, permanent: boolean) => ipcRenderer.invoke(IPC.FILE_DELETE, filePath, permanent),
  copyPath: (filePath: string) => ipcRenderer.invoke(IPC.FILE_COPY_PATH, filePath),

  // LLM
  analyzeLLM: (scanResult: ScanResult) => ipcRenderer.send(IPC.LLM_ANALYZE, scanResult),
  stopLLM: () => ipcRenderer.send(IPC.LLM_STOP),
  onLLMStream: (callback: (token: string) => void) => {
    safeOn(IPC.LLM_STREAM, callback as (...args: unknown[]) => void);
  },
  onLLMDone: (callback: () => void) => {
    safeOn(IPC.LLM_DONE, callback as (...args: unknown[]) => void);
  },
  onLLMError: (callback: (error: string) => void) => {
    safeOn(IPC.LLM_ERROR, callback as (...args: unknown[]) => void);
  },
  testLLM: (): Promise<{ ok: boolean; models?: string[]; error?: string }> =>
    ipcRenderer.invoke(IPC.LLM_TEST),

  // Config
  getConfig: (): Promise<AppConfig> => ipcRenderer.invoke(IPC.CONFIG_GET),
  setConfig: (updates: Partial<AppConfig>): Promise<AppConfig> =>
    ipcRenderer.invoke(IPC.CONFIG_SET, updates),

  // History
  getHistory: (): Promise<HistoryEntry[]> => ipcRenderer.invoke(IPC.HISTORY_GET),
  clearHistory: (): Promise<void> => ipcRenderer.invoke(IPC.HISTORY_CLEAR),

  // Export
  exportCSV: (scanResult: ScanResult) => ipcRenderer.invoke(IPC.EXPORT_CSV, scanResult),
  exportJSON: (scanResult: ScanResult) => ipcRenderer.invoke(IPC.EXPORT_JSON, scanResult),
  exportMD: (content: string) => ipcRenderer.invoke(IPC.EXPORT_MD, content),

  // Theme
  setTheme: (theme: 'light' | 'dark') => ipcRenderer.invoke(IPC.APP_THEME, theme),
});
