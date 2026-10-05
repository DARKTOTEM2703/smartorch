/** Puente que corre dentro del webview: da a la interfaz un transporte que pasa por la extension. */
export function bridgeScript(workspace: string): string {
  return `
(() => {
  const vscode = acquireVsCodeApi();
  let seq = 0;
  const calls = new Map();
  const streams = new Map();
  window.addEventListener('message', (e) => {
    const m = e.data;
    if (!m) return;
    if (m.type === 'http-result') {
      const c = calls.get(m.id); calls.delete(m.id);
      if (c) m.ok ? c.resolve(m.data) : c.reject(new Error(m.error || ('HTTP ' + m.status)));
    } else if (m.type === 'stream-event') {
      const s = streams.get(m.id); if (s) s.onEvent(m.event);
    } else if (m.type === 'stream-end' || m.type === 'stream-error') {
      const s = streams.get(m.id); streams.delete(m.id);
      if (s) m.type === 'stream-end' ? s.resolve() : s.reject(new Error(m.message || 'error de streaming'));
    }
  });
  const request = (path, opts) => new Promise((resolve, reject) => {
    const id = ++seq; calls.set(id, { resolve, reject });
    vscode.postMessage({ smartorch: true, type: 'http', id, path, method: opts && opts.method, body: opts && opts.body });
  });
  window.SmartOrchNative = {
    workspace: ${JSON.stringify(workspace)},
    post: (msg) => vscode.postMessage(msg),
    transport: {
      request,
      stream: (path, body, onEvent, signal) => new Promise((resolve, reject) => {
        const id = ++seq; streams.set(id, { onEvent, resolve, reject });
        if (signal) signal.addEventListener('abort', () => {
          vscode.postMessage({ smartorch: true, type: 'abort', id });
          const e = new Error('abortado'); e.name = 'AbortError'; reject(e);
        });
        vscode.postMessage({ smartorch: true, type: 'stream', id, path, body });
      }),
      config: () => request('/smartorch/ui-config'),
      health: () => request('/health'),
    },
  };
})();`;
}
