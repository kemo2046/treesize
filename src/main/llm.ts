import { ScanResult } from '../shared/types';

interface LLMOptions {
  apiUrl: string;
  apiKey: string;
  model: string;
  temperature?: number;
  onToken?: (token: string) => void;
  onDone?: () => void;
  onError?: (error: string) => void;
  signal?: AbortSignal;
}

const SYSTEM_PROMPT = `你是一位专业的磁盘空间分析助手。请根据用户提供的磁盘扫描结果，给出专业的分析和清理建议。

请按以下结构输出分析结果：
1. **空间概览** — 总体使用情况评估
2. **大目录分析** — 哪些目录占用最多空间，是否合理
3. **大文件分析** — 哪些文件可以考虑清理
4. **清理建议** — 按优先级排列的清理操作，标注预计释放空间和风险等级
5. **注意事项** — 清理前需要了解的风险和建议

风险等级说明：
- 🟢 无风险：可以安全清理
- 🟡 需确认：建议在清理前确认内容
- 🔴 高风险：可能影响系统或应用正常运行`;

export class LLMAnalyzer {
  private controller: AbortController | null = null;

  async analyze(scanResult: ScanResult, options: LLMOptions): Promise<void> {
    this.controller = new AbortController();
    const signal = options.signal
      ? anySignal([this.controller.signal, options.signal])
      : this.controller.signal;

    const prompt = this.buildPrompt(scanResult);

    try {
      const response = await fetch(`${options.apiUrl}/v1/chat/completions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${options.apiKey}`,
        },
        body: JSON.stringify({
          model: options.model,
          temperature: options.temperature ?? 0.3,
          messages: [
            { role: 'system', content: SYSTEM_PROMPT },
            { role: 'user', content: prompt },
          ],
          stream: true,
        }),
        signal,
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`API error ${response.status}: ${errorText}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith('data: ')) continue;

          const data = trimmed.slice(6);
          if (data === '[DONE]') {
            options.onDone?.();
            return;
          }

          try {
            const parsed = JSON.parse(data);
            const content = parsed.choices?.[0]?.delta?.content;
            if (content) {
              options.onToken?.(content);
            }
          } catch {
            // Ignore malformed JSON chunks
          }
        }
      }

      // Flush remaining buffer (last SSE frame may not end with \n)
      const remaining = buffer.trim();
      if (remaining && remaining.startsWith('data: ')) {
        const tail = remaining.slice(6);
        if (tail !== '[DONE]') {
          try {
            const parsed = JSON.parse(tail);
            const content = parsed.choices?.[0]?.delta?.content;
            if (content) options.onToken?.(content);
          } catch { /* ignore */ }
        }
      }

      options.onDone?.();
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        options.onDone?.();
        return;
      }
      const message = err instanceof Error ? err.message : String(err);
      options.onError?.(message);
    }
  }

  async testConnection(apiUrl: string, apiKey: string): Promise<{ ok: boolean; models?: string[]; error?: string }> {
    try {
      const response = await fetch(`${apiUrl}/v1/models`, {
        headers: { 'Authorization': `Bearer ${apiKey}` },
        signal: AbortSignal.timeout(10000),
      });

      if (!response.ok) {
        return { ok: false, error: `HTTP ${response.status}` };
      }

      const data = await response.json() as { data?: { id: string }[] };
      const models = (data.data || []).map((m) => m.id);
      return { ok: true, models };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      return { ok: false, error: message };
    }
  }

  stop(): void {
    this.controller?.abort();
    this.controller = null;
  }

  private buildPrompt(result: ScanResult): string {
    const formatSize = (bytes: number): string => {
      if (bytes >= 1024 ** 4) return `${(bytes / 1024 ** 4).toFixed(2)} TB`;
      if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
      if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(2)} MB`;
      if (bytes >= 1024) return `${(bytes / 1024).toFixed(2)} KB`;
      return `${bytes} B`;
    };

    const lines: string[] = [];
    lines.push(`## 扫描信息`);
    lines.push(`- 扫描路径: ${result.topDirs[0]?.[0] || '未知'}`);
    lines.push(`- 扫描耗时: ${result.scanTime.toFixed(1)} 秒`);
    lines.push(`- 总计大小: ${formatSize(result.totalUsed)}`);
    lines.push(`- 扫描文件数: ${result.scannedItems.toLocaleString()}`);
    lines.push('');

    lines.push(`## 最大目录 Top ${Math.min(15, result.topDirs.length)}`);
    for (const [dirPath, size] of result.topDirs.slice(0, 15)) {
      lines.push(`- ${formatSize(size)} — ${dirPath}`);
    }
    lines.push('');

    lines.push(`## 最大文件 Top ${Math.min(15, result.topFiles.length)}`);
    for (const [filePath, size, mtime] of result.topFiles.slice(0, 15)) {
      const date = new Date(mtime * 1000).toLocaleDateString('zh-CN');
      lines.push(`- ${formatSize(size)} — ${filePath} (修改: ${date})`);
    }
    lines.push('');

    lines.push(`## 文件类型统计 Top 10`);
    for (const [ext, size] of result.extStats.slice(0, 10)) {
      lines.push(`- ${ext}: ${formatSize(size)}`);
    }
    lines.push('');

    if (result.junkDirs.length > 0) {
      lines.push(`## 可清理目录`);
      for (const [dirPath, size] of result.junkDirs) {
        lines.push(`- ${formatSize(size)} — ${dirPath}`);
      }
      lines.push('');
    }

    lines.push(`## 文件年龄分布`);
    for (const [group, count] of Object.entries(result.ageGroups)) {
      lines.push(`- ${group}: ${count.toLocaleString()} 个文件`);
    }
    lines.push('');

    if (result.duplicates.length > 0) {
      const totalWasted = result.duplicates.reduce((sum, [size, , paths]) => sum + size * (paths.length - 1), 0);
      lines.push(`## 重复文件`);
      lines.push(`- 共 ${result.duplicates.length} 组重复文件`);
      lines.push(`- 浪费空间: ${formatSize(totalWasted)}`);
      lines.push('');
      for (const [size, , paths] of result.duplicates.slice(0, 5)) {
        lines.push(`### ${paths[0].split(/[\\/]/).pop()} (${paths.length} 个副本, 每份 ${formatSize(size)})`);
        for (const p of paths) {
          lines.push(`- ${p}`);
        }
        lines.push('');
      }
    }

    return lines.join('\n');
  }
}

function anySignal(signals: AbortSignal[]): AbortSignal {
  const controller = new AbortController();
  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort(signal.reason);
      return controller.signal;
    }
    signal.addEventListener('abort', () => controller.abort(signal.reason), { once: true });
  }
  return controller.signal;
}
