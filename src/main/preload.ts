import { contextBridge, ipcRenderer } from 'electron';
import { IPC, ScanResult, ScanProgress, DiskInfo, AppConfig, HistoryEntry } from '../shared/types';

contextBridge.exposeInMainWorld('api', {
  // Disk
  getDiskInfo: (): Promise<DiskInfo[]> => ipcRenderer.invoke(IPC.DISK_INFO),

  // Scan
  startScan: (path: string) => ipcRenderer.send(IPC.SCAN_START, path),
  stopScan: () => ipcRenderer.send(IPC.SCAN_STOP),
  onScanProgress: (callback: (progress: ScanProgress) => void) => {
    ipcRenderer.on(IPC.SCAN_PROGRESS, (_, data) => callback(data));
  },
  onScanResult: (callback: (result: ScanResult) => void) => {
    ipcRenderer.on(IPC.SCAN_RESULT, (_, data) => callback(data));
  },
  onScanError: (callback: (error: string) => void) => {
    ipcRenderer.on(IPC.SCAN_ERROR, (_, data) => callback(data));
  },

  // File operations
  openFile: (path: string) => ipcRenderer.invoke(IPC.FILE_OPEN, path),
  openDir: (path: string) => ipcRenderer.invoke(IPC.FILE_OPEN_DIR, path),
  revealFile: (path: string) => ipcRenderer.invoke(IPC.FILE_REVEAL, path),
  deleteFile: (path: string, permanent: boolean) => ipcRenderer.invoke(IPC.FILE_DELETE, path, permanent),
  copyPath: (path: string) => ipcRenderer.invoke(IPC.FILE_COPY_PATH, path),

  // LLM
  analyzeLLM: (scanResult: ScanResult) => ipcRenderer.send(IPC.LLM_ANALYZE, scanResult),
  stopLLM: () => ipcRenderer.send(IPC.LLM_STOP),
  onLLMStream: (callback: (token: string) => void) => {
    ipcRenderer.on(IPC.LLM_STREAM, (_, data) => callback(data));
  },
  onLLMDone: (callback: () => void) => {
    ipcRenderer.on(IPC.LLM_DONE, () => callback());
  },
  onLLMError: (callback: (error: string) => void) => {
    ipcRenderer.on(IPC.LLM_ERROR, (_, data) => callback(data));
  },
  testLLM: (): Promise<{ ok: boolean; models?: string[]; error?: string }> =>
    ipcRenderer.invoke(IPC.LLM_TEST),

  // Config
  getConfig: (): Promise<AppConfig> => ipcRenderer.invoke(IPC.CONFIG_GET),
  setConfig: (updates: Partial<AppConfig>): Promise<AppConfig> =>
    ipcRenderer.invoke(IPC.CONFIG_SET, updates),

  // History
  getHistory: (): Promise<HistoryEntry[]> => ipcRenderer.invoke(IPC.HISTORY_GET),
  clearHistory: () => ipcRenderer.send(IPC.HISTORY_CLEAR),

  // Export
  exportCSV: (scanResult: ScanResult) => ipcRenderer.invoke(IPC.EXPORT_CSV, scanResult),
  exportJSON: (scanResult: ScanResult) => ipcRenderer.invoke(IPC.EXPORT_JSON, scanResult),
  exportMD: (content: string) => ipcRenderer.invoke(IPC.EXPORT_MD, content),

  // Theme
  setTheme: (theme: 'light' | 'dark') => ipcRenderer.invoke(IPC.APP_THEME, theme),
});
