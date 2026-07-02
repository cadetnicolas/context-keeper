import * as vscode from "vscode";
import * as cp from "child_process";
import * as path from "path";
import * as fs from "fs";
import * as os from "os";
import * as http from "http";

// ─────────────────────────── 全局状态 ───────────────────────────
let statusBarItem: vscode.StatusBarItem;
let serverProcess: cp.ChildProcess | null = null;
let outputChannel: vscode.OutputChannel;

const EXT_ROOT          = path.resolve(__dirname, "..", "..");
const BACKEND_DIR       = path.join(EXT_ROOT, "backend");
const PYTHON_REQUIREMENTS = path.join(BACKEND_DIR, "requirements.txt");
const VENV_DIR          = path.join(os.homedir(), ".context-keeper", "ck-env");
const DB_DIR            = path.join(os.homedir(), ".context-keeper");
// 标记文件：记录已完成安装的 requirements hash，避免重复 pip install
const INSTALL_STAMP     = path.join(DB_DIR, ".install-stamp");

// ─────────────────────────── 激活入口 ───────────────────────────
export async function activate(context: vscode.ExtensionContext) {
  outputChannel = vscode.window.createOutputChannel("ContextKeeper");
  context.subscriptions.push(outputChannel);

  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBarItem.command = "contextkeeper.showStatus";
  statusBarItem.text    = "$(sync~spin) ContextKeeper";
  statusBarItem.tooltip = "ContextKeeper — Team Memory for AI Agents";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  context.subscriptions.push(
    vscode.commands.registerCommand("contextkeeper.openDashboard", openDashboard),
    vscode.commands.registerCommand("contextkeeper.restartServer", () => restartServer(context)),
    vscode.commands.registerCommand("contextkeeper.showStatus",   showStatus),
    vscode.commands.registerCommand("contextkeeper.setupMCP",     () => setupMCP(context))
  );

  const config = vscode.workspace.getConfiguration("contextkeeper");
  if (config.get<boolean>("autoStart", true)) {
    startContextKeeper(context).catch((err) => log(`Startup error: ${err}`));
  }
}

export function deactivate() { stopServer(); }

// ─────────────────────────── 核心启动流程 ───────────────────────────
async function startContextKeeper(context: vscode.ExtensionContext) {
  const isFirstInstall = !fs.existsSync(getPythonBin());

  // 首次安装：自动打开日志面板，让用户看到进度
  if (isFirstInstall) {
    outputChannel.show(true);
  }

  try {
    await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title:    "ContextKeeper",
        cancellable: false,
      },
      async (progress) => {
        fs.mkdirSync(DB_DIR, { recursive: true });

        // ── Step 1/4：创建 Python 虚拟环境 ──────────────────────────
        progress.report({ message: "步骤 1/4  创建 Python 虚拟环境…", increment: 0 });
        setStatus("$(sync~spin) ContextKeeper: 创建虚拟环境…");
        await ensureVenv();
        progress.report({ increment: 15 });

        // ── Step 2/4：安装 Python 依赖 ───────────────────────────────
        const needInstall = isFirstInstall || !isInstallUpToDate();
        if (needInstall) {
          const mirror = resolvePipMirror();
          const mHost = mirror ? (() => { try { return new URL(mirror).hostname; } catch { return mirror; } })() : null;
          const mNote = mHost ? ` — ${mHost}` : " — PyPI";
          progress.report({
            message:   `步骤 2/4  安装依赖包${isFirstInstall ? `（首次${mNote}）` : ""}…`,
            increment: 0,
          });
          setStatus("$(sync~spin) ContextKeeper: 安装依赖…");
          log(mHost
            ? `首次安装：使用 ${mirror} 加速下载（onnxruntime、fastapi 等）…`
            : `首次安装：从 PyPI 下载依赖。如在中国大陆可设置 contextkeeper.pipMirror 加速。`);
          await installDependencies(progress);
          markInstallDone();
        } else {
          log("依赖已是最新，跳过安装。");
          progress.report({ increment: 50 });
        }

        // ── Step 3/4：启动服务 ───────────────────────────────────────
        progress.report({ message: "步骤 3/4  启动 ContextKeeper 服务…", increment: 0 });
        setStatus("$(sync~spin) ContextKeeper: 启动服务…");
        await startServer(context);
        progress.report({ increment: 25 });

        // ── Step 4/4：注册 MCP ───────────────────────────────────────
        progress.report({ message: "步骤 4/4  写入 MCP 配置…", increment: 0 });
        await setupMCP(context, true);
        progress.report({ increment: 10 });
      }
    );

    setStatus("$(check) ContextKeeper", "ContextKeeper 运行中 — 点击查看详情");
    log("ContextKeeper is ready.");

    const port = getPort();
    const action = await vscode.window.showInformationMessage(
      "✅ ContextKeeper 已就绪！AI Agent 现在可以调用 contextkeeper_recall 和 contextkeeper_remember。",
      "打开 Dashboard"
    );
    if (action === "打开 Dashboard") {
      vscode.env.openExternal(vscode.Uri.parse(`http://127.0.0.1:${port}/static/index.html`));
    }
  } catch (err: any) {
    setStatus("$(error) ContextKeeper", `启动失败: ${err?.message}`);
    log(`Error: ${err?.message}`);
    const sel = await vscode.window.showErrorMessage(
      `ContextKeeper 启动失败: ${err?.message}`,
      "查看日志",
      "重试"
    );
    if (sel === "查看日志") outputChannel.show();
    if (sel === "重试") startContextKeeper(context);
  }
}

// ─────────────────────────── 安装检查（跳过重复 pip install）───────────────────────────
function requirementsHash(): string {
  try { return fs.readFileSync(PYTHON_REQUIREMENTS, "utf-8"); } catch { return ""; }
}

function isInstallUpToDate(): boolean {
  try {
    const stamp = JSON.parse(fs.readFileSync(INSTALL_STAMP, "utf-8"));
    return stamp.hash === requirementsHash() && fs.existsSync(getPythonBin());
  } catch { return false; }
}

function markInstallDone() {
  fs.writeFileSync(INSTALL_STAMP, JSON.stringify({ hash: requirementsHash(), ts: Date.now() }));
}

// ─────────────────────────── Python 环境 ───────────────────────────
function getPythonBin(): string {
  return process.platform === "win32"
    ? path.join(VENV_DIR, "Scripts", "python.exe")
    : path.join(VENV_DIR, "bin", "python3");
}

async function ensureVenv(): Promise<void> {
  if (fs.existsSync(getPythonBin())) {
    log("Virtual environment (ck-env) found.");
    return;
  }
  const python = await findSystemPython();
  if (fs.existsSync(VENV_DIR)) {
    log("ck-env directory exists but binary missing — recreating with --clear…");
    await runCommand(python, ["-m", "venv", "--clear", VENV_DIR], os.homedir());
  } else {
    log(`Creating ck-env at ${VENV_DIR}…`);
    await runCommand(python, ["-m", "venv", VENV_DIR], os.homedir());
  }
  log("ck-env ready.");
}

async function findSystemPython(): Promise<string> {
  for (const candidate of ["python3", "python3.12", "python3.11", "python3.10", "python3.9", "python"]) {
    try {
      const result = await runCommandOutput(candidate, ["--version"]);
      if (result.includes("Python 3")) { log(`System Python: ${candidate}`); return candidate; }
    } catch { /* try next */ }
  }
  throw new Error("Python 3.9+ not found. Install from https://python.org");
}

// ─────────────────────────── 镜像源检测 ───────────────────────────
/**
 * 根据系统时区判断是否在中国大陆/港台地区
 * 优先用清华源（每秒 30-50MB，比 PyPI 快 20-50 倍）
 */
function isChineseTimezone(): boolean {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return [
      "Asia/Shanghai", "Asia/Chongqing", "Asia/Harbin",
      "Asia/Urumqi",   "Asia/Taipei",    "Asia/Hong_Kong",
      "Asia/Macau",
    ].some((z) => tz === z || tz.startsWith(z));
  } catch { return false; }
}

/** 返回 pip 应使用的镜像 URL（undefined = 使用 pip 默认 PyPI）*/
function resolvePipMirror(): string | undefined {
  const cfg = vscode.workspace.getConfiguration("contextkeeper");
  const setting = cfg.get<string>("pipMirror", "auto").trim();

  if (setting === "off" || setting === "") return undefined;
  if (setting !== "auto") return setting;   // 用户手动指定

  // auto: 按时区自动选择
  return isChineseTimezone()
    ? "https://pypi.tuna.tsinghua.edu.cn/simple"
    : undefined;
}

// 安装依赖，同时实时推送进度（解析 pip 输出）
async function installDependencies(
  progress: vscode.Progress<{ message?: string; increment?: number }>
): Promise<void> {
  const pip    = getPythonBin();
  const mirror = resolvePipMirror();

  const args: string[] = [
    "-m", "pip", "install",
    "-r", PYTHON_REQUIREMENTS,
    "--progress-bar", "off",
  ];

  if (mirror) {
    // 主镜像 + 备用镜像（pip 会按顺序尝试）
    args.push("-i", mirror);
    try {
      args.push("--trusted-host", new URL(mirror).hostname);
    } catch { /* ignore */ }
    log(`📦 使用 pip 镜像加速: ${mirror}`);
    progress.report({ message: `步骤 2/4  安装依赖 (${new URL(mirror).hostname})…` });
  } else {
    log("📦 使用默认 pip 源 (PyPI)");
    progress.report({ message: "步骤 2/4  安装依赖 (PyPI)…" });
  }

  return new Promise((resolve, reject) => {
    log(`$ ${pip} ${args.join(" ")}`);
    const proc = cp.spawn(pip, args, {
      cwd:   BACKEND_DIR,
      shell: process.platform === "win32",
    });

    let installedCount = 0;
    const estimatedTotal = 12;

    const handleLine = (raw: string) => {
      const line = raw.trim();
      if (!line) return;
      log(line);

      if (line.startsWith("Collecting ")) {
        const pkg = line.replace("Collecting ", "").split(" ")[0].split(";")[0];
        progress.report({ message: `步骤 2/4  解析 ${pkg}…` });
      } else if (line.startsWith("Downloading ")) {
        const pkg = path.basename(line.replace("Downloading ", "").split(" ")[0]);
        progress.report({ message: `步骤 2/4  下载 ${pkg}…` });
      } else if (line.startsWith("Installing collected packages")) {
        progress.report({ message: "步骤 2/4  安装包到虚拟环境…" });
      } else if (line.includes("Successfully installed")) {
        const pkgs = line.replace("Successfully installed", "").trim();
        progress.report({ message: `步骤 2/4  安装完成 ✓`, increment: 50 });
        log(`已安装: ${pkgs}`);
      } else if (line.startsWith("Requirement already satisfied")) {
        installedCount++;
        if (installedCount <= estimatedTotal) {
          progress.report({ increment: Math.floor(50 / estimatedTotal) });
        }
      } else if (line.toLowerCase().includes("error") || line.toLowerCase().includes("failed")) {
        // 高亮错误
        log(`⚠️  ${line}`);
      }
    };

    proc.stdout?.on("data", (d: Buffer) => d.toString().split("\n").forEach(handleLine));
    proc.stderr?.on("data", (d: Buffer) => d.toString().split("\n").forEach(handleLine));

    proc.on("close", (code) => {
      if (code === 0) { log("✅ 依赖安装完成。"); resolve(); }
      else {
        const hint = mirror
          ? `pip install 失败（exit ${code}）。镜像: ${mirror}`
          : `pip install 失败（exit ${code}）。可在设置中配置 contextkeeper.pipMirror 使用国内镜像。`;
        reject(new Error(hint));
      }
    });
  });
}

// ─────────────────────────── 服务进程 ───────────────────────────
async function startServer(context: vscode.ExtensionContext): Promise<void> {
  const port = getPort();
  if (await isServerRunning(port)) {
    log(`Server already running on port ${port}.`);
    return;
  }
  stopServer();

  const python = getPythonBin();
  const env = {
    ...process.env,
    CK_DATABASE_URL: `sqlite:///${path.join(DB_DIR, "context_keeper.db")}`,
    CK_PORT:         String(port),
    CK_HOST:         "127.0.0.1",
    PYTHONPATH:      BACKEND_DIR,
  };

  log(`Starting ContextKeeper on port ${port}…`);
  serverProcess = cp.spawn(python, ["-m", "app.mcp.server"], {
    cwd:      BACKEND_DIR,
    env,
    stdio:    ["pipe", "pipe", "pipe"],
    detached: false,
  });

  serverProcess.stderr?.on("data", (data: Buffer) => {
    const msg = data.toString().trim();
    if (msg) log(`[server] ${msg}`);
  });
  serverProcess.on("exit", (code) => {
    log(`Server exited (code ${code})`);
    serverProcess = null;
    setStatus("$(error) ContextKeeper", "服务已停止 — 点击查看详情");
  });

  await waitForPort(port, 30000);
  log(`HTTP service ready at http://127.0.0.1:${port}`);
}

function stopServer() {
  if (serverProcess) { serverProcess.kill(); serverProcess = null; }
}

async function isServerRunning(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${port}/api/v1/health`, (res) => {
      resolve(res.statusCode === 200);
    });
    req.on("error", () => resolve(false));
    req.setTimeout(1000, () => { req.destroy(); resolve(false); });
  });
}

async function waitForPort(port: number, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await isServerRunning(port)) return;
    await sleep(500);
  }
  throw new Error(`Server did not start within ${timeoutMs / 1000}s on port ${port}`);
}

// ─────────────────────────── MCP 自动注册 ───────────────────────────
async function setupMCP(context: vscode.ExtensionContext, silent = false): Promise<void> {
  const port   = getPort();
  const python = getPythonBin();
  const config = buildMCPConfig(python, port);
  const written: string[] = [];

  if (writeMCPConfig(path.join(os.homedir(), ".cursor", "mcp.json"), config))
    written.push("Cursor");

  for (const p of [
    path.join(os.homedir(), ".claude.json"),
    path.join(os.homedir(), ".config", "claude", "settings.json"),
  ]) {
    if (writeMCPConfig(p, config, "claude")) written.push("Claude Code");
  }

  if (written.length > 0) {
    log(`MCP config written for: ${written.join(", ")}`);
    if (!silent) {
      vscode.window.showInformationMessage(
        `ContextKeeper MCP 已注册到 ${written.join("、")}。重启 IDE 后生效。`
      );
    }
  }
}

function buildMCPConfig(pythonBin: string, port: number) {
  return {
    command: pythonBin,
    args:    ["-m", "app.mcp.server"],
    cwd:     BACKEND_DIR,
    env: {
      CK_DATABASE_URL: `sqlite:///${path.join(DB_DIR, "context_keeper.db")}`,
      CK_PORT:         String(port),
      PYTHONPATH:      BACKEND_DIR,
    },
  };
}

function writeMCPConfig(filePath: string, serverConfig: object, format = "cursor"): boolean {
  try {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    let existing: any = {};
    if (fs.existsSync(filePath)) {
      try { existing = JSON.parse(fs.readFileSync(filePath, "utf-8")); } catch { /* ignore */ }
    }
    existing.mcpServers = existing.mcpServers || {};
    existing.mcpServers.contextkeeper = serverConfig;
    fs.writeFileSync(filePath, JSON.stringify(existing, null, 2) + "\n");
    log(`MCP config written: ${filePath}`);
    return true;
  } catch (err: any) {
    log(`Failed to write MCP config ${filePath}: ${err?.message}`);
    return false;
  }
}

// ─────────────────────────── 命令实现 ───────────────────────────
function openDashboard() {
  vscode.env.openExternal(
    vscode.Uri.parse(`http://127.0.0.1:${getPort()}/static/index.html`)
  );
}

async function restartServer(context: vscode.ExtensionContext) {
  setStatus("$(sync~spin) ContextKeeper: 重启中…");
  stopServer();
  await sleep(500);
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "ContextKeeper: 重启服务…", cancellable: false },
    async () => { await startServer(context); }
  );
  setStatus("$(check) ContextKeeper", "ContextKeeper 运行中");
}

function showStatus() {
  const port = getPort();
  outputChannel.show();
  vscode.window.showInformationMessage(
    `ContextKeeper 运行中 | Dashboard: http://127.0.0.1:${port}/static/index.html`,
    "打开 Dashboard"
  ).then((sel) => {
    if (sel === "打开 Dashboard") openDashboard();
  });
}

// ─────────────────────────── 工具函数 ───────────────────────────
function getPort(): number {
  return vscode.workspace.getConfiguration("contextkeeper").get<number>("port", 8765);
}

function setStatus(text: string, tooltip?: string) {
  statusBarItem.text = text;
  if (tooltip) statusBarItem.tooltip = tooltip;
}

function log(msg: string) {
  outputChannel.appendLine(`[${new Date().toLocaleTimeString()}] ${msg}`);
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function runCommand(cmd: string, args: string[], cwd: string): Promise<void> {
  return new Promise((resolve, reject) => {
    log(`$ ${cmd} ${args.join(" ")}`);
    const proc = cp.spawn(cmd, args, { cwd, shell: process.platform === "win32" });
    proc.stdout?.on("data", (d: Buffer) => log(d.toString().trim()));
    proc.stderr?.on("data", (d: Buffer) => log(d.toString().trim()));
    proc.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Command failed (exit ${code}): ${cmd} ${args.join(" ")}`));
    });
  });
}

function runCommandOutput(cmd: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    cp.execFile(cmd, args, (err, stdout) => {
      if (err) reject(err); else resolve(stdout);
    });
  });
}
