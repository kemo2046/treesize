import * as fs from 'fs';
import * as path from 'path';
import { app } from 'electron';
import { AppConfig, HistoryEntry } from '../shared/types';

const APP_DIR = path.join(app.getPath('home'), '.disk_analyzer');
const CONFIG_FILE = path.join(APP_DIR, 'config.json');
const HISTORY_FILE = path.join(APP_DIR, 'history.json');
const GEOMETRY_FILE = path.join(APP_DIR, 'geometry.json');

const DEFAULT_CONFIG: AppConfig = {
  excludeDirs: [],
  customJunkDirs: [],
  lastScanPath: '',
  topN: 15,
  llmApiUrl: '',
  llmApiKey: '',
  llmModel: '',
  llmTemperature: 0.3,
  duplicateDetection: true,
  simulateMode: false,
  theme: 'light',
};

export class ConfigManager {
  private config: AppConfig;
  private history: HistoryEntry[] = [];
  private geometry: Record<string, unknown> = {};

  constructor() {
    try {
      this.ensureDir();
      this.config = this.loadConfig();
      this.history = this.loadHistory();
      this.geometry = this.loadGeometry();
    } catch (e) {
      console.error('ConfigManager init failed, using defaults:', e);
      this.config = { ...DEFAULT_CONFIG };
      this.history = [];
      this.geometry = {};
    }
  }

  private ensureDir(): void {
    if (!fs.existsSync(APP_DIR)) {
      fs.mkdirSync(APP_DIR, { recursive: true });
    }
  }

  // --- Config ---

  private loadConfig(): AppConfig {
    try {
      if (fs.existsSync(CONFIG_FILE)) {
        const data = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
        return { ...DEFAULT_CONFIG, ...data };
      }
    } catch {
      // Ignore corrupt config
    }
    return { ...DEFAULT_CONFIG };
  }

  saveConfig(): void {
    try {
      fs.writeFileSync(CONFIG_FILE, JSON.stringify(this.config, null, 2), 'utf-8');
    } catch (e) {
      console.error('Failed to save config:', e);
    }
  }

  get(): AppConfig {
    return this.config;
  }

  set(updates: Partial<AppConfig>): void {
    Object.assign(this.config, updates);
    this.saveConfig();
  }

  // --- History ---

  private loadHistory(): HistoryEntry[] {
    try {
      if (fs.existsSync(HISTORY_FILE)) {
        const data = JSON.parse(fs.readFileSync(HISTORY_FILE, 'utf-8'));
        return Array.isArray(data) ? data : [];
      }
    } catch {
      // Ignore corrupt history
    }
    return [];
  }

  saveHistory(): void {
    try {
      // Keep last 100 entries
      const trimmed = this.history.slice(-100);
      fs.writeFileSync(HISTORY_FILE, JSON.stringify(trimmed, null, 2), 'utf-8');
    } catch (e) {
      console.error('Failed to save history:', e);
    }
  }

  addHistoryEntry(entry: HistoryEntry): void {
    this.history.push(entry);
    this.saveHistory();
  }

  getHistory(): HistoryEntry[] {
    return this.history;
  }

  clearHistory(): void {
    this.history = [];
    this.saveHistory();
  }

  // --- Geometry ---

  private loadGeometry(): Record<string, unknown> {
    try {
      if (fs.existsSync(GEOMETRY_FILE)) {
        return JSON.parse(fs.readFileSync(GEOMETRY_FILE, 'utf-8'));
      }
    } catch {
      // Ignore
    }
    return {};
  }

  saveGeometry(geometry: Record<string, unknown>): void {
    this.geometry = geometry;
    try {
      fs.writeFileSync(GEOMETRY_FILE, JSON.stringify(geometry, null, 2), 'utf-8');
    } catch (e) {
      console.error('Failed to save geometry:', e);
    }
  }

  getGeometry(): Record<string, unknown> {
    return this.geometry;
  }
}
