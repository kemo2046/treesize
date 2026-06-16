import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

// ---- Types ----

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

// ---- Constants ----

const MAX_DEPTH = 30;
const TOP_N = 15;
const PROGRESS_INTERVAL_MS = 150;

const AGE_THRESHOLDS: [number, string][] = [
  [7, '0-7天'],
  [28, '1-4周'],
  [90, '1-3月'],
  [180, '3-6月'],
  [365, '6-12月'],
  [730, '1-2年'],
  [Infinity, '2年+'],
];

const SKIP_DIRS = new Set([
  'C:\\Documents and Settings',
  'C:\\System Volume Information',
  'C:\\$Recycle.Bin',
  'C:\\Windows\\CSC',
  'C:\\Windows\\Installer',
]);

const JUNK_PATHS_WIN = [
  () => process.env.TEMP || '',
  () => process.env.TMP || '',
  () => 'C:\\Windows\\Temp',
  () => 'C:\\Windows\\Prefetch',
  () => 'C:\\Windows\\SoftwareDistribution\\Download',
  () => path.join(os.homedir(), 'AppData', 'Local', 'Temp'),
];

// ---- Helpers ----

function classifyAge(mtime: number): string {
  const days = Math.max(0, Math.floor((Date.now() / 1000 - mtime) / 86400));
  for (const [threshold, label] of AGE_THRESHOLDS) {
    if (days < threshold) return label;
  }
  return '2年+';
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

  private junkPaths: string[] = [];
  private excludeDirs: string[] = [];

  constructor(
    private targetPath: string,
    private onProgress?: (p: ScanProgress) => void,
  ) {
    // Init age groups
    for (const [, label] of AGE_THRESHOLDS) {
      this.ageGroups[label] = 0;
    }
    // Init junk paths (Windows)
    for (const fn of JUNK_PATHS_WIN) {
      const p = fn();
      if (p && fs.existsSync(p)) {
        this.junkPaths.push(path.resolve(p));
      }
    }
  }

  abort(): void {
    this.aborted = true;
  }

  async scan(): Promise<ScanResult> {
    this.startTime = Date.now();
    await this.scanDir(this.targetPath, 0);

    const elapsed = (Date.now() - this.startTime) / 1000;

    // Sort heaps descending
    this.dirHeap.sort((a, b) => b[0] - a[0]);
    this.fileHeap.sort((a, b) => b[0] - a[0]);

    // Sort ext stats
    const extEntries = Object.entries(this.extStats)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 15);

    // Sort junk dirs
    const junkEntries: [string, number][] = Object.entries(this.junkStats)
      .filter(([, size]) => size > 0)
      .sort((a, b) => b[1] - a[1]);

    return {
      topDirs: this.dirHeap.slice(0, TOP_N).map(([size, p]) => [p, size]),
      topFiles: this.fileHeap.slice(0, TOP_N).map(([size, p, m]) => [p, size, m]),
      junkDirs: junkEntries,
      extStats: extEntries,
      ageGroups: { ...this.ageGroups },
      dirSizeCache: { ...this.dirSizeCache },
      totalUsed: this.totalUsed,
      scanTime: elapsed,
      scannedItems: this.scannedItems,
    };
  }

  private async scanDir(dirPath: string, depth: number): Promise<number> {
    if (this.aborted || depth > MAX_DEPTH) return 0;
    if (this.shouldExclude(dirPath)) return 0;

    // Report progress
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

    for (const entry of entries) {
      if (this.aborted) break;

      const fullPath = path.join(dirPath, entry.name);

      try {
        // Skip symlinks
        if (entry.isSymbolicLink()) continue;

        if (entry.isFile()) {
          let size = 0;
          let mtime = 0;
          try {
            const stat = await fs.promises.stat(fullPath);
            size = stat.size;
            mtime = stat.mtimeMs / 1000;
          } catch {
            continue;
          }

          totalSize += size;
          this.totalUsed += size;
          this.scannedItems++;

          // Extension stats
          const ext = path.extname(entry.name).toLowerCase();
          if (ext) {
            this.extStats[ext] = (this.extStats[ext] || 0) + size;
          }

          // Junk stats
          for (const jp of this.junkPaths) {
            if (fullPath.startsWith(jp)) {
              this.junkStats[jp] = (this.junkStats[jp] || 0) + size;
              break;
            }
          }

          // Top files heap
          this.addToHeap(this.fileHeap, [size, fullPath, mtime], TOP_N);

          // Age group
          const ageLabel = classifyAge(mtime);
          this.ageGroups[ageLabel] = (this.ageGroups[ageLabel] || 0) + 1;

        } else if (entry.isDirectory()) {
          const subSize = await this.scanDir(fullPath, depth + 1);
          totalSize += subSize;
        }
      } catch {
        // Skip inaccessible entries
      }
    }

    // Record dir size
    const normPath = path.resolve(dirPath);
    this.dirSizeCache[normPath] = totalSize;
    if (totalSize > 0) {
      this.addToHeap(this.dirHeap, [totalSize, dirPath], TOP_N);
    }

    return totalSize;
  }

  private shouldExclude(dirPath: string): boolean {
    const norm = path.resolve(dirPath);
    if (SKIP_DIRS.has(norm)) return true;
    return this.excludeDirs.some((ex) => norm.startsWith(ex));
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
    // Keep heap property (min at top) — we use sort later, so just maintain partial order
    if (heap.length > 1) {
      // Bubble down the new root to keep min-heap
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
  });
  scanner.scan().then((result) => {
    console.log('\n');
    console.log(`Total used: ${formatSize(result.totalUsed)}`);
    console.log(`Files scanned: ${result.scannedItems}`);
    console.log(`Scan time: ${result.scanTime.toFixed(2)}s`);
    console.log('\nTop directories:');
    for (const [dir, size] of result.topDirs) {
      console.log(`  ${formatSize(size).padStart(12)}  ${dir}`);
    }
    console.log('\nTop files:');
    for (const [file, size] of result.topFiles) {
      console.log(`  ${formatSize(size).padStart(12)}  ${file}`);
    }
    console.log('\nExtension stats:');
    for (const [ext, size] of result.extStats) {
      console.log(`  ${formatSize(size).padStart(12)}  ${ext}`);
    }
    console.log('\nAge groups:');
    for (const [label, count] of Object.entries(result.ageGroups)) {
      if (count > 0) console.log(`  ${label}: ${count} files`);
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
