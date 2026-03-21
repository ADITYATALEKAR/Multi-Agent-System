import * as fs from "fs";
import * as http from "http";
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
  repoRootExists: boolean;
  pythonExists: boolean;
}

interface PanelChatMessage {
  role: "assistant" | "user";
  text: string;
}

function getConfig() {
  const config = vscode.workspace.getConfiguration("masi");
  return {
    apiBaseUrl: config.get<string>("apiBaseUrl", "http://127.0.0.1:8000"),
    repoRoot: config.get<string>("repoRoot", ""),
    pythonPath: config.get<string>("pythonPath", ""),
  };
}

function getRuntimeStatus(): RuntimeStatus {
  const { repoRoot, pythonPath } = getConfig();
  return {
    repoRootExists: fs.existsSync(repoRoot),
    pythonExists: fs.existsSync(pythonPath),
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

async function ensureRepoRootConfigured(): Promise<string | undefined> {
  const config = vscode.workspace.getConfiguration("masi");
  const configuredRepoRoot = config.get<string>("repoRoot", "");
  if (configuredRepoRoot && fs.existsSync(configuredRepoRoot)) {
    return configuredRepoRoot;
  }

  const selectedFolder = await vscode.window.showOpenDialog({
    canSelectFiles: false,
    canSelectFolders: true,
    canSelectMany: false,
    defaultUri: vscode.workspace.workspaceFolders?.[0]?.uri,
    openLabel: "Select MAS Repository",
    title: "Select the MAS repository root",
  });
  const repoRoot = selectedFolder?.[0]?.fsPath;
  if (!repoRoot) {
    void vscode.window.showErrorMessage("MAS needs a repository folder before it can install or start.");
    return undefined;
  }

  await config.update("repoRoot", repoRoot, vscode.ConfigurationTarget.Global);
  await config.update("pythonPath", getDefaultPythonPath(repoRoot), vscode.ConfigurationTarget.Global);
  return repoRoot;
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

function getPanelHtml(webview: vscode.Webview, messages: PanelChatMessage[]): string {
  const nonce = String(Date.now());
  const logoUri = webview.asWebviewUri(
    vscode.Uri.joinPath(vscode.Uri.file(__dirname), "..", "media", "mas-icon.png"),
  );
  const transcript = messages.map((message) => `
    <div class="message ${message.role}">
      <div class="message-role">${message.role === "assistant" ? "MAS" : "You"}</div>
      <div class="message-body">${escapeHtml(message.text)}</div>
    </div>
  `).join("");
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${webview.cspSource} https: data:; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>MAS</title>
  <style>
    :root {
      --bg: #111315;
      --bg-2: #171b20;
      --panel: rgba(26, 32, 39, 0.88);
      --panel-2: rgba(18, 23, 29, 0.92);
      --text: #f3f7fc;
      --muted: #9fb0c5;
      --accent: #4eb2ff;
      --accent-2: #0d8bff;
      --border: rgba(98, 128, 164, 0.22);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top center, rgba(78, 178, 255, 0.14), transparent 24%),
        radial-gradient(circle at bottom center, rgba(13, 139, 255, 0.09), transparent 20%),
        linear-gradient(180deg, var(--bg) 0%, #0d0f12 100%);
      display: flex;
      flex-direction: column;
    }

    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 18px 24px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    }

    .title {
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 15px;
      font-weight: 600;
    }

    .title img {
      width: 28px;
      height: 28px;
      border-radius: 8px;
    }

    .actions-top {
      display: flex;
      gap: 10px;
    }

    .shell {
      flex: 1;
      width: min(980px, 100%);
      margin: 0 auto;
      padding: 24px 24px 20px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }

    .hero {
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      gap: 10px;
      padding-top: 18px;
    }

    .hero-logo {
      width: 84px;
      height: 84px;
      border-radius: 22px;
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
    }

    h1 {
      margin: 0;
      font-size: 44px;
      font-weight: 700;
      letter-spacing: 0.02em;
    }

    .subtitle {
      max-width: 720px;
      margin: 0;
      font-size: 18px;
      line-height: 1.6;
      color: var(--muted);
    }

    .conversation-shell {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-height: 380px;
      border-radius: 24px;
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(20, 25, 31, 0.94), rgba(14, 18, 23, 0.98));
      overflow: hidden;
      box-shadow: 0 22px 50px rgba(0, 0, 0, 0.24);
    }

    .messages {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 14px;
      padding: 24px;
      overflow-y: auto;
    }

    .message {
      max-width: 760px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      padding: 14px 16px;
      border-radius: 18px;
      border: 1px solid var(--border);
      background: rgba(24, 31, 38, 0.95);
    }

    .message.user {
      align-self: flex-end;
      background: linear-gradient(180deg, rgba(78, 178, 255, 0.16), rgba(26, 47, 71, 0.94));
      border-color: rgba(78, 178, 255, 0.35);
    }

    .message-role {
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }

    .message-body {
      font-size: 15px;
      line-height: 1.6;
      white-space: pre-wrap;
    }

    .quick-actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      padding: 0 24px 18px;
    }

    .quick-chip {
      border: 1px solid var(--border);
      background: rgba(24, 31, 38, 0.95);
      color: var(--text);
      border-radius: 999px;
      padding: 10px 14px;
      cursor: pointer;
      font-size: 13px;
      transition: transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease;
    }

    .quick-chip:hover, .mini-button:hover, .prompt-button:hover {
      transform: translateY(-2px);
      border-color: rgba(78, 178, 255, 0.75);
      box-shadow: 0 14px 30px rgba(0, 0, 0, 0.22);
    }

    .composer {
      margin-top: auto;
      border-top: 1px solid rgba(255, 255, 255, 0.06);
      padding: 18px 24px 24px;
      background: rgba(13, 17, 22, 0.94);
    }

    .composer-box {
      border-radius: 22px;
      border: 1px solid rgba(78, 178, 255, 0.35);
      background: linear-gradient(180deg, rgba(29, 34, 41, 0.96), rgba(18, 22, 27, 0.98));
      box-shadow: 0 0 0 1px rgba(78, 178, 255, 0.08) inset;
      padding: 16px;
    }

    .composer-input {
      width: 100%;
      min-height: 88px;
      resize: vertical;
      border: none;
      outline: none;
      background: transparent;
      color: #d9e4f2;
      font: inherit;
      font-size: 16px;
      line-height: 1.5;
    }

    .composer-input::placeholder {
      color: #8698ae;
    }

    .composer-actions {
      margin-top: 12px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      flex-wrap: wrap;
    }

    .composer-hint {
      color: var(--muted);
      font-size: 13px;
    }

    .mini-button, .prompt-button {
      border: 1px solid var(--border);
      background: rgba(24, 31, 38, 0.95);
      color: var(--text);
      border-radius: 14px;
      padding: 10px 14px;
      cursor: pointer;
      font-size: 13px;
      transition: transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease;
    }

    .prompt-button.primary {
      background: linear-gradient(180deg, var(--accent), var(--accent-2));
      border-color: rgba(78, 178, 255, 0.95);
      color: white;
      font-weight: 600;
    }

    .hint {
      color: var(--muted);
      font-size: 13px;
    }
  </style>
</head>
<body>
  <div class="topbar">
    <div class="title">
      <img src="${logoUri}" alt="MAS" />
      <span>MAS Control Panel</span>
    </div>
    <div class="actions-top">
      <button class="mini-button" data-action="openSidebar">Open Sidebar</button>
      <button class="mini-button" data-action="refreshSidebar">Refresh</button>
    </div>
  </div>

  <div class="shell">
    <div class="hero">
      <img class="hero-logo" src="${logoUri}" alt="MAS" />
      <h1>MAS</h1>
      <p class="subtitle">Chat with MAS to install the runtime, start the API, run analysis, and inspect the latest task from a full editor panel.</p>
    </div>

    <div class="conversation-shell">
      <div class="messages">${transcript}</div>
      <div class="quick-actions">
        <button class="quick-chip" data-action="installRuntime">Install Runtime</button>
        <button class="quick-chip" data-action="startApi">Start API</button>
        <button class="quick-chip" data-action="healthCheck">Health Check</button>
        <button class="quick-chip" data-action="analyzeWorkspace">Analyze Workspace</button>
        <button class="quick-chip" data-action="showLastTask">Show Last Task</button>
      </div>
      <div class="composer">
        <div class="composer-box">
          <textarea id="promptInput" class="composer-input" placeholder="Ask MAS to install runtime, start the API, run health check, analyze this workspace, or show the last task..."></textarea>
          <div class="composer-actions">
            <div class="composer-hint">Press Enter to send. Use Shift+Enter for a new line.</div>
            <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
              <button class="mini-button" data-action="openSidebar">Open Sidebar</button>
              <button class="prompt-button primary" id="sendPrompt">Run MAS</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    document.querySelectorAll("[data-action]").forEach((element) => {
      element.addEventListener("click", () => {
        vscode.postMessage({ type: element.getAttribute("data-action") });
      });
    });
    const promptInput = document.getElementById("promptInput");
    const sendPrompt = document.getElementById("sendPrompt");
    function submitPrompt() {
      const text = promptInput.value.trim();
      if (!text) {
        return;
      }
      vscode.postMessage({ type: "prompt", text });
      promptInput.value = "";
    }
    sendPrompt.addEventListener("click", submitPrompt);
    promptInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        submitPrompt();
      }
    });
  </script>
</body>
</html>`;
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

class MasiSidebarProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = "masi.sidebar";

  private view?: vscode.WebviewView;
  private selectedTaskId?: string;
  private pollHandle?: NodeJS.Timeout;

  public constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly output: vscode.OutputChannel,
  ) {}

  public resolveWebviewView(webviewView: vscode.WebviewView): void | Thenable<void> {
    this.view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
    };

    webviewView.webview.onDidReceiveMessage(async (message: JsonObject) => {
      const type = String(message.type ?? "");
      if (type === "installRuntime") {
        await vscode.commands.executeCommand("masi.installRuntime");
      } else if (type === "startApi") {
        await vscode.commands.executeCommand("masi.startApi");
      } else if (type === "healthCheck") {
        await vscode.commands.executeCommand("masi.healthCheck");
      } else if (type === "analyzeWorkspace") {
        await vscode.commands.executeCommand("masi.analyzeWorkspace");
      } else if (type === "refresh") {
        await this.refresh();
      } else if (type === "selectTask") {
        const taskId = String(message.taskId ?? "");
        if (taskId) {
          this.selectedTaskId = taskId;
          await this.context.globalState.update("masi.lastTaskId", taskId);
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

    let healthStatus = "unknown";
    let tasks: TaskItem[] = [];
    let selectedTask: TaskItem | undefined;
    const runtimeStatus = getRuntimeStatus();

    try {
      const health = await requestJson<JsonObject>("GET", "/health");
      healthStatus = String(health.status ?? "unknown");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.output.appendLine(`Sidebar health check failed: ${message}`);
      healthStatus = "offline";
    }

    try {
      tasks = await requestJson<TaskItem[]>("GET", "/api/v1/tasks?limit=8");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.output.appendLine(`Sidebar task refresh failed: ${message}`);
    }

    if (!this.selectedTaskId) {
      this.selectedTaskId = this.context.globalState.get<string>("masi.lastTaskId");
    }
    if (!this.selectedTaskId && tasks.length > 0) {
      this.selectedTaskId = tasks[0].task_id;
    }
    if (this.selectedTaskId) {
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
      this.selectedTaskId = taskId;
      await this.context.globalState.update("masi.lastTaskId", taskId);
      await this.refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.output.appendLine(`Fetching task ${taskId} failed: ${message}`);
      void vscode.window.showErrorMessage(`MAS task lookup failed: ${message}`);
    }
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

  private getHtml(
    healthStatus: string,
    tasks: TaskItem[],
    selectedTask: TaskItem | undefined,
    runtimeStatus: RuntimeStatus,
  ): string {
    const nonce = String(Date.now());
    const runtimeLabel = runtimeStatus.pythonExists
      ? "runtime ready"
      : runtimeStatus.repoRootExists ? "runtime missing" : "repo not set";
    const installButtonClass = runtimeStatus.pythonExists ? "action" : "action primary";
    const taskCards = tasks.length > 0
      ? tasks.map((task) => {
          const result = task.result ?? {};
          const selectedClass = task.task_id === selectedTask?.task_id ? " selected" : "";
          return `
            <button class="task-card${selectedClass}" data-task-id="${escapeHtml(task.task_id)}">
              <span class="task-id">${escapeHtml(task.task_id)}</span>
              <span class="task-status">${escapeHtml(task.status)}</span>
              <span class="task-repo">${escapeHtml(task.repo_path)}</span>
              <span class="task-meta">violations ${escapeHtml(String(result.violations_found ?? task.violations.length))} | repairs ${escapeHtml(String(result.repairs_proposed ?? task.repairs.length))}</span>
            </button>
          `;
        }).join("")
      : `<div class="empty">No MAS tasks yet. Run an analysis to populate this view.</div>`;

    const selectedTaskMarkup = selectedTask
      ? this.renderSelectedTask(selectedTask)
      : `<div class="empty">Select a task to inspect violations, repairs, and hypotheses.</div>`;

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>
    :root {
      --bg: #0f1722;
      --panel: #162131;
      --panel-alt: #1c2b40;
      --panel-soft: #23354e;
      --text: #edf3ff;
      --muted: #9fb0c8;
      --accent: #4eb2ff;
      --accent-strong: #0d8bff;
      --border: #2b3e58;
      --success: #3ddc97;
      --warn: #ffb454;
      --danger: #ff6b6b;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      padding: 14px;
      font-family: "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top right, rgba(78, 178, 255, 0.16), transparent 34%),
        linear-gradient(180deg, #101722 0%, #0f1722 100%);
      color: var(--text);
    }

    .shell { display: flex; flex-direction: column; gap: 14px; }
    .hero, .task-panel, .detail-panel {
      border: 1px solid var(--border);
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(22, 33, 49, 0.98), rgba(15, 23, 34, 0.94));
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.18);
    }

    .hero {
      padding: 14px;
    }

    .hero h2, .detail-panel h3, .task-panel h3 {
      margin: 0 0 4px;
      font-size: 14px;
      font-weight: 700;
      letter-spacing: 0.02em;
    }

    .hero p, .tiny, .meta-line {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      margin: 0;
    }

    .status-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid rgba(159, 176, 200, 0.15);
    }

    .health-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(28, 43, 64, 0.9);
      border: 1px solid var(--border);
      font-size: 12px;
    }

    .health-pill::before {
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: ${healthStatus === "healthy" ? "var(--success)" : healthStatus === "offline" ? "var(--danger)" : "var(--warn)"};
      box-shadow: 0 0 10px ${healthStatus === "healthy" ? "rgba(61, 220, 151, 0.8)" : healthStatus === "offline" ? "rgba(255, 107, 107, 0.8)" : "rgba(255, 180, 84, 0.8)"};
    }

    .health-pill.runtime::before {
      background: ${runtimeStatus.pythonExists ? "var(--success)" : runtimeStatus.repoRootExists ? "var(--warn)" : "var(--danger)"};
      box-shadow: 0 0 10px ${runtimeStatus.pythonExists ? "rgba(61, 220, 151, 0.8)" : runtimeStatus.repoRootExists ? "rgba(255, 180, 84, 0.8)" : "rgba(255, 107, 107, 0.8)"};
    }

    .actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }

    .action, .task-card, .pill-button, .action-inline {
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(28, 43, 64, 0.98), rgba(17, 27, 40, 0.98));
      color: var(--text);
      cursor: pointer;
      transition: transform 0.14s ease, border-color 0.14s ease, background 0.14s ease;
    }

    .action {
      padding: 11px 12px;
      border-radius: 14px;
      text-align: left;
      font-size: 12px;
      line-height: 1.4;
    }

    .action strong {
      display: block;
      margin-bottom: 3px;
      font-size: 12px;
    }

    .action:hover, .task-card:hover, .pill-button:hover, .action-inline:hover {
      transform: translateY(-1px);
      border-color: var(--accent);
    }

    .action.primary {
      background: linear-gradient(180deg, rgba(13, 139, 255, 0.95), rgba(8, 100, 190, 0.98));
      border-color: rgba(78, 178, 255, 0.8);
    }

    .task-panel header, .detail-panel header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 14px;
      border-bottom: 1px solid rgba(159, 176, 200, 0.14);
    }

    .task-list, .detail-body {
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 12px;
    }

    .task-card {
      width: 100%;
      border-radius: 14px;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 4px;
      text-align: left;
    }

    .task-card.selected {
      border-color: var(--accent);
      background: linear-gradient(180deg, rgba(37, 63, 96, 0.98), rgba(20, 33, 48, 0.98));
    }

    .task-id {
      font-weight: 700;
      font-size: 12px;
    }

    .task-status, .task-meta, .task-repo {
      color: var(--muted);
      font-size: 11px;
    }

    .summary-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
    }

    .summary-card {
      border: 1px solid rgba(159, 176, 200, 0.12);
      border-radius: 14px;
      padding: 10px;
      background: var(--panel-soft);
    }

    .summary-card strong {
      display: block;
      font-size: 16px;
    }

    .section {
      border: 1px solid rgba(159, 176, 200, 0.1);
      border-radius: 14px;
      padding: 10px;
      background: rgba(19, 30, 45, 0.9);
    }

    .section h4 {
      margin: 0 0 8px;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
    }

    .item-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .item-card {
      border: 1px solid rgba(159, 176, 200, 0.1);
      border-radius: 12px;
      padding: 10px;
      background: var(--panel-alt);
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .item-card strong {
      font-size: 12px;
    }

    .item-card p {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }

    .actions-row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }

    .pill-button, .action-inline {
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 11px;
    }

    .pill-button.approve {
      background: linear-gradient(180deg, rgba(61, 220, 151, 0.28), rgba(17, 73, 55, 0.9));
    }

    .severity {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
    }

    .severity::before {
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--warn);
    }

    .severity.high::before { background: var(--danger); }
    .severity.medium::before { background: var(--warn); }
    .severity.low::before { background: var(--success); }

    .empty {
      padding: 12px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <h2>MAS Control Panel</h2>
      <p>Install the runtime, launch the API, and run your multi-agent workflows without leaving the editor.</p>
      <div class="status-row">
        <span class="health-pill">API ${escapeHtml(healthStatus)}</span>
        <span class="health-pill runtime">Runtime ${escapeHtml(runtimeLabel)}</span>
        <span class="tiny">Polling every 15s</span>
      </div>
    </section>

    <section class="actions">
      <button class="${installButtonClass}" data-action="installRuntime"><strong>Install Runtime</strong>Set up the MAS Python environment in the configured repo.</button>
      <button class="action" data-action="startApi"><strong>Start API</strong>Launch the MAS FastAPI service in a terminal.</button>
      <button class="action" data-action="healthCheck"><strong>Health Check</strong>Ping the runtime and confirm availability.</button>
      <button class="action primary" data-action="analyzeWorkspace"><strong>Analyze Workspace</strong>Run the full MAS pipeline for the open folder.</button>
      <button class="action" data-action="refresh"><strong>Refresh Now</strong>Force a poll instead of waiting for the timer.</button>
    </section>

    <section class="task-panel">
      <header>
        <h3>Recent Tasks</h3>
        <span class="tiny">${tasks.length} loaded</span>
      </header>
      <div class="task-list">
        ${taskCards}
      </div>
    </section>

    <section class="detail-panel">
      <header>
        <h3>Task Detail</h3>
        <span class="tiny">${escapeHtml(selectedTask?.task_id ?? "none selected")}</span>
      </header>
      <div class="detail-body">
        ${selectedTaskMarkup}
      </div>
    </section>
  </div>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    document.querySelectorAll("[data-action]").forEach((element) => {
      element.addEventListener("click", () => {
        vscode.postMessage({ type: element.getAttribute("data-action") });
      });
    });
    document.querySelectorAll("[data-task-id]").forEach((element) => {
      element.addEventListener("click", () => {
        vscode.postMessage({ type: "selectTask", taskId: element.getAttribute("data-task-id") });
      });
    });
    document.querySelectorAll("[data-open-task]").forEach((element) => {
      element.addEventListener("click", () => {
        vscode.postMessage({ type: "openTaskDocument", taskId: element.getAttribute("data-open-task") });
      });
    });
    document.querySelectorAll("[data-approve-repair]").forEach((element) => {
      element.addEventListener("click", () => {
        vscode.postMessage({ type: "approveRepair", repairId: element.getAttribute("data-approve-repair") });
      });
    });
    document.querySelectorAll("[data-show-violation]").forEach((element) => {
      element.addEventListener("click", () => {
        vscode.postMessage({
          type: "showViolation",
          taskId: element.getAttribute("data-task"),
          violationId: element.getAttribute("data-show-violation"),
        });
      });
    });
  </script>
</body>
</html>`;
  }

  private renderSelectedTask(task: TaskItem): string {
    const result = task.result ?? {};
    const repairs = task.repairs.length > 0
      ? task.repairs.map((repair) => {
          const approveButton = repair.status === "proposed"
            ? `<button class="pill-button approve" data-approve-repair="${escapeHtml(repair.repair_id)}">Approve repair</button>`
            : "";
          return `
            <div class="item-card">
              <strong>${escapeHtml(repair.repair_id)}</strong>
              <p>${escapeHtml(repair.description)}</p>
              <p class="meta-line">Rule: ${escapeHtml(repair.rule || "n/a")} | Status: ${escapeHtml(repair.status)}</p>
              <div class="actions-row">${approveButton}</div>
            </div>
          `;
        }).join("")
      : `<div class="empty">No repair candidates for this task.</div>`;

    const violations = task.violations.length > 0
      ? task.violations.map((violation) => `
          <div class="item-card">
            <div class="severity ${escapeHtml(violation.severity.toLowerCase())}">${escapeHtml(violation.severity)}</div>
            <strong>${escapeHtml(violation.rule)}</strong>
            <p>${escapeHtml(violation.message)}</p>
            <p class="meta-line">${escapeHtml(violation.file_path || "(no file path)")}</p>
            <div class="actions-row">
              <button class="action-inline" data-task="${escapeHtml(task.task_id)}" data-show-violation="${escapeHtml(violation.violation_id)}">Open detail</button>
            </div>
          </div>
        `).join("")
      : `<div class="empty">No violations recorded for this task.</div>`;

    const hypotheses = task.hypotheses.length > 0
      ? task.hypotheses.map((hypothesis) => `
          <div class="item-card">
            <strong>${escapeHtml(hypothesis.title)}</strong>
            <p>${escapeHtml(hypothesis.summary)}</p>
          </div>
        `).join("")
      : `<div class="empty">No hypotheses generated for this task.</div>`;

    return `
      <div class="summary-grid">
        <div class="summary-card">
          <strong>${escapeHtml(String(result.violations_found ?? task.violations.length))}</strong>
          <span class="tiny">Violations</span>
        </div>
        <div class="summary-card">
          <strong>${escapeHtml(String(result.hypotheses_generated ?? task.hypotheses.length))}</strong>
          <span class="tiny">Hypotheses</span>
        </div>
        <div class="summary-card">
          <strong>${escapeHtml(String(result.repairs_proposed ?? task.repairs.length))}</strong>
          <span class="tiny">Repairs</span>
        </div>
      </div>

      <div class="section">
        <h4>Overview</h4>
        <p class="meta-line">Repo: ${escapeHtml(task.repo_path)}</p>
        <p class="meta-line">Created: ${escapeHtml(task.created_at)}</p>
        <p class="meta-line">Status: ${escapeHtml(task.status)}</p>
        <div class="actions-row" style="margin-top:8px;">
          <button class="action-inline" data-open-task="${escapeHtml(task.task_id)}">Open Markdown Report</button>
        </div>
      </div>

      <div class="section">
        <h4>Violations</h4>
        <div class="item-list">${violations}</div>
      </div>

      <div class="section">
        <h4>Repair Actions</h4>
        <div class="item-list">${repairs}</div>
      </div>

      <div class="section">
        <h4>Hypotheses</h4>
        <div class="item-list">${hypotheses}</div>
      </div>
    `;
  }
}

class MasiPanel {
  private static currentPanel: MasiPanel | undefined;
  private readonly messages: PanelChatMessage[] = [
    {
      role: "assistant",
      text: "You made it to the MAS control panel. Ask me to install the runtime, start the API, run a health check, analyze this workspace, or show the last task.",
    },
  ];

  public static createOrShow(
    context: vscode.ExtensionContext,
    output: vscode.OutputChannel,
    sidebar: MasiSidebarProvider,
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

    MasiPanel.currentPanel = new MasiPanel(panel, context, output, sidebar);
  }

  private constructor(
    private readonly panel: vscode.WebviewPanel,
    private readonly context: vscode.ExtensionContext,
    private readonly output: vscode.OutputChannel,
    private readonly sidebar: MasiSidebarProvider,
  ) {
    this.render();
    this.panel.onDidDispose(() => {
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
    this.panel.webview.html = getPanelHtml(this.panel.webview, this.messages);
  }

  private appendMessage(role: PanelChatMessage["role"], text: string): void {
    this.messages.push({ role, text });
    this.render();
  }

  private async handleAction(action: string): Promise<void> {
    const actionMap: Record<string, { command: string; reply: string }> = {
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
      refreshSidebar: {
        command: "masi.refreshSidebar",
        reply: "Refreshing the MAS sidebar.",
      },
      openSidebar: {
        command: "workbench.view.extension.masi",
        reply: "Opening the MAS sidebar.",
      },
    };

    const item = actionMap[action];
    if (!item) {
      this.appendMessage("assistant", "I do not recognize that MAS action yet.");
      return;
    }

    await vscode.commands.executeCommand(item.command);
    this.appendMessage("assistant", item.reply);
  }

  private async handlePrompt(prompt: string): Promise<void> {
    const normalized = prompt.toLowerCase();
    this.appendMessage("user", prompt);

    if (normalized.includes("install") || normalized.includes("setup") || normalized.includes("runtime")) {
      await this.handleAction("installRuntime");
      return;
    }
    if ((normalized.includes("start") || normalized.includes("launch")) && normalized.includes("api")) {
      await this.handleAction("startApi");
      return;
    }
    if (normalized.includes("health") || normalized.includes("status") || normalized.includes("ping")) {
      await this.handleAction("healthCheck");
      return;
    }
    if (normalized.includes("analyze") || normalized.includes("scan") || normalized.includes("inspect workspace")) {
      await this.handleAction("analyzeWorkspace");
      return;
    }
    if (normalized.includes("last task") || normalized.includes("latest task") || normalized.includes("show task")) {
      await this.handleAction("showLastTask");
      return;
    }
    if (normalized.includes("sidebar")) {
      await this.handleAction("openSidebar");
      return;
    }

    this.appendMessage(
      "assistant",
      "I can help with: install runtime, start API, run health check, analyze the current workspace, show the last task, or open the sidebar.",
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
  const sidebar = new MasiSidebarProvider(context, output);

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
      MasiPanel.createOrShow(context, output, sidebar);
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("masi.installRuntime", async () => {
      await installRuntime(output, sidebar);
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("masi.startApi", async () => {
      const repoRoot = await ensureRepoRootConfigured();
      if (!repoRoot) {
        return;
      }

      const { pythonPath } = getConfig();
      const terminal = vscode.window.createTerminal({
        name: "MAS API",
        cwd: repoRoot,
      });
      terminal.show(true);
      terminal.sendText(`"${pythonPath}" -m uvicorn src.api.app:create_app --factory --host 127.0.0.1 --port 8000`);
      output.appendLine(`Started MAS API terminal in ${repoRoot}`);
      await sidebar.refresh();
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("masi.healthCheck", async () => {
      output.show(true);
      try {
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
