# 磁盘空间分析工具 v2 — 功能文档

## 概述

磁盘空间分析工具是一款跨平台（Windows/macOS/Linux）桌面应用，用于可视化磁盘使用情况、定位大文件和重复文件、检测垃圾文件，并提供 AI 驱动的智能清理建议。

当前版本基于 Python + tkinter 构建，采用单文件架构（`disk_analyzer_v2.py`，约 3400 行）。

---

## 架构概览

```
┌─────────────────────────────────────────────────┐
│                   UI Layer                       │
│  DiskAnalyzerApp (tkinter/ttk)                   │
│  ├── 侧栏导航 (7 面板切换)                        │
│  ├── 顶栏 (标题/主题/扫描按钮)                     │
│  └── 内容面板 (概览/大文件/重复/临时/AI/历史/设置)   │
├─────────────────────────────────────────────────┤
│                 Business Layer                   │
│  ├── FastScanner — 文件系统扫描引擎                │
│  ├── LLMAnalyzer — AI 分析器                     │
│  ├── Config — 配置管理                            │
│  └── ScanResult — 数据模型                        │
├─────────────────────────────────────────────────┤
│                 Storage Layer                    │
│  ~/.disk_analyzer/                               │
│  ├── config.json    (用户配置)                    │
│  ├── cache.json     (扫描缓存, 1h TTL)            │
│  ├── size_cache.json (目录大小缓存)                │
│  ├── history.json   (扫描历史, 最多100条)          │
│  ├── geometry.json  (窗口位置/大小)                │
│  └── app.log        (运行日志)                    │
└─────────────────────────────────────────────────┘
```

---

## 核心类

| 类 | 职责 |
|---|------|
| `ScanResult` | dataclass，存储扫描结果：top_dirs, top_files, junk_dirs, ext_stats, age_groups, dir_size_cache, duplicates, total_used, scan_time, scanned_items |
| `Config` | 管理用户配置的加载/保存，支持排除目录、自定义垃圾目录、LLM API 配置等 |
| `FastScanner` | 扫描引擎，使用 os.scandir 递归遍历，支持进度回调、停止控制、重复文件检测（xxhash 两阶段哈希） |
| `LLMAnalyzer` | 调用 OpenAI 兼容 API 进行流式分析，支持取消操作 |
| `DiskAnalyzerApp` | 主应用类，负责全部 GUI 构建和交互逻辑 |

---

## 七大功能面板

### 1. 概览 (Overview)

**统计卡片** — 4 个核心指标：
- 总容量：磁盘总大小 + 文件系统类型
- 已使用：已用空间 + 占总容量百分比
- 可用空间：剩余空间 + 健康建议（建议 >= 50GB）
- 可清理：缓存 + 日志 + 重复文件的总量

**存储分布图** — 环形图（Canvas 绘制）：
- 按文件扩展名统计 Top 6，其余归入"其他"
- 中心显示已使用总量
- 图例含颜色、名称、大小、百分比

**健康度指标** — 3 个进度条：
- 磁盘使用率：<75% 绿 / <90% 橙 / >=90% 红
- 可清理空间占比
- 重复文件占比

**最大目录表格** — Treeview 显示 Top N 目录：
- 列：序号、路径、类型标签、大小、占比条形图
- 支持搜索过滤、列排序、行悬停高亮
- 右键菜单：打开文件夹、复制路径、继续扫描此目录

### 2. 大文件 (Large Files)

**文件列表** — Treeview 显示超过阈值的文件：
- 列：文件名、路径、类型标签、修改日期、大小
- 支持搜索过滤、列排序（大小智能解析为字节数）
- 右键菜单：打开所在文件夹、复制路径、移至回收站、永久删除
- 导出功能：CSV（UTF-8-BOM）、JSON

### 3. 重复文件 (Duplicates)

**分组显示** — 按文件哈希分组：
- 每组显示：文件名、副本数、单份大小、浪费空间
- 组内每项：路径、修改日期、大小、"建议删除"标签
- 汇总：总组数、总可释放空间
- 操作：选中后批量移至回收站

**检测算法** — 两阶段 xxhash：
1. 快速过滤：32 线程并发计算前 64KB 哈希
2. 深度校验：4 线程并发计算完整文件哈希
- 仅检测 >= 100MB 的文件
- 按浪费空间降序排列

### 4. 临时文件 (Temp Files)

**垃圾文件检测** — 跨平台自动识别：
- Windows：`%TEMP%`, `C:\Windows\Temp`, `C:\Windows\Prefetch`, `$Recycle.Bin`, Downloads, AppData\Local\Temp
- macOS：`.Trash`, `Library/Caches`, `Library/Logs`, `/tmp`, `/var/tmp`
- Linux：`.cache`, `.local/share/Trash`, `/tmp`, `/var/tmp`
- 支持用户自定义垃圾目录

**操作**：打开位置、移至回收站、永久删除
**安全模式**：模拟预览模式，开启后删除操作仅显示弹窗不真实执行

### 5. 智能清理 / AI 分析 (AI Clean)

**LLM 集成** — 支持所有 OpenAI 兼容 API：
- 流式输出（SSE），逐 token 接收
- 后台线程执行，不阻塞 UI
- 支持取消操作

**分析内容** — 自动将 ScanResult 格式化为结构化 prompt：
- 扫描路径、耗时、总计大小、文件数
- Top 15 目录、Top 15 文件（含修改日期）
- 文件类型统计 Top 10
- 可清理目录列表、文件年龄分布
- 重复文件详情（前 5 组）

**输出渲染** — Markdown 风格：
- 标题层级（h1/h2/h3）、粗体、emoji
- 逐行缓冲输出
- 操作：停止、清空、复制结果、导出 Markdown

### 6. 历史记录 (History)

**统计卡片** — 3 个汇总指标：
- 累计释放空间
- 平均每次释放
- 扫描次数

**操作时间线** — 每次扫描记录：
- 时间戳、扫描路径、总使用量
- 扫描耗时、扫描项数
- 可清理大小、重复文件组数
- 最多保留 100 条记录

### 7. 设置 (Settings)

**通用设置**：
- 深色模式开关（实时切换）

**扫描设置**：
- Top N 显示数量（5-100）
- 重复文件检测开关
- 模拟预览模式开关

**LLM 配置**：
- API 地址、API Key、模型名称
- 温度参数（默认 0.3）
- 连接测试功能

**排除目录**：
- 可编辑的排除目录列表

**关于信息**：
- 版本号、作者

---

## 通用功能

### 快捷键
| 快捷键 | 功能 |
|--------|------|
| F5 | 开始扫描 |
| Ctrl+O | 浏览选择目录 |
| Escape | 停止扫描 |
| Ctrl+1 ~ Ctrl+7 | 快速切换面板 |
| Enter | 确认路径开始扫描 |

### 右键上下文菜单
- 概览目录：打开文件夹、复制路径、继续扫描此目录
- 大文件：打开所在文件夹、复制文件路径、移至回收站、永久删除
- 重复文件：打开所在文件夹、复制文件路径、移至回收站、永久删除

### 搜索过滤
- 每个 Treeview 上方有搜索框
- 实时过滤（递归匹配子节点）
- 占位符提示文本、清空按钮
- 过滤时匹配项高亮，展开父节点

### 列排序
- 所有列可点击表头排序
- 大小列智能解析（"1.23 GB" 转浮点数）
- 递归排序子节点
- 排序状态记忆

### 行悬停效果
- 鼠标经过 Treeview 行时高亮
- 离开时恢复斑马纹

### 主题系统
- 浅色主题：现代浅灰白背景，靛蓝主色调
- 深色主题：深色背景，紫色主色调
- 顶栏月亮/太阳按钮、设置面板开关双控
- 实时切换所有组件颜色

### 磁盘分区信息
- 延迟加载（启动 200ms 后异步）
- 分区选择器（显示挂载点和使用率）
- 选择分区自动填入扫描路径

### 导出功能
- CSV：UTF-8-BOM 编码，Excel 兼容
- JSON：完整扫描数据
- Markdown：AI 分析结果

### 跨平台适配
- Windows：管理员权限检测、DPI 适配、长路径支持（`\\?\` 前缀）
- macOS：Finder 集成、open 命令
- Linux：xdg-open 集成

### 依赖检测与降级
| 依赖 | 用途 | 降级行为 |
|------|------|----------|
| psutil | 磁盘分区信息 | 必需 |
| xxhash | 重复文件检测 | 未安装时跳过并提示 |
| requests | LLM API 调用 | 未安装时提示 |
| send2trash | 回收站功能 | 未安装时降级为永久删除 |

---

## 数据模型

### ScanResult
```python
@dataclass
class ScanResult:
    top_dirs: List[Tuple[str, int]]       # (路径, 大小) — Top N 目录
    top_files: List[Tuple[str, int, float]] # (路径, 大小, 修改时间) — Top N 文件
    junk_dirs: List[Tuple[str, int]]      # (路径, 大小) — 垃圾目录
    ext_stats: List[Tuple[str, int]]      # (扩展名, 总大小) — 扩展名统计
    age_groups: Dict[str, int]            # 年龄分组 → 文件数
    dir_size_cache: Dict[str, int]        # 目录路径 → 大小缓存
    duplicates: List[Tuple[int, List[str]]] # (单份大小, [路径列表]) — 重复文件
    total_used: int                       # 总已用空间
    scan_time: float                      # 扫描耗时(秒)
    scanned_items: int                    # 扫描文件数
```

### Config
```python
class Config:
    exclude_dirs: List[str]        # 排除目录列表
    custom_junk_dirs: List[str]    # 自定义垃圾目录
    last_scan_path: str            # 上次扫描路径
    top_n: int                     # Top N 数量 (默认 15)
    llm_api_url: str               # LLM API 地址
    llm_api_key: str               # LLM API Key
    llm_model: str                 # 模型名称
    llm_temperature: float         # 温度 (默认 0.3)
```

---

## 文件操作

### 打开文件/目录
- Windows：`os.startfile()` / Explorer `/select` 定位
- macOS：`open` 命令
- Linux：`xdg-open` 命令

### 删除操作
- 移至回收站：`send2trash.send2trash(path)`（需安装 send2trash）
- 永久删除：文件 `os.remove()` / 目录 `shutil.rmtree()`
- 模拟模式：仅显示弹窗预览，不真实执行

---

## 性能特性

- **扫描引擎**：`os.scandir` 高效遍历，最大深度 30 层保护
- **进度节流**：0.15 秒间隔回调，避免 UI 刷新过频
- **多线程重复检测**：32 线程快速过滤 + 4 线程深度校验
- **缓存系统**：扫描结果 1 小时 TTL，目录大小持久化缓存
- **延迟加载**：磁盘分区信息异步加载，不阻塞启动
- **可中断扫描**：`threading.Event` 控制，Escape 键随时停止
