# Contracts and commands

Use Python 3.9+ with standard library only. All operations are local. The argument order is:

```sh
python3 "<skill-dir>/scripts/ck.py" --workspace "<project>" <command> [options]
```

## Project and tasks

- `init [--name NAME] [--project-id UUID]`: create `.context-keeper/project.json`, local ignore rules and a readable index. Does not change the parent Git ignore/config or install anything.
- `status`: list tasks/revisions and experience status. Does not initialize a missing project.
- `inspect`: read current Git fingerprint without writing task data.
- `save --input FILE [--task ID]`: validate and save a checkpoint. `--input -` accepts JSON from stdin. A new task ID is derived from project identity plus the initial idempotency key, so an identical initial retry does not create a second task.
- `show --task ID [--revision N]`: read full authoritative checkpoint JSON.
- `draft --task ID`: produce a valid next-save input, preserving prior evidence and removing computed fields; edit the result and save it with the same task ID.
- `resume --task ID [--revision N]`: read-only comparison; returns `context_checked` or `partial`, differences, missing files and old checks to rerun.
- `export (--task ID | --all) --output NEW.zip`: create an archive outside the context directory. Existing output is never overwritten. The archive does not contain source code.

For a new task, use the bundled checkpoint template. For an update, use `draft --task ID` and edit that valid input. It retains evidence referenced by existing claims, removes computed fields, and supplies the next version and a fresh key. Do not feed the entire `show` envelope back to save.

Claim: `{"text":"Keep the legacy response field","evidence_ids":["user-1"]}`. Evidence entry: `{"id":"user-1","kind":"user","summary":"User explicitly requested compatibility","locator":"source conversation or file locator"}`. IDs use lowercase letters, digits and hyphens. Evidence kinds: user, agent, file, test, tool. These labels describe origins, not proof of truth or authority.

Attempt: `{"action":"Remove old field","result":"Existing caller failed","conditions":"Legacy client still supported","evidence_ids":["test-1"]}`.

Check:

```json
{
  "command": "pytest tests/test_login.py",
  "observed_at": "2026-10-16T08:00:00Z",
  "exit_code": 0,
  "workspace_fingerprint": "fingerprint-from-the-actual-tested-state",
  "evidence_ids": ["test-1"]
}
```

Only record checks actually observed. Even a matching fingerprint leaves verification `self_reported`. To mark a task completed, set status=completed, a nonempty result and acceptance evidence. Otherwise use active/blocked/archived.

Artifact paths must be workspace-relative and cannot escape through symlinks. Missing artifacts are kept as explicitly unavailable. Hashing is capped at 256 MiB/file and 10,000 untracked files; files deliberately ignored by Git require explicit artifact entries if important.

Generated data is excluded from Git fingerprints even when the user deliberately versions it. Directory moves and clones can preserve identity by carrying `project.json`; workspace checks compare branch, commit and changes independently. Without Git, only explicit artifacts can be compared and the result remains partial.

## Project experience

`remember --input FILE [--experience ID]` accepts:

```json
{
  "idempotency_key": "a-new-proposal-key",
  "expected_version": 0,
  "scope": "project",
  "task_id": null,
  "kind": "preference",
  "content": "Show the proposed approach before changing database schema.",
  "conditions": "Schema-changing work in this project; requested explicitly by the user.",
  "evidence_refs": [{"task_id":"TASK_ID","revision":1,"evidence_id":"user-1"}]
}
```

For task scope, pass scope=task and the existing task_id. Project scope must have task_id=null. This version has no global user scope. Kinds: preference, decision, lesson, fact, architecture.

- New cards: expected_version=0. Updates: pass the ID and latest version; scope cannot silently change.
- Every proposal starts proposed. Correcting an active card creates a new proposed version and stops using the previous content.
- Activate: `experience-state ID --status active --expected-version N --note "Exact user confirmation or applicable existing authorization"`.
- Withdraw: same command with disputed/invalid/archived and a reason.
- `recall --query QUERY [--task ID] [--limit 10]`: active, scoped lexical search; applicable project preferences are included regardless of vocabulary. Read and assess conditions before applying.
- An active version can be used in a checkpoint's `experience_refs`: `[{"id":"EXPERIENCE_ID","version":2}]`. Corrections or withdrawal then appear as a mismatch during resume.

## Storage and recovery

Task: `.context-keeper/tasks/ID/checkpoints/000001/state.json` and `HANDOFF.md`, plus `meta.json` pointing to the committed revision and a generated current `HANDOFF.md`.

Experience: `.context-keeper/experiences/ID/versions/000001/experience.json` and `meta.json`.

Do not edit checksummed immutable records. Reuse identical save/proposal input after an interrupted write; publication can repair a committed revision whose pointer was not updated. If an orphan belongs to different input, preserve it for diagnosis rather than overwriting it. A checksum detects accidental edits; it is not an authenticity signature.

Saved data is ignored by default. Sharing is explicit. Exports contain task history; older summaries may contain sensitive context even when the latest one is short. Already shared files cannot be revoked by modifying the local project. Missing experience evidence from another task must be treated as unavailable, not silently inferred.

## Team merge (0.4.0)

See [merge.md](merge.md) for project identity, examples, conflict granularity, source history and crash recovery.

- `init --project-id UUID`: initialize a teammate checkout with the shared identity before creating history. Existing different identities are not overwritten.
- `export --all --output NEW.zip`: explicitly share every task and experience, including reachable merge provenance. The single-task export remains available.
- `merge --source FILE.zip`: preview imports, fast-forwards, three-way field merges and unresolved conflicts. Never extracts source files into the project.
- `merge --source FILE.zip --resolutions FILE.json`: preview with per-conflict `{ "take": "local" }` or `{ "take": "incoming" }` choices.
- `merge --source FILE.zip --apply --plan TOKEN [--resolutions FILE.json]`: apply a current, fully resolved plan; retry exactly this command after interruption.
- `status.pending_merges`: incomplete transactions that must be recovered before saving/remembering/exporting.

Task/experience records may include `parents` hashes and `merge` provenance. Original branch records are retained in `objects/HASH.json`; transaction and receipt are in `merges/PLAN/`. Do not edit these files. Older schema-1 records remain readable; conservative conflicts are reported when legacy data lacks a unique common ancestor.
