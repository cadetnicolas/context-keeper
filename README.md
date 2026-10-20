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

## 多人并行开发：上下文合并

团队先约定同一项目 UUID（`init --project-id UUID`），不同工作使用独立任务 ID。每个 Agent 使用自己的项目上下文，通过 ZIP 交换任务或整个项目的记录：

```bash
python3 /installed/context-keeper/scripts/ck.py --workspace /bob/project export --all --output /tmp/bob-context.zip
python3 /installed/context-keeper/scripts/ck.py --workspace /alice/project merge --source /tmp/bob-context.zip
# 审阅预览结果，填入返回的 plan；有冲突时还需 --resolutions FILE.json
python3 /installed/context-keeper/scripts/ck.py --workspace /alice/project merge --source /tmp/bob-context.zip --apply --plan PLAN
```

独立任务保留各自记录，同一任务基于共同祖先合并字段；双方对同一字段的不同修改需明确选择。原始历史保留，合并结果不会被当作已验证的代码或测试结果。内容存在分叉合并的经验重新成为待确认提案。

查看[团队合并与冲突处理](skills/context-keeper/references/merge.md)，了解共享项目身份、证据分组、重复合并、中断恢复及跨任务语义冲突的边界。

## 仓库与验证

`skills/context-keeper/` 是可独立分发的完整能力包；其他文件只用于安装说明、许可、测试和打包。旧版前后端、扩展与服务已从主分支文件中移除。

```bash
python3 -m unittest discover -s tests -v
bash build.sh
```

打包结果为 `dist/context-keeper-skill-0.4.0.zip`，解压后可脱离源码仓库安装。

[Skill 指令](skills/context-keeper/SKILL.md) · [安装参数](skills/context-keeper/INSTALL.md) · [命令参考](skills/context-keeper/references/commands.md) · [MIT License](LICENSE)
