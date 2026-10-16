#!/usr/bin/env python3
"""Install the portable ContextKeeper bundle without changing any agent configuration."""
import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path(__file__).resolve().parent


def manifest(directory):
    return {path.relative_to(directory).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob('*'))
            if path.is_file() and '__pycache__' not in path.parts and path.name != '.context-keeper-install.json'}


def install(destination, update=False):
    destination = Path(destination).expanduser().absolute()
    try:
        destination.resolve().relative_to(SOURCE.resolve())
    except ValueError:
        pass
    else:
        raise ValueError('Choose a destination outside the extracted/source bundle to avoid recursive installation.')
    if destination.is_symlink():
        raise ValueError('The skill destination must not be a symlink.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not update:
        raise ValueError('Skill already exists. Inspect it, then use --update for a backed-up replacement.')
    if destination.exists() and not (destination / '.context-keeper-install.json').is_file():
        raise ValueError('Existing directory is not a managed ContextKeeper install; it will not be overwritten.')
    for path in SOURCE.rglob('*'):
        if path.is_symlink():
            raise ValueError('Skill source must not contain symlinks.')
    staging = Path(tempfile.mkdtemp(prefix='.context-keeper-install-', dir=str(destination.parent)))
    backup = None
    try:
        shutil.copytree(SOURCE, staging, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        files = manifest(staging)
        if files != manifest(SOURCE):
            raise ValueError('Skill copy does not match source files.')
        version = (SOURCE / 'VERSION').read_text().strip()
        metadata = {'name':'context-keeper','version':version,'files':files,
                    'installed_at':datetime.now(timezone.utc).isoformat()}
        (staging/'.context-keeper-install.json').write_text(json.dumps(metadata,indent=2)+'\n')
        if destination.exists():
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            # Keep backups outside discovery directories so agents do not load duplicate skills.
            backups = destination.parent.parent / 'context-keeper-backups'
            backups.mkdir(parents=True, exist_ok=True)
            backup = backups / ('context-keeper-' + stamp)
            destination.rename(backup)
        try:
            staging.rename(destination)
        except Exception:
            if backup and not destination.exists():
                backup.rename(destination)
            raise
        return {'installed':str(destination.resolve()),'version':version,'files_verified':len(files),
                'backup':str(backup) if backup else None,
                'agent_entry':str((destination/'AGENT.md').resolve()),
                'skill_entry':str((destination/'SKILL.md').resolve()),
                'script':str((destination/'scripts'/'ck.py').resolve()),
                'next':'Ask your agent to read agent_entry, then save/resume the intended project. Native skill discovery depends on the host.'}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    choices=parser.add_mutually_exclusive_group()
    choices.add_argument('--project',type=Path,help='Install under PROJECT/.agent-tools/context-keeper; no host config is changed')
    choices.add_argument('--dest',type=Path,help='Exact bundle directory, including context-keeper; use the actual host skill path')
    choices.add_argument('--codex',action='store_true',help='Explicit optional Codex user-skill installation')
    parser.add_argument('--update',action='store_true',help='Back up and replace a previously managed install')
    args=parser.parse_args()
    if args.project:
        project=args.project.expanduser().resolve()
        if not project.is_dir():
            parser.error('--project must be an existing project directory')
        destination=project/'.agent-tools'/'context-keeper'
    elif args.dest:
        destination=args.dest
    elif args.codex:
        destination=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'skills'/'context-keeper'
    else:
        data_home=Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local'/'share')))
        destination=data_home/'agent-tools'/'context-keeper'
    try:
        print(json.dumps(install(destination,args.update),ensure_ascii=False,indent=2))
        return 0
    except (OSError,ValueError) as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
