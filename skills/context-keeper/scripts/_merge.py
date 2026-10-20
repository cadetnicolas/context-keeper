"""Offline three-way context merge. No code execution or archive extraction."""
import copy
from collections import deque
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath
from uuid import uuid4

from _store import (DATA_DIR, SCHEMA, VERSION, Error, atomic_write, digest,
                    identity, immutable_directory, lock, now, read_json, seal, verify)

CATEGORIES = ('tasks', 'experiences')
MAX_BYTES = 32 * 1024 * 1024


def key(record):
    return record['task_id'] if 'state' in record else record['id']


def validate_record(record):
    from ck import validate_task, validate_experience
    from _store import integer, text, texts
    integer(record['revision'], 'revision', 1)
    identity(key(record))
    text(record['created_at'], 'created_at', 128)
    parents = record.get('parents', [])
    if not isinstance(parents, list) or len(parents) > 2 or any(
            not isinstance(p, str) or len(p) != 64 or any(c not in '0123456789abcdef' for c in p) for p in parents):
        raise Error('Invalid record ancestry.')
    if 'state' in record:
        raw = copy.deepcopy(record['state'])
        raw.update(idempotency_key='merge-validation', expected_revision=0)
        raw['capture'].pop('complete', None)
        for evidence in raw['evidence']:
            evidence.pop('verification', None)
        for check in raw['checks']:
            check.pop('verification', None)
            check.pop('matches_checkpoint', None)
        validate_task(raw)
        text(record['original_goal'], 'original_goal')
        texts(record['warnings'], 'warnings', 1000)
        workspace = record['workspace']
        if not isinstance(workspace, dict) or workspace.get('project_id') != record['project_id']:
            raise Error('Invalid source workspace identity.')
        if workspace.get('mode') not in ('git', 'directory'):
            raise Error('Invalid source workspace mode.')
        for name in ('fingerprint', 'branch', 'commit', 'limitations'):
            if name not in workspace:
                raise Error('Missing source workspace field.')
        texts(workspace['limitations'], 'workspace limitations', 1000)
        if not isinstance(record['artifacts'], list) or len(record['artifacts']) > 100:
            raise Error('Invalid source artifacts.')
        for artifact in record['artifacts']:
            path = PurePosixPath(artifact['path'])
            if (not artifact['path'] or path.is_absolute() or '\\' in artifact['path']
                    or any(p in ('.git', '.hg', '.svn', DATA_DIR, '..') for p in path.parts)
                    or not isinstance(artifact['available'], bool)):
                raise Error('Invalid source artifact path or availability.')
            if artifact['available'] and (not isinstance(artifact['sha256'], str) or len(artifact['sha256']) != 64):
                raise Error('Invalid source artifact hash.')
    else:
        raw = {name: record.get(name) for name in
               ('content', 'conditions', 'scope', 'task_id', 'kind', 'evidence_refs')}
        raw.update(idempotency_key='merge-validation', expected_version=0)
        validate_experience(raw)
        if record['status'] not in ('proposed', 'active', 'disputed', 'invalid', 'archived'):
            raise Error('Invalid source experience status.')


def histories(keeper):
    return {category: {key(head): [keeper.record(category, key(head), n)
                                  for n in range(1, head['revision'] + 1)]
                       for head in keeper.entries(category)} for category in CATEGORIES}


def objects(keeper):
    result = {}
    directory = keeper.path('objects')
    if directory.exists():
        for path in directory.iterdir():
            record = verify(read_json(keeper.path('objects', path.name)))
            if path.name != digest(record) + '.json' or record['project_id'] != keeper.project()['project_id']:
                raise Error('Invalid merge history object.')
            result[digest(record)] = record
    return result


def pending(keeper):
    directory = keeper.path('merges')
    if not directory.exists():
        return []
    return [entry.name for entry in sorted(directory.iterdir())
            if keeper.path('merges', entry.name, 'transaction.json').exists()
            and not keeper.path('merges', entry.name, 'receipt.json').exists()]


def ensure_ready(keeper):
    if pending(keeper):
        raise Error('An interrupted merge is pending; retry merge with its original --plan before writing.')


def read_bundle(source, project_id):
    source = Path(source).expanduser()
    files = {}
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > 10000 or sum(e.file_size for e in entries) > MAX_BYTES:
                raise Error('Context archive exceeds 10,000 entries or 32 MiB.')
            seen = set()
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (entry.filename in seen or path.is_absolute() or '..' in path.parts
                        or path.as_posix() != entry.filename.rstrip('/')
                        or '\\' in entry.filename or not path.parts or path.parts[0] != DATA_DIR
                        or stat.S_ISLNK(entry.external_attr >> 16)):
                    raise Error('Unsafe, duplicate or symlinked context archive entry.')
                seen.add(entry.filename)
                if entry.is_dir():
                    continue
                if entry.file_size > 2 * 1024 * 1024:
                    raise Error('Context archive member exceeds 2 MiB.')
                files[path.relative_to(DATA_DIR).as_posix()] = archive.read(entry)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise Error('Invalid or unsupported context ZIP.') from error
    def load(name):
        if name not in files:
            raise Error('Context archive is missing required metadata/history: ' + name)
        return json.loads(files[name].decode('utf-8'))
    project = load('project.json')
    if project.get('schema_version') != SCHEMA or project.get('project_id') != project_id:
        raise Error('Project identity differs. Share the original project.json before parallel development; do not relabel histories.')
    data = {category: {} for category in CATEGORIES}
    all_objects = {}
    for name in files:
        parts = name.split('/')
        if len(parts) == 3 and parts[0] in CATEGORIES and parts[2] == 'meta.json':
            category, item_id = parts[:2]
            identity(item_id)
            meta = load(name)
            revision = meta.get('revision')
            if isinstance(revision, bool) or not isinstance(revision, int) or not 1 <= revision <= 10000:
                raise Error('Invalid source revision.')
            records = []
            for n in range(1, revision + 1):
                folder, filename = ('checkpoints', 'state.json') if category == 'tasks' else ('versions', 'experience.json')
                record = verify(load('%s/%s/%s/%06d/%s' % (category, item_id, folder, n, filename)))
                if (record['project_id'] != project_id or key(record) != item_id or record['revision'] != n
                        or ('state' in record) != (category == 'tasks')):
                    raise Error('Source record identity does not match its path.')
                validate_record(record)
                records.append(record)
                all_objects[digest(record)] = record
            data[category][item_id] = records
        elif len(parts) == 2 and parts[0] == 'objects':
            record = verify(load(name))
            if parts[1] != digest(record) + '.json' or record['project_id'] != project_id:
                raise Error('Invalid source history object.')
            validate_record(record)
            all_objects[digest(record)] = record
    for record in all_objects.values():
        for parent in record.get('parents', []):
            if parent in all_objects and (key(all_objects[parent]) != key(record)
                                         or ('state' in all_objects[parent]) != ('state' in record)):
                raise Error('Ancestry links unrelated tasks or experiences.')
    if not any(data.values()):
        raise Error('Archive contains no task or experience history.')
    return data, all_objects


def payload(category, record):
    if category == 'experiences':
        return {name: record.get(name) for name in
                ('content', 'conditions', 'scope', 'task_id', 'kind', 'evidence_refs', 'status')}
    state = copy.deepcopy(record['state'])
    # Evidence IDs have meaning only together with claims and checks from that source.
    claims = {name: state.pop(name) for name in ('evidence', 'constraints', 'decisions', 'attempts', 'checks')}
    state['claims'] = claims
    state['artifacts'] = copy.deepcopy(record['artifacts'])
    state['original_goal'] = record['original_goal']
    return state


def graph_records(data, pool):
    parents = {}
    for record_hash, record in pool.items():
        parents[record_hash] = list(record.get('parents', []))
    for category in CATEGORIES:
        for records in data[category].values():
            for index, record in enumerate(records):
                record_hash = digest(record)
                parents.setdefault(record_hash, [])
                if index and not record.get('parents'):
                    parents[record_hash] = [digest(records[index - 1])]
    return parents


def ancestors(head, graph):
    found, queue = {}, deque([(head, 0)])
    while queue:
        node, distance = queue.popleft()
        if node in found:
            continue
        found[node] = distance
        if len(found) > 20000:
            raise Error('Context ancestry exceeds the inspection limit.')
        queue.extend((parent, distance + 1) for parent in graph.get(node, []))
    return found


def preview(keeper, incoming, pool, resolutions):
    local = histories(keeper)
    own_objects = objects(keeper)
    pool = {**pool, **own_objects}
    for data in (local, incoming):
        for category in CATEGORIES:
            for records in data[category].values():
                pool.update((digest(r), r) for r in records)
    graph = graph_records(incoming, pool)
    for node, parents in graph_records(local, pool).items():
        if parents:
            graph[node] = parents
    basis = {category: {item_id: digest(records[-1]) for item_id, records in local[category].items()}
             for category in CATEGORIES}
    source_heads = {category: {item_id: digest(records[-1]) for item_id, records in incoming[category].items()}
                    for category in CATEGORIES}
    plan_id = digest({'project': keeper.project()['project_id'], 'local': basis,
                      'incoming': source_heads, 'objects': sorted(pool)})
    actions, conflicts, used = [], [], set()
    for category in CATEGORIES:
        for item_id, records in sorted(incoming[category].items()):
            remote = records[-1]
            local_records = local[category].get(item_id, [])
            if not local_records:
                actions.append({'category': category, 'id': item_id, 'kind': 'import', 'records': records})
                continue
            ours = local_records[-1]
            ours_hash, theirs_hash = digest(ours), digest(remote)
            ours_anc = ancestors(ours_hash, graph)
            theirs_anc = ancestors(theirs_hash, graph)
            if theirs_hash in ours_anc:
                continue
            common = set(ours_anc) & set(theirs_anc)
            # Multiple incomparable merge bases need human reconciliation; do not guess a base.
            older, queue = set(), [parent for node in common for parent in graph.get(node, [])]
            while queue:
                node = queue.pop()
                if node not in older:
                    older.add(node)
                    queue.extend(graph.get(node, []))
            bases = sorted(common - older)
            base = pool[bases[0]] if len(bases) == 1 else None
            if (len(records) >= len(local_records)
                    and all(digest(a) == digest(b) for a, b in zip(local_records, records))):
                actions.append({'category': category, 'id': item_id, 'kind': 'fast_forward',
                                'records': records[len(local_records):]})
                continue
            left, right = payload(category, ours), payload(category, remote)
            if left == right:
                continue
            ancestor = payload(category, base) if base else {}
            merged = {}
            for field in sorted(left):
                lvalue, rvalue = left[field], right[field]
                if lvalue == rvalue:
                    merged[field] = lvalue
                elif base and lvalue == ancestor[field]:
                    merged[field] = rvalue
                elif base and rvalue == ancestor[field]:
                    merged[field] = lvalue
                else:
                    conflict_id = '%s/%s/%s' % (category, item_id, field)
                    if conflict_id in resolutions:
                        choice = resolutions[conflict_id]
                        if not isinstance(choice, dict) or set(choice) != {'take'} or choice['take'] not in ('local', 'incoming'):
                            raise Error('Resolution must be {"take":"local"} or {"take":"incoming"}.')
                        merged[field] = lvalue if choice['take'] == 'local' else rvalue
                        used.add(conflict_id)
                    else:
                        conflicts.append({'id': conflict_id, 'base': ancestor.get(field),
                                          'local': lvalue, 'incoming': rvalue,
                                          'reason': 'both_changed' if base else 'no_unique_common_ancestor'})
                        merged[field] = lvalue
            actions.append({'category': category, 'id': item_id, 'kind': 'merge',
                            'local': ours, 'incoming': remote, 'payload': merged,
                            'base': digest(base) if base else None})
    if set(resolutions) - used:
        raise Error('Resolution names an unknown or no-longer-conflicting field; preview again.')
    return plan_id, actions, conflicts, pool


def materialize(keeper, action):
    from ck import validate_task, validate_experience
    category = action['category']
    if action['kind'] != 'merge':
        records = action['records']
    else:
        ours, remote, merged = action['local'], action['incoming'], copy.deepcopy(action['payload'])
        parents = [digest(ours), digest(remote)]
        record = {name: value for name, value in ours.items() if name != 'integrity'}
        record.update(version=VERSION, revision=ours['revision'] + 1, created_at=now(), parents=parents,
                      request_key='merge-' + str(uuid4()), request_hash=digest(merged),
                      merge={'base': action['base'], 'source_head': digest(remote),
                             'verification': 'merged context; workspace and checks require reconciliation'})
        if category == 'tasks':
            record['artifacts'] = merged.pop('artifacts')
            record['original_goal'] = merged.pop('original_goal')
            merged.update(merged.pop('claims'))
            # Normalize through the save contract; never stamp current workspace over remote evidence.
            raw = copy.deepcopy(merged)
            raw.update(idempotency_key=record['request_key'], expected_revision=ours['revision'])
            raw['capture'].pop('complete', None)
            for check in raw['checks']:
                check.pop('verification', None)
                check.pop('matches_checkpoint', None)
            for evidence in raw['evidence']:
                evidence.pop('verification', None)
            normalized = validate_task(raw)
            record['state'] = {k: v for k, v in normalized.items()
                               if k not in ('idempotency_key', 'expected_revision', 'artifact_paths')}
            for check in record['state']['checks']:
                check['matches_checkpoint'] = False
            record['warnings'] = list(dict.fromkeys(ours['warnings'] + remote['warnings'] +
                ['Merged context from parallel work; reconcile code and rerun checks before relying on it.']))
        else:
            raw = {k: v for k, v in merged.items() if k != 'status'}
            raw.update(idempotency_key=record['request_key'], expected_version=ours['revision'])
            validate_experience(raw)
            record.update(merged)
            # Combining competing experience content is a new proposal, not a new confirmation.
            record['status'] = 'proposed'
            record.pop('confirmation', None)
            record.pop('note', None)
        records = [seal(record)]
    return {'category': category, 'id': action['id'], 'kind': action['kind'], 'records': records}


def apply_transaction(keeper, plan_id, transaction):
    for action in transaction['actions']:
        category, item_id = action['category'], action['id']
        for record in action['records']:
            meta = keeper.path(category, item_id, 'meta.json')
            revision = read_json(meta)['revision'] if meta.exists() else 0
            if revision >= record['revision']:
                if digest(keeper.record(category, item_id, record['revision'])) != digest(record):
                    raise Error('Merge recovery found a different committed revision; preserve data and inspect.')
                continue
            if revision + 1 != record['revision']:
                raise Error('Merge recovery found a revision gap.')
            # Recover a crash after immutable publication but before pointer publication.
            folder, filename = ('checkpoints', 'state.json') if category == 'tasks' else ('versions', 'experience.json')
            path = keeper.path(category, item_id, folder, '%06d' % record['revision'], filename)
            if path.exists():
                if digest(verify(read_json(path))) != digest(record):
                    raise Error('An immutable revision already contains different data.')
                keeper.publish_pointer(category, item_id, record)
            else:
                keeper.publish(category, item_id, record)
    keeper.reindex()
    receipt = {'plan': plan_id, 'status': 'merged', 'completed_at': now(),
               'actions': [{'category': a['category'], 'id': a['id'], 'kind': a['kind']} for a in transaction['actions']],
               'note': 'Context merged locally; code and semantic agreement are not verified.'}
    atomic_write(keeper.path('merges', plan_id, 'receipt.json'), receipt)
    return receipt


def merge(keeper, source, apply=False, expected_plan=None, resolutions=None):
    resolutions = resolutions or {}
    if not isinstance(resolutions, dict):
        raise Error('Resolutions must be an object keyed by conflict ID.')
    project_id = keeper.project()['project_id']
    try:
        incoming, pool = read_bundle(source, project_id)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise Error('Invalid source context structure; verify the export contract.') from error
    source_signature = digest({'history': sorted(pool), 'heads': {
        c: {i: digest(r[-1]) for i, r in incoming[c].items()} for c in CATEGORIES}})
    with lock(keeper.directory):
        if apply and expected_plan:
            if len(expected_plan) != 64 or any(c not in '0123456789abcdef' for c in expected_plan):
                raise Error('Invalid merge plan token.')
            transaction_path = keeper.path('merges', expected_plan, 'transaction.json')
            if transaction_path.exists():
                transaction = verify(read_json(transaction_path))
                if transaction['source'] != source_signature or transaction['resolutions'] != resolutions:
                    raise Error('Merge retry must use the original source and resolutions.')
                receipt = keeper.path('merges', expected_plan, 'receipt.json')
                return read_json(receipt) if receipt.exists() else apply_transaction(keeper, expected_plan, transaction)
        ensure_ready(keeper)
        plan_id, actions, conflicts, pool = preview(keeper, incoming, pool, resolutions)
        report = {'plan': plan_id, 'status': 'conflicts' if conflicts else 'ready', 'conflicts': conflicts,
                  'actions': [{'category': a['category'], 'id': a['id'], 'kind': a['kind']} for a in actions],
                  'note': 'Arrays are atomic fields. Different task IDs stay separate; cross-task semantic conflicts need review.'}
        if not apply:
            return report
        if expected_plan != plan_id:
            raise Error('Merge plan changed or missing; preview and pass its current --plan token.')
        if conflicts:
            raise Error('Unresolved conflicts; preview and provide --resolutions before applying.')
        prepared = [materialize(keeper, action) for action in actions]
        for action in prepared:
            for record in action['records']:
                validate_record(record)
                folder, filename = ('checkpoints', 'state.json') if action['category'] == 'tasks' else ('versions', 'experience.json')
                target = keeper.path(action['category'], action['id'], folder, '%06d' % record['revision'], filename)
                if target.exists() and digest(verify(read_json(target))) != digest(record):
                    raise Error('An unpublished immutable revision contains different data; recover its original write first.')
        transaction = seal({'schema_version': SCHEMA, 'project_id': project_id, 'plan': plan_id,
                            'source': source_signature,
                            'resolutions': resolutions, 'actions': prepared})
        def stored_size(value):
            return len((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        if stored_size(transaction) > 2 * 1024 * 1024:
            raise Error('Merge transaction exceeds 2 MiB; export smaller task batches.')
        if any(stored_size(record) > 2 * 1024 * 1024 for record in pool.values()):
            raise Error('A source history object exceeds the stored JSON size limit.')
        # Preserve both source histories as content-addressed immutable objects before changing heads.
        for object_hash, record in pool.items():
            path = keeper.path('objects', object_hash + '.json')
            if path.exists():
                if digest(verify(read_json(path))) != object_hash:
                    raise Error('Stored history object was modified.')
            else:
                atomic_write(path, record)
        immutable_directory(keeper.path('merges', plan_id), {'transaction.json': transaction})
        return apply_transaction(keeper, plan_id, transaction)
