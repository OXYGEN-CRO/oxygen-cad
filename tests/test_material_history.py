"""Accepted-increment force Jacobians, state ownership and bounded native evidence."""
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest
from scipy import sparse

from o3d.simulation_contact_global import matrix_hash
from o3d.simulation_solver_profile import validated_build
from o3d.solver import solver_binary
from .test_accepted_bulk import actual


def require_native_replay():
    if not os.environ.get('O3D_BULK_KERNEL_EVIDENCE') or validated_build(solver_binary()) is None:
        pytest.skip('Configure the source-bound native material replay tool')


def test_every_loading_and_unloading_increment_has_its_own_qualified_force_operator(actual):
    require_native_replay();_,cases=actual
    for name in ['plastic','plastic-unload']:
        system,_,directory,result=cases[name]
        summary=result['checks']['material_derivative_history']
        source=json.loads((directory/'material-derivative-history.json').read_text())
        assert summary['all_accepted_force_derivatives_available']
        assert summary['qualified_plastic_frames']==summary['accepted_frames']>1
        assert summary['replayed_points']==summary['accepted_frames']*12
        assert not summary['full_contact_stability_qualified'] and not summary['accepted_state_tangent_qualified']
        provider=system._accepted_bulk;reserved=provider.material_history.points
        matrices=[]
        for entry in source['history']:
            assert entry['available'] and entry['operator_kind']=='refined-native-stress-force-jacobian'
            audit_path=directory/entry['audit_file'];assert hashlib.sha256(audit_path.read_bytes()).hexdigest()==entry['audit_file_sha256']
            audit=json.loads(audit_path.read_text());assert audit['accepted_bulk_evidence_root']=='../..'
            assert audit['accepted_bulk_file_sha256']==entry['accepted_bulk_file_sha256']
            assert hashlib.sha256((directory/entry['accepted_bulk_file']).read_bytes()).hexdigest()==entry['accepted_bulk_file_sha256']
            for key in ['input','output']:
                path=audit_path.parent/audit['run'][key+'_file'];assert path.suffix=='.gz'
                assert hashlib.sha256(path.read_bytes()).hexdigest()==audit['run'][key+'_compressed_sha256']
                assert hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest()==audit['run'][key+'_sha256']
            meta={key:entry[key] for key in ['step','increment']}
            matrix,bound=provider.force_operator(meta)
            assert matrix_hash(matrix)==entry['operator_sha256']
            assert not provider.rows[(meta['step'],meta['increment'])]['bulk_force_jacobian_complete']
            matrices.append(matrix)
        assert provider.material_history.points==reserved # Reads do not re-run constitutive updates.
        assert sparse.linalg.norm(matrices[0]-matrices[9])/sparse.linalg.norm(matrices[0])>.01


def test_material_jacobian_cannot_be_reused_for_a_neighbour_or_corrupted_after_cache(actual,tmp_path):
    require_native_replay();_,cases=actual;system,_,directory,_=cases['plastic']
    original=system._accepted_bulk.material_history
    identity=next(iter(original.entries));row=original.entries[identity];history=copy.copy(original)
    history.provider=copy.copy(original.provider);history.provider.directory=tmp_path
    history.entries=copy.deepcopy(original.entries);history.cache=None
    for name in row['evidence_files_sha256']:
        target=tmp_path/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(directory/name,target)
    target=tmp_path/row['operator_file']
    data={'file_sha256':row['accepted_bulk_file_sha256']}
    assert matrix_hash(history.candidate(identity,data))==row['operator_sha256']
    with pytest.raises(ValueError,match='different accepted state'):
        history.candidate(identity,{'file_sha256':'0'*64})
    target.write_bytes(target.read_bytes()+b'corrupt')
    with pytest.raises(ValueError,match='evidence changed'):
        history.candidate(identity,data)


@pytest.mark.parametrize('damage',['stream','state','coverage','counts','link'])
def test_handoff_cannot_reindex_corrupted_material_history_as_new_evidence(actual,tmp_path,damage):
    require_native_replay()
    from o3d.simulation_material_history import verify_material_history
    _,cases=actual;_,_,directory,_=cases['plastic']
    document=json.loads((directory/'material-derivative-history.json').read_text())
    shutil.copyfile(directory/'analysis.accepted-bulk.jsonl',tmp_path/'analysis.accepted-bulk.jsonl')
    for entry in document['history']:
        for name in [entry['accepted_bulk_file'],*entry['evidence_files_sha256']]:
            p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(directory/name,p)
    index=tmp_path/'material-derivative-history.json';index.write_text(json.dumps(document))
    assert verify_material_history(tmp_path)['accepted_frames']==len(document['history'])
    row=document['history'][0]
    if damage=='stream':
        p=next(tmp_path/name for name in row['evidence_files_sha256'] if name.endswith('output.txt.gz'));p.write_bytes(p.read_bytes()+b'corrupt')
    elif damage=='state':
        p=tmp_path/row['accepted_bulk_file'];p.write_bytes(p.read_bytes()+b'corrupt')
    elif damage=='coverage':document['history'].pop()
    elif damage=='counts':document['summary']['force_derivative_available_frames']-=1
    else:
        p=tmp_path/row['audit_file'];p.unlink();p.symlink_to(directory/row['audit_file'])
    index.write_text(json.dumps(document))
    with pytest.raises(ValueError):verify_material_history(tmp_path)


@pytest.mark.parametrize('limitation',['budget','missing-tool'])
def test_unperformed_increment_does_not_inherit_a_qualified_final_candidate(tmp_path,monkeypatch,limitation):
    require_native_replay()
    from o3d import simulation_material_history as history
    from o3d.extended_solver import calculix_solve
    from .test_simulation_domain import fixture
    _,domain,study,case,system=fixture(divisions=(2,1,1),kind='nonlinear_static')
    m=domain.materials[0];m.plastic_curve=[(250,0),(350,.01)];m.plastic_source='Declared numerical hardening reference';case.loads[0].vector=(1200,0,0)
    if limitation=='budget':monkeypatch.setattr(history,'MAX_HISTORY_POINTS',0)
    else:monkeypatch.delenv('O3D_BULK_KERNEL_EVIDENCE')
    result=calculix_solve(system,case,tmp_path);summary=result['checks']['material_derivative_history']
    assert summary['qualified_plastic_frames']==0 and not summary['all_accepted_force_derivatives_available']
    assert summary['unperformed_plastic_frames']==summary['accepted_frames']
    assert summary['replayed_points']==0
    if limitation=='budget':assert result['checks']['material_derivative_audit']['candidate_operator_available']
    for identity in system._accepted_bulk.order:
        meta=dict(zip(['step','increment'],identity));native,_,_=system._accepted_bulk.get(meta)
        selected,entry=system._accepted_bulk.force_operator(meta)
        assert not entry['available'] and entry['operator_kind']=='unqualified-native-algorithmic'
        assert matrix_hash(selected)==matrix_hash(native)
