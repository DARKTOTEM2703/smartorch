/**
 * Vistas nativas de SmartOrch en la barra lateral:
 *   - Historial: las conversaciones compartidas entre la web, la terminal y VS Code.
 *   - Proyecto: el analisis del workspace (lenguajes, puntos de entrada, modulos y sus simbolos).
 */
import * as vscode from "vscode";
import { apiFetch } from "./bridge";

// ── Historial ───────────────────────────────────────────────────────────────

interface ConversationInfo {
  id: string;
  title: string;
  source: string;
  message_count: number;
  updated_at: number;
}

class ConversationItem extends vscode.TreeItem {
  constructor(readonly info: ConversationInfo) {
    super(info.title, vscode.TreeItemCollapsibleState.None);
    this.description = `${info.source} · ${info.message_count}`;
    this.tooltip = `${info.title}\nIniciada en ${info.source} · ${info.message_count} mensajes\n${new Date(info.updated_at * 1000).toLocaleString()}`;
    this.contextValue = "conversation";
    this.iconPath = new vscode.ThemeIcon(
      info.source === "cli" ? "terminal" : info.source === "web" ? "globe" : "comment-discussion",
    );
    this.command = { command: "smartorch.openConversation", title: "Abrir", arguments: [info.id] };
  }
}

export class ConversationsProvider implements vscode.TreeDataProvider<ConversationItem> {
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.changed.event;

  refresh() {
    this.changed.fire();
  }

  getTreeItem(item: ConversationItem) {
    return item;
  }

  async getChildren(): Promise<ConversationItem[]> {
    try {
      const data: any = await (await apiFetch("/smartorch/conversations?limit=60")).json();
      return (data.conversations as ConversationInfo[]).map((c) => new ConversationItem(c));
    } catch {
      return []; // servidor apagado: la vista muestra su mensaje de bienvenida
    }
  }
}

// ── Proyecto ────────────────────────────────────────────────────────────────

type Node = { label: string; description?: string; icon?: string; children?: Node[]; file?: string; line?: number; command?: vscode.Command };

class ProjectItem extends vscode.TreeItem {
  constructor(readonly node: Node, workspace: vscode.Uri | undefined) {
    super(node.label, node.children?.length ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None);
    this.description = node.description;
    if (node.icon) this.iconPath = new vscode.ThemeIcon(node.icon);
    if (node.file && workspace) {
      const uri = vscode.Uri.joinPath(workspace, node.file);
      this.command = {
        command: "vscode.open",
        title: "Abrir",
        arguments: [uri, node.line ? { selection: new vscode.Range(node.line - 1, 0, node.line - 1, 0) } : {}],
      };
    } else if (node.command) {
      this.command = node.command;
    }
  }
}

export class ProjectProvider implements vscode.TreeDataProvider<ProjectItem> {
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.changed.event;
  private roots: Node[] = [];

  refresh() {
    this.changed.fire();
  }

  getTreeItem(item: ProjectItem) {
    return item;
  }

  async getChildren(item?: ProjectItem): Promise<ProjectItem[]> {
    const workspace = vscode.workspace.workspaceFolders?.[0]?.uri;
    if (item) return (item.node.children ?? []).map((n) => new ProjectItem(n, workspace));
    this.roots = await this.load(workspace);
    return this.roots.map((n) => new ProjectItem(n, workspace));
  }

  private async load(workspace: vscode.Uri | undefined): Promise<Node[]> {
    if (!workspace) return [];
    try {
      const q = `?root=${encodeURIComponent(workspace.fsPath)}`;
      const p: any = await (await apiFetch(`/smartorch/analysis${q}`)).json();
      return buildNodes(p);
    } catch (e: any) {
      const msg = String(e?.message ?? e);
      if (msg.startsWith("403") || msg.startsWith("400")) {
        return [{ label: "Indexar este workspace", icon: "sync", description: "para analizarlo",
                  command: { command: "smartorch.indexWorkspace", title: "Indexar" } }];
      }
      return []; // servidor apagado
    }
  }
}

function buildNodes(p: any): Node[] {
  const nodes: Node[] = [
    { label: `${p.files} archivos · ${p.loc} líneas`, icon: "graph", description: p.name },
    {
      label: "Lenguajes", icon: "symbol-misc",
      children: p.languages.map((l: any) => ({ label: l.name, description: `${l.files} archivos · ${l.loc} líneas` })),
    },
  ];
  if (p.entry_points?.length) {
    nodes.push({
      label: "Puntos de entrada", icon: "play",
      children: p.entry_points.map((f: string) => ({ label: f, icon: "file", file: f })),
    });
  }
  nodes.push({
    label: "Carpetas", icon: "folder",
    children: p.dirs.filter((d: any) => d.path.split("/").length <= 2)
      .map((d: any) => ({ label: d.path + "/", description: `${d.files} archivos · ${d.loc} líneas`, icon: "folder" })),
  });
  nodes.push({
    label: "Módulos", icon: "symbol-module", description: "los más grandes",
    children: p.modules.slice(0, 40).map((m: any) => ({
      label: m.path, description: `${m.loc} líneas`, icon: "file-code", file: m.path,
      children: m.symbols.slice(0, 25).map((s: any) => ({
        label: s.name, description: s.kind, icon: s.kind === "class" ? "symbol-class" : "symbol-function",
        file: m.path, line: s.line,
      })),
    })),
  });
  if (p.dependencies && Object.keys(p.dependencies).length) {
    nodes.push({
      label: "Dependencias", icon: "package",
      children: Object.entries(p.dependencies).map(([src, names]: [string, any]) => ({
        label: src, children: names.map((n: string) => ({ label: n })),
      })),
    });
  }
  if (p.todos?.length) {
    nodes.push({
      label: "Pendientes", icon: "checklist", description: `${p.todos.length}`,
      children: p.todos.slice(0, 40).map((t: any) => ({
        label: `${t.tag}: ${t.text || "(sin texto)"}`, description: `${t.path}:${t.line}`, icon: "warning", file: t.path, line: t.line,
      })),
    });
  }
  return nodes;
}

// ── Registro ────────────────────────────────────────────────────────────────

async function analyzeProject() {
  const workspace = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!workspace) {
    void vscode.window.showWarningMessage("SmartOrch: abre una carpeta para analizar el proyecto.");
    return;
  }
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "SmartOrch: analizando el proyecto…" },
    async () => {
      try {
        const q = `?root=${encodeURIComponent(workspace)}&refresh=true`;
        const md = await (await apiFetch(`/smartorch/analysis/report${q}`)).text();
        const doc = await vscode.workspace.openTextDocument({ language: "markdown", content: md });
        await vscode.window.showTextDocument(doc, { preview: false });
        await vscode.commands.executeCommand("markdown.showPreviewToSide");
      } catch (e: any) {
        void vscode.window.showErrorMessage(`SmartOrch: ${e.message}. ¿Está indexado el workspace y corriendo el servidor?`);
      }
    },
  );
}

export function registerTrees(context: vscode.ExtensionContext) {
  const conversations = new ConversationsProvider();
  const project = new ProjectProvider();

  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("smartorch.conversationsView", conversations),
    vscode.window.registerTreeDataProvider("smartorch.projectView", project),
    vscode.commands.registerCommand("smartorch.refreshViews", () => {
      conversations.refresh();
      project.refresh();
    }),
    vscode.commands.registerCommand("smartorch.analyzeProject", analyzeProject),
    vscode.commands.registerCommand("smartorch.renameConversation", async (item: ConversationItem) => {
      const title = await vscode.window.showInputBox({ prompt: "Nuevo título", value: item.info.title });
      if (!title?.trim()) return;
      await apiFetch(`/smartorch/conversations/${item.info.id}`, { method: "PATCH", body: JSON.stringify({ title }) });
      conversations.refresh();
    }),
    vscode.commands.registerCommand("smartorch.deleteConversation", async (item: ConversationItem) => {
      const ok = await vscode.window.showWarningMessage(`¿Eliminar «${item.info.title}»?`, { modal: true }, "Eliminar");
      if (ok !== "Eliminar") return;
      await apiFetch(`/smartorch/conversations/${item.info.id}`, { method: "DELETE" });
      conversations.refresh();
    }),
  );

  // el historial se actualiza solo: lo que hables en la web o la terminal aparece aqui
  const timer = setInterval(() => conversations.refresh(), 15000);
  context.subscriptions.push({ dispose: () => clearInterval(timer) });
}
