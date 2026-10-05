/**
 * InlineCompletionItemProvider — autocompletado en línea con SmartOrch
 */
import * as vscode from 'vscode';
import { SmartOrchClient } from './smartorch-client';

export class SmartOrchCompletionProvider implements vscode.InlineCompletionItemProvider {
  private _pending: NodeJS.Timeout | undefined;
  private _lastText = '';

  constructor(private readonly _client: SmartOrchClient) {}

  async provideInlineCompletionItems(
    document: vscode.TextDocument,
    position: vscode.Position,
    _context: vscode.InlineCompletionContext,
    token: vscode.CancellationToken
  ): Promise<vscode.InlineCompletionList | null> {
    const config = vscode.workspace.getConfiguration('smartorch');
    if (!config.get<boolean>('autocomplete', true)) return null;

    // Extraer contexto: 20 líneas antes del cursor
    const startLine = Math.max(0, position.line - 20);
    const prefix    = document.getText(
      new vscode.Range(new vscode.Position(startLine, 0), position)
    ).trim();

    if (!prefix || prefix === this._lastText) return null;
    if (prefix.length < 10) return null;

    this._lastText = prefix;

    const prompt = `${prefix}`;

    try {
      const suggestion = await this._client.complete(prompt, 80);
      if (token.isCancellationRequested || !suggestion.trim()) return null;

      return {
        items: [
          new vscode.InlineCompletionItem(
            suggestion,
            new vscode.Range(position, position)
          ),
        ],
      };
    } catch {
      return null;
    }
  }
}
