# 磁盘空间分析器 — 项目文档

## 1. 项目概述

**名称：** 磁盘空间分析器 (TreeSize)
**版本：** 3.0.0
**定位：** 跨平台磁盘空间分析与智能清理工具
**目标用户：** 需要快速定位大文件、清理磁盘空间的个人用户和技术人员

### 核心价值
- 快速扫描目录，可视化磁盘使用情况
- 智能识别可清理的临时文件、缓存、重复文件
- 集成 LLM AI 分析，给出专业清理建议
- 支持 Windows / macOS / Linux

---

## 2. 技术架构

### 2.1 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 桌面框架 | Electron 35 | 跨平台桌面应用 |
| 主进程 | TypeScript (Node.js) | 文件扫描、配置、LLM 调用 |
| 渲染进程 | HTML + CSS + JavaScript | UI 渲染，纯原生无框架 |
| 构建工具 | Vite (渲染进程) + tsc (主进程) | |
| 打包 | electron-builder | 生成安装包/便携版 |
| 参考原型 | Python tkinter (disk_analyzer_v2.py) | 原始功能实现 |

### 2.2 目录结构

```
treesize/
├── src/
│   ├── main/                    # Electron 主进程
│   │   ├── main.ts              # 入口，IPC 路由，窗口管理
│   │   ├── scanner.ts           # 文件扫描引擎
│   │   ├── llm.ts               # LLM API 调用（流式 SSE）
│   │   ├── config.ts            # 配置持久化 (JSON)
│   │   └── preload.ts           # contextBridge 安全暴露 API
│   ├── renderer/
│   │   ├── index.html           # 完整 UI（HTML + CSS + JS 一体）
│   │   └── vite.config.ts       # Vite 构建配置
│   └── shared/
│       └── types.ts             # IPC 通道名 + 共享类型定义
├── dist/                        # 编译输出
├── disk_analyzer_v2.py          # Python 原型（功能参考）
├── design-preview.html          # UI 设计稿（视觉参考）
├── forge.config.ts              # Electron Forge 配置
├── electron-builder.yml         # electron-builder 配置
├── package.json
└── tsconfig.main.json
```

### 2.3 进程通信架构

```
┌──────────────────────┐         IPC          ┌──────────────────────┐
│    渲染进程 (Renderer) │ ◄══════════════════► │    主进程 (Main)      │
│                      │   contextBridge      │                      │
│  index.html          │   window.api.*       │  scanner.ts          │
│  - 扫描 UI           │                      │  - FastScanner       │
│  - 结果展示          │   ipcRenderer        │  - 重复文件检测      │
│  - AI 分析面板       │   .send/.invoke      │                      │
│  - 设置页面          │                      │  llm.ts              │
│                      │                      │  - LLMAnalyzer       │
│                      │                      │  - SSE 流式解析      │
│                      │                      │                      │
│                      │                      │  config.ts           │
│                      │                      │  - ConfigManager     │
│                      │                      │  - 历史记录          │
└──────────────────────┘                      └──────────────────────┘
```

### 2.4 IPC 通道定义

| 通道 | 方向 | 用途 |
|------|------|------|
| `scan:start` | Renderer → Main | 开始扫描（传入路径） |
| `scan:stop` | Renderer → Main | 取消扫描 |
| `scan:progress` | Main → Renderer | 扫描进度更新 |
| `scan:result` | Main → Renderer | 扫描完成，返回结果 |
| `scan:error` | Main → Renderer | 扫描出错 |
| `disk:info` | Renderer → Main | 获取磁盘分区信息 |
| `llm:analyze` | Renderer → Main | 开始 AI 分析 |
| `llm:stop` | Renderer → Main | 停止 AI 分析 |
| `llm:stream` | Main → Renderer | AI 流式 token |
| `llm:done` | Main → Renderer | AI 分析完成 |
| `file:open` | Renderer → Main | 打开文件/目录 |
| `file:delete` | Renderer → Main | 删除文件（回收站/永久） |
| `file:copy-path` | Renderer → Main | 复制路径到剪贴板 |
| `config:get` | Renderer → Main | 读取配置 |
| `config:set` | Renderer → Main | 保存配置 |
| `app:theme` | Renderer → Main | 切换主题 |
| `export:csv/json/md` | Renderer → Main | 导出报告 |

---

## 3. 功能模块详解

### 3.1 文件扫描引擎 (scanner.ts)

**类：** `FastScanner`

**职责：** 递归扫描目录，收集文件统计信息

**核心算法：**
- 单线程 `fs.opendir` 递归遍历（可靠，避免 race condition）
- 最大深度保护（`MAX_DEPTH = 30`）
- 跳过符号链接和 junction
- 进度节流报告（每 150ms 更新一次）
- 支持取消（`AbortController`）

**扫描输出 — `ScanResult`：**

```typescript
interface ScanResult {
  topDirs: [string, number][];         // [路径, 大小] — Top N 目录
  topFiles: [string, number, number][];// [路径, 大小, mtime] — Top N 文件
  junkDirs: [string, number][];        // [路径, 大小] — 可清理目录
  extStats: [string, number][];        // [扩展名, 总大小] — 文件类型统计
  ageGroups: Record<string, number>;   // 年龄分组 → 文件数
  dirSizeCache: Record<string, number>;// 路径 → 目录大小缓存
  duplicates: [number, number, string[]][]; // [大小, mtime, 路径列表]
  totalUsed: number;                   // 总已用字节
  scanTime: number;                    // 扫描耗时(秒)
  scannedItems: number;                // 扫描文件数
}
```

**重复文件检测（两阶段）：**
1. **阶段一：** 按文件大小分组 → 对同大小文件计算 64KB 头部哈希（xxhash，多线程并发）
2. **阶段二：** 头部哈希匹配的文件 → 计算全文件哈希确认

**输入：** 扫描路径 + 配置（排除目录、Top N、是否检测重复）
**输出：** `ScanResult` 对象

---

### 3.2 LLM 分析器 (llm.ts)

**类：** `LLMAnalyzer`

**职责：** 调用 OpenAI 兼容 API，对扫描结果进行流式智能分析

**工作流程：**
1. 将 `ScanResult` 格式化为结构化 prompt
2. 发起 SSE 流式请求（`POST /chat/completions`，`stream: true`）
3. 逐 token 回调渲染进程
4. 支持取消（`AbortController`）

**System Prompt 结构：**
```
你是一名专业的磁盘空间分析顾问。
请按以下结构输出：
## 📊 空间概览
## 🔍 大目录分析
## 📄 大文件分析
## 🗑️ 清理建议
## ⚠️ 注意事项
```

**输入：** `ScanResult` + LLM 配置（API URL、Key、Model）
**输出：** 流式 Markdown 文本

---

### 3.3 配置管理 (config.ts)

**类：** `ConfigManager`

**持久化文件：** `~/.disk_analyzer/config.json`

**配置项：**

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `excludeDirs` | string[] | [] | 扫描排除目录 |
| `customJunkDirs` | string[] | [] | 自定义可清理目录 |
| `lastScanPath` | string | "" | 上次扫描路径 |
| `topN` | number | 15 | Top N 数量 |
| `llmApiUrl` | string | "" | LLM API 地址 |
| `llmApiKey` | string | "" | LLM API Key |
| `llmModel` | string | "" | LLM 模型名 |
| `llmTemperature` | number | 0.3 | LLM 温度 |
| `duplicateDetection` | boolean | false | 是否检测重复文件 |
| `simulateMode` | boolean | false | 模拟删除模式 |
| `theme` | 'light'\|'dark' | 'light' | 主题 |

**历史记录：** `~/.disk_analyzer/history.json`
```typescript
interface HistoryEntry {
  timestamp: number;
  scanPath: string;
  totalUsed: number;
  scanTime: number;
  scannedItems: number;
  cleanableSize: number;
  duplicateGroups: number;
}
```

---

### 3.4 UI 模块 (index.html)

**架构：** 单文件 HTML，内嵌 CSS + JS，无外部依赖

**布局结构：**
```
┌─────────────────────────────────────────────────┐
│  侧边栏 (220px)  │      主内容区                │
│                   │  ┌─────────────────────────┐ │
│  ┌─────────────┐  │  │  顶栏 (标题+主题切换)   │ │
│  │ Logo + 标题  │  │  ├─────────────────────────┤ │
│  ├─────────────┤  │  │                         │ │
│  │ 分析         │  │  │   内容面板              │ │
│  │  · 概览      │  │  │   (按导航切换)          │ │
│  │  · 大文件    │  │  │                         │ │
│  │  · 重复文件  │  │  │                         │ │
│  │  · 临时文件  │  │  │                         │ │
│  ├─────────────┤  │  │                         │ │
│  │ 工具         │  │  │                         │ │
│  │  · AI 清理   │  │  │                         │ │
│  │  · 历史记录  │  │  │                         │ │
│  │  · 设置      │  │  │                         │ │
│  ├─────────────┤  │  └─────────────────────────┘ │
│  │ 磁盘使用条   │  │                             │
│  │ 版本信息     │  │                             │
│  └─────────────┘  │                             │
└─────────────────────────────────────────────────┘
```

**7 个内容面板：**

#### 面板 1：概览 (Overview)
- 4 个统计卡片：总容量、已用、可用、可清理
- 环形图：文件类型分布（Canvas `create_arc`）
- 健康指标条：清理潜力、重复浪费
- Top 目录表格（Treeview）

#### 面板 2：大文件 (Large Files)
- 头部：文件数统计 + 排序下拉 + 导出按钮
- 表格：文件名、路径、类型标签、修改日期、大小
- 分页控件

#### 面板 3：重复文件 (Duplicates)
- 头部：分组数 + 可回收空间 + 重新扫描/清理按钮
- 可折叠卡片组，每组：
  - 头部：文件名 + 副本数标签 + 大小
  - 行：复选框 + 路径 + 日期 + 大小 + "建议删除" 标签

#### 面板 4：临时文件 (Temp Files)
- 头部：可清理总大小 + "安全" 标签 + 清理按钮
- 按类别分组：浏览器缓存、系统临时、应用缓存、回收站等
- 每类可展开，显示文件列表

#### 面板 5：AI 清理 (AI Clean)
- 左侧 2/3：工具栏（开始/停止/清空/复制/导出）+ 流式输出文本区
- 右侧 1/3：快捷操作建议卡片 + 风险等级说明

#### 面板 6：历史记录 (History)
- 3 个汇总卡片：累计释放、平均释放、增长趋势
- 柱状图：存储趋势（Canvas）
- 时间线：每次扫描记录

#### 面板 7：设置 (Settings)
- 设置分组：通用设置、扫描设置、LLM 配置、清理安全、关于
- 每行：标签 + 描述 + 控件（开关/输入框/下拉框）

---

## 4. 数据流

### 4.1 扫描流程

```
用户输入路径 → 点击"开始扫描"
    │
    ▼
Renderer: window.api.startScan(path)
    │  ipcRenderer.send('scan:start', path)
    ▼
Main: main.ts SCAN_START handler
    │  1. 验证路径存在且是目录
    │  2. 创建 FastScanner 实例
    │  3. 在新线程中调用 scanner.scan()
    ▼
Scanner: FastScanner.scan()
    │  1. 递归遍历目录树
    │  2. 收集统计数据（大小、类型、年龄等）
    │  3. 定期发送 scan:progress
    │  4. 可选：两阶段重复文件检测
    │  5. 完成后发送 scan:result
    ▼
Renderer: 收到 scan:result
    │  1. 更新概览统计卡片
    │  2. 填充大文件表格
    │  3. 填充重复文件分组
    │  4. 填充临时文件分类
    │  5. 绘制环形图
    │  6. 保存历史记录
    ▼
用户浏览结果 → 可选择清理操作
```

### 4.2 AI 分析流程

```
用户点击"开始分析"
    │
    ▼
Renderer: window.api.llmAnalyze(scanResult)
    │  ipcRenderer.send('llm:analyze', scanResult)
    ▼
Main: LLMAnalyzer.analyze()
    │  1. 将 ScanResult 格式化为 prompt
    │  2. POST 请求 LLM API (stream: true)
    │  3. 解析 SSE 数据流
    │  4. 每个 token → llm:stream
    │  5. 完成 → llm:done
    ▼
Renderer: 逐 token 追加到输出区
    │  自动滚动到底部
    ▼
完成: 显示完整分析结果，可复制/导出
```

### 4.3 文件操作流程

```
用户右键文件 → 上下文菜单
    │
    ├── 打开文件 → window.api.openFile(path)
    │               → shell.openPath(path)
    │
    ├── 打开所在目录 → window.api.openDir(path)
    │                  → shell.showItemInFolder(path)
    │
    ├── 复制路径 → window.api.copyPath(path)
    │              → clipboard.writeText(path)
    │
    └── 删除文件 → window.api.deleteFile(path, permanent?)
                    → fs.rm / shell.trashItem
```

---

## 5. 函数清单

### 5.1 主进程 (main.ts)

| 函数 | 说明 |
|------|------|
| `createWindow()` | 创建主窗口，加载 index.html |
| `SCAN_START` handler | 验证路径，创建 Scanner，启动扫描 |
| `SCAN_STOP` handler | 取消当前扫描 |
| `DISK_INFO` handler | 读取 `/proc/mounts` 或 `wmic` 获取分区信息 |
| `LLM_ANALYZE` handler | 启动 LLM 流式分析 |
| `LLM_STOP` handler | 取消 LLM 分析 |
| `CONFIG_GET/SET` handler | 读写配置 |
| `EXPORT_CSV/JSON/MD` handler | 导出报告到文件 |
| `APP_THEME` handler | 切换主题并持久化 |
| `FILE_OPEN/DELETE/COPY` handler | 文件操作 |
| `csvEscape()` | CSV 注入防护 |
| `getDiskPartitions()` | 跨平台获取磁盘分区 |

### 5.2 扫描引擎 (scanner.ts)

| 函数 | 说明 |
|------|------|
| `FastScanner.scan()` | 主扫描入口 |
| `FastScanner.scanDir(dir, depth)` | 递归扫描目录 |
| `FastScanner.detectDuplicates()` | 两阶段重复文件检测 |
| `FastScanner.hashFileHead(path, bytes)` | 计算文件头部哈希（带超时） |
| `FastScanner.hashFileFull(path)` | 计算全文件哈希（带超时） |
| `FastScanner.reportProgress(msg)` | 发送进度更新 |
| `FastScanner.abort()` | 取消扫描 |

### 5.3 LLM 分析器 (llm.ts)

| 函数 | 说明 |
|------|------|
| `LLMAnalyzer.analyze(result, onToken, onDone)` | 发起流式分析 |
| `LLMAnalyzer.stop()` | 取消分析 |
| `LLMAnalyzer.buildPrompt(result)` | 将 ScanResult 格式化为 prompt |
| `parseSSEStream(response, callbacks)` | 解析 SSE 数据流 |

### 5.4 配置管理 (config.ts)

| 函数 | 说明 |
|------|------|
| `ConfigManager.get()` | 获取当前配置 |
| `ConfigManager.set(partial)` | 更新配置 |
| `ConfigManager.getHistory()` | 获取扫描历史 |
| `ConfigManager.addHistory(entry)` | 添加历史记录 |
| `ConfigManager.getGeometry()` | 获取窗口位置 |
| `ConfigManager.setGeometry(data)` | 保存窗口位置 |

### 5.5 渲染进程 (index.html)

| 函数 | 说明 |
|------|------|
| `startScan(path)` | 发起扫描请求 |
| `populateOverview(result)` | 填充概览面板 |
| `populateLargeFiles(result)` | 填充大文件表格 |
| `populateDuplicates(result)` | 填充重复文件面板 |
| `populateTempFiles(result)` | 填充临时文件面板 |
| `populateHistory()` | 填充历史记录面板 |
| `loadSettings()` | 加载设置面板 |
| `showPanel(name)` | 切换侧边栏导航 |
| `drawDonut(canvas, data)` | 绘制环形图 |
| `formatSize(bytes)` | 格式化文件大小 |
| `escapeHtml(str)` | XSS 防护 |
| `startLlmAnalysis()` | 启动 AI 分析 |
| `exportReport(format)` | 导出报告 |

---

## 6. 文件系统交互

### 6.1 读取

| 路径 | 用途 |
|------|------|
| 用户指定的扫描路径 | 递归遍历目录 |
| `/proc/mounts` (Linux) | 获取磁盘分区信息 |
| `~/.disk_analyzer/config.json` | 读取配置 |
| `~/.disk_analyzer/history.json` | 读取历史记录 |

### 6.2 写入

| 路径 | 用途 |
|------|------|
| `~/.disk_analyzer/config.json` | 保存配置 |
| `~/.disk_analyzer/history.json` | 保存历史记录 |
| 用户选择的导出路径 | 导出 CSV/JSON/MD 报告 |
| 回收站 / 永久删除 | 文件清理操作 |

### 6.3 外部调用

| 命令 | 平台 | 用途 |
|------|------|------|
| `explorer /select,` | Windows | 在资源管理器中定位文件 |
| `open` | macOS | 打开文件/目录 |
| `xdg-open` | Linux | 打开文件/目录 |
| LLM API (HTTP) | 全平台 | AI 分析请求 |

---

## 7. 安全设计

| 风险 | 防护措施 |
|------|----------|
| XSS | `escapeHtml()` 转义所有用户数据 |
| CSV 注入 | `csvEscape()` 对公式触发字符加前缀 |
| 路径遍历 | 扫描路径由用户通过文件对话框选择 |
| API Key 泄露 | Key 仅存在主进程内存和本地配置文件 |
| 文件删除 | 默认使用回收站，永久删除需二次确认 |
| 扫描卡死 | 文件哈希计算 30s 超时 + AbortController |
| 权限不足 | 扫描前检查管理员权限并提示 |

---

## 8. 性能指标

| 指标 | 目标 |
|------|------|
| 扫描速度 | > 10,000 文件/秒 (SSD) |
| 内存占用 | < 200MB (100万文件) |
| 启动时间 | < 2 秒 |
| UI 响应 | 扫描期间 UI 不卡顿（异步架构） |
| 重复检测 | 1000 个大文件 < 30 秒 |

---

## 9. 依赖清单

### 9.1 运行时依赖

| 包 | 用途 |
|------|------|
| `electron` | 桌面应用框架 |
| `node:fs` | 文件系统操作 |
| `node:crypto` | 文件哈希计算 |
| `node:child_process` | 打开文件管理器 |

### 9.2 开发依赖

| 包 | 用途 |
|------|------|
| `typescript` | 主进程类型安全 |
| `vite` | 渲染进程构建 |
| `electron-builder` | 应用打包 |
| `@electron-forge/*` | 应用打包（备选） |

### 9.3 零外部 UI 依赖

渲染进程完全使用原生 HTML + CSS + JavaScript，无 React/Vue/Angular 等框架依赖。

---

## 10. 与 Python 原型的功能对照

| Python 功能 | Electron 实现 | 状态 |
|-------------|---------------|------|
| 目录递归扫描 | scanner.ts scanDir() | ✅ |
| Top N 大文件/大目录 | scanner.ts 堆排序 | ✅ |
| 文件类型统计 | scanner.ts extStats | ✅ |
| 文件年龄分析 | scanner.ts ageGroups | ✅ |
| 重复文件检测 | scanner.ts detectDuplicates() | ✅ |
| 垃圾目录识别 | scanner.ts junkPaths | ✅ |
| 配置管理 | config.ts ConfigManager | ✅ |
| LLM AI 分析 | llm.ts LLMAnalyzer | ✅ |
| 暗色主题 | index.html CSS 变量 | ✅ |
| 导出报告 (CSV/JSON) | main.ts export handlers | ✅ |
| 扫描缓存 | config.ts history | ✅ |
| 磁盘分区信息 | main.ts getDiskPartitions() | ✅ |
| 上下文菜单 | index.html 右键菜单 | ✅ |
| 窗口几何记忆 | config.ts geometry | ✅ |
| 配置对话框 | index.html 设置面板 | ✅ |
| 目录树浏览 | Python tab_filetree | ❌ 未实现 |
| 文件年龄面板 | Python tab_age | ❌ 未实现 |
| 饼图 | Python _draw_pie_chart | ✅ 环形图替代 |
| 模拟删除模式 | Python simulate_mode_var | ✅ |
