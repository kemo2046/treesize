import * as https from 'https';
import * as http from 'http';
import { ScanResult } from './scanner';

export interface LLMConfig {
  llmApiUrl: string;
  llmApiKey: string;
  llmModel: string;
  llmTemperature: number;
}

export class LLMAnalyzer {
  private aborted = false;

  static readonly SYSTEM_PROMPT = `你是一名专业的磁盘空间分析顾问。用户会给你一份磁盘扫描报告，你需要分析哪些目录和文件占用了大量空间，判断它们是什么、为什么大、是否安全清理，并给出具体的清理建议。

请按以下结构输出：
## 空间概览
## 大目录分析
## 大文件分析
## 清理建议
## 注意事项

注意：
- 按浪费空间从大到小排序
- 对每个目录/文件给出：是什么、为什么大、能否清理、风险等级
- 用中文回答，简洁实用
- 如果有重复文件，重点指出`;

  buildPrompt(result: ScanResult, scanPath: string): string {
    const fmt = (b: number) => {
      if (!Number.isFinite(b) || b < 0) return '0 B';
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let v = b;
      for (const u of units) { if (v < 1024) return v.toFixed(2) + ' ' + u; v /= 1024; }
      return v.toFixed(2) + ' PB';
    };

    const lines: string[] = [];
    lines.push(`扫描路径: ${scanPath}`);
    lines.push(`总用量: ${fmt(result.totalUsed)}`);
    lines.push(`文件数: ${result.scannedItems.toLocaleString()}`);
    lines.push(`扫描耗时: ${result.scanTime.toFixed(2)}s`);
    lines.push('');

    lines.push('## 大目录 Top 15');
    for (const [dir, size] of result.topDirs) {
      lines.push(`  ${fmt(size).padStart(12)}  ${dir}`);
    }
    lines.push('');

    lines.push('## 大文件 Top 15');
    for (const [file, size] of result.topFiles) {
      lines.push(`  ${fmt(size).padStart(12)}  ${file}`);
    }
    lines.push('');

    lines.push('## 文件类型统计');
    for (const [ext, size] of result.extStats) {
      lines.push(`  ${fmt(size).padStart(12)}  ${ext || '(无后缀)'}`);
    }
    lines.push('');

    if (result.junkDirs.length > 0) {
      lines.push('## 可清理目录');
      for (const [dir, size] of result.junkDirs) {
        lines.push(`  ${fmt(size).padStart(12)}  ${dir}`);
      }
      lines.push('');
    }

    if (result.ageGroups) {
      lines.push('## 文件年龄分布');
      for (const [group, count] of Object.entries(result.ageGroups)) {
        if (count > 0) lines.push(`  ${group}: ${count.toLocaleString()} 个文件`);
      }
      lines.push('');
    }

    if (result.duplicates.length > 0) {
      const totalWaste = result.duplicates.reduce((sum, [s, paths]) => sum + s * (paths.length - 1), 0);
      lines.push(`## 重复文件 (${result.duplicates.length} 组, 可回收 ${fmt(totalWaste)})`);
      for (const [size, paths] of result.duplicates.slice(0, 10)) {
        lines.push(`  ${fmt(size)} × ${paths.length} 份:`);
        for (const p of paths) {
          lines.push(`    - ${p}`);
        }
      }
    }

    return lines.join('\n');
  }

  async analyze(
    scanPath: string,
    result: ScanResult,
    config: LLMConfig,
    onToken: (token: string) => void,
    onDone: (fullText: string | null, error: string | null) => void,
  ): Promise<void> {
    this.aborted = false;

    // Build the final URL for /chat/completions
    let apiBase = config.llmApiUrl.replace(/\/+$/, '');
    // If user already included /chat/completions, use as-is
    // If user included /v1 or similar, append /chat/completions
    // If user gave bare host, append /v1/chat/completions
    let finalUrl: string;
    if (apiBase.endsWith('/chat/completions')) {
      finalUrl = apiBase;
    } else if (apiBase.match(/\/v\d+$/)) {
      finalUrl = apiBase + '/chat/completions';
    } else {
      // Assume it's a base URL, append /chat/completions directly
      finalUrl = apiBase + '/chat/completions';
    }

    console.log('[llm] Request URL:', finalUrl);
    console.log('[llm] Model:', config.llmModel);

    const isHttps = finalUrl.startsWith('https://');
    const url = new URL(finalUrl);

    const body = JSON.stringify({
      model: config.llmModel,
      messages: [
        { role: 'system', content: LLMAnalyzer.SYSTEM_PROMPT },
        { role: 'user', content: this.buildPrompt(result, scanPath) },
      ],
      temperature: config.llmTemperature,
      stream: true,
    });

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(body).toString(),
    };
    if (config.llmApiKey) {
      headers['Authorization'] = `Bearer ${config.llmApiKey}`;
    }

    const transport = isHttps ? https : http;

    return new Promise<void>((resolve) => {
      const req = transport.request(
        {
          hostname: url.hostname,
          port: url.port || (isHttps ? 443 : 80),
          path: url.pathname + url.search,
          method: 'POST',
          headers,
          timeout: 120000,
        },
        (res) => {
          if (res.statusCode && res.statusCode >= 400) {
            let errBody = '';
            res.on('data', (chunk: Buffer) => { errBody += chunk.toString(); });
            res.on('end', () => {
              onDone(null, `HTTP ${res.statusCode} (${finalUrl}): ${errBody.slice(0, 200)}`);
              resolve();
            });
            return;
          }

          let buffer = '';
          let fullText = '';

          res.on('data', (chunk: Buffer) => {
            if (this.aborted) return;
            buffer += chunk.toString();

            const frames = buffer.split('\n\n');
            buffer = frames.pop() || '';

            for (const frame of frames) {
              for (const line of frame.split('\n')) {
                if (!line.startsWith('data: ')) continue;
                const data = line.slice(6);
                if (data.trim() === '[DONE]') {
                  onDone(fullText, null);
                  resolve();
                  return;
                }
                try {
                  const parsed = JSON.parse(data);
                  const content = parsed.choices?.[0]?.delta?.content;
                  if (content) {
                    fullText += content;
                    onToken(content);
                  }
                } catch {
                  // ignore malformed JSON
                }
              }
            }
          });

          res.on('end', () => {
            if (!this.aborted) {
              onDone(fullText || null, fullText ? null : '连接中断，未收到完整响应');
            }
            resolve();
          });

          res.on('error', (err: Error) => {
            if (!this.aborted) {
              onDone(null, err.message);
            }
            resolve();
          });
        },
      );

      req.on('error', (err: Error) => {
        if (!this.aborted) {
          onDone(null, `连接失败: ${err.message}`);
        }
        resolve();
      });

      req.on('timeout', () => {
        req.destroy();
        if (!this.aborted) {
          onDone(null, '请求超时');
        }
        resolve();
      });

      req.write(body);
      req.end();
    });
  }

  stop(): void {
    this.aborted = true;
  }
}
