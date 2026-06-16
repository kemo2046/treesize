// Shared type definitions for disk analyzer

export interface ScanResult {
  topDirs: [string, number][];
  topFiles: [string, number, number][];
  junkDirs: [string, number][];
  extStats: [string, number][];
  ageGroups: Record<string, number>;
  dirSizeCache: Record<string, number>;
  duplicates: [number, number, string[]][]; // [size, mtime, paths[]]
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
  llmApiUrl: string;
  llmApiKey: string;
  llmModel: string;
  llmTemperature: number;
  duplicateDetection: boolean;
  simulateMode: boolean;
  theme: 'light' | 'dark';
}

export interface HistoryEntry {
  timestamp: number;
  scanPath: string;
  totalUsed: number;
  scanTime: number;
  scannedItems: number;
  cleanableSize: number;
  duplicateGroups: number;
}

export interface FileInfo {
  name: string;
  path: string;
  size: number;
  mtime: number;
  ext: string;
}

export interface DupGroup {
  hash: string;
  size: number;
  files: FileInfo[];
  wasted: number;
}

export interface JunkCategory {
  name: string;
  icon: string;
  paths: [string, number][];
  totalSize: number;
  fileCount: number;
}

// IPC channel names
export const IPC = {
  // Scan
  SCAN_START: 'scan:start',
  SCAN_STOP: 'scan:stop',
  SCAN_PROGRESS: 'scan:progress',
  SCAN_RESULT: 'scan:result',
  SCAN_ERROR: 'scan:error',

  // Disk
  DISK_INFO: 'disk:info',

  // File operations
  FILE_OPEN: 'file:open',
  FILE_OPEN_DIR: 'file:open-dir',
  FILE_DELETE: 'file:delete',
  FILE_COPY_PATH: 'file:copy-path',
  FILE_REVEAL: 'file:reveal',

  // LLM
  LLM_ANALYZE: 'llm:analyze',
  LLM_STOP: 'llm:stop',
  LLM_STREAM: 'llm:stream',
  LLM_DONE: 'llm:done',
  LLM_ERROR: 'llm:error',
  LLM_TEST: 'llm:test',

  // Config
  CONFIG_GET: 'config:get',
  CONFIG_SET: 'config:set',

  // History
  HISTORY_GET: 'history:get',
  HISTORY_CLEAR: 'history:clear',

  // Export
  EXPORT_CSV: 'export:csv',
  EXPORT_JSON: 'export:json',
  EXPORT_MD: 'export:md',

  // App
  APP_THEME: 'app:theme',
} as const;

export type Theme = 'light' | 'dark';
