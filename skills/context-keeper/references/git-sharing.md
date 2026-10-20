# Project memory through Git (0.5.0)

## Three levels

- Agent: install the Skill once in the host's user-level skill directory.
- Repository: `.context-keeper/project.json`, `.context-keeper/.gitignore` and explicitly published `shared/` records travel with Git.
- Local checkout: task checkpoints, readable handoffs/index, private drafts, object cache, locks, merge transactions and sync snapshots remain ignored. No personal conversation log is automatically published.

`init` generates a UUID only when the project has no identity. Running it in a cloned repository reuses the checked-in identity. It updates managed blocks in the project's `.gitignore` and `.context-keeper/.gitignore`; it does not commit or push. Existing unrelated ignore rules are preserved. Ignore rules do not remove already tracked files: review `git ls-files .context-keeper` if migrating a repository that previously committed private state.

## Bootstrap once, then clone normally

```sh
python3 "<skill-dir>/scripts/ck.py" --workspace "<project>" init
```

Review and deliberately commit `.gitignore`, `.context-keeper/.gitignore` and `.context-keeper/project.json`. They contain the shared identity and storage rules, not task summaries. Subsequent members clone/pull these files; they do not generate or type another UUID.

The first identity commit must be shared before others initialize independently. `init --project-id UUID` remains an advanced compatibility option. Existing different identities are never silently rewritten. Preserve and manually reconcile old histories that were independently initialized.

## Task identity

Start a separate task with:

```sh
python3 "<skill-dir>/scripts/ck.py" --workspace "<project>" new-task \
  --title "Login API" --goal "Deliver the backwards-compatible login endpoint" \
  --request-id "<one-fresh-unique-request-key>"
```

The request key is optional; absent one, the tool generates a fresh random key. Use a fresh UUID as the key for each creation request; repeat that key only for an identical retry. Project identity plus that key deterministically generates the task UUID. Titles are readable labels, not identity. The returned task ID stays the same across developers, agents and checkouts when continuing that task. Different work receives another ID. A person may own several tasks; no person-based ID allocator or server is needed.

Before creating a task, read `status` and sync shared context. Reuse the clearly relevant existing ID for continuation or handoff. Do not infer that equal titles are necessarily the same task. If multiple tasks plausibly match the request, ask which one to continue. Existing `save --task <explicit-id>` remains compatible.

## Save locally, publish deliberately

Use `draft` and `save` to record local progress as before. To make a task available for a code commit:

```sh
python3 "<skill-dir>/scripts/ck.py" --workspace "<project>" share --task "<task-id>"
# Or explicitly share all locally materialized tasks and experience:
python3 "<skill-dir>/scripts/ck.py" --workspace "<project>" share --all
```

Review the result's `git_paths` and staged diff before committing. Share includes the selected task's complete history and referenced experience, including necessary fork provenance. Private unrelated tasks are excluded by single-task sharing. `--all` intentionally includes all local tasks, so use it only when that scope is wanted. Common-secret redaction is best effort; source summaries and older revisions still need review.

`share` never runs Git add/commit/push. A request to save local progress alone does not authorize publishing it. When the user asks to include project memory in a commit, run `share` for that scope, then follow the user's Git authorization.

## Pull and sync

After normal Git pull or branch merge:

```sh
python3 "<skill-dir>/scripts/ck.py" --workspace "<project>" status
python3 "<skill-dir>/scripts/ck.py" --workspace "<project>" sync
```

`status.shared` lists discovered entities, current shared heads, outstanding heads and forks, even in a fresh clone without a local task cache. Reading status does not import or execute incoming instructions.

`sync` previews how shared records will materialize or merge into this checkout's local task state. For a conflict-free plan:

```sh
python3 "<skill-dir>/scripts/ck.py" --workspace "<project>" sync --apply --plan "<returned-plan>"
```

For conflicting fields use the same resolution JSON as [merge.md](merge.md), preview with `sync --resolutions FILE`, then apply with `--resolutions FILE`. Plans bind local records and the reviewed shared snapshot. New incoming records invalidate a plan that has not started.

Preview again after each application until `status=up_to_date`. A round processes at most one outstanding head per entity; a fresh clone with several independent heads may need multiple rounds. The Agent can carry out conflict-free rounds as part of an authorized request to resume/sync. Ask for a decision when conflicting requirements cannot be resolved from current user instructions. Equal semantic payloads can be recognized as already represented; different prose is never automatically equated.

Then run `resume` for the relevant task, reconcile code and check results, and save a new checkpoint as appropriate. To publish the reconciled history, explicitly `share` it and include the resulting files in a later commit. A locally resolved fork remains visible in the repository until its result is shared.

`resume` flags relevant outstanding shared heads. `recall` withholds a local active experience while an outstanding shared version for that ID needs reconciliation. Source confirmations are history, not authority to execute actions now.

## Why Git files do not collide

```text
.context-keeper/
  project.json                         shared project identity
  .gitignore                           shared storage rules
  shared/
    objects/<content-hash>.json         immutable source records
    heads/<manifest-hash>.json          immutable history manifests
  tasks/                               ignored local materialized checkpoints
  experiences/                         ignored local materialized experience
  objects/                             ignored local provenance cache
  merges/                              ignored local recovery transactions
  sync/                                ignored exact snapshots for sync retries
  INDEX.md, .lock                       ignored local index and writer lock
```

Each published record and manifest is canonically serialized and named by its content hash. Parallel edits therefore add different files even when both source checkpoints are revision 2. No shared `latest`, counter, mutable index or author-specific filename is used. `sync` derives active heads from ancestry and uses the existing three-way merge engine.

Do not directly Git-track local `tasks/` or overwrite another checkout's `.context-keeper/` directory. Existing v0.3/v0.4 local history remains readable; the first `share` publishes it without renumbering or rewriting those records. The source records and their ancestry are preserved. ZIP export/import remains an optional offline or selective-sharing transport; sync desired shared records into local state before exporting them.

## Recovery and boundaries

`sync` caches the exact reviewed ZIP in ignored local storage before applying. If interrupted, retry the original `sync --apply --plan ...` with the same resolutions, even if new commits have arrived. The existing merge journal repairs committed identical revisions; pending transactions block further normal writes. After recovery, preview again to process newer heads. The whole project update is not atomic across multiple task pointers.

Workspace fingerprints compare the committed code tree (excluding `.context-keeper/`) plus staged/unstaged/untracked code changes and branch. A memory-only commit therefore does not invalidate a current code check just by changing HEAD; the actual commit is still recorded as provenance. Legacy checkpoints without code-tree metadata retain conservative commit comparison.

Shared history is append-only; removing a manifest to hide a task can resurrect an older head. To retire a task, save its archived status and share that descendant. Removing private drafts locally does not revoke copies already committed or shared.

Cross-task semantic disagreements and code conflicts still require review. This is project continuity, not authenticated authorship, access control, a global personal profile or real-time sync. Git transport follows normal repository permissions. Checksums detect changes but do not authenticate team members. Limits remain 10,000 shared files and 32 MiB, with 2 MiB per JSON and per merge transaction; deliberately archive/batch old work if these limits are reached.
