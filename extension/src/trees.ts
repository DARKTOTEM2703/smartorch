/**
 * Vista nativa de SmartOrch en la barra lateral (el historial vive en el boton del reloj del panel de chat):
 *   - Proyecto: el analisis del workspace (lenguajes, puntos de entrada, modulos y sus simbolos).
 */
import * as vscode from "vscode";
import { apiFetch } from "./bridge";

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
      const nodes = buildNodes(p);
      try {
        const h: any = await (await apiFetch(`/smartorch/health${q}`)).json();
        nodes.push(...healthNodes(h));
      } catch { /* sin grafo todavia: el resto de la vista sigue funcionando */ }
      return nodes;
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

function healthNodes(h: any): Node[] {
  const s = h.smells ?? {};
  const group = (label: string, icon: string, items: Node[]): Node[] =>
    items.length ? [{ label, icon, description: `${items.length}`, children: items }] : [];
  const issues: Node[] = [
    ...group("Funciones largas o complejas", "symbol-function", (s.long_functions ?? []).map((f: any) => ({
      label: f.qual, description: `${f.lines} líneas · complejidad ${f.complexity}`, icon: "symbol-function", file: f.file, line: f.line }))),
    ...group("Clases con demasiadas responsabilidades", "symbol-class", (s.big_classes ?? []).map((c: any) => ({
      label: c.qual, description: `${c.methods} métodos`, icon: "symbol-class", file: c.file, line: c.line }))),
    ...group("Código duplicado (DRY)", "files", (s.duplicates ?? []).map((g: any[], i: number) => ({
      label: `Grupo ${i + 1}: ${g[0][1]}`, description: `${g.length} copias`, icon: "files",
      children: g.map((d: any[]) => ({ label: d[1], description: d[0], icon: "symbol-function", file: d[0], line: d[2] })) }))),
    ...group("Ciclos de importación", "refresh", (s.cycles ?? []).map((c: string[]) => ({
      label: c.join(" → "), icon: "warning", file: c[0] }))),
  ];
  const m = h.map ?? {};
  const mapNode: Node = m.summarized_files
    ? { label: "Mapa de resúmenes", icon: "book", description: `${m.summarized_files} archivos resumidos`,
        command: { command: "smartorch.buildMap", title: "Actualizar mapa" } }
    : { label: "Construir mapa de resúmenes", icon: "book", description: "el modelo lee cada archivo una vez",
        command: { command: "smartorch.buildMap", title: "Construir mapa" } };
  return [{
    label: "Salud del proyecto", icon: "pulse",
    description: `${h.graph?.symbols ?? 0} símbolos · ${issues.length ? issues.length + " avisos" : "sin avisos"}`,
    children: [...issues, mapNode,
      { label: "Experiencias aprendidas", icon: "lightbulb", description: `${h.experiences ?? 0}`,
        command: { command: "smartorch.forgetExperiences", title: "Gestionar experiencias" } }],
  }];
}

async function buildMap() {
  const workspace = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!workspace) {
    void vscode.window.showWarningMessage("SmartOrch: abre una carpeta para construir su mapa.");
    return;
  }
  const q = `?root=${encodeURIComponent(workspace)}`;
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "SmartOrch: construyendo el mapa del proyecto", cancellable: true },
    async (progress, token) => {
      try {
        const started: any = await (await apiFetch(`/smartorch/map/build${q}`, { method: "POST" })).json();
        if (!started.started) progress.report({ message: started.reason });
        let last = 0;
        while (!token.isCancellationRequested) {
          await new Promise((r) => setTimeout(r, 1500));
          const st: any = await (await apiFetch(`/smartorch/map/status${q}`)).json();
          const p = st.progress ?? {};
          if (p.error) throw new Error(p.error);
          if (p.total) {
            const pct = Math.round((p.done / p.total) * 100);
            progress.report({ message: `${p.phase}: ${p.done}/${p.total}`, increment: Math.max(0, pct - last) });
            last = pct;
          }
          if (!p.running && st.summarized_files) break;
        }
        if (token.isCancellationRequested) {
          await apiFetch(`/smartorch/map/cancel${q}`, { method: "POST" });
          void vscode.window.showInformationMessage("SmartOrch: lo ya resumido queda guardado; vuelve a ejecutarlo para continuar.");
        } else {
          void vscode.window.showInformationMessage("SmartOrch: mapa del proyecto listo.");
        }
        void vscode.commands.executeCommand("smartorch.refreshViews");
      } catch (e: any) {
        void vscode.window.showErrorMessage(`SmartOrch: ${e.message}`);
      }
    },
  );
}

async function manageExperiences() {
  const workspace = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!workspace) return;
  const q = `?root=${encodeURIComponent(workspace)}`;
  const data: any = await (await apiFetch(`/smartorch/experiences${q}`)).json();
  const items = (data.experiences as any[]).map((e) => ({
    label: e.task, description: `${e.kind === "lesson" ? "lección" : "receta"} · usada ${e.uses} veces`,
    detail: `${e.files}${e.lesson ? " — " + e.lesson : ""}`, id: e.id,
  }));
  if (!items.length) {
    void vscode.window.showInformationMessage("SmartOrch: aún no ha aprendido nada de este proyecto (se guarda cuando una tarea termina con los tests en verde).");
    return;
  }
  const picked = await vscode.window.showQuickPick(
    [...items, { label: "$(trash) Olvidar todo lo aprendido de este proyecto", description: "", detail: "", id: -1 }],
    { placeHolder: "Experiencias de SmartOrch en este proyecto (elige una para olvidarla)" });
  if (!picked) return;
  const all = picked.id === -1;
  const ok = await vscode.window.showWarningMessage(
    all ? "¿Olvidar todo lo aprendido de este proyecto?" : `¿Olvidar «${picked.label}»?`, { modal: true }, "Olvidar");
  if (ok !== "Olvidar") return;
  await apiFetch(`/smartorch/experiences${q}${all ? "" : `&id=${picked.id}`}`, { method: "DELETE" });
  void vscode.commands.executeCommand("smartorch.refreshViews");
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
  const project = new ProjectProvider();

  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("smartorch.projectTree", project),
    vscode.commands.registerCommand("smartorch.refreshViews", () => project.refresh()),
    vscode.commands.registerCommand("smartorch.analyzeProject", analyzeProject),
    vscode.commands.registerCommand("smartorch.buildMap", buildMap),
    vscode.commands.registerCommand("smartorch.forgetExperiences", manageExperiences),
  );
}
