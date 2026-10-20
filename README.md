# ContextKeeper Skill

让编码 Agent 在切换工具或会话时接续项目进度，并沿用已确认的协作经验。

ContextKeeper 使用 Agent 自身的总结能力，把目标、决策、已完成工作、下一步和协作经验保存为项目内的 Markdown/JSON。接手的 Agent 读取交接内容后，使用独立脚本核对工作区和记录完整性。需要 Python 3.9+；完整工作区核对需要 Git。

## 让 Agent 安装

把 [INSTALL.md](INSTALL.md) 的 GitHub 链接交给编码 Agent，并说：

> 请按此安装说明，将 ContextKeeper 安装到当前 Agent 的用户级 Skill 目录，验证安装结果，并告诉我如何保存和恢复进度。

也可以克隆仓库后手动运行：

```bash
python3 skills/context-keeper/install.py --dest /actual/agent/user/skills/context-keeper
```

安装目录应使用当前宿主实际支持的用户级 Skill 目录；Codex 可直接使用 `--codex`。安装一次供多个项目复用，运行时用 `--workspace /absolute/path/to/project` 指定项目。各项目的总结与经验保存在自己的 `.context-keeper/` 中。

不支持原生 Skill 发现的宿主，可显式读取安装目录中的 `AGENT.md`。`--project` 仅用于可选的项目内便携部署。

## 保存与接续

- 保存：让 Agent 读取安装后的 AGENT.md，保存当前任务进度并准备交接。
- 接续：将项目代码和 `.context-keeper/` 记录一起交给下一个 Agent，让其读取 AGENT.md 并恢复指定任务。
- 经验：由用户确认的协作偏好可记录为项目经验，后续接续时一并读取。

Skill 能力包与项目记忆分开存储。安装不启动服务、不修改宿主配置；工作区核对只验证文件状态，不能保证模型已经理解全部背景。

## 项目记忆随 Git 协作

项目首次 `init` 自动生成 UUID，将 `.context-keeper/project.json` 和生成的忽略规则提交到仓库。队友克隆后直接沿用这个身份，无需再约定或输入 UUID。Skill 仍安装在每个 Agent 上。

```bash
# 首次初始化；之后审阅并提交项目身份和忽略规则
python3 /installed/context-keeper/scripts/ck.py --workspace /project init

# 新工作自动分配任务 ID；继续已有工作时沿用原 ID
python3 /installed/context-keeper/scripts/ck.py --workspace /project new-task --title "登录接口" --goal "实现兼容的登录接口"

# 正常保存是本地草稿；明确分享后，记忆文件才成为 Git 提交候选
python3 /installed/context-keeper/scripts/ck.py --workspace /project share --task TASK_ID

# 队友拉取代码后：预览并应用共享记忆；重复到 up_to_date
python3 /installed/context-keeper/scripts/ck.py --workspace /project sync
python3 /installed/context-keeper/scripts/ck.py --workspace /project sync --apply --plan PLAN
```

共享记录按内容哈希命名，双方写出同一个检查点版本号也不会覆盖文件。`sync` 基于历史合并上下文，同字段冲突需明确解决；冲突参数见[合并说明](skills/context-keeper/references/merge.md)。代码合并和测试仍需正常完成。

共享记忆与本地状态分开：项目 UUID、显式发布的任务和经验随 Git 流转，私有草稿、锁和恢复事务留在本地。工具不会自动执行 Git 提交或推送。ZIP 保留为离线交接和选择性分享方式。

完整流程见[项目记忆与 Git 协作](skills/context-keeper/references/git-sharing.md)，包括任务 ID、旧记录兼容、并行分叉、隐私和中断恢复。

## 仓库与验证

`skills/context-keeper/` 是可独立分发的完整能力包；其他文件只用于安装说明、许可、测试和打包。旧版前后端、扩展与服务已从主分支文件中移除。

```bash
python3 -m unittest discover -s tests -v
bash build.sh
```

打包结果为 `dist/context-keeper-skill-0.5.0.zip`，解压后可脱离源码仓库安装。

[Skill 指令](skills/context-keeper/SKILL.md) · [安装参数](skills/context-keeper/INSTALL.md) · [命令参考](skills/context-keeper/references/commands.md) · [MIT License](LICENSE)
