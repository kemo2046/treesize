#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
磁盘空间分析工具 v2 — 清理助手
依赖安装：pip install psutil send2trash

v2 改进:
- P0: 线程安全回调、iid 路径化、配置路径迁移、Explorer /select 修复
- P1: 暗色主题、两行控制栏、zebra 配色优化、目录树懒加载、文件年龄分析
- P1: 多线程扫描、max_depth 保护、Windows 长路径、扫描缓存
- 代码: functools.partial、日志文件、类型注解
"""

from __future__ import annotations

import csv
import functools
import heapq
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import defaultdict

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional, Tuple

import psutil

try:
    import xxhash
    HAS_XXHASH = True
except ImportError:
    HAS_XXHASH = False

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import send2trash

    HAS_SEND2TRASH = True
except ImportError:
    HAS_SEND2TRASH = False

# ========== 常量 ==========
DEFAULT_TOP_N: int = 15
PROGRESS_UPDATE_INTERVAL: float = 0.15
MAX_STATUS_PATH_LEN: int = 60
PATH_ELLIPSIS_LEFT: int = 28
PATH_ELLIPSIS_RIGHT: int = 29
MAX_DEPTH: int = 30
WIN_MAX_PATH: int = 260
CACHE_MAX_AGE: int = 3600
DUP_MIN_SIZE: int = 100 * 1024 * 1024  # 重复文件检测最小大小 (100MB)

APP_DIR: Path = Path.home() / ".disk_analyzer"
CONFIG_FILE: Path = APP_DIR / "config.json"
CACHE_FILE: Path = APP_DIR / "cache.json"
LOG_FILE: Path = APP_DIR / "app.log"
GEOMETRY_FILE: Path = APP_DIR / "geometry.json"

AGE_GROUP_KEYS: List[str] = [
    "0-7天",
    "1-4周",
    "1-3月",
    "3-6月",
    "6-12月",
    "1-2年",
    "2年+",
]
AGE_THRESHOLDS: List[Tuple[int, str]] = [
    (7, "0-7天"),
    (28, "1-4周"),
    (90, "1-3月"),
    (180, "3-6月"),
    (365, "6-12月"),
    (730, "1-2年"),
    (999999, "2年+"),
]


# ========== 配色方案 ==========
class LightPalette:
    # ── Header (飞书风格：白色清爽头部) ──
    HEADER_BG: str = "#FFFFFF"
    HEADER_ACCENT: str = "#F5F7FA"
    HEADER_TEXT: str = "#1F2D3D"
    HEADER_SUBTITLE: str = "#8F9BB3"
    # ── Base (飞书风格：浅灰白背景) ──
    BG: str = "#F5F7FA"
    FRAME_BG: str = "#FFFFFF"
    TEXT: str = "#1F2D3D"
    TEXT_MUTED: str = "#8F9BB3"
    PRIMARY: str = "#3370FF"
    PRIMARY_LIGHT: str = "#EBF0FF"
    BORDER: str = "#E8ECF0"
    STRIPE_ODD: str = "#FFFFFF"
    STRIPE_EVEN: str = "#F7F8FA"
    HIGHLIGHT: str = "#E1EAFF"
    DANGER: str = "#F54A45"
    SUCCESS: str = "#34C759"
    WARNING: str = "#FF9500"
    # ── Buttons (飞书风格：扁平按钮) ──
    BUTTON_BG: str = "#F0F1F5"
    BUTTON_ACTIVE: str = "#E4E6EB"
    BUTTON_DISABLED: str = "#F5F7FA"
    ACCENT_HOVER: str = "#2860E1"
    ACCENT_DISABLED: str = "#A3BFFA"
    DANGER_HOVER: str = "#D93632"
    # ── Tree ──
    TREE_HEADING_BG: str = "#FAFBFC"
    TREE_HEADING_ACTIVE: str = "#E8ECF0"
    TROUGH: str = "#E8ECF0"
    NOTEBOOK_TAB_BG: str = "#F5F7FA"
    # ── Metric cards (飞书风格) ──
    METRIC_TOTAL: str = "#3370FF"
    METRIC_USED: str = "#FF9500"
    METRIC_FREE: str = "#34C759"
    METRIC_SCAN: str = "#8B5CF6"


class DarkPalette:
    HEADER_BG: str = "#020617"
    HEADER_ACCENT: str = "#172554"
    HEADER_TEXT: str = "#F1F5F9"
    HEADER_SUBTITLE: str = "#CBD5E1"
    BG: str = "#0F172A"
    FRAME_BG: str = "#1E293B"
    TEXT: str = "#F8FAFC"
    TEXT_MUTED: str = "#F1F5F9"
    PRIMARY: str = "#60A5FA"
    PRIMARY_LIGHT: str = "#1E3A5F"
    BORDER: str = "#334155"
    STRIPE_ODD: str = "#1E293B"
    STRIPE_EVEN: str = "#172033"  # P0: 暗色条纹对比度加大
    HIGHLIGHT: str = "#1E3A5F"
    DANGER: str = "#EF4444"
    SUCCESS: str = "#22C55E"
    WARNING: str = "#FBBF24"
    BUTTON_BG: str = "#334155"
    BUTTON_ACTIVE: str = "#475569"
    BUTTON_DISABLED: str = "#1E293B"
    ACCENT_HOVER: str = "#60A5FA"
    ACCENT_DISABLED: str = "#334155"
    DANGER_HOVER: str = "#FCA5A5"
    TREE_HEADING_BG: str = "#1E293B"
    TREE_HEADING_ACTIVE: str = "#334155"
    TROUGH: str = "#334155"
    NOTEBOOK_TAB_BG: str = "#1E293B"
    METRIC_TOTAL: str = "#60A5FA"
    METRIC_USED: str = "#FBBF24"
    METRIC_FREE: str = "#4ADE80"
    METRIC_SCAN: str = "#C084FC"


Palette: type = LightPalette

# ========== 日志 ==========
APP_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
logger: logging.Logger = logging.getLogger(__name__)


# ========== 数据模型 ==========
@dataclass
class ScanResult:
    top_dirs: List[Tuple[int, str]] = field(default_factory=list)
    top_files: List[Tuple[int, str]] = field(default_factory=list)
    junk_dirs: List[Tuple[str, int]] = field(default_factory=list)
    ext_stats: List[Tuple[str, int]] = field(default_factory=list)
    age_groups: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    dir_size_cache: Dict[str, int] = field(default_factory=dict)
    duplicates: List[Tuple[int, List[str]]] = field(default_factory=list)  # (size, [paths])
    total_used: int = 0
    scan_time: float = 0.0
    scanned_items: int = 0


# ========== 工具函数 ==========
def format_size(size_in_bytes: int) -> str:
    if size_in_bytes < 0:
        return "0 B"
    size: float = float(size_in_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def is_admin() -> bool:
    if sys.platform == "win32":
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def open_file_or_dir(path: str, select_file: bool = False) -> None:
    if not os.path.exists(path):
        logger.warning("路径不存在: %s", path)
        return
    try:
        if sys.platform == "win32":
            if select_file and os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", path], shell=False)
            else:
                subprocess.Popen(["explorer", path], shell=False)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        logger.error("打开路径失败 %s: %s", path, e)


def long_path_prefix(path: str) -> str:
    if sys.platform == "win32" and len(path) > WIN_MAX_PATH and not path.startswith("\\\\?\\"):
        return "\\\\?\\" + path
    return path


def get_system_junk_paths() -> Dict[str, int]:
    junk: Dict[str, int] = {}
    home: Path = Path.home()

    if sys.platform == "win32":
        candidates: List[Optional[Path]] = [
            Path(os.environ.get("TEMP", "")),
            Path(os.environ.get("TMP", "")),
            Path(r"C:\Windows\Temp"),
            Path(r"C:\Windows\Prefetch"),
            Path(r"C:\Windows\SoftwareDistribution\Download"),
            Path(r"C:\$Recycle.Bin"),
            home / "Downloads",
            home / "AppData/Local/Temp",
        ]
    elif sys.platform == "darwin":
        candidates = [
            home / ".Trash",
            home / "Library/Caches",
            home / "Library/Logs",
            Path("/tmp"),
            Path("/var/tmp"),
        ]
    else:
        candidates = [
            home / ".cache",
            home / ".local/share/Trash",
            Path("/tmp"),
            Path("/var/tmp"),
        ]

    for p in candidates:
        if p and p.exists():
            junk[str(p)] = 0
    return junk


# ========== 配置管理 ==========
class Config:
    def __init__(self) -> None:
        self.exclude_dirs: List[str] = []
        self.custom_junk_dirs: List[str] = []
        self.last_scan_path: str = ""
        self.top_n: int = DEFAULT_TOP_N
        # LLM 配置
        self.llm_api_url: str = ""
        self.llm_api_key: str = ""
        self.llm_model: str = ""
        self.llm_temperature: float = 0.3
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self.load()

    def load(self) -> None:
        if not CONFIG_FILE.exists():
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data: Dict[str, Any] = json.load(f)
                self.exclude_dirs = data.get("exclude_dirs", [])
                self.custom_junk_dirs = data.get("custom_junk_dirs", [])
                self.last_scan_path = data.get("last_scan_path", "")
                self.top_n = data.get("top_n", DEFAULT_TOP_N)
                self.llm_api_url = data.get("llm_api_url", "")
                self.llm_api_key = data.get("llm_api_key", "")
                self.llm_model = data.get("llm_model", "")
                self.llm_temperature = data.get("llm_temperature", 0.3)
        except Exception as e:
            logger.error("加载配置文件失败: %s", e)

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        try:
            data: Dict[str, Any] = {
                "exclude_dirs": self.exclude_dirs,
                "custom_junk_dirs": self.custom_junk_dirs,
                "last_scan_path": self.last_scan_path,
                "top_n": self.top_n,
                "llm_api_url": self.llm_api_url,
                "llm_api_key": self.llm_api_key,
                "llm_model": self.llm_model,
                "llm_temperature": self.llm_temperature,
            }
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("保存配置文件失败: %s", e)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_url and self.llm_model)


# ========== LLM 分析器 ==========
class LLMAnalyzer:
    """调用 OpenAI 兼容 API 对扫描结果进行智能分析，支持流式输出"""

    SYSTEM_PROMPT: str = (
        "你是一名专业的磁盘空间分析顾问。用户会给你一份磁盘扫描报告，"
        "你需要分析哪些目录和文件占用了大量空间，判断它们是什么、为什么大、"
        "是否安全清理，并给出具体的清理建议。\n\n"
        "请按以下结构输出：\n"
        "## 📊 空间概览\n"
        "## 🔍 大目录分析\n"
        "## 📄 大文件分析\n"
        "## 🗑️ 清理建议\n"
        "## ⚠️ 注意事项\n\n"
        "注意：\n"
        "- 按浪费空间从大到小排序\n"
        "- 对每个目录/文件给出：是什么、为什么大、能否清理、风险等级\n"
        "- 用中文回答，简洁实用\n"
        "- 如果有重复文件，重点指出"
    )

    def __init__(self, config: Config) -> None:
        self.config: Config = config
        self._cancel_event: threading.Event = threading.Event()

    def build_prompt(self, result: ScanResult) -> str:
        """将 ScanResult 格式化为 LLM prompt"""
        lines: List[str] = [
            f"扫描路径: {self.config.last_scan_path}",
            f"扫描耗时: {result.scan_time:.1f}s | 总计: {format_size(result.total_used)} | 文件数: {result.scanned_items}",
            "",
        ]

        # 大目录
        if result.top_dirs:
            lines.append("## 占用最大的目录（含子目录）:")
            for i, (size, path) in enumerate(result.top_dirs[:15], 1):
                lines.append(f"  {i}. {format_size(size)}  {path}")
            lines.append("")

        # 大文件
        if result.top_files:
            lines.append("## 占用最大的文件:")
            for i, (size, path) in enumerate(result.top_files[:15], 1):
                try:
                    mtime = datetime.fromtimestamp(
                        os.path.getmtime(path)
                    ).strftime("%Y-%m-%d")
                except Exception:
                    mtime = "未知"
                lines.append(f"  {i}. {format_size(size)}  [{mtime}]  {path}")
            lines.append("")

        # 文件类型统计
        if result.ext_stats:
            lines.append("## 文件类型统计 (按大小):")
            for ext, size in result.ext_stats[:10]:
                lines.append(f"  {ext or '(无后缀)'}: {format_size(size)}")
            lines.append("")

        # 建议清理目录
        if result.junk_dirs:
            lines.append("## 系统识别的可清理目录:")
            for path, size in result.junk_dirs:
                lines.append(f"  {format_size(size)}  {path}")
            lines.append("")

        # 文件年龄分布
        if result.age_groups:
            lines.append("## 文件年龄分布:")
            for label in AGE_GROUP_KEYS:
                info = result.age_groups.get(label, (0, 0))
                if isinstance(info, (list, tuple)) and info[0] > 0:
                    lines.append(f"  {label}: {info[0]} 个文件, {format_size(info[1])}")
            lines.append("")

        # 重复文件
        if result.duplicates:
            lines.append("## 重复文件:")
            total_waste = sum(s * (len(p) - 1) for s, p in result.duplicates)
            lines.append(f"  共 {len(result.duplicates)} 组, 浪费 {format_size(total_waste)}")
            for size, paths in result.duplicates[:5]:
                lines.append(f"  - {format_size(size)} x {len(paths)} 份:")
                for p in paths[:3]:
                    lines.append(f"      {p}")
                if len(paths) > 3:
                    lines.append(f"      ... 还有 {len(paths) - 3} 个")
            lines.append("")

        lines.append("请根据以上数据进行分析，给出清理建议。")
        return "\n".join(lines)

    def analyze(
        self,
        result: ScanResult,
        on_token: Callable[[str], None],
        on_done: Callable[[Optional[str], Optional[str]], None],
    ) -> None:
        """在后台线程中发起流式 LLM 请求

        Args:
            result: 扫描结果
            on_token: 每收到一个 token 时回调（主线程调度）
            on_done: 完成时回调 (full_text, error)
        """
        self._cancel_event.clear()

        if not HAS_REQUESTS:
            on_done(None, "缺少 requests 库，请执行: pip install requests")
            return

        if not self.config.llm_configured:
            on_done(None, "请先在「配置」中设置 LLM 的 API 地址和模型名称")
            return

        prompt = self.build_prompt(result)

        def _worker() -> None:
            try:
                url = self.config.llm_api_url.rstrip("/")
                if not url.endswith("/chat/completions"):
                    url += "/chat/completions"

                headers: Dict[str, str] = {
                    "Content-Type": "application/json",
                }
                if self.config.llm_api_key:
                    headers["Authorization"] = f"Bearer {self.config.llm_api_key}"

                payload: Dict[str, Any] = {
                    "model": self.config.llm_model,
                    "messages": [
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": self.config.llm_temperature,
                    "stream": True,
                }

                logger.info("LLM 请求: %s model=%s", url, self.config.llm_model)

                resp = _requests.post(
                    url, json=payload, headers=headers, stream=True, timeout=120
                )
                resp.raise_for_status()

                full_text = ""
                for line in resp.iter_lines(decode_unicode=True):
                    if self._cancel_event.is_set():
                        break
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            full_text += content
                            on_token(content)
                    except (json.JSONDecodeError, IndexError, KeyError):
                        continue

                if self._cancel_event.is_set():
                    on_done(full_text or None, "已取消")
                else:
                    on_done(full_text, None)

            except _requests.exceptions.ConnectionError:
                on_done(None, f"连接失败: {self.config.llm_api_url}\n请检查 API 地址是否正确")
            except _requests.exceptions.Timeout:
                on_done(None, "请求超时（120s），请稍后重试")
            except _requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response else "?"
                body = ""
                try:
                    body = e.response.text[:500] if e.response else ""
                except Exception:
                    pass
                on_done(None, f"HTTP {status}: {body}")
            except Exception as e:
                on_done(None, f"未知错误: {e}")
                logger.error("LLM 分析失败: %s", e)

        threading.Thread(target=_worker, daemon=True).start()

    def cancel(self) -> None:
        self._cancel_event.set()


# ========== 扫描核心 ==========
class FastScanner:
    SKIP_DIRS: set = {
        r"C:\Documents and Settings",
        r"C:\System Volume Information",
        r"C:\$Recycle.Bin",
        r"C:\Windows\CSC",
        r"C:\ProgramData\Microsoft\Windows\Start Menu\程序",
        r"C:\ProgramData\Microsoft\Windows\SystemData",
        r"C:\Windows\Installer",
    }

    def __init__(
        self,
        target_path: str,
        top_n: int = DEFAULT_TOP_N,
        exclude_dirs: Optional[List[str]] = None,
        custom_junk_dirs: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[str, int], None]] = None,
        finish_callback: Optional[Callable[[Optional[ScanResult], Optional[str]], None]] = None,
        max_depth: int = MAX_DEPTH,
        enable_dup_detection: bool = False,
    ) -> None:
        self.target_path: str = os.path.abspath(target_path)
        self.top_n: int = top_n
        self.exclude_dirs: set = set(exclude_dirs or [])
        self.max_depth: int = max_depth
        self.enable_dup_detection: bool = enable_dup_detection
        self.junk_paths: Dict[str, int] = get_system_junk_paths()
        for d in custom_junk_dirs or []:
            if d and os.path.exists(d):
                self.junk_paths[os.path.abspath(d)] = 0
        self.progress_callback = progress_callback
        self.finish_callback = finish_callback

        self._lock: threading.Lock = threading.Lock()
        self._stop_event: threading.Event = threading.Event()
        self._file_heap: List[Tuple[int, str]] = []
        self._dir_heap: List[Tuple[int, str]] = []
        self._ext_stats: Dict[str, int] = defaultdict(int)
        self._junk_stats: Dict[str, int] = defaultdict(int)
        self._age_groups: Dict[str, Tuple[int, int]] = {k: (0, 0) for k in AGE_GROUP_KEYS}
        self._dir_size_cache: Dict[str, int] = {}
        self._size_groups: Dict[int, List[str]] = defaultdict(list)
        self._total_used: int = 0
        self._scanned_items: int = 0
        self._last_callback_time: float = time.time()

    def _should_exclude(self, path: str) -> bool:
        return any(path.startswith(excl) for excl in self.exclude_dirs)

    def _add_to_heap(
        self, heap: List[Tuple[int, str]], size: int, path: str, max_size: int
    ) -> None:
        if len(heap) < max_size:
            heapq.heappush(heap, (size, path))
        elif size > heap[0][0]:
            heapq.heapreplace(heap, (size, path))

    def _classify_age(self, mtime: float) -> str:
        days: int = max(0, int((time.time() - mtime) / 86400))
        for threshold, label in AGE_THRESHOLDS:
            if days < threshold:
                return label
        return "2年+"

    def _scan_dir(self, path: str, depth: int = 0) -> int:
        if self._stop_event.is_set() or depth > self.max_depth:
            return 0
        if self._should_exclude(path) or path in self.SKIP_DIRS:
            return 0

        now: float = time.time()
        if now - self._last_callback_time >= PROGRESS_UPDATE_INTERVAL:
            self._last_callback_time = now
            if self.progress_callback:
                if len(path) <= MAX_STATUS_PATH_LEN:
                    short: str = path
                else:
                    short = path[:PATH_ELLIPSIS_LEFT] + "..." + path[-PATH_ELLIPSIS_RIGHT:]
                self.progress_callback(f"正在扫描: {short}", -1)

        total_size: int = 0
        try:
            with os.scandir(long_path_prefix(path)) as it:
                for entry in it:
                    if self._stop_event.is_set():
                        break
                    try:
                        if entry.is_symlink():
                            continue
                        if hasattr(entry, "is_junction") and entry.is_junction():
                            continue
                        if entry.is_file(follow_symlinks=False):
                            try:
                                st = entry.stat(follow_symlinks=False)
                            except OSError:
                                continue
                            size: int = st.st_size
                            total_size += size
                            with self._lock:
                                self._total_used += size
                                self._scanned_items += 1
                                if self.enable_dup_detection and size >= DUP_MIN_SIZE:
                                    self._size_groups[size].append(entry.path)
                                ext: str = os.path.splitext(entry.name)[1].lower()
                                if ext:
                                    self._ext_stats[ext] += size
                                for junk_path in self.junk_paths:
                                    if entry.path.startswith(junk_path):
                                        self._junk_stats[junk_path] += size
                                        break
                                self._add_to_heap(self._file_heap, size, entry.path, self.top_n)
                                age_label: str = self._classify_age(st.st_mtime)
                                old_ag = self._age_groups[age_label]
                                self._age_groups[age_label] = (old_ag[0] + 1, old_ag[1] + size)
                        elif entry.is_dir(follow_symlinks=False):
                            total_size += self._scan_dir(entry.path, depth + 1)
                    except OSError as e:
                        logger.debug("扫描条目失败 %s: %s", entry.path, e)
        except OSError as e:
            logger.debug("打开目录失败 %s: %s", path, e)

        norm_path = os.path.normcase(os.path.normpath(path))
        with self._lock:
            self._dir_size_cache[norm_path] = total_size
            if total_size > 0:
                self._add_to_heap(self._dir_heap, total_size, path, self.top_n)
        return total_size

    def scan(self) -> None:
        if not os.path.exists(self.target_path):
            if self.finish_callback:
                self.finish_callback(None, f"路径不存在: {self.target_path}")
            return

        start: float = time.time()
        if self.progress_callback:
            self.progress_callback("准备扫描...", -1)

        # 单线程递归扫描（稳定可靠，与原版 v1 一致）
        total: int = self._scan_dir(self.target_path)

        if self._stop_event.is_set():
            if self.finish_callback:
                self.finish_callback(None, "扫描已停止")
            return

        # P3: 重复文件检测（基于 xxhash）
        duplicates = self._find_duplicates() if self.enable_dup_detection else []

        elapsed: float = time.time() - start
        result: ScanResult = ScanResult(
            top_dirs=sorted(self._dir_heap, key=lambda x: x[0], reverse=True),
            top_files=sorted(self._file_heap, key=lambda x: x[0], reverse=True),
            junk_dirs=sorted(
                [(p, s) for p, s in self._junk_stats.items() if s > 0],
                key=lambda x: x[1],
                reverse=True,
            ),
            ext_stats=sorted(self._ext_stats.items(), key=lambda x: x[1], reverse=True)[:15],
            age_groups=dict(self._age_groups),
            dir_size_cache=dict(self._dir_size_cache),
            duplicates=duplicates,
            total_used=self._total_used,
            scan_time=elapsed,
            scanned_items=self._scanned_items,
        )
        if self.finish_callback:
            self.finish_callback(result, None)

    def stop(self) -> None:
        self._stop_event.set()

    def _find_duplicates(self) -> List[Tuple[int, List[str]]]:
        """P3: 基于 xxhash 的两阶段深度重复文件检测
        
        策略：
        1. 使用扫描过程中收集到的 self._size_groups 进行分组（只包含 >= 100MB 的文件）。
        2. 第一阶段 (快速过滤)：只对有多个文件的大小分组计算前 64KB 的 xxhash（多线程并发计算）。
        3. 第二阶段 (深度一致性校验)：对第一阶段初步判定的潜在重复组，并发计算完整文件哈希，确保 100% 精准无误。
        
        返回: [(size, [path1, path2, ...]), ...] 按总浪费空间降序
        """
        if not HAS_XXHASH:
            logger.info("xxhash 未安装，跳过重复文件检测")
            return []

        import concurrent.futures

        # 1. 过滤出大小相同且数量 >= 2 的文件分组
        size_groups_to_hash = {
            size: paths for size, paths in self._size_groups.items() if len(paths) >= 2
        }

        if not size_groups_to_hash:
            return []

        # 收集所有需要计算头部 hash 的文件路径
        all_paths_to_hash = []
        for paths in size_groups_to_hash.values():
            all_paths_to_hash.extend(paths)

        total_files_to_hash = len(all_paths_to_hash)
        if total_files_to_hash == 0:
            return []

        # 2. 第一阶段：多线程并发计算 64KB 头部 xxhash
        hash_chunk_size = 65536  # 64KB
        file_hashes = {}
        hashed_count = 0

        def _hash_single_file(fpath: str) -> Tuple[str, Optional[str]]:
            if self._stop_event.is_set():
                return fpath, None
            try:
                h = xxhash.xxh64()
                with open(long_path_prefix(fpath), "rb") as f:
                    chunk = f.read(hash_chunk_size)
                    h.update(chunk)
                return fpath, h.hexdigest()
            except OSError:
                return fpath, None

        max_workers = min(32, (os.cpu_count() or 4) * 2)

        if self.progress_callback:
            self.progress_callback(f"第一阶段：准备计算 {total_files_to_hash} 个大文件的头部哈希...", -1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交任务
            future_to_path = {
                executor.submit(_hash_single_file, path): path
                for path in all_paths_to_hash
            }

            for future in concurrent.futures.as_completed(future_to_path):
                if self._stop_event.is_set():
                    for fut in future_to_path:
                        fut.cancel()
                    break
                path, fhash = future.result()
                if fhash:
                    file_hashes[path] = fhash
                hashed_count += 1
                if hashed_count % 50 == 0 or hashed_count == total_files_to_hash:
                    if self.progress_callback:
                        self.progress_callback(
                            f"计算大文件头部哈希: {hashed_count}/{total_files_to_hash}...", -1
                        )

        if self._stop_event.is_set():
            return []

        # 3. 第二阶段：筛选出头部匹配的潜在重复组，进行全文件哈希深度一致性校验
        candidates_by_group = []
        full_hash_paths = []

        for size, paths in size_groups_to_hash.items():
            hash_groups = defaultdict(list)
            for path in paths:
                fhash = file_hashes.get(path)
                if fhash:
                    hash_groups[fhash].append(path)

            for fhash, dup_paths in hash_groups.items():
                if len(dup_paths) >= 2:
                    candidates_by_group.append((size, dup_paths))
                    full_hash_paths.extend(dup_paths)

        total_full_files = len(full_hash_paths)
        if total_full_files == 0:
            return []

        if self.progress_callback:
            self.progress_callback(f"第二阶段：启动深度一致性校验 (共 {total_full_files} 个文件)...", -1)

        full_file_hashes = {}
        full_hashed_count = 0

        def _hash_full_file(fpath: str) -> Tuple[str, Optional[str]]:
            if self._stop_event.is_set():
                return fpath, None
            try:
                h = xxhash.xxh64()
                with open(long_path_prefix(fpath), "rb") as f:
                    while True:
                        if self._stop_event.is_set():
                            return fpath, None
                        chunk = f.read(1024 * 1024)  # 1MB 缓冲
                        if not chunk:
                            break
                        h.update(chunk)
                return fpath, h.hexdigest()
            except OSError:
                return fpath, None

        # 限制大文件全哈希的并发数，防止磁盘机械寻道/网道严重冲突
        full_max_workers = max(1, min(4, os.cpu_count() or 2))

        with concurrent.futures.ThreadPoolExecutor(max_workers=full_max_workers) as executor:
            future_to_path_full = {
                executor.submit(_hash_full_file, path): path
                for path in full_hash_paths
            }

            for future in concurrent.futures.as_completed(future_to_path_full):
                if self._stop_event.is_set():
                    for fut in future_to_path_full:
                        fut.cancel()
                    break
                path, full_hash = future.result()
                if full_hash:
                    full_file_hashes[path] = full_hash
                full_hashed_count += 1
                if self.progress_callback:
                    self.progress_callback(
                        f"深度全哈希校验: {full_hashed_count}/{total_full_files}...", -1
                    )

        # 4. 根据全文件哈希确认真正的重复文件
        duplicates: List[Tuple[int, List[str]]] = []
        for size, paths in candidates_by_group:
            if self._stop_event.is_set():
                break
            full_hash_groups = defaultdict(list)
            for path in paths:
                fh = full_file_hashes.get(path)
                if fh:
                    full_hash_groups[fh].append(path)

            for fh, dup_paths in full_hash_groups.items():
                if len(dup_paths) >= 2:
                    duplicates.append((size, dup_paths))

        # 按浪费空间降序排序（size * (count-1)）
        duplicates.sort(key=lambda x: x[0] * (len(x[1]) - 1), reverse=True)
        return duplicates


def load_cache() -> Optional[ScanResult]:
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data: Dict[str, Any] = json.load(f)
        cache_time: float = data.get("cache_time", 0)
        if time.time() - cache_time > CACHE_MAX_AGE:
            return None
        sr = ScanResult(
            top_dirs=[tuple(x) for x in data.get("top_dirs", [])],
            top_files=[tuple(x) for x in data.get("top_files", [])],
            junk_dirs=[tuple(x) for x in data.get("junk_dirs", [])],
            ext_stats=[tuple(x) for x in data.get("ext_stats", [])],
            age_groups={k: tuple(v) for k, v in data.get("age_groups", {}).items()},
            dir_size_cache=data.get("dir_size_cache", {}),
            total_used=data.get("total_used", 0),
            scan_time=data.get("scan_time", 0),
            scanned_items=data.get("scanned_items", 0),
        )
        return sr
    except Exception as e:
        logger.debug("加载缓存失败: %s", e)
        return None


def save_cache(result: ScanResult) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        data: Dict[str, Any] = {
            "cache_time": time.time(),
            "top_dirs": result.top_dirs,
            "top_files": result.top_files,
            "junk_dirs": result.junk_dirs,
            "ext_stats": result.ext_stats,
            "age_groups": result.age_groups,
            "dir_size_cache": result.dir_size_cache,
            "total_used": result.total_used,
            "scan_time": result.scan_time,
            "scanned_items": result.scanned_items,
        }
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.debug("保存缓存失败: %s", e)


# ========== GUI ==========
class DiskAnalyzerApp:
    JUNK_KEYWORDS: Tuple[str, ...] = ("log", "backup", "cache", "temp")

    def __init__(self, root: tk.Tk) -> None:
        self.root: tk.Tk = root
        self.root.title("磁盘空间分析工具 v2 - 清理助手")
        self.root.geometry("1500x900")
        self.root.minsize(1200, 700)

        self.config: Config = Config()
        self.scanner: Optional[FastScanner] = None
        self.scan_thread: Optional[threading.Thread] = None
        self.last_update_time: float = 0
        self._last_scan_result: Optional[ScanResult] = None
        self._dir_size_cache: Dict[str, int] = {}
        self._loaded_cache: bool = False
        self._theme_name: str = "light"

        self.tree_dirs: Optional[ttk.Treeview] = None
        self.tree_files: Optional[ttk.Treeview] = None
        self.tree_ext: Optional[ttk.Treeview] = None
        self.tree_junk: Optional[ttk.Treeview] = None
        self.tree_age: Optional[ttk.Treeview] = None
        self.tree_filetree: Optional[ttk.Treeview] = None
        self.tree_dup: Optional[ttk.Treeview] = None  # P3: 重复文件
        self._loaded_nodes: set = set()
        self._detached_parent_map: Dict[str, str] = {}
        self._last_sort_col: Dict[ttk.Treeview, str] = {}
        self._last_sort_reverse: Dict[ttk.Treeview, bool] = {}

        self.path_var: tk.StringVar = tk.StringVar()
        self.path_entry: Optional[ttk.Entry] = None
        self.scan_btn: Optional[ttk.Button] = None
        self.stop_btn: Optional[ttk.Button] = None
        self.simulate_mode_var: tk.BooleanVar = tk.BooleanVar(value=False)
        self.enable_dup_var: tk.BooleanVar = tk.BooleanVar(value=False)  # 重复文件检测开关
        self.top_n_var: tk.IntVar = tk.IntVar(value=self.config.top_n)
        self.progress_var: tk.IntVar = tk.IntVar()
        self.progress_bar: Optional[ttk.Progressbar] = None
        self.status_label: Optional[ttk.Label] = None
        self.disk_text: Optional[tk.Text] = None
        self.theme_btn: Optional[ttk.Button] = None
        self.notebook: Optional[ttk.Notebook] = None
        self.tab_filetree: Optional[ttk.Frame] = None
        self.tab_age: Optional[ttk.Frame] = None
        self.tab_dup: Optional[ttk.Frame] = None  # P3
        self._pie_canvas: Optional[tk.Canvas] = None  # P3
        self.dup_summary: Optional[ttk.Label] = None  # P3
        # LLM
        self.tab_llm: Optional[ttk.Frame] = None
        self._llm_text: Optional[tk.Text] = None
        self._llm_btn: Optional[ttk.Button] = None
        self._llm_analyzer: Optional[LLMAnalyzer] = None
        self._llm_streaming: bool = False
        self._context_menus: List[tk.Menu] = []

        self._apply_palette()
        self._setup_styles()
        self._create_widgets()
        self._bind_events()
        self._load_last_path()
        self._check_admin_and_warn()
        self._check_cache()
        self.cache_file = os.path.join(os.path.expanduser("~/.disk_analyzer"), "size_cache.json")
        self._load_size_cache()
        self._load_window_geometry()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ---- 主题 ----
    def _apply_palette(self) -> None:
        global Palette
        Palette = DarkPalette if self._theme_name == "dark" else LightPalette
        self.root.configure(bg=Palette.BG)

    # ---- 窗口几何记忆 ----
    def _load_window_geometry(self) -> None:
        """加载保存的窗口位置和大小，以及最后选中的标签页"""
        try:
            if GEOMETRY_FILE.exists():
                with open(GEOMETRY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "geometry" in data:
                    self.root.geometry(data["geometry"])
                if "notebook_index" in data and self.notebook is not None:
                    self.notebook.select(data["notebook_index"])
        except Exception:
            pass

    def _save_window_geometry(self) -> None:
        """保存当前窗口几何和状态"""
        try:
            data = {
                "geometry": self.root.geometry(),
                "notebook_index": (
                    self.notebook.index(self.notebook.select())
                    if self.notebook is not None
                    else 0
                ),
            }
            with open(GEOMETRY_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _on_closing(self) -> None:
        """窗口关闭回调：保存几何、停止扫描、保存配置、销毁窗口"""
        self._save_window_geometry()
        if self.scanner:
            self.scanner.stop()
        self.config.save()
        self._save_size_cache()
        self.root.destroy()

    def _setup_styles(self) -> None:
        style: ttk.Style = ttk.Style()
        style.theme_use("clam")

        if sys.platform == "win32":
            base_font: Tuple[str, int] = ("Microsoft YaHei UI", 10)
            tree_font: Tuple[str, int] = ("Microsoft YaHei UI", 9)
        else:
            base_font = ("Helvetica", 10)
            tree_font = ("Helvetica", 9)

        style.configure(".", font=base_font, background=Palette.BG, foreground=Palette.TEXT)
        style.configure("Card.TFrame", background=Palette.FRAME_BG)
        style.configure("TLabelframe", background=Palette.BG, bordercolor=Palette.BORDER)
        style.configure(
            "TLabelframe.Label",
            font=(base_font[0], 10, "bold"),
            foreground=Palette.PRIMARY,
            background=Palette.FRAME_BG,
        )

        style.configure(
            "TButton",
            padding=(14, 6),
            relief="flat",
            background=Palette.BUTTON_BG,
            borderwidth=0,
            foreground=Palette.TEXT,
            font=(base_font[0], 9),
        )
        style.map(
            "TButton",
            background=[
                ("active", Palette.BUTTON_ACTIVE),
                ("disabled", Palette.BUTTON_DISABLED),
            ],
        )
        style.configure(
            "Accent.TButton",
            background=Palette.PRIMARY,
            foreground="white",
            font=(base_font[0], 10, "bold"),
            padding=(20, 7),
        )
        style.map(
            "Accent.TButton",
            background=[
                ("active", Palette.ACCENT_HOVER),
                ("disabled", Palette.ACCENT_DISABLED),
            ],
        )
        style.configure("Danger.TButton", background=Palette.DANGER, foreground="white")
        style.map("Danger.TButton", background=[("active", Palette.DANGER_HOVER)])

        style.configure(
            "Modern.Horizontal.TProgressbar",
            background=Palette.PRIMARY,
            troughcolor=Palette.TROUGH,
            bordercolor=Palette.BG,
            lightcolor=Palette.PRIMARY,
            darkcolor=Palette.PRIMARY,
            thickness=6,
        )

        style.configure(
            "Treeview",
            rowheight=28,
            font=tree_font,
            background=Palette.FRAME_BG,
            fieldbackground=Palette.FRAME_BG,
            borderwidth=0,
            foreground=Palette.TEXT,
        )
        style.map(
            "Treeview",
            background=[("selected", Palette.HIGHLIGHT)],
            foreground=[("selected", Palette.TEXT)],
        )
        style.configure(
            "Treeview.Heading",
            font=(base_font[0], 8, "bold"),
            background=Palette.TREE_HEADING_BG,
            foreground=Palette.TEXT_MUTED,
            relief="flat",
            padding=(6, 6),
        )
        style.map("Treeview.Heading", background=[("active", Palette.TREE_HEADING_ACTIVE)])

        style.configure("TNotebook", background=Palette.BG, borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            padding=(18, 9),
            font=(base_font[0], 10),
            background=Palette.NOTEBOOK_TAB_BG,
            foreground=Palette.TEXT_MUTED,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", Palette.FRAME_BG)],
            foreground=[("selected", Palette.PRIMARY)],
            expand=[("selected", [0, 0, 0, 3])],
        )

        # 纤细滚动条 (现代风格)
        style.configure("Vertical.TScrollbar", gripcount=0, background=Palette.BORDER,
                        troughcolor=Palette.BG, arrowcolor=Palette.TEXT_MUTED, width=8)
        style.configure("Horizontal.TScrollbar", gripcount=0, background=Palette.BORDER,
                        troughcolor=Palette.BG, arrowcolor=Palette.TEXT_MUTED, height=8)

        # 现代圆角输入框
        style.configure("Modern.TEntry",
                        fieldbackground=Palette.BG,
                        foreground=Palette.TEXT,
                        bordercolor=Palette.BORDER,
                        lightcolor=Palette.BORDER,
                        darkcolor=Palette.BORDER,
                        padding=6)
        style.map("Modern.TEntry",
                  fieldbackground=[("focus", Palette.FRAME_BG)],
                  foreground=[("focus", Palette.TEXT)],
                  bordercolor=[("focus", Palette.PRIMARY)])

    def _create_widgets(self) -> None:
        # ================================================================
        # 1. 渐变头部横幅 (Canvas)
        # ================================================================
        self._header_canvas = tk.Canvas(self.root, height=88, highlightthickness=0, bd=0)
        self._header_canvas.pack(fill=tk.X)
        self._draw_gradient_header()
        self._header_resize_job: Optional[str] = None
        def _debounced_header_draw(event: tk.Event) -> None:
            if self._header_resize_job:
                self.root.after_cancel(self._header_resize_job)
            self._header_resize_job = self.root.after(80, self._draw_gradient_header)
        self._header_canvas.bind("<Configure>", _debounced_header_draw)

        # ================================================================
        # 2. 工具栏 (两行布局：第一行路径+扫描，第二行选项+工具)
        # ================================================================
        toolbar = ttk.Frame(self.root, padding=(16, 10, 16, 4))
        toolbar.pack(fill=tk.X)
        toolbar.columnconfigure(1, weight=1)

        # -- Row 0: 路径 + 扫描按钮 + 分区 --
        ttk.Label(toolbar, text="路径:", font=("", 9, "bold")).grid(
            row=0, column=0, padx=(0, 6), sticky="w"
        )
        self.path_entry = ttk.Entry(toolbar, textvariable=self.path_var, font=("", 10))
        self.path_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.path_entry.bind("<Return>", lambda e: self.start_scan())
        self.root.bind("<F5>", lambda e: self.start_scan())
        self.root.bind("<Control-o>", lambda e: self.browse_path())
        self.root.bind("<Escape>", lambda e: self.stop_scan())

        self.scan_btn = ttk.Button(
            toolbar, text="  开始扫描 (F5)  ", command=self.start_scan, style="Accent.TButton"
        )
        self.scan_btn.grid(row=0, column=2, padx=(0, 4))
        self.stop_btn = ttk.Button(
            toolbar, text="停止 (Esc)", command=self.stop_scan, state=tk.DISABLED
        )
        self.stop_btn.grid(row=0, column=3, padx=(0, 8))

        ttk.Button(toolbar, text="浏览", command=self.browse_path).grid(
            row=0, column=4, padx=(0, 8)
        )

        # P2: 分区选择器（延迟加载避免阻塞启动）
        self.partition_var = tk.StringVar(value="加载中...")
        self._partition_paths: Dict[str, str] = {}
        self._partition_toolbar = toolbar
        self._partition_next_col = 6
        part_placeholder = ttk.Label(toolbar, text="加载分区中...", font=("", 9))
        part_placeholder.grid(row=0, column=5, padx=(0, 8), sticky="w")
        self._partition_placeholder = part_placeholder
        self.root.after(200, self._load_partitions)

        # -- Row 1: 选项 + 工具按钮 --
        row1 = ttk.Frame(toolbar)
        row1.grid(row=1, column=0, columnspan=7, sticky="ew", pady=(6, 0))

        ttk.Label(row1, text="Top", font=("", 9)).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Spinbox(
            row1, from_=5, to=100, width=4, textvariable=self.top_n_var
        ).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Checkbutton(
            row1,
            text="检测重复大文件 (>=100MB)",
            variable=self.enable_dup_var
        ).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Checkbutton(
            row1, text="模拟预览（不真实删除）", variable=self.simulate_mode_var
        ).pack(side=tk.LEFT, padx=(0, 12))

        # 右侧工具按钮
        ttk.Button(row1, text="导出", command=self.export_report).pack(
            side=tk.RIGHT, padx=(4, 0)
        )
        ttk.Button(row1, text="配置", command=self.open_config_dialog).pack(
            side=tk.RIGHT, padx=(4, 0)
        )
        self.theme_btn = ttk.Button(row1, text="🌙", width=4, command=self._toggle_theme)
        self.theme_btn.pack(side=tk.RIGHT, padx=(4, 0))

        # 分隔线
        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16)

        # ================================================================
        # 3. 进度条 + 状态 (飞书风格：清新间距)
        # ================================================================
        progress_frame = ttk.Frame(self.root, padding=(16, 8, 16, 4))
        progress_frame.pack(fill=tk.X)
        self.progress_bar = ttk.Progressbar(
            progress_frame,
            variable=self.progress_var,
            maximum=100,
            style="Modern.Horizontal.TProgressbar",
        )
        self.progress_bar.pack(fill=tk.X)
        self.status_label = ttk.Label(
            progress_frame, text="就绪", font=("", 9), foreground=Palette.TEXT_MUTED
        )
        self.status_label.pack(fill=tk.X, pady=(2, 0))

        # ================================================================
        # 4. 选项卡区域
        # ================================================================
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=16, pady=(6, 0))

        self.tab_main = ttk.Frame(self.notebook, style="Card.TFrame")
        self.notebook.add(self.tab_main, text=" 📁 大目录 & 大文件 ")
        self._create_main_tab()

        self.tab_filetree = ttk.Frame(self.notebook, style="Card.TFrame")
        self.notebook.add(self.tab_filetree, text=" 🌳 目录树 ")
        self._create_filetree_tab()

        self.tab_ext = ttk.Frame(self.notebook, style="Card.TFrame")
        self.notebook.add(self.tab_ext, text=" 📊 文件类型 ")
        # Left-right split: treeview + pie chart
        ext_paned = ttk.PanedWindow(self.tab_ext, orient=tk.HORIZONTAL)
        ext_paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        ext_left = ttk.Frame(ext_paned)
        ext_paned.add(ext_left, weight=2)
        ext_right = ttk.Frame(ext_paned)
        ext_paned.add(ext_right, weight=1)
        self.tree_ext = self._create_treeview(
            ext_left,
            [("ext", "扩展名", 200), ("size", "总大小", 200)],
        )
        self._add_filter(ext_left, self.tree_ext)
        self._pie_canvas = tk.Canvas(ext_right, highlightthickness=0, bd=0)
        self._pie_canvas.pack(fill=tk.BOTH, expand=True)

        self.tab_junk = ttk.Frame(self.notebook, style="Card.TFrame")
        self.notebook.add(self.tab_junk, text=" 🗑️ 建议清理 ")
        self._create_junk_tab()

        self.tab_age = ttk.Frame(self.notebook, style="Card.TFrame")
        self.notebook.add(self.tab_age, text=" 📅 文件年龄 ")
        self._create_age_tab()

        # P3: 重复文件 Tab
        self.tab_dup = ttk.Frame(self.notebook, style="Card.TFrame")
        self.notebook.add(self.tab_dup, text=" 🔍 重复文件 ")
        self._create_dup_tab()

        # LLM 分析 Tab
        self.tab_llm = ttk.Frame(self.notebook, style="Card.TFrame")
        self.notebook.add(self.tab_llm, text=" 🤖 AI 分析 ")
        self._create_llm_tab()

        self._create_context_menus()

        # Tab change: show scan hint in status bar when switching to empty tabs
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Tab keyboard shortcuts: Ctrl+1 ~ Ctrl+7
        for i in range(7):
            self.root.bind(f"<Control-Key-{i + 1}>", lambda e, idx=i: self._select_tab(idx))

        # ================================================================
        # 5. 底部状态栏
        # ================================================================
        self._status_bar = ttk.Frame(self.root, padding=(16, 4))
        self._status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Separator(self._status_bar, orient=tk.HORIZONTAL).pack(fill=tk.X)
        status_inner = ttk.Frame(self._status_bar)
        status_inner.pack(fill=tk.X, pady=(4, 0))
        self._sb_left = ttk.Label(status_inner, text="就绪", font=("", 8), foreground=Palette.TEXT_MUTED)
        self._sb_left.pack(side=tk.LEFT)
        self._sb_right = ttk.Label(
            status_inner,
            text="F5 扫描 | Ctrl+O 浏览 | Esc 停止",
            font=("", 8),
            foreground=Palette.TEXT_MUTED,
        )
        self._sb_right.pack(side=tk.RIGHT)

    # ---- 飞书风格清爽头部 ----
    def _draw_gradient_header(self) -> None:
        c = self._header_canvas
        w = c.winfo_width() or 1300
        h = 88
        c.delete("all")

        # 纯白背景（飞书风格：干净清爽）
        c.create_rectangle(0, 0, w, h, fill=Palette.FRAME_BG, outline="")
        # 底部分隔线
        c.create_line(0, h - 1, w, h - 1, fill=Palette.BORDER, width=1)

        # 左侧标题文字
        title_font = ("Microsoft YaHei UI", 18, "bold") if sys.platform == "win32" else ("Helvetica", 16, "bold")
        c.create_text(24, 24, text="磁盘分析器", anchor="w",
                       font=title_font, fill=Palette.HEADER_TEXT)
        c.create_text(24, 52, text="快速定位大文件 · 智能清理建议", anchor="w",
                       font=("", 10), fill=Palette.HEADER_SUBTITLE)

        # 左侧磁盘使用率进度条（更醒目）
        bar_x, bar_y, bar_w, bar_h = 24, 70, 200, 12
        c.create_rectangle(bar_x, bar_y, bar_x + bar_w, bar_y + bar_h,
                           fill=Palette.BORDER, outline="", width=0)
        self._usage_bar_id = c.create_rectangle(
            bar_x, bar_y, bar_x, bar_y + bar_h,
            fill=Palette.METRIC_FREE, outline="", width=0
        )
        self._usage_text_id = c.create_text(
            bar_x + bar_w + 8, bar_y + 6, text="--%",
            anchor="w", font=("", 9, "bold"), fill=Palette.HEADER_SUBTITLE
        )

        # 右侧指标卡片（飞书风格：白色卡片 + 左侧彩色指示条）
        self._metric_labels = {}
        cards = [
            ("total", "总容量", "--", Palette.METRIC_TOTAL),
            ("used", "已使用", "--", Palette.METRIC_USED),
            ("free", "可用", "--", Palette.METRIC_FREE),
            ("scan", "扫描耗时", "--", Palette.METRIC_SCAN),
        ]
        # Responsive card sizing: shrink on narrow windows
        available = w - 32  # left + right margin
        card_gap = 8
        card_w = min(140, (available - card_gap * 3) // 4)
        card_w = max(90, card_w)  # minimum width
        card_h = 58
        total_cards_w = card_w * 4 + card_gap * 3
        start_x = w - total_cards_w - 16
        # Ensure cards don't overlap title (title area ~ 300px)
        if start_x < 300:
            start_x = 300
            card_w = max(60, (w - 300 - 16 - card_gap * 3) // 4)
            total_cards_w = card_w * 4 + card_gap * 3
        font_size = 12 if card_w < 110 else 14
        label_font_size = 8 if card_w < 110 else 9
        for idx, (key, label, val, accent) in enumerate(cards):
            x = start_x + idx * (card_w + card_gap)
            y = 14
            c.create_rectangle(x, y, x + card_w, y + card_h,
                               fill=Palette.FRAME_BG, outline=Palette.BORDER, width=1)
            c.create_rectangle(x, y, x + 4, y + card_h, fill=accent, outline="")
            val_id = c.create_text(x + 14, y + 20, text=val, anchor="w",
                                    font=("", font_size, "bold"), fill=Palette.HEADER_TEXT)
            self._metric_labels[key] = val_id
            c.create_text(x + 14, y + 42, text=label, anchor="w",
                           font=("", label_font_size), fill=Palette.HEADER_SUBTITLE)

    def _update_header_metrics(self, total: str = "", used: str = "", free: str = "", scan: str = "") -> None:
        if not hasattr(self, '_metric_labels'):
            return
        c = self._header_canvas
        updates = {"total": total, "used": used, "free": free, "scan": scan}
        for key, val in updates.items():
            if val and key in self._metric_labels:
                c.itemconfig(self._metric_labels[key], text=val)

    def _create_treeview(
        self,
        parent: tk.Widget,
        columns: List[Tuple[str, str, int]],
        show_headings: bool = True,
        **pack_kwargs: Any,
    ) -> ttk.Treeview:
        container: ttk.Frame = ttk.Frame(parent)
        container.pack(fill=tk.BOTH, expand=True, **pack_kwargs)
        col_ids: List[str] = [c[0] for c in columns]
        tree: ttk.Treeview = ttk.Treeview(
            container,
            columns=col_ids,
            show="headings" if show_headings else "tree headings",
        )
        for col_id, title, width in columns:
            tree.heading(
                col_id,
                text=title,
                command=functools.partial(self._sort_treeview, tree, col_id, False),
            )
            anchor: str = "w" if "path" in col_id.lower() else "center"
            # P2: 路径列允许拉伸
            stretch = "path" in col_id.lower() or col_id == "#0"
            tree.column(col_id, width=width, anchor=anchor, stretch=stretch)
        tree.tag_configure("evenrow", background=Palette.STRIPE_EVEN)
        tree.tag_configure("oddrow", background=Palette.STRIPE_ODD)
        tree.tag_configure("highlight", background=Palette.HIGHLIGHT)

        # Hover effect: track mouse position and highlight row under cursor
        _hover_iid: List[str] = [""]

        def _restore_item_tags(item: str) -> None:
            """Restore zebra stripe tag for an item based on its position"""
            try:
                parent = tree.parent(item)
                siblings = list(tree.get_children(parent))
                idx = siblings.index(item)
                tag = "evenrow" if idx % 2 == 0 else "oddrow"
                tree.item(item, tags=(tag,))
            except (ValueError, tk.TclError):
                pass

        def _on_motion(event: tk.Event) -> None:
            iid = tree.identify_row(event.y)
            if iid == _hover_iid[0]:
                return
            # Restore previous hover row
            if _hover_iid[0]:
                _restore_item_tags(_hover_iid[0])
            # Highlight new row (supports nested child items)
            if iid:
                try:
                    tree.item(iid, tags=("highlight",))
                except tk.TclError:
                    pass
            _hover_iid[0] = iid

        def _on_leave(event: tk.Event) -> None:
            if _hover_iid[0]:
                _restore_item_tags(_hover_iid[0])
            _hover_iid[0] = ""

        tree.bind("<Motion>", _on_motion, add="+")
        tree.bind("<Leave>", _on_leave, add="+")

        scroll_y: ttk.Scrollbar = ttk.Scrollbar(
            container, orient=tk.VERTICAL, command=tree.yview, style="Vertical.TScrollbar"
        )
        scroll_x: ttk.Scrollbar = ttk.Scrollbar(
            container, orient=tk.HORIZONTAL, command=tree.xview, style="Horizontal.TScrollbar"
        )
        tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        # 双击由各 treeview 自行绑定（避免通用绑定取错 selection）
        return tree

    def _sort_treeview(self, tree: ttk.Treeview, col: str, reverse: bool) -> None:
        try:
            self._last_sort_col[tree] = col
            self._last_sort_reverse[tree] = reverse
            
            def _recursive_sort(parent: str) -> None:
                items: List[Tuple[str, str]] = [
                    (tree.set(k, col), k) for k in tree.get_children(parent)
                ]
                if not items:
                    return
                
                heading_text: str = tree.heading(col, "text")
                if "大小" in heading_text:
                    items.sort(key=lambda t: self._size_to_float(t[0]), reverse=reverse)
                else:
                    items.sort(reverse=reverse)
                
                for index, (_, k) in enumerate(items):
                    tree.move(k, parent, index)
                    # 只有根节点需要处理斑马纹，子节点通常由父节点决定
                    if parent == "":
                        tag: str = "evenrow" if index % 2 == 0 else "oddrow"
                        tree.item(k, tags=(tag,))
                    _recursive_sort(k)
            
            _recursive_sort("")
            tree.heading(
                col,
                command=functools.partial(self._sort_treeview, tree, col, not reverse),
            )
        except Exception as e:
            logger.debug("排序失败: %s", e)

    # P1: 搜索过滤功能（递归增强版）
    def _add_filter(self, parent: tk.Widget, tree: ttk.Treeview, placeholder: str = "🔍 搜索过滤...") -> ttk.Entry:
        """在 Treeview 上方添加搜索过滤框，支持递归匹配子节点"""
        filter_frame = ttk.Frame(parent)
        filter_frame.pack(fill=tk.X, padx=8, pady=(8, 4))
        filter_var = tk.StringVar()
        filter_entry = ttk.Entry(filter_frame, textvariable=filter_var, style="Modern.TEntry")
        filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        clear_btn = ttk.Button(filter_frame, text="✖", width=3, command=lambda: self._clear_filter(tree, filter_var, filter_entry, placeholder))
        clear_btn.pack(side=tk.RIGHT)

        # Placeholder: show hint text when empty and unfocused
        def _show_placeholder(*_: Any) -> None:
            if not filter_var.get():
                filter_entry.insert(0, placeholder)
                filter_entry.config(foreground=Palette.TEXT_MUTED)

        def _hide_placeholder(*_: Any) -> None:
            if filter_entry.get() == placeholder:
                filter_entry.delete(0, tk.END)
                filter_entry.config(foreground=Palette.TEXT)

        def _on_var_change(*_: Any) -> None:
            # Sync placeholder visibility with variable
            if filter_entry.focus_get() == filter_entry:
                return  # Don't interfere while typing

        _show_placeholder()
        filter_entry.bind("<FocusIn>", _hide_placeholder)
        filter_entry.bind("<FocusOut>", lambda e: _show_placeholder() if not filter_var.get() else None)
        filter_var.trace_add("write", _on_var_change)

        # 记录被 detach 的节点，用于恢复
        _detached: List[str] = []

        def _apply_filter(*_args: Any) -> None:
            nonlocal _detached
            raw = filter_var.get().strip()
            # Skip if showing placeholder
            keyword = raw.lower() if raw != placeholder else ""

            # 先恢复所有被隐藏的节点
            for iid in _detached:
                try:
                    # 找到原始父节点重新附加
                    parent_iid = self._detached_parent_map.get(iid, "")
                    tree.reattach(iid, parent_iid, tk.END)
                except tk.TclError:
                    try:
                        tree.reattach(iid, "", tk.END)
                    except tk.TclError:
                        pass
            _detached.clear()

            if not keyword:
                # 恢复斑马纹
                for idx, item in enumerate(tree.get_children("")):
                    tag = "evenrow" if idx % 2 == 0 else "oddrow"
                    tree.item(item, tags=(tag,))
                
                # 恢复之前的排序状态
                col = self._last_sort_col.get(tree)
                reverse = self._last_sort_reverse.get(tree, False)
                if col:
                    self._sort_treeview(tree, col, reverse)
                return

            # 递归隐藏不匹配的节点，收集被 detach 的 iid
            self._detached_parent_map = {}
            self._filter_tree_recursive(tree, "", keyword, _detached)

        filter_var.trace_add("write", _apply_filter)
        return filter_entry

    def _clear_filter(self, tree: ttk.Treeview, filter_var: tk.StringVar,
                      entry: Optional[ttk.Entry] = None, placeholder: str = "") -> None:
        """清空搜索框并恢复所有节点"""
        filter_var.set("")
        if entry and placeholder:
            entry.delete(0, tk.END)
            entry.insert(0, placeholder)
            entry.config(foreground=Palette.TEXT_MUTED)

    def _restore_tree_visibility(self, tree: ttk.Treeview, item: str, root: bool = False) -> None:
        """递归恢复节点及子节点可见，并重置斑马纹"""
        try:
            tree.reattach(item, tree.parent(item) if tree.parent(item) else "", tk.END)
        except tk.TclError:
            pass
        for child in tree.get_children(item):
            self._restore_tree_visibility(tree, child)
        if root:
            for idx, child in enumerate(tree.get_children("")):
                tag = "evenrow" if idx % 2 == 0 else "oddrow"
                tree.item(child, tags=(tag,))

    def _filter_tree_recursive(self, tree: ttk.Treeview, parent: str, keyword: str, detached: List[str]) -> bool:
        """递归过滤，返回父节点是否应显示（有任一子节点匹配）"""
        any_child_match = False
        for item in tree.get_children(parent):
            child_match = self._filter_tree_recursive(tree, item, keyword, detached)
            values = tree.item(item, "values")
            text = " ".join(str(v) for v in values).lower()
            iid_match = keyword in str(item).lower()
            self_match = keyword in text or iid_match
            if self_match or child_match:
                any_child_match = True
                try:
                    tree.reattach(item, parent, tk.END)
                except tk.TclError:
                    pass
                if self_match:
                    tree.item(item, tags=("highlight",))
                if parent and (self_match or child_match):
                    try:
                        tree.item(parent, open=True)
                    except tk.TclError:
                        pass
            else:
                try:
                    # 记录父节点关系，用于恢复
                    self._detached_parent_map[item] = parent
                    tree.detach(item)
                    detached.append(item)
                except tk.TclError:
                    pass
        return any_child_match

    def _create_main_tab(self) -> None:
        paned: ttk.PanedWindow = ttk.PanedWindow(self.tab_main, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        left_frame: ttk.LabelFrame = ttk.LabelFrame(
            paned, text=" 占用最大目录 (包含子目录) ", padding=5
        )
        paned.add(left_frame, weight=1)
        self.tree_dirs = self._create_treeview(
            left_frame,
            [("size", "大小", 100), ("path", "目录路径 (可清理/压缩)", 400)],
        )
        self.tree_dirs.bind("<Double-1>", lambda e: self._open_tree_path(self.tree_dirs, event=e))
        self._add_filter(left_frame, self.tree_dirs)

        right_frame: ttk.LabelFrame = ttk.LabelFrame(paned, text=" 占用最大文件 ", padding=5)
        paned.add(right_frame, weight=2)
        self.tree_files = self._create_treeview(
            right_frame,
            [
                ("size", "大小", 100),
                ("mtime", "修改时间", 140),
                ("path", "文件路径", 400),
            ],
        )
        self.tree_files.bind("<Double-1>", lambda e: self._open_tree_path(self.tree_files, True, event=e))
        self._add_filter(right_frame, self.tree_files)

        # Empty state overlay
        self._empty_overlay = ttk.Label(
            self.tab_main,
            text="📁  选择路径后按 F5 开始扫描磁盘空间\n\n"
                 "支持扫描本地磁盘、外接硬盘、网络驱动器等\n"
                 "扫描结果将显示占用最大的目录和文件",
            font=("", 12),
            foreground=Palette.TEXT_MUTED,
            justify=tk.CENTER,
        )
        self._empty_overlay.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

    def _create_junk_tab(self) -> None:
        self.tree_junk = self._create_treeview(
            self.tab_junk,
            [("size", "大小", 120), ("path", "路径", 600)],
            padx=8,
            pady=(8, 0),
        )
        # P1: 垃圾目录过滤
        self._add_filter(self.tab_junk, self.tree_junk)
        self.tree_junk.bind("<Double-1>", lambda e: self._open_tree_path(self.tree_junk, event=e))
        btn_frame: ttk.Frame = ttk.Frame(self.tab_junk)
        btn_frame.pack(fill=tk.X, pady=8, padx=8)
        ttk.Button(btn_frame, text="打开位置", command=self._open_selected_junk).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(btn_frame, text="移至回收站", command=self._delete_selected_junk).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(
            btn_frame,
            text="永久删除",
            command=self._permanently_delete_junk,
            style="Danger.TButton",
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(
            btn_frame,
            text="开启顶部 [模拟预览] 可安全测试删除结果",
            foreground=Palette.DANGER,
        ).pack(side=tk.LEFT, padx=15)

    def _create_filetree_tab(self) -> None:
        assert self.tab_filetree is not None
        self.tree_filetree = self._create_treeview(
            self.tab_filetree,
            [("size", "大小", 120), ("path", "路径", 600)],
            show_headings=False,
            padx=8,
            pady=8,
        )
        assert self.tree_filetree is not None
        self.tree_filetree.heading("size", text="大小")
        self.tree_filetree.heading("path", text="路径")
        self.tree_filetree.heading("#0", text="目录树")
        self.tree_filetree.column("#0", width=250, anchor="w")
        self.tree_filetree.bind("<<TreeviewOpen>>", self._on_filetree_expand)
        # 目录树单独绑双击：用 identify_row 精确定位，避免 selection() 取错节点
        self.tree_filetree.bind("<Double-1>", self._on_filetree_double_click)
        # P2: 目录树过滤
        self._add_filter(self.tab_filetree, self.tree_filetree)

    def _create_age_tab(self) -> None:
        assert self.tab_age is not None
        self.tree_age = self._create_treeview(
            self.tab_age,
            [
                ("age", "时间段", 200),
                ("count", "文件数", 150),
                ("size", "总大小", 200),
            ],
            padx=8,
            pady=8,
        )

    def _create_dup_tab(self) -> None:
        """P3: 重复文件检测 Tab"""
        assert self.tab_dup is not None
        # 统计行
        self.dup_summary = ttk.Label(
            self.tab_dup, text="扫描后显示重复文件", font=("", 10), foreground=Palette.TEXT_MUTED
        )
        self.dup_summary.pack(fill=tk.X, padx=8, pady=(8, 4))

        self.tree_dup = self._create_treeview(
            self.tab_dup,
            [
                ("size", "单个大小", 100),
                ("count", "副本数", 80),
                ("waste", "浪费空间", 120),
                ("path", "文件路径", 500),
            ],
            padx=8, pady=(0, 0),
        )
        self.tree_dup.bind("<Double-1>", lambda e: self._open_tree_path(self.tree_dup, event=e))
        # P3: 重复文件过滤
        self._add_filter(self.tab_dup, self.tree_dup)

    def _create_llm_tab(self) -> None:
        """LLM 智能分析 Tab"""
        assert self.tab_llm is not None

        # 顶部工具栏
        toolbar = ttk.Frame(self.tab_llm, padding=(8, 8, 8, 4))
        toolbar.pack(fill=tk.X)

        self._llm_btn = ttk.Button(
            toolbar, text="  🤖 开始 AI 分析  ", style="Accent.TButton",
            command=self._start_llm_analysis,
        )
        self._llm_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._llm_stop_btn = ttk.Button(
            toolbar, text="⏹ 停止", command=self._cancel_llm_analysis, state=tk.DISABLED,
        )
        self._llm_stop_btn.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(
            toolbar, text="清空", command=self._clear_llm_output
        ).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(
            toolbar, text="复制结果", command=self._copy_llm_output
        ).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(
            toolbar, text="导出 Markdown", command=self._export_llm_markdown
        ).pack(side=tk.LEFT, padx=(0, 8))

        self._llm_status = ttk.Label(
            toolbar, text="", foreground=Palette.TEXT_MUTED, font=("", 9)
        )
        self._llm_status.pack(side=tk.LEFT, padx=(8, 0))

        # LLM 输出区域
        output_frame = ttk.Frame(self.tab_llm)
        output_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self._llm_text = tk.Text(
            output_frame,
            font=("", 10),
            bg=Palette.FRAME_BG,
            fg=Palette.TEXT,
            insertbackground=Palette.TEXT,
            wrap=tk.WORD,
            padx=12,
            pady=10,
            spacing1=2,
            spacing3=2,
            state=tk.DISABLED,
        )
        llm_scroll = ttk.Scrollbar(
            output_frame, orient=tk.VERTICAL, command=self._llm_text.yview,
            style="Vertical.TScrollbar",
        )
        self._llm_text.configure(yscrollcommand=llm_scroll.set)
        self._llm_text.grid(row=0, column=0, sticky="nsew")
        llm_scroll.grid(row=0, column=1, sticky="ns")
        output_frame.rowconfigure(0, weight=1)
        output_frame.columnconfigure(0, weight=1)

        # 配置 Markdown-like 标签样式
        self._llm_text.tag_configure("h1", font=("", 14, "bold"), foreground=Palette.PRIMARY)
        self._llm_text.tag_configure("h2", font=("", 12, "bold"), foreground=Palette.PRIMARY)
        self._llm_text.tag_configure("h3", font=("", 11, "bold"), foreground=Palette.TEXT)
        self._llm_text.tag_configure("bold", font=("", 10, "bold"))
        self._llm_text.tag_configure("emoji", font=("", 11))
        self._llm_text.tag_configure("dim", foreground=Palette.TEXT_MUTED)

        # 默认提示
        self._llm_text.config(state=tk.NORMAL)
        self._llm_text.insert(
            tk.END,
            "等待扫描完成后，点击上方「🤖 开始 AI 分析」按钮。\n\n"
            "AI 将分析扫描结果，告诉你：\n"
            "  • 哪些目录/文件占用了大量空间\n"
            "  • 它们是什么、为什么大\n"
            "  • 哪些可以安全清理\n"
            "  • 具体的清理操作建议\n\n"
            "请先在「配置 → LLM 分析」中设置 API 地址和模型。",
            "dim",
        )
        self._llm_text.config(state=tk.DISABLED)

    def _start_llm_analysis(self) -> None:
        """触发 LLM 分析"""
        if self._llm_streaming:
            return
        if not self._last_scan_result:
            messagebox.showinfo("提示", "请先执行一次扫描后再进行 AI 分析。")
            return
        if not self.config.llm_configured:
            if messagebox.askyesno("LLM 未配置", "尚未配置 LLM API，是否现在去设置？"):
                self.open_config_dialog()
            return

        self._llm_streaming = True
        if self._llm_btn:
            self._llm_btn.config(state=tk.DISABLED, text="  ⏳ 分析中...  ")
        if hasattr(self, '_llm_stop_btn') and self._llm_stop_btn:
            self._llm_stop_btn.config(state=tk.NORMAL)
        if self._llm_status:
            self._llm_status.config(text="正在调用 LLM API...")
        if self._llm_text:
            self._llm_text.config(state=tk.NORMAL)
            self._llm_text.delete("1.0", tk.END)
            self._llm_text.insert(tk.END, "正在分析扫描结果，请稍候...\n\n", "dim")
            self._llm_text.config(state=tk.DISABLED)

        # 切换到 LLM tab
        if self.notebook and self.tab_llm:
            self.notebook.select(self.tab_llm)

        self._llm_analyzer = LLMAnalyzer(self.config)
        self._llm_analyzer.analyze(
            self._last_scan_result,
            on_token=self._on_llm_token,
            on_done=self._on_llm_done,
        )

    def _on_llm_token(self, token: str) -> None:
        """LLM 流式 token 回调（后台线程调用，需调度到主线程）"""
        self.root.after(0, self._append_llm_token, token)

    def _append_llm_token(self, token: str) -> None:
        """在主线程中追加 token 到输出区"""
        if not self._llm_text:
            return
        self._llm_text.config(state=tk.NORMAL)

        # 简单 Markdown 渲染
        if token.startswith("## "):
            self._llm_text.insert(tk.END, token[3:], "h2")
        elif token.startswith("### "):
            self._llm_text.insert(tk.END, token[4:], "h3")
        elif token.startswith("# "):
            self._llm_text.insert(tk.END, token[2:], "h1")
        else:
            self._llm_text.insert(tk.END, token)

        self._llm_text.see(tk.END)
        self._llm_text.config(state=tk.DISABLED)

    def _on_llm_done(self, full_text: Optional[str], error: Optional[str]) -> None:
        """LLM 分析完成回调（后台线程调用）"""
        self.root.after(0, self._finish_llm_analysis, full_text, error)

    def _finish_llm_analysis(self, full_text: Optional[str], error: Optional[str]) -> None:
        """在主线程中处理分析完成"""
        self._llm_streaming = False
        if self._llm_btn:
            self._llm_btn.config(state=tk.NORMAL, text="  🤖 重新分析  ")
        if hasattr(self, '_llm_stop_btn') and self._llm_stop_btn:
            self._llm_stop_btn.config(state=tk.DISABLED)

        if error and not full_text:
            if self._llm_status:
                self._llm_status.config(text=f"错误: {error}", foreground=Palette.DANGER)
            if self._llm_text:
                self._llm_text.config(state=tk.NORMAL)
                self._llm_text.delete("1.0", tk.END)
                self._llm_text.insert(tk.END, f"❌ 分析失败\n\n{error}", "dim")
                self._llm_text.config(state=tk.DISABLED)
        elif error == "已取消":
            if self._llm_status:
                self._llm_status.config(text="已取消", foreground=Palette.WARNING)
        else:
            if self._llm_status:
                self._llm_status.config(text="分析完成 ✓", foreground=Palette.SUCCESS)
            # 对完整文本做一次最终 Markdown 渲染
            if full_text and self._llm_text:
                self._render_llm_markdown(full_text)

    def _render_llm_markdown(self, text: str) -> None:
        """将完整 Markdown 文本渲染到 Text 组件"""
        if not self._llm_text:
            return
        self._llm_text.config(state=tk.NORMAL)
        self._llm_text.delete("1.0", tk.END)

        for line in text.split("\n"):
            if line.startswith("### "):
                self._llm_text.insert(tk.END, line[4:] + "\n", "h3")
            elif line.startswith("## "):
                self._llm_text.insert(tk.END, line[3:] + "\n", "h2")
            elif line.startswith("# "):
                self._llm_text.insert(tk.END, line[2:] + "\n", "h1")
            elif line.startswith("- **") or line.startswith("* **"):
                # Bold bullet points
                self._llm_text.insert(tk.END, line + "\n", "bold")
            else:
                self._llm_text.insert(tk.END, line + "\n")

        self._llm_text.see(tk.END)
        self._llm_text.config(state=tk.DISABLED)

    def _clear_llm_output(self) -> None:
        """清空 LLM 输出"""
        if self._llm_streaming and self._llm_analyzer:
            self._llm_analyzer.cancel()
        if self._llm_text:
            self._llm_text.config(state=tk.NORMAL)
            self._llm_text.delete("1.0", tk.END)
            self._llm_text.config(state=tk.DISABLED)
        if self._llm_status:
            self._llm_status.config(text="")

    def _copy_llm_output(self) -> None:
        """复制 LLM 输出到剪贴板"""
        if not self._llm_text:
            return
        content = self._llm_text.get("1.0", tk.END).strip()
        if not content:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        if self._llm_status:
            self._llm_status.config(text="已复制到剪贴板 ✓", foreground=Palette.SUCCESS)

    def _cancel_llm_analysis(self) -> None:
        """取消正在进行的 LLM 分析"""
        if self._llm_analyzer:
            self._llm_analyzer.cancel()
        if self._llm_stop_btn:
            self._llm_stop_btn.config(state=tk.DISABLED)
        if self._llm_status:
            self._llm_status.config(text="正在取消...", foreground=Palette.WARNING)

    def _export_llm_markdown(self) -> None:
        """导出 LLM 分析结果为 Markdown 文件"""
        if not self._llm_text:
            return
        content = self._llm_text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showinfo("提示", "当前无分析结果可导出。")
            return
        fp = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("文本文件", "*.txt")],
            title="导出 AI 分析结果",
        )
        if not fp:
            return
        try:
            with open(fp, "w", encoding="utf-8") as f:
                f.write(f"# 磁盘分析报告 - AI 分析\n\n")
                f.write(f"扫描路径: {self.config.last_scan_path}\n")
                f.write(f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
                f.write("---\n\n")
                f.write(content)
            if self._llm_status:
                self._llm_status.config(text=f"已导出: {fp}", foreground=Palette.SUCCESS)
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _create_context_menus(self) -> None:
        menu_kw: Dict[str, str] = {
            "bg": Palette.FRAME_BG,
            "fg": Palette.TEXT,
            "activebackground": Palette.HIGHLIGHT,
            "activeforeground": Palette.TEXT,
        }

        def popup(event: tk.Event, tree: ttk.Treeview, menu: tk.Menu) -> None:
            iid: str = tree.identify_row(event.y)
            if iid:
                tree.selection_set(iid)
                menu.tk_popup(event.x_root, event.y_root)

        # 目录
        dir_menu: tk.Menu = tk.Menu(self.root, tearoff=0, font=("", 9), **menu_kw)
        self._context_menus.append(dir_menu)
        dir_menu.add_command(
            label="打开文件夹",
            command=functools.partial(self._open_tree_path, self.tree_dirs),
        )
        dir_menu.add_command(
            label="复制路径",
            command=functools.partial(self._copy_path, self.tree_dirs),
        )
        dir_menu.add_separator()
        dir_menu.add_command(label="继续扫描此目录", command=self._continue_scan_dir)
        assert self.tree_dirs is not None
        self.tree_dirs.bind(
            "<Button-3>",
            functools.partial(popup, tree=self.tree_dirs, menu=dir_menu),
        )

        # 文件
        file_menu: tk.Menu = tk.Menu(self.root, tearoff=0, font=("", 9), **menu_kw)
        self._context_menus.append(file_menu)
        file_menu.add_command(
            label="打开所在文件夹",
            command=functools.partial(self._open_tree_path, self.tree_files, True),
        )
        file_menu.add_command(
            label="复制文件路径",
            command=functools.partial(self._copy_path, self.tree_files),
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="移至回收站",
            command=functools.partial(self._delete_selected_file, True),
        )
        file_menu.add_command(
            label="永久删除",
            command=functools.partial(self._delete_selected_file, False),
        )
        assert self.tree_files is not None
        self.tree_files.bind(
            "<Button-3>",
            functools.partial(popup, tree=self.tree_files, menu=file_menu),
        )

        # 重复文件
        dup_menu: tk.Menu = tk.Menu(self.root, tearoff=0, font=("", 9), **menu_kw)
        self._context_menus.append(dup_menu)
        dup_menu.add_command(
            label="打开所在文件夹",
            command=functools.partial(self._open_tree_path, self.tree_dup, True),
        )
        dup_menu.add_command(
            label="复制文件路径",
            command=functools.partial(self._copy_path, self.tree_dup),
        )
        dup_menu.add_separator()
        dup_menu.add_command(
            label="移至回收站",
            command=functools.partial(self._delete_selected_dup, True),
        )
        dup_menu.add_command(
            label="永久删除",
            command=functools.partial(self._delete_selected_dup, False),
        )
        assert self.tree_dup is not None
        self.tree_dup.bind(
            "<Button-3>",
            functools.partial(popup, tree=self.tree_dup, menu=dup_menu),
        )

        # 目录树 — 右键菜单与大文件一致
        ft_menu: tk.Menu = tk.Menu(self.root, tearoff=0, font=("", 9), **menu_kw)
        self._context_menus.append(ft_menu)
        ft_menu.add_command(
            label="📂 打开",
            command=functools.partial(self._filetree_open),
        )
        ft_menu.add_command(
            label="📋 复制路径",
            command=functools.partial(self._copy_path, self.tree_filetree),
        )
        ft_menu.add_separator()
        ft_menu.add_command(
            label="🔍 继续扫描此目录",
            command=self._filetree_continue_scan,
        )
        ft_menu.add_separator()
        ft_menu.add_command(
            label="🗑️ 移至回收站",
            command=functools.partial(self._filetree_delete, recycle=True),
        )
        ft_menu.add_command(
            label="⚠️ 永久删除",
            command=functools.partial(self._filetree_delete, recycle=False),
        )
        assert self.tree_filetree is not None
        self.tree_filetree.bind(
            "<Button-3>",
            functools.partial(popup, tree=self.tree_filetree, menu=ft_menu),
        )

    def _bind_events(self) -> None:
        pass  # Events are bound in __init__ and _create_widgets

    def _load_last_path(self) -> None:
        if self.config.last_scan_path and os.path.exists(self.config.last_scan_path):
            self.path_var.set(self.config.last_scan_path)
        else:
            default: str = "C:\\" if sys.platform == "win32" else str(Path.home())
            self.path_var.set(default)

    def _check_admin_and_warn(self) -> None:
        if sys.platform == "win32" and not is_admin():
            self.root.after(
                500,
                lambda: messagebox.showwarning(
                    "权限提示",
                    "未以管理员身份运行，扫描系统目录时部分数据可能受限。",
                ),
            )

    def _check_cache(self) -> None:
        cached: Optional[ScanResult] = load_cache()
        if cached is not None:
            if messagebox.askyesno("缓存可用", "检测到上次扫描结果（不到1小时），是否加载？"):
                self._last_scan_result = cached
                self._dir_size_cache = cached.dir_size_cache
                self._loaded_cache = True
                self._populate_trees(cached)
                self._populate_filetree(self.path_var.get())

    def _on_tab_changed(self, event: tk.Event) -> None:
        """Show hint in status bar when switching to an empty tab"""
        if self._last_scan_result:
            return  # Already have data
        if self.status_label:
            self.status_label.config(text="提示: 请先按 F5 或点击「开始扫描」分析磁盘空间")

    def _select_tab(self, index: int) -> None:
        """Switch to tab by index (0-based), bound to Ctrl+1~7"""
        if self.notebook:
            try:
                tabs = self.notebook.tabs()
                if 0 <= index < len(tabs):
                    self.notebook.select(index)
            except tk.TclError:
                pass

    def _toggle_theme(self) -> None:
        self._theme_name = "dark" if self._theme_name == "light" else "light"
        self._apply_palette()
        self._setup_styles()
        if self.status_label:
            self.status_label.configure(foreground=Palette.TEXT_MUTED)
        if self.theme_btn:
            self.theme_btn.configure(text="☀️" if self._theme_name == "dark" else "🌙")
        # 重绘渐变头部和饼图
        self._draw_gradient_header()
        if self._last_scan_result:
            self._draw_pie_chart(self._last_scan_result.ext_stats)
        # 重新填充指标
        self.update_disk_info()
        if self._last_scan_result:
            r = self._last_scan_result
            self._update_header_metrics(scan=f"{r.scan_time:.1f}s")
        for tree in (
            self.tree_dirs,
            self.tree_files,
            self.tree_ext,
            self.tree_junk,
            self.tree_age,
            self.tree_filetree,
            self.tree_dup,
        ):
            if tree:
                tree.tag_configure("evenrow", background=Palette.STRIPE_EVEN)
                tree.tag_configure("oddrow", background=Palette.STRIPE_ODD)
                tree.tag_configure("highlight", background=Palette.HIGHLIGHT)
        # Update LLM text widget colors
        if self._llm_text:
            self._llm_text.config(bg=Palette.FRAME_BG, fg=Palette.TEXT,
                                  insertbackground=Palette.TEXT)
            self._llm_text.tag_configure("h1", foreground=Palette.PRIMARY)
            self._llm_text.tag_configure("h2", foreground=Palette.PRIMARY)
            self._llm_text.tag_configure("h3", foreground=Palette.TEXT)
            self._llm_text.tag_configure("dim", foreground=Palette.TEXT_MUTED)
        # Update LLM status label
        if hasattr(self, '_llm_status') and self._llm_status:
            self._llm_status.config(foreground=Palette.TEXT_MUTED)
        # Update empty overlay
        if hasattr(self, '_empty_overlay') and self._empty_overlay:
            self._empty_overlay.config(foreground=Palette.TEXT_MUTED)
        # Update context menu colors
        for menu in self._context_menus:
            try:
                menu.config(bg=Palette.FRAME_BG, fg=Palette.TEXT,
                            activebackground=Palette.HIGHLIGHT,
                            activeforeground=Palette.TEXT)
            except tk.TclError:
                pass

    @staticmethod
    def _norm_path(p: str) -> str:
        """规范化路径用于缓存匹配（小写 + 去尾斜杠 + 统一分隔符）"""
        return os.path.normcase(os.path.normpath(p))

    # ---- 目录树懒加载 ----
    def _populate_filetree(self, path: str) -> None:
        assert self.tree_filetree is not None
        self.tree_filetree.delete(*self.tree_filetree.get_children())
        self._loaded_nodes.clear()
        if not os.path.isdir(path):
            self.tree_filetree.insert(
                "", tk.END, text="目录不存在", values=("", path)
            )
            return
        # P2: 根目录加图标
        self.tree_filetree.insert(
            "",
            tk.END,
            iid=path,
            text="📁 " + (os.path.basename(path) or path),
            values=("", path),
            open=True,
        )
        self._load_children(path)

    def _on_filetree_expand(self, event: tk.Event) -> None:
        assert self.tree_filetree is not None
        iid: str = self.tree_filetree.focus()
        logger.debug("TreeviewOpen: iid=%s, in_loaded=%s, isdir=%s",
                      iid, iid in self._loaded_nodes, os.path.isdir(iid) if iid else False)
        if iid and iid not in self._loaded_nodes and os.path.isdir(iid):
            self._load_children(iid)

    def _on_filetree_double_click(self, event: tk.Event) -> str:
        """目录树双击：用 identify_row 精确定位，目录→打开，文件→用默认程序打开"""
        assert self.tree_filetree is not None
        iid: str = self.tree_filetree.identify_row(event.y)
        if not iid:
            return "break"
        if os.path.isdir(iid):
            open_file_or_dir(iid)
        elif os.path.isfile(iid):
            # 文件用系统默认程序打开
            try:
                if sys.platform == "win32":
                    os.startfile(iid)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", iid])
                else:
                    subprocess.Popen(["xdg-open", iid])
            except Exception as e:
                logger.error("打开文件失败 %s: %s", iid, e)
        return "break"  # 阻止通用 <Double-1> 处理

    def _filetree_get_selected_path(self) -> Optional[str]:
        """获取目录树中选中的路径（用 selection 而非 focus）"""
        assert self.tree_filetree is not None
        sel = self.tree_filetree.selection()
        if sel:
            return sel[0]
        return None

    def _filetree_open(self) -> None:
        """右键打开：目录→资源管理器，文件→默认程序"""
        path = self._filetree_get_selected_path()
        if not path:
            return
        if os.path.isfile(path):
            try:
                if sys.platform == "win32":
                    os.startfile(path)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
            except Exception as e:
                logger.error("打开文件失败 %s: %s", path, e)
        else:
            open_file_or_dir(path)

    def _filetree_continue_scan(self) -> None:
        """右键继续扫描此目录"""
        path = self._filetree_get_selected_path()
        if not path or not os.path.isdir(path):
            return
        if messagebox.askyesno("继续扫描", f"将以此目录为起点重新扫描:\n{path}"):
            self.stop_scan()
            self.path_var.set(path)
            self.start_scan()

    def _filetree_delete(self, recycle: bool = True) -> None:
        """右键删除文件/目录"""
        path = self._filetree_get_selected_path()
        if not path:
            return
        if self.simulate_mode_var.get():
            action = "移至回收站" if recycle else "永久删除"
            messagebox.showinfo("模拟模式拦截", f"欲执行: {action}\n目标: {path}")
            return
        confirm_msg = f"确定要删除吗？\n{path}\n此操作{'将移入回收站' if recycle else '不可撤销'}！"
        if not messagebox.askyesno("确认删除", confirm_msg):
            return
        try:
            if recycle and HAS_SEND2TRASH:
                send2trash.send2trash(path)
            elif os.path.isfile(path):
                os.remove(path)
            else:
                shutil.rmtree(path)
            # 从树中移除
            try:
                self.tree_filetree.delete(path)
            except tk.TclError:
                pass
            self.update_disk_info()
            if self.status_label:
                self.status_label.config(text=f"已删除: {path}")
        except Exception as e:
            messagebox.showerror("删除失败", str(e))
            logger.error("删除失败 %s: %s", path, e)

    def _load_children(self, parent_path: str) -> None:
        """加载子目录，大小从扫描缓存读取。"""
        assert self.tree_filetree is not None
        if parent_path in self._loaded_nodes:
            return
        logger.debug("_load_children called for: %s", parent_path)

        children: List[str] = list(self.tree_filetree.get_children(parent_path))
        for child in children:
            if self.tree_filetree.item(child, "text") == "...":
                self.tree_filetree.delete(child)

        child_info: List[Tuple[str, int]] = []  # (path, size)
        try:
            with os.scandir(long_path_prefix(parent_path)) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                        key: str = self._norm_path(entry.path)
                        size: int = self._dir_size_cache.get(key, -1)
                        child_info.append((entry.path, size))
        except OSError as e:
            logger.debug("扫描子目录失败 %s: %s", parent_path, e)
            return

        # 只有真正有子目录时才标记已加载
        if child_info:
            self._loaded_nodes.add(parent_path)

        # 分离：有缓存的 vs 无缓存的
        cached: List[Tuple[str, int]] = [(p, s) for p, s in child_info if s >= 0]
        uncached: List[str] = [p for p, s in child_info if s < 0]

        # 有缓存的按大小降序
        cached.sort(key=lambda x: x[1], reverse=True)

        # 先插入有缓存的（真实大小），加占位子节点以显示展开箭头
        for child_path, child_size in cached:
            try:
                name: str = os.path.basename(child_path)
                self.tree_filetree.insert(
                    parent_path,
                    tk.END,
                    iid=child_path,
                    text="📁 " + name,
                    values=(format_size(child_size), child_path),
                )
                self.tree_filetree.insert(
                    child_path, tk.END, text="...", values=("", "")
                )
            except tk.TclError:
                pass

        # 无缓存的显示"计算中..."，后台算完再刷新
        for child_path in uncached:
            try:
                name: str = os.path.basename(child_path)
                self.tree_filetree.insert(
                    parent_path,
                    tk.END,
                    iid=child_path,
                    text="📁 " + name,
                    values=("计算中...", child_path),
                )
                self.tree_filetree.insert(
                    child_path, tk.END, text="...", values=("", "")
                )
            except tk.TclError:
                pass

        if not cached and not uncached:
            # 无子目录 → 列出文件
            files: List[Tuple[str, int]] = []
            try:
                with os.scandir(long_path_prefix(parent_path)) as it:
                    for entry in it:
                        if entry.is_file(follow_symlinks=False):
                            try:
                                fsize: int = entry.stat(follow_symlinks=False).st_size
                            except OSError:
                                fsize = 0
                            files.append((entry.path, fsize))
            except OSError:
                pass
            if files:
                self._loaded_nodes.add(parent_path)
                files.sort(key=lambda x: x[1], reverse=True)
                for fpath, fsize in files:
                    try:
                        fname: str = os.path.basename(fpath)
                        self.tree_filetree.insert(
                            parent_path,
                            tk.END,
                            iid=fpath,
                            text="📄 " + fname,
                            values=(format_size(fsize), fpath),
                        )
                    except tk.TclError:
                        pass
            else:
                self.tree_filetree.insert(
                    parent_path, tk.END, text="(空目录)", values=("", "")
                )

        # 后台补齐无缓存的大小
        if uncached:
            def _fill_missing() -> None:
                results: List[Tuple[str, int]] = []
                for cpath in uncached:
                    total: int = 0
                    try:
                        for dirpath, _dirnames, filenames in os.walk(
                            cpath, followlinks=False
                        ):
                            for fname in filenames:
                                try:
                                    total += os.path.getsize(
                                        os.path.join(dirpath, fname)
                                    )
                                except OSError:
                                    pass
                    except OSError:
                        pass
                    results.append((cpath, total))
                    # 写入缓存
                    self._dir_size_cache[self._norm_path(cpath)] = total
                # 回主线程刷新 + 重排全部子节点
                self.root.after(
                    0,
                    lambda: self._refresh_children(parent_path, results),
                )

            threading.Thread(target=_fill_missing, daemon=True).start()

    def _load_size_cache(self) -> None:
        """从磁盘加载大小缓存"""
        try:
            with open(self.cache_file, 'r') as f:
                self._dir_size_cache = json.load(f)
        except Exception:
            self._dir_size_cache = {}

    def _save_size_cache(self) -> None:
        """保存大小缓存到磁盘"""
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, 'w') as f:
                json.dump(self._dir_size_cache, f)
        except Exception as e:
            logger.debug("保存缓存失败: %s", e)

    def _refresh_children(
        self, parent: str, updated: List[Tuple[str, int]]
    ) -> None:
        """更新无缓存节点的大小，并按大小重排所有子节点"""
        assert self.tree_filetree is not None
        # 更新大小
        for cpath, total in updated:
            try:
                self.tree_filetree.set(cpath, "size", format_size(total))
            except tk.TclError:
                pass
        # 收集所有子节点，按大小降序重排
        all_children: List[Tuple[str, int]] = []
        for child_iid in self.tree_filetree.get_children(parent):
            size_text: str = self.tree_filetree.set(child_iid, "size")
            # 解析 "1.23 GB" 回字节数用于排序
            all_children.append((child_iid, self._parse_size(size_text)))
        all_children.sort(key=lambda x: x[1], reverse=True)
        for idx, (child_iid, _) in enumerate(all_children):
            try:
                self.tree_filetree.move(child_iid, parent, idx)
            except tk.TclError:
                pass

    @staticmethod
    def _parse_size(text: str) -> int:
        """把 '1.23 GB' 解析回字节数，用于排序"""
        try:
            parts = text.split()
            if len(parts) != 2:
                return 0
            val = float(parts[0])
            units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
            return int(val * units.get(parts[1], 1))
        except (ValueError, IndexError):
            return 0

    @staticmethod
    def _size_to_float(val: str) -> float:
        """把 '1.23 GB' 解析回浮点数，用于排序"""
        units: Dict[str, int] = {
            "B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4,
        }
        try:
            parts: List[str] = val.split()
            return float(parts[0]) * units.get(parts[1], 1)
        except (ValueError, AttributeError, IndexError):
            return 0.0

    # ---- 路径操作 ----
    def _open_tree_path(
        self, tree: ttk.Treeview, select_file: bool = False, event: Optional[tk.Event] = None
    ) -> None:
        # P0: 优先用 identify_row 精确定位（避免 selection() 取错节点）
        if event:
            iid: str = tree.identify_row(event.y)
            if not iid:
                return
            path = iid
        else:
            sel: Tuple[str, ...] = tree.selection()
            if not sel:
                return
            path = sel[0]
        open_file_or_dir(path, select_file)

    def _copy_path(self, tree: ttk.Treeview) -> None:
        sel: Tuple[str, ...] = tree.selection()
        if not sel:
            return
        path: str = sel[0]
        self.root.clipboard_clear()
        self.root.clipboard_append(path)
        if self.status_label:
            self.status_label.config(text=f"已复制: {path}")

    def _continue_scan_dir(self) -> None:
        assert self.tree_dirs is not None
        sel: Tuple[str, ...] = self.tree_dirs.selection()
        if not sel:
            return
        path: str = sel[0]
        if not os.path.isdir(path):
            return
        if messagebox.askyesno("继续扫描", f"将以此目录为起点重新扫描:\n{path}"):
            self.stop_scan()
            self.path_var.set(path)
            self.start_scan()

    def _delete_selected_file(self, recycle: bool = True) -> None:
        assert self.tree_files is not None
        sel: Tuple[str, ...] = self.tree_files.selection()
        if not sel:
            return
        path: str = sel[0]
        if self.simulate_mode_var.get():
            action: str = "移至回收站" if recycle else "永久删除"
            messagebox.showinfo(
                "模拟模式拦截",
                f"欲执行: {action}\n目标: {path}\n当前为预览模式，无真实变动。",
            )
            return
        confirm_msg: str = (
            f"确定要删除此文件吗？\n{path}\n此操作{'将移入回收站' if recycle else '不可撤销'}！"
        )
        if not messagebox.askyesno("危险操作确认", confirm_msg):
            return
        try:
            if recycle and HAS_SEND2TRASH:
                send2trash.send2trash(path)
            else:
                os.remove(path)
            self.tree_files.delete(sel[0])
            self.update_disk_info()
            if self.status_label:
                self.status_label.config(text=f"已删除: {path}")
        except Exception as e:
            messagebox.showerror("删除失败", str(e))
            logger.error("删除文件失败 %s: %s", path, e)

    def _open_selected_junk(self) -> None:
        assert self.tree_junk is not None
        sel: Tuple[str, ...] = self.tree_junk.selection()
        if not sel:
            return
        open_file_or_dir(sel[0])

    def _delete_selected_junk(self) -> None:
        assert self.tree_junk is not None
        sel: Tuple[str, ...] = self.tree_junk.selection()
        if not sel:
            return
        path: str = sel[0]
        if self.simulate_mode_var.get():
            messagebox.showinfo("模拟模式拦截", f"模拟清空: {path}")
            return
        if not messagebox.askyesno("确认清理", f"将清理该目录:\n{path}\n确定吗？"):
            return
        try:
            if HAS_SEND2TRASH:
                send2trash.send2trash(path)
                self.tree_junk.delete(sel[0])
                self.update_disk_info()
                if self.status_label:
                    self.status_label.config(text=f"已移至回收站: {path}")
            else:
                messagebox.showerror("依赖缺失", "需要 pip install send2trash")
        except Exception as e:
            messagebox.showerror("操作失败", str(e))
            logger.error("清理目录失败 %s: %s", path, e)

    def _permanently_delete_junk(self) -> None:
        assert self.tree_junk is not None
        sel: Tuple[str, ...] = self.tree_junk.selection()
        if not sel:
            return
        path: str = sel[0]
        if self.simulate_mode_var.get():
            messagebox.showinfo("模拟模式拦截", f"模拟彻底删除: {path}")
            return
        if not messagebox.askyesno(
            "警告", f"将彻底删除不可恢复:\n{path}\n\n确认吗？", icon="warning"
        ):
            return
        try:
            shutil.rmtree(path)
            self.tree_junk.delete(sel[0])
            self.update_disk_info()
            if self.status_label:
                self.status_label.config(text=f"已永久删除: {path}")
        except Exception as e:
            messagebox.showerror("删除失败", str(e))
            logger.error("永久删除失败 %s: %s", path, e)

    def update_disk_info(self) -> None:
        """更新头部指标卡片（总容量/已使用/可用）"""
        total_all: int = 0
        used_all: int = 0
        free_all: int = 0
        for partition in psutil.disk_partitions(all=False):
            if sys.platform == "win32":
                if "cdrom" in partition.opts or partition.fstype == "":
                    continue
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                total_all += usage.total
                used_all += usage.used
                free_all += usage.free
            except Exception as e:
                logger.debug("获取分区信息失败 %s: %s", partition.mountpoint, e)

        if total_all > 0:
            pct = int(used_all * 100 / total_all)
            self._update_header_metrics(
                total=format_size(total_all),
                used=f"{format_size(used_all)} ({pct}%)",
                free=format_size(free_all),
            )
            # P3: 更新使用率进度条
            if hasattr(self, '_usage_bar_id'):
                bar_x, bar_y, bar_w, bar_h = 24, 70, 200, 12
                fill_w = int(bar_w * pct / 100)
                # 颜色根据使用率变化
                if pct >= 90:
                    bar_color = Palette.DANGER
                elif pct >= 75:
                    bar_color = Palette.WARNING
                else:
                    bar_color = Palette.METRIC_FREE
                self._header_canvas.coords(self._usage_bar_id,
                                           bar_x, bar_y, bar_x + fill_w, bar_y + bar_h)
                self._header_canvas.itemconfig(self._usage_bar_id, fill=bar_color)
                self._header_canvas.itemconfig(self._usage_text_id, text=f"{pct}%")
        # 更新底部状态栏
        if hasattr(self, '_sb_left'):
            disk_info = " | ".join(
                f"{p.mountpoint} {psutil.disk_usage(p.mountpoint).percent}%"
                for p in psutil.disk_partitions(all=False)
                if not (sys.platform == "win32" and ("cdrom" in p.opts or p.fstype == ""))
            )
            self._sb_left.config(text=disk_info or "就绪")

    def _load_partitions(self) -> None:
        """Deferred partition loading to avoid blocking startup"""
        partitions: List[str] = []
        for p in psutil.disk_partitions(all=False):
            if sys.platform == "win32" and ("cdrom" in p.opts or p.fstype == ""):
                continue
            try:
                usage = psutil.disk_usage(p.mountpoint)
                pct = int(usage.used * 100 / usage.total)
                label = f"{p.mountpoint} ({pct}%)"
                partitions.append(label)
                self._partition_paths[label] = p.mountpoint
            except Exception:
                partitions.append(p.mountpoint)
        # Remove placeholder and create real OptionMenu
        if hasattr(self, "_partition_placeholder") and self._partition_placeholder:
            self._partition_placeholder.destroy()
        if partitions:
            self.partition_var.set(partitions[0])
            part_menu = ttk.OptionMenu(
                self._partition_toolbar, self.partition_var, partitions[0], *partitions,
                command=self._on_partition_selected,
            )
            part_menu.grid(row=0, column=5, padx=(0, 8))

    # P2: 分区选择回调
    def _on_partition_selected(self, value: str) -> None:
        mount = self._partition_paths.get(value, "")
        if mount:
            self.path_var.set(mount)

    def browse_path(self) -> None:
        path: str = filedialog.askdirectory(title="选择要扫描的根目录")
        if path:
            self.path_var.set(path)

    # ---- 扫描 ----
    def start_scan(self) -> None:
        target: str = self.path_var.get().strip()
        if not target or not os.path.exists(target):
            messagebox.showwarning("提示", "请输入有效的目录路径。")
            return

        if self.scan_thread and self.scan_thread.is_alive():
            if not messagebox.askyesno(
                "扫描中", "当前已有任务正在进行，是否强行中止并重新开始？"
            ):
                return
            self.stop_scan()
            self.scan_thread.join(timeout=1.0)

        for tree in (self.tree_dirs, self.tree_files, self.tree_ext, self.tree_junk, self.tree_age):
            if tree:
                tree.delete(*tree.get_children())
        assert self.tree_filetree is not None
        self.tree_filetree.delete(*self.tree_filetree.get_children())
        self._loaded_nodes.clear()
        self._loaded_cache = False

        self.progress_var.set(0)
        if self.status_label:
            self.status_label.config(text="正在分析文件树，请稍候...")

        self.config.last_scan_path = target
        self.config.top_n = self.top_n_var.get()
        self.config.save()

        self.scanner = FastScanner(
            enable_dup_detection=self.enable_dup_var.get(),
            target_path=target,
            top_n=self.config.top_n,
            exclude_dirs=self.config.exclude_dirs,
            custom_junk_dirs=self.config.custom_junk_dirs,
            progress_callback=self._update_progress,
            finish_callback=self._scan_finished,
            max_depth=MAX_DEPTH,
        )
        self.scan_thread = threading.Thread(target=self.scanner.scan, daemon=True)
        if self.scan_btn:
            self.scan_btn.config(state=tk.DISABLED, text="  ⏳ 扫描中...  ")
        if self.stop_btn:
            self.stop_btn.config(state=tk.NORMAL)
        self._scan_start_time = time.time()
        self._animate_scan_btn()
        self.scan_thread.start()

    def _animate_scan_btn(self) -> None:
        """Scan button text animation + live elapsed timer in status bar"""
        if not self.scan_thread or not self.scan_thread.is_alive():
            return
        elapsed = time.time() - getattr(self, "_scan_start_time", time.time())
        dots = "." * (int(elapsed * 2) % 4)
        if self.scan_btn:
            self.scan_btn.config(text=f"  ⏳ 扫描中{dots}  ")
        if self.status_label and elapsed > 1:
            current = self.status_label.cget("text")
            if current and not current.startswith("完成"):
                self.status_label.config(text=f"{current}  [{elapsed:.0f}s]")
        self.root.after(1000, self._animate_scan_btn)

    def stop_scan(self) -> None:
        if self.scanner:
            self.scanner.stop()
        if self.stop_btn:
            self.stop_btn.config(state=tk.DISABLED)
        if self.scan_btn:
            self.scan_btn.config(state=tk.NORMAL, text="  开始扫描  ")
        if self.status_label:
            self.status_label.config(text="扫描正在中断...")

    def _update_progress(self, msg: str, percent: int) -> None:
        now: float = time.time()
        if (
            now - self.last_update_time < PROGRESS_UPDATE_INTERVAL
            and percent < 100
            and percent != -1
        ):
            return
        self.last_update_time = now
        self.root.after(
            0,
            lambda m=msg: (self.status_label.config(text=m) if self.status_label else None),
        )
        if percent >= 0:
            if self.progress_bar and self.progress_bar.cget("mode") == "indeterminate":
                self.root.after(0, self.progress_bar.stop)
                self.root.after(
                    0,
                    lambda: (
                        self.progress_bar.config(mode="determinate")
                        if self.progress_bar
                        else None
                    ),
                )
            self.root.after(0, lambda p=percent: self.progress_var.set(p))
        else:
            if self.progress_bar and self.progress_bar.cget("mode") != "indeterminate":
                self.root.after(
                    0,
                    lambda: (
                        self.progress_bar.config(mode="indeterminate")
                        if self.progress_bar
                        else None
                    ),
                )
                self.root.after(0, self.progress_bar.start if self.progress_bar else lambda: None)

    def _scan_finished(
        self, result: Optional[ScanResult], error: Optional[str]
    ) -> None:
        def _on_done() -> None:
            if self.scan_btn:
                self.scan_btn.config(state=tk.NORMAL, text="  开始扫描  ")
            if self.stop_btn:
                self.stop_btn.config(state=tk.DISABLED)
            if self.progress_bar:
                self.progress_bar.stop()
                self.progress_bar.config(mode="determinate")
            self.progress_var.set(100)

            if error:
                messagebox.showerror("中断/错误", error)
                if self.status_label:
                    self.status_label.config(text="扫描已终止。")
                return

            if not result:
                return

            self._last_scan_result = result
            self._dir_size_cache = result.dir_size_cache
            self._populate_trees(result)
            self._populate_duplicates(result)
            self._draw_pie_chart(result.ext_stats)
            save_cache(result)
            self._save_size_cache()
            self._populate_filetree(self.path_var.get())

            # LLM 分析就绪提示
            if self._llm_btn:
                self._llm_btn.config(state=tk.NORMAL)
            if self._llm_status:
                if self.config.llm_configured:
                    self._llm_status.config(
                        text="扫描完成，可点击「🤖 开始 AI 分析」",
                        foreground=Palette.SUCCESS,
                    )
                else:
                    self._llm_status.config(
                        text="扫描完成。在「配置 → LLM 分析」中设置 API 后可使用 AI 分析",
                        foreground=Palette.TEXT_MUTED,
                    )

            if self.status_label:
                self.status_label.config(
                    text=(
                        f"完成 | 占用总计: {format_size(result.total_used)} | "
                        f"耗时: {result.scan_time:.1f}s | 检索: {result.scanned_items} 项"
                    )
                )
            self._update_header_metrics(scan=f"{result.scan_time:.1f}s")
            if hasattr(self, '_sb_right'):
                self._sb_right.config(
                    text=f"扫描 {result.scanned_items} 项 · {format_size(result.total_used)} · {result.scan_time:.1f}s"
                )

        self.root.after(0, _on_done)

    def _populate_trees(self, result: ScanResult) -> None:
        # Hide empty state overlay
        if hasattr(self, '_empty_overlay') and self._empty_overlay:
            self._empty_overlay.place_forget()
        assert self.tree_dirs is not None
        for i, (size, path) in enumerate(result.top_dirs):
            tag: str = "evenrow" if i % 2 == 0 else "oddrow"
            marker: str = (
                "[可清理] " if any(kw in path.lower() for kw in self.JUNK_KEYWORDS) else ""
            )
            # P1: 目录加图标
            self.tree_dirs.insert(
                "", tk.END, iid=path, values=(format_size(size), "📁 " + marker + path), tags=(tag,)
            )
        if not result.top_dirs:
            self.tree_dirs.insert(
                "", tk.END, iid="__empty_dirs__", values=("", "暂无数据"), tags=("oddrow",)
            )

        assert self.tree_files is not None
        for i, (size, path) in enumerate(result.top_files):
            tag = "evenrow" if i % 2 == 0 else "oddrow"
            try:
                mtime: str = datetime.fromtimestamp(os.path.getmtime(path)).strftime(
                    "%Y-%m-%d %H:%M"
                )
            except Exception:
                mtime = "未知"
            self.tree_files.insert(
                "", tk.END, iid=path, values=(format_size(size), mtime, "📄 " + path), tags=(tag,)
            )
        if not result.top_files:
            self.tree_files.insert(
                "",
                tk.END,
                iid="__empty_files__",
                values=("", "", "暂无数据"),
                tags=("oddrow",),
            )

        assert self.tree_ext is not None
        for i, (ext, size) in enumerate(result.ext_stats):
            tag = "evenrow" if i % 2 == 0 else "oddrow"
            self.tree_ext.insert(
                "",
                tk.END,
                values=(ext if ext else "(无后缀)", format_size(size)),
                tags=(tag,),
            )

        assert self.tree_junk is not None
        for i, (path, size) in enumerate(result.junk_dirs):
            tag = "evenrow" if i % 2 == 0 else "oddrow"
            self.tree_junk.insert(
                "", tk.END, iid=path, values=(format_size(size), "🗑️ " + path), tags=(tag,)
            )

        assert self.tree_age is not None
        age_data: Dict[str, Tuple[int, int]] = result.age_groups or {}
        for i, label in enumerate(AGE_GROUP_KEYS):
            tag = "evenrow" if i % 2 == 0 else "oddrow"
            info: Tuple[int, int] = age_data.get(label, (0, 0))
            cnt: int = info[0] if isinstance(info, (list, tuple)) else int(info)
            sz: int = (
                info[1]
                if isinstance(info, (list, tuple)) and len(info) > 1
                else 0
            )
            self.tree_age.insert(
                "",
                tk.END,
                values=(label, str(cnt), format_size(sz) if sz else "-"),
                tags=(tag,),
            )
        if not any(
            (
                (age_data.get(k, (0, 0))[0] if isinstance(age_data.get(k), (list, tuple)) else age_data.get(k, 0))
                for k in AGE_GROUP_KEYS
            )
        ):
            self.tree_age.insert(
                "", tk.END, values=("暂无数据", "", ""), tags=("oddrow",)
            )

    def _populate_duplicates(self, result: ScanResult) -> None:
        """P3: 填充重复文件 Tab"""
        assert self.tree_dup is not None
        # 清空
        for child in self.tree_dup.get_children():
            self.tree_dup.delete(child)

        duplicates = result.duplicates or []
        total_waste = 0
        total_groups = 0

        for size, paths in duplicates:
            count = len(paths)
            waste = size * (count - 1)
            total_waste += waste
            total_groups += 1

            for i, fpath in enumerate(paths):
                tag = "evenrow" if i % 2 == 0 else "oddrow"
                try:
                    self.tree_dup.insert(
                        "", tk.END, iid=fpath,
                        values=(
                            format_size(size),
                            str(count) if i == 0 else "",
                            format_size(waste) if i == 0 else "",
                            fpath,
                        ),
                        tags=(tag,),
                    )
                except tk.TclError:
                    pass

        # 更新统计
        if total_groups > 0:
            self.dup_summary.config(
                text=f"🔍 发现 {total_groups} 组重复文件，共浪费 {format_size(total_waste)} 磁盘空间",
                foreground=Palette.DANGER,
            )
        else:
            self.dup_summary.config(
                text="✅ 未发现重复文件" if HAS_XXHASH else "⚠️ xxhash 未安装，无法检测重复文件",
                foreground=Palette.SUCCESS if HAS_XXHASH else Palette.WARNING,
            )

    PIE_COLORS = [
        "#3B82F6", "#F59E0B", "#22C55E", "#EF4444", "#A855F7",
        "#EC4899", "#14B8A6", "#F97316", "#6366F1", "#84CC16",
    ]

    def _draw_pie_chart(self, ext_stats: List[Tuple[str, int]]) -> None:
        """P3: 绘制文件类型饼图"""
        c = self._pie_canvas
        c.delete("all")

        if not ext_stats:
            c.create_text(200, 100, text="暂无数据", fill=Palette.TEXT_MUTED, font=("", 10))
            return

        # 取 top 8 + 其他
        top_n = 8
        top = ext_stats[:top_n]
        other_size = sum(s for _, s in ext_stats[top_n:])
        total = sum(s for _, s in ext_stats)

        if total == 0:
            return

        # 准备数据
        slices = [(ext if ext else "(无后缀)", size) for ext, size in top]
        if other_size > 0:
            slices.append(("其他", other_size))

        # 饼图参数
        cx, cy, r = 80, 110, 80
        start_angle = 0

        for i, (label, size) in enumerate(slices):
            extent = 360 * size / total
            color = self.PIE_COLORS[i % len(self.PIE_COLORS)]

            # 画扇形
            c.create_arc(
                cx - r, cy - r, cx + r, cy + r,
                start=start_angle, extent=extent,
                fill=color, outline=Palette.FRAME_BG, width=2,
            )
            start_angle += extent

        # 图例
        legend_x = 190
        legend_y = 20
        for i, (label, size) in enumerate(slices):
            pct = size * 100 / total
            color = self.PIE_COLORS[i % len(self.PIE_COLORS)]
            y = legend_y + i * 22

            # 色块
            c.create_rectangle(legend_x, y, legend_x + 14, y + 14, fill=color, outline="")
            # 文字
            c.create_text(
                legend_x + 20, y + 7,
                text=f"{label}  {format_size(size)} ({pct:.1f}%)",
                anchor="w", font=("", 9), fill=Palette.TEXT,
            )

        # 总计
        c.create_text(
            legend_x, legend_y + len(slices) * 22 + 10,
            text=f"总计: {format_size(total)} ({len(ext_stats)} 种类型)",
            anchor="w", font=("", 9, "bold"), fill=Palette.TEXT,
        )

    def export_report(self) -> None:
        if self._loaded_cache:
            messagebox.showinfo("提示", "当前为缓存数据，请先执行一次新扫描后再导出。")
            return
        assert self.tree_dirs is not None
        if not self.tree_dirs.get_children():
            messagebox.showinfo("提示", "当前无分析数据，请先扫描。")
            return
        fp: str = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV文件", "*.csv"), ("JSON文件", "*.json")],
            title="导出扫描报告",
        )
        if not fp:
            return
        try:
            if fp.endswith(".json"):
                self._export_json(fp)
            else:
                self._export_csv(fp)
            messagebox.showinfo("导出成功", f"报告保存至:\n{fp}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))
            logger.error("导出报告失败: %s", e)

    def _export_json(self, fp: str) -> None:
        data: Dict[str, Any] = {
            "scan_time": datetime.now().isoformat(),
            "top_dirs": [],
            "top_files": [],
            "junk_dirs": [],
        }
        assert self.tree_dirs is not None
        for child in self.tree_dirs.get_children():
            vals: List[Any] = self.tree_dirs.item(child)["values"]
            if vals and vals[0]:
                data["top_dirs"].append({"size": vals[0], "path": str(vals[1])})
        assert self.tree_files is not None
        for child in self.tree_files.get_children():
            vals = self.tree_files.item(child)["values"]
            if vals and vals[0]:
                data["top_files"].append(
                    {"size": vals[0], "path": str(vals[2]), "mtime": vals[1]}
                )
        assert self.tree_junk is not None
        for child in self.tree_junk.get_children():
            vals = self.tree_junk.item(child)["values"]
            if vals and vals[0]:
                data["junk_dirs"].append({"size": vals[0], "path": str(vals[1])})
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _export_csv(self, fp: str) -> None:
        with open(fp, "w", encoding="utf-8-sig", newline="") as f:
            writer: csv.writer = csv.writer(f)
            writer.writerow(["类别", "大小", "路径/扩展名", "修改时间"])
            assert self.tree_dirs is not None
            for child in self.tree_dirs.get_children():
                vals: List[Any] = self.tree_dirs.item(child)["values"]
                if vals and vals[0]:
                    writer.writerow(["大目录", vals[0], str(vals[1]), ""])
            assert self.tree_files is not None
            for child in self.tree_files.get_children():
                vals = self.tree_files.item(child)["values"]
                if vals and vals[0]:
                    writer.writerow(["大文件", vals[0], str(vals[2]), vals[1]])
            assert self.tree_ext is not None
            for child in self.tree_ext.get_children():
                vals = self.tree_ext.item(child)["values"]
                writer.writerow(["文件类型", vals[1], vals[0], ""])
            assert self.tree_junk is not None
            for child in self.tree_junk.get_children():
                vals = self.tree_junk.item(child)["values"]
                if vals and vals[0]:
                    writer.writerow(["垃圾目录", vals[0], str(vals[1]), ""])

    def open_config_dialog(self) -> None:
        dlg: tk.Toplevel = tk.Toplevel(self.root)
        dlg.title("设置")
        dlg.geometry("560x480")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg=Palette.BG)
        dlg.minsize(480, 400)

        style = ttk.Style()
        style.configure("Card.TFrame", background=Palette.FRAME_BG)

        # Notebook with two tabs
        dlg_nb = ttk.Notebook(dlg)
        dlg_nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # ---- Tab 1: 排除目录 ----
        tab_exclude = ttk.Frame(dlg_nb, style="Card.TFrame")
        dlg_nb.add(tab_exclude, text="  排除目录  ")
        ttk.Label(tab_exclude, text="扫描时忽略以下目录前缀（每行一个）:").pack(
            anchor=tk.W, padx=10, pady=(10, 0)
        )
        text_area: tk.Text = tk.Text(
            tab_exclude,
            font=("", 10),
            bg=Palette.FRAME_BG,
            fg=Palette.TEXT,
            insertbackground=Palette.TEXT,
        )
        text_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        text_area.insert(1.0, "\n".join(self.config.exclude_dirs))

        # ---- Tab 2: LLM 配置 ----
        tab_llm = ttk.Frame(dlg_nb, style="Card.TFrame")
        dlg_nb.add(tab_llm, text="  LLM 分析  ")

        llm_frame = ttk.Frame(tab_llm, padding=16)
        llm_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            llm_frame,
            text="配置 OpenAI 兼容的 LLM API，用于智能分析扫描结果",
            foreground=Palette.TEXT_MUTED,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))

        ttk.Label(llm_frame, text="API 地址:").grid(
            row=1, column=0, sticky="w", pady=4
        )
        url_var = tk.StringVar(value=self.config.llm_api_url)
        url_entry = ttk.Entry(llm_frame, textvariable=url_var, width=48)
        url_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)

        ttk.Label(llm_frame, text="API Key:").grid(
            row=2, column=0, sticky="w", pady=4
        )
        key_var = tk.StringVar(value=self.config.llm_api_key)
        key_entry = ttk.Entry(llm_frame, textvariable=key_var, show="•", width=48)
        key_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)

        ttk.Label(llm_frame, text="模型名称:").grid(
            row=3, column=0, sticky="w", pady=4
        )
        model_var = tk.StringVar(value=self.config.llm_model)
        model_entry = ttk.Entry(llm_frame, textvariable=model_var, width=48)
        model_entry.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)

        ttk.Label(llm_frame, text="Temperature:").grid(
            row=4, column=0, sticky="w", pady=4
        )
        temp_var = tk.StringVar(value=str(self.config.llm_temperature))
        temp_entry = ttk.Entry(llm_frame, textvariable=temp_var, width=10)
        temp_entry.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=4)

        llm_frame.columnconfigure(1, weight=1)

        # 提示信息
        hint_text = (
            "常见配置示例：\n"
            "• OpenAI: https://api.openai.com/v1\n"
            "• DeepSeek: https://api.deepseek.com/v1\n"
            "• 本地 Ollama: http://localhost:11434/v1\n"
            "• 本地 LM Studio: http://localhost:1234/v1\n"
            "• Azure OpenAI: https://xxx.openai.azure.com/openai/deployments/xxx/v1\n\n"
            "支持所有 OpenAI Chat Completions 兼容 API。\n"
            "API Key 可留空（如本地模型无需认证）。"
        )
        hint = ttk.Label(
            llm_frame, text=hint_text, foreground=Palette.TEXT_MUTED, justify=tk.LEFT
        )
        hint.grid(row=5, column=0, columnspan=3, sticky="w", pady=(12, 0))

        # 测试连接按钮
        def test_connection() -> None:
            api_url = url_var.get().strip()
            api_key = key_var.get().strip()
            model = model_var.get().strip()
            if not api_url or not model:
                messagebox.showwarning("提示", "请填写 API 地址和模型名称", parent=dlg)
                return
            if not HAS_REQUESTS:
                messagebox.showerror("错误", "缺少 requests 库: pip install requests", parent=dlg)
                return
            try:
                test_url = api_url.rstrip("/")
                if not test_url.endswith("/models"):
                    test_url += "/models"
                headers: Dict[str, str] = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                resp = _requests.get(test_url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    messagebox.showinfo("连接成功", "API 连接正常！", parent=dlg)
                else:
                    messagebox.showwarning(
                        "连接异常",
                        f"HTTP {resp.status_code}\n{resp.text[:300]}",
                        parent=dlg,
                    )
            except Exception as e:
                messagebox.showerror("连接失败", str(e), parent=dlg)

        ttk.Button(llm_frame, text="测试连接", command=test_connection).grid(
            row=4, column=2, sticky="w", padx=(8, 0), pady=4
        )

        # ---- 保存 ----
        def save_conf() -> None:
            lines: List[str] = [
                line.strip()
                for line in text_area.get(1.0, tk.END).split("\n")
                if line.strip()
            ]
            self.config.exclude_dirs = lines
            self.config.llm_api_url = url_var.get().strip()
            self.config.llm_api_key = key_var.get().strip()
            self.config.llm_model = model_var.get().strip()
            try:
                self.config.llm_temperature = float(temp_var.get())
            except ValueError:
                self.config.llm_temperature = 0.3
            self.config.save()
            dlg.destroy()

        btn_frame = ttk.Frame(dlg, padding=(10, 0, 10, 10))
        btn_frame.pack(fill=tk.X)
        ttk.Button(
            btn_frame, text="保存并关闭", command=save_conf, style="Accent.TButton"
        ).pack(side=tk.RIGHT)
        dlg.protocol("WM_DELETE_WINDOW", lambda: (self.config.save(), dlg.destroy()))
        dlg.bind("<Escape>", lambda e: (self.config.save(), dlg.destroy()))


    def _delete_selected_dup(self, recycle: bool = True) -> None:
        assert self.tree_dup is not None
        sel: Tuple[str, ...] = self.tree_dup.selection()
        if not sel:
            return
        # The path is the last column (index 3)
        values = self.tree_dup.item(sel[0], "values")
        path: str = values[3]  # 注意：values 是一个元组，顺序为 (size, count, waste, path)
        if self.simulate_mode_var.get():
            action: str = "移至回收站" if recycle else "永久删除"
            messagebox.showinfo(
                "模拟模式拦截",
                f"欲执行: {action}\n目标: {path}\n当前为预览模式，无真实变动。",
            )
            return
        confirm_msg: str = (
            f"确定要删除此文件吗？\n{path}\n此操作{'将移入回收站' if recycle else '不可撤销'}！"
        )
        if not messagebox.askyesno("危险操作确认", confirm_msg):
            return
        try:
            if recycle and HAS_SEND2TRASH:
                send2trash.send2trash(path)
            else:
                os.remove(path)
            # 删除整个行（因为重复文件列表按组显示，删除一个文件后，该组可能不再完整，所以直接移除整行）
            self.tree_dup.delete(sel[0])
            self.update_disk_info()
            if self.status_label:
                self.status_label.config(text=f"已删除: {path}")
        except Exception as e:
            messagebox.showerror("删除失败", str(e))
            logger.error("删除文件失败 %s: %s", path, e)

# ========== 入口 ==========
def main() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root: tk.Tk = tk.Tk()
    app: DiskAnalyzerApp = DiskAnalyzerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
