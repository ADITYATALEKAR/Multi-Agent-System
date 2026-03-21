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
  statusItem.text = "$(hubot) MAS Analyze";
  statusItem.command = "masi.analyzeWorkspace";
  statusItem.tooltip = "Analyze the current workspace with MAS";
  statusItem.show();

  context.subscriptions.push(
    output,
    statusItem,
    vscode.window.registerWebviewViewProvider(MasiSidebarProvider.viewType, sidebar),
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
