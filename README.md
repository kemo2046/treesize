# 磁盘空间分析工具 v2

一款基于 Python + Tkinter 的磁盘空间分析与清理助手，支持大文件定位、目录树浏览、文件类型统计、重复文件检测，以及 **LLM 智能分析**。

## 功能特性

- **大目录 & 大文件** — 快速定位占用空间最多的目录和文件
- **目录树浏览** — 懒加载目录树，支持大小缓存
- **文件类型统计** — 按扩展名统计空间占用，含饼图可视化
- **建议清理** — 自动识别系统临时文件、缓存、日志等可清理目录
- **文件年龄分析** — 按时间维度分析文件分布
- **重复文件检测** — 基于 xxhash 两阶段哈希校验，精准识别重复大文件
- **LLM 智能分析** — 接入 OpenAI 兼容 API，自动分析扫描结果并给出清理建议
- **暗色/亮色主题** — 一键切换，飞书风格 UI
- **跨平台** — 支持 Windows / macOS / Linux

## 安装依赖

```bash
pip install psutil

# 可选依赖
pip install send2trash    # 回收站支持
pip install xxhash        # 重复文件检测
pip install requests      # LLM 智能分析
```

## 使用方法

```bash
python disk_analyzer_v2.py
```

### Windows 用户

双击 `run.bat` 一键运行（自动设置 UTF-8 编码，避免中文乱码）。

或打包为独立 exe：双击 `build.bat`，完成后在 `dist/` 目录找到 `DiskAnalyzer.exe`。

### 快捷键

| 快捷键 | 功能 |
|--------|------|
| `F5` | 开始扫描 |
| `Ctrl+O` | 浏览选择目录 |
| `Escape` | 停止扫描 / 关闭对话框 |
| `Ctrl+1` ~ `Ctrl+7` | 切换标签页 |

### LLM 智能分析

1. 打开「配置 → LLM 分析」
2. 填入 API 地址（支持 OpenAI / DeepSeek / Ollama / LM Studio 等兼容 API）
3. 填入模型名称和 API Key（本地模型可留空）
4. 扫描完成后切换到「AI 分析」标签页，点击「开始 AI 分析」

## 截图

> 欢迎补充截图

## 许可证

MIT License
