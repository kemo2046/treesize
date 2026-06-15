import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { createHash } from 'crypto';
import { ScanResult, ScanProgress, JunkCategory } from '../shared/types';

const MAX_DEPTH = 30;
const DUP_MIN_SIZE = 100 * 1024 * 1024; // 100MB
const TOP_N = 15;
const PROGRESS_THROTTLE_MS = 150;

// Windows system dirs to skip
const WIN_SKIP_DIRS = new Set([
  'C:\\Documents and Settings',
  'C:\\System Volume Information',
  'C:\\$Recycle.Bin',
  'C:\\Windows\\CSC',
  'C:\\Windows\\Installer',
  'C:\\Windows\\WinSxS',
]);

interface ScanOptions {
  topN?: number;
  excludeDirs?: string[];
  enableDuplicateDetection?: boolean;
  onProgress?: (progress: ScanProgress) => void;
  signal?: AbortSignal;
}

export class Scanner {
  private topN: number;
  private excludeDirs: string[];
  private enableDup: boolean;
  private onProgress?: (progress: ScanProgress) => void;
  private signal?: AbortSignal;
  private abortController = new AbortController();

  get stopSignal(): AbortSignal { return this.abortController.signal; }

  abort(): void { this.abortController.abort(); }

  private topDirs: Map<string, number> = new Map();
  private topFiles: Array<[string, number, number]> = [];
  private junkDirs: [string, number][] = [];
  private extStats: Map<string, number> = new Map();
  private ageGroups: Record<string, number> = {
    '0-7天': 0, '1-4周': 0, '1-3月': 0, '3-6月': 0,
    '6-12月': 0, '1-2年': 0, '2年+': 0,
  };
  private dirSizeCache: Map<string, number> = new Map();
  private largeFiles: Map<string, [number, number]> = new Map(); // path -> [size, mtime]

  private scannedItems = 0;
  private totalUsed = 0;
  private startTime = 0;
  private lastProgressTime = 0;
  private aborted = false;

  constructor(options: ScanOptions = {}) {
    this.topN = options.topN ?? TOP_N;
    this.excludeDirs = options.excludeDirs ?? [];
    this.enableDup = options.enableDuplicateDetection ?? true;
    this.onProgress = options.onProgress;
    this.signal = options.signal;
  }

  async scan(rootPath: string): Promise<ScanResult> {
    this.startTime = Date.now();
    this.aborted = false;

    // Listen to both external and internal abort signals
    const signals = [this.signal, this.abortController.signal].filter(Boolean) as AbortSignal[];
    for (const sig of signals) {
      sig.addEventListener('abort', () => { this.aborted = true; }, { once: true });
    }

    // Phase 1: Walk filesystem
    await this.walkDir(rootPath, 0);

    // Phase 2: Detect duplicates
    let duplicates: [number, number, string[]][] = [];
    if (this.enableDup && !this.aborted) {
      duplicates = await this.detectDuplicates();
    }

    // Phase 3: Build junk dirs
    this.buildJunkDirs(rootPath);

    // Sort top dirs by size descending
    const topDirs = [...this.topDirs.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, this.topN);

    // Sort top files by size descending
    const topFiles = this.topFiles
      .sort((a, b) => b[1] - a[1])
      .slice(0, this.topN);

    // Sort ext stats
    const extStats = [...this.extStats.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 15);

    return {
      topDirs,
      topFiles,
      junkDirs: this.junkDirs,
      extStats,
      ageGroups: this.ageGroups,
      dirSizeCache: Object.fromEntries(this.dirSizeCache),
      duplicates,
      totalUsed: this.totalUsed,
      scanTime: (Date.now() - this.startTime) / 1000,
      scannedItems: this.scannedItems,
    };
  }

  private async walkDir(dirPath: string, depth: number): Promise<number> {
    if (this.aborted || depth > MAX_DEPTH) return 0;

    // Skip excluded dirs
    const normalized = this.normalizePath(dirPath);
    for (const ex of this.excludeDirs) {
      if (normalized.startsWith(this.normalizePath(ex))) return 0;
    }

    // Skip Windows system dirs
    if (process.platform === 'win32') {
      const upper = normalized.toUpperCase();
      for (const skip of WIN_SKIP_DIRS) {
        if (upper.startsWith(skip.toUpperCase())) return 0;
      }
    }

    let dirSize = 0;
    let entries: fs.Dirent[];

    try {
      entries = await fs.promises.readdir(dirPath, { withFileTypes: true });
    } catch {
      return 0;
    }

    for (const entry of entries) {
      if (this.aborted) break;

      const fullPath = path.join(dirPath, entry.name);

      try {
        if (entry.isSymbolicLink()) {
          continue; // Skip symlinks
        }

        if (entry.isDirectory()) {
          const childSize = await this.walkDir(fullPath, depth + 1);
          dirSize += childSize;
          this.dirSizeCache.set(this.normalizePath(fullPath), childSize);

          // Track top directories
          this.maintainTopDirs(fullPath, childSize);
        } else if (entry.isFile()) {
          let stat: fs.Stats;
          try {
            stat = await fs.promises.stat(fullPath);
          } catch {
            continue;
          }

          const size = stat.size;
          dirSize += size;
          this.totalUsed += size;
          this.scannedItems++;

          // Extension stats
          const ext = path.extname(entry.name).toLowerCase() || '(无扩展名)';
          this.extStats.set(ext, (this.extStats.get(ext) || 0) + size);

          // Age groups
          const ageDays = (Date.now() - stat.mtimeMs) / (1000 * 60 * 60 * 24);
          this.updateAgeGroup(ageDays);

          // Track large files for duplicate detection
          if (size >= DUP_MIN_SIZE) {
            this.largeFiles.set(fullPath, [size, stat.mtimeMs]);
          }

          // Maintain top files
          this.maintainTopFiles(fullPath, size, stat.mtimeMs);

          // Report progress
          this.reportProgress(fullPath);
        }
      } catch {
        // Permission denied or other errors
        continue;
      }
    }

    // Cache root dir size
    if (depth === 0) {
      this.dirSizeCache.set(this.normalizePath(dirPath), dirSize);
      this.maintainTopDirs(dirPath, dirSize);
    }

    return dirSize;
  }

  private maintainTopDirs(dirPath: string, size: number): void {
    this.topDirs.set(dirPath, size);
    if (this.topDirs.size > this.topN * 3) {
      // Prune to keep only top N
      const sorted = [...this.topDirs.entries()].sort((a, b) => b[1] - a[1]);
      this.topDirs = new Map(sorted.slice(0, this.topN * 2));
    }
  }

  private maintainTopFiles(filePath: string, size: number, mtime: number): void {
    this.topFiles.push([filePath, size, mtime]);
    if (this.topFiles.length > this.topN * 3) {
      this.topFiles.sort((a, b) => b[1] - a[1]);
      this.topFiles = this.topFiles.slice(0, this.topN * 2);
    }
  }

  private updateAgeGroup(ageDays: number): void {
    if (ageDays < 7) this.ageGroups['0-7天']++;
    else if (ageDays < 28) this.ageGroups['1-4周']++;
    else if (ageDays < 90) this.ageGroups['1-3月']++;
    else if (ageDays < 180) this.ageGroups['3-6月']++;
    else if (ageDays < 365) this.ageGroups['6-12月']++;
    else if (ageDays < 730) this.ageGroups['1-2年']++;
    else this.ageGroups['2年+']++;
  }

  private reportProgress(currentPath: string): void {
    const now = Date.now();
    if (now - this.lastProgressTime < PROGRESS_THROTTLE_MS) return;
    this.lastProgressTime = now;

    if (this.onProgress) {
      this.onProgress({
        currentPath,
        scannedItems: this.scannedItems,
        scannedSize: this.totalUsed,
        elapsed: (now - this.startTime) / 1000,
      });
    }
  }

  // --- Duplicate Detection ---

  private async detectDuplicates(): Promise<[number, number, string[]][]> {
    // Phase 1: Hash first 64KB of each large file
    const hashMap = new Map<string, string[]>(); // hash -> [paths]

    const entries = [...this.largeFiles.entries()];

    // Process in batches for concurrency control
    const batchSize = 16;
    for (let i = 0; i < entries.length; i += batchSize) {
      if (this.aborted) break;

      const batch = entries.slice(i, i + batchSize);
      const results = await Promise.allSettled(
        batch.map(async ([filePath, [size]]) => {
          const hash = await this.hashFileHead(filePath, 65536);
          return { filePath, size, hash };
        })
      );

      for (const result of results) {
        if (result.status === 'fulfilled') {
          const { filePath, hash } = result.value;
          if (!hashMap.has(hash)) hashMap.set(hash, []);
          hashMap.get(hash)!.push(filePath);
        }
      }
    }

    // Phase 2: Full hash for matching groups
    const duplicates: [number, number, string[]][] = [];
    const fullHashMap = new Map<string, string[]>();

    for (const [headHash, paths] of hashMap) {
      if (paths.length < 2 || this.aborted) continue;

      for (const filePath of paths) {
        try {
          const fullHash = await this.hashFileFull(filePath);
          if (!fullHashMap.has(fullHash)) fullHashMap.set(fullHash, []);
          fullHashMap.get(fullHash)!.push(filePath);
        } catch {
          // Skip unreadable files
        }
      }
    }

    for (const [_, paths] of fullHashMap) {
      if (paths.length < 2) continue;
      const fileData = this.largeFiles.get(paths[0]);
      const size = fileData?.[0] ?? 0;
      const mtime = fileData?.[1] ?? 0;
      duplicates.push([size, mtime, paths]);
    }

    // Sort by wasted space descending
    duplicates.sort((a, b) => (b[0] * (b[2].length - 1)) - (a[0] * (a[2].length - 1)));
    return duplicates;
  }

  private static readonly HASH_ALG = (() => {
    try { createHash('xxhash64'); return 'xxhash64'; } catch { return 'sha256'; }
  })();

  private async hashFileHead(filePath: string, bytes: number): Promise<string> {
    const fd = await fs.promises.open(filePath, 'r');
    try {
      const buf = Buffer.alloc(bytes);
      const { bytesRead } = await fd.read(buf, 0, bytes, 0);
      return createHash(Scanner.HASH_ALG).update(buf.subarray(0, bytesRead)).digest('hex');
    } finally {
      await fd.close();
    }
  }

  private async hashFileFull(filePath: string): Promise<string> {
    return new Promise((resolve, reject) => {
      const hash = createHash(Scanner.HASH_ALG);
      const stream = fs.createReadStream(filePath);
      stream.on('data', (chunk) => hash.update(chunk));
      stream.on('end', () => resolve(hash.digest('hex')));
      stream.on('error', reject);
    });
  }

  // --- Junk Detection ---

  private buildJunkDirs(rootPath: string): void {
    const junkPaths = this.getJunkPaths(rootPath);
    for (const junkPath of junkPaths) {
      const size = this.dirSizeCache.get(this.normalizePath(junkPath));
      if (size && size > 0) {
        this.junkDirs.push([junkPath, size]);
      }
    }
    this.junkDirs.sort((a, b) => b[1] - a[1]);
  }

  private getJunkPaths(rootPath: string): string[] {
    const plat = process.platform;
    const home = os.homedir();
    const paths: string[] = [];

    if (plat === 'win32') {
      const temp = process.env.TEMP || process.env.TMP || '';
      if (temp) paths.push(temp);
      paths.push('C:\\Windows\\Temp');
      paths.push('C:\\Windows\\Prefetch');
      paths.push('C:\\Windows\\SoftwareDistribution\\Download');
      paths.push('C:\\$Recycle.Bin');
      paths.push(path.join(home, 'Downloads'));
      paths.push(path.join(home, 'AppData', 'Local', 'Temp'));
    } else if (plat === 'darwin') {
      paths.push(path.join(home, '.Trash'));
      paths.push(path.join(home, 'Library', 'Caches'));
      paths.push(path.join(home, 'Library', 'Logs'));
      paths.push('/tmp');
      paths.push('/var/tmp');
    } else {
      paths.push(path.join(home, '.cache'));
      paths.push(path.join(home, '.local', 'share', 'Trash'));
      paths.push('/tmp');
      paths.push('/var/tmp');
    }

    return paths;
  }

  private normalizePath(p: string): string {
    let normalized = path.normalize(p).replace(/[/\\]$/, '');
    // Only lowercase on Windows where paths are case-insensitive
    if (process.platform === 'win32') normalized = normalized.toLowerCase();
    return normalized;
  }
}
