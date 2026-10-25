"""Source-bound native J2 replay and refined material force derivatives.

The separate GPL executable evaluates the original constitutive update. Its
finite differences are diagnostic approximations at fixed increment-start
history. They never replace the solver's algorithmic tensor or qualify contact,
dynamics, physical properties, or a nonsmooth material transition.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import numpy as np
from scipy import sparse

from .simulation_accepted_bulk import assemble_bulk, native_material_tensor
from .simulation_solver_profile import file_hash, validated_material_probe

MAX_POINTS = 4096
MAX_OUTPUT_BYTES = 128 * 1024 * 1024
STEPS = (1e-6, 1e-7, 1e-8, 1e-9, 3e-10, 1e-10)
DERIVATIVE_TOLERANCE = 2e-6
REPLAY_TOLERANCE = 2e-10


def quadrature_strains(xyz, displacement, nonlinear):
    """Native XX YY ZZ XY XZ YZ tensor strains (engineering perturbations later)."""
    from .quadratic import weights_and_gradients
    n=len(xyz)
    if n==4:
        references=[np.array([[-1.,-1.,-1.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]])]
    elif n==10:
        references=[weights_and_gradients(np.array([.5854101966249685 if j==q else .1381966011250105 for j in range(4)])[1:])[1] for q in range(4)]
    else:raise ValueError('Unsupported material replay element')
    result=[]
    for reference in references:
        jac=(xyz-xyz[:1]).T@reference
        if np.linalg.det(jac)<=0:raise ValueError('Invalid material replay reference Jacobian')
        gradient=displacement.T@(reference@np.linalg.inv(jac))
        if nonlinear:
            f=np.eye(3)+gradient
            if np.linalg.det(f)<=0:raise ValueError('Invalid material replay deformation Jacobian')
            tensor=(f.T@f-np.eye(3))/2
        else:tensor=(gradient+gradient.T)/2
        result.append(np.r_[np.diag(tensor),tensor[0,1],tensor[0,2],tensor[1,2]])
    return np.array(result)


def replay_requests(system,data):
    from .solver import calculix_number
    requests=[];offset=0;nonlinear=bool(data['header']['iperturb'][1])
    elements={e.id:e for e in system.mesh.elements}
    for record in data['records']:
        label,nn,nq,kind=map(int,record[:4]);nodes=np.array(record[4:4+nn],dtype=int)
        if kind==-51:
            if data['header']['state_count']!=13:raise ValueError('Material replay requires the complete supported 13-variable J2 history')
            material=system.assignments[elements[label].body_id]
            if not material.plastic_curve or len(material.plastic_curve)>200:
                raise ValueError('Material replay hardening curve is unavailable or exceeds 200 points')
            xyz=np.array([system.mesh.nodes[int(n)] for n in nodes])
            strains=quadrature_strains(xyz,data['accepted_displacements_mm'][nodes-1],nonlinear)
            # Replay the numbers actually serialized into the native deck.
            e,nu=[float(calculix_number(v)) for v in (material.youngs_modulus_mpa,material.poisson_ratio)]
            curve=[(float(calculix_number(p)),float(calculix_number(s))) for s,p in material.plastic_curve]
            for q,strain in enumerate(strains):
                requests.append({'point':offset+q,'element':label,'quadrature':q+1,'nonlinear':int(nonlinear),
                    'youngs_modulus_mpa':e,'poisson_ratio':nu,'curve':curve,'strain':strain,
                    'initial_state':data['initial_state'][offset+q],
                    'increment_duration':data['header']['increment_duration']})
        offset+=nq
    return requests


def run_replay(probe,requests,directory):
    """One bounded native process; original material state resets for every call."""
    if not requests or len(requests)>MAX_POINTS:raise ValueError('Material replay exceeds the 4096-point budget')
    lines=[]
    for request in requests:
        for h in STEPS:
            lines.append(f"{request['nonlinear']} {request['youngs_modulus_mpa']:.17g} {request['poisson_ratio']:.17g} {len(request['curve'])} {request['increment_duration']:.17g} {h:.17g}")
            lines.append(' '.join(format(float(v),'.17g') for v in request['strain']))
            lines.append(' '.join(format(float(v),'.17g') for v in request['initial_state']))
            lines.extend(f'{p:.17g} {s:.17g}' for p,s in request['curve'])
    content=('\n'.join(lines)+'\n').encode()
    if len(content)>32*1024*1024:raise ValueError('Material replay input exceeds its budget')
    directory=Path(directory);input_path=directory/'material-replay-input.txt';output_path=directory/'material-replay-output.txt';error_path=directory/'material-replay-stderr.txt'
    # These are fresh evidence files in a solver-owned trial directory.
    for path in (input_path,output_path,error_path):
        if path.exists() or path.is_symlink():raise ValueError('Material replay evidence already exists')
    input_path.write_bytes(content)
    environment=dict(os.environ,LD_LIBRARY_PATH=probe['runtime_library_path'],OMP_NUM_THREADS='1')
    with input_path.open('rb') as source,output_path.open('wb') as target,error_path.open('wb') as errors:
        run=subprocess.run([probe['binary']],stdin=source,stdout=target,stderr=errors,env=environment,cwd=directory,timeout=60,check=False)
    if run.returncode:raise ValueError('Native material replay did not complete')
    if output_path.stat().st_size>MAX_OUTPUT_BYTES:raise ValueError('Material replay output exceeds its budget')
    words=output_path.read_text().replace('D','E').split()
    if len(words)!=len(requests)*len(STEPS)*148:raise ValueError('Incomplete native material replay output')
    values=np.array([float(v) for v in words]).reshape(len(requests),len(STEPS),148)
    if not np.isfinite(values).all():raise ValueError('Nonfinite native material replay output')
    return values,{'input_sha256':file_hash(input_path),'output_sha256':file_hash(output_path),
        'input_bytes':len(content),'output_bytes':output_path.stat().st_size}


def _relative(actual,expected,floor=1.):
    return float(np.linalg.norm(actual-expected)/max(float(np.linalg.norm(expected)),floor))


def _column_relative(actual,expected):
    return float(np.max(np.linalg.norm(actual-expected,axis=0)/np.maximum(np.linalg.norm(expected,axis=0),1e-12)))


def assess_point(values,stress,state,packed):
    """Retain one-sided/refinement failures; never symmetrize a force derivative."""
    if np.shape(values)!=(len(STEPS),148) or not np.isfinite(values).all():raise ValueError('Invalid material replay values')
    baseline=values[0]
    stress_error=_relative(baseline[:6],stress)
    state_error=_relative(baseline[6:19],state,1e-6)
    tensor_error=_relative(baseline[19:40],packed)
    replay_bound=max(stress_error,state_error,tensor_error)<=REPLAY_TOLERANCE
    # All h values must replay the identical baseline, not merely nearby inputs.
    replay_bound=replay_bound and bool(np.array_equal(values[:,:40],np.broadcast_to(baseline[:40],(len(STEPS),40))))
    fd=values[:,40:76].reshape(len(STEPS),6,6)
    forward=values[:,76:112].reshape(len(STEPS),6,6);backward=values[:,112:148].reshape(len(STEPS),6,6)
    refinement=[_column_relative(fd[i+1],fd[i]) for i in range(len(STEPS)-1)]
    sided=[_column_relative(forward[i],backward[i]) for i in range(len(STEPS))]
    # Require two successive converged refinements and matching one-sided limits.
    # Choose the largest qualifying step to avoid unnecessarily small, noisy
    # differences. A coarse perturbation crossing a nearby branch cannot veto a
    # later resolved local derivative, but a persistent yield kink cannot pass.
    candidates=[i for i in range(2,len(STEPS)) if max(refinement[i-2:i])<=DERIVATIVE_TOLERANCE and sided[i]<=DERIVATIVE_TOLERANCE]
    selected=candidates[0] if candidates else len(STEPS)-1
    smooth=bool(candidates)
    native=native_material_tensor(packed,-51)
    discrepancy=_column_relative(native,fd[selected])
    qualified=bool(replay_bound and smooth)
    row={'replay_bound':bool(replay_bound),'stress_relative_error':stress_error,'state_relative_error':state_error,
        'native_tensor_replay_relative_error':tensor_error,'engineering_strain_steps':list(STEPS),
        'maximum_column_refinement_errors':refinement,'maximum_column_one_sided_gaps':sided,
        'selected_engineering_strain_step':STEPS[selected] if smooth else None,
        'finite_difference_qualified':qualified,'native_algorithmic_tensor_qualified':bool(qualified and discrepancy<=DERIVATIVE_TOLERANCE),
        'native_tensor_maximum_column_derivative_error':discrepancy,
        'derivative_asymmetry_ratio':float(np.linalg.norm(fd[selected]-fd[selected].T)/max(float(np.linalg.norm(fd[selected])),1e-12)),
        'reason':None if qualified else 'Captured constitutive replay mismatch' if not replay_bound else 'Two-sided material derivative has no qualified refinement plateau'}
    return row,fd[selected]


def audit_final_material(provider,result):
    """Audit every plastic point in the final accepted frame within explicit bounds.

    The separate candidate operator is evidence only. Runtime native-algorithmic
    bulk/contact operators and their qualification flags are preserved.
    """
    from .store import write_json
    from .simulation_contact_global import matrix_hash
    summary={'performed':False,'scope':'Final accepted state, all plastic Gauss points, fixed increment-start history; separate refined native-stress finite-difference candidate. No changing-map, dynamic or physical qualification.',
        'reason':None,'native_execution_attempted':False,'plastic_points':0,'replay_bound_points':0,'two_sided_qualified_points':0,
        'native_algorithmic_tensor_qualified_points':0,'candidate_operator_available':False,
        'full_contact_stability_qualified':False,'accepted_state_tangent_qualified':False}
    rows=[];identity=None;extra={}
    if provider.reason:summary['reason']=provider.reason
    else:
        frame=provider.frames[provider.order[-1]];identity={'step':frame['step'],'increment':frame['increment']}
        value=provider.get(identity)
        if value is None:summary['reason']='Final accepted bulk state is unavailable'
        else:
            native_operator,_,data=value
            count=sum(int(r[2]) for r in data['records'] if r[3]==-51);summary['plastic_points']=count
            if not count:summary['reason']='No translated plastic material in the final accepted state'
            elif count>MAX_POINTS:summary['reason']='Final plastic material replay exceeds the 4096-point budget'
            elif not provider.build:summary['reason']='Pinned native build identity is unavailable'
            else:
                probe=validated_material_probe(provider.build)
                if probe is None:summary['reason']='Source-bound native material replay tool is unavailable'
                else:
                    extra={'tool':{k:v for k,v in probe.items() if k not in ('binary','runtime_library_path')},'accepted_bulk_file':frame['file'],'accepted_bulk_file_sha256':data['file_sha256']}
                    try:
                        requests=replay_requests(provider.system,data)
                        summary['native_execution_attempted']=True
                        values,receipt=run_replay(probe,requests,provider.directory);extra['run']=receipt
                        tensors=np.empty((data['header']['points'],6,6));offset=0
                        for record in data['records']:
                            for q in range(int(record[2])):tensors[offset+q]=native_material_tensor(data['tangent'][offset+q],int(record[3]))
                            offset+=int(record[2])
                        for request,point in zip(requests,values):
                            i=request['point'];row,derivative=assess_point(point,data['stress'][i],data['state'][i],data['tangent'][i])
                            rows.append({'element':request['element'],'quadrature':request['quadrature'],'point':i,**row});tensors[i]=derivative
                        summary.update(performed=True,replay_bound_points=sum(r['replay_bound'] for r in rows),
                            two_sided_qualified_points=sum(r['finite_difference_qualified'] for r in rows),
                            native_algorithmic_tensor_qualified_points=sum(r['native_algorithmic_tensor_qualified'] for r in rows))
                        np.savez_compressed(provider.directory/'material-derivative-tensors.npz',point_indices=np.array([r['point'] for r in rows]),
                            derivatives_mpa=tensors[[r['point'] for r in rows]],qualified=np.array([r['finite_difference_qualified'] for r in rows]))
                        if len(rows)==count and all(r['finite_difference_qualified'] for r in rows):
                            candidate,_=assemble_bulk(provider.system,data,material_tensors=tensors)
                            sparse.save_npz(provider.directory/'material-final-force-jacobian.npz',candidate)
                            summary.update(candidate_operator_available=True,candidate_operator_sha256=matrix_hash(candidate),
                                candidate_vs_native_algorithmic_relative_difference=float(sparse.linalg.norm(candidate-native_operator)/max(float(sparse.linalg.norm(native_operator)),1e-12)))
                        else:summary['reason']='At least one final plastic point lacks bound, refined two-sided derivative evidence'
                    except (ValueError,subprocess.TimeoutExpired) as error:
                        summary['reason']=str(error)
    write_json(provider.directory/'material-derivative-audit.json',{'schema':1,'summary':summary,'identity':identity,'history':rows,**extra})
    result['checks']['material_derivative_audit']=summary
    return summary
