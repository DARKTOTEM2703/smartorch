import * as child_process from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";

import { apiFetch } from "./bridge";
import {
  currentRuntime,
  runSmartorchCli,
  startSmartOrchServer,
  stopSmartOrchServer,
} from "./runtime";

function cfg() {
  return vscode.workspace.getConfiguration("smartorch");
}

const GLOBAL = vscode.ConfigurationTarget.Global;

export function modelsPath(): string {
  return (
    cfg().get<string>("ollamaModelsPath", "") ||
    process.env.OLLAMA_MODELS ||
    path.join(os.homedir(), ".ollama", "models")
  );
}

export function isCustomModelsPath(): boolean {
  return !!cfg().get<string>("ollamaModelsPath", "");
}

/** Ejecutable de Ollama elegido por el usuario, o "ollama" si usa el del PATH. */
export function ollamaExe(): string {
  const p = cfg().get<string>("ollamaPath", "");
  return p && fs.existsSync(p) ? p : "ollama";
}

export function freeGb(target: string): number | undefined {
  let dir = target;
  while (dir && !fs.existsSync(dir)) {
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  try {
    const s = (fs as any).statfsSync?.(dir);
    return s ? Math.round((s.bavail * s.bsize) / 1_073_741_824) : undefined;
  } catch {
    return undefined;
  }
}

function folderSizeGb(dir: string): number {
  let total = 0;
  const walk = (d: string) => {
    let entries: fs.Dirent[] = [];
    try {
      entries = fs.readdirSync(d, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const full = path.join(d, e.name);
      if (e.isDirectory()) walk(full);
      else {
        try {
          total += fs.statSync(full).size;
        } catch {
          /* ignorar */
        }
      }
    }
  };
  walk(dir);
  return Math.round((total / 1_073_741_824) * 10) / 10;
}

function stopOllama() {
  try {
    if (process.platform === "win32") {
      child_process.spawnSync(
        "taskkill",
        ["/F", "/IM", "ollama.exe", "/IM", "ollama app.exe"],
        { stdio: "ignore" },
      );
    } else {
      child_process.spawnSync("pkill", ["-f", "ollama"], { stdio: "ignore" });
    }
  } catch {
    /* no estaba corriendo */
  }
}

/** Terminal integrada con OLLAMA_MODELS ya aplicado, sin sintaxis de shell. */
export function ollamaTerminal(name: string): vscode.Terminal {
  const env: Record<string, string> = {};
  if (isCustomModelsPath()) env.OLLAMA_MODELS = modelsPath();
  return vscode.window.createTerminal({
    name,
    env,
    iconPath: new vscode.ThemeIcon("cloud-download"),
  });
}

/** Sustituye el `ollama` inicial de un comando por el ejecutable elegido. */
export function withOllamaExe(cmd: string): string {
  const exe = ollamaExe();
  if (exe === "ollama") return cmd;
  const prefix = process.platform === "win32" ? `& "${exe}"` : `"${exe}"`;
  return cmd.replace(/^ollama\b/, prefix);
}

export async function pickModelsFolder(): Promise<boolean> {
  const picked = await vscode.window.showOpenDialog({
    canSelectFolders: true,
    canSelectFiles: false,
    canSelectMany: false,
    openLabel: "Guardar los modelos aquí",
    title: "Carpeta donde Ollama guardará los modelos",
  });
  if (!picked?.length) return false;

  const target = picked[0].fsPath;
  const current = modelsPath();
  if (path.resolve(target) === path.resolve(current)) return false;

  const free = freeGb(target);
  const existing = fs.existsSync(current) ? folderSizeGb(current) : 0;
  const canCopy = existing > 0;
  const info =
    `Nueva carpeta: ${target}` +
    (free !== undefined ? `\nEspacio libre: ${free} GB` : "") +
    (canCopy ? `\nModelos actuales: ${existing} GB en ${current}` : "");

  const actions = canCopy
    ? ["Copiar modelos y usar", "Solo usar (descargar de nuevo)"]
    : ["Usar esta carpeta"];
  const choice = await vscode.window.showInformationMessage(
    "SmartOrch reiniciará Ollama para aplicar el cambio.",
    { modal: true, detail: info },
    ...actions,
  );
  if (!choice) return false;

  await cfg().update("ollamaModelsPath", target, GLOBAL);
  fs.mkdirSync(target, { recursive: true });

  if (process.platform === "win32") {
    child_process.spawnSync("setx", ["OLLAMA_MODELS", target], {
      stdio: "ignore",
    });
  }

  stopOllama();
  const term = ollamaTerminal("SmartOrch — Ollama");
  if (choice === "Copiar modelos y usar") {
    term.sendText(
      process.platform === "win32"
        ? `robocopy "${current}" "${target}" /E /NFL /NDL /NJH`
        : `cp -a "${current}/." "${target}/"`,
    );
  }
  term.sendText(withOllamaExe("ollama serve"));
  term.show(true);
  void vscode.window.showInformationMessage(
    `SmartOrch: modelos de Ollama en ${target}`,
  );
  return true;
}

export async function pickOllamaExecutable(): Promise<boolean> {
  const picked = await vscode.window.showOpenDialog({
    canSelectFiles: true,
    canSelectFolders: false,
    canSelectMany: false,
    openLabel: "Usar este Ollama",
    title: "Selecciona el ejecutable de Ollama",
    filters: process.platform === "win32" ? { Ejecutable: ["exe"] } : undefined,
  });
  if (!picked?.length) return false;
  await cfg().update("ollamaPath", picked[0].fsPath, GLOBAL);
  void vscode.window.showInformationMessage(
    `SmartOrch: usando Ollama en ${picked[0].fsPath}`,
  );
  return true;
}

export async function resetStorage() {
  await cfg().update("ollamaModelsPath", undefined, GLOBAL);
  await cfg().update("ollamaPath", undefined, GLOBAL);
}

// ── Carpeta de datos de SmartOrch (historial, indice, RAG) ───────────────────

export interface DataInfo {
  data_dir: string;
  custom: boolean;
  free_gb: number | null;
  items: { name: string; label: string; exists: boolean; mb: number }[];
  total_mb: number;
}

export async function fetchDataInfo(): Promise<DataInfo | undefined> {
  try {
    return (await (await apiFetch("/smartorch/data-dir")).json()) as DataInfo;
  } catch {
    return undefined;
  }
}

async function applyDataChange(
  args: string[],
  detail: string,
): Promise<boolean> {
  if (currentRuntime() !== "native") {
    void vscode.window.showWarningMessage(
      "Cambiar la carpeta de datos desde aquí solo funciona con la ejecución nativa. En WSL usa `smartorch data <carpeta>` dentro de la distro; en Docker define SMARTORCH_DATA_DIR.",
    );
    return false;
  }
  const choice = await vscode.window.showInformationMessage(
    "SmartOrch detendrá y reiniciará el servidor para mover los datos.",
    { modal: true, detail },
    "Continuar",
  );
  if (choice !== "Continuar") return false;

  return vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: "SmartOrch: moviendo datos…",
    },
    async () => {
      if (!(await stopSmartOrchServer())) {
        void vscode.window.showErrorMessage(
          "SmartOrch: no pude detener el servidor. Si lo abriste en tu propia terminal, deténlo (Ctrl+C) y vuelve a intentarlo.",
        );
        return false;
      }
      const result = runSmartorchCli(args);
      await startSmartOrchServer(undefined, true);
      if (!result.ok) {
        void vscode.window.showErrorMessage(
          `SmartOrch: ${result.output || "no se pudo cambiar la carpeta de datos"}`,
        );
        return false;
      }
      void vscode.window.showInformationMessage(
        `SmartOrch: ${result.output.split("\n").filter(Boolean).slice(0, 2).join(" · ")}`,
      );
      return true;
    },
  );
}

export async function pickDataFolder(): Promise<boolean> {
  const picked = await vscode.window.showOpenDialog({
    canSelectFolders: true,
    canSelectFiles: false,
    canSelectMany: false,
    openLabel: "Guardar los datos de SmartOrch aquí",
    title: "Carpeta para el historial, el índice y el RAG de SmartOrch",
  });
  if (!picked?.length) return false;
  const target = picked[0].fsPath;

  const info = await fetchDataInfo();
  const free = freeGb(target);
  const detail =
    `Nueva carpeta: ${target}` +
    (free !== undefined ? `\nEspacio libre: ${free} GB` : "") +
    (info ? `\nSe copiarán ${info.total_mb} MB desde ${info.data_dir}` : "") +
    "\nLo anterior no se borra: puedes eliminarlo cuando confirmes que todo funciona.";
  return applyDataChange(["data", target], detail);
}

export async function resetDataFolder(): Promise<boolean> {
  return applyDataChange(
    ["data", "--reset"],
    "Volverás a la carpeta por defecto (~/.smartorch). Tus datos en la carpeta actual no se borran ni se copian.",
  );
}
