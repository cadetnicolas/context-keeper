# ContextKeeper

团队级 AI 编码 Agent 共享记忆层。

让 Cursor、Claude Code、Codex 等 AI 编码工具记住团队决策、踩坑记录、架构规范和项目偏好，告别"每次新会话都要重新解释一遍"的低效循环。

## 安装（插件方式）

### 方式一：下载 .vsix 直接安装（推荐）

1. 从 [Releases](https://github.com/cadetnicolas/context-keeper/releases) 下载 `context-keeper-0.1.0.vsix`
2. 打开 Cursor 命令面板（`Cmd+Shift+P`）
3. 选择 **Extensions: Install from VSIX…**，选中下载的文件
4. 重启 Cursor

**安装完成后，扩展会自动：**

- 检测系统 Python 并在 `~/.context-keeper/venv` 创建虚拟环境
- 安装所有 Python 依赖（首次约需 1-2 分钟）
- 在 `~/.cursor/mcp.json` 中注册 MCP 服务
- 在后台启动 HTTP 服务（Dashboard 和 REST API）

状态栏右下角出现 `✓ ContextKeeper` 即表示服务就绪。

### 方式二：从源码构建 .vsix

```bash
git clone https://github.com/cadetnicolas/context-keeper.git
cd context-keeper
bash build.sh
# 生成 context-keeper-0.1.0.vsix，按方式一安装
```

## 功能说明

安装完成后，Cursor/Claude Code 的 AI Agent 可自动调用两个工具：

| 工具 | 说明 |
|------|------|
| `contextkeeper_recall` | 根据当前任务召回相关团队记忆 |
| `contextkeeper_remember` | 将决策/教训/规范写入团队记忆 |

**示例对话：**

```
你：我们怎么处理数据库迁移？
AI：(自动调用 contextkeeper_recall，找到记忆)
    根据团队决策：我们统一使用 Alembic 管理迁移，
    所有迁移脚本需要在 PR 中 review，禁止直接执行原始 SQL。
```

## 扩展命令

打开命令面板（`Cmd+Shift+P`）可使用：

| 命令 | 说明 |
|------|------|
| `ContextKeeper: Open Dashboard` | 打开记忆管理界面 |
| `ContextKeeper: Restart Server` | 重启后台服务 |
| `ContextKeeper: Setup MCP (re-register)` | 重新写入 MCP 配置 |
| `ContextKeeper: Show Status` | 显示运行状态 |

## 扩展设置

在 Cursor 设置中可调整：

| 设置项 | 默认值 | 说明 |
|--------|--------|------|
| `contextkeeper.port` | `8765` | HTTP 服务端口 |
| `contextkeeper.autoStart` | `true` | IDE 启动时自动运行 |
| `contextkeeper.defaultProjectId` | `default` | 默认项目 ID |
| `contextkeeper.defaultTeamId` | `default` | 默认团队 ID |

## 记忆类型

| 类型 | 说明 | 示例 |
|------|------|------|
| `decision` | 技术决策 | "我们选择 PostgreSQL 而非 MySQL" |
| `lesson` | 踩坑记录 | "不要直接修改 migrations，必须用 Alembic" |
| `preference` | 团队偏好 | "统一使用 Black 格式化 Python 代码" |
| `architecture` | 架构设计 | "所有外部 API 调用封装在 services/ 目录" |
| `fact` | 项目事实 | "生产环境 PostgreSQL 版本是 15.2" |
| `todo` | 技术债 | "旧版 UserController 需要重构" |

## 团队共享

默认使用本地 SQLite（`~/.context-keeper/context_keeper.db`）。如需多人共享：

1. 将 `CK_DATABASE_URL` 设置为 PostgreSQL 连接字符串
2. 或使用 Dashboard 的导出/导入功能同步 JSON 快照

## 系统要求

- Python 3.9+（安装时自动检测）
- Node.js 不需要（扩展已打包编译好的 JS）
- 磁盘空间：约 500MB（Python 依赖，首次安装时下载）

## 项目结构

```
context-keeper/
├── extension/              ← VS Code / Cursor 扩展
│   ├── src/extension.ts    ← 扩展核心逻辑（TypeScript）
│   └── package.json        ← 扩展清单
├── backend/                ← Python 后端
│   ├── app/
│   │   ├── main.py         ← FastAPI 入口
│   │   ├── models.py       ← 数据模型
│   │   ├── memory/         ← 存储 / 检索 / 同步
│   │   ├── mcp/server.py   ← MCP Server
│   │   └── api/routes.py   ← REST API
│   └── requirements.txt
├── build.sh                ← 一键构建 .vsix
└── docker-compose.yml      ← 团队服务器部署
```
