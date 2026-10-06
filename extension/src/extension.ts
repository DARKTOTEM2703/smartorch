/**
 * SmartOrch — extension de VS Code.
 * Chat (la web de SmartOrch embebida), autocompletado en linea y puente con el editor.
 */
import * as vscode from "vscode";

import { CompletionProvider } from "./completion";
import { activateSmartOrch } from "./core";
import { ChatPanel, registerProposedProvider } from "./panel";
import { startSmartOrchServer } from "./runtime";
import { apiFetch } from "./bridge";
import { registerTrees } from "./trees";

// Comandos de editor: clic derecho / paleta / atajos. Cada uno envia el codigo al chat.
const SLASH: Record<string, string> = {
  explain: "Explícame este código paso a paso. ¿Qué hace, cómo funciona internamente y hay algo no obvio?",
  fix: "Encuentra todos los bugs y corrígelos. Muestra el código corregido completo y explica cada cambio:",
  refactor: "Refactoriza este código aplicando SOLID, DRY y clean architecture. Devuelve el resultado completo:",
  solid: "Aplica los principios SOLID y DRY a este código. Para cada violación: qué viola → por qué → cómo lo arreglas. Devuelve el código refactorizado completo:",
  review: "Haz un code review exhaustivo. Identifica bugs, violaciones de SOLID/DRY, problemas de seguridad y rendimiento:",
  test: "Escribe tests unitarios completos. Cubre happy path, casos borde y casos de error con el framework apropiado:",
  docstring: "Genera documentación completa (docstrings/JSDoc) con descripción, parámetros, retorno, excepciones y ejemplo de uso:",
  optimize: "Analiza la complejidad actual O(?) e identifica cuellos de botella. Devuelve la versión optimizada con la nueva complejidad:",
  yara: "Analiza este código/muestra y genera una regla YARA precisa con strings únicos, condición robusta y metadata completa (author, MITRE ATT&CK, severity):",
  sigma: "Genera una regla Sigma para detectar este comportamiento/IOC con logsource correcto, filtros de falsos positivos y nivel justificado:",
  c2: "Analiza este código/tráfico C2 (contexto educativo/defensivo). Mapea a MITRE ATT&CK, extrae IoCs y documenta C2, persistencia y evasión:",
  pentest: "Analiza este objetivo desde perspectiva de pentest (entorno controlado/educativo). Superficie de ataque, vectores, metodología y mitigaciones:",
  web: "Implementa el siguiente requerimiento web (React/Vue/FastAPI/Django) con arquitectura limpia, SOLID/DRY y separación de capas:",
  mobile: "Implementa el siguiente requerimiento para app móvil (Flutter/React Native) aplicando SOLID, manejo de estado correcto y buenas prácticas:",
};

function selectionOrFile(): { code: string; language: string; file: string } | undefined {
  const editor = vscode.window.activeTextEditor;
  if (!editor) return undefined;
  const sel = editor.selection;
  return {
    code: sel.isEmpty ? editor.document.getText().slice(0, 8000) : editor.document.getText(sel),
    language: editor.document.languageId,
    file: vscode.workspace.asRelativePath(editor.document.uri),
  };
}

/** Historial como selector nativo (icono del reloj en el titulo del panel). Cada fila permite renombrar y borrar. */
async function pickConversation(panel: ChatPanel) {
  type Item = vscode.QuickPickItem & { id: string; buttons: vscode.QuickInputButton[] };
  const rename: vscode.QuickInputButton = { iconPath: new vscode.ThemeIcon("edit"), tooltip: "Renombrar" };
  const remove: vscode.QuickInputButton = { iconPath: new vscode.ThemeIcon("trash"), tooltip: "Eliminar" };
  const load = async (): Promise<Item[]> => {
    const data: any = await (await apiFetch("/smartorch/conversations?limit=100")).json();
    return (data.conversations as any[]).map((c) => ({
      label: c.title,
      description: `${c.source} · ${c.message_count} mensajes`,
      detail: new Date(c.updated_at * 1000).toLocaleString(),
      id: c.id as string,
      buttons: [rename, remove],
    }));
  };
  let items: Item[];
  try {
    items = await load();
  } catch {
    void vscode.window.showWarningMessage("SmartOrch: no pude leer el historial. ¿Está corriendo el servidor?");
    return;
  }
  if (!items.length) {
    void vscode.window.showInformationMessage("SmartOrch: aún no hay conversaciones.");
    return;
  }
  const qp = vscode.window.createQuickPick<Item>();
  qp.placeholder = "Conversaciones de SmartOrch (compartidas con la terminal y la web)";
  qp.matchOnDetail = true;
  qp.items = items;
  qp.onDidAccept(() => {
    const picked = qp.selectedItems[0];
    qp.hide();
    if (picked) void panel.openConversation(picked.id);
  });
  qp.onDidTriggerItemButton(async (e) => {
    const item = e.item;
    try {
      if (e.button === rename) {
        const title = await vscode.window.showInputBox({ prompt: "Nuevo título", value: item.label });
        if (!title?.trim()) return;
        await apiFetch(`/smartorch/conversations/${item.id}`, { method: "PATCH", body: JSON.stringify({ title }) });
      } else {
        const ok = await vscode.window.showWarningMessage(`¿Eliminar «${item.label}»?`, { modal: true }, "Eliminar");
        if (ok !== "Eliminar") return;
        await apiFetch(`/smartorch/conversations/${item.id}`, { method: "DELETE" });
      }
      qp.items = await load();
    } catch {
      void vscode.window.showWarningMessage("SmartOrch: no pude completar la acción.");
    }
  });
  qp.onDidHide(() => qp.dispose());
  qp.show();
}

/** VS Code no expone un comando para mover una vista por nombre: se abre su selector y se explica el paso. */
async function moveChatToSecondarySideBar() {
  void vscode.window.showInformationMessage(
    "En el selector que se abre elige «SmartOrch: Chat» y luego «Secondary Side Bar» (barra lateral derecha). También puedes arrastrar el icono de SmartOrch hacia la derecha.");
  await vscode.commands.executeCommand("workbench.action.moveView");
}

export function activate(context: vscode.ExtensionContext) {
  const panel = new ChatPanel(context);
  registerProposedProvider(context);
  registerTrees(context);

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(ChatPanel.viewId, panel, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.languages.registerInlineCompletionItemProvider({ pattern: "**" }, new CompletionProvider()),
    vscode.commands.registerCommand("smartorch.openChat", () =>
      vscode.commands.executeCommand("workbench.view.extension.smartorch"),
    ),
    vscode.commands.registerCommand("smartorch.openChatTab", () => panel.openTab()),
    vscode.commands.registerCommand("smartorch.openConversation", (id: string) => panel.openConversation(id)),
    vscode.commands.registerCommand("smartorch.newChat", () => panel.newChat()),
    vscode.commands.registerCommand("smartorch.chatHistory", () => pickConversation(panel)),
    vscode.commands.registerCommand("smartorch.moveChat", moveChatToSecondarySideBar),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("smartorch.apiUrl")) panel.refresh();
    }),
  );

  for (const [key, prompt] of Object.entries(SLASH)) {
    context.subscriptions.push(
      vscode.commands.registerCommand(`smartorch.${key}`, async () => {
        const ctx = selectionOrFile();
        if (!ctx) {
          void vscode.window.showWarningMessage("SmartOrch: selecciona código o abre un archivo.");
          return;
        }
        await panel.sendPrompt(`${prompt}\n\nArchivo \`${ctx.file}\`:\n\`\`\`${ctx.language}\n${ctx.code}\n\`\`\``);
      }),
    );
  }

  activateSmartOrch(context);
  void startSmartOrchServer(context);
}

export function deactivate() {}
