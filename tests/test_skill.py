"""Skill runtime tests; run with Python's built-in unittest, no pip install."""
import contextlib
import io
import os
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'context-keeper' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from ck import Keeper, main
from _store import Error, read_json


def run_git(root, *args):
    return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(root), *args],
                          check=True, capture_output=True)


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ck-test-')
        self.base = Path(self.temp.name)
        self.root = self.base / 'project'
        self.root.mkdir()
        run_git(self.root, 'init', '-b', 'main')
        (self.root / 'app.py').write_text('value = 1\n')
        run_git(self.root, 'add', '.')
        run_git(self.root, '-c', 'user.name=Test', '-c', 'user.email=test@local', 'commit', '-m', 'initial')
        self.keeper = Keeper(self.root)
        self.keeper.init()

    def tearDown(self):
        self.temp.cleanup()

    def task(self, key='save-1', revision=0, **kwargs):
        return {'idempotency_key': key, 'expected_revision': revision, 'title': '登录兼容',
                'goal': '保留旧接口字段', 'acceptance_criteria': ['兼容性验证'], 'done': ['检查调用方'],
                'pending': ['兼容测试'], 'next_actions': ['检查差异，再做兼容测试'],
                'constraints': [{'text':'保留旧字段','evidence_ids':['user-1']}],
                'evidence':[{'id':'user-1','kind':'user','summary':'用户明确要求兼容旧客户端','locator':'source user request'}],
                **kwargs}

    def experience(self, key='exp-1', version=0, **kwargs):
        return {'idempotency_key':key, 'expected_version':version, 'scope':'project','kind':'lesson',
                'content':'登录接口变更先检查调用方', 'conditions':'旧客户端仍在支持范围', **kwargs}

    def test_no_server_roundtrip_and_local_default(self):
        saved = self.keeper.save(self.task(), 'login')
        result = self.keeper.resume('login')
        self.assertEqual(result['status'], 'context_checked')
        self.assertTrue(Path(saved['handoff']).is_file())
        self.assertIn('保留旧字段', Path(saved['handoff']).read_text())
        self.assertEqual(self.keeper.status()['tasks'][0]['id'], 'login')
        ignored = run_git(self.root, 'check-ignore', '.context-keeper/project.json')
        self.assertTrue(ignored.stdout)

    def test_metadata_changes_do_not_change_code_fingerprint(self):
        before = self.keeper.inspect()['fingerprint']
        self.keeper.save(self.task(), 'login')
        self.keeper.remember(self.experience())
        self.assertEqual(self.keeper.inspect()['fingerprint'], before)

    def test_initial_retry_is_not_another_task(self):
        first = self.keeper.save(self.task())
        second = self.keeper.save(self.task())
        self.assertEqual(first['task_id'], second['task_id'])
        self.assertTrue(second['idempotent_retry'])
        self.assertEqual(len(self.keeper.status()['tasks']), 1)

    def test_revision_cas_and_immutable_history(self):
        first = self.keeper.save(self.task(), 'login')
        immutable = Path(first['state']).read_bytes()
        with self.assertRaisesRegex(Error,'revision changed'):
            self.keeper.save(self.task(key='wrong'), 'login')
        second = self.keeper.save(self.task(key='save-2',revision=1,done=['已修改代码']), 'login')
        self.assertEqual(second['revision'], 2)
        self.assertEqual(Path(first['state']).read_bytes(), immutable)
        retry = self.keeper.save(self.task(), 'login')
        self.assertEqual(retry['revision'], 1)
        self.assertEqual(self.keeper.status()['tasks'][0]['revision'], 2)
        with self.assertRaisesRegex(Error,'reused'):
            self.keeper.save(self.task(done=['different']), 'login')

    def test_interrupted_publication_can_be_retried(self):
        with mock.patch.object(self.keeper, 'publish_pointer', side_effect=OSError('simulated crash')):
            with self.assertRaises(OSError):
                self.keeper.save(self.task(), 'login')
        self.assertEqual(self.keeper.status()['tasks'], [])
        retry = self.keeper.save(self.task(), 'login')
        self.assertEqual(retry['revision'], 1)
        self.assertEqual(self.keeper.resume('login')['status'], 'context_checked')

    def test_dirty_untracked_and_branch_differences(self):
        self.keeper.save(self.task(artifact_paths=['app.py']), 'login')
        (self.root/'app.py').write_text('value = 2\n')
        result = self.keeper.resume('login')
        self.assertEqual(result['status'], 'partial')
        self.assertIn('artifact', [item['field'] for item in result['mismatches']])
        (self.root/'app.py').write_text('value = 1\n')
        (self.root/'new.txt').write_text('one')
        before = self.keeper.inspect()['fingerprint']
        (self.root/'new.txt').write_text('two')
        self.assertNotEqual(self.keeper.inspect()['fingerprint'], before)
        (self.root/'new.txt').unlink()
        run_git(self.root,'checkout','-b','other')
        self.assertIn('branch',[item['field'] for item in self.keeper.resume('login')['mismatches']])

    def test_missing_artifact_and_old_check(self):
        self.keeper.save(self.task(artifact_paths=['missing.py'],checks=[{'command':'pytest','observed_at':'2026-10-16T08:00:00Z','exit_code':0,'workspace_fingerprint':'old'}]), 'login')
        result=self.keeper.resume('login')
        self.assertIn('missing.py',result['missing'])
        self.assertEqual(result['checks_needing_rerun'],['pytest'])
        self.assertFalse(self.keeper.record('tasks','login')['state']['checks'][0]['matches_checkpoint'])

    def test_traversal_and_symlink_rejected(self):
        (self.base/'outside').write_text('outside')
        with self.assertRaises(Error):
            self.keeper.save(self.task(artifact_paths=['../outside']), 'login')
        (self.root/'escape').symlink_to(self.base/'outside')
        with self.assertRaises(Error):
            self.keeper.save(self.task(artifact_paths=['escape']), 'login')
        with self.assertRaises(Error):
            self.keeper.save(self.task(), '../bad')
        context = self.base/'redirected'
        context.mkdir()
        other = self.base/'other';other.mkdir()
        (other/'.context-keeper').symlink_to(context, target_is_directory=True)
        with self.assertRaises(Error):
            Keeper(other)

    def test_redaction_and_integrity(self):
        saved=self.keeper.save(self.task(done=['password=hidden-value','api_key=not-for-export']), 'login')
        self.assertNotIn('hidden-value',Path(saved['state']).read_text())
        self.assertNotIn('not-for-export',Path(saved['handoff']).read_text())
        path=Path(saved['state']);content=read_json(path);content['state']['goal']='tampered'
        path.write_text(json.dumps(content))
        with self.assertRaisesRegex(Error,'integrity'):
            self.keeper.resume('login')

    def test_experience_lifecycle_scope_and_invalidates_reference(self):
        exp=self.keeper.remember(self.experience())
        self.assertEqual(self.keeper.recall('登录'),[])
        active=self.keeper.experience_state(exp['id'],'active',1,'user confirmed project lesson')
        self.assertEqual(self.keeper.recall('登录')[0]['id'],exp['id'])
        self.keeper.save(self.task(experience_refs=[{'id':exp['id'],'version':active['revision']}]),'login')
        correction=self.keeper.remember(self.experience(key='correction',version=2,content='纠正后的经验'),exp['id'])
        self.assertEqual(correction['status'],'proposed')
        self.assertEqual(self.keeper.recall('登录'),[])
        self.assertEqual(self.keeper.resume('login')['status'],'partial')
        with self.assertRaisesRegex(Error,'version changed'):
            self.keeper.experience_state(exp['id'],'active',2,'stale confirmation')
        with self.assertRaisesRegex(Error,'broaden'):
            self.keeper.remember(self.experience(key='broaden',version=3,scope='task',task_id='login'),exp['id'])

    def test_task_experience_and_explicit_preferences(self):
        self.keeper.save(self.task(),'login')
        self.keeper.save(self.task(key='other'),'other')
        exp=self.keeper.remember(self.experience(scope='task',task_id='login',kind='preference',content='先给方案'))
        self.keeper.experience_state(exp['id'],'active',1,'explicit request')
        self.assertEqual(self.keeper.recall('unrelated','other'),[])
        self.assertEqual(len(self.keeper.recall('unrelated','login')),1)
        with self.assertRaises(Error):
            self.keeper.remember(self.experience(key='global',scope='user'))

    def test_export_clone_roundtrip_no_unrelated_tasks_or_code(self):
        self.keeper.save(self.task(),'login')
        self.keeper.save(self.task(key='private',title='unrelated-private-task'),'other')
        archive=self.base/'handoff.zip'
        self.keeper.export('login',archive)
        with zipfile.ZipFile(archive) as bundle:
            names=bundle.namelist()
            self.assertFalse(any('tasks/other/' in name or name.endswith('app.py') for name in names))
            self.assertNotIn('unrelated-private-task',bundle.read('.context-keeper/INDEX.md').decode())
            clone=self.base/'clone'
            subprocess.run(['git','clone',str(self.root),str(clone)],check=True,capture_output=True)
            bundle.extractall(clone)
        self.assertEqual(Keeper(clone).resume('login')['status'],'context_checked')
        with self.assertRaisesRegex(Error,'already exists'):
            self.keeper.export('login',archive)

    def test_directory_only_mode_is_explicitly_partial(self):
        folder=self.base/'without-git';folder.mkdir()
        keeper=Keeper(folder);keeper.init();keeper.save(self.task(),'plain')
        self.assertEqual(keeper.resume('plain')['status'],'partial')
        self.assertEqual(keeper.inspect()['mode'],'directory')

    def test_concurrent_writers_only_one_revision_wins(self):
        self.keeper.save(self.task(),'login')
        inputs=[]
        for index in range(2):
            path=self.base/('input%d.json'%index)
            path.write_text(json.dumps(self.task(key='concurrent-%d'%index,revision=1)))
            inputs.append(path)
        processes=[subprocess.Popen([sys.executable,str(SCRIPTS/'ck.py'),'--workspace',str(self.root),'save','--task','login','--input',str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for path in inputs]
        outputs=[process.communicate(timeout=30) for process in processes]
        self.assertEqual(sorted(process.returncode for process in processes),[0,1])
        self.assertEqual(self.keeper.status()['tasks'][0]['revision'],2)

    def test_cli_contract_error_and_stdlib_execution(self):
        out=io.StringIO();err=io.StringIO()
        path=self.base/'input.json';path.write_text(json.dumps(self.task()))
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            code=main(['--workspace',str(self.root),'save','--input',str(path)])
        self.assertEqual(code,0,err.getvalue())
        # -S disables site-packages: this skill must still work.
        result=subprocess.run([sys.executable,'-S',str(SCRIPTS/'ck.py'),'--workspace',str(self.root),'status'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(json.loads(result.stdout)['initialized'])
        path.write_text(json.dumps({'goal':'api_key=do-not-leak'}))
        failed=subprocess.run([sys.executable,'-S',str(SCRIPTS/'ck.py'),'--workspace',str(self.root),'save','--input',str(path)],capture_output=True,text=True)
        self.assertEqual(failed.returncode,1)
        self.assertNotIn('do-not-leak',failed.stderr)

    def test_next_save_draft_preserves_sources_and_is_valid(self):
        self.keeper.save(self.task(checks=[{'command':'pytest','observed_at':'2026-10-16T08:00:00Z','exit_code':0,
                                           'workspace_fingerprint':self.keeper.inspect()['fingerprint'],'evidence_ids':['user-1']}]),'login')
        draft=self.keeper.draft('login')
        draft['done'].append('next step completed')
        result=self.keeper.save(draft,'login')
        self.assertEqual(result['revision'],2)
        self.assertEqual(self.keeper.record('tasks','login')['state']['evidence'][0]['id'],'user-1')
        with self.assertRaises(Error):
            self.keeper.record('tasks','login',0)

    def test_modified_handoff_is_not_silently_trusted(self):
        saved=self.keeper.save(self.task(),'login')
        record=self.keeper.record('tasks','login')
        path=self.keeper.path('tasks','login','checkpoints','000001','HANDOFF.md')
        path.write_text('incorrect handoff')
        result=self.keeper.resume('login')
        self.assertEqual(result['status'],'partial')
        self.assertIn('handoff_modified',[item['field'] for item in result['mismatches']])

    def test_installer_and_standalone_installed_runtime(self):
        installer=SCRIPTS.parent/'install.py'
        destination=self.base/'codex'/'skills'/'context-keeper'
        command=[sys.executable,str(installer),'--dest',str(destination)]
        result=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        metadata=json.loads(result.stdout)
        self.assertEqual(metadata['version'],(SCRIPTS.parent/'VERSION').read_text().strip())
        installed=subprocess.run([sys.executable,'-S',str(destination/'scripts'/'ck.py'),'--workspace',str(self.root),'status'],cwd=self.base,capture_output=True,text=True)
        self.assertEqual(installed.returncode,0,installed.stderr)
        refused=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(refused.returncode,1)
        updated=subprocess.run(command+['--update'],capture_output=True,text=True)
        self.assertEqual(updated.returncode,0,updated.stdout)
        backup=Path(json.loads(updated.stdout)['backup'])
        self.assertTrue(backup.is_dir())
        self.assertNotEqual(backup.parent,destination.parent)

    def test_portable_bundle_installs_without_source_repository_or_codex(self):
        bundle=self.base/'downloaded'/'context-keeper'
        shutil.copytree(SCRIPTS.parent,bundle,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        # Installer must use only files in its own downloaded bundle.
        result=subprocess.run([sys.executable,'-S',str(bundle/'install.py'),'--project',str(self.root)],cwd=self.base,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        installed=json.loads(result.stdout)
        destination=self.root/'.agent-tools'/'context-keeper'
        self.assertEqual(Path(installed['installed']),destination.resolve())
        self.assertTrue((destination/'AGENT.md').is_file())
        self.assertTrue((destination/'SKILL.md').is_file())
        runtime=subprocess.run([sys.executable,'-S',str(destination/'scripts'/'ck.py'),'--workspace',str(self.root),'status'],capture_output=True,text=True)
        self.assertEqual(runtime.returncode,0,runtime.stderr)

    def test_portable_default_does_not_install_into_codex(self):
        bundle=SCRIPTS.parent
        data_home=self.base/'xdg'
        environment={**os.environ,'XDG_DATA_HOME':str(data_home),'CODEX_HOME':str(self.base/'codex-unused')}
        result=subprocess.run([sys.executable,'-S',str(bundle/'install.py')],env=environment,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(Path(json.loads(result.stdout)['installed']),(data_home/'agent-tools'/'context-keeper').resolve())
        self.assertFalse((self.base/'codex-unused').exists())

    def test_installer_rejects_recursive_source_destination(self):
        bundle=self.base/'downloaded'/'context-keeper'
        shutil.copytree(SCRIPTS.parent,bundle,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        result=subprocess.run([sys.executable,str(bundle/'install.py'),'--dest',str(bundle/'nested')],capture_output=True,text=True)
        self.assertEqual(result.returncode,1)
        self.assertFalse((bundle/'nested').exists())


if __name__ == '__main__':
    unittest.main()
