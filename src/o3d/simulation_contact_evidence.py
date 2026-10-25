"""Independent geometry/work audit of pinned accepted contact state exports.

Printed CELS cannot supply frictional dissipation. The optional external solver
exports quadrature, state and incremental dissipation; this module reconstructs
areas, kinematics, forces, spring energy and plastic-slip work independently.
It does not claim full contact-tangent stability or physical calibration.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from .quadratic import FACES,weights_and_gradients
from .simulation_bonding import triangle_basis
from .store import write_json
from .simulation_solver_profile import contract,validated_build,file_hash


def finite(value,shape=None):
    values=np.asarray(value,dtype=float)
    if (shape is not None and values.shape!=shape) or not np.isfinite(values).all():
        raise ValueError('Contact telemetry has incomplete or nonfinite numerical data')
    return values


def frames(path):
    """A closed frame is the transaction boundary; truncated records refuse."""
    expected=contract();frame=None;seen=set();header=False
    if path.is_symlink() or not path.is_file() or path.stat().st_size>expected['maximum_output_bytes']:
        raise ValueError('Missing/unsafe/oversized contact telemetry')
    with path.open() as stream:
        for line in stream:
            row=json.loads(line);kind=row.get('type')
            if kind=='header':
                if header or frame is not None:raise ValueError('Duplicate contact telemetry header')
                if any(row.get(k)!=expected[k] for k in ('schema','source_sha256','instrumentation')) or row.get('upstream')!='2.23':
                    raise ValueError('Contact telemetry source/schema is not the pinned implementation')
                header=True
            elif kind=='frame':
                if not header or frame is not None:raise ValueError('Unclosed contact telemetry frame')
                identity=(row['step'],row['increment'])
                if identity in seen or any(not isinstance(v,int) or isinstance(v,bool) or v<1 for v in identity):
                    raise ValueError('Duplicate/invalid accepted contact increment')
                seen.add(identity);finite([row['solver_total_time'],row['increment_duration']])
                if row['increment_duration']<0:raise ValueError('Negative contact increment duration')
                frame={'meta':row,'nodes':{},'bulk':{},'points':[]}
            elif kind in {'node','bulk','point'}:
                if frame is None:raise ValueError('Contact record outside an accepted frame')
                if kind=='point':frame['points'].append(row)
                else:
                    target=frame['nodes' if kind=='node' else 'bulk'];identifier=row['id' if kind=='node' else 'element']
                    if not isinstance(identifier,int) or isinstance(identifier,bool) or identifier<1 or identifier in target:
                        raise ValueError('Duplicate/invalid contact output node or element')
                    target[identifier]=row
            elif kind=='end':
                if frame is None or row['points']!=len(frame['points']) or frame['meta']['contact_points']!=len(frame['points']):
                    raise ValueError('Contact frame point count is incomplete')
                if len(frame['nodes'])!=frame['meta']['node_label_max'] or len(frame['bulk'])!=frame['meta']['bulk_elements']:
                    raise ValueError('Contact frame node/bulk coverage is incomplete')
                frame['end']=row;yield frame;frame=None
            else:raise ValueError('Unknown contact telemetry record')
    if not header or frame is not None:raise ValueError('Truncated contact telemetry; no complete state inferred')


def audit_contact(directory,contact,case,result,force):
    system=contact.system;path=directory/'analysis.contact.jsonl'
    if not getattr(system,'_contact_build',None):return None
    records=contact.records;extent=max(float(np.linalg.norm(np.ptp(system.xyz,axis=0))),1e-300)
    elements={e.id:e for e in system.mesh.elements};faces={}
    for e in system.mesh.elements:
        for indices,face in zip(FACES,(1,2,4,3)):faces[e.id*10+face]=tuple(e.nodes[i] for i in indices)
    secondary={e*10+f:r for r in records for e,f in r['secondary_faces']}
    primary={r['id']:{e*10+f for e,f in r['primary_faces']} for r in records}
    alpha=.5854101966249685;beta=.1381966011250105
    points=np.array([[alpha if j==i else beta for j in range(4)] for i in range(4)])[:,1:]
    volume_gradients=[weights_and_gradients(p)[1] for p in points]
    volume_weights={e.id:np.array([np.linalg.det((np.array([system.mesh.nodes[n] for n in e.nodes])-system.mesh.nodes[e.nodes[0]]).T@g)/24 for g in volume_gradients]) for e in system.mesh.elements}
    if any(np.any(w<=0) for w in volume_weights.values()):raise ValueError('Invalid solver-order bulk energy quadrature')
    expected={(r['step'],r['increment']):r for r in result['checks']['increments']}
    load_path=system._load_path
    printed=json.loads((directory/'contact.json').read_text())['history']
    previous_u=np.zeros((len(system.ids),3));previous_f=np.zeros_like(previous_u)
    force_scale=max(load_path.force_scale_n,1e-300)
    work=0.;dissipation=0.;initial_energy=None;history=[];seen=set();maximum_errors={}
    elastic_scale=sum(system.assignments[b].youngs_modulus_mpa*v for b,v in system.body_volumes.items())
    for frame in frames(path):
        meta=frame['meta'];identity=(meta['step'],meta['increment']);time=meta['solver_total_time']-contact.initialization_period
        if set(frame['bulk'])!=set(elements) or meta['bulk_label_max']!=max(elements) or meta['node_label_max']!=max(system.ids):
            raise ValueError('Contact telemetry differs from the actual mesh labels')
        if set(frame['nodes'])!=set(range(1,max(system.ids)+1)):
            raise ValueError('Contact telemetry node label slots are incomplete')
        u=np.array([finite(frame['nodes'][n]['displacement_mm'],(3,)) for n in system.ids])
        previous=np.array([finite(frame['nodes'][n]['previous_displacement_mm'],(3,)) for n in system.ids])
        for n in system.ids:
            if not np.array_equal(finite(frame['nodes'][n]['reference_mm'],(3,)),np.array(system.mesh.nodes[n])):
                raise ValueError('Contact telemetry reference geometry differs from the exact solver input')
        loaded=meta['step']>1
        if loaded:
            if identity not in expected or not np.isclose(time,expected[identity]['total_time'],rtol=1e-6,atol=1e-9):
                raise ValueError('Contact telemetry is not an accepted completed solver increment')
            seen.add(identity)
        elif abs(time)>contact.initialization_period*1e-6:
            raise ValueError('Unexpected contact initialization load parameter')
        f=load_path.at(time).reshape(-1,3) if loaded else np.zeros_like(previous_u)
        work+=float(np.sum((f+previous_f)*(u-previous_u)))/2
        if loaded and np.max(np.linalg.norm(previous-previous_u,axis=1))>extent*1e-8:
            raise ValueError('Exported increment-start displacement differs from the previous accepted state')
        previous_u=u;previous_f=f
        bulk=sum(float(finite(frame['bulk'][e]['internal_energy_density_n_mm_mm3'],(4,))@volume_weights[e]) for e in elements)
        nodal={n:(np.array(system.mesh.nodes[n]),u[system.index[n]],previous[system.index[n]]) for n in system.ids}
        area_error=0.;total_area=0.;gap_error=0.;state_error=0.;spring_error=0.;slip_error=0.;diss=0.;spring=0.
        force_by_pair={r['id']:np.zeros(3) for r in records};primary_moment={r['id']:np.zeros(3) for r in records};secondary_moment={r['id']:np.zeros(3) for r in records}
        quadrature_ids=set()
        if {p['generated_element'] for p in frame['points']}!=set(range(max(elements)+1,max(elements)+1+len(frame['points']))):
            raise ValueError('Generated contact element coverage is incomplete')
        for point in frame['points']:
            code=point['secondary_face'];record=secondary.get(code)
            if record is None or point['quadrature_id'] in quadrature_ids:raise ValueError('Unknown/duplicate contact quadrature ownership')
            quadrature_ids.add(point['quadrature_id'])
            sn=point['secondary_nodes'];mn=point['primary_nodes'];uv=finite(point['secondary_uv_weight'],(3,));muv=finite(point['primary_uv_face_normal'],(6,))
            master_code=int(muv[2]);adjustments=finite(point['secondary_adjustments_mm'],(18,))
            if np.any(adjustments):raise ValueError('Contact telemetry includes an undeclared coordinate adjustment')
            if master_code not in primary[record['id']] or set(sn)!=set(faces[code]) or set(mn)!=set(faces[master_code]):
                raise ValueError('Exported contact face connectivity differs from its declared regions')
            for nodes,reference in [(sn,uv[:2]),(mn,muv[:2])]:
                if len(nodes)!=6 or len(set(nodes))!=6 or min(1-sum(reference),*reference)<-1e-7:raise ValueError('Invalid/extrapolated quadratic contact trace')
                reference_face=faces[code if nodes is sn else master_code]
                edges={frozenset((reference_face[a],reference_face[b])):reference_face[k+3] for k,(a,b) in enumerate([(0,1),(1,2),(2,0)])}
                if any(edges.get(frozenset((nodes[a],nodes[b])))!=nodes[k+3] for k,(a,b) in enumerate([(0,1),(1,2),(2,0)])):
                    raise ValueError('Contact midside edge order differs from the complete quadratic face')
            a,gradient=triangle_basis(uv[:2]);b,mg=triangle_basis(muv[:2])
            sx=np.array([nodal[n][0] for n in sn]);mx=np.array([nodal[n][0] for n in mn]);su=np.array([nodal[n][1] for n in sn]);mu=np.array([nodal[n][1] for n in mn])
            sp=np.array([nodal[n][2] for n in sn]);mp=np.array([nodal[n][2] for n in mn])
            jac=(sx+sp-sx[0]-sp[0]).T@gradient;area=float(np.linalg.norm(np.cross(jac[:,0],jac[:,1]))*uv[2])
            if area<=0:raise ValueError('Nonpositive independent contact quadrature area')
            stated_area=float(finite(point['solver_area_mm2']));area_error+=abs(area-stated_area);total_area+=area
            normal=muv[3:];norm=float(np.linalg.norm(normal))
            if abs(norm-1)>1e-9:raise ValueError('Contact telemetry normal is not a unit vector')
            mj=(mx+mp-mx[0]-mp[0]).T@mg;independent_normal=np.cross(mj[:,0],mj[:,1]);independent_normal/=np.linalg.norm(independent_normal)
            if np.dot(normal,independent_normal)<1-1e-6:raise ValueError('Frozen contact normal differs from independently reconstructed primary geometry')
            t1=(np.array([0.,0.,1.])-normal[2]*normal) if 1-abs(normal[0])<1.5231e-6 else np.array([1.,0.,0.])-normal[0]*normal
            t1/=np.linalg.norm(t1);t2=np.cross(normal,t1)
            values=finite(point['contact_displacement_stress'],(6,));pressure=values[3];tau=values[4]*t1+values[5]*t2
            xs=a@(sx+su);xm=b@(mx+mu)
            if loaded:
                left,right=load_path.times[meta['step']-2:meta['step']]
                relative_time=(time-left)/(right-left)
            else:relative_time=meta['solver_total_time']/contact.initialization_period
            correction=float(finite(point['first_step_clearance_correction_mm']))*(1-relative_time)
            gap_error=max(gap_error,abs(float((xs-xm)@normal)-correction-values[0]))
            spring_density=pressure**2/(2*record['normal_penalty_n_mm3'])
            if record['friction_coefficient']:spring_density+=float(tau@tau)/(2*record['tangential_penalty_n_mm3'])
            expected_spring=spring_density*area;stated_spring=float(finite(point['spring_energy_n_mm']))
            spring_error+=abs(expected_spring-stated_spring);spring+=expected_spring
            expected_dissipation=0.
            if record['friction_coefficient']:
                state=finite(point['state_plastic_relative_tangential'],(9,));old=finite(point['initial_state_plastic_relative_tangential'],(9,))
                expected_dissipation=float(tau@(state[:3]-old[:3]))*area
                state_error=max(state_error,float(np.linalg.norm(state[3:6]-(a@su-b@mu))))
            stated_dissipation=float(finite(point['friction_increment_n_mm']));slip_error+=abs(expected_dissipation-stated_dissipation);diss+=expected_dissipation
            traction=area*(pressure*normal-tau);force_by_pair[record['id']]+=traction
            secondary_moment[record['id']]+=np.cross(xs,traction);primary_moment[record['id']]+=np.cross(xm,-traction)
        footer=frame['end'];roundoff=max(extent*force_scale*np.finfo(float).eps*64,elastic_scale*np.finfo(float).eps**2*4096)*max(len(history),1)
        if abs(float(finite(footer['spring_energy_n_mm']))-spring)>max(abs(spring),roundoff)*1e-8 or abs(float(finite(footer['friction_increment_n_mm']))-diss)>max(abs(diss),roundoff)*1e-8:
            raise ValueError('Independent contact spring/slip integration differs from exported solver totals')
        if diss < -roundoff:raise ValueError('Contact produces negative frictional dissipation')
        if loaded:dissipation+=diss
        elif abs(bulk+spring)>roundoff or abs(diss)>roundoff:raise ValueError('Contact initialization introduced nonzero energy')
        if initial_energy is None:initial_energy=bulk+spring
        errors={'area_relative_error':area_error/max(total_area,1e-300),'gap_geometry_error_mm':gap_error,
            'relative_state_error_mm':state_error,'spring_energy_integration_relative_error':spring_error/max(abs(spring),roundoff),
            'slip_work_integration_relative_error':slip_error/max(abs(diss),roundoff)}
        for name,value in errors.items():maximum_errors[name]=max(maximum_errors.get(name,0.),float(value))
        if loaded:
            candidates=[value for key,value in printed.items() if np.isclose(float(key),time,rtol=1e-5,atol=1e-8)]
            if len(candidates)!=1:raise ValueError('Contact telemetry has no unambiguous printed resultant frame')
            resultant_error=0.;moment_error=0.
            for record in records:
                cf=finite(candidates[0]['resultants'][record['id']]['resultant'],(6,))
                resultant_error=max(resultant_error,float(np.linalg.norm(force_by_pair[record['id']]-cf[:3]))/force_scale)
                moment_error=max(moment_error,float(np.linalg.norm(secondary_moment[record['id']]-cf[3:]))/(force_scale*extent))
            if max(resultant_error,moment_error)>1e-5:raise ValueError('Independent quadrature forces/moments differ from printed contact resultants')
            history.append({'step':meta['step'],'increment':meta['increment'],'time_s':time,'solver_total_time':meta['solver_total_time'],
                'load_multipliers':load_path.factors(time),
                'bulk_internal_energy_n_mm':bulk,'contact_spring_energy_n_mm':spring,'friction_increment_n_mm':diss,
                'frictional_dissipation_n_mm':dissipation,'external_work_n_mm':work,
                'energy_residual_n_mm':bulk+spring+dissipation-initial_energy-work,
                'secondary_force_n':{k:v.tolist() for k,v in force_by_pair.items()},
                'secondary_moment_n_mm':{k:v.tolist() for k,v in secondary_moment.items()},
                'primary_moment_n_mm':{k:v.tolist() for k,v in primary_moment.items()},'errors':errors,
                'printed_resultant_relative_error':resultant_error,'printed_moment_relative_error':moment_error})
    if seen!=set(expected) or not history:raise ValueError('Contact telemetry misses accepted loading increments')
    scale=max(max(abs(r[k]) for r in history) for k in ('external_work_n_mm','bulk_internal_energy_n_mm','contact_spring_energy_n_mm','frictional_dissipation_n_mm'))
    scale=max(scale,roundoff,1e-300)
    for row in history:row['relative_energy_error']=abs(row['energy_residual_n_mm'])/scale
    if maximum_errors['area_relative_error']>1e-8 or maximum_errors['spring_energy_integration_relative_error']>1e-8 or maximum_errors['slip_work_integration_relative_error']>1e-8 or max(maximum_errors['gap_geometry_error_mm'],maximum_errors['relative_state_error_mm'])>extent*1e-8:
        raise ValueError('Independent contact geometry/state/work audit failed; no qualification inferred')
    final=history[-1]
    summary={'schema':1,'binary_sha256':system._contact_build['binary_sha256'],'receipt_sha256':system._contact_build['receipt_sha256'],
        'raw_telemetry_sha256':file_hash(path),'raw_telemetry_bytes':path.stat().st_size,
        'loading_frames':len(history),'maximum_errors':maximum_errors,'energy_scale_n_mm':scale,
        'energy_roundoff_bound_n_mm':roundoff,
        'energy_balance_error':max(r['relative_energy_error'] for r in history),
        'frictional_dissipation_n_mm':final['frictional_dissipation_n_mm'],
        'frictional_work_integrated':True,'contact_tangent_stability_qualified':False,
        'scope':'Independent full-quadratic geometry/traction/spring/plastic-slip integration and external-work balance; step/mesh convergence, tangent stability and physical validation remain distinct'}
    write_json(directory/'contact-work.json',{'summary':summary,'history':history})
    result['values'].update({k:final[k] for k in ('bulk_internal_energy_n_mm','contact_spring_energy_n_mm','frictional_dissipation_n_mm','external_work_n_mm')})
    result['checks'].update({'contact_work_audit':summary,'energy_balance_error':summary['energy_balance_error'],'frictional_work_integrated':True})
    result['contact_work_history']=history
    evidence=json.loads((directory/'contact.json').read_text());evidence.update({'work_audit':summary,'work_history':history,
        'frictional_dissipation_n_mm':final['frictional_dissipation_n_mm']})
    write_json(directory/'contact.json',evidence)
    return summary


def work_sensitivity(coarse,fine):
    """Compare complete cumulative slip-work trajectories on both time grids."""
    left=coarse['checks'].get('contact_work_audit');right=fine['checks'].get('contact_work_audit')
    if left is None or right is None:return None
    a=coarse['contact_work_history'];b=fine['contact_work_history']
    scale=max(max(abs(r['frictional_dissipation_n_mm']) for r in [*a,*b]),left['energy_roundoff_bound_n_mm'],right['energy_roundoff_bound_n_mm'],1e-300)
    times=np.unique([0.,*[r['time_s'] for r in a],*[r['time_s'] for r in b]])
    def sampled(rows):return np.interp(times,[0.,*[r['time_s'] for r in rows]],[0.,*[r['frictional_dissipation_n_mm'] for r in rows]])
    return float(np.max(abs(sampled(a)-sampled(b)))/scale)
