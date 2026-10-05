/**
 * SmartOrch API Client — comunicación con el servidor local
 */
import * as https from 'https';
import * as http from 'http';

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface ChatResponse {
  content: string;
  model: string;
  taskType?: string;
}

export class SmartOrchClient {
  private apiUrl: string;
  private apiKey: string;
  private model:  string;

  constructor(apiUrl: string, apiKey: string, model: string) {
    this.apiUrl = apiUrl.replace(/\/$/, '');
    this.apiKey = apiKey;
    this.model  = model;
  }

  async chat(messages: ChatMessage[], maxTokens = 2048): Promise<ChatResponse> {
    const body = JSON.stringify({ messages, model: this.model, max_tokens: maxTokens, temperature: 0.3 });
    const data = await this._post('/chat/completions', body);
    return {
      content:  data.choices?.[0]?.message?.content ?? 'Sin respuesta',
      model:    data.model ?? this.model,
      taskType: data.smartorch?.task_type,
    };
  }

  async complete(prompt: string, maxTokens = 128): Promise<string> {
    const body = JSON.stringify({ prompt, model: this.model, max_tokens: maxTokens, temperature: 0.1 });
    const data = await this._post('/completions', body);
    return data.choices?.[0]?.text ?? '';
  }

  /**
   * Streaming chat — llama onChunk por cada fragmento de texto,
   * onStatus por eventos de pipeline, onDone al terminar con stats.
   */
  chatStream(
    messages: ChatMessage[],
    onChunk:  (text: string) => void,
    onDone:   (stats: { promptTokens: number; completionTokens: number; tokensPerSec: number }) => void,
    onStatus?: (step: string) => void,
    maxTokens = 3072,
  ): void {
    const body = JSON.stringify({
      model:      this.model,
      messages,
      stream:     true,
      max_tokens: maxTokens,
      temperature: 0.3,
    });

    const url     = new URL(this.apiUrl + '/chat/completions');
    const lib     = url.protocol === 'https:' ? require('https') : require('http');
    const options = {
      hostname: url.hostname,
      port:     url.port || (url.protocol === 'https:' ? 443 : 80),
      path:     url.pathname + url.search,
      method:   'POST',
      headers:  {
        'Content-Type':   'application/json',
        'Content-Length': Buffer.byteLength(body),
        'Authorization':  `Bearer ${this.apiKey}`,
        'Accept':         'text/event-stream',
      },
    };

    const req = lib.request(options, (res: any) => {
      let buf = '';
      res.on('data', (chunk: Buffer) => {
        buf += chunk.toString();
        const lines = buf.split('\n');
        buf = lines.pop() ?? '';
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;
          const payload = trimmed.slice(5).trim();
          if (payload === '[DONE]') continue;
          try {
            const obj = JSON.parse(payload);
            if (obj.choices) {
              const text = obj.choices[0]?.delta?.content ?? '';
              if (text) onChunk(text);
            } else if (obj.type === 'status' && onStatus) {
              onStatus(obj.step ?? '');
            } else if (obj.type === 'token_stats') {
              onDone({
                promptTokens:     obj.prompt_tokens ?? 0,
                completionTokens: obj.completion_tokens ?? 0,
                tokensPerSec:     obj.tokens_per_sec ?? 0,
              });
            }
          } catch { /* ignore malformed SSE */ }
        }
      });
      res.on('end', () => onDone({ promptTokens: 0, completionTokens: 0, tokensPerSec: 0 }));
    });

    req.on('error', () => {
      onChunk('\n\n*Error: SmartOrch no responde. ¿Está corriendo? → `smartorch serve`*');
      onDone({ promptTokens: 0, completionTokens: 0, tokensPerSec: 0 });
    });
    req.setTimeout(300_000, () => {
      req.destroy();
      onDone({ promptTokens: 0, completionTokens: 0, tokensPerSec: 0 });
    });
    req.write(body);
    req.end();
  }

  async isAlive(): Promise<boolean> {
    try {
      await this._get(this._baseUrl('/health'));
      return true;
    } catch {
      return false;
    }
  }

  async indexWorkspace(rootPath: string): Promise<number> {
    const body = JSON.stringify({ root: rootPath });
    const data = await this._postRaw(this._baseUrl('/smartorch/index'), body);
    return data.chunks ?? 0;
  }

  /** Construye URL relativa a la raíz del servidor (no a /v1) */
  private _baseUrl(path: string): string {
    // apiUrl es algo como http://localhost:8080/v1 — subimos un nivel
    const base = this.apiUrl.replace(/\/v\d+\/?$/, '');
    return base + path;
  }

  private _post(path: string, body: string): Promise<any> {
    return this._postRaw(this.apiUrl + path, body);
  }

  private _postRaw(fullUrl: string, body: string): Promise<any> {
    return new Promise((resolve, reject) => {
      const url     = new URL(fullUrl);
      const options = {
        hostname: url.hostname,
        port:     url.port || (url.protocol === 'https:' ? 443 : 80),
        path:     url.pathname + url.search,
        method:   'POST',
        headers:  {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(body),
          'Authorization': `Bearer ${this.apiKey}`,
        },
      };

      const lib     = url.protocol === 'https:' ? https : http;
      const req     = lib.request(options, (res) => {
        let raw = '';
        res.on('data', (chunk) => raw += chunk);
        res.on('end', () => {
          try { resolve(JSON.parse(raw)); }
          catch { reject(new Error(`JSON inválido: ${raw.slice(0, 200)}`)); }
        });
      });
      req.on('error', reject);
      req.setTimeout(120000, () => { req.destroy(); reject(new Error('Timeout')); });
      req.write(body);
      req.end();
    });
  }

  private _get(path: string): Promise<any> {
    return new Promise((resolve, reject) => {
      const url     = new URL(this.apiUrl + path);
      const options = {
        hostname: url.hostname,
        port:     url.port || 80,
        path:     url.pathname,
        method:   'GET',
        headers:  { 'Authorization': `Bearer ${this.apiKey}` },
      };
      const lib = url.protocol === 'https:' ? https : http;
      const req = lib.request(options, (res) => {
        let raw = '';
        res.on('data', c => raw += c);
        res.on('end', () => {
          try { resolve(JSON.parse(raw)); } catch { resolve({}); }
        });
      });
      req.on('error', reject);
      req.setTimeout(5000, () => { req.destroy(); reject(new Error('Timeout')); });
      req.end();
    });
  }
}
