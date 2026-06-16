import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

const APP_DIR = path.join(os.homedir(), '.disk_analyzer');
const CONFIG_FILE = path.join(APP_DIR, 'config.json');
const GEOMETRY_FILE = path.join(APP_DIR, 'geometry.json');

export interface AppConfig {
  excludeDirs: string[];
  customJunkDirs: string[];
  lastScanPath: string;
  topN: number;
  theme: 'light' | 'dark';
}

const DEFAULT_CONFIG: AppConfig = {
  excludeDirs: [],
  customJunkDirs: [],
  lastScanPath: '',
  topN: 15,
  theme: 'light',
};

export interface WindowGeometry {
  width: number;
  height: number;
  x?: number;
  y?: number;
}

export class ConfigManager {
  private config: AppConfig;
  private geometry: WindowGeometry | null = null;

  constructor() {
    this.config = { ...DEFAULT_CONFIG };
    try {
      fs.mkdirSync(APP_DIR, { recursive: true });
      this.load();
    } catch (e) {
      console.error('ConfigManager init failed, using defaults:', e);
    }
  }

  private load(): void {
    try {
      if (fs.existsSync(CONFIG_FILE)) {
        const data = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
        this.config = { ...DEFAULT_CONFIG, ...data };
      }
    } catch (e) {
      console.error('Failed to load config:', e);
    }

    try {
      if (fs.existsSync(GEOMETRY_FILE)) {
        this.geometry = JSON.parse(fs.readFileSync(GEOMETRY_FILE, 'utf-8'));
      }
    } catch {
      // ignore
    }
  }

  get(): AppConfig {
    return { ...this.config };
  }

  set(partial: Partial<AppConfig>): void {
    this.config = { ...this.config, ...partial };
    this.save();
  }

  getGeometry(): WindowGeometry | null {
    return this.geometry;
  }

  setGeometry(geo: WindowGeometry): void {
    this.geometry = geo;
    try {
      fs.mkdirSync(APP_DIR, { recursive: true });
      fs.writeFileSync(GEOMETRY_FILE, JSON.stringify(geo));
    } catch (e) {
      console.error('Failed to save geometry:', e);
    }
  }

  private save(): void {
    try {
      fs.mkdirSync(APP_DIR, { recursive: true });
      fs.writeFileSync(CONFIG_FILE, JSON.stringify(this.config, null, 2));
    } catch (e) {
      console.error('Failed to save config:', e);
    }
  }
}
