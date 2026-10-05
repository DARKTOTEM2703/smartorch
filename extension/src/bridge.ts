import * as vscode from "vscode";

const cfg = () => vscode.workspace.getConfiguration("smartorch");

export const serverRoot = () =>
  cfg()
    .get<string>("apiUrl", "http://localhost:8080/v1")
    .replace(/\/v\d+\/?$/, "");

export async function apiFetch(path: string, init: RequestInit = {}) {
  const key = cfg().get<string>("apiKey", "smartorch-local-key");
  const res = await fetch(`${serverRoot()}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${key}`,
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    signal: init.signal ?? AbortSignal.timeout(15000),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res;
}

export async function indexWorkspace(root: string): Promise<number> {
  const res = await apiFetch("/smartorch/index", {
    method: "POST",
    body: JSON.stringify({ root }),
    signal: AbortSignal.timeout(120000),
  });
  const data: any = await res.json();
  return data.tfidf_chunks ?? data.chunks ?? 0;
}

interface ConversationSummary {
  id: string;
  title: string;
  source: string;
  message_count: number;
  updated_at: number;
}

async function openConversation(id: string) {
  await vscode.commands.executeCommand("smartorch.openConversation", id);
}

async function openWeb(conv?: string) {
  const url = `${serverRoot()}/${conv ? `?c=${conv}` : ""}`;
  await vscode.env.openExternal(vscode.Uri.parse(url));
}

async function sendToWeb() {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    await openWeb();
    return;
  }
  const sel = editor.selection;
  const code = sel.isEmpty
    ? editor.document.getText().slice(0, 8000)
    : editor.document.getText(sel);
  const lang = editor.document.languageId;
  const file = vscode.workspace.asRelativePath(editor.document.uri);
  const conv: any = await (
    await apiFetch("/smartorch/conversations", {
      method: "POST",
      body: JSON.stringify({
        title: `Código de ${file}`,
        source: "vscode",
        workspace: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath,
        messages: [
          {
            role: "user",
            content: `Archivo \`${file}\`:\n\`\`\`${lang}\n${code}\n\`\`\``,
          },
        ],
      }),
    })
  ).json();
  await openWeb(conv.id);
}

async function pickConversation() {
  const data: any = await (
    await apiFetch("/smartorch/conversations?limit=50")
  ).json();
  const items = (data.conversations as ConversationSummary[]).map((c) => ({
    label: c.title,
    description: `${c.source} · ${c.message_count} mensajes`,
    detail: new Date(c.updated_at * 1000).toLocaleString(),
    id: c.id,
  }));
  if (!items.length) {
    vscode.window.showInformationMessage(
      "Aún no hay conversaciones guardadas.",
    );
    return;
  }
  const picked = await vscode.window.showQuickPick(items, {
    title: "Conversaciones de SmartOrch (web · terminal · VS Code)",
    matchOnDescription: true,
    matchOnDetail: true,
  });
  if (picked) await openConversation(picked.id);
}

const guarded = (fn: () => Promise<void>) => async () => {
  try {
    await fn();
  } catch (e: any) {
    vscode.window.showErrorMessage(
      `SmartOrch: ${e.message}. ¿Está corriendo el servidor?`,
    );
  }
};

export function registerBridge(context: vscode.ExtensionContext) {
  context.subscriptions.push(
    vscode.commands.registerCommand("smartorch.openWeb", () => openWeb()),
    vscode.commands.registerCommand("smartorch.sendToWeb", guarded(sendToWeb)),
    vscode.commands.registerCommand(
      "smartorch.conversations",
      guarded(pickConversation),
    ),
    vscode.window.registerUriHandler({
      handleUri: guardedUri,
    }),
  );
}

async function guardedUri(uri: vscode.Uri) {
  const conv = new URLSearchParams(uri.query).get("conv");
  if (uri.path === "/open" && conv) {
    try {
      await openConversation(conv);
    } catch (e: any) {
      vscode.window.showErrorMessage(
        `SmartOrch: no pude abrir la conversación (${e.message})`,
      );
    }
  }
}
