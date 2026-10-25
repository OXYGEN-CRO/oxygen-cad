"""State-indexed force Jacobians with bounded native material replay evidence."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import time
from types import SimpleNamespace

import numpy as np
from scipy import sparse

from .simulation_material_derivatives import audit_final_material,MAX_POINTS
from .simulation_solver_profile import file_hash

MAX_HISTORY_POINTS=65536
MAX_HISTORY_BYTES=256*1024*1024
MAX_HISTORY_SECONDS=240
SCOPE='This accepted increment at fixed increment-start history; refined native-stress force derivatives. No nonsmooth, changing-contact-map, dynamic or physical qualification.'


class MaterialHistory:
    """A candidate belongs to exactly one committed native state, never its neighbour."""
    def __init__(self,provider):
        self.provider=provider;self.entries={};self.points=0;self.bytes=0;self.elapsed=0.
        self.cache=None

    def unavailable(self,identity,reason):
        self.entries.setdefault(identity,{'step':identity[0],'increment':identity[1],
            'available':False,'plastic_points':0,'performed':False,'reason':reason})

    def record(self,frame,data,native_operator,force):
        identity=(frame['step'],frame['increment'])
        if identity in self.entries:
            if self.entries[identity].get('accepted_bulk_file_sha256')!=data['file_sha256']:
                raise ValueError('Material history state identity changed')
            return self.entries[identity]
        count=sum(int(r[2]) for r in data['records'] if r[3]==-51)
        row={'step':identity[0],'increment':identity[1],'accepted_time':frame['accepted_time'],
            'accepted_bulk_file':frame['file'],'accepted_bulk_file_sha256':data['file_sha256'],
            'plastic_points':count,'available':not bool(count),'performed':False,'reason':None,
            'operator_kind':'native-elastic-material-and-geometric' if not count else 'unqualified-native-algorithmic'}
        self.entries[identity]=row
        if not count:return row
        if not self.provider.build:row['reason']='Pinned native build identity is unavailable';return row
        if not os.environ.get('O3D_BULK_KERNEL_EVIDENCE'):row['reason']='Source-bound native material replay tool is unavailable';return row
        if count>MAX_POINTS:row['reason']='Material history frame exceeds the 4096-point budget';return row
        if self.points+count>MAX_HISTORY_POINTS:row['reason']='Material history exceeds the 65536-point trial budget';return row
        # Leave room for one bounded uncompressed native response and its input
        # while compressed prior evidence remains on disk. Never start an
        # unbounded producer to discover that the trial budget is exhausted.
        required=count*6*148*28+32*1024*1024
        if self.bytes+required>MAX_HISTORY_BYTES:row['reason']='Material history exceeds the 256 MiB trial evidence budget';return row
        if self.elapsed>=MAX_HISTORY_SECONDS:row['reason']='Material history exceeds the 240-second trial replay budget';return row
        directory=self.provider.directory/'material-history'/f'step-{identity[0]:08d}-increment-{identity[1]:08d}'
        directory.mkdir(parents=True,exist_ok=False)
        proxy=SimpleNamespace(reason=None,frames={identity:frame},order=[identity],
            get=lambda meta:(native_operator,force,data),system=self.provider.system,
            build=self.provider.build,directory=directory)
        result={'checks':{}};started=time.monotonic();self.points+=count
        try:
            summary=audit_final_material(proxy,result,scope=SCOPE,
                timeout=max(.001,MAX_HISTORY_SECONDS-self.elapsed),evidence_root='../..')
            audit_path=directory/'material-derivative-audit.json';audit=json.loads(audit_path.read_text())
            if 'run' in audit:
                for key in ('input','output'):
                    path=directory/f'material-replay-{key}.txt';content=path.read_bytes()
                    if hashlib.sha256(content).hexdigest()!=audit['run'][key+'_sha256']:
                        raise ValueError('Native material history stream changed before compression')
                    compressed=path.with_suffix(path.suffix+'.gz')
                    compressed.write_bytes(gzip.compress(content,compresslevel=6,mtime=0))
                    if hashlib.sha256(gzip.decompress(compressed.read_bytes())).hexdigest()!=audit['run'][key+'_sha256']:
                        raise ValueError('Native material history compression changed evidence')
                    audit['run'][key+'_file']=compressed.name
                    audit['run'][key+'_compressed_sha256']=file_hash(compressed)
                    path.unlink()
                audit['run']['compression']='gzip; hashes without compressed suffix identify exact original bytes'
            from .store import write_json
            write_json(audit_path,audit)
            row.update(performed=summary['performed'],available=summary['candidate_operator_available'],
                reason=summary['reason'],replay_bound_points=summary['replay_bound_points'],
                native_execution_attempted=summary['native_execution_attempted'],
                two_sided_qualified_points=summary['two_sided_qualified_points'],
                audit_file=str(audit_path.relative_to(self.provider.directory)),audit_file_sha256=file_hash(audit_path))
            row['evidence_files_sha256']={str(p.relative_to(self.provider.directory)):file_hash(p) for p in sorted(directory.iterdir()) if p.is_file()}
            if row['available']:
                path=directory/'material-final-force-jacobian.npz'
                row.update(operator_kind='refined-native-stress-force-jacobian',
                    operator_file=str(path.relative_to(self.provider.directory)),operator_file_sha256=file_hash(path),
                    operator_sha256=summary['candidate_operator_sha256'],
                    candidate_vs_native_algorithmic_relative_difference=summary['candidate_vs_native_algorithmic_relative_difference'])
        finally:
            self.elapsed+=time.monotonic()-started
            self.bytes+=sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
        if self.bytes>MAX_HISTORY_BYTES:
            row.update(available=False,reason='Material history exceeded its trial evidence budget',operator_kind='unqualified-native-algorithmic')
        if self.elapsed>MAX_HISTORY_SECONDS:
            row.update(available=False,reason='Material history exceeded its trial replay time budget',operator_kind='unqualified-native-algorithmic')
        return row

    def candidate(self,identity,data):
        row=self.entries[identity]
        if row.get('accepted_bulk_file_sha256')!=data['file_sha256']:
            raise ValueError('Material force Jacobian belongs to a different accepted state')
        if not row['available'] or not row.get('plastic_points'):return None
        for name,digest in row['evidence_files_sha256'].items():
            evidence=self.provider.directory/name
            if evidence.is_symlink() or not evidence.is_file() or file_hash(evidence)!=digest:
                raise ValueError('Material force Jacobian supporting evidence changed')
        path=self.provider.directory/row['operator_file']
        if path.is_symlink() or not path.is_file() or file_hash(path)!=row['operator_file_sha256']:
            raise ValueError('Material force Jacobian evidence changed')
        if self.cache is not None and self.cache[0]==identity:return self.cache[1]
        matrix=sparse.load_npz(path)
        from .simulation_contact_global import matrix_hash
        if matrix.shape!=(3*len(self.provider.system.ids),)*2 or not np.isfinite(matrix.data).all() or matrix_hash(matrix)!=row['operator_sha256']:
            raise ValueError('Material force Jacobian data differs from its receipt')
        self.cache=(identity,matrix)
        return matrix

    def write(self,result):
        from .store import write_json
        rows=[self.entries[k] for k in getattr(self.provider,'order',[]) if k in self.entries]
        if not self.provider.reason and len(rows)!=len(self.provider.order):raise ValueError('Material history lacks accepted increment coverage')
        plastic=[r for r in rows if r['plastic_points']]
        reasons=sorted({r['reason'] for r in rows if not r['available'] and r.get('reason')})
        summary={'performed':any(r['performed'] for r in plastic),'reason':self.provider.reason,
            'unavailable_or_unresolved_reasons':reasons,
            'accepted_frames':len(rows),'plastic_frames':len(plastic),
            'force_derivative_available_frames':sum(r['available'] for r in rows),
            'qualified_plastic_frames':sum(r['available'] for r in plastic),
            'unresolved_plastic_frames':sum(not r['available'] and r['performed'] for r in plastic),
            'unperformed_plastic_frames':sum(not r['performed'] for r in plastic),
            'all_accepted_force_derivatives_available':bool(rows) and all(r['available'] for r in rows),
            'replayed_points':sum(r['plastic_points'] for r in plastic if r['performed']),
            'reserved_point_evaluations':self.points,'compressed_evidence_bytes':self.bytes,'replay_elapsed_seconds':self.elapsed,
            'limits':{'points_per_frame':MAX_POINTS,'points_per_trial':MAX_HISTORY_POINTS,'bytes_per_trial':MAX_HISTORY_BYTES,'replay_seconds_per_trial':MAX_HISTORY_SECONDS},
            'full_contact_stability_qualified':False,'accepted_state_tangent_qualified':False,
            'scope':'Every committed accepted increment, with explicit unavailable/unresolved states. '+SCOPE}
        document={'schema':1,'summary':summary,'history':rows}
        if len(json.dumps(document,indent=2).encode())>64*1024*1024:
            raise ValueError('Material history index exceeds its 64 MiB budget')
        write_json(self.provider.directory/'material-derivative-history.json',document)
        result['checks']['material_derivative_history']=summary
        return summary


def verify_material_history(directory):
    """Validate the existing state/proof closure before handoff re-indexes files."""
    directory=Path(directory);index=directory/'material-derivative-history.json'
    if index.is_symlink() or not index.is_file() or index.stat().st_size>64*1024*1024:
        raise ValueError('Unsafe or missing material history index')
    document=json.loads(index.read_text())
    if document.get('schema')!=1 or not isinstance(document.get('history'),list):
        raise ValueError('Unsupported material history evidence index')
    rows=document['history'];summary=document.get('summary',{})
    if not rows and summary.get('performed') is False and summary.get('accepted_frames')==0 and isinstance(summary.get('reason'),str) and summary['reason'] and summary.get('all_accepted_force_derivatives_available') is False:
        return {'accepted_frames':0,'force_derivative_available_frames':0,'unperformed_reason':summary['reason']}
    manifest=directory/'analysis.accepted-bulk.jsonl'
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size>8*1024*1024:
        raise ValueError('Material history lacks its accepted-state manifest')
    frames=[json.loads(line) for line in manifest.read_text().splitlines()][1:]
    if [(r.get('step'),r.get('increment')) for r in rows]!=[(f.get('step'),f.get('increment')) for f in frames]:
        raise ValueError('Material history accepted-increment coverage changed')
    def verify(name,digest):
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts or not re.fullmatch('[0-9a-f]{64}',str(digest)):
            raise ValueError('Unsafe material history evidence reference')
        cursor=directory
        for part in relative.parts:
            cursor=cursor/part
            if cursor.is_symlink():raise ValueError('Unsafe material history evidence link')
        if not cursor.is_file() or file_hash(cursor)!=digest:
            raise ValueError('Material history evidence changed; recompute before handoff')
    for frame,row in zip(frames,rows):
        if type(row.get('available')) is not bool or type(row.get('performed')) is not bool or type(row.get('plastic_points')) is not int or row['plastic_points']<0:
            raise ValueError('Invalid material history qualification fields')
        if not frame.get('available'):
            if row.get('available'):raise ValueError('Unavailable accepted state acquired a material derivative')
            continue
        name=row.get('accepted_bulk_file')
        if name!=frame.get('file') or not isinstance(name,str) or not re.fullmatch(r'analysis\.accepted-bulk-[0-9]{8,16}\.bin',name):
            raise ValueError('Material history source frame changed')
        verify(name,row.get('accepted_bulk_file_sha256'))
        if row.get('plastic_points') and row.get('available') and (not row.get('performed') or not row.get('evidence_files_sha256')):
            raise ValueError('Material force derivative lacks its replay proof')
        prefix=f"material-history/step-{frame['step']:08d}-increment-{frame['increment']:08d}/"
        for name,digest in row.get('evidence_files_sha256',{}).items():
            if not name.startswith(prefix) or len(Path(name).parts)!=3:
                raise ValueError('Material history proof belongs to a different increment')
            verify(name,digest)
        if row.get('audit_file'):verify(row['audit_file'],row['audit_file_sha256'])
        if row.get('operator_file'):verify(row['operator_file'],row['operator_file_sha256'])
    available=sum(bool(r.get('available')) for r in rows)
    summary=document.get('summary',{})
    if summary.get('accepted_frames')!=len(rows) or summary.get('force_derivative_available_frames')!=available:
        raise ValueError('Material history qualification counts changed')
    return {'accepted_frames':len(rows),'force_derivative_available_frames':available}
