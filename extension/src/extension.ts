/**
 * SmartOrch VS Code Extension — punto de entrada
 * Identidad propia sobre la base de Continue. Como Cursor sobre VS Code.
 */
import * as vscode from 'vscode';
import { SmartOrchClient } from './smartorch-client';
import { SmartOrchSidebarProvider } from './sidebar-provider';
import { SmartOrchCompletionProvider } from './completion-provider';
import { OnboardingPanel } from './onboarding-provider';

let statusBar: vscode.StatusBarItem;
let client: SmartOrchClient;
let sidebar: SmartOrchSidebarProvider;
let healthInterval: NodeJS.Timeout;

// ── Tabla de slash commands ───────────────────────────────────────────────────

const SLASH_COMMANDS: Record<string, { prompt: string; hint: string }> = {
  explain: {
    hint:   'Explicando código...',
    prompt: 'Explícame este código paso a paso. ¿Qué hace, cómo funciona internamente y hay algo no obvio?',
  },
  fix: {
    hint:   'Buscando bugs...',
    prompt: 'Encuentra todos los bugs y corrígelos. Muestra el código corregido completo y explica cada cambio:',
  },
  refactor: {
    hint:   'Refactorizando...',
    prompt: 'Refactoriza este código aplicando SOLID, DRY y clean architecture. Devuelve el resultado completo:',
  },
  solid: {
    hint:   'Aplicando SOLID/DRY...',
    prompt: 'Aplica los principios SOLID y DRY a este código. Para cada violación: qué viola → por qué → cómo lo arreglas. Devuelve el código refactorizado completo:',
  },
  review: {
    hint:   'Haciendo code review...',
    prompt: 'Haz un code review exhaustivo. Identifica bugs, violaciones de SOLID/DRY, problemas de seguridad y rendimiento. Usa números de línea cuando sea posible:',
  },
  test: {
    hint:   'Generando tests...',
    prompt: 'Escribe tests unitarios completos. Cubre: happy path, casos borde y casos de error. Usa el framework de testing apropiado:',
  },
  docstring: {
    hint:   'Generando documentación...',
    prompt: 'Genera documentación completa (docstrings/JSDoc). Incluye: descripción, parámetros, retorno, excepciones y ejemplo de uso:',
  },
  optimize: {
    hint:   'Optimizando rendimiento...',
    prompt: 'Analiza la complejidad actual O(?) e identifica cuellos de botella. Devuelve la versión optimizada con la nueva complejidad:',
  },
  yara: {
    hint:   'Generando regla YARA...',
    prompt: 'Analiza este código/muestra y genera una regla YARA precisa con strings únicos, condición robusta y metadata completa (author, MITRE ATT&CK, severity):',
  },
  sigma: {
    hint:   'Generando regla Sigma...',
    prompt: 'Genera una regla Sigma para detectar este comportamiento/IOC. Incluye logsource correcto, detection precisa con filtros de FP y nivel justificado:',
  },
  c2: {
    hint:   'Analizando C2/malware...',
    prompt: 'Analiza este código/tráfico C2 (contexto educativo/defensivo). Mapea a MITRE ATT&CK, extrae IoCs, documenta mecanismos de C2, persistencia y evasión:',
  },
  pentest: {
    hint:   'Analizando superficie de ataque...',
    prompt: 'Analiza este objetivo desde perspectiva de pentest (entorno controlado/educativo). Superficie de ataque, vectores, metodología de prueba y mitigaciones:',
  },
  web: {
    hint:   'Desarrollando feature web...',
    prompt: 'Implementa el siguiente requerimiento web (React/Vue/FastAPI/Django) aplicando arquitectura limpia, SOLID/DRY y separación de capas correcta:',
  },
  mobile: {
    hint:   'Desarrollando feature mobile...',
    prompt: 'Implementa el siguiente requerimiento para aplicación móvil (Flutter/React Native). Aplica SOLID, manejo de estado correcto y mejores prácticas del stack:',
  },
};

export function activate(context: vscode.ExtensionContext) {
  client  = _makeClient();
  sidebar = new SmartOrchSidebarProvider(context, client);

  // ── Sidebar (activity bar) ───────────────────────────────────────────────
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(
      SmartOrchSidebarProvider.viewId,
      sidebar,
      { webviewOptions: { retainContextWhenHidden: true } }
    )
  );

  // ── Autocompletado inline ────────────────────────────────────────────────
  if (vscode.workspace.getConfiguration('smartorch').get('autocomplete', true)) {
    context.subscriptions.push(
      vscode.languages.registerInlineCompletionItemProvider(
        { pattern: '**' },
        new SmartOrchCompletionProvider(client)
      )
    );
  }

  // ── VS Code Chat Participant (@smartorch) ────────────────────────────────
  _registerChatParticipant(context);

  // ── Status bar ───────────────────────────────────────────────────────────
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = 'smartorch.status';
  setStatus('checking');
  statusBar.show();
  context.subscriptions.push(statusBar);

  // ── Comandos principales ─────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand('smartorch.openChat', () => {
      vscode.commands.executeCommand('workbench.view.extension.smartorch');
    }),

    vscode.commands.registerCommand('smartorch.onboarding', () => OnboardingPanel.show(context)),

    vscode.commands.registerCommand('smartorch.startServer', async () => {
      const already = await client.isAlive();
      if (already) {
        vscode.window.showInformationMessage('SmartOrch ya está corriendo.');
        return;
      }
      vscode.window.showInformationMessage('Iniciando SmartOrch... (abre una terminal y corre: smartorch serve)');
    }),

    vscode.commands.registerCommand('smartorch.status', async () => {
      const alive = await client.isAlive();
      const cfg   = vscode.workspace.getConfiguration('smartorch');
      const url   = cfg.get<string>('apiUrl', 'http://localhost:8080/v1') as string;
      if (alive) {
        vscode.window.showInformationMessage(`⚡ SmartOrch activo — ${url}`);
      } else {
        const action = await vscode.window.showWarningMessage(
          'SmartOrch no responde. ¿Iniciar en terminal?',
          'Abrir terminal'
        );
        if (action === 'Abrir terminal') {
          const term = vscode.window.createTerminal('SmartOrch Server');
          term.sendText('smartorch serve');
          term.show();
        }
      }
    }),

    vscode.commands.registerCommand('smartorch.indexWorkspace', async () => {
      const folders = vscode.workspace.workspaceFolders;
      if (!folders?.length) {
        vscode.window.showErrorMessage('No hay workspace abierto');
        return;
      }
      vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'SmartOrch: indexando workspace para RAG...' },
        async () => {
          try {
            const chunks = await client.indexWorkspace(folders[0].uri.fsPath);
            vscode.window.showInformationMessage(`SmartOrch: ${chunks} chunks indexados — RAG activo`);
          } catch (e: any) {
            vscode.window.showErrorMessage(`SmartOrch: ${e.message}`);
          }
        }
      );
    }),
  );

  // ── Slash commands — registrar todos desde la tabla ──────────────────────
  for (const [cmdKey, { prompt, hint }] of Object.entries(SLASH_COMMANDS)) {
    const cmdId = `smartorch.${cmdKey}`;
    context.subscriptions.push(
      vscode.commands.registerCommand(cmdId, async () => {
        const code = getSelectionOrFile();
        if (!code) {
          vscode.window.showWarningMessage('SmartOrch: selecciona código o abre un archivo');
          return;
        }
        vscode.window.setStatusBarMessage(`⚡ SmartOrch: ${hint}`, 3000);
        await sidebar.sendSlash(prompt, code, cmdKey);
      })
    );
  }

  // ── Config change → reload client ───────────────────────────────────────
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration(e => {
      if (e.affectsConfiguration('smartorch')) {
        client = _makeClient();
        sidebar.updateClient(client);
      }
    })
  );

  // ── Health check periódico ────────────────────────────────────────────────
  checkHealth();
  healthInterval = setInterval(checkHealth, 20_000);

  // ── Auto-indexar workspace en background ─────────────────────────────────
  autoIndexWorkspace();

  // ── Onboarding en la primera instalación ─────────────────────────────────
  if (!context.globalState.get<boolean>('smartorch.onboardingShown')) {
    context.globalState.update('smartorch.onboardingShown', true);
    OnboardingPanel.show(context);
  }
}

export function deactivate() {
  clearInterval(healthInterval);
}

// ── Chat Participant @smartorch ───────────────────────────────────────────────

function _registerChatParticipant(context: vscode.ExtensionContext) {
  try {
    const chatApi = (vscode as any).chat;
    if (!chatApi?.createChatParticipant) {
      return; // VS Code < 1.90 o sin Chat extension
    }

    const participant = chatApi.createChatParticipant(
      'smartorch.assistant',
      async (request: any, _ctx: any, stream: any, _token: any) => {
        const cmd     = request.command ?? '';
        const userMsg = request.prompt ?? '';

        // Contexto del editor activo
        let editorCtx = '';
        const editor  = vscode.window.activeTextEditor;
        if (editor) {
          const sel = editor.selection;
          const txt = sel.isEmpty
            ? editor.document.getText().slice(0, 4000)
            : editor.document.getText(sel);
          if (txt.trim()) {
            editorCtx = `\n\n\`\`\`${editor.document.languageId}\n${txt}\n\`\`\``;
          }
        }

        const slashDef = SLASH_COMMANDS[cmd];
        let finalPrompt: string;
        if (slashDef) {
          finalPrompt = slashDef.prompt + (editorCtx || `\n\n${userMsg}`);
        } else {
          finalPrompt = userMsg + editorCtx;
        }

        stream.markdown('*SmartOrch procesando...*\n\n');

        try {
          const messages = [{ role: 'user' as const, content: finalPrompt }];
          const resp     = await client.chat(messages, 3072);
          stream.markdown(resp.content);
        } catch (e: any) {
          stream.markdown(`**Error:** ${e.message}\n\n¿Está corriendo SmartOrch? → \`smartorch serve\``);
        }
      }
    );

    participant.iconPath = vscode.Uri.joinPath(context.extensionUri, 'media', 'icons', 'smartorch-activity.svg');
    context.subscriptions.push(participant);
  } catch {
    // Chat API no disponible — no es crítico
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _makeClient(): SmartOrchClient {
  const cfg = vscode.workspace.getConfiguration('smartorch');
  return new SmartOrchClient(
    cfg.get<string>('apiUrl',  'http://localhost:8080/v1'),
    cfg.get<string>('apiKey',  'smartorch-local-key'),
    cfg.get<string>('model',   'hermes3:8b')
  );
}

function getSelectionOrFile(): string | undefined {
  const editor = vscode.window.activeTextEditor;
  if (!editor) return undefined;
  const sel = editor.selection;
  return sel.isEmpty
    ? editor.document.getText().slice(0, 8000)
    : editor.document.getText(sel);
}

async function checkHealth() {
  const alive = await client.isAlive();
  setStatus(alive ? 'online' : 'offline');
}

function setStatus(state: 'online' | 'offline' | 'checking') {
  const map = {
    online:   { icon: '$(sparkle)',      text: 'SmartOrch',          bg: undefined },
    offline:  { icon: '$(circle-slash)', text: 'SmartOrch (offline)', bg: 'statusBarItem.warningBackground' },
    checking: { icon: '$(loading~spin)', text: 'SmartOrch…',          bg: undefined },
  };
  const s = map[state];
  statusBar.text    = `${s.icon} ${s.text}`;
  statusBar.tooltip = state === 'offline'
    ? 'SmartOrch no responde — ejecuta: smartorch serve'
    : 'SmartOrch AI local activo · Click para estado';
  statusBar.backgroundColor = s.bg
    ? new vscode.ThemeColor(s.bg)
    : undefined;
}

async function autoIndexWorkspace() {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders?.length) return;
  // Esperar a que el servidor esté listo (hasta 30s)
  for (let i = 0; i < 6; i++) {
    if (await client.isAlive()) {
      try { await client.indexWorkspace(folders[0].uri.fsPath); } catch { /* silencioso */ }
      return;
    }
    await new Promise(r => setTimeout(r, 5_000));
  }
}
