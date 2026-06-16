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

# Windows: 强制 UTF-8 输出，避免中文乱码
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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
    # ── Header (渐变靛蓝头部) ──
    HEADER_BG: str = "#4F46E5"
    HEADER_ACCENT: str = "#6366F1"
    HEADER_TEXT: str = "#FFFFFF"
    HEADER_SUBTITLE: str = "#C7D2FE"
    # ── Base (现代浅灰白背景) ──
    BG: str = "#F8F9FB"
    FRAME_BG: str = "#FFFFFF"
    SURFACE: str = "#FFFFFF"
    SURFACE_ALT: str = "#F3F4F6"
    TEXT: str = "#1A1F2E"
    TEXT_MUTED: str = "#6B7280"
    FG_SECONDARY: str = "#6B7280"
    PRIMARY: str = "#4F46E5"
    PRIMARY_LIGHT: str = "#EEF2FF"
    BORDER: str = "#E5E7EB"
    BORDER_STRONG: str = "#D1D5DB"
    STRIPE_ODD: str = "#FFFFFF"
    STRIPE_EVEN: str = "#F9FAFB"
    HIGHLIGHT: str = "#E0E7FF"
    DANGER: str = "#EF4444"
    SUCCESS: str = "#10B981"
    WARNING: str = "#F59E0B"
    # ── Buttons ──
    BUTTON_BG: str = "#F3F4F6"
    BUTTON_ACTIVE: str = "#E5E7EB"
    BUTTON_DISABLED: str = "#F9FAFB"
    ACCENT_HOVER: str = "#4338CA"
    ACCENT_DISABLED: str = "#A5B4FC"
    ACCENT_SOFT: str = "#EEF2FF"
    DANGER_HOVER: str = "#DC2626"
    SUCCESS_SOFT: str = "#ECFDF5"
    WARNING_SOFT: str = "#FFFBEB"
    DANGER_SOFT: str = "#FEF2F2"
    # ── Tree ──
    TREE_HEADING_BG: str = "#F9FAFB"
    TREE_HEADING_ACTIVE: str = "#E5E7EB"
    TROUGH: str = "#E5E7EB"
    NOTEBOOK_TAB_BG: str = "#F3F4F6"
    # ── Sidebar (深色侧栏，两主题一致) ──
    SIDEBAR_BG: str = "#111827"
    SIDEBAR_FG: str = "#E5E7EB"
    SIDEBAR_MUTED: str = "#6B7280"
    SIDEBAR_ACTIVE: str = "#818CF8"
    SIDEBAR_HOVER: str = "#1F2937"
    # ── Metric cards ──
    METRIC_TOTAL: str = "#4F46E5"
    METRIC_USED: str = "#F59E0B"
    METRIC_FREE: str = "#10B981"
    METRIC_SCAN: str = "#8B5CF6"
    # ── Chart colors ──
    CHART_1: str = "#4F46E5"
    CHART_2: str = "#0D9488"
    CHART_3: str = "#D97706"
    CHART_4: str = "#DC2626"
    CHART_5: str = "#7E22CE"
    CHART_6: str = "#16679A"
    CHART_7: str = "#84CC16"


class DarkPalette:
    HEADER_BG: str = "#312E81"
    HEADER_ACCENT: str = "#4338CA"
    HEADER_TEXT: str = "#F9FAFB"
    HEADER_SUBTITLE: str = "#C7D2FE"
    BG: str = "#111827"
    FRAME_BG: str = "#1F2937"
    SURFACE: str = "#1F2937"
    SURFACE_ALT: str = "#161E2E"
    TEXT: str = "#F9FAFB"
    TEXT_MUTED: str = "#9CA3AF"
    FG_SECONDARY: str = "#8B95A9"
    PRIMARY: str = "#818CF8"
    PRIMARY_LIGHT: str = "#1E1B4B"
    BORDER: str = "#374151"
    BORDER_STRONG: str = "#4B5563"
    STRIPE_ODD: str = "#1F2937"
    STRIPE_EVEN: str = "#161E2E"
    HIGHLIGHT: str = "#1E1B4B"
    DANGER: str = "#F87171"
    SUCCESS: str = "#34D399"
    WARNING: str = "#FBBF24"
    BUTTON_BG: str = "#374151"
    BUTTON_ACTIVE: str = "#4B5563"
    BUTTON_DISABLED: str = "#1F2937"
    ACCENT_HOVER: str = "#A5B4FC"
    ACCENT_DISABLED: str = "#374151"
    ACCENT_SOFT: str = "#1E1B4B"
    DANGER_HOVER: str = "#FCA5A5"
    SUCCESS_SOFT: str = "#064E3B"
    WARNING_SOFT: str = "#78350F"
    DANGER_SOFT: str = "#7F1D1D"
    TREE_HEADING_BG: str = "#1F2937"
    TREE_HEADING_ACTIVE: str = "#374151"
    TROUGH: str = "#374151"
    NOTEBOOK_TAB_BG: str = "#1F2937"
    SIDEBAR_BG: str = "#0D1117"
    SIDEBAR_FG: str = "#D1D5DB"
    SIDEBAR_MUTED: str = "#6B7280"
    SIDEBAR_ACTIVE: str = "#A5B4FC"
    SIDEBAR_HOVER: str = "#161B22"
    METRIC_TOTAL: str = "#818CF8"
    METRIC_USED: str = "#FBBF24"
    METRIC_FREE: str = "#34D399"
    METRIC_SCAN: str = "#C084FC"
    CHART_1: str = "#818CF8"
    CHART_2: str = "#2DD4BF"
    CHART_3: str = "#FBBF24"
    CHART_4: str = "#F87171"
    CHART_5: str = "#C084FC"
    CHART_6: str = "#22D3EE"
    CHART_7: str = "#A3E635"


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


# ========== 自定义控件 ==========
class RoundedFrame(tk.Canvas):
    """带圆角背景的容器控件，内部放置 .inner Frame 供子控件布局。"""

    def __init__(
        self,
        parent: tk.Widget,
        radius: int = 10,
        bg: str = "#FFFFFF",
        border_color: str = "#E5E7EB",
        border_width: int = 1,
        **kwargs: Any,
    ) -> None:
        self._radius = radius
        self._bg = bg
        self._border_color = border_color
        self._border_width = border_width
        super().__init__(
            parent,
            highlightthickness=0,
            bd=0,
            bg=parent["bg"] if hasattr(parent, "__getitem__") else bg,
            **kwargs,
        )
        self.inner = ttk.Frame(self, style="Card.TFrame")
        self._win_id = self.create_window(0, 0, window=self.inner, anchor="nw")
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, event: tk.Event) -> None:
        self._draw(event.width, event.height)
        self.coords(self._win_id, event.width // 2, event.height // 2)
        self.itemconfigure(self._win_id, width=event.width, height=event.height)

    def _draw(self, w: int, h: int) -> None:
        self.delete("bg")
        r = self._radius
        if w < 2 * r or h < 2 * r:
            r = min(w, h) // 2
        points: List[int] = []
        for x, y in [(r, r), (w - r, r), (w - r, h - r), (r, h - r)]:
            points.extend([x, y])
        # 四个圆角弧
        self.create_arc(
            0, 0, 2 * r, 2 * r, start=90, extent=90, fill=self._bg,
            outline=self._border_color, width=self._border_width, tags="bg",
        )
        self.create_arc(
            w - 2 * r, 0, w, 2 * r, start=0, extent=90, fill=self._bg,
            outline=self._border_color, width=self._border_width, tags="bg",
        )
        self.create_arc(
            w - 2 * r, h - 2 * r, w, h, start=270, extent=90, fill=self._bg,
            outline=self._border_color, width=self._border_width, tags="bg",
        )
        self.create_arc(
            0, h - 2 * r, 2 * r, h, start=180, extent=90, fill=self._bg,
            outline=self._border_color, width=self._border_width, tags="bg",
        )
        # 填充矩形（覆盖弧之间的空白）
        self.create_rectangle(
            r, 0, w - r, h, fill=self._bg, outline=self._bg, tags="bg",
        )
        self.create_rectangle(
            0, r, w, h - r, fill=self._bg, outline=self._bg, tags="bg",
        )
        # 边框线
        if self._border_width > 0:
            self.create_line(r, 0, w - r, 0, fill=self._border_color, width=self._border_width, tags="bg")
            self.create_line(r, h, w - r, h, fill=self._border_color, width=self._border_width, tags="bg")
            self.create_line(0, r, 0, h - r, fill=self._border_color, width=self._border_width, tags="bg")
            self.create_line(w, r, w, h - r, fill=self._border_color, width=self._border_width, tags="bg")

    def configure_colors(self, bg: str, border_color: str) -> None:
        self._bg = bg
        self._border_color = border_color
        w, h = self.winfo_width(), self.winfo_height()
        if w > 1 and h > 1:
            self._draw(w, h)


class ToggleSwitch(tk.Canvas):
    """仿 iOS 风格的开关控件。"""

    def __init__(
        self,
        parent: tk.Widget,
        variable: tk.BooleanVar,
        command: Optional[Callable] = None,
        **kwargs: Any,
    ) -> None:
        self._var = variable
        self._command = command
        self._on_color = "#4F46E5"
        self._off_color = "#D1D5DB"
        self._knob_color = "#FFFFFF"
        super().__init__(
            parent, width=40, height=22, highlightthickness=0, bd=0,
            bg=parent["bg"] if hasattr(parent, "__getitem__") else "#FFFFFF",
            **kwargs,
        )
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        on = self._var.get()
        color = self._on_color if on else self._off_color
        # 圆角药丸背景
        self.create_oval(2, 2, 22, 20, fill=color, outline=color)
        self.create_oval(18, 2, 38, 20, fill=color, outline=color)
        self.create_rectangle(11, 2, 29, 20, fill=color, outline=color)
        # 圆形旋钮
        kx = 28 if on else 10
        self.create_oval(kx, 3, kx + 16, 19, fill=self._knob_color, outline=self._knob_color)

    def _on_click(self, _event: tk.Event) -> None:
        self._var.set(not self._var.get())
        self._draw()
        if self._command:
            self._command()

    def set_colors(self, on_color: str, off_color: str) -> None:
        self._on_color = on_color
        self._off_color = off_color
        self._draw()


class ScrollableFrame(tk.Frame):
    """可垂直滚动的 Frame 容器。"""

    def __init__(self, parent: tk.Widget, **kwargs: Any) -> None:
        super().__init__(parent, **kwargs)
        self._canvas = tk.Canvas(
            self, highlightthickness=0, bd=0,
            bg=kwargs.get("bg", "#FFFFFF"),
        )
        self._scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self.inner = ttk.Frame(self._canvas, style="Card.TFrame")
        self.inner.bind("<Configure>", self._on_frame_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._win_id = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self._canvas.configure(yscrollcommand=self._scrollbar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._scrollbar.pack(side="right", fill="y")
        # 鼠标滚轮绑定
        self.inner.bind("<Enter>", self._bind_mousewheel)
        self.inner.bind("<Leave>", self._unbind_mousewheel)

    def _on_frame_configure(self, _event: tk.Event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self._canvas.itemconfigure(self._win_id, width=event.width)

    def _bind_mousewheel(self, _event: tk.Event) -> None:
        if sys.platform == "win32":
            self._canvas.bind_all("<MouseWheel>", self._on_mousewheel_win)
        else:
            self._canvas.bind_all("<Button-4>", self._on_mousewheel_up)
            self._canvas.bind_all("<Button-5>", self._on_mousewheel_down)

    def _unbind_mousewheel(self, _event: tk.Event) -> None:
        if sys.platform == "win32":
            self._canvas.unbind_all("<MouseWheel>")
        else:
            self._canvas.unbind_all("<Button-4>")
            self._canvas.unbind_all("<Button-5>")

    def _on_mousewheel_win(self, event: tk.Event) -> None:
        self._canvas.yview_scroll(-1 * (event.delta // 120), "units")

    def _on_mousewheel_up(self, _event: tk.Event) -> None:
        self._canvas.yview_scroll(-1, "units")

    def _on_mousewheel_down(self, _event: tk.Event) -> None:
        self._canvas.yview_scroll(1, "units")

    def update_bg(self, bg: str) -> None:
        self._canvas.configure(bg=bg)


# ========== GUI ==========
class DiskAnalyzerApp:
    JUNK_KEYWORDS: Tuple[str, ...] = ("log", "backup", "cache", "temp")

    # 面板名称列表
    PANEL_NAMES: List[str] = [
        "overview", "large_files", "duplicates", "temp_files",
        "ai_clean", "history", "settings",
    ]
    PANEL_TITLES: Dict[str, str] = {
        "overview": "概览",
        "large_files": "大文件",
        "duplicates": "重复文件",
        "temp_files": "临时文件",
        "ai_clean": "智能清理",
        "history": "历史记录",
        "settings": "设置",
    }

    def __init__(self, root: tk.Tk) -> None:
        self.root: tk.Tk = root
        self.root.title("磁盘空间分析工具 v2 - 清理助手")
        self.root.geometry("1500x900")
        self.root.minsize(1200, 700)

        self.config: Config = Config()
        self.scanner: Optional[FastScanner] = None
        self.scan_thread: Optional[threading.Thread] = None
        self.last_update_time: float = 0
        self._last_progress_msg: str = ""
        self._last_scan_result: Optional[ScanResult] = None
        self._dir_size_cache: Dict[str, int] = {}
        self._loaded_cache: bool = False
        self._theme_name: str = "light"
        self._current_panel: str = "overview"

        # Treeview widgets
        self.tree_top_dirs: Optional[ttk.Treeview] = None
        self.tree_large_files: Optional[ttk.Treeview] = None
        self.tree_junk: Optional[ttk.Treeview] = None
        self.tree_dup: Optional[ttk.Treeview] = None
        self._detached_parent_map: Dict[str, str] = {}
        self._last_sort_col: Dict[ttk.Treeview, str] = {}
        self._last_sort_reverse: Dict[ttk.Treeview, bool] = {}

        # Scan controls
        self.path_var: tk.StringVar = tk.StringVar()
        self.path_entry: Optional[ttk.Entry] = None
        self.scan_btn: Optional[ttk.Button] = None
        self.stop_btn: Optional[ttk.Button] = None
        self._rescan_btn: Optional[ttk.Button] = None
        self.simulate_mode_var: tk.BooleanVar = tk.BooleanVar(value=False)
        self.enable_dup_var: tk.BooleanVar = tk.BooleanVar(value=False)
        self.top_n_var: tk.IntVar = tk.IntVar(value=self.config.top_n)
        self.progress_var: tk.IntVar = tk.IntVar()
        self.progress_bar: Optional[ttk.Progressbar] = None
        self.status_label: Optional[ttk.Label] = None
        self.theme_btn: Optional[ttk.Button] = None

        # Sidebar nav buttons
        self._nav_buttons: Dict[str, tk.Frame] = {}
        self._nav_labels: Dict[str, tk.Label] = {}
        self._nav_bars: Dict[str, tk.Canvas] = {}

        # Panels
        self._panels: Dict[str, ttk.Frame] = {}
        self._sidebar: Optional[tk.Frame] = None
        self._sidebar_labels: List[tk.Label] = []
        self._topbar_title: Optional[ttk.Label] = None
        self._topbar_sub: Optional[ttk.Label] = None

        # Overview stat cards
        self._stat_labels: Dict[str, ttk.Label] = {}
        self._donut_canvas: Optional[tk.Canvas] = None
        self._health_bars: Dict[str, tk.Canvas] = {}

        # LLM
        self._llm_text: Optional[tk.Text] = None
        self._llm_btn: Optional[ttk.Button] = None
        self._llm_stop_btn: Optional[ttk.Button] = None
        self._llm_analyzer: Optional[LLMAnalyzer] = None
        self._llm_streaming: bool = False
        self._llm_line_buffer: str = ""
        self._llm_status: Optional[ttk.Label] = None
        self._context_menus: List[tk.Menu] = []

        # History
        self._history_file: str = os.path.join(
            os.path.expanduser("~/.disk_analyzer"), "history.json"
        )

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
                if "current_panel" in data:
                    self._show_panel(data["current_panel"])
        except Exception:
            pass

    def _save_window_geometry(self) -> None:
        """保存当前窗口几何和状态"""
        try:
            data = {
                "geometry": self.root.geometry(),
                "current_panel": self._current_panel,
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
        style.configure("Sidebar.TFrame", background=Palette.SIDEBAR_BG)
        style.configure("Topbar.TFrame", background=Palette.FRAME_BG)
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
            borderwidth=1,
            foreground=Palette.TEXT,
            font=(base_font[0], 9),
            bordercolor=Palette.BORDER,
        )
        style.map(
            "TButton",
            background=[
                ("active", Palette.BUTTON_ACTIVE),
                ("disabled", Palette.BUTTON_DISABLED),
            ],
            bordercolor=[("active", Palette.BORDER)],
        )
        style.configure(
            "Accent.TButton",
            background=Palette.PRIMARY,
            foreground="white",
            font=(base_font[0], 10, "bold"),
            padding=(20, 7),
            borderwidth=0,
            bordercolor=Palette.PRIMARY,
        )
        style.map(
            "Accent.TButton",
            background=[
                ("active", Palette.ACCENT_HOVER),
                ("disabled", Palette.ACCENT_DISABLED),
            ],
            bordercolor=[("active", Palette.ACCENT_HOVER)],
        )
        style.configure("Danger.TButton", background=Palette.DANGER, foreground="white", borderwidth=0)
        style.map("Danger.TButton", background=[("active", Palette.DANGER_HOVER)])

        style.configure(
            "Modern.Horizontal.TProgressbar",
            background=Palette.PRIMARY,
            troughcolor=Palette.TROUGH,
            bordercolor=Palette.BG,
            lightcolor=Palette.PRIMARY,
            darkcolor=Palette.PRIMARY,
            thickness=8,
        )

        style.configure(
            "Treeview",
            rowheight=32,
            font=tree_font,
            background=Palette.FRAME_BG,
            fieldbackground=Palette.FRAME_BG,
            borderwidth=0,
            foreground=Palette.TEXT,
        )
        style.map(
            "Treeview",
            background=[("selected", Palette.HIGHLIGHT)],
            foreground=[("selected", Palette.PRIMARY)],
        )
        style.configure(
            "Treeview.Heading",
            font=(base_font[0], 9, "bold"),
            background=Palette.TREE_HEADING_BG,
            foreground=Palette.TEXT_MUTED,
            relief="flat",
            padding=(8, 8),
        )
        style.map("Treeview.Heading", background=[("active", Palette.TREE_HEADING_ACTIVE)])

        style.configure("Vertical.TScrollbar", gripcount=0, background=Palette.BORDER,
                        troughcolor=Palette.BG, arrowcolor=Palette.TEXT_MUTED, width=10)
        style.configure("Horizontal.TScrollbar", gripcount=0, background=Palette.BORDER,
                        troughcolor=Palette.BG, arrowcolor=Palette.TEXT_MUTED, height=10)

        style.configure("Modern.TEntry",
                        fieldbackground=Palette.FRAME_BG,
                        foreground=Palette.TEXT,
                        bordercolor=Palette.BORDER,
                        lightcolor=Palette.BORDER,
                        darkcolor=Palette.BORDER,
                        padding=8)
        style.map("Modern.TEntry",
                  fieldbackground=[("focus", Palette.FRAME_BG)],
                  foreground=[("focus", Palette.TEXT)],
                  bordercolor=[("focus", Palette.PRIMARY)])

        # Tag styles for badges
        style.configure("Tag.TLabel", font=(base_font[0], 8), padding=(6, 2))
        style.configure("TagAccent.TLabel", background=Palette.ACCENT_SOFT, foreground=Palette.PRIMARY)
        style.configure("TagSuccess.TLabel", background=Palette.SUCCESS_SOFT, foreground=Palette.SUCCESS)
        style.configure("TagWarning.TLabel", background=Palette.WARNING_SOFT, foreground=Palette.WARNING)
        style.configure("TagDanger.TLabel", background=Palette.DANGER_SOFT, foreground=Palette.DANGER)
        style.configure("StatValue.TLabel", font=(base_font[0], 20, "bold"), foreground=Palette.TEXT)
        style.configure("StatLabel.TLabel", font=(base_font[0], 9), foreground=Palette.TEXT_MUTED)
        style.configure("Nav.TLabel", font=(base_font[0], 10), foreground=Palette.SIDEBAR_FG,
                        background=Palette.SIDEBAR_BG)
        style.configure("NavActive.TLabel", font=(base_font[0], 10, "bold"), foreground=Palette.SIDEBAR_ACTIVE,
                        background=Palette.SIDEBAR_BG)

    def _create_widgets(self) -> None:
        # ================================================================
        # 1. 侧栏 (深色，固定宽度 220px)
        # ================================================================
        self._sidebar = tk.Frame(self.root, width=220, bg=Palette.SIDEBAR_BG)
        self._sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self._sidebar.pack_propagate(False)

        # Logo 区域
        logo_frame = tk.Frame(self._sidebar, bg=Palette.SIDEBAR_BG)
        logo_frame.pack(fill=tk.X, padx=16, pady=(20, 16))
        logo_icon = tk.Canvas(logo_frame, width=32, height=32, bg=Palette.SIDEBAR_BG,
                              highlightthickness=0, bd=0)
        logo_icon.pack(side=tk.LEFT)
        # 画一个简化的磁盘图标
        logo_icon.create_oval(2, 2, 30, 30, fill=Palette.PRIMARY, outline=Palette.SIDEBAR_ACTIVE, width=2)
        logo_icon.create_text(16, 16, text="D", fill="white", font=("", 12, "bold"))
        logo_text_frame = tk.Frame(logo_frame, bg=Palette.SIDEBAR_BG)
        logo_text_frame.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(logo_text_frame, text="磁盘分析器", font=("", 13, "bold"),
                 fg=Palette.SIDEBAR_FG, bg=Palette.SIDEBAR_BG).pack(anchor="w")
        tk.Label(logo_text_frame, text="v2.0", font=("", 8),
                 fg=Palette.SIDEBAR_MUTED, bg=Palette.SIDEBAR_BG).pack(anchor="w")

        # 导航区域
        nav_sections = [
            ("存储分析", [
                ("overview", "概览", "📊"),
                ("large_files", "大文件", "📁"),
                ("duplicates", "重复文件", "🔍"),
                ("temp_files", "临时文件", "🗑️"),
            ]),
            ("智能工具", [
                ("ai_clean", "智能清理", "🤖"),
                ("history", "历史记录", "📅"),
                ("settings", "设置", "⚙️"),
            ]),
        ]

        for section_title, items in nav_sections:
            # 分区标题
            sec_label = tk.Label(self._sidebar, text=section_title, font=("", 8),
                                 fg=Palette.SIDEBAR_MUTED, bg=Palette.SIDEBAR_BG,
                                 anchor="w", padx=16)
            sec_label.pack(fill=tk.X, pady=(16, 4))
            self._sidebar_labels.append(sec_label)

            for panel_name, label_text, icon in items:
                btn_frame = tk.Frame(self._sidebar, bg=Palette.SIDEBAR_BG, cursor="hand2")
                btn_frame.pack(fill=tk.X, padx=8, pady=1)

                # 左侧活跃指示条
                bar = tk.Canvas(btn_frame, width=3, height=32, bg=Palette.SIDEBAR_BG,
                                highlightthickness=0, bd=0)
                bar.pack(side=tk.LEFT, padx=(0, 0))

                # 图标+文字
                nav_label = tk.Label(
                    btn_frame, text=f" {icon}  {label_text}", font=("", 10),
                    fg=Palette.SIDEBAR_FG, bg=Palette.SIDEBAR_BG,
                    anchor="w", padx=12, pady=6,
                )
                nav_label.pack(fill=tk.X)

                self._nav_buttons[panel_name] = btn_frame
                self._nav_labels[panel_name] = nav_label
                self._nav_bars[panel_name] = bar

                # 绑定点击事件
                for widget in (btn_frame, nav_label):
                    widget.bind("<Button-1>", lambda e, p=panel_name: self._show_panel(p))
                    widget.bind("<Enter>", lambda e, p=panel_name: self._nav_hover(p, True))
                    widget.bind("<Leave>", lambda e, p=panel_name: self._nav_hover(p, False))

        # 底部空间
        spacer = tk.Frame(self._sidebar, bg=Palette.SIDEBAR_BG)
        spacer.pack(fill=tk.BOTH, expand=True)

        # 底部版本信息
        footer = tk.Frame(self._sidebar, bg=Palette.SIDEBAR_BG)
        footer.pack(fill=tk.X, padx=16, pady=(0, 16))
        tk.Label(footer, text="磁盘空间分析工具", font=("", 8),
                 fg=Palette.SIDEBAR_MUTED, bg=Palette.SIDEBAR_BG).pack(anchor="w")
        tk.Label(footer, text="v2.0 · Python/Tkinter", font=("", 7),
                 fg=Palette.SIDEBAR_MUTED, bg=Palette.SIDEBAR_BG).pack(anchor="w")

        # ================================================================
        # 2. 主区域 (顶栏 + 内容面板)
        # ================================================================
        main_area = ttk.Frame(self.root)
        main_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 顶栏
        topbar = ttk.Frame(main_area, style="Topbar.TFrame", padding=(20, 12, 20, 12))
        topbar.pack(fill=tk.X)
        topbar_left = ttk.Frame(topbar, style="Topbar.TFrame")
        topbar_left.pack(side=tk.LEFT)
        self._topbar_title = ttk.Label(topbar_left, text="概览", font=("", 16, "bold"),
                                       foreground=Palette.TEXT, background=Palette.FRAME_BG)
        self._topbar_title.pack(anchor="w")
        self._topbar_sub = ttk.Label(topbar_left, text="快速了解磁盘空间使用情况",
                                     font=("", 9), foreground=Palette.TEXT_MUTED,
                                     background=Palette.FRAME_BG)
        self._topbar_sub.pack(anchor="w")

        topbar_right = ttk.Frame(topbar, style="Topbar.TFrame")
        topbar_right.pack(side=tk.RIGHT)

        self.theme_btn = ttk.Button(topbar_right, text="🌙", width=4, command=self._toggle_theme)
        self.theme_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self._rescan_btn = ttk.Button(
            topbar_right, text="  重新扫描  ", command=self.start_scan, style="Accent.TButton"
        )
        self._rescan_btn.pack(side=tk.RIGHT, padx=(8, 0))

        # 扫描路径行
        path_frame = ttk.Frame(main_area, padding=(20, 4, 20, 4))
        path_frame.pack(fill=tk.X)
        path_frame.columnconfigure(1, weight=1)
        ttk.Label(path_frame, text="路径:", font=("", 9, "bold")).grid(
            row=0, column=0, padx=(0, 6), sticky="w"
        )
        self.path_entry = ttk.Entry(path_frame, textvariable=self.path_var, font=("", 10))
        self.path_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.path_entry.bind("<Return>", lambda e: self.start_scan())
        self.root.bind("<F5>", lambda e: self.start_scan())
        self.root.bind("<Control-o>", lambda e: self.browse_path())
        self.root.bind("<Escape>", lambda e: self.stop_scan())

        self.scan_btn = ttk.Button(
            path_frame, text="  开始扫描 (F5)  ", command=self.start_scan, style="Accent.TButton"
        )
        self.scan_btn.grid(row=0, column=2, padx=(0, 4))
        self.stop_btn = ttk.Button(
            path_frame, text="停止 (Esc)", command=self.stop_scan, state=tk.DISABLED
        )
        self.stop_btn.grid(row=0, column=3, padx=(0, 4))
        ttk.Button(path_frame, text="浏览", command=self.browse_path).grid(row=0, column=4)

        # 分区选择器
        self.partition_var = tk.StringVar(value="加载中...")
        self._partition_paths: Dict[str, str] = {}
        self._partition_toolbar = path_frame
        self._partition_next_col = 6
        part_placeholder = ttk.Label(path_frame, text="加载分区中...", font=("", 9))
        part_placeholder.grid(row=0, column=5, padx=(8, 0), sticky="w")
        self._partition_placeholder = part_placeholder
        self.root.after(200, self._load_partitions)

        # 进度条
        progress_frame = ttk.Frame(main_area, padding=(20, 2, 20, 2))
        progress_frame.pack(fill=tk.X)
        self.progress_bar = ttk.Progressbar(
            progress_frame, variable=self.progress_var, maximum=100,
            style="Modern.Horizontal.TProgressbar",
        )
        self.progress_bar.pack(fill=tk.X)
        self.status_label = ttk.Label(
            progress_frame, text="就绪", font=("", 9), foreground=Palette.TEXT_MUTED
        )
        self.status_label.pack(fill=tk.X, pady=(2, 0))

        # ================================================================
        # 3. 内容面板区域
        # ================================================================
        self._content_frame = ttk.Frame(main_area)
        self._content_frame.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        # 创建所有面板（初始隐藏）
        self._panels["overview"] = ttk.Frame(self._content_frame, style="Card.TFrame")
        self._panels["large_files"] = ttk.Frame(self._content_frame, style="Card.TFrame")
        self._panels["duplicates"] = ttk.Frame(self._content_frame, style="Card.TFrame")
        self._panels["temp_files"] = ttk.Frame(self._content_frame, style="Card.TFrame")
        self._panels["ai_clean"] = ttk.Frame(self._content_frame, style="Card.TFrame")
        self._panels["history"] = ttk.Frame(self._content_frame, style="Card.TFrame")
        self._panels["settings"] = ttk.Frame(self._content_frame, style="Card.TFrame")

        self._create_panel_overview()
        self._create_panel_large_files()
        self._create_panel_duplicates()
        self._create_panel_temp_files()
        self._create_panel_ai_clean()
        self._create_panel_history()
        self._create_panel_settings()

        self._create_context_menus()

        # 显示默认面板
        self._show_panel("overview")

        # 面板切换快捷键 Ctrl+1~7
        for i, name in enumerate(self.PANEL_NAMES):
            self.root.bind(f"<Control-Key-{i + 1}>", lambda e, p=name: self._show_panel(p))

        # 底部状态栏
        self._status_bar = ttk.Frame(main_area, padding=(16, 4))
        self._status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Separator(self._status_bar, orient=tk.HORIZONTAL).pack(fill=tk.X)
        status_inner = ttk.Frame(self._status_bar)
        status_inner.pack(fill=tk.X, pady=(4, 0))
        self._sb_left = ttk.Label(status_inner, text="就绪", font=("", 8), foreground=Palette.TEXT_MUTED)
        self._sb_left.pack(side=tk.LEFT)
        self._sb_right = ttk.Label(
            status_inner, text="F5 扫描 | Ctrl+O 浏览 | Esc 停止",
            font=("", 8), foreground=Palette.TEXT_MUTED,
        )
        self._sb_right.pack(side=tk.RIGHT)

    # ---- 侧栏导航 ----
    def _show_panel(self, name: str) -> None:
        """切换到指定面板"""
        if name not in self._panels:
            return
        for panel in self._panels.values():
            panel.pack_forget()
        self._panels[name].pack(fill=tk.BOTH, expand=True)
        self._current_panel = name
        # 更新导航按钮样式
        for p, label in self._nav_labels.items():
            if p == name:
                label.configure(fg=Palette.SIDEBAR_ACTIVE, font=("", 10, "bold"))
                self._nav_bars[p].delete("all")
                self._nav_bars[p].create_rectangle(0, 6, 3, 26, fill=Palette.SIDEBAR_ACTIVE, outline="")
                self._nav_buttons[p].configure(bg=Palette.SIDEBAR_HOVER)
            else:
                label.configure(fg=Palette.SIDEBAR_FG, font=("", 10))
                self._nav_bars[p].delete("all")
                self._nav_buttons[p].configure(bg=Palette.SIDEBAR_BG)
        title = self.PANEL_TITLES.get(name, "")
        if self._topbar_title:
            self._topbar_title.configure(text=title)
        subtitles = {
            "overview": "快速了解磁盘空间使用情况",
            "large_files": "查看和管理占用空间最大的文件",
            "duplicates": "查找并清理重复文件，释放磁盘空间",
            "temp_files": "清理临时文件、缓存和垃圾文件",
            "ai_clean": "使用 AI 智能分析并推荐清理方案",
            "history": "查看历史扫描记录和空间变化趋势",
            "settings": "配置扫描参数和 LLM 分析选项",
        }
        if self._topbar_sub:
            self._topbar_sub.configure(text=subtitles.get(name, ""))

    def _nav_hover(self, panel_name: str, enter: bool) -> None:
        if panel_name == self._current_panel:
            return
        if enter:
            self._nav_buttons[panel_name].configure(bg=Palette.SIDEBAR_HOVER)
            self._nav_labels[panel_name].configure(fg="#FFFFFF")
        else:
            self._nav_buttons[panel_name].configure(bg=Palette.SIDEBAR_BG)
            self._nav_labels[panel_name].configure(fg=Palette.SIDEBAR_FG)

    # ---- 面板：概览 ----
    def _create_panel_overview(self) -> None:
        panel = self._panels["overview"]
        scroll = ScrollableFrame(panel)
        scroll.pack(fill=tk.BOTH, expand=True)
        inner = scroll.inner
        # 统计卡片行
        cards_frame = ttk.Frame(inner, padding=(20, 16, 20, 8))
        cards_frame.pack(fill=tk.X)
        cards_frame.columnconfigure((0, 1, 2, 3), weight=1)
        card_data = [
            ("total", "总容量", "--", Palette.METRIC_TOTAL, "💾"),
            ("used", "已使用", "--", Palette.METRIC_USED, "📀"),
            ("free", "可用空间", "--", Palette.METRIC_FREE, "✅"),
            ("cleanable", "可清理", "--", Palette.METRIC_SCAN, "🧹"),
        ]
        for i, (key, title, value, color, icon) in enumerate(card_data):
            card = tk.Frame(cards_frame, bg=Palette.FRAME_BG, highlightbackground=Palette.BORDER,
                            highlightthickness=1, padx=16, pady=12)
            card.grid(row=0, column=i, padx=(0, 8), sticky="nsew")
            color_bar = tk.Frame(card, bg=color, width=4)
            color_bar.place(x=0, y=0, relheight=1, width=4)
            tk.Label(card, text=icon, font=("", 18), bg=Palette.FRAME_BG).pack(anchor="w")
            val_label = tk.Label(card, text=value, font=("", 20, "bold"), bg=Palette.FRAME_BG, fg=Palette.TEXT)
            val_label.pack(anchor="w", pady=(4, 0))
            self._stat_labels[key] = val_label
            tk.Label(card, text=title, font=("", 9), bg=Palette.FRAME_BG, fg=Palette.TEXT_MUTED).pack(anchor="w")
        # 中间行：环形图 + 健康度
        middle_frame = ttk.Frame(inner, padding=(20, 8, 20, 8))
        middle_frame.pack(fill=tk.X)
        middle_frame.columnconfigure(0, weight=2)
        middle_frame.columnconfigure(1, weight=1)
        donut_card = tk.Frame(middle_frame, bg=Palette.FRAME_BG, highlightbackground=Palette.BORDER, highlightthickness=1)
        donut_card.grid(row=0, column=0, padx=(0, 8), sticky="nsew")
        tk.Label(donut_card, text="存储空间分布", font=("", 11, "bold"), bg=Palette.FRAME_BG, fg=Palette.TEXT).pack(anchor="w", padx=16, pady=(12, 4))
        self._donut_canvas = tk.Canvas(donut_card, width=320, height=200, bg=Palette.FRAME_BG, highlightthickness=0, bd=0)
        self._donut_canvas.pack(padx=16, pady=(0, 12))
        health_card = tk.Frame(middle_frame, bg=Palette.FRAME_BG, highlightbackground=Palette.BORDER, highlightthickness=1)
        health_card.grid(row=0, column=1, sticky="nsew")
        tk.Label(health_card, text="存储健康度", font=("", 11, "bold"), bg=Palette.FRAME_BG, fg=Palette.TEXT).pack(anchor="w", padx=16, pady=(12, 8))
        for key, label, color in [("disk_usage", "磁盘使用率", Palette.PRIMARY), ("cleanable", "可清理空间", Palette.WARNING), ("dup_ratio", "重复文件", Palette.DANGER)]:
            row = tk.Frame(health_card, bg=Palette.FRAME_BG)
            row.pack(fill=tk.X, padx=16, pady=4)
            tk.Label(row, text=label, font=("", 9), bg=Palette.FRAME_BG, fg=Palette.TEXT_MUTED).pack(anchor="w")
            bar_canvas = tk.Canvas(row, height=8, bg=Palette.TROUGH, highlightthickness=0, bd=0)
            bar_canvas.pack(fill=tk.X, pady=(2, 0))
            self._health_bars[key] = bar_canvas
        # 最大目录表格
        dirs_card = tk.Frame(inner, bg=Palette.FRAME_BG, highlightbackground=Palette.BORDER, highlightthickness=1)
        dirs_card.pack(fill=tk.BOTH, expand=True, padx=20, pady=(8, 16))
        header_row = tk.Frame(dirs_card, bg=Palette.FRAME_BG)
        header_row.pack(fill=tk.X, padx=16, pady=(12, 4))
        tk.Label(header_row, text="最大目录 TOP N", font=("", 11, "bold"), bg=Palette.FRAME_BG, fg=Palette.TEXT).pack(side=tk.LEFT)
        self.tree_top_dirs = self._create_treeview(dirs_card, [("size", "大小", 120), ("path", "目录路径", 500)], padx=8, pady=(0, 8))
        self.tree_top_dirs.bind("<Double-1>", lambda e: self._open_tree_path(self.tree_top_dirs, event=e))
        self._add_filter(dirs_card, self.tree_top_dirs)

    # ---- 面板：大文件 ----
    def _create_panel_large_files(self) -> None:
        panel = self._panels["large_files"]
        header = ttk.Frame(panel, padding=(20, 16, 20, 8))
        header.pack(fill=tk.X)
        self._large_files_info = ttk.Label(header, text="等待扫描...", font=("", 10), foreground=Palette.TEXT_MUTED)
        self._large_files_info.pack(side=tk.LEFT)
        self.tree_large_files = self._create_treeview(panel, [("size", "大小", 100), ("type", "类型", 80), ("mtime", "修改时间", 140), ("path", "文件路径", 500)], padx=20, pady=(0, 0))
        self.tree_large_files.bind("<Double-1>", lambda e: self._open_tree_path(self.tree_large_files, True, event=e))
        self._add_filter(panel, self.tree_large_files)
        btn_frame = ttk.Frame(panel, padding=(20, 8, 20, 12))
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="打开所在文件夹", command=lambda: self._open_tree_path(self.tree_large_files, True)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="复制路径", command=lambda: self._copy_path(self.tree_large_files)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="移至回收站", command=lambda: self._delete_selected_file(True)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="导出", command=self.export_report).pack(side=tk.RIGHT)

    # ---- 面板：重复文件 ----
    def _create_panel_duplicates(self) -> None:
        panel = self._panels["duplicates"]
        header = ttk.Frame(panel, padding=(20, 16, 20, 8))
        header.pack(fill=tk.X)
        self.dup_summary = ttk.Label(header, text="扫描后显示重复文件", font=("", 10), foreground=Palette.TEXT_MUTED)
        self.dup_summary.pack(side=tk.LEFT)
        self.tree_dup = self._create_treeview(panel, [("size", "单个大小", 100), ("count", "副本数", 80), ("waste", "浪费空间", 120), ("path", "文件路径", 500)], padx=20, pady=(0, 0))
        self.tree_dup.bind("<Double-1>", lambda e: self._open_tree_path(self.tree_dup, event=e))
        self._add_filter(panel, self.tree_dup)
        btn_frame = ttk.Frame(panel, padding=(20, 8, 20, 12))
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="打开所在文件夹", command=lambda: self._open_tree_path(self.tree_dup, True)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="移至回收站", command=lambda: self._delete_selected_dup(True)).pack(side=tk.LEFT, padx=(0, 8))

    # ---- 面板：临时文件 ----
    def _create_panel_temp_files(self) -> None:
        panel = self._panels["temp_files"]
        header = ttk.Frame(panel, padding=(20, 16, 20, 8))
        header.pack(fill=tk.X)
        self._temp_files_info = ttk.Label(header, text="扫描后显示可清理的临时文件", font=("", 10), foreground=Palette.TEXT_MUTED)
        self._temp_files_info.pack(side=tk.LEFT)
        self.tree_junk = self._create_treeview(panel, [("size", "大小", 120), ("path", "路径", 600)], padx=20, pady=(0, 0))
        self._add_filter(panel, self.tree_junk)
        self.tree_junk.bind("<Double-1>", lambda e: self._open_tree_path(self.tree_junk, event=e))
        btn_frame = ttk.Frame(panel, padding=(20, 8, 20, 12))
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="打开位置", command=self._open_selected_junk).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="移至回收站", command=self._delete_selected_junk).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="永久删除", command=self._permanently_delete_junk, style="Danger.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(btn_frame, text="开启 [模拟预览] 可安全测试", foreground=Palette.DANGER).pack(side=tk.LEFT, padx=15)

    # ---- 面板：智能清理 (AI) ----
    def _create_panel_ai_clean(self) -> None:
        panel = self._panels["ai_clean"]
        toolbar = ttk.Frame(panel, padding=(20, 12, 20, 4))
        toolbar.pack(fill=tk.X)
        self._llm_btn = ttk.Button(toolbar, text="  🤖 开始 AI 分析  ", style="Accent.TButton", command=self._start_llm_analysis)
        self._llm_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._llm_stop_btn = ttk.Button(toolbar, text="⏹ 停止", command=self._cancel_llm_analysis, state=tk.DISABLED)
        self._llm_stop_btn.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="清空", command=self._clear_llm_output).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="复制结果", command=self._copy_llm_output).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="导出 Markdown", command=self._export_llm_markdown).pack(side=tk.LEFT, padx=(0, 8))
        self._llm_status = ttk.Label(toolbar, text="", foreground=Palette.TEXT_MUTED, font=("", 9))
        self._llm_status.pack(side=tk.LEFT, padx=(8, 0))
        output_frame = ttk.Frame(panel, padding=(20, 4, 20, 12))
        output_frame.pack(fill=tk.BOTH, expand=True)
        self._llm_text = tk.Text(output_frame, font=("", 10), bg=Palette.FRAME_BG, fg=Palette.TEXT, insertbackground=Palette.TEXT, wrap=tk.WORD, padx=12, pady=10, spacing1=2, spacing3=2, state=tk.DISABLED)
        llm_scroll = ttk.Scrollbar(output_frame, orient=tk.VERTICAL, command=self._llm_text.yview, style="Vertical.TScrollbar")
        self._llm_text.configure(yscrollcommand=llm_scroll.set)
        self._llm_text.grid(row=0, column=0, sticky="nsew")
        llm_scroll.grid(row=0, column=1, sticky="ns")
        output_frame.rowconfigure(0, weight=1)
        output_frame.columnconfigure(0, weight=1)
        self._llm_text.tag_configure("h1", font=("", 14, "bold"), foreground=Palette.PRIMARY)
        self._llm_text.tag_configure("h2", font=("", 12, "bold"), foreground=Palette.PRIMARY)
        self._llm_text.tag_configure("h3", font=("", 11, "bold"), foreground=Palette.TEXT)
        self._llm_text.tag_configure("bold", font=("", 10, "bold"))
        self._llm_text.tag_configure("emoji", font=("", 11))
        self._llm_text.tag_configure("dim", foreground=Palette.TEXT_MUTED)
        self._llm_text.config(state=tk.NORMAL)
        self._llm_text.insert(tk.END, "等待扫描完成后，点击上方「🤖 开始 AI 分析」按钮。\n\nAI 将分析扫描结果，告诉你：\n  • 哪些目录/文件占用了大量空间\n  • 它们是什么、为什么大\n  • 哪些可以安全清理\n  • 具体的清理操作建议\n\n请先在「设置 → LLM 配置」中设置 API 地址和模型。", "dim")
        self._llm_text.config(state=tk.DISABLED)

    # ---- 面板：历史记录 ----
    def _create_panel_history(self) -> None:
        panel = self._panels["history"]
        scroll = ScrollableFrame(panel)
        scroll.pack(fill=tk.BOTH, expand=True)
        inner = scroll.inner
        cards_frame = ttk.Frame(inner, padding=(20, 16, 20, 8))
        cards_frame.pack(fill=tk.X)
        cards_frame.columnconfigure((0, 1, 2), weight=1)
        for i, (title, value, color) in enumerate([("累计释放空间", "--", Palette.SUCCESS), ("平均每次释放", "--", Palette.PRIMARY), ("扫描次数", "0", Palette.WARNING)]):
            card = tk.Frame(cards_frame, bg=Palette.FRAME_BG, highlightbackground=Palette.BORDER, highlightthickness=1, padx=16, pady=12)
            card.grid(row=0, column=i, padx=(0, 8), sticky="nsew")
            tk.Frame(card, bg=color, width=4).place(x=0, y=0, relheight=1, width=4)
            tk.Label(card, text=title, font=("", 9), bg=Palette.FRAME_BG, fg=Palette.TEXT_MUTED).pack(anchor="w")
            tk.Label(card, text=value, font=("", 18, "bold"), bg=Palette.FRAME_BG, fg=Palette.TEXT).pack(anchor="w", pady=(4, 0))
        timeline_header = ttk.Frame(inner, padding=(20, 12, 20, 4))
        timeline_header.pack(fill=tk.X)
        tk.Label(timeline_header, text="操作时间线", font=("", 11, "bold"), bg=Palette.BG, fg=Palette.TEXT).pack(anchor="w")
        self._history_container = ttk.Frame(inner, padding=(20, 4, 20, 16))
        self._history_container.pack(fill=tk.BOTH, expand=True)
        ttk.Label(self._history_container, text="扫描完成后将自动记录历史", font=("", 10), foreground=Palette.TEXT_MUTED).pack(pady=20)

    # ---- 面板：设置 ----
    def _create_panel_settings(self) -> None:
        panel = self._panels["settings"]
        scroll = ScrollableFrame(panel)
        scroll.pack(fill=tk.BOTH, expand=True)
        inner = scroll.inner
        center = ttk.Frame(inner, padding=(40, 20, 40, 20))
        center.pack(anchor="center")
        self._add_settings_section(center, "通用设置", [
            ("深色模式", "切换深色/浅色主题", "toggle", self._theme_name == "dark",
             lambda v: self._toggle_theme() if v != (self._theme_name == "dark") else None),
        ])
        self._add_settings_section(center, "扫描设置", [
            ("大文件阈值 (Top N)", "显示占用最大的 N 个文件/目录", "spinbox", self.config.top_n, {"from_": 5, "to": 100}),
            ("检测重复文件", "扫描时检测重复的大文件 (需要 xxhash)", "toggle", self.enable_dup_var.get(), lambda v: self.enable_dup_var.set(v)),
            ("模拟预览模式", "开启后删除操作将只显示预览，不真实执行", "toggle", self.simulate_mode_var.get(), lambda v: self.simulate_mode_var.set(v)),
        ])
        self._add_settings_section(center, "LLM 配置", [
            ("API 地址", "OpenAI 兼容的 API 端点", "entry", self.config.llm_api_url, lambda v: setattr(self.config, 'llm_api_url', v)),
            ("API Key", "API 认证密钥（本地模型可留空）", "password", self.config.llm_api_key, lambda v: setattr(self.config, 'llm_api_key', v)),
            ("模型名称", "使用的模型标识符", "entry", self.config.llm_model, lambda v: setattr(self.config, 'llm_model', v)),
        ])
        test_frame = ttk.Frame(center)
        test_frame.pack(fill=tk.X, pady=(0, 16))
        ttk.Button(test_frame, text="测试 LLM 连接", command=self._test_llm_connection).pack(side=tk.LEFT)
        ttk.Button(test_frame, text="保存配置", command=self.config.save, style="Accent.TButton").pack(side=tk.LEFT, padx=(8, 0))
        self._add_settings_section(center, "关于", [
            ("版本", "磁盘空间分析工具 v2.0", "label", "", None),
            ("Python", sys.version.split()[0], "label", "", None),
            ("xxhash", "已安装" if HAS_XXHASH else "未安装", "label", "", None),
            ("send2trash", "已安装" if HAS_SEND2TRASH else "未安装", "label", "", None),
        ])

    def _add_settings_section(self, parent: tk.Widget, title: str, items: List[Tuple[str, str, str, Any, Optional[Callable]]]) -> None:
        section = tk.Frame(parent, bg=Palette.FRAME_BG, highlightbackground=Palette.BORDER, highlightthickness=1)
        section.pack(fill=tk.X, pady=(0, 12))
        tk.Label(section, text=title, font=("", 11, "bold"), bg=Palette.FRAME_BG, fg=Palette.TEXT).pack(anchor="w", padx=16, pady=(12, 8))
        for i, (label, desc, widget_type, value, callback) in enumerate(items):
            row = tk.Frame(section, bg=Palette.FRAME_BG)
            row.pack(fill=tk.X, padx=16, pady=4)
            if i > 0:
                tk.Frame(row, bg=Palette.BORDER, height=1).pack(fill=tk.X, pady=(0, 4))
            left = tk.Frame(row, bg=Palette.FRAME_BG)
            left.pack(side=tk.LEFT, fill=tk.X, expand=True)
            tk.Label(left, text=label, font=("", 10), bg=Palette.FRAME_BG, fg=Palette.TEXT).pack(anchor="w")
            if desc:
                tk.Label(left, text=desc, font=("", 8), bg=Palette.FRAME_BG, fg=Palette.TEXT_MUTED).pack(anchor="w")
            right = tk.Frame(row, bg=Palette.FRAME_BG)
            right.pack(side=tk.RIGHT, padx=(16, 0))
            if widget_type == "toggle":
                var = tk.BooleanVar(value=value)
                ToggleSwitch(right, var, command=lambda cb=callback, v=var: cb(v.get()) if cb else None).pack()
            elif widget_type in ("entry", "password"):
                var = tk.StringVar(value=value or "")
                entry = ttk.Entry(right, textvariable=var, width=30, show="•" if widget_type == "password" else "")
                entry.pack()
                if callback:
                    entry.bind("<FocusOut>", lambda e, cb=callback, v=var: cb(v.get()))
            elif widget_type == "spinbox":
                var = tk.IntVar(value=value)
                ttk.Spinbox(right, from_=5, to=100, width=6, textvariable=var).pack()
            elif widget_type == "label":
                tk.Label(right, text=str(value), font=("", 10), bg=Palette.FRAME_BG, fg=Palette.TEXT_MUTED).pack(anchor="e")
        tk.Frame(section, height=8, bg=Palette.FRAME_BG).pack()

    def _test_llm_connection(self) -> None:
        if not self.config.llm_api_url or not self.config.llm_model:
            messagebox.showwarning("提示", "请先填写 API 地址和模型名称")
            return
        if not HAS_REQUESTS:
            messagebox.showerror("错误", "缺少 requests 库: pip install requests")
            return
        try:
            test_url = self.config.llm_api_url.rstrip("/")
            if not test_url.endswith("/models"):
                test_url += "/models"
            headers: Dict[str, str] = {}
            if self.config.llm_api_key:
                headers["Authorization"] = f"Bearer {self.config.llm_api_key}"
            resp = _requests.get(test_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                messagebox.showinfo("连接成功", "API 连接正常！")
            else:
                messagebox.showwarning("连接异常", f"HTTP {resp.status_code}\n{resp.text[:300]}")
        except Exception as e:
            messagebox.showerror("连接失败", str(e))

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
        """保留兼容：旧方法已移至 _create_panel_overview / _create_panel_large_files"""
        pass

    # 旧 tab 创建方法已移至 _create_panel_* 系列方法

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
        self._llm_line_buffer = ""  # BUG FIX: 重置行缓冲区
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

        # 切换到 AI 面板
        self._show_panel("ai_clean")

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
        """在主线程中追加 token 到输出区（逐行缓冲，正确处理多行 token 和跨 token 标题）"""
        if not self._llm_text:
            return
        self._llm_line_buffer += token
        self._llm_text.config(state=tk.NORMAL)

        # �行处理：保留最后一个不完整的行在缓冲区中
        while "\n" in self._llm_line_buffer:
            line, self._llm_line_buffer = self._llm_line_buffer.split("\n", 1)
            self._insert_llm_line(line + "\n")

        self._llm_text.see(tk.END)
        self._llm_text.config(state=tk.DISABLED)

    def _flush_llm_buffer(self) -> None:
        """刷新 LLM 行缓冲区中剩余内容（流结束时调用）"""
        if not self._llm_text or not self._llm_line_buffer:
            return
        self._llm_text.config(state=tk.NORMAL)
        self._insert_llm_line(self._llm_line_buffer)
        self._llm_line_buffer = ""
        self._llm_text.see(tk.END)
        self._llm_text.config(state=tk.DISABLED)

    def _insert_llm_line(self, line: str) -> None:
        """将一行文本插入 LLM 输出区，应用 Markdown 格式"""
        if line.startswith("### "):
            self._llm_text.insert(tk.END, line[4:], "h3")
        elif line.startswith("## "):
            self._llm_text.insert(tk.END, line[3:], "h2")
        elif line.startswith("# "):
            self._llm_text.insert(tk.END, line[2:], "h1")
        else:
            self._llm_text.insert(tk.END, line)

    def _on_llm_done(self, full_text: Optional[str], error: Optional[str]) -> None:
        """LLM 分析完成回调（后台线程调用）"""
        self.root.after(0, self._finish_llm_analysis, full_text, error)

    def _finish_llm_analysis(self, full_text: Optional[str], error: Optional[str]) -> None:
        """在主线程中处理分析完成"""
        self._llm_streaming = False
        self._llm_line_buffer = ""  # BUG FIX: 清空缓冲区（最终渲染用 full_text）
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

        # 概览面板：目录右键菜单
        if self.tree_top_dirs:
            dir_menu: tk.Menu = tk.Menu(self.root, tearoff=0, font=("", 9), **menu_kw)
            self._context_menus.append(dir_menu)
            dir_menu.add_command(label="打开文件夹", command=functools.partial(self._open_tree_path, self.tree_top_dirs))
            dir_menu.add_command(label="复制路径", command=functools.partial(self._copy_path, self.tree_top_dirs))
            dir_menu.add_separator()
            dir_menu.add_command(label="继续扫描此目录", command=self._continue_scan_dir)
            self.tree_top_dirs.bind("<Button-3>", functools.partial(popup, tree=self.tree_top_dirs, menu=dir_menu))

        # 大文件面板：文件右键菜单
        if self.tree_large_files:
            file_menu: tk.Menu = tk.Menu(self.root, tearoff=0, font=("", 9), **menu_kw)
            self._context_menus.append(file_menu)
            file_menu.add_command(label="打开所在文件夹", command=functools.partial(self._open_tree_path, self.tree_large_files, True))
            file_menu.add_command(label="复制文件路径", command=functools.partial(self._copy_path, self.tree_large_files))
            file_menu.add_separator()
            file_menu.add_command(label="移至回收站", command=functools.partial(self._delete_selected_file, True))
            file_menu.add_command(label="永久删除", command=functools.partial(self._delete_selected_file, False))
            self.tree_large_files.bind("<Button-3>", functools.partial(popup, tree=self.tree_large_files, menu=file_menu))

        # 重复文件面板
        if self.tree_dup:
            dup_menu: tk.Menu = tk.Menu(self.root, tearoff=0, font=("", 9), **menu_kw)
            self._context_menus.append(dup_menu)
            dup_menu.add_command(label="打开所在文件夹", command=functools.partial(self._open_tree_path, self.tree_dup, True))
            dup_menu.add_command(label="复制文件路径", command=functools.partial(self._copy_path, self.tree_dup))
            dup_menu.add_separator()
            dup_menu.add_command(label="移至回收站", command=functools.partial(self._delete_selected_dup, True))
            dup_menu.add_command(label="永久删除", command=functools.partial(self._delete_selected_dup, False))
            self.tree_dup.bind("<Button-3>", functools.partial(popup, tree=self.tree_dup, menu=dup_menu))

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

    def _on_panel_changed(self) -> None:
        if self._last_scan_result:
            return
        if self.status_label:
            self.status_label.config(text="提示: 请先按 F5 或点击「开始扫描」分析磁盘空间")

    def _select_panel(self, name: str) -> None:
        if name in self.PANEL_NAMES:
            self._show_panel(name)

    def _toggle_theme(self) -> None:
        self._theme_name = "dark" if self._theme_name == "light" else "light"
        self._apply_palette()
        self._setup_styles()
        if self.status_label:
            self.status_label.configure(foreground=Palette.TEXT_MUTED)
        if self.theme_btn:
            self.theme_btn.configure(text="☀️" if self._theme_name == "dark" else "🌙")
        # 更新侧栏颜色
        if self._sidebar:
            self._sidebar.configure(bg=Palette.SIDEBAR_BG)
        for label in self._sidebar_labels:
            label.configure(bg=Palette.SIDEBAR_BG, fg=Palette.SIDEBAR_MUTED)
        for p, btn in self._nav_buttons.items():
            btn.configure(bg=Palette.SIDEBAR_HOVER if p == self._current_panel else Palette.SIDEBAR_BG)
        for p, label in self._nav_labels.items():
            label.configure(bg=Palette.SIDEBAR_BG,
                            fg=Palette.SIDEBAR_ACTIVE if p == self._current_panel else Palette.SIDEBAR_FG)
        # 更新所有 treeview 标签
        for tree in (self.tree_top_dirs, self.tree_large_files, self.tree_dup, self.tree_junk):
            if tree:
                tree.tag_configure("evenrow", background=Palette.STRIPE_EVEN)
                tree.tag_configure("oddrow", background=Palette.STRIPE_ODD)
                tree.tag_configure("highlight", background=Palette.HIGHLIGHT)
        # 更新 LLM 文本颜色
        if self._llm_text:
            self._llm_text.config(bg=Palette.FRAME_BG, fg=Palette.TEXT, insertbackground=Palette.TEXT)
            self._llm_text.tag_configure("h1", foreground=Palette.PRIMARY)
            self._llm_text.tag_configure("h2", foreground=Palette.PRIMARY)
            self._llm_text.tag_configure("h3", foreground=Palette.TEXT)
            self._llm_text.tag_configure("dim", foreground=Palette.TEXT_MUTED)
        if hasattr(self, '_llm_status') and self._llm_status:
            self._llm_status.config(foreground=Palette.TEXT_MUTED)
        # 更新右键菜单颜色
        for menu in self._context_menus:
            try:
                menu.config(bg=Palette.FRAME_BG, fg=Palette.TEXT,
                            activebackground=Palette.HIGHLIGHT, activeforeground=Palette.TEXT)
            except tk.TclError:
                pass
        # 重绘环形图
        if self._last_scan_result:
            self._draw_donut_chart(self._last_scan_result.ext_stats)

    @staticmethod
    def _norm_path(p: str) -> str:
        """规范化路径用于缓存匹配（小写 + 去尾斜杠 + 统一分隔符）"""
        return os.path.normcase(os.path.normpath(p))

    def _load_size_cache(self) -> None:
        """从磁盘加载大小缓存"""
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                self._dir_size_cache = json.load(f)
        except Exception:
            self._dir_size_cache = {}

    def _save_size_cache(self) -> None:
        """保存大小缓存到磁盘"""
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self._dir_size_cache, f)
        except Exception as e:
            logger.debug("保存缓存失败: %s", e)

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
        tree = self.tree_top_dirs
        if not tree:
            return
        sel: Tuple[str, ...] = tree.selection()
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
        tree = self.tree_large_files
        if not tree:
            return
        sel: Tuple[str, ...] = tree.selection()
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
            tree.delete(sel[0])
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
        """更新概览面板的统计卡片"""
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
            if "total" in self._stat_labels:
                self._stat_labels["total"].configure(text=format_size(total_all))
            if "used" in self._stat_labels:
                self._stat_labels["used"].configure(text=f"{format_size(used_all)} ({pct}%)")
            if "free" in self._stat_labels:
                self._stat_labels["free"].configure(text=format_size(free_all))
            # 更新健康度进度条
            if "disk_usage" in self._health_bars:
                self._draw_health_bar("disk_usage", pct, Palette.PRIMARY if pct < 75 else Palette.WARNING if pct < 90 else Palette.DANGER)
        # 更新底部状态栏
        if hasattr(self, '_sb_left'):
            disk_info = " | ".join(
                f"{p.mountpoint} {psutil.disk_usage(p.mountpoint).percent}%"
                for p in psutil.disk_partitions(all=False)
                if not (sys.platform == "win32" and ("cdrom" in p.opts or p.fstype == ""))
            )
            self._sb_left.config(text=disk_info or "就绪")

    def _draw_health_bar(self, key: str, pct: int, color: str) -> None:
        """绘制健康度进度条"""
        canvas = self._health_bars.get(key)
        if not canvas:
            return
        canvas.delete("all")
        w = canvas.winfo_width()
        if w < 2:
            w = 200
        h = 8
        fill_w = max(0, min(w, int(w * pct / 100)))
        canvas.create_rectangle(0, 0, fill_w, h, fill=color, outline="")
        canvas.create_text(w + 8, h // 2, text=f"{pct}%", anchor="w", font=("", 8), fill=Palette.TEXT_MUTED)

    def _load_partitions(self) -> None:
        """Deferred partition loading to avoid blocking startup"""
        partitions: List[str] = []
        for p in psutil.disk_partitions(all=False):
            if sys.platform == "win32" and ("cdrom" in p.opts or p.fstype == ""):
                continue
            try:
                usage = psutil.disk_usage(p.mountpoint)
                if usage.total <= 0:
                    partitions.append(p.mountpoint)
                    continue
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

        for tree in (self.tree_top_dirs, self.tree_large_files, self.tree_junk, self.tree_dup):
            if tree:
                tree.delete(*tree.get_children())
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
            base = self._last_progress_msg
            if base and not base.startswith("完成"):
                self.status_label.config(text=f"{base}  [{elapsed:.0f}s]")
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
        self._last_progress_msg = msg  # BUG FIX: 保存原始消息
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
            save_cache(result)
            self._save_size_cache()
            self._save_scan_entry(result)

            # LLM 分析就绪提示
            if self._llm_btn:
                self._llm_btn.config(state=tk.NORMAL)
            if self._llm_status:
                if self.config.llm_configured:
                    self._llm_status.config(text="扫描完成，可点击「🤖 开始 AI 分析」", foreground=Palette.SUCCESS)
                else:
                    self._llm_status.config(text="扫描完成。在「设置 → LLM 配置」中设置 API 后可使用 AI 分析", foreground=Palette.TEXT_MUTED)

            if self.status_label:
                self.status_label.config(
                    text=f"完成 | 占用总计: {format_size(result.total_used)} | "
                         f"耗时: {result.scan_time:.1f}s | 检索: {result.scanned_items} 项"
                )
            if hasattr(self, '_sb_right'):
                self._sb_right.config(
                    text=f"扫描 {result.scanned_items} 项 · {format_size(result.total_used)} · {result.scan_time:.1f}s"
                )
            # 自动切换到概览面板
            self._show_panel("overview")

        self.root.after(0, _on_done)

    def _populate_trees(self, result: ScanResult) -> None:
        # 概览面板：最大目录
        if self.tree_top_dirs:
            for i, (size, path) in enumerate(result.top_dirs):
                tag: str = "evenrow" if i % 2 == 0 else "oddrow"
                marker: str = "[可清理] " if any(kw in path.lower() for kw in self.JUNK_KEYWORDS) else ""
                self.tree_top_dirs.insert(
                    "", tk.END, iid=path, values=(format_size(size), "📁 " + marker + path), tags=(tag,)
                )

        # 大文件面板
        if self.tree_large_files:
            for i, (size, path) in enumerate(result.top_files):
                tag = "evenrow" if i % 2 == 0 else "oddrow"
                ext = os.path.splitext(path)[1].lower()
                try:
                    mtime: str = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    mtime = "未知"
                self.tree_large_files.insert(
                    "", tk.END, iid=path,
                    values=(format_size(size), ext or "—", mtime, "📄 " + path), tags=(tag,)
                )
            if hasattr(self, '_large_files_info') and self._large_files_info:
                total_large = sum(s for s, _ in result.top_files)
                self._large_files_info.configure(
                    text=f"共 {len(result.top_files)} 个大文件，总计 {format_size(total_large)}"
                )

        # 临时文件面板
        if self.tree_junk:
            for i, (path, size) in enumerate(result.junk_dirs):
                tag = "evenrow" if i % 2 == 0 else "oddrow"
                self.tree_junk.insert(
                    "", tk.END, iid=path, values=(format_size(size), "🗑️ " + path), tags=(tag,)
                )
            if hasattr(self, '_temp_files_info') and self._temp_files_info:
                total_junk = sum(s for _, s in result.junk_dirs)
                self._temp_files_info.configure(
                    text=f"可安全清理 {format_size(total_junk)}，共 {len(result.junk_dirs)} 个项目"
                )

        # 更新可清理空间统计
        cleanable = sum(s for _, s in result.junk_dirs)
        if "cleanable" in self._stat_labels:
            self._stat_labels["cleanable"].configure(text=format_size(cleanable))

        # 绘制环形图
        self._draw_donut_chart(result.ext_stats)

        # 更新健康度条
        if result.age_groups:
            age_data = result.age_groups
            old_count = sum(
                (age_data.get(k, (0, 0))[0] if isinstance(age_data.get(k), (list, tuple)) else age_data.get(k, 0))
                for k in ["1-7天", "8-30天", "1-3月"]
            )
            total_files = sum(
                (age_data.get(k, (0, 0))[0] if isinstance(age_data.get(k), (list, tuple)) else age_data.get(k, 0))
                for k in AGE_GROUP_KEYS
            )
            if total_files > 0 and "cleanable" in self._health_bars:
                cleanable_pct = min(100, int(cleanable * 100 / max(1, result.total_used)))
                self._draw_health_bar("cleanable", cleanable_pct, Palette.WARNING)

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
        "#4F46E5", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6",
        "#06B6D4", "#84CC16", "#F97316", "#6366F1", "#EC4899",
    ]

    def _draw_donut_chart(self, ext_stats: List[Tuple[str, int]]) -> None:
        """绘制环形图（概览面板）"""
        c = self._donut_canvas
        if not c:
            return
        c.delete("all")
        if not ext_stats:
            c.create_text(160, 100, text="暂无数据", fill=Palette.TEXT_MUTED, font=("", 10))
            return
        top_n = 6
        top = ext_stats[:top_n]
        other_size = sum(s for _, s in ext_stats[top_n:])
        total = sum(s for _, s in ext_stats)
        if total == 0:
            return
        slices = [(ext if ext else "(无后缀)", size) for ext, size in top]
        if other_size > 0:
            slices.append(("其他", other_size))
        # 环形图参数
        cx, cy, r_outer, r_inner = 80, 100, 70, 40
        start_angle = 90
        for i, (label, size) in enumerate(slices):
            extent = 360 * size / total
            color = self.PIE_COLORS[i % len(self.PIE_COLORS)]
            # 外圆弧
            c.create_arc(cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer,
                         start=start_angle, extent=extent, fill=color, outline=Palette.FRAME_BG, width=2, style="pieslice")
            start_angle += extent
        # 中心白色圆（形成环形效果）
        c.create_oval(cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner,
                      fill=Palette.FRAME_BG, outline=Palette.FRAME_BG)
        # 中心文字
        c.create_text(cx, cy - 6, text="已使用", font=("", 8), fill=Palette.TEXT_MUTED)
        c.create_text(cx, cy + 10, text=format_size(total), font=("", 11, "bold"), fill=Palette.TEXT)
        # 图例
        legend_x = 175
        legend_y = 15
        for i, (label, size) in enumerate(slices):
            pct = size * 100 / total
            color = self.PIE_COLORS[i % len(self.PIE_COLORS)]
            y = legend_y + i * 24
            c.create_rectangle(legend_x, y, legend_x + 12, y + 12, fill=color, outline="")
            c.create_text(legend_x + 18, y + 6,
                          text=f"{label}  {format_size(size)} ({pct:.1f}%)",
                          anchor="w", font=("", 9), fill=Palette.TEXT)

    # ---- 扫描历史 ----
    def _load_scan_history(self) -> List[Dict[str, Any]]:
        try:
            if os.path.exists(self._history_file):
                with open(self._history_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return []

    def _save_scan_entry(self, result: ScanResult) -> None:
        history = self._load_scan_history()
        entry = {
            "timestamp": datetime.now().isoformat(),
            "scan_path": self.path_var.get(),
            "total_used": result.total_used,
            "scan_time": result.scan_time,
            "scanned_items": result.scanned_items,
            "cleanable": sum(s for _, s in result.junk_dirs),
            "dup_groups": len(result.duplicates) if result.duplicates else 0,
        }
        history.append(entry)
        # 只保留最近 100 条
        if len(history) > 100:
            history = history[-100:]
        try:
            os.makedirs(os.path.dirname(self._history_file), exist_ok=True)
            with open(self._history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.debug("保存历史失败: %s", e)

    def export_report(self) -> None:
        if self._loaded_cache:
            messagebox.showinfo("提示", "当前为缓存数据，请先执行一次新扫描后再导出。")
            return
        tree = self.tree_top_dirs
        if not tree or not tree.get_children():
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
        data: Dict[str, Any] = {"scan_time": datetime.now().isoformat(), "top_dirs": [], "top_files": [], "junk_dirs": []}
        if self.tree_top_dirs:
            for child in self.tree_top_dirs.get_children():
                vals = self.tree_top_dirs.item(child)["values"]
                if vals and vals[0]:
                    data["top_dirs"].append({"size": vals[0], "path": str(vals[1])})
        if self.tree_large_files:
            for child in self.tree_large_files.get_children():
                vals = self.tree_large_files.item(child)["values"]
                if vals and vals[0]:
                    data["top_files"].append({"size": vals[0], "path": str(vals[3]), "mtime": vals[2]})
        if self.tree_junk:
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
            if self.tree_top_dirs:
                for child in self.tree_top_dirs.get_children():
                    vals = self.tree_top_dirs.item(child)["values"]
                    if vals and vals[0]:
                        writer.writerow(["大目录", vals[0], str(vals[1]), ""])
            if self.tree_large_files:
                for child in self.tree_large_files.get_children():
                    vals = self.tree_large_files.item(child)["values"]
                    if vals and vals[0]:
                        writer.writerow(["大文件", vals[0], str(vals[3]), vals[2]])
            if self.tree_junk:
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
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        dlg.bind("<Escape>", lambda e: dlg.destroy())


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
