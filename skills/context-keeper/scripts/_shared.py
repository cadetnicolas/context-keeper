"""Git transport: append-only shared objects/manifests, private local projections."""
import json
import os
import tempfile
import zipfile
from pathlib import Path

from _store import (DATA_DIR, SCHEMA, Error, atomic_write, digest, identity, lock,
                    read_json, seal, verify)
from _merge import (CATEGORIES, MAX_BYTES, ancestors, ensure_ready, graph_records,
                    histories, key, objects, payload, validate_record)

ROOT_RULES = '''# BEGIN ContextKeeper project sharing
!/.context-keeper/
/.context-keeper/*
!/.context-keeper/.gitignore
!/.context-keeper/project.json
!/.context-keeper/shared/
!/.context-keeper/shared/**
# END ContextKeeper project sharing
'''
CONTEXT_RULES = '''# BEGIN ContextKeeper local isolation
*
!.gitignore
!project.json
!shared/
!shared/objects/
!shared/objects/*.json
!shared/heads/
!shared/heads/*.json
# END ContextKeeper local isolation
'''


def rules(path, block):
    if path.is_symlink():
        raise Error('Ignore rules must not be symlinked.')
    source = path.read_text(encoding='utf-8') if path.exists() else ''
    start, end = block.splitlines()[0], block.splitlines()[-1]
    if start in source:
        begin = source.index(start)
        finish = source.find(end, begin)
        if finish < 0:
            raise Error('Incomplete ContextKeeper ignore rules; inspect .gitignore.')
        source = source[:begin] + source[finish + len(end):].lstrip('\n')
    updated = source.rstrip('\n') + ('\n\n' if source.strip() else '') + block
    if not path.exists() or path.read_text(encoding='utf-8') != updated:
        atomic_write(path, updated)


def enable(keeper):
    # Only project identity and explicitly shared records become Git candidates.
    rules(keeper.root / '.gitignore', ROOT_RULES)
    rules(keeper.path('.gitignore'), CONTEXT_RULES)


def store(path, record):
    content = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    if len(content.encode('utf-8')) > 2 * 1024 * 1024:
        raise Error('Shared record exceeds the JSON size limit.')
    if path.exists():
        if read_json(path) != record:
            raise Error('An immutable shared file has different contents; preserve it and inspect.')
    else:
        atomic_write(path, record)


def load(keeper):
    pool, manifests, size = {}, [], 0
    project_id = keeper.project()['project_id']
    for folder in ('objects', 'heads'):
        directory = keeper.path('shared', folder)
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir()):
            if path.name.startswith('.ck-'):
                continue  # atomic_write temporary files are not published records.
            path = keeper.path('shared', folder, path.name)
            if not path.is_file() or path.suffix != '.json':
                raise Error('Unexpected shared file; only immutable JSON records are supported.')
            size += path.stat().st_size
            if size > MAX_BYTES or len(pool) + len(manifests) >= 10000:
                raise Error('Shared context exceeds 10,000 files or 32 MiB; archive older work deliberately.')
            record = verify(read_json(path))
            if path.name != digest(record) + '.json' or record.get('project_id') != project_id:
                raise Error('Shared context identity or content address does not match.')
            if folder == 'objects':
                validate_record(record)
                pool[digest(record)] = record
            else:
                manifests.append(record)
    data, inferred = {}, {}
    for manifest in manifests:
        category, item_id = manifest.get('category'), identity(manifest.get('id'))
        chain = manifest.get('history')
        if category not in CATEGORIES or not isinstance(chain, list) or not chain or len(chain) > 10000:
            raise Error('Invalid shared history manifest.')
        records = []
        for index, node in enumerate(chain):
            if not isinstance(node, str) or node not in pool:
                raise Error('Shared history is incomplete; fetch all shared files before syncing.')
            record = pool[node]
            if (key(record) != item_id or ('state' in record) != (category == 'tasks')
                    or record['revision'] != index + 1):
                raise Error('Shared history does not match its entity/revision.')
            previous = chain[index - 1] if index else None
            if record.get('parents') and record['parents'][0] != previous:
                raise Error('Shared primary ancestry does not match its history.')
            if node in inferred and inferred[node] != previous:
                raise Error('Legacy shared history has ambiguous ancestry.')
            inferred[node] = previous
            records.append(record)
        data[(category, item_id, chain[-1])] = records
    graph = {node: list(record.get('parents', [])) for node, record in pool.items()}
    for node, parent in inferred.items():
        if parent and not graph[node]:
            graph[node] = [parent]
    for node, record in pool.items():
        for parent in graph[node]:
            if parent not in pool:
                raise Error('Shared parent object is missing; fetch the complete shared directory.')
            if key(pool[parent]) != key(record) or ('state' in pool[parent]) != ('state' in record):
                raise Error('Shared ancestry links unrelated entities.')
    candidates = {}
    for (category, item_id, head), chain in data.items():
        candidates.setdefault((category, item_id), {})[head] = chain
    tips = {}
    for entity, heads in candidates.items():
        older, queue = set(), [parent for head in heads for parent in graph.get(head, [])]
        while queue:
            node = queue.pop()
            if node not in older:
                older.add(node)
                queue.extend(graph.get(node, []))
        tips[entity] = {head: chain for head, chain in heads.items() if head not in older}
        if not tips[entity]:
            raise Error('Shared ancestry contains no usable head.')
    return pool, tips, graph


def inspect(keeper):
    pool, tips, graph = load(keeper)
    local = histories(keeper)
    own_pool = objects(keeper)
    for category in CATEGORIES:
        for chain in local[category].values():
            own_pool.update((digest(r), r) for r in chain)
    own_graph = graph_records(local, own_pool)
    graph.update({node: parents for node, parents in own_graph.items() if parents or node not in graph})
    rows = []
    for (category, item_id), heads in sorted(tips.items()):
        chain = local[category].get(item_id, [])
        incorporated = ancestors(digest(chain[-1]), graph) if chain else {}
        outstanding = sorted(head for head in heads if head not in incorporated
                             and (not chain or payload(category, pool[head]) != payload(category, chain[-1])))
        rows.append({'category': category, 'id': item_id, 'heads': sorted(heads),
                     'outstanding': outstanding, 'forked': len(heads) > 1,
                     'title': next(iter(heads.values()))[-1].get('state', {}).get('title')})
    return {'entities': rows, 'needs_sync': any(row['outstanding'] for row in rows),
            'forks': sum(row['forked'] for row in rows), 'objects': len(pool)}


def share(keeper, task_id=None, all_tasks=False):
    if (task_id is None) == (not all_tasks):
        raise Error('Select exactly one task or --all to share.')
    with lock(keeper.directory):
        ensure_ready(keeper)
        enable(keeper)
        local = histories(keeper)
        selected = [(c, i) for c in CATEGORIES for i in local[c]] if all_tasks else [('tasks', identity(task_id))]
        if not all_tasks:
            if task_id not in local['tasks']:
                raise Error('Task is not available locally; sync or choose an existing task.')
            refs = {ref['id'] for r in local['tasks'][task_id] for ref in r['state']['experience_refs']}
            selected += [('experiences', i) for i in sorted(refs)]
        pool = objects(keeper)
        for category in CATEGORIES:
            for chain in local[category].values():
                pool.update((digest(r), r) for r in chain)
        # Preserve selected history/provenance, including legacy records without parent links.
        # Private unrelated entities stay local.
        needed, manifests, visited = [], [], set()
        for category, item_id in selected:
            if item_id not in local[category]:
                raise Error('Referenced experience history is missing locally.')
            chain = local[category][item_id]
            needed.extend(digest(r) for r in chain)
            manifests.append(seal({'schema_version': SCHEMA, 'project_id': keeper.project()['project_id'],
                                   'category': category, 'id': item_id, 'history': [digest(r) for r in chain]}))
        selected_entities = set(selected)
        needed.extend(node for node, record in pool.items()
                      if ('tasks' if 'state' in record else 'experiences', key(record)) in selected_entities)
        while needed:
            node = needed.pop()
            if node in visited:
                continue
            if node not in pool:
                raise Error('Required source ancestry is unavailable; recover it before sharing.')
            record = pool[node]
            validate_record(record)
            visited.add(node)
            needed.extend(record.get('parents', []))
        # Validate everything before publishing immutable manifests. No mutable head pointer is shared.
        remaining = {node: set(pool[node].get('parents', [])) for node in visited}
        children = {}
        for node, parents in remaining.items():
            for parent in parents:
                children.setdefault(parent, []).append(node)
        ready = sorted(node for node, parents in remaining.items() if not parents)
        written = set()
        while ready:
            node = ready.pop()
            store(keeper.path('shared', 'objects', node + '.json'), pool[node])
            written.add(node)
            for child in children.get(node, []):
                remaining[child].discard(node)
                if not remaining[child]:
                    ready.append(child)
        if written != visited:
            raise Error('Cyclic source ancestry cannot be shared.')
        for manifest in manifests:
            store(keeper.path('shared', 'heads', digest(manifest) + '.json'), manifest)
        return {'status': 'shared', 'entities': [{'category': c, 'id': i} for c, i in selected],
                'objects': len(visited), 'git_paths': [DATA_DIR + '/project.json', DATA_DIR + '/.gitignore',
                                                    DATA_DIR + '/shared/', '.gitignore'],
                'note': 'Review and commit these files with Git; no commit, upload or push was performed.'}


def make_bundle(keeper, destination):
    pool, tips, graph = load(keeper)
    state = inspect(keeper)
    selected = {}
    for row in state['entities']:
        if row['outstanding']:
            # One frontier per entity per round; repeated sync incorporates further forks explicitly.
            selected[(row['category'], row['id'])] = tips[(row['category'], row['id'])][row['outstanding'][0]]
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        def write(name, value):
            archive.writestr(DATA_DIR + '/' + name, json.dumps(value, ensure_ascii=False))
        write('project.json', keeper.project())
        for (category, item_id), chain in selected.items():
            write('%s/%s/meta.json' % (category, item_id), {'revision': chain[-1]['revision']})
            folder, filename = ('checkpoints', 'state.json') if category == 'tasks' else ('versions', 'experience.json')
            for record in chain:
                write('%s/%s/%s/%06d/%s' % (category, item_id, folder, record['revision'], filename), record)
        # The entire shared pool binds the plan to the reviewed shared snapshot.
        included = {digest(record) for chain in selected.values() for record in chain}
        for node, record in pool.items():
            if node not in included:
                write('objects/' + node + '.json', record)
    return state, selected


def sync(keeper, apply=False, expected_plan=None, resolutions=None):
    if apply and expected_plan:
        if len(expected_plan) != 64 or any(c not in '0123456789abcdef' for c in expected_plan):
            raise Error('Invalid sync plan token.')
        cached = keeper.path('sync', expected_plan + '.zip')
        if cached.exists():
            result = keeper.merge(cached, True, expected_plan, resolutions)
            return {**result, 'shared': inspect(keeper), 'next': 'Preview sync again for any remaining heads.'}
    with tempfile.TemporaryDirectory(prefix='ck-sync-') as temporary:
        bundle = Path(temporary) / 'shared.zip'
        state, selected = make_bundle(keeper, bundle)
        if not selected:
            return {'status': 'up_to_date', 'shared': state}
        report = keeper.merge(bundle, resolutions=resolutions)
        report.update(shared=state, selected=[{'category': c, 'id': i, 'head': digest(chain[-1])}
                                              for (c, i), chain in selected.items()],
                      next='Apply this plan, then preview sync again until up_to_date; share reconciled results deliberately.')
        if not apply:
            return report
        if expected_plan != report['plan']:
            raise Error('Sync plan changed or missing; preview the current shared records again.')
        if report['conflicts']:
            raise Error('Unresolved shared conflicts; provide --resolutions before applying.')
        # Cache the exact reviewed archive for recovery even if Git receives additional shared records.
        cache = keeper.path('sync', expected_plan + '.zip')
        with lock(keeper.directory):
            cache.parent.mkdir(parents=True, exist_ok=True)
            if not cache.exists():
                descriptor, temporary_path = tempfile.mkstemp(prefix='.pending-', dir=str(cache.parent))
                try:
                    with os.fdopen(descriptor, 'wb') as target:
                        target.write(bundle.read_bytes())
                        target.flush()
                        os.fsync(target.fileno())
                    os.replace(temporary_path, cache)
                finally:
                    if os.path.exists(temporary_path):
                        os.unlink(temporary_path)
        result = keeper.merge(cache, True, expected_plan, resolutions)
        return {**result, 'shared': inspect(keeper), 'next': report['next']}
