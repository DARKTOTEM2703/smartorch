/**
 * Autocompletado en linea: pide a SmartOrch (/v1/completions) el relleno entre el
 * codigo previo y el posterior al cursor, con pausa al teclear y cancelacion.
 */
import * as vscode from "vscode";
import { apiFetch } from "./bridge";

const MAX_PREFIX_LINES = 60;
const MAX_SUFFIX_LINES = 20;

export class CompletionProvider implements vscode.InlineCompletionItemProvider {
  private lastKey = "";

  async provideInlineCompletionItems(
    document: vscode.TextDocument,
    position: vscode.Position,
    _context: vscode.InlineCompletionContext,
    token: vscode.CancellationToken,
  ): Promise<vscode.InlineCompletionList | undefined> {
    const cfg = vscode.workspace.getConfiguration("smartorch");
    if (!cfg.get<boolean>("autocomplete", true)) return undefined;
    if (document.uri.scheme !== "file" && document.uri.scheme !== "untitled") return undefined;

    const prefix = document.getText(
      new vscode.Range(new vscode.Position(Math.max(0, position.line - MAX_PREFIX_LINES), 0), position),
    );
    const suffix = document.getText(
      new vscode.Range(
        position,
        new vscode.Position(Math.min(document.lineCount - 1, position.line + MAX_SUFFIX_LINES), 10_000),
      ),
    );
    if (prefix.trim().length < 4) return undefined;

    // Pausa: si el usuario sigue escribiendo, VS Code cancela y no se llega a pedir nada
    const delay = cfg.get<number>("autocompleteDelay", 400);
    await new Promise((r) => setTimeout(r, delay));
    if (token.isCancellationRequested) return undefined;

    const key = `${document.uri}:${position.line}:${position.character}:${prefix.length}`;
    if (key === this.lastKey) return undefined;
    this.lastKey = key;

    const controller = new AbortController();
    token.onCancellationRequested(() => controller.abort());
    try {
      const res = await apiFetch("/v1/completions", {
        method: "POST",
        signal: controller.signal,
        body: JSON.stringify({ prompt: prefix, suffix, max_tokens: 96 }),
      });
      const data: any = await res.json();
      const text: string = data?.choices?.[0]?.text ?? "";
      if (token.isCancellationRequested || !text.trim()) return undefined;
      return new vscode.InlineCompletionList([
        new vscode.InlineCompletionItem(text, new vscode.Range(position, position)),
      ]);
    } catch {
      return undefined;
    }
  }
}
