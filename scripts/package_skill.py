#!/usr/bin/env python3
"""Package the self-contained skill, not project data or legacy services."""
from pathlib import Path
import json
import zipfile
import hashlib

root=Path(__file__).resolve().parents[1]
source=root/'skills'/'context-keeper'
version=(root/'VERSION').read_text().strip()
destination=root/'dist'/('context-keeper-skill-'+version+'.zip')
destination.parent.mkdir(exist_ok=True)
files=[]
with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(source.rglob('*')):
        if path.is_symlink():
            raise RuntimeError('Do not package symlinked skill resources.')
        if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':
            relative=path.relative_to(source)
            archive.write(path,'context-keeper/'+relative.as_posix())
            files.append(relative.as_posix())
print(json.dumps({'package':str(destination),'version':version,'files':files,
                  'size_bytes':destination.stat().st_size,'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()},ensure_ascii=False,indent=2))
