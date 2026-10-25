"""Bind native inputs, dictionaries, mesh, logs and output fields before handoff."""
import json
from pathlib import Path
from .simulation_solver_profile import file_hash


NAME='flow-evidence-index.json'

def inventory(directory):
    directory=Path(directory);result={}
    for p in sorted(directory.rglob('*')):
        if p.is_symlink() or not p.resolve().is_relative_to(directory.resolve()):raise ValueError('Unsafe native flow evidence link')
        if p.is_file() and p!=directory/NAME:
            result[str(p.relative_to(directory))]={'sha256':file_hash(p),'bytes':p.stat().st_size}
    return result


def bind(directory):
    from .store import write_json
    directory=Path(directory);path=directory/NAME
    if path.exists():raise ValueError('Native flow evidence is already bound')
    files=inventory(directory)
    if not files or 'flow-evidence.json' not in files:raise ValueError('Incomplete native flow evidence')
    write_json(path,{'schema':1,'files':files})
    return {'sha256':file_hash(path),'bytes':path.stat().st_size}


def verify(directory,record):
    directory=Path(directory);path=directory/NAME
    if path.is_symlink() or not path.is_file() or path.stat().st_size!=record['bytes'] or file_hash(path)!=record['sha256']:
        raise ValueError('Native flow evidence index changed or is missing')
    value=json.loads(path.read_text())
    if value.get('schema')!=1 or value.get('files')!=inventory(directory):raise ValueError('Executed native flow evidence changed; handoff refused')
    return value
