"""Distributed context scenarios, using independent clones and exported bundles."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/context-keeper/scripts'))
from ck import Keeper
from _store import Error, digest, read_json, seal


def task(key='initial', revision=0, **extra):
    return {'idempotency_key': key, 'expected_revision': revision, 'title': 'Shared feature', 'goal': 'Deliver feature', **extra}


def experience(key='rule', revision=0, **extra):
    return dict(idempotency_key=key, expected_version=revision, content='Use pagination',
                conditions='List endpoints', scope='project', kind='lesson', **extra)


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.left = self.base / 'alice'
        self.left.mkdir()
        self.a = Keeper(self.left)
        self.a.init()
        self.a.save(task(), 'shared')
        self.right = self.base / 'bob'
        shutil.copytree(self.left, self.right)
        self.b = Keeper(self.right)
        self.sequence = 0

    def tearDown(self):
        self.temp.cleanup()

    def bundle(self, keeper=None, task_id=None):
        self.sequence += 1
        path = self.base / ('context-%d.zip' % self.sequence)
        (keeper or self.b).export(task_id, path)
        return path

    def apply(self, keeper, path, resolutions=None):
        plan = keeper.merge(path, resolutions=resolutions)
        return keeper.merge(path, True, plan['plan'], resolutions)

    def test_distinct_tasks_preview_and_repeat(self):
        self.a.save(task('alice'), 'frontend')
        self.b.save(task('bob'), 'backend')
        archive = self.bundle()
        before = list(self.a.status()['tasks'])
        plan = self.a.merge(archive)
        self.assertEqual(plan['conflicts'], [])
        self.assertEqual(before, self.a.status()['tasks'])
        self.apply(self.a, archive)
        self.assertEqual({t['id'] for t in self.a.status()['tasks']}, {'shared', 'frontend', 'backend'})
        self.assertEqual(self.a.merge(archive)['actions'], [])
        # Replaying a completed transaction returns the original receipt.
        self.assertEqual(self.a.merge(archive, True, plan['plan'])['status'], 'merged')

    def test_disjoint_fields_and_preserved_remote_history(self):
        original = self.a.record('tasks', 'shared')
        self.a.save(task('alice', 1, done=['UI ready']), 'shared')
        self.b.save(task('bob', 1, pending=['API review']), 'shared')
        theirs = self.b.record('tasks', 'shared')
        self.apply(self.a, self.bundle())
        merged = self.a.record('tasks', 'shared')
        self.assertEqual(merged['revision'], 3)
        self.assertEqual(merged['state']['done'], ['UI ready'])
        self.assertEqual(merged['state']['pending'], ['API review'])
        self.assertEqual(self.a.record('tasks', 'shared', 1), original)
        self.assertEqual(read_json(self.a.path('objects', digest(theirs) + '.json')), theirs)
        self.assertIn('merged_context', [v['field'] for v in self.a.resume('shared')['mismatches']])

    def test_same_field_conflicts_and_explicit_resolution(self):
        self.a.save(task('alice', 1, goal='Ship UI'), 'shared')
        self.b.save(task('bob', 1, goal='Ship API'), 'shared')
        path = self.bundle()
        plan = self.a.merge(path)
        self.assertEqual([c['id'] for c in plan['conflicts']], ['tasks/shared/goal'])
        with self.assertRaisesRegex(Error, 'Unresolved'):
            self.a.merge(path, True, plan['plan'])
        self.assertEqual(self.a.record('tasks', 'shared')['revision'], 2)
        self.apply(self.a, path, {'tasks/shared/goal': {'take': 'incoming'}})
        self.assertEqual(self.a.record('tasks', 'shared')['state']['goal'], 'Ship API')
        self.assertEqual(self.a.record('tasks', 'shared', 2)['state']['goal'], 'Ship UI')

    def test_claims_are_not_mixed_by_evidence_id(self):
        def claims(word):
            return {'evidence': [{'id': 'request', 'kind': 'user', 'summary': word}],
                    'decisions': [{'text': word, 'evidence_ids': ['request']}]}
        self.a.save(task('alice', 1, **claims('UI')), 'shared')
        self.b.save(task('bob', 1, **claims('API')), 'shared')
        path = self.bundle()
        self.assertEqual(self.a.merge(path)['conflicts'][0]['id'], 'tasks/shared/claims')
        self.apply(self.a, path, {'tasks/shared/claims': {'take': 'incoming'}})
        self.assertEqual(self.a.record('tasks', 'shared')['state']['evidence'][0]['summary'], 'API')

    def test_stale_plan_and_unknown_resolution_are_rejected(self):
        self.b.save(task('bob'), 'api')
        path = self.bundle()
        plan = self.a.merge(path)
        self.a.save(task('alice', 1, done=['new']), 'shared')
        with self.assertRaisesRegex(Error, 'plan changed'):
            self.a.merge(path, True, plan['plan'])
        with self.assertRaisesRegex(Error, 'unknown'):
            self.a.merge(path, resolutions={'tasks/shared/goal': {'take': 'local'}})

    def test_fast_forward_and_reverse_merge_use_ancestry(self):
        self.b.save(task('bob', 1, done=['API']), 'shared')
        incoming = self.bundle()
        self.assertEqual(self.a.merge(incoming)['actions'][0]['kind'], 'fast_forward')
        self.apply(self.a, incoming)
        self.a.save(task('alice', 2, done=['API'], pending=['UI']), 'shared')
        self.apply(self.b, self.bundle(self.a))
        self.assertEqual(self.b.record('tasks', 'shared')['revision'], 3)
        self.assertEqual(self.a.merge(self.bundle())['actions'], [])

    def test_merge_back_after_fork_does_not_duplicate_or_lose_state(self):
        self.a.save(task('alice', 1, done=['UI']), 'shared')
        self.b.save(task('bob', 1, pending=['API']), 'shared')
        self.apply(self.a, self.bundle())
        self.apply(self.b, self.bundle(self.a))
        self.assertEqual(self.b.record('tasks', 'shared')['state']['done'], ['UI'])
        self.assertEqual(self.b.record('tasks', 'shared')['state']['pending'], ['API'])
        self.assertEqual(self.a.merge(self.bundle())['actions'], [])
        # A third developer can receive the merged task with all fork objects.
        third = self.base / 'third'; third.mkdir()
        c = Keeper(third); c.init(project_id=self.a.project()['project_id'])
        self.apply(c, self.bundle(self.a, 'shared'))
        self.assertTrue(c.path('objects').is_dir())
        self.assertEqual(c.record('tasks', 'shared')['state']['done'], ['UI'])

    def test_project_identity_and_no_ancestor_collision(self):
        other = self.base / 'other'; other.mkdir()
        c = Keeper(other); c.init(); c.save(task(), 'shared')
        with self.assertRaisesRegex(Error, 'identity differs'):
            self.a.merge(self.bundle(c))
        with self.assertRaisesRegex(Error, 'different identity'):
            c.init(project_id=self.a.project()['project_id'])
        # Independently created tasks with same explicit ID must not be equated by revision.
        separate = self.base / 'separate'; separate.mkdir()
        d = Keeper(separate); d.init(project_id=self.a.project()['project_id'])
        d.save(task('different', done=['other']), 'shared')
        self.assertEqual(self.a.merge(self.bundle(d))['conflicts'][0]['reason'], 'no_unique_common_ancestor')

    def test_experience_fork_requires_reconfirmation(self):
        self.a.remember(experience(), 'pagination')
        shutil.rmtree(self.right); shutil.copytree(self.left, self.right)
        self.a.remember(experience('alice', 1), 'pagination')
        self.b.experience_state('pagination', 'active', 1, 'Bob confirmed')
        self.apply(self.a, self.bundle())
        self.assertEqual(self.a.record('experiences', 'pagination')['status'], 'proposed')
        self.assertEqual(self.a.recall('pagination'), [])

    def test_interrupted_merge_is_recoverable_and_blocks_writes(self):
        self.b.save(task('bob'), 'api')
        path = self.bundle()
        plan = self.a.merge(path)
        with mock.patch.object(self.a, 'publish_pointer', side_effect=OSError('power loss')):
            with self.assertRaises(OSError):
                self.a.merge(path, True, plan['plan'])
        self.assertEqual(self.a.status()['pending_merges'], [plan['plan']])
        with self.assertRaisesRegex(Error, 'interrupted merge'):
            self.a.save(task('new'), 'new')
        self.a.merge(path, True, plan['plan'])
        self.assertEqual(self.a.status()['pending_merges'], [])
        self.assertEqual(self.a.record('tasks', 'api')['revision'], 1)

    def test_zip_traversal_duplicates_and_tampering_rejected(self):
        for names in [('../escape',), ('.context-keeper/project.json', '.context-keeper/project.json')]:
            path = self.base / ('bad-%s.zip' % len(names))
            with zipfile.ZipFile(path, 'w') as archive:
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning)
                    for name in names:
                        archive.writestr(name, '{}')
            with self.assertRaises(Error):
                self.a.merge(path)
        path = self.bundle()
        bad = self.base / 'tampered.zip'
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(bad, 'w') as target:
            for info in source.infolist():
                content = source.read(info)
                if info.filename.endswith('state.json'):
                    record = json.loads(content); record['state']['goal'] = 'tampered'
                    content = json.dumps(record).encode()
                target.writestr(info, content)
        with self.assertRaisesRegex(Error, 'integrity'):
            self.a.merge(bad)

    def test_partial_multi_item_commit_recovers_without_duplicates(self):
        self.b.save(task('api'), 'api')
        self.b.save(task('docs'), 'docs')
        path = self.bundle()
        plan = self.a.merge(path)
        original = self.a.publish
        def interrupted(category, item_id, record):
            if item_id == 'docs':
                raise OSError('interrupted second entity')
            return original(category, item_id, record)
        with mock.patch.object(self.a, 'publish', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.a.merge(path, True, plan['plan'])
        self.assertEqual(self.a.record('tasks', 'api')['revision'], 1)
        self.assertIn('pending_merge', [v['field'] for v in self.a.resume('shared')['mismatches']])
        with self.assertRaisesRegex(Error, 'interrupted merge'):
            self.a.export(None, self.base / 'partial.zip')
        self.a.merge(path, True, plan['plan'])
        self.assertEqual({r['id'] for r in self.a.status()['tasks']}, {'shared', 'api', 'docs'})
        self.assertEqual(self.a.record('tasks', 'api')['revision'], 1)

    def test_unpublished_save_collision_is_rejected_before_transaction(self):
        self.b.save(task('bob', 1, done=['API']), 'shared')
        path = self.bundle()
        with mock.patch.object(self.a, 'publish_pointer', side_effect=OSError('crash')):
            with self.assertRaises(OSError):
                self.a.save(task('alice', 1, done=['UI']), 'shared')
        plan = self.a.merge(path)
        with self.assertRaisesRegex(Error, 'unpublished immutable'):
            self.a.merge(path, True, plan['plan'])
        self.assertEqual(self.a.status()['pending_merges'], [])
        self.assertEqual(self.a.record('tasks', 'shared')['revision'], 1)
        self.a.save(task('alice', 1, done=['UI']), 'shared')
        self.assertEqual(self.a.record('tasks', 'shared')['revision'], 2)

    def test_selected_export_omits_unrelated_provenance(self):
        self.a.save(task('alice', 1, done=['UI']), 'shared')
        self.b.save(task('bob', 1, pending=['API']), 'shared')
        self.b.save(task('private', goal='private-unrelated-sentinel'), 'private')
        self.apply(self.a, self.bundle())
        exported = self.bundle(self.a, 'shared')
        with zipfile.ZipFile(exported) as archive:
            self.assertFalse(any('private/' in name for name in archive.namelist()))
            for name in archive.namelist():
                self.assertNotIn(b'private-unrelated-sentinel', archive.read(name))

    def test_stale_source_and_invalid_resolution_do_not_apply(self):
        self.a.save(task('alice', 1, done=['UI']), 'shared')
        self.b.save(task('bob', 1, done=['API']), 'shared')
        path = self.bundle()
        plan = self.a.merge(path)
        with self.assertRaisesRegex(Error, 'Resolution must'):
            self.a.merge(path, resolutions={'tasks/shared/done': {'take': 'newest'}})
        self.b.save(task('bob-next', 2, done=['API2']), 'shared')
        newer = self.bundle()
        with self.assertRaisesRegex(Error, 'plan changed'):
            self.a.merge(newer, True, plan['plan'], {'tasks/shared/done': {'take': 'incoming'}})
        self.assertEqual(self.a.record('tasks', 'shared')['state']['done'], ['UI'])

    def test_check_evidence_is_not_rebased_to_current_workspace(self):
        self.a.save(task('alice', 1, done=['UI']), 'shared')
        checks = [{'command': 'test-api', 'observed_at': '2026-10-20T00:00:00Z',
                   'workspace_fingerprint': 'remote-tested-state', 'exit_code': 0}]
        self.b.save(task('bob', 1, checks=checks), 'shared')
        self.apply(self.a, self.bundle())
        state = self.a.record('tasks', 'shared')['state']
        self.assertEqual(state['checks'][0]['workspace_fingerprint'], 'remote-tested-state')
        self.assertFalse(state['checks'][0]['matches_checkpoint'])
        self.assertIn('test-api', self.a.resume('shared')['checks_needing_rerun'])

    def test_cli_preview_and_all_export_without_site_packages(self):
        script = Path(__file__).resolve().parents[1] / 'skills/context-keeper/scripts/ck.py'
        path = self.base / 'cli.zip'
        exported = subprocess.run([sys.executable, '-S', str(script), '--workspace', str(self.right),
                                   'export', '--all', '--output', str(path)], capture_output=True, text=True)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        preview = subprocess.run([sys.executable, '-S', str(script), '--workspace', str(self.left),
                                  'merge', '--source', str(path)], capture_output=True, text=True)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(json.loads(preview.stdout)['status'], 'ready')


if __name__ == '__main__':
    unittest.main()
