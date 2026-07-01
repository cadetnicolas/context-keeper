# ContextKeeper

团队级 AI 编码 Agent 共享记忆层。

让 Cursor、Claude Code、Codex 等 AI 编码工具记住团队决策、踩坑记录、架构规范和项目偏好，告别"每次新会话都要重新解释一遍"的低效循环。

## 核心特性

- **MCP 原生接入**：通过 Model Context Protocol 与 Cursor / Claude Code / Codex 无缝集成。
- **团队共享记忆**：个人本地 SQLite 或团队 PostgreSQL + pgvector 两种部署模式。
- **混合召回**：BM25 + 向量相似度融合检索，兼顾关键词精确匹配与语义相似度。
- **记忆质量管理**：置信度评分、使用频率追踪、陈旧度衰减、人工反馈闭环。
- **Web Dashboard**：可视化查看、添加、搜索团队记忆。
- **团队同步**：支持 Git 快照导出/导入，或基于 REST API 的团队同步。

## 项目结构

```
context-keeper/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 主入口
│   │   ├── config.py            # 配置管理
│   │   ├── models.py            # SQLAlchemy 数据模型
│   │   ├── memory/
│   │   │   ├── store.py         # 记忆 CRUD
│   │   │   ├── retrieval.py     # BM25 + 向量混合检索
│   │   │   └── sync.py          # 团队记忆同步
│   │   ├── mcp/
│   │   │   └── server.py        # MCP Server（stdio）
│   │   ├── api/
│   │   │   └── routes.py        # REST API
│   │   └── dashboard/
│   │       └── static/index.html # Web 管理后台
│   ├── requirements.txt
│   └── Dockerfile
├── configs/
│   ├── cursor-mcp.json          # Cursor MCP 配置示例
│   └── claude-code-mcp.json     # Claude Code MCP 配置示例
├── docker-compose.yml
└── .env.example
```

## 快速开始

### 1. 安装依赖

```bash
cd context-keeper/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 启动 API 服务

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

服务启动后访问：

- API 文档：http://127.0.0.1:8000/docs
- Dashboard：http://127.0.0.1:8000/static/index.html

### 3. 接入 Cursor

打开 Cursor Settings → MCP，添加以下配置：

```json
{
  "mcpServers": {
    "contextkeeper": {
      "command": "python3",
      "args": ["-m", "app.mcp.server"],
      "cwd": "/Users/zhongxing/Desktop/code_project/daily_project_Qoder/context-keeper/backend",
      "env": {
        "CK_DATABASE_URL": "sqlite:///~/.context-keeper/context_keeper.db"
      }
    }
  }
}
```

Cursor 启动新会话时会自动调用 `contextkeeper_recall` 加载相关记忆，Agent 也可在对话中调用 `contextkeeper_remember` 记录新决策。

### 4. 接入 Claude Code

将 `configs/claude-code-mcp.json` 复制到 Claude Code 配置目录：

```bash
# macOS
mkdir -p ~/.claude-code
cp configs/claude-code-mcp.json ~/.claude-code/mcp-config.json
```

## 核心工具

### `contextkeeper_recall`

根据当前任务召回相关团队记忆。

输入：

```json
{
  "query": "How should we handle database migrations?",
  "project_id": "default",
  "team_id": "default",
  "top_k": 5
}
```

### `contextkeeper_remember`

将团队决策或教训写入记忆。

输入：

```json
{
  "content": "We decided to use Alembic for all database migrations and never commit raw SQL changes.",
  "memory_type": "decision",
  "project_id": "default",
  "team_id": "default",
  "tags": ["database", "migration", "alembic"]
}
```

## Docker 部署

```bash
cd context-keeper
docker-compose up -d
```

服务将在 http://localhost:8000 启动。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CK_DATABASE_URL` | 数据库连接 URL | SQLite 本地路径 |
| `CK_EMBEDDING_MODEL` | 嵌入模型名称 | sentence-transformers/all-MiniLM-L6-v2 |
| `CK_VECTOR_WEIGHT` | 向量检索权重 | 0.6 |
| `CK_BM25_WEIGHT` | BM25 权重 | 0.4 |
| `CK_MEMORY_STALENESS_DAYS` | 记忆陈旧阈值 | 90 |
| `CK_SYNC_MODE` | 同步模式 | none |

## 后续迭代方向

- [ ] 记忆图谱可视化（实体关系网络）
- [ ] 自动从 Git commit / PR review 提取记忆
- [ ] 团队权限与审计日志
- [ ] 与 Linear/Notion/GitHub 的集成
