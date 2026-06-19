import { contextBridge, ipcRenderer } from 'electron';

// Inline IPC constants to avoid require path issues in preload context
const IPC = {
  SCAN_START: 'scan:start',
  SCAN_STOP: 'scan:stop',
  SCAN_PROGRESS: 'scan:progress',
  SCAN_RESULT: 'scan:result',
  SCAN_ERROR: 'scan:error',
  DISK_INFO: 'disk:info',
  FILE_OPEN: 'file:open',
  FILE_OPEN_DIR: 'file:open-dir',
  FILE_DELETE: 'file:delete',
  FILE_COPY_PATH: 'file:copy-path',
  CONFIG_GET: 'config:get',
  CONFIG_SET: 'config:set',
  APP_THEME: 'app:theme',
  DIALOG_OPEN: 'dialog:open',
  EXPORT_CSV: 'export:csv',
  EXPORT_JSON: 'export:json',
  EXPORT_MD: 'export:md',
  HISTORY_GET: 'history:get',
  HISTORY_ADD: 'history:add',
  TREE_LIST_DIR: 'tree:list-dir',
  LLM_ANALYZE: 'llm:analyze',
  LLM_STOP: 'llm:stop',
  LLM_STREAM: 'llm:stream',
  LLM_DONE: 'llm:done',
  LLM_ERROR: 'llm:error',
};

try {
  contextBridge.exposeInMainWorld('api', {
    // Scan
    startScan: (scanPath: string) => ipcRenderer.send(IPC.SCAN_START, scanPath),
    stopScan: () => ipcRenderer.send(IPC.SCAN_STOP),
    onScanProgress: (cb: (data: any) => void) => {
      const handler = (_event: any, data: any) => cb(data);
      ipcRenderer.on(IPC.SCAN_PROGRESS, handler);
      return () => ipcRenderer.removeListener(IPC.SCAN_PROGRESS, handler);
    },
    onScanResult: (cb: (data: any) => void) => {
      const handler = (_event: any, data: any) => cb(data);
      ipcRenderer.on(IPC.SCAN_RESULT, handler);
      return () => ipcRenderer.removeListener(IPC.SCAN_RESULT, handler);
    },
    onScanError: (cb: (msg: string) => void) => {
      const handler = (_event: any, msg: string) => cb(msg);
      ipcRenderer.on(IPC.SCAN_ERROR, handler);
      return () => ipcRenderer.removeListener(IPC.SCAN_ERROR, handler);
    },

    // Disk
    getDiskInfo: () => ipcRenderer.invoke(IPC.DISK_INFO),

    // File ops
    openFile: (filePath: string) => ipcRenderer.send(IPC.FILE_OPEN, filePath),
    openDir: (dirPath: string) => ipcRenderer.send(IPC.FILE_OPEN_DIR, dirPath),
    deleteFile: (filePath: string, permanent?: boolean) =>
      ipcRenderer.invoke(IPC.FILE_DELETE, filePath, permanent),
    copyPath: (filePath: string) => ipcRenderer.send(IPC.FILE_COPY_PATH, filePath),

    // Config
    getConfig: () => ipcRenderer.invoke(IPC.CONFIG_GET),
    setConfig: (partial: any) => ipcRenderer.invoke(IPC.CONFIG_SET, partial),

    // Theme
    setTheme: (theme: string) => ipcRenderer.invoke(IPC.APP_THEME, theme),

    // Dialog
    showOpenDialog: () => ipcRenderer.invoke(IPC.DIALOG_OPEN),

    // Export
    exportCSV: () => ipcRenderer.invoke(IPC.EXPORT_CSV),
    exportJSON: () => ipcRenderer.invoke(IPC.EXPORT_JSON),
    exportMD: () => ipcRenderer.invoke(IPC.EXPORT_MD),

    // History
    getHistory: () => ipcRenderer.invoke(IPC.HISTORY_GET),
    addHistory: (entry: any) => ipcRenderer.send(IPC.HISTORY_ADD, entry),

    // Tree
    treeListDir: (dirPath: string) => ipcRenderer.invoke(IPC.TREE_LIST_DIR, dirPath),

    // LLM
    llmAnalyze: () => ipcRenderer.send(IPC.LLM_ANALYZE),
    llmStop: () => ipcRenderer.send(IPC.LLM_STOP),
    onLlmStream: (cb: (token: string) => void) => {
      const handler = (_event: any, token: string) => cb(token);
      ipcRenderer.on(IPC.LLM_STREAM, handler);
      return () => ipcRenderer.removeListener(IPC.LLM_STREAM, handler);
    },
    onLlmDone: (cb: (fullText: string) => void) => {
      const handler = (_event: any, fullText: string) => cb(fullText);
      ipcRenderer.on(IPC.LLM_DONE, handler);
      return () => ipcRenderer.removeListener(IPC.LLM_DONE, handler);
    },
    onLlmError: (cb: (msg: string) => void) => {
      const handler = (_event: any, msg: string) => cb(msg);
      ipcRenderer.on(IPC.LLM_ERROR, handler);
      return () => ipcRenderer.removeListener(IPC.LLM_ERROR, handler);
    },

    // Window controls
    winMinimize: () => ipcRenderer.send('win:minimize'),
    winMaximize: () => ipcRenderer.send('win:maximize'),
    winClose: () => ipcRenderer.send('win:close'),
    winIsMaximized: () => ipcRenderer.invoke('win:isMaximized'),
  });
  console.log('[preload] API exposed successfully');
} catch (e) {
  console.error('[preload] Failed to expose API:', e);
}
