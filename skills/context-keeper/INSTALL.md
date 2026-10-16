# 安装 ContextKeeper 到任意编码 Agent

能力包遵循公开 Agent Skills 文件格式；支持该格式的宿主可以直接加载 SKILL.md。其他具备文件读取和本地 Python 执行能力的 Agent 可从 AGENT.md 使用相同流程。各宿主的发现目录、权限和触发方式不同，不承诺放到任意目录就会自动加载。

## 推荐：安装到 Agent 用户级 Skill 目录

能力包安装一次供多个项目复用，使用宿主实际支持的用户级 Skill 目录：

```bash
python3 /path/to/context-keeper/install.py --dest /actual/agent/user/skills/context-keeper
```

Codex 可使用下文的 `--codex`。各项目的总结、任务与经验保存在自己的 `.context-keeper/`，不会写入 Skill 安装目录。运行时用 `--workspace /absolute/path/to/project` 指定项目。

## 可选：项目内便携部署

将能力包下载或解压后，运行（把路径换成你的项目）：

```bash
python3 /path/to/context-keeper/install.py --project /absolute/path/to/project
```

结果为：`/absolute/path/to/project/.agent-tools/context-keeper/`。

然后对 Agent 说：

```text
请读取当前项目的 .agent-tools/context-keeper/AGENT.md，
按其中的 Skill 流程保存当前进度，准备交接给另一个 Agent。
```

`.agent-tools` 是本工具的普通目录选择，不是所有 Agent 的标准发现目录。无需改写 AGENTS.md 或宿主配置。若希望自动发现，请使用宿主实际支持的 Skill 目录。

## 安装到指定宿主的 Skill 目录

```bash
python3 /path/to/context-keeper/install.py --dest /actual/host/skills/context-keeper
```

`--dest` 是完整目标目录，包含末尾 context-keeper。目录结构和版本会被保留并校验。

## 通用用户目录与 Codex 选项

不传目录参数时，安装到 `$XDG_DATA_HOME/agent-tools/context-keeper`，未设置时为 `~/.local/share/agent-tools/context-keeper`。该目录同样需要让 Agent 显式读取 AGENT.md，不能假设宿主自动扫描。

Codex 用户可显式选择：

```bash
python3 /path/to/context-keeper/install.py --codex
```

## 更新与验证

已存在目录不会被静默覆盖。对已由安装器管理的副本，使用相同目标参数并加 `--update`；旧版备份保存在发现目录之外。

```bash
python3 /path/to/context-keeper/install.py --project /absolute/path/to/project --update
python3 /absolute/path/to/project/.agent-tools/context-keeper/scripts/ck.py --version
python3 /absolute/path/to/project/.agent-tools/context-keeper/scripts/ck.py --workspace /absolute/path/to/project status
```

安装不启动服务、不改模型或 MCP 设置、不读取项目对话、不上传数据。使用前审阅来源和脚本，按宿主正常执行权限运行，不需要关闭沙箱或绕过审批。没有代码执行能力的聊天 Agent 可以读交接 Markdown，但不能完成安装和自动核对。
