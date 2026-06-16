// IPC channel names
export const IPC = {
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
} as const;

export interface ScanResult {
  topDirs: [string, number][];
  topFiles: [string, number, number][];
  junkDirs: [string, number][];
  extStats: [string, number][];
  ageGroups: Record<string, number>;
  dirSizeCache: Record<string, number>;
  totalUsed: number;
  scanTime: number;
  scannedItems: number;
}

export interface ScanProgress {
  currentPath: string;
  scannedItems: number;
  scannedSize: number;
  elapsed: number;
}

export interface DiskInfo {
  mountpoint: string;
  device: string;
  fstype: string;
  total: number;
  used: number;
  free: number;
  percent: number;
}

export interface AppConfig {
  excludeDirs: string[];
  customJunkDirs: string[];
  lastScanPath: string;
  topN: number;
  theme: 'light' | 'dark';
}

export type Theme = 'light' | 'dark';
