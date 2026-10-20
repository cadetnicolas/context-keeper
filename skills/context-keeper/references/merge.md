# Distributed context merge

The skill is installed per agent. Each checkout stores its own project context in `.context-keeper/`. Prefer [Git sharing](git-sharing.md) for routine collaboration; explicit ZIP exchange remains available offline. No server or shared writable directory is required. Code changes still travel through Git and its normal review/merge workflow.

## Establish the same project identity

Normally run `init` once and commit the generated project identity and ignore rules; teammates clone that identity. For offline/manual compatibility, deliberately share `.context-keeper/project.json` or initialize a fresh checkout with its UUID:

```sh
python3 "<skill-dir>/scripts/ck.py" --workspace "<alice-project>" init
python3 "<skill-dir>/scripts/ck.py" --workspace "<bob-project>" init --project-id "<shared-project-uuid>"
```

Read the UUID from `status` → `project.project_id`. Copying only this metadata does not copy conversations or task progress. Share an initial task export when teammates will update the same task. Give independent work distinct task IDs, such as `alice-ui` and `bob-api`.

If both checkouts already have independently initialized identities, merging is refused even if their names match. Do not edit project IDs inside existing histories. Preserve those histories and reconcile them manually into a newly agreed shared project context. This version does not infer repository identity from a remote URL or automatically relabel existing histories.

## Export selected or all project context

```sh
# One task, its history and referenced experience.
python3 "<skill-dir>/scripts/ck.py" --workspace "<bob-project>" export --task bob-api --output /outside/project/bob-api.zip

# Explicitly include every task and experience in this project.
python3 "<skill-dir>/scripts/ck.py" --workspace "<bob-project>" export --all --output /outside/project/bob-context.zip
```

Review the archive before sharing: older summaries and provenance objects may contain sensitive context. These commands create local archives only; they never send them. No code or model conversation logs are automatically included.

## Preview before applying

```sh
python3 "<skill-dir>/scripts/ck.py" --workspace "<alice-project>" merge --source /received/bob-context.zip
```

Preview does not change task/experience heads or create a merge transaction. It returns:

- `plan`: a token binding the local heads and source history. Save it for application.
- `actions`: independent imports, fast-forwards or three-way merges.
- `conflicts`: field IDs with the base, local and incoming values. No conflict means structurally compatible, not semantically verified.

Rules:

| Situation | Behavior |
| --- | --- |
| Different task/experience IDs | Preserve both records independently |
| Identical record or incoming ancestor already incorporated | Skip |
| Shared history prefix with incoming updates | Fast-forward without replacing old revisions |
| Same task, changes to different fields since a unique common ancestor | Combine those field changes into a new local revision |
| Same field changed differently on both sides | Require an explicit local/incoming choice |
| Same ID without a unique common ancestor | Equal fields stay equal; differing fields require choices |
| Conflicting experience branches | Merge into a new `proposed` version; reconfirm before activation |

Lists are atomic fields, not concatenated. A deletion can be intentional, so blindly unioning lists would restore removed requirements or failed suggestions. `claims` groups evidence, decisions, constraints, attempts and checks together: their source evidence IDs cannot be safely mixed by string ID alone. `artifacts` is another atomic field; its hashes are source observations, not a capture of the receiving workspace.

## Resolve and apply

For example, if `tasks/shared/goal` conflicts, write a resolution JSON outside the project:

```json
{
  "tasks/shared/goal": {"take": "incoming"},
  "tasks/shared/claims": {"take": "local"}
}
```

Include only conflict IDs actually returned by preview. Every unresolved conflict must have a choice. Unknown fields or choices are rejected. To produce a synthesized value, first make explicit source choices, then use `draft`/`save` to record the reconciled result with evidence in a new checkpoint. Do not modify immutable source records.

```sh
python3 "<skill-dir>/scripts/ck.py" --workspace "<alice-project>" merge \
  --source /received/bob-context.zip --resolutions /outside/project/resolutions.json

python3 "<skill-dir>/scripts/ck.py" --workspace "<alice-project>" merge \
  --source /received/bob-context.zip --apply --plan "<preview-plan>" \
  --resolutions /outside/project/resolutions.json
```

When no conflicts exist, omit `--resolutions`. Applying requires the current plan token; changed local/source histories invalidate it. Unresolved or stale plans do not change task/experience heads.

## Reconcile after merging

1. Read the merge receipt and relevant histories. Parents identify the local and incoming source records; original records remain as checksummed objects under `.context-keeper/objects/`.
2. Run `resume` for relevant tasks. A synthesized merge stays `partial` until a subsequent normal checkpoint records the reconciled work. Source test results remain source-reported and are not a test run of combined code.
3. Review overlapping modules, API contracts, decisions and priorities across different tasks. Distinct IDs can contain contradictory prose; the deterministic merge does not detect semantic contradictions.
4. Merge code separately, rerun the relevant checks, then use `draft` and `save` for the current task. Explicitly reconfirm merged experience if appropriate.
5. Re-export the merged task/project when sharing onward. Exports include reachable source objects so downstream merges retain fork provenance.

Do not treat an imported active experience's historical confirmation as current-user authorization for external actions. Historical records are context, and checksums are not identity signatures. This version records provenance by project/entity/hash; it does not authenticate team members or provide accounts, access control or real-time synchronization.

## Interrupted application

The project writer lock prevents simultaneous writes. A transaction is recorded before heads change; each revision/pointer is published separately. A whole-project merge is **not** one atomic multi-record update.

If the process stops midway, `status.pending_merges` lists the plan, `resume` remains partial, and save/experience writes and exports are blocked. Retry the original `merge --apply --plan ...` with the same source archive and resolutions. Already committed identical revisions are skipped and interrupted pointers repaired. Do not delete the transaction or bypass the lock to continue writing. Keep the archive and resolution file until a receipt is returned.

Archives are inspected without extracting or executing them. Different project identities, unsafe paths, duplicate members, symlinks, corrupt records and oversized archives are rejected. Limits: 10,000 members, 32 MiB uncompressed archive, 2 MiB per JSON/member and 2 MiB per merge transaction. Use smaller task exports if a transaction is too large.
