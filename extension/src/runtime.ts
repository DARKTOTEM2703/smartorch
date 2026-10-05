import * as child_process from "child_process";
import * as fs from "fs";
import * as http from "http";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";

export type RuntimeId = "native" | "wsl" | "docker" | "remote";

export interface RuntimeOption {
  id: RuntimeId;
  label: string;
  description: string;
  available: boolean;
  detail: string;
}

interface ServerLaunch {
  cmd: string;
  args: string[];
  shell: boolean;
  cwd?: string;
}

const SMARTORCH_PORT = 8080;

function cfg() {
  return vscode.workspace.getConfiguration("smartorch");
}

function run(cmd: string, args: string[]): string | undefined {
  try {
    const r = child_process.spawnSync(cmd, args, {
      timeout: 8000,
      shell: true,
      encoding: "utf8",
    });
    return r.status === 0 ? `${r.stdout ?? ""}`.trim() : undefined;
  } catch {
    return undefined;
  }
}

/** Ejecuta la CLI de SmartOrch (comando `smartorch` o `python -m smartorch.cli`). */
export function runSmartorchCli(args: string[]): {
  ok: boolean;
  output: string;
} {
  const finder = process.platform === "win32" ? "where" : "which";
  const hasCli = run(finder, ["smartorch"]) !== undefined;
  const cmd = hasCli ? "smartorch" : "python";
  const full = (hasCli ? args : ["-m", "smartorch.cli", ...args]).map((a) =>
    /[\s"]/.test(a) ? `"${a.replace(/"/g, '\\"')}"` : a,
  );
  try {
    const r = child_process.spawnSync(cmd, full, {
      timeout: 15 * 60 * 1000,
      shell: true,
      encoding: "utf8",
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    });
    // eslint-disable-next-line no-control-regex
    const clean = `${r.stdout ?? ""}${r.stderr ?? ""}`
      .replace(/\x1b\[[0-9;]*m/g, "")
      .trim();
    return { ok: r.status === 0, output: clean };
  } catch (e: any) {
    return { ok: false, output: String(e?.message ?? e) };
  }
}

/** Detiene el servidor que SmartOrch abrio en su terminal integrada. */
export async function stopSmartOrchServer(): Promise<boolean> {
  vscode.window.terminals
    .filter((t) => t.name === "SmartOrch Server")
    .forEach((t) => t.dispose());
  for (let i = 0; i < 10; i++) {
    if (!(await isServerRunning())) return true;
    await new Promise((r) => setTimeout(r, 700));
  }
  return !(await isServerRunning());
}

function fileExists(p: string): boolean {
  try {
    fs.accessSync(p);
    return true;
  } catch {
    return false;
  }
}

export function isServerRunning(): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get(`http://localhost:${SMARTORCH_PORT}/health`, (res) =>
      resolve(res.statusCode === 200),
    );
    req.on("error", () => resolve(false));
    req.setTimeout(2000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

/** Carpeta con run.py + paquete smartorch: workspace o paquete python instalado. */
export function findServerDir(): string | undefined {
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    for (const rel of ["server", "."]) {
      const dir = path.join(folder.uri.fsPath, rel);
      if (
        fileExists(path.join(dir, "run.py")) &&
        fileExists(path.join(dir, "smartorch"))
      ) {
        return dir;
      }
    }
  }
  const pkg = run("python", [
    "-c",
    '"import smartorch,os;print(os.path.dirname(os.path.dirname(smartorch.__file__)))"',
  ]);
  if (pkg && fileExists(path.join(pkg, "run.py"))) return pkg;
  return undefined;
}

function nativeLaunch(): ServerLaunch | undefined {
  const configured = cfg().get<string>("serverPath", "");
  if (configured && fileExists(configured)) {
    return { cmd: "python", args: [configured], shell: false };
  }
  const finder = process.platform === "win32" ? "where" : "which";
  if (run(finder, ["smartorch"]) !== undefined) {
    return { cmd: "smartorch", args: ["serve"], shell: true };
  }
  const dir = findServerDir();
  if (dir) {
    return { cmd: "python", args: [path.join(dir, "run.py")], shell: false };
  }
  if (run("python", ["-c", '"import smartorch"']) !== undefined) {
    return {
      cmd: "python",
      args: ["-m", "smartorch.cli", "serve"],
      shell: false,
    };
  }
  return undefined;
}

function launchFor(id: RuntimeId): ServerLaunch | undefined {
  if (id === "native") return nativeLaunch();
  if (id === "wsl") {
    return {
      cmd: "wsl",
      args: ["-e", "bash", "-lc", "smartorch serve"],
      shell: false,
    };
  }
  if (id === "docker") {
    const dir = findServerDir();
    return dir
      ? {
          cmd: "docker",
          args: ["compose", "up", "-d"],
          shell: false,
          cwd: dir,
        }
      : undefined;
  }
  return undefined;
}

export function currentRuntime(): RuntimeId {
  const v = cfg().get<string>("runtime", "auto");
  return v === "wsl" || v === "docker" || v === "remote" ? v : "native";
}

export function detectRuntimes(): RuntimeOption[] {
  const plat = process.platform;
  const osName =
    plat === "win32" ? "Windows" : plat === "darwin" ? "macOS" : "Linux";

  const py = run("python", ["--version"]) ?? run("python3", ["--version"]);
  const cli = run(plat === "win32" ? "where" : "which", ["smartorch"]);
  const options: RuntimeOption[] = [
    {
      id: "native",
      label: `${osName} (nativo)`,
      description: "Corre directo en tu sistema con Python. Lo más simple.",
      available: !!(py || cli),
      detail: cli
        ? "Comando smartorch encontrado"
        : py
          ? `${py} — falta instalar: pip install -e <carpeta server>`
          : "Python no detectado",
    },
  ];

  if (plat === "win32") {
    const distros = run("wsl", ["-l", "-q"]);
    options.push({
      id: "wsl",
      label: "Linux (WSL)",
      description:
        "Corre dentro de WSL. Mejor rendimiento de Python y herramientas Linux.",
      available: !!distros && distros.replace(/\0/g, "").trim().length > 0,
      detail: distros
        ? "WSL detectado — instala smartorch dentro de la distro"
        : "WSL no detectado (wsl --install)",
    });
  }

  const docker = run("docker", ["compose", "version"]);
  options.push({
    id: "docker",
    label: "Docker",
    description:
      "Levanta Ollama + SmartOrch con un comando. Sin instalar Python.",
    available: !!docker,
    detail: docker ? docker.split("\n")[0] : "Docker no detectado",
  });

  options.push({
    id: "remote",
    label: "Servidor remoto",
    description: "Conéctate a un SmartOrch que corre en otra máquina o VPS.",
    available: true,
    detail: "Indica la URL del servidor",
  });

  return options;
}

/** Apunta los modelos de ~/.continue/config.yaml (los de SmartOrch) a otra URL. */
function patchContinueApiBase(apiUrl: string) {
  const file = path.join(os.homedir(), ".continue", "config.yaml");
  if (!fileExists(file)) return;
  const text = fs.readFileSync(file, "utf8");
  const next = text.replace(/^(\s*apiBase:\s*)\S+\/v1\s*$/gm, `$1${apiUrl}`);
  if (next !== text) fs.writeFileSync(file, next, "utf8");
}

export async function selectRuntime(id: RuntimeId, remoteUrl?: string) {
  const target = vscode.ConfigurationTarget.Global;
  await cfg().update("runtime", id, target);
  if (id === "remote") {
    const url = (remoteUrl ?? "").trim().replace(/\/+$/, "");
    if (!/^https?:\/\//.test(url)) {
      vscode.window.showErrorMessage(
        "SmartOrch: la URL debe empezar con http:// o https://",
      );
      return;
    }
    const apiUrl = url.endsWith("/v1") ? url : `${url}/v1`;
    await cfg().update("apiUrl", apiUrl, target);
    patchContinueApiBase(apiUrl);
    vscode.window.showInformationMessage(
      `SmartOrch: usando servidor remoto ${apiUrl}`,
    );
    return;
  }
  await cfg().update("apiUrl", "http://localhost:8080/v1", target);
  patchContinueApiBase("http://localhost:8080/v1");
  await startSmartOrchServer(undefined, true);
}

export async function startSmartOrchServer(
  context?: vscode.ExtensionContext,
  force = false,
) {
  if (!force && !cfg().get<boolean>("autoStart", true)) return;
  const runtime = currentRuntime();
  if (runtime === "remote") return;

  if (await isServerRunning()) {
    void vscode.window.setStatusBarMessage(
      "⚡ SmartOrch server ya esta activo",
      3000,
    );
    return;
  }

  const launch = launchFor(runtime);
  if (!launch) {
    void vscode.window
      .showWarningMessage(
        "SmartOrch: no se pudo iniciar el servidor. Elige dónde correrlo en la configuración inicial.",
        "Abrir configuración",
      )
      .then((sel) => {
        if (sel === "Abrir configuración") {
          void vscode.commands.executeCommand("smartorch.onboarding");
        }
      });
    return;
  }

  void vscode.window.setStatusBarMessage(
    "⚡ Iniciando SmartOrch server...",
    5000,
  );
  // Terminal integrada: se ven los logs sin salir de VS Code
  const quote = (a: string) => (/\s/.test(a) ? `"${a}"` : a);
  const commandLine = [launch.cmd, ...launch.args.map(quote)].join(" ");
  const existing = vscode.window.terminals.find(
    (t) => t.name === "SmartOrch Server",
  );
  existing?.dispose();
  const term = vscode.window.createTerminal({
    name: "SmartOrch Server",
    cwd: launch.cwd,
    iconPath: new vscode.ThemeIcon("server"),
  });
  term.sendText(commandLine);
  term.show(true);

  let attempts = 0;
  const check = setInterval(async () => {
    if (await isServerRunning()) {
      clearInterval(check);
      void vscode.window.setStatusBarMessage(
        "⚡ SmartOrch listo en :8080",
        5000,
      );
    } else if (++attempts > 60) {
      clearInterval(check);
      void vscode.window
        .showWarningMessage(
          "SmartOrch: el servidor no respondio a tiempo. Revisa la opción elegida en la configuración inicial.",
          "Abrir configuración",
        )
        .then((sel) => {
          if (sel) void vscode.commands.executeCommand("smartorch.onboarding");
        });
    }
  }, 1000);
}
