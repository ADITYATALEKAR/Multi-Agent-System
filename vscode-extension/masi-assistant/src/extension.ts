import { execFile } from "child_process";
import * as fs from "fs";
import * as http from "http";
import * as https from "https";
import * as path from "path";
import * as vscode from "vscode";

type JsonObject = Record<string, unknown>;

interface TaskViolation {
  violation_id: string;
  rule: string;
  severity: string;
  file_path: string;
  message: string;
}

interface TaskRepair {
  repair_id: string;
  status: string;
  description: string;
  rule: string;
}

interface TaskHypothesis {
  title: string;
  summary: string;
}

interface TaskItem {
  task_id: string;
  status: string;
  repo_path: string;
  created_at: string;
  result: JsonObject;
  work_items: Array<{ item_id: string; description: string; status: string }>;
  violations: TaskViolation[];
  repairs: TaskRepair[];
  hypotheses: TaskHypothesis[];
}

interface RuntimeStatus {
  repoRoot: string;
  pythonPath: string;
  repoRootExists: boolean;
  pythonExists: boolean;
  autoDetectedRepoRoot: boolean;
}

interface PanelChatMessage {
  role: "assistant" | "user";
  text: string;
  taskId?: string;
  summary?: string;
  actionsTaken?: string[];
  filesInFocus?: string[];
  filesChanged?: string[];
  codeChanges?: string[];
  symbolsInFocus?: string[];
  validationResults?: string[];
  suggestions?: string[];
  nextStep?: string;
  cards?: ChatCard[];
  followUpActions?: FollowUpAction[];
}

interface BackendChatResponse {
  answer: string;
  intent: "answer" | "action";
  recommended_action?: string;
  source_task_id?: string;
  summary?: string;
  actions_taken?: string[];
  files_in_focus?: string[];
  files_changed?: string[];
  code_changes?: string[];
  symbols_in_focus?: string[];
  suggestions?: string[];
  next_step?: string;
  highlights?: string[];
  cards?: ChatCard[];
  follow_up_actions?: FollowUpAction[];
}

interface ChatCard {
  title: string;
  body: string;
  action?: string | null;
  action_label?: string | null;
}

interface FollowUpAction {
  action: string;
  label: string;
}

interface AppliedEditResult {
  text: string;
  summary?: string;
  actionsTaken?: string[];
  filesInFocus?: string[];
  filesChanged?: string[];
  codeChanges?: string[];
  symbolsInFocus?: string[];
  validationResults?: string[];
  suggestions?: string[];
  nextStep?: string;
}

interface StoredAiProviderConfig {
  providerId: string;
  label: string;
  model: string;
  baseUrl?: string;
  apiKeyConfigured: boolean;
}

interface AiProviderChoice {
  id: string;
  label: string;
  defaultModel: string;
  placeholder: string;
  defaultBaseUrl?: string;
}

const AI_PROVIDER_CHOICES: AiProviderChoice[] = [
  { id: "openai", label: "ChatGPT / OpenAI", defaultModel: "gpt-4.1", placeholder: "sk-...", defaultBaseUrl: "https://api.openai.com/v1" },
  { id: "anthropic", label: "Claude / Anthropic", defaultModel: "claude-sonnet-4-20250514", placeholder: "sk-ant-..." },
  { id: "deepseek", label: "DeepSeek", defaultModel: "deepseek-chat", placeholder: "sk-...", defaultBaseUrl: "https://api.deepseek.com/v1" },
  { id: "kimi", label: "Kimi / Moonshot", defaultModel: "moonshot-v1-8k", placeholder: "sk-...", defaultBaseUrl: "https://api.moonshot.cn/v1" },
  { id: "openrouter", label: "OpenRouter", defaultModel: "openai/gpt-4.1", placeholder: "sk-or-...", defaultBaseUrl: "https://openrouter.ai/api/v1" },
  { id: "compatible", label: "Other OpenAI-compatible", defaultModel: "custom-model", placeholder: "your-api-key", defaultBaseUrl: "https://api.example.com/v1" },
];

function getConfig() {
  const config = vscode.workspace.getConfiguration("masi");
  const configuredRepoRoot = config.get<string>("repoRoot", "");
  const detectedRepoRoot = (!configuredRepoRoot || !fs.existsSync(configuredRepoRoot))
    ? detectWorkspaceRepoRoot()
    : undefined;
  const repoRoot = configuredRepoRoot && fs.existsSync(configuredRepoRoot)
    ? configuredRepoRoot
    : detectedRepoRoot ?? configuredRepoRoot;
  const configuredPythonPath = config.get<string>("pythonPath", "");
  const pythonPath = configuredPythonPath && fs.existsSync(configuredPythonPath)
    ? configuredPythonPath
    : repoRoot ? getDefaultPythonPath(repoRoot) : configuredPythonPath;
  return {
    apiBaseUrl: config.get<string>("apiBaseUrl", "http://127.0.0.1:8000"),
    repoRoot,
    pythonPath,
    autoDetectedRepoRoot: Boolean(detectedRepoRoot && detectedRepoRoot === repoRoot),
  };
}

function getRuntimeStatus(): RuntimeStatus {
  const { repoRoot, pythonPath, autoDetectedRepoRoot } = getConfig();
  return {
    repoRoot,
    pythonPath,
    repoRootExists: fs.existsSync(repoRoot),
    pythonExists: fs.existsSync(pythonPath),
    autoDetectedRepoRoot,
  };
}

function getDefaultPythonPath(repoRoot: string): string {
  if (process.platform === "win32") {
    return path.join(repoRoot, ".venv312", "Scripts", "python.exe");
  }
  return path.join(repoRoot, ".venv312", "bin", "python");
}

function quoteForPowerShell(value: string): string {
  return `"${value.replace(/"/g, '`"')}"`;
}

function isMasRepoRoot(candidate: string): boolean {
  return fs.existsSync(path.join(candidate, "pyproject.toml"))
    && fs.existsSync(path.join(candidate, "src", "api", "app.py"));
}

function detectWorkspaceRepoRoot(): string | undefined {
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    if (isMasRepoRoot(folder.uri.fsPath)) {
      return folder.uri.fsPath;
    }
  }
  return undefined;
}

async function persistRepoConfiguration(repoRoot: string): Promise<void> {
  const config = vscode.workspace.getConfiguration("masi");
  const nextPythonPath = getDefaultPythonPath(repoRoot);
  await config.update("repoRoot", repoRoot, vscode.ConfigurationTarget.Global);
  await config.update("pythonPath", nextPythonPath, vscode.ConfigurationTarget.Global);
}

async function ensureRepoRootConfigured(): Promise<string | undefined> {
  const { repoRoot, autoDetectedRepoRoot } = getConfig();
  if (repoRoot && fs.existsSync(repoRoot)) {
    if (autoDetectedRepoRoot) {
      await persistRepoConfiguration(repoRoot);
    }
    return repoRoot;
  }

  const detectedRepoRoot = detectWorkspaceRepoRoot();
  if (detectedRepoRoot) {
    await persistRepoConfiguration(detectedRepoRoot);
    return detectedRepoRoot;
  }

  const selectedFolder = await vscode.window.showOpenDialog({
    canSelectFiles: false,
    canSelectFolders: true,
    canSelectMany: false,
    defaultUri: vscode.workspace.workspaceFolders?.[0]?.uri,
    openLabel: "Select MAS Repository",
    title: "Select the MAS repository root",
  });
  const selectedRepoRoot = selectedFolder?.[0]?.fsPath;
  if (!selectedRepoRoot) {
    void vscode.window.showErrorMessage("MAS needs a repository folder before it can install or start.");
    return undefined;
  }

  await persistRepoConfiguration(selectedRepoRoot);
  return selectedRepoRoot;
}

function buildInstallCommands(repoRoot: string, pythonPath: string): string[] {
  if (process.platform !== "win32") {
    const escapedRepoRoot = repoRoot.replace(/'/g, `'\\''`);
    const escapedPythonPath = pythonPath.replace(/'/g, `'\\''`);
    return [
      `export REPO_ROOT='${escapedRepoRoot}'`,
      `export PYTHON_PATH='${escapedPythonPath}'`,
      "if [ ! -x \"$PYTHON_PATH\" ]; then python3.12 -m venv \"$REPO_ROOT/.venv312\" || python3 -m venv \"$REPO_ROOT/.venv312\"; fi",
      "\"$PYTHON_PATH\" -m pip install --upgrade pip",
      "cd \"$REPO_ROOT\"",
      "\"$PYTHON_PATH\" -m pip install -e \".[dev]\"",
      "echo \"MAS runtime install complete. Run 'MAS: Start API' next.\"",
    ];
  }

  const quotedRepoRoot = quoteForPowerShell(repoRoot);
  const quotedPythonPath = quoteForPowerShell(pythonPath);
  return [
    `$repoRoot = ${quotedRepoRoot}`,
    `$pythonPath = ${quotedPythonPath}`,
    "if (-not (Test-Path $pythonPath)) { py -3.12 -m venv (Join-Path $repoRoot \".venv312\") }",
    "& $pythonPath -m pip install --upgrade pip",
    "Push-Location $repoRoot",
    "& $pythonPath -m pip install -e \".[dev]\"",
    "Pop-Location",
    "Write-Host \"MAS runtime install complete. Run 'MAS: Start API' next.\"",
  ];
}

function createOutputChannel(): vscode.OutputChannel {
  return vscode.window.createOutputChannel("MAS");
}

function getAiProviderConfig(context: vscode.ExtensionContext): StoredAiProviderConfig | undefined {
  return context.globalState.get<StoredAiProviderConfig>("masi.aiProvider");
}

function getAiProviderSummary(context: vscode.ExtensionContext): string {
  const provider = getAiProviderConfig(context);
  if (!provider) {
    return "not connected";
  }
  const apiKeyStatus = provider.apiKeyConfigured ? "key saved" : "key missing";
  const endpoint = provider.baseUrl ? ` | ${provider.baseUrl}` : "";
  return `connected to ${provider.label} | ${provider.model} | ${apiKeyStatus}${endpoint}`;
}

async function configureAiProvider(
  context: vscode.ExtensionContext,
  output: vscode.OutputChannel,
  sidebar?: MasiSidebarProvider,
): Promise<void> {
  const current = getAiProviderConfig(context);
  const picked = await vscode.window.showQuickPick(
    AI_PROVIDER_CHOICES.map((item) => ({
      label: item.label,
      description: item.defaultModel,
      detail: item.id === current?.providerId ? "currently selected" : undefined,
      item,
    })),
    {
      title: "Connect MAS To An LLM",
      placeHolder: "Choose the LLM provider you want MAS chat to use",
    },
  );
  if (!picked) {
    return;
  }

  const model = await vscode.window.showInputBox({
    title: "MAS LLM Model",
    prompt: "Enter the model name for this provider",
    value: current?.providerId === picked.item.id ? current.model : picked.item.defaultModel,
    ignoreFocusOut: true,
  });
  if (!model) {
    return;
  }

  const needsBaseUrlInput = picked.item.id === "compatible";
  const baseUrl = needsBaseUrlInput
    ? await vscode.window.showInputBox({
      title: "MAS Provider Base URL",
      prompt: "Enter the OpenAI-compatible base URL for this provider",
      value: current?.providerId === picked.item.id ? current.baseUrl ?? picked.item.defaultBaseUrl ?? "" : picked.item.defaultBaseUrl ?? "",
      ignoreFocusOut: true,
    })
    : (picked.item.defaultBaseUrl ?? current?.baseUrl);

  const apiKey = await vscode.window.showInputBox({
    title: "MAS LLM API Key",
    prompt: `Paste the API key for ${picked.item.label}`,
    password: true,
    placeHolder: picked.item.placeholder,
    ignoreFocusOut: true,
  });
  if (!apiKey) {
    return;
  }

  const providerConfig: StoredAiProviderConfig = {
    providerId: picked.item.id,
    label: picked.item.label,
    model,
    baseUrl: baseUrl?.trim() || undefined,
    apiKeyConfigured: true,
  };

  await context.secrets.store(`masi.aiProviderKey.${picked.item.id}`, apiKey.trim());
  await context.globalState.update("masi.aiProvider", providerConfig);
  output.appendLine(`Updated MAS LLM provider: ${providerConfig.label} (${providerConfig.model})`);
  void vscode.window.showInformationMessage(`MAS is now connected to ${providerConfig.label} (${providerConfig.model}).`);
  await sidebar?.refresh();
  MasiPanel.refreshVisible();
}

class ChatHistoryStore {
  private readonly listeners = new Set<() => void>();

  public constructor(private readonly context: vscode.ExtensionContext) {}

  public load(taskId?: string): PanelChatMessage[] {
    return this.context.workspaceState.get<PanelChatMessage[]>(this.getThreadKey(taskId), []);
  }

  public async save(messages: PanelChatMessage[], taskId?: string): Promise<void> {
    await this.context.workspaceState.update(this.getThreadKey(taskId), messages);
    this.emit();
  }

  public getActiveTaskId(): string | undefined {
    return this.context.workspaceState.get<string>(this.getActiveTaskKey());
  }

  public async setActiveTaskId(taskId?: string): Promise<void> {
    await this.context.workspaceState.update(this.getActiveTaskKey(), taskId);
    this.emit();
  }

  public subscribe(listener: () => void): vscode.Disposable {
    this.listeners.add(listener);
    return new vscode.Disposable(() => {
      this.listeners.delete(listener);
    });
  }

  private emit(): void {
    for (const listener of this.listeners) {
      listener();
    }
  }

  private getActiveTaskKey(): string {
    const workspaceKey = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "global";
    return `masi.chatActiveTask::${workspaceKey}`;
  }

  private getThreadKey(taskId?: string): string {
    const workspaceKey = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "global";
    return `masi.chatHistory::${workspaceKey}::${taskId ?? "general"}`;
  }
}

function getDefaultChatMessages(): PanelChatMessage[] {
  return [];
}

type MasiAction =
  | "installRuntime"
  | "startApi"
  | "healthCheck"
  | "analyzeWorkspace"
  | "showLastTask"
  | "configureProvider"
  | "applyApprovedEdits"
  | "refreshSidebar"
  | "openSidebar";

const MAS_ACTIONS: Record<MasiAction, { command: string; reply: string }> = {
  installRuntime: {
    command: "masi.installRuntime",
    reply: "Started the MAS runtime install flow. Check the MAS Install terminal if you want the live setup output.",
  },
  startApi: {
    command: "masi.startApi",
    reply: "Starting the MAS API now. Once it is up, run a health check or jump straight into analysis.",
  },
  healthCheck: {
    command: "masi.healthCheck",
    reply: "Running a MAS health check now.",
  },
  analyzeWorkspace: {
    command: "masi.analyzeWorkspace",
    reply: "Submitting the current workspace to MAS for analysis.",
  },
  showLastTask: {
    command: "masi.showLastTask",
    reply: "Opening the latest MAS task summary.",
  },
  configureProvider: {
    command: "masi.configureProvider",
    reply: "Opening the LLM connection flow so you can add or change an API key and model.",
  },
  applyApprovedEdits: {
    command: "masi.applyApprovedEdits",
    reply: "Applying the approved edit now. MAS will report the file changes back here.",
  },
  refreshSidebar: {
    command: "masi.refreshSidebar",
    reply: "Refreshing the MAS sidebar.",
  },
  openSidebar: {
    command: "workbench.view.extension.masi",
    reply: "Opening the MAS sidebar.",
  },
};

function resolvePromptAction(prompt: string): MasiAction | undefined {
  const normalized = prompt.toLowerCase();
  if (normalized.includes("install") || normalized.includes("setup") || normalized.includes("runtime")) {
    return "installRuntime";
  }
  if ((normalized.includes("start") || normalized.includes("launch")) && normalized.includes("api")) {
    return "startApi";
  }
  if (normalized.includes("health") || normalized.includes("status") || normalized.includes("ping")) {
    return "healthCheck";
  }
  if (normalized.includes("analyze") || normalized.includes("scan") || normalized.includes("inspect workspace")) {
    return "analyzeWorkspace";
  }
  if (normalized.includes("last task") || normalized.includes("latest task") || normalized.includes("show task")) {
    return "showLastTask";
  }
  if (normalized.includes("api key") || normalized.includes("provider") || normalized.includes("model")) {
    return "configureProvider";
  }
  if (normalized.includes("apply approved") || normalized.includes("apply the patch") || normalized.includes("make the change")) {
    return "applyApprovedEdits";
  }
  if (normalized.includes("sidebar")) {
    return "openSidebar";
  }
  return undefined;
}

function isConnectionRefusedError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return message.includes("ECONNREFUSED") || message.includes("socket hang up");
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

interface ChatRenderState {
  title: string;
  messages: PanelChatMessage[];
  runtimeStatus: RuntimeStatus;
  healthStatus: string;
  providerSummary: string;
  selectedTask?: TaskItem;
}

function renderStructuredSection(title: string, value?: string, items: string[] = []): string {
  if (!value && items.length === 0) {
    return "";
  }
  const renderedItems = items.length > 0
    ? `<div class="structured-list">${items.map((item) => `<div class="structured-item">${escapeHtml(item)}</div>`).join("")}</div>`
    : "";
  return `
    <div class="info-card structured-card">
      <div class="card-title">${escapeHtml(title)}</div>
      ${value ? `<div class="card-body">${escapeHtml(value)}</div>` : ""}
      ${renderedItems}
    </div>
  `;
}

function renderMessageCards(message: PanelChatMessage): string {
  const cards = message.cards ?? [];
  const followUpActions = message.followUpActions ?? [];
  const structuredMarkup = message.role === "assistant"
    ? [
      renderStructuredSection("summary:", message.summary),
      renderStructuredSection("actions taken:", undefined, message.actionsTaken ?? []),
      renderStructuredSection("files in focus:", undefined, message.filesInFocus ?? []),
      renderStructuredSection("symbols in focus:", undefined, message.symbolsInFocus ?? []),
      renderStructuredSection("files changed:", undefined, message.filesChanged ?? []),
      renderStructuredSection("code changes:", undefined, message.codeChanges ?? []),
      renderStructuredSection("validation:", undefined, message.validationResults ?? []),
      renderStructuredSection("suggestions:", undefined, message.suggestions ?? []),
        renderStructuredSection("next step:", message.nextStep),
      ].join("")
    : "";
  const cardMarkup = cards.length > 0
    ? `<div class="card-list">${cards.map((card) => `
        <div class="info-card">
          <div class="card-title">${escapeHtml(card.title)}</div>
          <div class="card-body">${escapeHtml(card.body)}</div>
          ${card.action ? `<button class="chip" data-action="${escapeHtml(card.action)}">${escapeHtml(card.action_label ?? 'open')}</button>` : ''}
        </div>
      `).join("")}</div>`
    : "";
  const structuredCards = structuredMarkup ? `<div class="card-list">${structuredMarkup}</div>` : "";
  const followUpsMarkup = followUpActions.length > 0
    ? `<div class="follow-ups"><span class="meta-label">options:</span>${followUpActions.map((item) => `
        <button class="chip" data-action="${escapeHtml(item.action)}">${escapeHtml(item.label)}</button>
      `).join("")}</div>`
    : "";
  return structuredCards + cardMarkup + followUpsMarkup;
}

function renderMessages(messages: PanelChatMessage[]): string {
  return messages.map((message) => `
    <div class="message-row ${message.role}">
      <div class="message-bubble ${message.role}">
        <div class="message-role">${message.role === 'assistant' ? 'MAS' : 'You'}</div>
        <div class="message-body">${escapeHtml(message.text)}</div>
        ${renderMessageCards(message)}
      </div>
    </div>
  `).join("");
}

function renderTaskSummary(task: TaskItem | undefined): string {
  if (!task) {
    return 'task: none';
  }
  const result = task.result ?? {};
  return [
    `task: ${escapeHtml(task.task_id)}`,
    `status ${escapeHtml(task.status)}`,
    `violations ${escapeHtml(String(result.violations_found ?? task.violations.length))}`,
    `repairs ${escapeHtml(String(result.repairs_proposed ?? task.repairs.length))}`,
  ].join(' | ');
}

function renderChatHtml(webview: vscode.Webview, state: ChatRenderState): string {
  const nonce = String(Date.now());
  const setupParts = [
    `repo ${state.runtimeStatus.repoRootExists ? 'ready' : 'unset'}`,
    `runtime ${state.runtimeStatus.pythonExists ? 'ready' : 'missing'}`,
    `api ${state.healthStatus}`,
  ].join(' | ');
  const systemButtons = [
    ['installRuntime', 'setup'],
    ['startApi', 'start api'],
    ['healthCheck', 'health'],
    ['analyzeWorkspace', 'analyze'],
    ['showLastTask', 'last task'],
  ].map(([action, label]) => `
      <button class="chip" data-action="${action}">${label}</button>
    `).join('');
  const modelButtons = `
      <button class="chip" data-action="configureProvider">${state.providerSummary === 'not connected' ? 'connect llm' : 'change llm'}</button>
    `;
  const transcript = renderMessages(state.messages);

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${escapeHtml(state.title)}</title>
  <style>
    :root {
      --bg: #000000;
      --panel: #070707;
      --panel-2: #101010;
      --panel-3: #171717;
      --border: #242424;
      --text: #ffffff;
      --muted: #cfcfcf;
      --soft: #8d8d8d;
    }
    * { box-sizing: border-box; }
    html, body {
      height: 100%;
      overflow: hidden;
    }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Georgia, "Times New Roman", serif;
    }
    .shell {
      display: flex;
      flex-direction: column;
      height: 100vh;
      background: var(--bg);
    }
    .topbar {
      padding: 14px 20px 10px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      border-bottom: 1px solid var(--border);
      background: var(--bg);
    }
    .topbar-left, .topbar-right {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .brand {
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0.01em;
    }
    .topbar-meta {
      color: var(--soft);
      font-size: 12px;
      text-align: right;
      font-family: "Segoe UI", sans-serif;
    }
    .dashboard {
      display: flex;
      flex-direction: column;
      gap: 8px;
      padding: 8px 20px 12px;
      border-bottom: 1px solid var(--border);
      background: linear-gradient(180deg, #050505 0%, #020202 100%);
    }
    .dashboard.hidden {
      display: none;
    }
    .category {
      border: 1px solid var(--border);
      border-radius: 14px;
      background: rgba(255, 255, 255, 0.02);
      overflow: hidden;
    }
    .category[open] {
      background: rgba(255, 255, 255, 0.03);
    }
    .category-summary {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 12px;
      cursor: pointer;
      list-style: none;
    }
    .category-summary::-webkit-details-marker {
      display: none;
    }
    .category-summary-main {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      flex: 1;
    }
    .category-copy {
      min-width: 0;
      flex: 1;
    }
    .category-text {
      color: var(--soft);
      font-size: 12px;
      line-height: 1.4;
      font-family: "Segoe UI", sans-serif;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .category-arrow {
      color: var(--soft);
      font-size: 11px;
      transition: transform 120ms ease;
      font-family: "Segoe UI Symbol", "Segoe UI", sans-serif;
    }
    .category[open] .category-arrow {
      transform: rotate(90deg);
    }
    .category-body {
      display: flex;
      flex-direction: column;
      gap: 8px;
      padding: 0 12px 10px;
      border-top: 1px solid rgba(255, 255, 255, 0.06);
    }
    .category-label {
      color: var(--text);
      font-weight: 700;
      font-size: 12px;
      text-transform: lowercase;
      font-family: "Segoe UI", sans-serif;
    }
    .meta-line {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      font-family: "Segoe UI", sans-serif;
    }
    .chip-row, .follow-ups {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    .chat {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-height: 0;
      overflow: hidden;
      position: relative;
    }
    .messages {
      flex: 1;
      overflow-y: auto;
      padding: 24px 24px 180px;
      display: flex;
      flex-direction: column;
      gap: 18px;
      min-height: 0;
      scroll-behavior: smooth;
    }
    .messages-inner {
      width: min(100%, 980px);
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }
    .message-row {
      display: flex;
      width: 100%;
    }
    .message-row.assistant {
      justify-content: flex-start;
    }
    .message-row.user {
      justify-content: flex-end;
    }
    .message-bubble {
      width: min(84%, 760px);
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 14px 16px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      box-shadow: 0 16px 32px rgba(0, 0, 0, 0.2);
    }
    .message-bubble.assistant {
      background: linear-gradient(180deg, #0a0a0a 0%, #070707 100%);
    }
    .message-bubble.user {
      background: linear-gradient(180deg, #171717 0%, #111111 100%);
    }
    .message-role {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--soft);
      font-family: "Segoe UI", sans-serif;
    }
    .message-body {
      font-size: 17px;
      line-height: 1.65;
      white-space: pre-wrap;
    }
    .card-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .info-card {
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 12px 14px;
      background: #020202;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .card-title {
      color: var(--text);
      font-size: 12px;
      font-weight: 700;
      text-transform: lowercase;
      font-family: "Segoe UI", sans-serif;
    }
    .card-body {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.55;
      white-space: pre-wrap;
      font-family: "Segoe UI", sans-serif;
    }
    .structured-card {
      gap: 6px;
    }
    .structured-list {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .structured-item {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
      font-family: "Segoe UI", sans-serif;
      padding-left: 12px;
      position: relative;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .structured-item::before {
      content: "-";
      color: var(--soft);
      position: absolute;
      left: 0;
    }
    .chip {
      border: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.02);
      color: var(--text);
      border-radius: 999px;
      padding: 7px 12px;
      font-size: 12px;
      cursor: pointer;
      font-family: "Segoe UI", sans-serif;
    }
    .chip:hover {
      border-color: #ffffff;
    }
    .workspace-toggle {
      border: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.02);
      color: var(--text);
      border-radius: 999px;
      padding: 7px 12px;
      cursor: pointer;
      font-size: 12px;
      font-family: "Segoe UI", sans-serif;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 7px;
    }
    .workspace-toggle-arrow {
      color: var(--soft);
      font-size: 11px;
      transition: transform 120ms ease;
      font-family: "Segoe UI Symbol", "Segoe UI", sans-serif;
    }
    .workspace-toggle.open .workspace-toggle-arrow {
      transform: rotate(90deg);
    }
    .composer {
      position: absolute;
      left: 0;
      right: 0;
      bottom: 0;
      padding: 14px 20px 20px;
      background: linear-gradient(180deg, rgba(0, 0, 0, 0) 0%, rgba(0, 0, 0, 0.94) 28%, #000000 100%);
    }
    .composer-shell {
      width: min(100%, 980px);
      margin: 0 auto;
      border: 1px solid var(--border);
      border-radius: 24px;
      background: rgba(10, 10, 10, 0.98);
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      box-shadow: 0 24px 40px rgba(0, 0, 0, 0.35);
    }
    .composer textarea {
      width: 100%;
      min-height: 88px;
      max-height: 180px;
      resize: none;
      border: none;
      background: transparent;
      color: var(--text);
      padding: 8px 10px 0;
      font: inherit;
      line-height: 1.6;
      font-size: 16px;
      outline: none;
      font-family: "Segoe UI", sans-serif;
    }
    .composer textarea::placeholder {
      color: var(--soft);
    }
    .composer-bottom {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }
    .hint {
      color: var(--soft);
      font-size: 12px;
      font-family: "Segoe UI", sans-serif;
    }
    .send {
      border: 1px solid #ffffff;
      background: #151515;
      color: #ffffff;
      border-radius: 14px;
      padding: 10px 16px;
      cursor: pointer;
      font-weight: 700;
      font-family: "Segoe UI", sans-serif;
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="topbar">
      <div class="topbar-left">
        <div class="brand">${escapeHtml(state.title)}</div>
        <button class="workspace-toggle" id="workspaceToggle">
          <span>workspace</span>
          <span class="workspace-toggle-arrow">▸</span>
        </button>
      </div>
      <div class="topbar-right">
        <div class="topbar-meta">${escapeHtml(setupParts)}</div>
      </div>
    </div>
    <div class="dashboard hidden" id="workspacePanel">
      <details class="category">
        <summary class="category-summary">
          <div class="category-summary-main">
            <div class="category-label">system:</div>
            <div class="category-copy">
              <div class="category-text">setup | start api | health | analyze | last task</div>
            </div>
          </div>
          <div class="category-arrow">▸</div>
        </summary>
        <div class="category-body">
          <div class="chip-row">${systemButtons}</div>
        </div>
      </details>
      <details class="category">
        <summary class="category-summary">
          <div class="category-summary-main">
            <div class="category-label">llm:</div>
            <div class="category-copy">
              <div class="category-text">${escapeHtml(state.providerSummary)}</div>
            </div>
          </div>
          <div class="category-arrow">▸</div>
        </summary>
        <div class="category-body">
          <div class="meta-line">${escapeHtml(state.providerSummary)}</div>
          <div class="chip-row">${modelButtons}</div>
        </div>
      </details>
      <details class="category">
        <summary class="category-summary">
          <div class="category-summary-main">
            <div class="category-label">task:</div>
            <div class="category-copy">
              <div class="category-text">${renderTaskSummary(state.selectedTask)}</div>
            </div>
          </div>
          <div class="category-arrow">▸</div>
        </summary>
        <div class="category-body">
          <div class="meta-line">${renderTaskSummary(state.selectedTask)}</div>
        </div>
      </details>
    </div>
    <div class="chat">
      <div class="messages">
        <div class="messages-inner">
          ${transcript}
        </div>
      </div>
      <div class="composer">
        <div class="composer-shell">
          <textarea id="promptInput" placeholder="Tell MAS what you want in English. Example: connect to DeepSeek, start the API, then analyze this repo."></textarea>
          <div class="composer-bottom">
            <div class="hint">enter: send | shift+enter: new line</div>
            <button class="send" id="sendPrompt">send</button>
          </div>
        </div>
      </div>
    </div>
  </div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const state = vscode.getState() || { workspaceOpen: false };
    const workspacePanel = document.getElementById('workspacePanel');
    const workspaceToggle = document.getElementById('workspaceToggle');
    const setWorkspaceOpen = (isOpen) => {
      workspacePanel.classList.toggle('hidden', !isOpen);
      workspaceToggle.classList.toggle('open', isOpen);
      vscode.setState({ workspaceOpen: isOpen });
    };
    setWorkspaceOpen(Boolean(state.workspaceOpen));
    workspaceToggle.addEventListener('click', () => {
      setWorkspaceOpen(workspacePanel.classList.contains('hidden'));
    });
    document.querySelectorAll('[data-action]').forEach((element) => {
      element.addEventListener('click', () => {
        vscode.postMessage({ type: element.getAttribute('data-action') });
      });
    });
    const promptInput = document.getElementById('promptInput');
    const sendPrompt = document.getElementById('sendPrompt');
    const submitPrompt = () => {
      const text = promptInput.value.trim();
      if (!text) {
        return;
      }
      vscode.postMessage({ type: 'prompt', text });
      promptInput.value = '';
    };
    sendPrompt.addEventListener('click', submitPrompt);
    promptInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        submitPrompt();
      }
    });
  </script>
</body>
</html>`;
}

function getPanelHtml(
  webview: vscode.Webview,
  messages: PanelChatMessage[],
  runtimeStatus: RuntimeStatus,
  healthStatus: string,
  providerSummary: string,
  selectedTask?: TaskItem,
): string {
  return renderChatHtml(webview, {
    title: 'MAS',
    messages,
    runtimeStatus,
    healthStatus,
    providerSummary,
    selectedTask,
  });
}

function requestJson<T>(method: string, path: string, body?: JsonObject): Promise<T> {
  const { apiBaseUrl } = getConfig();
  const baseUrl = new URL(apiBaseUrl);
  const payload = body ? JSON.stringify(body) : undefined;

  return new Promise((resolve, reject) => {
    const request = http.request(
      {
        method,
        hostname: baseUrl.hostname,
        port: baseUrl.port,
        path,
        headers: {
          "Content-Type": "application/json",
          ...(payload ? { "Content-Length": Buffer.byteLength(payload) } : {}),
        },
      },
      (response) => {
        const chunks: Buffer[] = [];
        response.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
        response.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          if (!response.statusCode || response.statusCode >= 400) {
            reject(new Error(raw || `Request failed with status ${response.statusCode ?? "unknown"}`));
            return;
          }
          resolve((raw ? JSON.parse(raw) : {}) as T);
        });
      },
    );
    request.on("error", reject);
    if (payload) {
      request.write(payload);
    }
    request.end();
  });
}

function requestExternalJson<T>(
  method: string,
  rawUrl: string,
  headers: Record<string, string>,
  body?: JsonObject,
): Promise<T> {
  const target = new URL(rawUrl);
  const payload = body ? JSON.stringify(body) : undefined;
  const client = target.protocol === "https:" ? https : http;

  return new Promise((resolve, reject) => {
    const request = client.request(
      {
        method,
        hostname: target.hostname,
        port: target.port || (target.protocol === "https:" ? 443 : 80),
        path: `${target.pathname}${target.search}`,
        headers: {
          "Content-Type": "application/json",
          ...headers,
          ...(payload ? { "Content-Length": Buffer.byteLength(payload) } : {}),
        },
      },
      (response) => {
        const chunks: Buffer[] = [];
        response.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
        response.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          if (!response.statusCode || response.statusCode >= 400) {
            reject(new Error(raw || `Request failed with status ${response.statusCode ?? "unknown"}`));
            return;
          }
          resolve((raw ? JSON.parse(raw) : {}) as T);
        });
      },
    );
    request.on("error", reject);
    if (payload) {
      request.write(payload);
    }
    request.end();
  });
}

async function getAiProviderApiKey(context: vscode.ExtensionContext): Promise<string | undefined> {
  const provider = getAiProviderConfig(context);
  if (!provider) {
    return undefined;
  }
  return context.secrets.get(`masi.aiProviderKey.${provider.providerId}`);
}

let apiStartupPromise: Promise<boolean> | undefined;

function buildStartApiCommand(pythonPath: string): string {
  if (process.platform === "win32") {
    return `& ${quoteForPowerShell(pythonPath)} -m uvicorn src.api.app:create_app --factory --host 127.0.0.1 --port 8000`;
  }
  return `"${pythonPath.replace(/"/g, '\\"')}" -m uvicorn src.api.app:create_app --factory --host 127.0.0.1 --port 8000`;
}

async function waitForApiReady(timeoutMs = 20000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await requestJson<JsonObject>("GET", "/health");
      return true;
    } catch (error) {
      if (!isConnectionRefusedError(error)) {
        throw error;
      }
      await sleep(1000);
    }
  }
  return false;
}

async function startApiProcess(
  output: vscode.OutputChannel,
  sidebar: MasiSidebarProvider,
  options: { revealTerminal: boolean; reason: string },
): Promise<boolean> {
  if (apiStartupPromise) {
    return apiStartupPromise;
  }

  apiStartupPromise = (async () => {
    try {
      await requestJson<JsonObject>("GET", "/health");
      output.appendLine("MAS API is already healthy on http://127.0.0.1:8000.");
      await sidebar.refresh();
      return true;
    } catch (error) {
      if (!isConnectionRefusedError(error)) {
        throw error;
      }
    }

    const repoRoot = await ensureRepoRootConfigured();
    if (!repoRoot) {
      return false;
    }

    const { pythonPath } = getConfig();
    if (!pythonPath || !fs.existsSync(pythonPath)) {
      output.appendLine(`MAS API start skipped: Python runtime not found at ${pythonPath || "(unset)"}.`);
      if (options.revealTerminal) {
        void vscode.window.showWarningMessage("MAS runtime is not installed yet. Run 'MAS: Install Runtime' first.");
      }
      await sidebar.refresh();
      return false;
    }

    const terminal = vscode.window.createTerminal({
      name: "MAS API",
      cwd: repoRoot,
    });
    if (options.revealTerminal) {
      terminal.show(true);
    }
    terminal.sendText(buildStartApiCommand(pythonPath), true);
    output.appendLine(`Starting MAS API in ${repoRoot} (${options.reason}).`);

    const ready = await waitForApiReady();
    output.appendLine(
      ready
        ? "MAS API is healthy on http://127.0.0.1:8000."
        : "MAS API is still starting or failed to come up. Check the MAS API terminal for details.",
    );
    await sidebar.refresh();
    return ready;
  })();

  try {
    return await apiStartupPromise;
  } finally {
    apiStartupPromise = undefined;
  }
}

async function ensureApiAvailable(
  output: vscode.OutputChannel,
  sidebar: MasiSidebarProvider,
  options: { revealTerminal: boolean; reason: string },
): Promise<boolean> {
  try {
    await requestJson<JsonObject>("GET", "/health");
    return true;
  } catch (error) {
    if (!isConnectionRefusedError(error)) {
      throw error;
    }

    const runtimeStatus = getRuntimeStatus();
    if (!runtimeStatus.pythonExists) {
      return false;
    }
    return startApiProcess(output, sidebar, options);
  }
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderTaskDocument(task: TaskItem): string {
  const result = task.result ?? {};
  const lines = [
    `Task ID: ${task.task_id}`,
    `Status: ${task.status}`,
    `Repo: ${task.repo_path}`,
    `Violations: ${String(result.violations_found ?? task.violations.length)}`,
    `Hypotheses: ${String(result.hypotheses_generated ?? task.hypotheses.length)}`,
    `Repairs: ${String(result.repairs_proposed ?? task.repairs.length)}`,
    `Work items: ${String(task.work_items.length)}`,
  ];
  const explanation = result.explanation;
  if (typeof explanation === "string" && explanation.length > 0) {
    lines.push("");
    lines.push("Explanation:");
    lines.push(explanation);
  }
  if (task.violations.length > 0) {
    lines.push("");
    lines.push("Violations:");
    for (const violation of task.violations) {
      lines.push(`- [${violation.severity}] ${violation.rule} :: ${violation.file_path} :: ${violation.message}`);
    }
  }
  if (task.repairs.length > 0) {
    lines.push("");
    lines.push("Repairs:");
    for (const repair of task.repairs) {
      lines.push(`- [${repair.status}] ${repair.repair_id} :: ${repair.description}`);
    }
  }
  return lines.join("\n");
}

async function requestBackendChatReply(
  prompt: string,
  context: vscode.ExtensionContext,
  output: vscode.OutputChannel,
  sidebar: MasiSidebarProvider,
): Promise<BackendChatResponse | undefined> {
  const runtimeStatus = getRuntimeStatus();
  const fallbackAction = resolvePromptAction(prompt);
  const provider = getAiProviderConfig(context);
  const apiReady = await ensureApiAvailable(output, sidebar, {
    revealTerminal: false,
    reason: "backend chat query",
  });
  if (!apiReady) {
    if (fallbackAction) {
      return {
        answer: runtimeStatus.pythonExists
          ? "The backend chat is not reachable yet, but I can still run that local MAS action for you."
          : "The MAS runtime is not installed yet, so I can fall back to the local setup flow.",
        intent: "action",
        recommended_action: fallbackAction,
        summary: runtimeStatus.pythonExists
          ? "The chat backend is offline, but MAS can still run the local action directly."
          : "The MAS runtime still needs setup before the backend chat can work.",
        files_in_focus: runtimeStatus.repoRoot ? [runtimeStatus.repoRoot] : [],
        suggestions: runtimeStatus.pythonExists
          ? ["Run the requested action, then retry the chat request."]
          : ["Run setup first, then start the API."],
        next_step: runtimeStatus.pythonExists ? "Run the local action now." : "Run setup, then start api.",
        follow_up_actions: [
          { action: fallbackAction, label: fallbackAction === "installRuntime" ? "setup" : "run" },
        ],
      };
    }
    return {
      answer: runtimeStatus.pythonExists
        ? "I tried to bring the MAS API online, but it is still starting or failed to start. Check the MAS API terminal for the real traceback."
        : "The MAS API is offline because the runtime is not installed yet. Ask me to install the runtime first.",
      intent: "answer",
    };
  }

  return requestJson<BackendChatResponse>("POST", "/api/v1/chat", {
    prompt,
    task_id: context.globalState.get<string>("masi.lastTaskId") ?? "",
    repo_path: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "",
    provider: provider?.providerId ?? "",
    model: provider?.model ?? "",
  });
}

function extractJsonObject(raw: string): string {
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fenced?.[1]) {
    return fenced[1].trim();
  }
  const firstBrace = raw.indexOf("{");
  const lastBrace = raw.lastIndexOf("}");
  if (firstBrace >= 0 && lastBrace > firstBrace) {
    return raw.slice(firstBrace, lastBrace + 1);
  }
  return raw.trim();
}

async function buildOperatorContext(
  context: vscode.ExtensionContext,
  output: vscode.OutputChannel,
  sidebar: MasiSidebarProvider,
): Promise<string> {
  const runtimeStatus = getRuntimeStatus();
  const parts = [
    `repo_root=${runtimeStatus.repoRoot || "(unset)"}`,
    `runtime_ready=${runtimeStatus.pythonExists}`,
    `workspace=${vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "(none)"}`,
    `last_task_id=${context.globalState.get<string>("masi.lastTaskId") ?? "(none)"}`,
  ];

  try {
    const apiReady = await ensureApiAvailable(output, sidebar, {
      revealTerminal: false,
      reason: "llm context refresh",
    });
    parts.push(`api_ready=${apiReady}`);
    if (apiReady) {
      const health = await requestJson<JsonObject>("GET", "/health");
      parts.push(`api_status=${String(health.status ?? "unknown")}`);
      const tasks = await requestJson<TaskItem[]>("GET", "/api/v1/tasks?limit=1");
      if (tasks.length > 0) {
        const task = tasks[0];
        parts.push(`latest_task=${task.task_id}`);
        parts.push(`latest_task_status=${task.status}`);
        parts.push(`latest_task_repo=${task.repo_path}`);
        parts.push(`latest_task_violations=${task.violations.length}`);
        parts.push(`latest_task_repairs=${task.repairs.length}`);
      }
    }
  } catch (error) {
    parts.push(`api_context_error=${error instanceof Error ? error.message : String(error)}`);
  }

  return parts.join("\n");
}

async function requestOpenAiCompatibleReply(
  baseUrl: string,
  apiKey: string,
  model: string,
  systemPrompt: string,
  userPrompt: string,
): Promise<string> {
  type OpenAiMessage = { role: string; content: string };
  const response = await requestExternalJson<{
    choices?: Array<{ message?: OpenAiMessage }>;
  }>(
    "POST",
    `${baseUrl.replace(/\/$/, "")}/chat/completions`,
    {
      Authorization: `Bearer ${apiKey}`,
    },
    {
      model,
      temperature: 0.2,
      response_format: { type: "json_object" },
      messages: [
        { role: "system", content: systemPrompt },
        { role: "user", content: userPrompt },
      ],
    },
  );
  return String(response.choices?.[0]?.message?.content ?? "");
}

async function requestAnthropicReply(
  apiKey: string,
  model: string,
  systemPrompt: string,
  userPrompt: string,
): Promise<string> {
  const response = await requestExternalJson<{
    content?: Array<{ type?: string; text?: string }>;
  }>(
    "POST",
    "https://api.anthropic.com/v1/messages",
    {
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
    },
    {
      model,
      max_tokens: 500,
      system: systemPrompt,
      messages: [
        { role: "user", content: userPrompt },
      ],
    },
  );
  return String(response.content?.find((item) => item.type === "text")?.text ?? "");
}

async function requestExternalLlmReply(
  prompt: string,
  context: vscode.ExtensionContext,
  output: vscode.OutputChannel,
  sidebar: MasiSidebarProvider,
): Promise<BackendChatResponse | undefined> {
  const provider = getAiProviderConfig(context);
  const apiKey = await getAiProviderApiKey(context);
  if (!provider || !apiKey) {
    return undefined;
  }

  const operatorContext = await buildOperatorContext(context, output, sidebar);
  const systemPrompt = [
    "You are the natural-language interface for MAS, a local software intelligence agent.",
    "Your job is to convert plain English into one of the supported MAS actions when appropriate, otherwise answer clearly like a helpful coding agent.",
    "Supported actions: installRuntime, startApi, healthCheck, analyzeWorkspace, showLastTask, configureProvider, applyApprovedEdits.",
    "Return only JSON with this schema:",
    '{"reply":"string","action":"installRuntime|startApi|healthCheck|analyzeWorkspace|showLastTask|configureProvider|applyApprovedEdits|null","summary":"string|null","actions_taken":["string"],"files_in_focus":["string"],"files_changed":["string"],"code_changes":["string"],"symbols_in_focus":["string"],"suggestions":["string"],"next_step":"string|null","highlights":["string"],"cards":[{"title":"string","body":"string","action":"string|null","action_label":"string|null"}],"follow_up_actions":[{"action":"string","label":"string"}]}',
    "Do not mention tenants, logins, or passwords. MAS is single-user and local.",
    "If the user asks to connect an LLM, configure a provider, set an API key, or change models, choose configureProvider.",
    "When you can, fill summary, suggestions, and next_step so MAS feels like a real teammate.",
  ].join("\n");
  const userPrompt = `MAS context:\n${operatorContext}\n\nUser request:\n${prompt}`;

  const rawReply = provider.providerId === "anthropic"
    ? await requestAnthropicReply(apiKey, provider.model, systemPrompt, userPrompt)
    : await requestOpenAiCompatibleReply(provider.baseUrl ?? "https://api.openai.com/v1", apiKey, provider.model, systemPrompt, userPrompt);

  const parsed = JSON.parse(extractJsonObject(rawReply)) as {
    reply?: string;
    action?: string | null;
    summary?: string;
    actions_taken?: string[];
    files_in_focus?: string[];
    files_changed?: string[];
    code_changes?: string[];
    symbols_in_focus?: string[];
    suggestions?: string[];
    next_step?: string;
    highlights?: string[];
    cards?: ChatCard[];
    follow_up_actions?: FollowUpAction[];
  };

  return {
    answer: parsed.reply ?? "I connected to your LLM, but the reply was empty.",
    intent: parsed.action ? "action" : "answer",
    recommended_action: parsed.action ?? undefined,
    source_task_id: context.globalState.get<string>("masi.lastTaskId") ?? undefined,
    summary: parsed.summary ?? undefined,
    actions_taken: parsed.actions_taken ?? [],
    files_in_focus: parsed.files_in_focus ?? [],
    files_changed: parsed.files_changed ?? [],
    code_changes: parsed.code_changes ?? [],
    symbols_in_focus: parsed.symbols_in_focus ?? [],
    suggestions: parsed.suggestions ?? [],
    next_step: parsed.next_step ?? undefined,
    highlights: parsed.highlights ?? [],
    cards: parsed.cards ?? [],
    follow_up_actions: parsed.follow_up_actions ?? [],
  };
}

async function requestOperatorReply(
  prompt: string,
  context: vscode.ExtensionContext,
  output: vscode.OutputChannel,
  sidebar: MasiSidebarProvider,
): Promise<BackendChatResponse | undefined> {
  try {
    const backendReply = await requestBackendChatReply(prompt, context, output, sidebar);
    if (backendReply) {
      return backendReply;
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    output.appendLine(`Backend chat request failed: ${message}`);
  }

  try {
    const externalReply = await requestExternalLlmReply(prompt, context, output, sidebar);
    if (externalReply) {
      return externalReply;
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    output.appendLine(`External LLM request failed: ${message}`);
    return {
      answer: `I could not reach your connected LLM: ${message}. Use "connect llm" to update the API key, model, or provider settings.`,
      intent: "action",
      recommended_action: "configureProvider",
      follow_up_actions: [
        { action: "configureProvider", label: "connect llm" },
      ],
    };
  }
  return undefined;
}

function extractPathCandidates(prompt: string): string[] {
  const matches = prompt.match(/[A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+/g) ?? [];
  const unique: string[] = [];
  for (const match of matches) {
    const normalized = match.replace(/^['"`]|['"`]$/g, "");
    if (normalized && !unique.includes(normalized)) {
      unique.push(normalized);
    }
  }
  return unique;
}

function normalizeFocusPath(value: string): string {
  return value.replace(/^[A-Z?]{1,2}\s+/, "").trim();
}

function resolveWorkspaceFileTargets(
  prompt: string,
  fallbackFiles: string[] = [],
  limit = 1,
): string[] {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!workspaceRoot) {
    return [];
  }

  const resolved: string[] = [];
  const seen = new Set<string>();
  const candidates = [
    ...extractPathCandidates(prompt),
    ...fallbackFiles.map(normalizeFocusPath),
  ];

  for (const candidate of candidates) {
    const normalized = candidate.replace(/\\/g, path.sep);
    const absolute = path.isAbsolute(normalized)
      ? normalized
      : path.join(workspaceRoot, normalized);
    if (fs.existsSync(absolute) && fs.statSync(absolute).isFile()) {
      const finalPath = path.normalize(absolute);
      if (!seen.has(finalPath)) {
        seen.add(finalPath);
        resolved.push(finalPath);
      }
    }
    if (resolved.length >= limit) {
      break;
    }
  }

  return resolved;
}

async function requestEditableFileRewrite(
  context: vscode.ExtensionContext,
  filePath: string,
  prompt: string,
): Promise<{
  updatedContent: string;
  summary?: string;
  validationSteps: string[];
  changeNotes: string[];
}> {
  const provider = getAiProviderConfig(context);
  const apiKey = await getAiProviderApiKey(context);
  if (!provider || !apiKey) {
    throw new Error("Connect an LLM first so MAS can generate the approved edit.");
  }

  const currentContent = fs.readFileSync(filePath, "utf8");
  if (currentContent.length > 30000) {
    throw new Error("That file is too large for the current in-editor edit flow. Pick a smaller file first.");
  }

  const relativePath = vscode.workspace.asRelativePath(filePath);
  const systemPrompt = [
    "You are MAS, a local coding agent applying an explicitly approved edit.",
    "Return only JSON with this schema:",
    '{"updated_content":"string","summary":"string","validation_steps":["string"],"change_notes":["string"]}',
    "Preserve unrelated code. Make the smallest coherent change that satisfies the user request.",
    "Do not wrap the content in markdown fences.",
  ].join("\n");
  const userPrompt = [
    `Target file: ${relativePath}`,
    `User request: ${prompt}`,
    "Current file content:",
    currentContent,
  ].join("\n\n");

  const rawReply = provider.providerId === "anthropic"
    ? await requestAnthropicReply(apiKey, provider.model, systemPrompt, userPrompt)
    : await requestOpenAiCompatibleReply(
      provider.baseUrl ?? "https://api.openai.com/v1",
      apiKey,
      provider.model,
      systemPrompt,
      userPrompt,
    );

  const parsed = JSON.parse(extractJsonObject(rawReply)) as {
    updated_content?: string;
    summary?: string;
    validation_steps?: string[];
    change_notes?: string[];
  };

  if (!parsed.updated_content) {
    throw new Error("The connected LLM did not return updated file content.");
  }

  return {
    updatedContent: parsed.updated_content,
    summary: parsed.summary,
    validationSteps: parsed.validation_steps ?? [],
    changeNotes: parsed.change_notes ?? [],
  };
}

function runCommandCapture(
  command: string,
  args: string[],
  cwd: string,
): Promise<{ stdout: string; stderr: string; code: number }> {
  return new Promise((resolve, reject) => {
    execFile(command, args, { cwd }, (error, stdout, stderr) => {
      if (error && typeof (error as { code?: number }).code !== "number") {
        reject(error);
        return;
      }
      resolve({
        stdout: stdout.toString(),
        stderr: stderr.toString(),
        code: typeof (error as { code?: number } | null)?.code === "number"
          ? Number((error as { code?: number }).code)
          : 0,
      });
    });
  });
}

function buildBeforeAfterSnippet(relativePath: string, before: string, after: string): string {
  const beforeLines = before.split(/\r?\n/);
  const afterLines = after.split(/\r?\n/);
  let start = 0;
  while (
    start < beforeLines.length
    && start < afterLines.length
    && beforeLines[start] === afterLines[start]
  ) {
    start += 1;
  }

  let beforeEnd = beforeLines.length - 1;
  let afterEnd = afterLines.length - 1;
  while (
    beforeEnd >= start
    && afterEnd >= start
    && beforeLines[beforeEnd] === afterLines[afterEnd]
  ) {
    beforeEnd -= 1;
    afterEnd -= 1;
  }

  const beforeSlice = beforeLines.slice(Math.max(0, start - 1), Math.min(beforeLines.length, beforeEnd + 2));
  const afterSlice = afterLines.slice(Math.max(0, start - 1), Math.min(afterLines.length, afterEnd + 2));
  return [
    relativePath,
    "before:",
    ...beforeSlice,
    "after:",
    ...afterSlice,
  ].join("\n");
}

async function runTargetedValidation(
  filePath: string,
  output: vscode.OutputChannel,
): Promise<string[]> {
  const repoRoot = getConfig().repoRoot || vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!repoRoot) {
    return ["validation skipped: workspace root is not configured"];
  }

  const relativePath = vscode.workspace.asRelativePath(filePath);
  const extensionRoot = path.join(repoRoot, "vscode-extension", "masi-assistant");
  const pythonPath = getConfig().pythonPath;
  const suffix = path.extname(filePath).toLowerCase();
  const results: string[] = [];

  if (suffix === ".py" && fs.existsSync(pythonPath)) {
    const ruffResult = await runCommandCapture(pythonPath, ["-m", "ruff", "check", relativePath], repoRoot);
    const label = ruffResult.code === 0 ? "validation passed" : "validation failed";
    results.push(`${label}: ruff check ${relativePath}`);
    if (ruffResult.code !== 0 && ruffResult.stderr.trim()) {
      output.appendLine(ruffResult.stderr.trim());
    }
    return results;
  }

  if (
    [".ts", ".tsx", ".js", ".jsx"].includes(suffix)
    && filePath.includes(path.join("vscode-extension", "masi-assistant"))
    && fs.existsSync(extensionRoot)
  ) {
    const npmExecutable = process.platform === "win32" ? "npm.cmd" : "npm";
    const compileResult = await runCommandCapture(npmExecutable, ["run", "compile"], extensionRoot);
    const label = compileResult.code === 0 ? "validation passed" : "validation failed";
    results.push(`${label}: npm run compile`);
    if (compileResult.code !== 0 && compileResult.stderr.trim()) {
      output.appendLine(compileResult.stderr.trim());
    }
    return results;
  }

  return ["validation skipped: no targeted validator is configured for this file type"];
}

async function applyApprovedEditsForPrompt(
  prompt: string,
  context: vscode.ExtensionContext,
  output: vscode.OutputChannel,
  fallbackFiles: string[] = [],
): Promise<AppliedEditResult> {
  const targets = resolveWorkspaceFileTargets(prompt, fallbackFiles, 1);
  if (targets.length === 0) {
    throw new Error("Include a file path in your prompt, or ask MAS to inspect a file first.");
  }

  const target = targets[0];
  output.appendLine(`Applying approved edits to ${target}`);
  const currentContent = fs.readFileSync(target, "utf8");
  const rewrite = await requestEditableFileRewrite(context, target, prompt);
  const relativePath = vscode.workspace.asRelativePath(target);

  if (rewrite.updatedContent === currentContent) {
    return {
      text: `The connected LLM reviewed ${relativePath}, but it did not propose any code changes.`,
      summary: rewrite.summary ?? `No file changes were needed for ${relativePath}.`,
      filesInFocus: [relativePath],
      suggestions: ["Refine the instruction if you want a more specific edit."],
      nextStep: "Adjust the prompt and try again if you still want a change.",
    };
  }

  const approval = await vscode.window.showInformationMessage(
    `Apply MAS edit to ${relativePath}?`,
    { modal: true, detail: rewrite.summary ?? prompt },
    "Apply",
  );
  if (approval !== "Apply") {
    throw new Error("Edit canceled before writing to disk.");
  }

  const document = await vscode.workspace.openTextDocument(target);
  const lastLine = document.lineAt(document.lineCount - 1);
  const fullRange = new vscode.Range(0, 0, document.lineCount - 1, lastLine.text.length);
  const edit = new vscode.WorkspaceEdit();
  edit.replace(document.uri, fullRange, rewrite.updatedContent);
  const applied = await vscode.workspace.applyEdit(edit);
  if (!applied) {
    throw new Error("VS Code could not apply the generated edit.");
  }
  await document.save();

  const validationResults = await runTargetedValidation(target, output);
  const diffSnippet = buildBeforeAfterSnippet(relativePath, currentContent, rewrite.updatedContent);
  return {
    text: `I applied the approved edit to ${relativePath} and saved the file.`,
    summary: rewrite.summary ?? `Applied an approved edit to ${relativePath}.`,
    actionsTaken: [
      `rewrote ${relativePath} with the connected LLM`,
      "saved the updated file to disk",
    ],
    filesInFocus: [relativePath],
    filesChanged: [relativePath],
    codeChanges: [diffSnippet, ...rewrite.changeNotes].slice(0, 4),
    validationResults,
    suggestions: [
      "Review the diff to confirm the change matches your intent.",
      "Run the smallest validation step before moving on.",
    ],
    nextStep: validationResults[0] ?? rewrite.validationSteps[0] ?? "Run the smallest relevant validation command.",
  };
}

class MasiSidebarProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = "masi.sidebar";

  private view?: vscode.WebviewView;
  private selectedTaskId?: string;
  private pollHandle?: NodeJS.Timeout;
  private messages: PanelChatMessage[];
  private lastOfflineNotice?: string;
  private readonly historySubscription: vscode.Disposable;

  public constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly output: vscode.OutputChannel,
    private readonly history: ChatHistoryStore,
  ) {
    this.selectedTaskId = this.history.getActiveTaskId() ?? this.context.globalState.get<string>("masi.lastTaskId");
    this.messages = this.loadMessages(this.selectedTaskId);
    this.historySubscription = this.history.subscribe(() => {
      void this.syncFromHistory();
    });
  }

  public resolveWebviewView(webviewView: vscode.WebviewView): void | Thenable<void> {
    this.view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
    };

    webviewView.webview.onDidReceiveMessage(async (message: JsonObject) => {
      const type = String(message.type ?? "");
      if (type === "prompt") {
        const text = String(message.text ?? "").trim();
        if (text) {
          await this.handlePrompt(text);
        }
      } else if (type in MAS_ACTIONS) {
        const action = type as MasiAction;
        if (action === "installRuntime" || action === "startApi" || action === "healthCheck" || action === "analyzeWorkspace") {
          await this.runAction(action);
        } else {
          await vscode.commands.executeCommand(MAS_ACTIONS[action].command);
        }
      } else if (type === "refresh") {
        await this.refresh();
      } else if (type === "selectTask") {
        const taskId = String(message.taskId ?? "");
        if (taskId) {
          await this.setActiveTask(taskId);
          await this.refresh();
        }
      } else if (type === "openTaskDocument") {
        const taskId = String(message.taskId ?? "");
        if (taskId) {
          await this.showTask(taskId);
        }
      } else if (type === "approveRepair") {
        const repairId = String(message.repairId ?? "");
        if (repairId) {
          await this.approveRepair(repairId);
        }
      } else if (type === "showViolation") {
        const taskId = String(message.taskId ?? "");
        const violationId = String(message.violationId ?? "");
        if (taskId && violationId) {
          await this.showViolation(taskId, violationId);
        }
      }
    });

    webviewView.onDidDispose(() => {
      if (this.pollHandle) {
        clearInterval(this.pollHandle);
        this.pollHandle = undefined;
      }
      this.view = undefined;
    });

    this.startPolling();
    void this.refresh();
  }

  public async refresh(): Promise<void> {
    if (!this.view) {
      return;
    }

    this.selectedTaskId = this.history.getActiveTaskId() ?? this.selectedTaskId;
    this.messages = this.loadMessages(this.selectedTaskId);

    let healthStatus = "unknown";
    let tasks: TaskItem[] = [];
    let selectedTask: TaskItem | undefined;
    const runtimeStatus = getRuntimeStatus();

    try {
      const health = await requestJson<JsonObject>("GET", "/health");
      healthStatus = String(health.status ?? "unknown");
      this.lastOfflineNotice = undefined;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (isConnectionRefusedError(error)) {
        healthStatus = runtimeStatus.pythonExists ? "starting" : "offline";
        const offlineNotice = runtimeStatus.pythonExists
          ? `MAS API is offline at ${getConfig().apiBaseUrl}. Starting it automatically.`
          : "MAS API is offline and the MAS runtime is not installed yet.";
        if (this.lastOfflineNotice !== offlineNotice) {
          this.output.appendLine(offlineNotice);
          this.lastOfflineNotice = offlineNotice;
        }
        if (runtimeStatus.pythonExists) {
          void startApiProcess(this.output, this, {
            revealTerminal: false,
            reason: "sidebar auto-start",
          });
        }
      } else {
        this.output.appendLine(`Sidebar health check failed: ${message}`);
        healthStatus = "offline";
      }
    }

    if (healthStatus === "healthy") {
      try {
        tasks = await requestJson<TaskItem[]>("GET", "/api/v1/tasks?limit=8");
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        this.output.appendLine(`Sidebar task refresh failed: ${message}`);
      }
    }

    if (!this.selectedTaskId) {
      this.selectedTaskId = this.history.getActiveTaskId() ?? this.context.globalState.get<string>("masi.lastTaskId");
    }
    if (!this.selectedTaskId && tasks.length > 0) {
      await this.setActiveTask(tasks[0].task_id);
    }
    if (healthStatus === "healthy" && this.selectedTaskId) {
      try {
        selectedTask = await requestJson<TaskItem>("GET", `/api/v1/tasks/${this.selectedTaskId}`);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        this.output.appendLine(`Sidebar task detail refresh failed: ${message}`);
      }
    }

    this.view.webview.html = this.getHtml(healthStatus, tasks, selectedTask, runtimeStatus);
  }

  public async showTask(taskId: string): Promise<void> {
    this.output.show(true);
    try {
      const task = await requestJson<TaskItem>("GET", `/api/v1/tasks/${taskId}`);
      const document = await vscode.workspace.openTextDocument({
        content: renderTaskDocument(task),
        language: "markdown",
      });
      await vscode.window.showTextDocument(document, { preview: false });
      await this.setActiveTask(taskId);
      await this.refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.output.appendLine(`Fetching task ${taskId} failed: ${message}`);
      void vscode.window.showErrorMessage(`MAS task lookup failed: ${message}`);
    }
  }

  public async showInlineResult(result: AppliedEditResult): Promise<void> {
    await this.appendMessage("assistant", result.text, result);
    await this.refresh();
  }

  private async approveRepair(repairId: string): Promise<void> {
    try {
      await requestJson<JsonObject>("POST", `/api/v1/repairs/${repairId}/approve`);
      void vscode.window.showInformationMessage(`MAS approved repair ${repairId}.`);
      await this.refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.output.appendLine(`Repair approval failed for ${repairId}: ${message}`);
      void vscode.window.showErrorMessage(`MAS repair approval failed: ${message}`);
    }
  }

  private async showViolation(taskId: string, violationId: string): Promise<void> {
    try {
      const task = await requestJson<TaskItem>("GET", `/api/v1/tasks/${taskId}`);
      const violation = task.violations.find((item) => item.violation_id === violationId);
      if (!violation) {
        void vscode.window.showErrorMessage(`MAS could not find violation ${violationId}.`);
        return;
      }

      const content = [
        `Violation ID: ${violation.violation_id}`,
        `Rule: ${violation.rule}`,
        `Severity: ${violation.severity}`,
        `File: ${violation.file_path || "(none)"}`,
        "",
        violation.message || "No additional message provided.",
      ].join("\n");
      const document = await vscode.workspace.openTextDocument({
        content,
        language: "markdown",
      });
      await vscode.window.showTextDocument(document, { preview: false });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.output.appendLine(`Violation drill-down failed for ${violationId}: ${message}`);
      void vscode.window.showErrorMessage(`MAS violation lookup failed: ${message}`);
    }
  }

  private startPolling(): void {
    if (this.pollHandle) {
      clearInterval(this.pollHandle);
    }
    this.pollHandle = setInterval(() => {
      void this.refresh();
    }, 15000);
  }

  private loadMessages(taskId?: string): PanelChatMessage[] {
    const stored = this.history.load(taskId);
    return stored.length > 0 ? stored : getDefaultChatMessages();
  }

  private async setActiveTask(taskId?: string): Promise<void> {
    this.selectedTaskId = taskId;
    this.messages = this.loadMessages(taskId);
    await this.context.globalState.update("masi.lastTaskId", taskId);
    await this.history.setActiveTaskId(taskId);
  }

  private async persistMessages(): Promise<void> {
    await this.history.save(this.messages, this.selectedTaskId);
  }

  private async appendMessage(
    role: PanelChatMessage["role"],
    text: string,
    extra: Partial<Omit<PanelChatMessage, "role" | "text">> = {},
  ): Promise<void> {
    this.messages.push({ role, text, ...extra });
    if (this.messages.length > 8) {
      this.messages.splice(1, this.messages.length - 8);
    }
    await this.persistMessages();
  }

  private async runAction(action: Extract<MasiAction, "installRuntime" | "startApi" | "healthCheck" | "analyzeWorkspace" | "configureProvider">): Promise<void> {
    await this.appendMessage("assistant", MAS_ACTIONS[action].reply);
    await vscode.commands.executeCommand(MAS_ACTIONS[action].command);
  }

  private async syncFromHistory(): Promise<void> {
    const nextTaskId = this.history.getActiveTaskId() ?? this.context.globalState.get<string>("masi.lastTaskId");
    if (nextTaskId !== this.selectedTaskId) {
      this.selectedTaskId = nextTaskId;
    }
    this.messages = this.loadMessages(this.selectedTaskId);
    await this.refresh();
  }

  private async handlePrompt(prompt: string): Promise<void> {
    await this.appendMessage("user", prompt);
    try {
      const smartReply = await requestOperatorReply(prompt, this.context, this.output, this);
      if (smartReply) {
        if (smartReply.source_task_id) {
          if (smartReply.source_task_id !== this.selectedTaskId) {
            await this.setActiveTask(smartReply.source_task_id);
            await this.appendMessage("user", prompt, { taskId: smartReply.source_task_id });
          }
        }
        await this.appendMessage("assistant", smartReply.answer, {
          taskId: smartReply.source_task_id,
          summary: smartReply.summary,
          actionsTaken: smartReply.actions_taken ?? [],
          filesInFocus: smartReply.files_in_focus ?? [],
          filesChanged: smartReply.files_changed ?? [],
          codeChanges: smartReply.code_changes ?? [],
          symbolsInFocus: smartReply.symbols_in_focus ?? [],
          suggestions: smartReply.suggestions ?? [],
          nextStep: smartReply.next_step,
          cards: smartReply.cards ?? [],
          followUpActions: smartReply.follow_up_actions ?? [],
        });
        if (smartReply.intent === "action" && smartReply.recommended_action) {
          const action = smartReply.recommended_action as MasiAction;
          if (action === "applyApprovedEdits") {
            const applied = await applyApprovedEditsForPrompt(
              prompt,
              this.context,
              this.output,
              smartReply.files_in_focus ?? [],
            );
            await this.appendMessage("assistant", applied.text, applied);
          } else if (action === "showLastTask") {
            await vscode.commands.executeCommand(MAS_ACTIONS[action].command);
          } else if (action === "installRuntime" || action === "startApi" || action === "healthCheck" || action === "analyzeWorkspace" || action === "configureProvider") {
            await vscode.commands.executeCommand(MAS_ACTIONS[action].command);
          }
        }
        await this.refresh();
        return;
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.output.appendLine(`MAS sidebar chat query failed: ${message}`);
      await this.appendMessage("assistant", `I hit a backend problem while checking MAS state: ${message}`);
      await this.refresh();
      return;
    }

    const action = resolvePromptAction(prompt);
    if (!action || action === "showLastTask" || action === "refreshSidebar" || action === "openSidebar") {
      await this.appendMessage(
        "assistant",
        "I can answer repo/status/task questions, inspect files and symbols, plan edits, apply approved edits, or help with: install runtime, start API, run health check, analyze the current workspace, show the last task, or set up an AI provider.",
      );
      await this.refresh();
      return;
    }

    if (action === "applyApprovedEdits") {
      try {
        const applied = await applyApprovedEditsForPrompt(prompt, this.context, this.output);
        await this.appendMessage("assistant", applied.text, applied);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        await this.appendMessage("assistant", `I could not apply the approved edit yet: ${message}`);
      }
      await this.refresh();
      return;
    }

    await this.runAction(action);
  }

  private getHtml(
    healthStatus: string,
    _tasks: TaskItem[],
    selectedTask: TaskItem | undefined,
    runtimeStatus: RuntimeStatus,
  ): string {
    return renderChatHtml(this.view!.webview, {
      title: "MAS",
      messages: this.messages,
      runtimeStatus,
      healthStatus,
      providerSummary: getAiProviderSummary(this.context),
      selectedTask,
    });
  }
}

class MasiPanel {
  private static currentPanel: MasiPanel | undefined;
  private messages: PanelChatMessage[];
  private selectedTaskId?: string;
  private readonly historySubscription: vscode.Disposable;

  public static refreshVisible(): void {
    MasiPanel.currentPanel?.render();
  }

  public static createOrShow(
    context: vscode.ExtensionContext,
    output: vscode.OutputChannel,
    sidebar: MasiSidebarProvider,
    history: ChatHistoryStore,
  ): void {
    const column = vscode.window.activeTextEditor?.viewColumn ?? vscode.ViewColumn.One;
    if (MasiPanel.currentPanel) {
      MasiPanel.currentPanel.panel.reveal(column);
      MasiPanel.currentPanel.render();
      return;
    }

    const panel = vscode.window.createWebviewPanel(
      "masi.mainPanel",
      "MAS",
      column,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
      },
    );

    MasiPanel.currentPanel = new MasiPanel(panel, context, output, sidebar, history);
  }

  private constructor(
    private readonly panel: vscode.WebviewPanel,
    private readonly context: vscode.ExtensionContext,
    private readonly output: vscode.OutputChannel,
    private readonly sidebar: MasiSidebarProvider,
    private readonly history: ChatHistoryStore,
  ) {
    this.selectedTaskId = this.history.getActiveTaskId() ?? this.context.globalState.get<string>("masi.lastTaskId");
    this.messages = this.loadMessages(this.selectedTaskId);
    this.historySubscription = this.history.subscribe(() => {
      void this.syncFromHistory();
    });
    this.render();
    this.panel.onDidDispose(() => {
      this.historySubscription.dispose();
      MasiPanel.currentPanel = undefined;
    });

    this.panel.webview.onDidReceiveMessage(async (message: JsonObject) => {
      const type = String(message.type ?? "");
      if (type === "prompt") {
        const text = String(message.text ?? "").trim();
        if (text) {
          await this.handlePrompt(text);
        }
      } else if (type) {
        await this.handleAction(type);
      }
    });
  }

  private render(): void {
    this.panel.webview.html = getPanelHtml(
      this.panel.webview,
      this.messages,
      getRuntimeStatus(),
      "unknown",
      getAiProviderSummary(this.context),
    );
  }

  private loadMessages(taskId?: string): PanelChatMessage[] {
    const stored = this.history.load(taskId);
    return stored.length > 0 ? stored : getDefaultChatMessages();
  }

  private async persistMessages(): Promise<void> {
    await this.history.save(this.messages, this.selectedTaskId);
  }

  private async appendMessage(
    role: PanelChatMessage["role"],
    text: string,
    extra: Partial<Omit<PanelChatMessage, "role" | "text">> = {},
  ): Promise<void> {
    this.messages.push({ role, text, ...extra });
    if (this.messages.length > 12) {
      this.messages.splice(1, this.messages.length - 12);
    }
    await this.persistMessages();
    this.render();
  }

  private async setActiveTask(taskId?: string): Promise<void> {
    this.selectedTaskId = taskId;
    this.messages = this.loadMessages(taskId);
    await this.context.globalState.update("masi.lastTaskId", taskId);
    await this.history.setActiveTaskId(taskId);
  }

  private async syncFromHistory(): Promise<void> {
    const nextTaskId = this.history.getActiveTaskId() ?? this.context.globalState.get<string>("masi.lastTaskId");
    this.selectedTaskId = nextTaskId;
    this.messages = this.loadMessages(nextTaskId);
    this.render();
  }

  private async handleAction(action: string): Promise<void> {
    const item = MAS_ACTIONS[action as MasiAction];
    if (!item) {
      await this.appendMessage("assistant", "I do not recognize that MAS action yet.");
      return;
    }

    if (action === "applyApprovedEdits") {
      const prompt = await vscode.window.showInputBox({
        prompt: "What edit should MAS apply?",
        placeHolder: "Example: apply approved edits to src/runtime/chat.py and simplify the summary text",
      });
      if (!prompt) {
        return;
      }
      try {
        const applied = await applyApprovedEditsForPrompt(prompt, this.context, this.output);
        await this.appendMessage("assistant", applied.text, applied);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        await this.appendMessage("assistant", `I could not apply the approved edit yet: ${message}`);
      }
      return;
    }

    await vscode.commands.executeCommand(item.command);
    await this.appendMessage("assistant", item.reply);
  }

  private async handlePrompt(prompt: string): Promise<void> {
    await this.appendMessage("user", prompt);

    try {
      const smartReply = await requestOperatorReply(prompt, this.context, this.output, this.sidebar);
      if (smartReply) {
        if (smartReply.source_task_id) {
          await this.setActiveTask(smartReply.source_task_id);
          await this.appendMessage("user", prompt, { taskId: smartReply.source_task_id });
        }
        await this.appendMessage("assistant", smartReply.answer, {
          taskId: smartReply.source_task_id,
          summary: smartReply.summary,
          actionsTaken: smartReply.actions_taken ?? [],
          filesInFocus: smartReply.files_in_focus ?? [],
          filesChanged: smartReply.files_changed ?? [],
          codeChanges: smartReply.code_changes ?? [],
          symbolsInFocus: smartReply.symbols_in_focus ?? [],
          suggestions: smartReply.suggestions ?? [],
          nextStep: smartReply.next_step,
          cards: smartReply.cards ?? [],
          followUpActions: smartReply.follow_up_actions ?? [],
        });
        if (smartReply.intent === "action" && smartReply.recommended_action) {
          const action = smartReply.recommended_action as MasiAction;
          if (action === "applyApprovedEdits") {
            const applied = await applyApprovedEditsForPrompt(
              prompt,
              this.context,
              this.output,
              smartReply.files_in_focus ?? [],
            );
            await this.appendMessage("assistant", applied.text, applied);
          } else if (MAS_ACTIONS[action]) {
            await vscode.commands.executeCommand(MAS_ACTIONS[action].command);
          }
        }
        return;
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.output.appendLine(`MAS panel chat query failed: ${message}`);
      await this.appendMessage("assistant", `I hit a backend problem while checking MAS state: ${message}`);
      return;
    }

    const action = resolvePromptAction(prompt);
    if (action) {
      await this.handleAction(action);
      return;
    }

    await this.appendMessage(
      "assistant",
      "I can help with: inspect files and symbols, plan edits, apply approved edits, install runtime, start API, run health check, analyze the current workspace, show the last task, open the sidebar, or set up an AI provider.",
    );
  }
}

async function runAnalyzeWorkspace(
  context: vscode.ExtensionContext,
  output: vscode.OutputChannel,
  sidebar: MasiSidebarProvider,
): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder) {
    void vscode.window.showErrorMessage("Open a workspace folder before running MAS analysis.");
    return;
  }

  output.show(true);
  output.appendLine(`Submitting analysis for ${folder.uri.fsPath}`);
  try {
    const apiReady = await ensureApiAvailable(output, sidebar, {
      revealTerminal: false,
      reason: "analysis request",
    });
    if (!apiReady) {
      void vscode.window.showErrorMessage("MAS API is not ready yet. Start the runtime or check the MAS API terminal.");
      return;
    }

    const submit = await requestJson<{ task_id: string }>("POST", "/api/v1/tasks", {
      task_type: "analysis",
      repo_path: folder.uri.fsPath,
      tenant_id: "default",
    });
    await context.globalState.update("masi.lastTaskId", submit.task_id);
    await sidebar.showTask(submit.task_id);
    await sidebar.refresh();
    output.appendLine(`Analysis complete for task ${submit.task_id}`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    output.appendLine(`Analysis failed: ${message}`);
    void vscode.window.showErrorMessage(`MAS analysis failed: ${message}`);
  }
}

async function installRuntime(
  output: vscode.OutputChannel,
  sidebar: MasiSidebarProvider,
): Promise<void> {
  const repoRoot = await ensureRepoRootConfigured();
  if (!repoRoot) {
    return;
  }

  const config = vscode.workspace.getConfiguration("masi");
  const pythonPath = config.get<string>("pythonPath", getDefaultPythonPath(repoRoot));
  const terminal = vscode.window.createTerminal({
    name: "MAS Install",
    cwd: repoRoot,
  });
  terminal.show(true);
  for (const command of buildInstallCommands(repoRoot, pythonPath)) {
    terminal.sendText(command, true);
  }

  output.appendLine(`Started MAS runtime install in ${repoRoot}`);
  void vscode.window.showInformationMessage("MAS runtime install started in the MAS Install terminal.");
  await sidebar.refresh();
}

export function activate(context: vscode.ExtensionContext) {
  const output = createOutputChannel();
  const history = new ChatHistoryStore(context);
  const sidebar = new MasiSidebarProvider(context, output, history);

  const statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusItem.text = "$(hubot) MAS";
  statusItem.command = "masi.openPanel";
  statusItem.tooltip = "Open the MAS control panel";
  statusItem.show();

  context.subscriptions.push(
    output,
    statusItem,
    vscode.window.registerWebviewViewProvider(MasiSidebarProvider.viewType, sidebar),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("masi.openPanel", async () => {
      MasiPanel.createOrShow(context, output, sidebar, history);
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("masi.installRuntime", async () => {
      await installRuntime(output, sidebar);
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("masi.startApi", async () => {
      await startApiProcess(output, sidebar, {
        revealTerminal: true,
        reason: "manual start",
      });
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("masi.healthCheck", async () => {
      output.show(true);
      try {
        const apiReady = await ensureApiAvailable(output, sidebar, {
          revealTerminal: false,
          reason: "health check",
        });
        if (!apiReady) {
          throw new Error("API offline and runtime is not installed yet.");
        }
        const response = await requestJson<JsonObject>("GET", "/health");
        output.appendLine(`Health: ${JSON.stringify(response)}`);
        void vscode.window.showInformationMessage(`MAS health: ${String(response.status ?? "unknown")}`);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        output.appendLine(`Health check failed: ${message}`);
        void vscode.window.showErrorMessage(`MAS health check failed: ${message}`);
      }
      await sidebar.refresh();
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("masi.analyzeWorkspace", async () => {
      await runAnalyzeWorkspace(context, output, sidebar);
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("masi.configureProvider", async () => {
      await configureAiProvider(context, output, sidebar);
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("masi.applyApprovedEdits", async () => {
      const prompt = await vscode.window.showInputBox({
        prompt: "What approved edit should MAS apply?",
        placeHolder: "Example: apply approved edits to src/runtime/chat.py and tighten the status summary wording",
      });
      if (!prompt) {
        return;
      }
      try {
        const applied = await applyApprovedEditsForPrompt(prompt, context, output);
        await sidebar.showInlineResult(applied);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        output.appendLine(`MAS apply-approved-edits failed: ${message}`);
        void vscode.window.showErrorMessage(`MAS could not apply the approved edit: ${message}`);
      }
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("masi.showLastTask", async () => {
      const taskId = context.globalState.get<string>("masi.lastTaskId");
      if (!taskId) {
        void vscode.window.showInformationMessage("No MAS task has been run from this extension yet.");
        return;
      }
      await sidebar.showTask(taskId);
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("masi.refreshSidebar", async () => {
      await sidebar.refresh();
    }),
  );
}

export function deactivate() {
  return undefined;
}
