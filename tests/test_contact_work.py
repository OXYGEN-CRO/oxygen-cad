"""Measured accepted-state integration and independent one-dimensional limits."""
import copy
import json
from pathlib import Path
import shutil
import pytest
from o3d.analysis import parse_frd
from o3d.extended_solver import calculix_solve
from o3d.simulation_contact_evidence import audit_contact,frames,work_sensitivity
from o3d.simulation_models import Region,Support,Load
from o3d.simulation_quadratic import QuadraticSystem
from o3d.simulation_solver_profile import validated_build,file_hash
from o3d.solver import run_process,solver_binary
from .test_canonical_contact import reference


def fixture(kind='compression',increment=.1):
    _,domain,study,case=reference(refined=False)
    study.settings.nonlinear_initial_increment=increment
    if kind!='compression':
        study.interfaces[0].friction_coefficient=.3
        study.interfaces[0].tangential_penalty_n_mm3=1e5
        mesh=domain.models[0].mesh
        mesh.regions.append(Region(id='first:guide',body_id='first',node_ids=sorted({n for e in mesh.elements if e.body_id=='first' for n in e.nodes})))
        case.supports.append(Support(id='simple-shear',region_id='first:guide',kind='partial',components=(True,False,True)))
        case.loads.append(Load(id='shear',region_id='first:xmax',vector=(0,10 if kind=='stick' else 100,0),source='Declared verification load, not hardware calibration'))
    return QuadraticSystem(domain.models[0].mesh,study,{m.id:m for m in domain.materials}),case


@pytest.fixture(scope='module')
def accepted(tmp_path_factory):
    binary=Path(solver_binary()).resolve()
    build=validated_build(binary)
    if build is None:pytest.skip('Pinned instrumented CalculiX build not configured')
    root=tmp_path_factory.mktemp('accepted-contact');values={}
    for kind,increment in [('compression',.1),('stick',.1),('slide',.1),('slide-half',.05),('slide-fine',.025),('slide-finer',.0125)]:
        system,case=fixture('slide' if kind.startswith('slide') else kind,increment)
        directory=root/kind
        result=calculix_solve(system,case,directory)
        values[kind]=(system,case,directory,result)
    return binary,build,values


def test_export_does_not_change_solver_results(accepted,tmp_path):
    binary,build,values=accepted
    _,_,directory,_=values['compression']
    shutil.copyfile(directory/'analysis.inp',tmp_path/'analysis.inp')
    run_process([binary,'analysis'],tmp_path,tmp_path/'solver.log',environment_overrides={
        'O3D_CCX_CONTACT_TELEMETRY':'0','LD_LIBRARY_PATH':build['runtime_library_path']})
    assert not (tmp_path/'analysis.contact.jsonl').exists()
    assert (tmp_path/'analysis.dat').read_bytes()==(directory/'analysis.dat').read_bytes()
    # FRD includes wall-clock metadata; compare all parsed numerical fields.
    assert parse_frd(tmp_path/'analysis.frd')==parse_frd(directory/'analysis.frd')


def test_independent_compression_and_stick_energy(accepted):
    _,_,values=accepted
    for kind in ('compression','stick'):
        *_,result=values[kind]
        audit=result['checks']['contact_work_audit']
        assert audit['energy_balance_error']<1e-6
        assert abs(result['values']['frictional_dissipation_n_mm'])<1e-12
        assert audit['maximum_errors']['area_relative_error']<1e-10
        assert audit['maximum_errors']['spring_energy_integration_relative_error']<1e-10
        assert audit['contact_tangent_stability_qualified'] is False
    result=values['compression'][-1]
    # SI reference: .5*F*(F*L/(E*A)), and .5*F*(pressure/penalty).
    assert result['values']['bulk_internal_energy_n_mm']==pytest.approx(.125,rel=1e-8)
    assert result['values']['contact_spring_energy_n_mm']==pytest.approx(.000125,rel=1e-8)


def test_sliding_work_requires_actual_increment_refinement(accepted):
    _,_,values=accepted
    coarse=values['slide'][-1];half=values['slide-half'][-1]
    fine=values['slide-fine'][-1];finer=values['slide-finer'][-1]
    assert work_sensitivity(coarse,half)>.02
    assert work_sensitivity(fine,finer)<.02
    # Independent proportional-load simple-shear limit: G*A/L=40000 N/mm,
    # mu*N=30 N; plastic slip=70/40000-30/(1e5*4).
    expected=.5*30*(70/40000-30/400000)
    assert expected==pytest.approx(.025125)
    errors=[abs(r['values']['frictional_dissipation_n_mm']-expected) for r in (coarse,half,fine,finer)]
    assert all(b<a*.6 for a,b in zip(errors,errors[1:]))
    assert errors[-1]/expected<.02
    assert finer['checks']['energy_balance_error']<.003
    audit=finer['checks']['contact_work_audit']
    assert audit['maximum_errors']['slip_work_integration_relative_error']<1e-10
    assert finer['checks']['frictional_work_qualified'] is False # single solve cannot establish convergence


def test_truncated_or_unpinned_contact_state_refuses(accepted,tmp_path):
    _,_,values=accepted;source=values['compression'][2]/'analysis.contact.jsonl'
    path=tmp_path/'state.jsonl'
    # A complete file minus its final transaction marker is not a completed run.
    lines=source.read_text().splitlines(True);path.write_text(''.join(lines[:-1]))
    with pytest.raises(ValueError,match='Truncated'):list(frames(path))
    header=json.loads(lines[0]);header['source_sha256']='0'*64
    path.write_text(json.dumps(header)+'\n'+''.join(lines[1:]))
    with pytest.raises(ValueError,match='source/schema'):list(frames(path))


def test_independent_geometry_detects_false_solver_state(accepted,tmp_path):
    _,_,values=accepted;system,case,directory,result=values['compression']
    shutil.copyfile(directory/'contact.json',tmp_path/'contact.json')
    lines=(directory/'analysis.contact.jsonl').read_text().splitlines(True)
    for i,line in enumerate(lines):
        row=json.loads(line)
        if row['type']=='point':
            row['secondary_nodes'][3],row['secondary_nodes'][4]=row['secondary_nodes'][4],row['secondary_nodes'][3]
            lines[i]=json.dumps(row)+'\n';break
    (tmp_path/'analysis.contact.jsonl').write_text(''.join(lines))
    contact=system._contact_definition
    with pytest.raises(ValueError,match='midside edge order'):
        audit_contact(tmp_path,contact,case,copy.deepcopy(result),system.loads(case))


def test_binary_and_library_receipt_drift_refuses(accepted,tmp_path):
    binary,_,_=accepted;target=tmp_path/binary.name
    shutil.copyfile(binary,target)
    receipt=json.loads(binary.with_name('build-manifest.json').read_text())
    path=tmp_path/'build-manifest.json';path.write_text(json.dumps(receipt))
    assert validated_build(target)['binary_sha256']==file_hash(binary)
    with target.open('ab') as stream:stream.write(b'changed')
    with pytest.raises(ValueError,match='binary differs'):validated_build(target)
    shutil.copyfile(binary,target)
    dependency=tmp_path/'changed-library.so';dependency.write_bytes(b'changed')
    receipt['libraries']={str(dependency):'0'*64};path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='numerical library changed'):validated_build(target)
