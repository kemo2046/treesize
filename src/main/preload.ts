import { contextBridge, ipcRenderer } from 'electron';
import { IPC } from '../shared/types';

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
    ipcRenderer.send(IPC.FILE_DELETE, filePath, permanent),
  copyPath: (filePath: string) => ipcRenderer.send(IPC.FILE_COPY_PATH, filePath),

  // Config
  getConfig: () => ipcRenderer.invoke(IPC.CONFIG_GET),
  setConfig: (partial: any) => ipcRenderer.send(IPC.CONFIG_SET, partial),

  // Theme
  setTheme: (theme: string) => ipcRenderer.invoke(IPC.APP_THEME, theme),
});
