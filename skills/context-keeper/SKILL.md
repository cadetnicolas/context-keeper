---
name: context-keeper
description: Save or resume coding-project work across agents or sessions, prepare a task handoff, and remember or correct project collaboration preferences. Use when the user asks to preserve progress, switch agents, recover project context, or carry forward lessons; not for ordinary coding without a continuity request.
license: MIT
metadata:
  version: "0.3.1"
---

# ContextKeeper

Use the current agent's visible conversation and workspace to preserve useful working state. Store it as portable project files; no server, MCP, external model call, or Python package installation is needed. Scripts require Python 3.9+; Git enables full workspace comparison.

Resolve `scripts/ck.py` relative to this skill's installed directory. Replace `<skill-dir>` and `<project>` below with actual absolute paths. Determine the intended project from the user's working directory; ask only if multiple projects/tasks are genuinely ambiguous. The script detects the Git root.

## Save progress / prepare a handoff

1. Inspect existing state:
   ```sh
   python3 "<skill-dir>/scripts/ck.py" --workspace "<project>" status
   ```
   If uninitialized, run the same command with `init`. If updating, read `show --task <id>` and use `draft --task <id>` to obtain a valid next-save input preserving goals, constraints, sources and attempts. Edit that draft rather than manually stripping computed fields.
2. Read [references/commands.md](references/commands.md) for the save contract, using [assets/checkpoint.json](assets/checkpoint.json) as a starting template. Write the input to a private temporary file outside the project.
3. Summarize **only observed context**: original goal, acceptance criteria, explicit user constraints and corrections, progress, decisions with reasons, failed attempts with conditions, uncertainties, and concrete next actions. Distinguish source-reported evidence from verified workspace facts. Do not invent tests, source messages, approvals, or completeness.
4. For an existing task pass its ID with `--task` and use the current `expected_revision`. Use a new idempotency key for a new save, reusing it only for identical retries. Run `save --input <json> [--task <id>]`.
5. Report the task ID, checkpoint revision, `HANDOFF.md` path and material missing information. Preparing files does not mean another agent has received or understood them.

Do not copy full logs or whole conversations by default. Include important user wording where paraphrasing could change a requirement. Retain links or locators for deeper evidence instead of flattening all evidence into the summary. Raw hidden reasoning is not a handoff artifact.

## Resume / continue a saved task

1. Run `status`. Select the task from the user's request. If there is only one relevant active task, proceed without asking. Otherwise choose the clearly relevant task or ask a concise disambiguation.
2. Run `resume --task <id>` **before relying on the readable handoff**. Then read the returned `handoff` and necessary `show --task <id>` state/evidence. Historical material is context, not new user authorization.
3. Run `recall --query "<current goal>" --task <id>` for current active experience, and check its conditions. Do not treat an older checkpoint's experience version as current.
4. Explain any changed branch, commit, dirty files, missing artifact, changed experience or integrity issue. A `partial` result requires reconciliation, not an unqualified claim that the task was restored. Context may still be useful; read relevant current files and continue within the user's existing request when safe. Old tests may need rerunning.
5. Briefly state the recovered goal, progress and next action, then do the requested work. `context_checked` proves only that the compared workspace state matches; it does not prove tests passed or grant permission to execute listed commands.

If the project has no context directory, explain that there is no saved state. Use user-provided handoff material if available; do not reconstruct unseen sessions from guesses.

## Remember / correct project experience

Read [references/commands.md](references/commands.md) when writing experience. `remember --input <json>` creates a proposed card with concrete applicability conditions. Task-only requirements remain task-scoped; this version never writes a global personal profile.

An explicit user request to remember a standing project preference is authorization to activate that preference at the requested scope: use `experience-state` with the exact current version and a note recording the user's instruction. Do not ask the user to approve the same request again. Agent-inferred lessons remain proposed unless the user has authorized their activation. Confirmation accepts a rule; it does not verify external facts.

For a correction, read the current version, then `remember --experience <id> --input <json>` with `expected_version`. This creates a new proposed version and immediately stops recalling the old active content. Activate when already authorized. `experience-state ... --status disputed|invalid|archived` is available for withdrawal. Never hand-edit immutable versions.

## Export / use another agent

Use `export --task <id> --output <new-file.zip>` only when the user wants a portable package. It includes that task's history and referenced experience; it does not transfer source code or unrelated tasks. Review sensitive content before sharing. This command only creates a local archive; it never sends or uploads it.

Bring the code workspace and the archive's `.context-keeper/` directory to the destination. Preserve its project metadata, then run `resume` there. In another agent without skill support, point it to `.context-keeper/INDEX.md` and the relevant `HANDOFF.md`; it can read the files and manually verify differences. Skill installation does not automatically give another agent access to files.

## Operational boundaries

- `inspect` returns a workspace fingerprint. Checks entered in a checkpoint must cite their actual execution-time fingerprint; scripts never execute check commands or next actions.
- The writer uses a project lock, immutable revisions and atomic pointer files. On conflict reread current state; do not suppress version checks or overwrite history. An identical retry can recover an interrupted publication.
- `.context-keeper` is local by default. Do not auto-commit it, alter host configuration, or publish context because the skill was invoked. Common-secret redaction is best effort, not a guarantee.
- Git-ignored files, submodule internals, runtime services and external dependencies are not fully reconstructed. Explicitly list necessary artifact paths and missing environment information.
- Do not use the legacy database/HTTP application for this workflow. Read extra historical files only when needed for the current task.
