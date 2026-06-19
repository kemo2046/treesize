import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import * as crypto from 'crypto';

// ---- Types ----

export interface ScanResult {
  topDirs: [string, number][];
  topFiles: [string, number, number][];
  junkDirs: [string, number][];
  extStats: [string, number][];
  ageGroups: Record<string, number>;
  dirSizeCache: Record<string, number>;
  duplicates: [number, string[]][];
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

// ---- Constants ----

const MAX_DEPTH = 30;
const TOP_N = 15;
const PROGRESS_INTERVAL_MS = 150;
const STAT_BATCH = 256;
const DUP_MIN_SIZE = 100 * 1024 * 1024; // 100MB
const HEAD_HASH_SIZE = 64 * 1024; // 64KB
const HASH_BATCH = 16;

const AGE_THRESHOLDS: [number, string][] = [
  [7, '0-7天'],
  [28, '1-4周'],
  [90, '1-3月'],
  [180, '3-6月'],
  [365, '6-12月'],
  [730, '1-2年'],
  [Infinity, '2年+'],
];

const SKIP_DIRS_WIN = new Set([
  'C:\\Documents and Settings',
  'C:\\System Volume Information',
  'C:\\$Recycle.Bin',
  'C:\\Windows\\CSC',
  'C:\\Windows\\Installer',
]);

const SKIP_DIRS_UNIX = new Set([
  '/proc', '/sys', '/dev', '/run', '/snap',
]);

const JUNK_PATHS_WIN: (() => string)[] = [
  () => process.env.TEMP || '',
  () => process.env.TMP || '',
  () => 'C:\\Windows\\Temp',
  () => 'C:\\Windows\\Prefetch',
  () => 'C:\\Windows\\SoftwareDistribution\\Download',
  () => path.join(os.homedir(), 'AppData', 'Local', 'Temp'),
];

const JUNK_PATHS_LINUX: (() => string)[] = [
  () => '/tmp',
  () => '/var/tmp',
  () => '/var/cache',
  () => '/var/log',
  () => path.join(os.homedir(), '.cache'),
  () => path.join(os.homedir(), '.local', 'share', 'Trash'),
  () => path.join(os.homedir(), '.thumbnails'),
];

const JUNK_PATHS_MAC: (() => string)[] = [
  () => '/tmp',
  () => '/private/var/tmp',
  () => path.join(os.homedir(), 'Library', 'Caches'),
  () => path.join(os.homedir(), '.Trash'),
  () => path.join(os.homedir(), 'Library', 'Logs'),
];

// ---- Helpers ----

function classifyAge(mtime: number): string {
  const days = Math.max(0, Math.floor((Date.now() / 1000 - mtime) / 86400));
  for (const [threshold, label] of AGE_THRESHOLDS) {
    if (days < threshold) return label;
  }
  return '2年+';
}

async function hashFileHead(filePath: string): Promise<string | null> {
  try {
    const fd = await fs.promises.open(filePath, 'r');
    try {
      const buf = Buffer.alloc(HEAD_HASH_SIZE);
      const { bytesRead } = await fd.read(buf, 0, HEAD_HASH_SIZE, 0);
      return crypto.createHash('sha256').update(buf.subarray(0, bytesRead)).digest('hex');
    } finally {
      await fd.close();
    }
  } catch {
    return null;
  }
}

async function hashFileFull(filePath: string): Promise<string | null> {
  try {
    const hash = crypto.createHash('sha256');
    const stream = fs.createReadStream(filePath, { highWaterMark: 1024 * 1024 });
    for await (const chunk of stream) {
      hash.update(chunk);
    }
    return hash.digest('hex');
  } catch {
    return null;
  }
}

export interface ScannerOptions {
  excludeDirs?: string[];
  customJunkDirs?: string[];
  topN?: number;
  enableDupDetection?: boolean;
}

// ---- Scanner class ----

export class Scanner {
  private aborted = false;
  private totalUsed = 0;
  private scannedItems = 0;
  private lastProgressTime = 0;
  private startTime = 0;

  // Top-N heaps (min-heaps via array)
  private dirHeap: [number, string][] = [];
  private fileHeap: [number, string, number][] = [];

  // Stats
  private extStats: Record<string, number> = {};
  private junkStats: Record<string, number> = {};
  private ageGroups: Record<string, number> = {};
  private dirSizeCache: Record<string, number> = {};

  // Duplicate detection
  private sizeGroups: Record<number, string[]> = {};

  private junkPathSet: Set<string> = new Set();
  private excludeDirSet: Set<string> = new Set();
  private topN: number;
  private enableDupDetection: boolean;

  constructor(
    private targetPath: string,
    private onProgress?: (p: ScanProgress) => void,
    options: ScannerOptions = {},
  ) {
    // Increase thread pool for concurrent fs I/O
    process.env.UV_THREADPOOL_SIZE = String(Math.max(64, parseInt(process.env.UV_THREADPOOL_SIZE || '0') || 0));

    this.topN = options.topN ?? TOP_N;
    this.excludeDirSet = new Set((options.excludeDirs || []).map((d) => path.resolve(d)));

    for (const [, label] of AGE_THRESHOLDS) {
      this.ageGroups[label] = 0;
    }

    // Platform-aware junk paths
    const platform = process.platform;
    const junkFns = platform === 'win32' ? JUNK_PATHS_WIN : platform === 'darwin' ? JUNK_PATHS_MAC : JUNK_PATHS_LINUX;
    for (const fn of junkFns) {
      const p = fn();
      if (p && fs.existsSync(p)) {
        this.junkPathSet.add(path.resolve(p));
      }
    }

    // User-defined custom junk dirs
    for (const d of options.customJunkDirs || []) {
      const resolved = path.resolve(d);
      if (fs.existsSync(resolved)) {
        this.junkPathSet.add(resolved);
      }
    }

    this.enableDupDetection = options.enableDupDetection ?? false;
  }

  abort(): void {
    this.aborted = true;
  }

  async scan(): Promise<ScanResult> {
    this.startTime = Date.now();
    await this.scanDir(this.targetPath, 0);

    // Detect duplicates if enabled
    let duplicates: [number, string[]][] = [];
    if (this.enableDupDetection && !this.aborted) {
      duplicates = await this.detectDuplicates();
    }

    const elapsed = (Date.now() - this.startTime) / 1000;

    this.dirHeap.sort((a, b) => b[0] - a[0]);
    this.fileHeap.sort((a, b) => b[0] - a[0]);

    const extEntries = Object.entries(this.extStats)
      .sort((a, b) => b[1] - a[1])
      .slice(0, this.topN);

    const junkEntries: [string, number][] = Object.entries(this.junkStats)
      .filter(([, size]) => size > 0)
      .sort((a, b) => b[1] - a[1]);

    return {
      topDirs: this.dirHeap.slice(0, this.topN).map(([size, p]) => [p, size]),
      topFiles: this.fileHeap.slice(0, this.topN).map(([size, p, m]) => [p, size, m]),
      junkDirs: junkEntries,
      extStats: extEntries,
      ageGroups: { ...this.ageGroups },
      dirSizeCache: { ...this.dirSizeCache },
      duplicates,
      totalUsed: this.totalUsed,
      scanTime: elapsed,
      scannedItems: this.scannedItems,
    };
  }

  private async detectDuplicates(): Promise<[number, string[]][]> {
    // Only consider size groups with 2+ files
    const candidates = Object.entries(this.sizeGroups)
      .filter(([, paths]) => paths.length >= 2)
      .map(([size, paths]) => [Number(size), paths] as [number, string[]]);

    if (candidates.length === 0) return [];

    // Phase 1: Head hash
    const headHashMap: Map<string, [number, string][]> = new Map();
    for (const [size, paths] of candidates) {
      if (this.aborted) break;
      for (let i = 0; i < paths.length; i += HASH_BATCH) {
        const batch = paths.slice(i, i + HASH_BATCH);
        const hashes = await Promise.all(batch.map((p) => hashFileHead(p)));
        for (let j = 0; j < hashes.length; j++) {
          const h = hashes[j];
          if (!h) continue;
          const key = `${size}:${h}`;
          if (!headHashMap.has(key)) headHashMap.set(key, []);
          headHashMap.get(key)!.push([size, batch[j]]);
        }
      }
    }

    // Phase 2: Full hash for head-hash matches
    const dupGroups: [number, string[]][] = [];
    for (const [, entries] of headHashMap) {
      if (this.aborted) break;
      if (entries.length < 2) continue;

      const fullHashMap: Map<string, string[]> = new Map();
      for (let i = 0; i < entries.length; i += HASH_BATCH) {
        const batch = entries.slice(i, i + HASH_BATCH);
        const hashes = await Promise.all(batch.map(([, p]) => hashFileFull(p)));
        for (let j = 0; j < hashes.length; j++) {
          const h = hashes[j];
          if (!h) continue;
          if (!fullHashMap.has(h)) fullHashMap.set(h, []);
          fullHashMap.get(h)!.push(batch[j][1]);
        }
      }

      for (const [, paths] of fullHashMap) {
        if (paths.length >= 2) {
          dupGroups.push([entries[0][0], paths]);
        }
      }
    }

    // Sort by wasted space descending
    dupGroups.sort((a, b) => b[0] * (b[1].length - 1) - a[0] * (a[1].length - 1));
    return dupGroups;
  }

  private async scanDir(dirPath: string, depth: number): Promise<number> {
    if (this.aborted || depth > MAX_DEPTH) return 0;
    if (this.shouldExclude(dirPath)) return 0;

    // Report progress (throttled)
    const now = Date.now();
    if (now - this.lastProgressTime > PROGRESS_INTERVAL_MS) {
      this.lastProgressTime = now;
      this.onProgress?.({
        currentPath: dirPath,
        scannedItems: this.scannedItems,
        scannedSize: this.totalUsed,
        elapsed: (now - this.startTime) / 1000,
      });
    }

    let totalSize = 0;

    let entries: fs.Dirent[];
    try {
      entries = await fs.promises.readdir(dirPath, { withFileTypes: true });
    } catch {
      return 0;
    }

    // Separate files from dirs
    const fileNames: string[] = [];
    const filePaths: string[] = [];
    const subDirs: string[] = [];

    for (const entry of entries) {
      if (this.aborted) break;
      if (entry.isSymbolicLink()) continue;
      if (entry.isFile()) {
        fileNames.push(entry.name);
        filePaths.push(path.join(dirPath, entry.name));
      } else if (entry.isDirectory()) {
        subDirs.push(path.join(dirPath, entry.name));
      }
    }

    // Batch stat calls in chunks — parallel I/O within each chunk
    for (let start = 0; start < filePaths.length; start += STAT_BATCH) {
      if (this.aborted) break;
      const end = Math.min(start + STAT_BATCH, filePaths.length);
      const batch = filePaths.slice(start, end);
      const stats = await Promise.all(
        batch.map((fp) => fs.promises.stat(fp).catch(() => null)),
      );

      for (let i = 0; i < stats.length; i++) {
        const stat = stats[i];
        if (!stat) continue;

        const fullPath = batch[i];
        const size = stat.size;
        const mtime = stat.mtimeMs / 1000;

        totalSize += size;
        this.totalUsed += size;
        this.scannedItems++;

        // Track large files for duplicate detection
        if (this.enableDupDetection && size >= DUP_MIN_SIZE) {
          if (!this.sizeGroups[size]) this.sizeGroups[size] = [];
          this.sizeGroups[size].push(fullPath);
        }

        const ext = path.extname(fileNames[start + i]).toLowerCase();
        if (ext) {
          this.extStats[ext] = (this.extStats[ext] || 0) + size;
        }

        for (const jp of this.junkPathSet) {
          if (fullPath.startsWith(jp)) {
            this.junkStats[jp] = (this.junkStats[jp] || 0) + size;
            break;
          }
        }

        this.addToHeap(this.fileHeap, [size, fullPath, mtime], this.topN);

        const ageLabel = classifyAge(mtime);
        this.ageGroups[ageLabel] = (this.ageGroups[ageLabel] || 0) + 1;
      }
    }

    // Scan all subdirectories concurrently
    if (subDirs.length > 0 && !this.aborted) {
      const childSizes = await Promise.all(
        subDirs.map((d) => this.scanDir(d, depth + 1)),
      );
      for (const s of childSizes) {
        totalSize += s;
      }
    }

    // Record dir size
    const normPath = path.resolve(dirPath);
    this.dirSizeCache[normPath] = totalSize;
    if (totalSize > 0) {
      this.addToHeap(this.dirHeap, [totalSize, dirPath], this.topN);
    }

    return totalSize;
  }

  private shouldExclude(dirPath: string): boolean {
    const norm = path.resolve(dirPath);
    const skipDirs = process.platform === 'win32' ? SKIP_DIRS_WIN : SKIP_DIRS_UNIX;
    if (skipDirs.has(norm)) return true;
    for (const ex of this.excludeDirSet) {
      if (norm.startsWith(ex)) return true;
    }
    return false;
  }

  private addToHeap<T extends [number, ...unknown[]]>(
    heap: T[],
    item: T,
    maxSize: number,
  ): void {
    if (heap.length < maxSize) {
      heap.push(item);
    } else if (item[0] > heap[0][0]) {
      heap[0] = item;
    }
    if (heap.length > 1) {
      let i = 0;
      while (true) {
        let smallest = i;
        const left = 2 * i + 1;
        const right = 2 * i + 2;
        if (left < heap.length && heap[left][0] < heap[smallest][0]) smallest = left;
        if (right < heap.length && heap[right][0] < heap[smallest][0]) smallest = right;
        if (smallest === i) break;
        [heap[i], heap[smallest]] = [heap[smallest], heap[i]];
        i = smallest;
      }
    }
  }
}

// ---- Standalone test ----

if (require.main === module) {
  const target = process.argv[2] || '.';
  console.log(`Scanning: ${path.resolve(target)}`);
  const scanner = new Scanner(target, (p) => {
    process.stdout.write(`\r${p.scannedItems} files, ${p.currentPath.slice(0, 60).padEnd(60)}`);
  }, { enableDupDetection: true });
  scanner.scan().then((result) => {
    console.log('\n');
    console.log(`Total used: ${formatSize(result.totalUsed)}`);
    console.log(`Files scanned: ${result.scannedItems}`);
    console.log(`Scan time: ${result.scanTime.toFixed(2)}s`);
    if (result.duplicates.length > 0) {
      console.log(`\nDuplicate groups: ${result.duplicates.length}`);
      for (const [size, paths] of result.duplicates.slice(0, 5)) {
        console.log(`  ${formatSize(size)} × ${paths.length} copies`);
      }
    }
  });
}

function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let b = bytes;
  for (const unit of units) {
    if (b < 1024) return `${b.toFixed(2)} ${unit}`;
    b /= 1024;
  }
  return `${b.toFixed(2)} PB`;
}
