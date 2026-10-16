# ContextKeeper Skill

让编码 Agent 在切换工具或会话时接续项目进度，并沿用已确认的协作经验。

ContextKeeper 使用 Agent 自身的总结能力，把目标、决策、已完成工作、下一步和协作经验保存为项目内的 Markdown/JSON。接手的 Agent 读取交接内容后，使用独立脚本核对工作区和记录完整性。需要 Python 3.9+；完整工作区核对需要 Git。

## 让 Agent 安装

把 [INSTALL.md](INSTALL.md) 的 GitHub 链接交给编码 Agent，并说：

> 请按此安装说明，将 ContextKeeper 安装到当前项目，验证安装结果，并告诉我如何保存和恢复进度。

也可以克隆仓库后手动运行：

```bash
python3 skills/context-keeper/install.py --project /absolute/path/to/project
```

随后让 Agent 读取目标项目中的 `.agent-tools/context-keeper/AGENT.md`。支持原生 Skill 发现的宿主，可使用 `--dest /actual/host/skills/context-keeper` 安装到其实际发现目录。

## 保存与接续

- 保存：让 Agent 读取安装后的 AGENT.md，保存当前任务进度并准备交接。
- 接续：将项目代码和 `.context-keeper/` 记录一起交给下一个 Agent，让其读取 AGENT.md 并恢复指定任务。
- 经验：由用户确认的协作偏好可记录为项目经验，后续接续时一并读取。

Skill 能力包与项目记忆分开存储。安装不启动服务、不修改宿主配置；工作区核对只验证文件状态，不能保证模型已经理解全部背景。

## 仓库与验证

`skills/context-keeper/` 是可独立分发的完整能力包；其他文件只用于安装说明、许可、测试和打包。旧版前后端、扩展与服务已从主分支文件中移除。

```bash
python3 -m unittest discover -s tests -v
bash build.sh
```

打包结果为 `dist/context-keeper-skill-0.3.1.zip`，解压后可脱离源码仓库安装。

[Skill 指令](skills/context-keeper/SKILL.md) · [安装参数](skills/context-keeper/INSTALL.md) · [命令参考](skills/context-keeper/references/commands.md) · [MIT License](LICENSE)
