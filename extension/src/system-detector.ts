/**
 * SmartOrch System Detector
 * Analiza el PC del usuario y recomienda el stack óptimo de modelos.
 */
import * as os from 'os';
import * as fs from 'fs';
import * as path from 'path';
import { execSync } from 'child_process';

export interface SystemInfo {
  os:         string;       // "Windows 11" | "Ubuntu 22.04" | "macOS 14"
  arch:       string;       // "x64" | "arm64"
  ramGb:      number;
  cpuCores:   number;
  cpuModel:   string;
  gpu:        GpuInfo | null;
  python:     string | null;  // "3.11.4" | null
  ollama:     OllamaInfo | null;
  diskFreeGb: number;
  workspace:  WorkspaceInfo;
}

export interface GpuInfo {
  name:    string;
  vramMb:  number;
  vendor:  'nvidia' | 'amd' | 'intel' | 'apple' | 'unknown';
}

export interface OllamaInfo {
  version:       string;
  running:       boolean;
  pulledModels:  string[];
}

export interface WorkspaceInfo {
  languages:   string[];   // ["python", "typescript", "dart"]
  frameworks:  string[];   // ["react", "fastapi", "flutter"]
  fileCount:   number;
}

// ── Recomendación de modelos según VRAM ──────────────────────────────────────

export interface ModelStack {
  tier:        'minimal' | 'standard' | 'full' | 'pro';
  description: string;
  models: {
    name:    string;
    role:    string;
    sizeGb:  number;
    pull:    string;  // comando ollama pull
  }[];
  totalVramGb:  number;
  totalDiskGb:  number;
}

export function recommendStack(sys: SystemInfo): ModelStack {
  const vramMb = sys.gpu?.vramMb ?? 0;
  const vramGb = vramMb / 1024;

  if (vramGb >= 12 || (!sys.gpu && sys.ramGb >= 24)) {
    return {
      tier: 'pro',
      description: 'Stack Pro — modelos 14B, máxima calidad',
      models: [
        { name: 'qwen2.5-coder:14b', role: 'Código (calidad alta)', sizeGb: 8.9,  pull: 'ollama pull qwen2.5-coder:14b' },
        { name: 'deepseek-r1:14b',   role: 'Agente + razonamiento', sizeGb: 9.0,  pull: 'ollama pull deepseek-r1:14b' },
        { name: 'nomic-embed-text',  role: 'RAG / embeddings',      sizeGb: 0.27, pull: 'ollama pull nomic-embed-text' },
      ],
      totalVramGb: 10, totalDiskGb: 18.2,
    };
  }
  if (vramGb >= 7 || (!sys.gpu && sys.ramGb >= 16)) {
    return {
      tier: 'full',
      description: 'Stack completo — 7-8B, mejor balance calidad/velocidad',
      models: [
        { name: 'qwen2.5-coder:7b', role: 'Código (recomendado)',  sizeGb: 4.7,  pull: 'ollama pull qwen2.5-coder:7b' },
        { name: 'hermes3:8b',       role: 'Chat / Agente / YARA',  sizeGb: 4.9,  pull: 'ollama pull hermes3:8b' },
        { name: 'nomic-embed-text', role: 'RAG / embeddings',      sizeGb: 0.27, pull: 'ollama pull nomic-embed-text' },
      ],
      totalVramGb: 7.5, totalDiskGb: 9.9,
    };
  }
  if (vramGb >= 4 || (!sys.gpu && sys.ramGb >= 8)) {
    return {
      tier: 'standard',
      description: 'Stack estándar — 7B código + embeddings (un modelo a la vez)',
      models: [
        { name: 'qwen2.5-coder:7b', role: 'Código + Chat',    sizeGb: 4.7,  pull: 'ollama pull qwen2.5-coder:7b' },
        { name: 'nomic-embed-text', role: 'RAG / embeddings', sizeGb: 0.27, pull: 'ollama pull nomic-embed-text' },
      ],
      totalVramGb: 5, totalDiskGb: 5.0,
    };
  }
  return {
    tier: 'minimal',
    description: 'Stack mínimo — modelos ligeros para hardware limitado',
    models: [
      { name: 'qwen2.5-coder:1.5b', role: 'Código (rápido)',   sizeGb: 1.0,  pull: 'ollama pull qwen2.5-coder:1.5b' },
      { name: 'nomic-embed-text',   role: 'RAG / embeddings',  sizeGb: 0.27, pull: 'ollama pull nomic-embed-text' },
    ],
    totalVramGb: 1.5, totalDiskGb: 1.3,
  };
}

// ── Detección ─────────────────────────────────────────────────────────────────

export async function detect(workspacePaths: string[]): Promise<SystemInfo> {
  const platform = os.platform();

  return {
    os:       _detectOs(),
    arch:     os.arch(),
    ramGb:    Math.round(os.totalmem() / 1_073_741_824),
    cpuCores: os.cpus().length,
    cpuModel: os.cpus()[0]?.model?.trim() ?? 'Desconocido',
    gpu:      _detectGpu(platform),
    python:   _detectPython(platform),
    ollama:   await _detectOllama(),
    diskFreeGb: _detectDiskFree(platform),
    workspace: _detectWorkspace(workspacePaths),
  };
}

function _detectOs(): string {
  const p = os.platform();
  const r = os.release();
  if (p === 'win32') {
    const build = parseInt(r.split('.')[2] ?? '0');
    return build >= 22000 ? `Windows 11 (build ${build})` : `Windows 10 (build ${build})`;
  }
  if (p === 'darwin') return `macOS ${r}`;
  try {
    const rel = execSync('cat /etc/os-release 2>/dev/null | grep PRETTY_NAME', { timeout: 2000 })
      .toString().match(/PRETTY_NAME="(.+)"/)?.[1];
    return rel ?? `Linux ${r}`;
  } catch { return `Linux ${r}`; }
}

function _detectGpu(platform: string): GpuInfo | null {
  // NVIDIA — nvidia-smi
  try {
    const raw = execSync(
      'nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits',
      { timeout: 5000, stdio: ['pipe','pipe','ignore'] }
    ).toString().trim();
    if (raw) {
      const [name, memStr] = raw.split(',').map(s => s.trim());
      const vramMb = parseInt(memStr ?? '0');
      if (name && vramMb > 0) {
        return { name, vramMb, vendor: 'nvidia' };
      }
    }
  } catch { /* no NVIDIA */ }

  // Windows WMIC fallback
  if (platform === 'win32') {
    try {
      const raw = execSync(
        'wmic path win32_VideoController get Name,AdapterRAM /format:csv',
        { timeout: 5000, stdio: ['pipe','pipe','ignore'] }
      ).toString();
      const lines = raw.split('\n').filter(l => l.includes(',') && !l.startsWith('Node'));
      for (const line of lines) {
        const parts = line.split(',');
        const name  = parts[2]?.trim() ?? '';
        const ramB  = parseInt(parts[1]?.trim() ?? '0');
        if (name && ramB > 0) {
          const vramMb  = Math.round(ramB / 1_048_576);
          const vendor  = name.toLowerCase().includes('nvidia') ? 'nvidia'
                        : name.toLowerCase().includes('amd') || name.toLowerCase().includes('radeon') ? 'amd'
                        : name.toLowerCase().includes('intel') ? 'intel' : 'unknown';
          return { name, vramMb, vendor: vendor as any };
        }
      }
    } catch { /* ignore */ }
  }

  // Apple Silicon
  if (platform === 'darwin' && os.arch() === 'arm64') {
    try {
      const raw = execSync(
        'system_profiler SPDisplaysDataType 2>/dev/null | grep "Total Number of Cores"',
        { timeout: 3000 }
      ).toString();
      const ramGb = Math.round(os.totalmem() / 1_073_741_824);
      return { name: 'Apple Silicon (shared RAM)', vramMb: ramGb * 1024 * 0.7, vendor: 'apple' };
    } catch { /* ignore */ }
  }

  return null;
}

function _detectPython(platform: string): string | null {
  const cmds = platform === 'win32'
    ? ['python --version', 'python3 --version', 'py --version']
    : ['python3 --version', 'python --version'];
  for (const cmd of cmds) {
    try {
      const out = execSync(cmd, { timeout: 3000, stdio: ['pipe','pipe','ignore'] }).toString().trim();
      const ver = out.match(/Python (\d+\.\d+\.\d+)/)?.[1];
      if (ver) return ver;
    } catch { /* try next */ }
  }
  return null;
}

async function _detectOllama(): Promise<OllamaInfo | null> {
  // Verificar si Ollama responde
  const isRunning = await _pingOllama();

  // Versión
  let version = 'unknown';
  try {
    const raw = execSync('ollama --version', { timeout: 3000, stdio: ['pipe','pipe','ignore'] }).toString();
    version   = raw.match(/(\d+\.\d+\.\d+)/)?.[1] ?? 'unknown';
  } catch {
    // Intentar rutas no estándar
    const candidates = [
      'D:\\Ollama\\ollama.exe',
      'C:\\Ollama\\ollama.exe',
      process.env.LOCALAPPDATA ? `${process.env.LOCALAPPDATA}\\Programs\\Ollama\\ollama.exe` : '',
    ].filter(Boolean);
    for (const c of candidates) {
      try {
        if (fs.existsSync(c)) {
          const raw = execSync(`"${c}" --version`, { timeout: 3000 }).toString();
          version   = raw.match(/(\d+\.\d+\.\d+)/)?.[1] ?? 'found';
          break;
        }
      } catch { /* try next */ }
    }
  }

  if (!isRunning && version === 'unknown') return null;

  let pulledModels: string[] = [];
  if (isRunning) {
    try {
      const res  = await fetch('http://localhost:11434/api/tags', { signal: AbortSignal.timeout(3000) });
      const data = await res.json() as any;
      pulledModels = (data.models ?? []).map((m: any) => m.name as string);
    } catch { /* no models */ }
  }

  return { version, running: isRunning, pulledModels };
}

async function _pingOllama(): Promise<boolean> {
  try {
    const res = await fetch('http://localhost:11434', { signal: AbortSignal.timeout(2000) });
    return res.ok || res.status < 500;
  } catch { return false; }
}

function _detectDiskFree(platform: string): number {
  try {
    if (platform === 'win32') {
      const raw = execSync(
        'wmic logicaldisk where "DeviceID=\'C:\'" get FreeSpace /format:value',
        { timeout: 3000, stdio: ['pipe','pipe','ignore'] }
      ).toString();
      const match = raw.match(/FreeSpace=(\d+)/);
      if (match) return Math.round(parseInt(match[1]) / 1_073_741_824);
    } else {
      const raw = execSync("df -BG / | awk 'NR==2{print $4}'", { timeout: 3000 }).toString();
      return parseInt(raw.replace('G','').trim()) || 0;
    }
  } catch { /* ignore */ }
  return 0;
}

function _detectWorkspace(workspacePaths: string[]): WorkspaceInfo {
  const langs = new Set<string>();
  const fws   = new Set<string>();
  let   files = 0;

  const EXT_MAP: Record<string, string> = {
    ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
    py: 'python', rs: 'rust', go: 'go', java: 'java', kt: 'kotlin',
    dart: 'dart', swift: 'swift', cs: 'csharp', cpp: 'cpp', c: 'c',
    rb: 'ruby', php: 'php', ex: 'elixir', hs: 'haskell',
  };
  const FW_MARKERS: Record<string, string[]> = {
    react:      ['package.json'],   // checked for content
    vue:        ['vue.config.js', 'vite.config.ts'],
    angular:    ['angular.json'],
    nextjs:     ['next.config.js', 'next.config.ts'],
    flutter:    ['pubspec.yaml'],
    fastapi:    ['requirements.txt'], // checked for content
    django:     ['manage.py'],
    nestjs:     ['nest-cli.json'],
    rust:       ['Cargo.toml'],
    go:         ['go.mod'],
    kotlin:     ['build.gradle.kts'],
  };

  for (const wsPath of workspacePaths.slice(0, 2)) {
    if (!wsPath || !fs.existsSync(wsPath)) continue;
    try {
      _walkDir(wsPath, 0, 3, (f) => {
        files++;
        const ext = path.extname(f).slice(1).toLowerCase();
        const lang = EXT_MAP[ext];
        if (lang) langs.add(lang);

        // Framework markers
        const base = path.basename(f).toLowerCase();
        for (const [fw, markers] of Object.entries(FW_MARKERS)) {
          if (markers.some(m => base === m.toLowerCase())) {
            if (fw === 'react') {
              try {
                const pkg = JSON.parse(fs.readFileSync(f, 'utf8'));
                if (pkg.dependencies?.react || pkg.devDependencies?.react) fws.add('react');
              } catch { /* skip */ }
            } else if (fw === 'fastapi') {
              try {
                const req = fs.readFileSync(f, 'utf8');
                if (req.includes('fastapi')) fws.add('fastapi');
              } catch { /* skip */ }
            } else {
              fws.add(fw);
            }
          }
        }
      });
    } catch { /* ignore unreadable workspace */ }
  }

  return { languages: [...langs], frameworks: [...fws], fileCount: files };
}

function _walkDir(dir: string, depth: number, maxDepth: number, cb: (f: string) => void) {
  if (depth > maxDepth) return;
  let entries: string[];
  try { entries = fs.readdirSync(dir); } catch { return; }
  for (const e of entries) {
    if (e.startsWith('.') || e === 'node_modules' || e === '__pycache__' || e === 'target' || e === 'dist') continue;
    const full = path.join(dir, e);
    try {
      const stat = fs.statSync(full);
      if (stat.isDirectory()) _walkDir(full, depth + 1, maxDepth, cb);
      else cb(full);
    } catch { /* skip */ }
  }
}
