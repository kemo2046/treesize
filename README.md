# 磁盘空间分析器 v4.0.0

跨平台磁盘空间分析工具，支持 Windows / macOS / Linux。

## 功能

- **磁盘扫描** — 快速递归扫描目录，统计文件大小、类型分布、年龄分组
- **大文件/大目录** — Top N 排序，一键定位占用空间最多的文件和目录
- **重复文件检测** — 基于 SHA256 哈希（先头部后全文），找出重复文件并估算可回收空间
- **垃圾目录识别** — 平台感知的临时文件/缓存目录检测（Windows/Linux/macOS），支持自定义
- **目录树浏览** — 交互式目录树，实时加载子目录，支持搜索、展开/折叠
- **历史记录** — 自动记录每次扫描结果，趋势图可视化
- **AI 智能分析** — 接入 OpenAI 兼容 API（GPT、DeepSeek 等），流式输出 Markdown 格式的清理建议
- **导出报告** — 支持 CSV、JSON、Markdown 三种格式导出
- **右键菜单** — 打开文件/目录、复制路径、移至回收站/永久删除
- **深色模式** — 一键切换明暗主题

## 技术栈

| 层级 | 技术 |
|------|------|
| 桌面框架 | Electron 35 |
| 主进程 | TypeScript (Node.js) |
| 渲染进程 | HTML + CSS + JavaScript（无框架） |
| 构建 | Vite (渲染) + tsc (主进程) |
| 打包 | electron-builder |

## 开发

```bash
npm install
npm run build        # 编译主进程 + 渲染进程
npm start            # 启动 Electron 开发模式
npm run test:scanner # 运行 Scanner 集成测试
```

## 打包

```bash
npm run make:win     # Windows x64 zip
npm run make:linux   # Linux AppImage
npm run make:mac     # macOS dmg
```

打包产物输出到 `release/` 目录。

## 项目结构

```
src/
├── main/
│   ├── main.ts          # Electron 入口，IPC 路由，窗口管理
│   ├── scanner.ts       # 文件扫描引擎（递归、哈希、去重）
│   ├── llm.ts           # LLM API 流式调用
│   ├── config.ts        # 配置与历史持久化
│   └── preload.ts       # contextBridge 安全暴露 API
├── renderer/
│   ├── index.html       # 完整 UI（HTML + CSS + JS）
│   └── vite.config.ts   # Vite 构建配置
└── shared/
    └── types.ts         # IPC 通道名 + 共享类型
```

## 配置

在设置页面可配置：

- **排除目录** — 扫描时跳过的路径
- **自定义垃圾目录** — 额外的可清理目录
- **Top N 数量** — 显示前 N 个大文件/大目录
- **重复文件检测** — 开启后扫描时检测 ≥100MB 的重复文件
- **LLM API** — OpenAI 兼容的 API 地址、Key、模型、温度
