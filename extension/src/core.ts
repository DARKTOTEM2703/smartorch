import * as vscode from "vscode";
import { indexWorkspace, registerBridge, serverRoot } from "./bridge";
import { OnboardingPanel } from "./onboarding";
import { currentRuntime, startSmartOrchServer } from "./runtime";

async function isAlive(): Promise<boolean> {
  try {
    const r = await fetch(`${serverRoot()}/health`, {
      signal: AbortSignal.timeout(2500),
    });
    return r.ok;
  } catch {
    return false;
  }
}

const startServerInTerminal = () => startSmartOrchServer(undefined, true);

function openTerminalChat() {
  const existing = vscode.window.terminals.find(
    (t) => t.name === "SmartOrch Chat",
  );
  if (existing) {
    existing.show();
    return;
  }
  const term = vscode.window.createTerminal({
    name: "SmartOrch Chat",
    iconPath: new vscode.ThemeIcon("comment-discussion"),
  });
  term.sendText(
    currentRuntime() === "wsl" ? 'wsl -e bash -lc "smartorch"' : "smartorch",
  );
  term.show();
}

export function activateSmartOrch(context: vscode.ExtensionContext) {
  const bar = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100,
  );
  bar.command = "smartorch.status";
  bar.show();
  context.subscriptions.push(bar);

  const refresh = async () => {
    const alive = await isAlive();
    bar.text = alive ? "$(server) Orch" : "$(circle-slash) Orch (offline)";
    bar.tooltip = alive
      ? "SmartOrch activo · click para estado"
      : "SmartOrch no responde — click para iniciar";
    bar.backgroundColor = alive
      ? undefined
      : new vscode.ThemeColor("statusBarItem.warningBackground");
    return alive;
  };

  const autoIndex = async () => {
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder) return;
    for (let i = 0; i < 6; i++) {
      if (await isAlive()) {
        try {
          await indexWorkspace(folder.uri.fsPath);
        } catch {
          /* silencioso */
        }
        return;
      }
      await new Promise((r) => setTimeout(r, 5000));
    }
  };

  context.subscriptions.push(
    vscode.commands.registerCommand("smartorch.onboarding", () =>
      OnboardingPanel.show(context),
    ),
    vscode.commands.registerCommand(
      "smartorch.startServer",
      startServerInTerminal,
    ),
    vscode.commands.registerCommand("smartorch.terminalChat", openTerminalChat),
    vscode.commands.registerCommand("smartorch.status", async () => {
      if (await refresh()) {
        vscode.window.showInformationMessage(
          `SmartOrch activo — ${serverRoot()}`,
        );
      } else {
        const a = await vscode.window.showWarningMessage(
          "SmartOrch no responde. ¿Iniciar en terminal?",
          "Iniciar",
        );
        if (a === "Iniciar") startServerInTerminal();
      }
    }),
    vscode.commands.registerCommand("smartorch.indexWorkspace", async () => {
      const folder = vscode.workspace.workspaceFolders?.[0];
      if (!folder) {
        vscode.window.showErrorMessage("No hay workspace abierto");
        return;
      }
      await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: "SmartOrch: indexando workspace para RAG...",
        },
        async () => {
          try {
            const n = await indexWorkspace(folder.uri.fsPath);
            vscode.window.showInformationMessage(
              `SmartOrch: ${n} chunks indexados — RAG activo`,
            );
          } catch (e: any) {
            vscode.window.showErrorMessage(`SmartOrch: ${e.message}`);
          }
        },
      );
    }),
  );

  registerBridge(context);

  void refresh();
  const timer = setInterval(refresh, 20000);
  context.subscriptions.push({ dispose: () => clearInterval(timer) });
  void autoIndex();

  if (!context.globalState.get<boolean>("smartorch.onboardingShown")) {
    void context.globalState.update("smartorch.onboardingShown", true);
    void OnboardingPanel.show(context);
  }
}
