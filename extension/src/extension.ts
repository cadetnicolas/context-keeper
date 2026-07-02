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

// 扩展自身所在目录（即 extension/ 目录，打包后为 extension root）
const EXT_ROOT = path.resolve(__dirname, "..", "..");  // extension/out → extension/ → repo root
const BACKEND_DIR = path.join(EXT_ROOT, "backend");
const PYTHON_REQUIREMENTS = path.join(BACKEND_DIR, "requirements.txt");
// 虚拟环境统一放在用户 home 目录，命名为 ck-env（ContextKeeper 缩写），便于识别
const VENV_DIR = path.join(os.homedir(), ".context-keeper", "ck-env");
const DB_DIR = path.join(os.homedir(), ".context-keeper");

// ─────────────────────────── 激活入口 ───────────────────────────
export async function activate(context: vscode.ExtensionContext) {
  outputChannel = vscode.window.createOutputChannel("ContextKeeper");
  context.subscriptions.push(outputChannel);

  // 状态栏
  statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100
  );
  statusBarItem.command = "contextkeeper.showStatus";
  statusBarItem.text = "$(sync~spin) ContextKeeper";
  statusBarItem.tooltip = "ContextKeeper — Team Memory for AI Agents";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  // 注册命令
  context.subscriptions.push(
    vscode.commands.registerCommand("contextkeeper.openDashboard", openDashboard),
    vscode.commands.registerCommand("contextkeeper.restartServer", () => restartServer(context)),
    vscode.commands.registerCommand("contextkeeper.showStatus", showStatus),
    vscode.commands.registerCommand("contextkeeper.setupMCP", () => setupMCP(context))
  );

  const config = vscode.workspace.getConfiguration("contextkeeper");
  if (config.get<boolean>("autoStart", true)) {
    // 异步启动，不阻塞 IDE
    startContextKeeper(context).catch((err) => {
      log(`Startup error: ${err}`);
    });
  }
}

export function deactivate() {
  stopServer();
}

// ─────────────────────────── 核心启动流程 ───────────────────────────
async function startContextKeeper(context: vscode.ExtensionContext) {
  setStatus("$(sync~spin) Installing…", "正在安装 ContextKeeper…");

  try {
    // Step 1: 确保数据目录存在
    fs.mkdirSync(DB_DIR, { recursive: true });

    // Step 2: 检查/创建 Python venv
    await ensureVenv();

    // Step 3: 安装 Python 依赖
    await installDependencies();

    // Step 4: 启动 MCP + HTTP 服务
    await startServer(context);

    // Step 5: 自动写入 MCP 配置
    await setupMCP(context, true);

    setStatus("$(check) ContextKeeper", "ContextKeeper 运行中 — 点击查看详情");
    log("ContextKeeper is ready.");
  } catch (err: any) {
    setStatus("$(error) ContextKeeper", `启动失败: ${err?.message}`);
    log(`Error: ${err?.message}`);
    vscode.window
      .showErrorMessage(
        `ContextKeeper 启动失败: ${err?.message}`,
        "查看日志"
      )
      .then((sel) => {
        if (sel === "查看日志") outputChannel.show();
      });
  }
}

// ─────────────────────────── Python 环境 ───────────────────────────
function getPythonBin(): string {
  return process.platform === "win32"
    ? path.join(VENV_DIR, "Scripts", "python.exe")
    : path.join(VENV_DIR, "bin", "python3");
}

async function ensureVenv(): Promise<void> {
  if (fs.existsSync(getPythonBin())) {
    log("Virtual environment found, skipping creation.");
    return;
  }

  const python = await findSystemPython();

  if (fs.existsSync(VENV_DIR)) {
    // 目录存在但 Python 二进制缺失 → venv 损坏，用 --clear 重建
    log("Virtual environment directory exists but appears broken. Recreating with --clear…");
    await runCommand(python, ["-m", "venv", "--clear", VENV_DIR], os.homedir());
  } else {
    log("Creating Python virtual environment…");
    await runCommand(python, ["-m", "venv", VENV_DIR], os.homedir());
  }

  log(`Virtual environment ready at ${VENV_DIR}`);
}

async function findSystemPython(): Promise<string> {
  for (const candidate of ["python3", "python3.11", "python3.10", "python3.9", "python"]) {
    try {
      const result = await runCommandOutput(candidate, ["--version"]);
      if (result.includes("Python 3")) {
        log(`Found system Python: ${candidate}`);
        return candidate;
      }
    } catch {
      /* try next */
    }
  }
  throw new Error(
    "Python 3.9+ not found. Please install Python from https://python.org"
  );
}

async function installDependencies(): Promise<void> {
  const pip = getPythonBin();
  log("Installing Python dependencies (first run may take 1-2 minutes)…");
  setStatus("$(sync~spin) Installing deps…");
  await runCommand(pip, ["-m", "pip", "install", "--quiet", "-r", PYTHON_REQUIREMENTS], BACKEND_DIR);
  log("Dependencies installed.");
}

// ─────────────────────────── 服务进程 ───────────────────────────
async function startServer(context: vscode.ExtensionContext): Promise<void> {
  const port = getPort();

  // 已在运行则跳过
  if (await isServerRunning(port)) {
    log(`Server already running on port ${port}.`);
    return;
  }

  stopServer();

  const python = getPythonBin();
  const env = {
    ...process.env,
    CK_DATABASE_URL: `sqlite:///${path.join(DB_DIR, "context_keeper.db")}`,
    CK_PORT: String(port),
    CK_HOST: "127.0.0.1",
    PYTHONPATH: BACKEND_DIR,
  };

  log(`Starting ContextKeeper server on port ${port}…`);

  serverProcess = cp.spawn(python, ["-m", "app.mcp.server"], {
    cwd: BACKEND_DIR,
    env,
    // MCP server uses stdio — we pipe stdin/stdout for MCP,
    // but the HTTP server runs in background thread inside Python
    stdio: ["pipe", "pipe", "pipe"],
    detached: false,
  });

  serverProcess.stderr?.on("data", (data: Buffer) => {
    const msg = data.toString().trim();
    if (msg) log(`[server] ${msg}`);
  });

  serverProcess.on("exit", (code) => {
    log(`Server exited with code ${code}`);
    serverProcess = null;
    setStatus("$(error) ContextKeeper", "服务已停止");
  });

  // 等待 HTTP 端口就绪（最多 30 秒）
  await waitForPort(port, 30000);
  log(`HTTP service ready at http://127.0.0.1:${port}`);
}

function stopServer() {
  if (serverProcess) {
    serverProcess.kill();
    serverProcess = null;
  }
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
  throw new Error(`Server did not start within ${timeoutMs / 1000}s`);
}

// ─────────────────────────── MCP 自动注册 ───────────────────────────
async function setupMCP(context: vscode.ExtensionContext, silent = false): Promise<void> {
  const port = getPort();
  const python = getPythonBin();
  const config = buildMCPConfig(python, port);
  const written: string[] = [];

  // Cursor: ~/.cursor/mcp.json
  const cursorMCPPath = path.join(os.homedir(), ".cursor", "mcp.json");
  if (writeMCPConfig(cursorMCPPath, config)) written.push("Cursor");

  // Claude Code: ~/.claude.json  or ~/.config/claude/settings.json
  const claudePaths = [
    path.join(os.homedir(), ".claude.json"),
    path.join(os.homedir(), ".config", "claude", "settings.json"),
  ];
  for (const p of claudePaths) {
    if (writeMCPConfig(p, config, "claude")) written.push("Claude Code");
  }

  if (written.length > 0) {
    log(`MCP configuration written for: ${written.join(", ")}`);
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
    args: ["-m", "app.mcp.server"],
    cwd: BACKEND_DIR,
    env: {
      CK_DATABASE_URL: `sqlite:///${path.join(DB_DIR, "context_keeper.db")}`,
      CK_PORT: String(port),
      PYTHONPATH: BACKEND_DIR,
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

    if (format === "claude") {
      // Claude Code 格式：{ "mcpServers": { "contextkeeper": {...} } }
      existing.mcpServers = existing.mcpServers || {};
      existing.mcpServers.contextkeeper = serverConfig;
    } else {
      // Cursor 格式：{ "mcpServers": { "contextkeeper": {...} } }
      existing.mcpServers = existing.mcpServers || {};
      existing.mcpServers.contextkeeper = serverConfig;
    }

    fs.writeFileSync(filePath, JSON.stringify(existing, null, 2) + "\n");
    log(`MCP config written: ${filePath}`);
    return true;
  } catch (err: any) {
    log(`Failed to write MCP config to ${filePath}: ${err?.message}`);
    return false;
  }
}

// ─────────────────────────── 命令实现 ───────────────────────────
function openDashboard() {
  const port = getPort();
  vscode.env.openExternal(
    vscode.Uri.parse(`http://127.0.0.1:${port}/static/index.html`)
  );
}

async function restartServer(context: vscode.ExtensionContext) {
  setStatus("$(sync~spin) Restarting…");
  stopServer();
  await sleep(500);
  await startServer(context);
  setStatus("$(check) ContextKeeper", "ContextKeeper 运行中");
}

function showStatus() {
  const port = getPort();
  outputChannel.show();
  vscode.window.showInformationMessage(
    `ContextKeeper 运行中 | Dashboard: http://127.0.0.1:${port}/static/index.html`
  );
}

// ─────────────────────────── 工具函数 ───────────────────────────
function getPort(): number {
  return vscode.workspace
    .getConfiguration("contextkeeper")
    .get<number>("port", 8765);
}

function setStatus(text: string, tooltip?: string) {
  statusBarItem.text = text;
  if (tooltip) statusBarItem.tooltip = tooltip;
}

function log(msg: string) {
  const ts = new Date().toLocaleTimeString();
  outputChannel.appendLine(`[${ts}] ${msg}`);
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
      else reject(new Error(`Command failed with exit code ${code}: ${cmd} ${args.join(" ")}`));
    });
  });
}

function runCommandOutput(cmd: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    cp.execFile(cmd, args, (err, stdout) => {
      if (err) reject(err);
      else resolve(stdout);
    });
  });
}
