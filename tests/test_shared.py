"""Real Git clone/branch scenarios for repository-carried context."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/context-keeper/scripts'))
from ck import Keeper
from _store import Error, digest, read_json
from _shared import ROOT_RULES


def git(root, *args, check=True):
    return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Test',
                           '-c', 'user.email=test@local', '-C', str(root), *args],
                          check=check, capture_output=True, text=True)


class SharedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'alice'; self.root.mkdir()
        git(self.root, 'init', '-b', 'main')
        (self.root / 'app.py').write_text('pass\n')
        self.a = Keeper(self.root); self.a.init()
        git(self.root, 'add', 'app.py', '.gitignore', '.context-keeper/.gitignore', '.context-keeper/project.json')
        git(self.root, 'commit', '-m', 'initialize project identity')

    def tearDown(self):
        self.temp.cleanup()

    def save(self, keeper, task_id='shared', key='initial', revision=0, **changes):
        return keeper.save({'idempotency_key': key, 'expected_revision': revision,
                            'title': 'Shared feature', 'goal': 'Deliver', **changes}, task_id)

    def commit(self, root, message='shared context'):
        git(root, 'add', '.gitignore', '.context-keeper')
        git(root, 'commit', '-m', message)

    def clone(self, name='bob'):
        destination = self.base / name
        git(self.root, 'clone', str(self.root), str(destination))
        return Keeper(destination)

    def sync_all(self, keeper):
        for _ in range(10):
            plan = keeper.sync()
            if plan['status'] == 'up_to_date':
                return
            self.assertEqual(plan['conflicts'], [])
            keeper.sync(True, plan['plan'])
        self.fail('Sync did not converge')

    def test_clone_automatically_reuses_project_identity(self):
        b = self.clone()
        before = b.project()
        b.init()
        self.assertEqual(before, b.project())
        self.assertEqual(b.project()['project_id'], self.a.project()['project_id'])
        self.assertEqual(git(b.root, 'status', '--porcelain').stdout, '')

    def test_only_explicit_shared_data_is_trackable(self):
        self.save(self.a)
        self.save(self.a, 'private', 'private', goal='private-sentinel')
        self.a.share('shared')
        ignored = ['tasks/shared/meta.json', '.lock', 'INDEX.md', 'objects/abc.json',
                   'merges/anything/transaction.json', 'sync/plan.zip', 'shared/private.txt']
        for relative in ignored:
            self.assertEqual(git(self.root, 'check-ignore', '.context-keeper/' + relative, check=False).returncode, 0, relative)
        self.commit(self.root)
        tracked = git(self.root, 'ls-files', '.context-keeper').stdout.splitlines()
        self.assertTrue(any('/shared/objects/' in name for name in tracked))
        self.assertFalse(any('/tasks/' in name or '/merges/' in name for name in tracked))
        for name in tracked:
            self.assertNotIn('private-sentinel', (self.root / name).read_text())

    def test_shared_task_is_discovered_and_materialized_after_clone(self):
        self.save(self.a)
        self.a.share('shared'); self.commit(self.root)
        b = self.clone()
        self.assertEqual(b.status()['tasks'], [])
        self.assertTrue(b.status()['shared']['needs_sync'])
        self.assertEqual(b.status()['shared']['entities'][0]['title'], 'Shared feature')
        self.sync_all(b)
        self.assertEqual(b.record('tasks', 'shared')['state']['goal'], 'Deliver')
        self.assertFalse(b.status()['shared']['needs_sync'])
        self.assertEqual(git(b.root, 'status', '--porcelain').stdout, '')

    def test_parallel_same_revision_git_merges_without_path_collisions(self):
        self.save(self.a); self.a.share('shared'); self.commit(self.root)
        b = self.clone(); self.sync_all(b)
        self.save(self.a, key='alice', revision=1, done=['UI'])
        self.a.share('shared'); self.commit(self.root, 'UI context')
        self.save(b, key='bob', revision=1, pending=['API'])
        b.share('shared'); self.commit(b.root, 'API context')
        git(self.root, 'fetch', str(b.root), 'main')
        git(self.root, 'merge', '--no-edit', 'FETCH_HEAD')
        self.assertEqual(git(self.root, 'diff', '--name-only', '--diff-filter=U').stdout, '')
        self.assertEqual(self.a.status()['shared']['forks'], 1)
        self.assertIn('shared_context_pending', [row['field'] for row in self.a.resume('shared')['mismatches']])
        self.sync_all(self.a)
        record = self.a.record('tasks', 'shared')
        self.assertEqual(record['state']['done'], ['UI'])
        self.assertEqual(record['state']['pending'], ['API'])
        self.a.share('shared'); self.commit(self.root, 'reconciled context')
        c = self.clone('third'); self.sync_all(c)
        self.assertEqual(c.record('tasks', 'shared')['state']['done'], ['UI'])
        self.assertEqual(c.status()['shared']['forks'], 0)

    def test_same_field_conflict_requires_resolution_after_git_merge(self):
        self.save(self.a); self.a.share('shared'); self.commit(self.root)
        b = self.clone(); self.sync_all(b)
        self.save(self.a, key='alice', revision=1, goal='UI')
        self.a.share('shared'); self.commit(self.root, 'UI goal')
        self.save(b, key='bob', revision=1, goal='API')
        b.share('shared'); self.commit(b.root, 'API goal')
        git(self.root, 'fetch', str(b.root), 'main'); git(self.root, 'merge', '--no-edit', 'FETCH_HEAD')
        plan = self.a.sync()
        self.assertEqual(plan['conflicts'][0]['id'], 'tasks/shared/goal')
        with self.assertRaisesRegex(Error, 'Unresolved'):
            self.a.sync(True, plan['plan'])
        resolutions = {'tasks/shared/goal': {'take': 'incoming'}}
        self.a.sync(True, plan['plan'], resolutions)
        self.assertEqual(self.a.record('tasks', 'shared')['state']['goal'], 'API')
        self.assertEqual(self.a.sync()['status'], 'up_to_date')

    def test_fresh_clone_with_two_heads_syncs_in_rounds(self):
        self.save(self.a); self.a.share('shared'); self.commit(self.root)
        b = self.clone(); self.sync_all(b)
        self.save(self.a, key='alice', revision=1, done=['UI'])
        self.a.share('shared'); self.commit(self.root)
        self.save(b, key='bob', revision=1, pending=['API'])
        b.share('shared'); self.commit(b.root)
        git(self.root, 'fetch', str(b.root), 'main'); git(self.root, 'merge', '--no-edit', 'FETCH_HEAD')
        c = self.clone('third'); self.sync_all(c)
        self.assertEqual(c.record('tasks', 'shared')['state']['done'], ['UI'])
        self.assertEqual(c.record('tasks', 'shared')['state']['pending'], ['API'])

    def test_distinct_task_ids_and_retry_key(self):
        first = self.a.new_task('UI', 'Build UI', 'unique-request-1')
        retry = self.a.new_task('UI', 'Build UI', 'unique-request-1')
        second = self.a.new_task('UI', 'Build UI')
        self.assertEqual(first['task_id'], retry['task_id'])
        self.assertNotEqual(first['task_id'], second['task_id'])
        self.assertTrue(retry['idempotent_retry'])

    def test_sync_stale_plan_is_rejected(self):
        self.save(self.a); self.a.share('shared'); self.commit(self.root)
        b = self.clone()
        plan = b.sync()
        self.save(self.a, 'new', 'new'); self.a.share('new'); self.commit(self.root)
        git(b.root, 'pull', '--ff-only')
        with self.assertRaisesRegex(Error, 'plan changed'):
            b.sync(True, plan['plan'])
        self.assertEqual(b.status()['tasks'], [])

    def test_sync_interruption_uses_original_cached_snapshot(self):
        self.save(self.a); self.a.share('shared'); self.commit(self.root)
        b = self.clone(); plan = b.sync()
        with mock.patch.object(b, 'publish_pointer', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                b.sync(True, plan['plan'])
        self.save(self.a, 'new', 'new'); self.a.share('new'); self.commit(self.root)
        git(b.root, 'pull', '--ff-only')
        b.sync(True, plan['plan'])
        self.assertEqual(b.record('tasks', 'shared')['revision'], 1)
        self.sync_all(b)
        self.assertEqual({row['id'] for row in b.status()['tasks']}, {'shared', 'new'})

    def test_shared_files_are_immutable_and_project_mismatch_rejected(self):
        self.save(self.a); self.a.share('shared'); self.commit(self.root)
        path = next(self.a.path('shared', 'objects').iterdir())
        original = path.read_bytes()
        record = read_json(path); record['state']['goal'] = 'changed'
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(Error, 'integrity'):
            self.a.status()
        path.write_bytes(original)
        self.a.share('shared')
        self.assertEqual(path.read_bytes(), original)
        config = self.a.project(); config['project_id'] = 'another-project'
        self.a.path('project.json').write_text(json.dumps(config))
        with self.assertRaises(Error):
            self.a.sync()

    def test_independent_new_tasks_from_two_clones_are_both_discovered(self):
        b = self.clone()
        left = self.a.new_task('UI', 'Build frontend')
        right = b.new_task('API', 'Build backend')
        self.assertNotEqual(left['task_id'], right['task_id'])
        self.a.share(left['task_id']); self.commit(self.root)
        b.share(right['task_id']); self.commit(b.root)
        git(self.root, 'fetch', str(b.root), 'main'); git(self.root, 'merge', '--no-edit', 'FETCH_HEAD')
        self.sync_all(self.a)
        self.assertEqual({r['id'] for r in self.a.status()['tasks']}, {left['task_id'], right['task_id']})

    def test_legacy_history_uuid_and_records_survive_first_share(self):
        from _store import seal
        self.save(self.a)
        metadata = self.a.project()
        path = self.a.path('tasks', 'shared', 'checkpoints', '000001', 'state.json')
        record = read_json(path); record.pop('integrity'); record.pop('parents')
        record['version'] = '0.3.1'
        record = seal(record)
        path.write_text(json.dumps(record))
        self.a.init()
        self.a.share('shared'); self.commit(self.root)
        b = self.clone(); self.sync_all(b)
        self.assertEqual(b.project()['project_id'], metadata['project_id'])
        self.assertEqual(b.record('tasks', 'shared'), record)
        self.assertEqual(self.a.record('tasks', 'shared'), record)

    def test_object_publication_crash_leaves_no_broken_visible_head(self):
        import _shared
        self.save(self.a)
        self.save(self.a, key='second', revision=1, done=['UI'])
        original = _shared.store
        calls = []
        def interrupted(path, record):
            if len(calls) == 1:
                raise OSError('power loss')
            calls.append(path)
            return original(path, record)
        with mock.patch.object(_shared, 'store', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.a.share('shared')
        self.assertEqual(self.a.status()['shared']['entities'], [])
        self.a.share('shared'); self.commit(self.root)
        b = self.clone(); self.sync_all(b)
        self.assertEqual(b.record('tasks', 'shared')['state']['done'], ['UI'])

    def test_shared_withdrawn_experience_is_not_recalled_until_sync(self):
        self.a.remember({'idempotency_key': 'rule', 'expected_version': 0,
                         'scope': 'project', 'kind': 'preference', 'content': 'Use pagination',
                         'conditions': 'List APIs'}, 'rule')
        self.a.experience_state('rule', 'active', 1, 'explicit confirmation')
        self.a.share(all_tasks=True); self.commit(self.root)
        b = self.clone(); self.sync_all(b)
        self.assertEqual(len(b.recall('pagination')), 1)
        self.a.experience_state('rule', 'invalid', 2, 'withdrawn')
        self.a.share(all_tasks=True); self.commit(self.root)
        git(b.root, 'pull', '--ff-only')
        self.assertEqual(b.recall('pagination'), [])
        self.sync_all(b)
        self.assertEqual(b.record('experiences', 'rule')['status'], 'invalid')

    def test_memory_only_commit_preserves_code_fingerprint_and_checks(self):
        before = self.a.inspect()
        self.save(self.a, checks=[{'command': 'test-app', 'observed_at': '2026-10-20T00:00:00Z',
                                  'exit_code': 0, 'workspace_fingerprint': before['fingerprint']}])
        self.a.share('shared'); self.commit(self.root)
        after = self.a.inspect()
        self.assertNotEqual(before['commit'], after['commit'])
        self.assertEqual(before['code_tree'], after['code_tree'])
        self.assertEqual(before['fingerprint'], after['fingerprint'])
        self.assertEqual(self.a.resume('shared')['status'], 'context_checked')
        self.assertEqual(self.a.resume('shared')['checks_needing_rerun'], [])
        (self.root / 'app.py').write_text('value = 2\n')
        git(self.root, 'add', 'app.py'); git(self.root, 'commit', '-m', 'code change')
        self.assertNotEqual(after['code_tree'], self.a.inspect()['code_tree'])
        self.assertEqual(self.a.resume('shared')['status'], 'partial')

    def test_existing_ignore_rules_and_private_history_are_preserved(self):
        original = (self.root / '.gitignore').read_text()
        (self.root / '.gitignore').write_text('secret.env\n.context-keeper/\n' + original)
        self.save(self.a)
        record = self.a.record('tasks', 'shared')
        self.a.init(); self.a.init()
        text = (self.root / '.gitignore').read_text()
        self.assertTrue(text.startswith('secret.env\n.context-keeper/\n'))
        self.assertEqual(text.count(ROOT_RULES.splitlines()[0]), 1)
        self.assertEqual(self.a.record('tasks', 'shared'), record)
        self.assertNotEqual(git(self.root, 'check-ignore', '.context-keeper/project.json', check=False).returncode, 0)
        self.assertEqual(git(self.root, 'check-ignore', '.context-keeper/tasks/shared/meta.json', check=False).returncode, 0)


if __name__ == '__main__':
    unittest.main()
