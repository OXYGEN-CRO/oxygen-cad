"""Explicit CalculiX 2.23 quadratic face-to-face penalty contact.

Surfaces retain their six-node owning volume faces. Geometry is never adjusted.
The initial rigid-space test counts normal contact only: friction is not an
invented mounting constraint. Contact output is experimental evidence, with
active-set tangent stability and integrated frictional work still unqualified.
"""
from __future__ import annotations
import re
import numpy as np
from .quadratic import FACES
from .solver import calculix_number,calculix_float
from .store import write_json


class SurfaceContact:
    def __init__(self,system):
        if system.mesh.element_family!='C3D10':raise ValueError('Surface penalty contact currently requires complete C3D10 solids')
        self.system=system;self.records=[];self.deck=[];self.output=[]
        owners={}
        for element in system.mesh.elements:
            for indices,face in zip(FACES,(1,2,4,3)):
                key=tuple(sorted(element.nodes[i] for i in indices))
                owners.setdefault(key,[]).append((element.id,face))
        used_secondary=set()
        for index,interface in enumerate(i for i in system.study.interfaces if i.kind!='disabled'):
            if interface.kind!='separation' or interface.formulation!='surface_to_surface_penalty':raise ValueError('Nonlinear surface contact does not discard or reinterpret bonds, thermal interfaces or paired-node penalties')
            regions=[system.regions.get(i) for i in (interface.primary_region_id,interface.secondary_region_id)]
            if any(r is None for r in regions) or regions[0].body_id==regions[1].body_id:raise ValueError('Contact requires existing primary/secondary regions on distinct bodies')
            record={'id':interface.id,'source':interface.source,'formulation':interface.formulation,'coordinate_adjustment':False,
                    'normal_penalty_n_mm3':interface.normal_penalty_n_mm3,'friction_coefficient':interface.friction_coefficient,
                    'tangential_penalty_n_mm3':interface.tangential_penalty_n_mm3,'declared_maximum_penetration_mm':interface.maximum_penetration_mm,
                    'position_tolerance_mm':interface.position_tolerance_mm}
            for role,region in zip(('primary','secondary'),regions):
                triangles=system.raw_mesh['surfaces'].get(region.id)
                if not triangles:raise ValueError('Contact requires complete quadratic exterior face regions, not a node selection')
                components={system.component_of[n] for t in triangles for n in t}
                if len(components)!=1:raise ValueError('Split contact regions across disconnected solids explicitly')
                name=f'CONTACT_{index}_{role.upper()}'
                self.deck.append(f'*SURFACE,NAME={name},TYPE=ELEMENT')
                faces=[]
                for triangle in triangles:
                    matches=owners.get(tuple(sorted(triangle)),[])
                    if len(matches)!=1:raise ValueError('Contact face must have exactly one owning volume element')
                    element,face=matches[0];self.deck.append(f'{element},S{face}');faces.append((element,face))
                nodes={n for t in triangles for n in t}
                if role=='secondary':
                    if nodes&used_secondary:raise ValueError('Secondary contact nodes belong to multiple pairs; separate ambiguous contact output ownership')
                    used_secondary.update(nodes)
                record.update({f'{role}_region_id':region.id,f'{role}_body_id':region.body_id,f'{role}_component':next(iter(components)),f'{role}_name':name,f'{role}_faces':faces})
            interaction=f'CONTACT_INTERACTION_{index}'
            self.deck.extend([f'*SURFACE INTERACTION,NAME={interaction}','*SURFACE BEHAVIOR,PRESSURE-OVERCLOSURE=LINEAR',calculix_number(interface.normal_penalty_n_mm3)])
            if interface.friction_coefficient:
                self.deck.extend(['*FRICTION',','.join(calculix_number(v) for v in (interface.friction_coefficient,interface.tangential_penalty_n_mm3))])
            self.deck.extend([f'*CONTACT PAIR,INTERACTION={interaction},TYPE=SURFACE TO SURFACE',f"{record['secondary_name']},{record['primary_name']}"])
            self.records.append(record)
        if not self.records:raise ValueError('Surface contact requires at least one active pair')
        self.output=['*CONTACT FILE,FREQUENCY=1','CDIS,CSTR,CELS','*CONTACT PRINT,FREQUENCY=1,TOTALS=YES','CDIS,CSTR,CELS,CNUM']
        for record in self.records:
            self.output.extend([f"*CONTACT PRINT,FREQUENCY=1,SLAVE={record['secondary_name']},MASTER={record['primary_name']}",'CF,CFN,CFS'])

    def validate_supports(self,case):
        system=self.system
        try:
            system.validate_supports(case)
            self.support_audit={'method':'each solid independently restrained','uses_contact_for_rigid_support':False}
            return
        except ValueError as error:
            if 'unrestrained rigid body modes' not in str(error):raise
        # Necessary initial normal-contact rank, not a friction assumption or a
        # spring added to hide a mechanism. Gapped/partially overlapping patches
        # cannot be used to repair an initially unsupported load path here.
        from .simulation_mortar import mortar_map
        from .simulation_bonding import triangle_basis
        count=len(system.solid_components)
        if count>32:raise ValueError('Contact rigid-space audit exceeds 32 solids; partition/qualify the assembly explicitly')
        center=system.xyz.mean(axis=0);length=max(float(np.linalg.norm(np.ptp(system.xyz,axis=0))),1e-12)
        rows=[];reduced=np.empty((0,6*count))
        def rigid(point):
            x,y,z=(point-center)/length
            return np.column_stack((np.eye(3),np.array([[0,z,-y],[-z,0,x],[y,-x,0]])))
        def add(row):
            nonlocal rows,reduced
            norm=np.linalg.norm(row)
            if norm:rows.append(row/norm)
            if len(rows)>=512:
                reduced=np.linalg.qr(np.vstack((reduced,rows)),mode='r');rows=[]
        for support in case.supports:
            if support.kind not in {'fixed','partial'}:raise ValueError('Contact supports require fixed/world-axis partial components')
            for i in system.region_nodes(support.region_id):
                n=system.ids[i];component=system.component_of[n]
                for axis,active in enumerate((True,True,True) if support.kind=='fixed' else support.components):
                    if active:
                        row=np.zeros(6*count);row[6*component:6*component+6]=rigid(system.xyz[i])[axis];add(row)
        for record in self.records:
            _,_,patches,_,_,_=mortar_map(system,record['primary_region_id'],record['secondary_region_id'],min(record['position_tolerance_mm'],length*1e-10))
            for sn,mn,ns,nm,_ in patches:
                xyz=np.array([system.mesh.nodes[n] for n in sn]);master=np.array([system.mesh.nodes[n] for n in mn])
                for s,m in zip(ns,nm):
                    r=s[1]+(s[3]+s[4])/2;t=s[2]+(s[4]+s[5])/2
                    _,gradient=triangle_basis((r,t));jacobian=(xyz-xyz[0]).T@gradient
                    normal=np.cross(jacobian[:,0],jacobian[:,1]);normal/=np.linalg.norm(normal)
                    row=np.zeros(6*count)
                    for role,sign,point in [('secondary',1,s@xyz),('primary',-1,m@master)]:
                        component=record[f'{role}_component'];row[6*component:6*component+6]+=sign*normal@rigid(point)
                    add(row)
        matrix=np.vstack((reduced,rows)) if rows else reduced
        rank=int(np.linalg.matrix_rank(matrix,tol=1e-9))
        if rank!=6*count:raise ValueError('Contact assembly has an initial unrestrained rigid mechanism; friction is not counted as an artificial mounting constraint')
        self.support_audit={'method':'supports plus coincident full-quadratic initial normal contact rigid-space rank','rank':rank,'rigid_dofs':6*count,'uses_contact_for_rigid_support':True,'tangential_friction_counted_as_support':False,'scope':'necessary initial rank only; active-set tangent stability throughout the load path remains unqualified'}


def normalize_contact_history(contact,history,frames,vectors,increments,convergence,force):
    """Audit an unloaded first step, then expose the supplied load parameter."""
    offset=contact.initialization_period;system=contact.system
    initial=[h for h in history if np.isclose(h['time'],offset,rtol=1e-6,atol=0)]
    loaded=[h for h in history if h['time']>offset*(1+1e-6)]
    if not initial or not loaded or not any(r['step']==1 and np.isclose(r['total_time'],offset,rtol=1e-6,atol=0) for r in increments):raise RuntimeError('Contact unloaded initialization step did not complete unambiguously')
    extent=max(float(np.linalg.norm(np.ptp(system.xyz,axis=0))),1e-12)
    initial_vectors=vectors.get(initial[0]['time'])
    if initial_vectors is None or any(set(initial_vectors.get(k,{}))!=set(system.ids) for k in ('displacements','reactions')):raise RuntimeError('Incomplete contact initialization displacement/reaction audit')
    initial_force=float(np.linalg.norm(np.array(list(initial_vectors['reactions'].values())),axis=1).sum())
    initial_displacement=max(r['max_displacement_mm'] for r in initial)
    if initial_displacement>extent*1e-10 or initial_force>max(system._load_path.force_scale_n,1.)*1e-8:raise RuntimeError('Unloaded contact initialization moved/loaded the design; initial interference/prestress requires explicit modeling')
    initial_contact=[f for f in frames if f['name']=='CONTACT' and np.isclose(f['time'],offset,rtol=1e-6,atol=0)]
    if not initial_contact or any(set(f['values'])!=set(system.ids) for f in initial_contact):raise RuntimeError('Missing initial contact stress audit')
    initial_pressure=max(abs(v[3]) for f in initial_contact for v in f['values'].values())
    precision=max(r['normal_penalty_n_mm3'] for r in contact.records)*extent*np.finfo(float).eps*64
    if initial_pressure>precision:raise RuntimeError('Unloaded contact initialization has nonzero contact pressure; no hidden interference fit is accepted')
    for row in loaded:row.update({'solver_total_time':row['time'],'time':row['time']-offset})
    active_frames=[]
    for frame in frames:
        if frame['time']>offset*(1+1e-6):
            frame.update({'solver_total_time':frame['time'],'time':frame['time']-offset});active_frames.append(frame)
    shifted_vectors={t-offset:v for t,v in vectors.items() if t>offset*(1+1e-6)}
    return loaded,active_frames,shifted_vectors,{'unloaded_first_step_period':offset,'initialization_output_frames':len(initial),'maximum_displacement_mm':initial_displacement,
        'external_force_sum_norm_n':initial_force,'maximum_contact_pressure_mpa':initial_pressure,'coordinate_adjustment':False,
        'time_mapping':'supplied quasi-static load parameter = solver total time minus unloaded initialization period; raw total time retained',
        'reason':'CalculiX 2.23 first-step penetration removal must finish before the supplied loading path'}


def read_contact(directory,contact,case,result,frames,vector_history,force,constrained):
    """Retain contact integration-point records and independently check each body."""
    system=contact.system;records=contact.records
    times=[r['time'] for r in result['load_displacement_history']]
    data={t:{'displacement':[],'stress':[],'spring_energy_n_mm':None,'contact_elements':None,'resultants':{}} for t in times}
    pairs={(r['secondary_name'],r['primary_name']):r for r in records}
    face_owner={tuple(f):r for r in records for f in r['secondary_faces']}
    current=None;when=None;pair=None;expect=None
    for line in (directory/'analysis.dat').read_text(errors='replace').splitlines():
        stripped=line.strip();lower=stripped.lower()
        if ' and time ' in lower or lower.startswith(('total contact spring energy for time','total number of contact elements for time')):
            value=float(stripped.split()[-1].replace('D','E'))-contact.initialization_period
            if abs(value)<=contact.initialization_period*1e-6:
                when=None;current=None;expect=None;pair=None
                continue
            matches=[t for t in times if np.isclose(t,value,rtol=1e-5,atol=1e-8)]
            if len(matches)!=1:raise RuntimeError('Contact output has an unknown or ambiguous increment time')
            when=matches[0];current=None;expect=None;pair=None
            if lower.startswith('relative contact displacement'):current='displacement'
            elif lower.startswith('contact stress ('):current='stress'
            elif lower.startswith('total contact spring energy'):expect='energy'
            elif lower.startswith('total number of contact elements'):expect='count'
            elif lower.startswith('statistics for slave set '):
                match=re.match(r'statistics for slave set (\S+), master set (\S+) and time',stripped,re.I)
                if not match or tuple(match.groups()) not in pairs:raise RuntimeError('Unknown contact resultant pair')
                pair=pairs[tuple(match.groups())]
        elif pair is not None and lower.startswith('total surface force '):expect='resultant'
        elif pair is not None and lower.startswith('total normal surface force '):expect='normal'
        elif pair is not None and lower.startswith('total shear surface force '):expect='shear'
        elif stripped:
            parts=stripped.split()
            if current is not None and len(parts)==5 and parts[0].isdigit() and parts[1].isdigit():
                element,face=int(parts[0]),int(parts[1]);values=[calculix_float(v) for v in parts[2:]]
                if (element,face) not in face_owner or not np.isfinite(values).all():raise RuntimeError('Unknown/nonfinite contact integration-point output')
                data[when][current].append([element,face,*values])
            elif expect=='energy' and len(parts)==1:
                data[when]['spring_energy_n_mm']=calculix_float(parts[0]);expect=None
            elif expect=='count' and len(parts)==1:
                data[when]['contact_elements']=int(parts[0]);expect=None
            elif expect in {'resultant','normal','shear'} and len(parts)==6:
                values=[calculix_float(v) for v in parts]
                if not np.isfinite(values).all():raise RuntimeError('Nonfinite contact resultant')
                entry=data[when]['resultants'].setdefault(pair['id'],{})
                if expect in entry:raise RuntimeError('Duplicate contact resultant')
                entry[expect]=values;expect=None
            elif current is not None:current=None
    checks=[];maximum_penetration=0.;penalty_error=0.;coulomb_error=0.;penetration_ratio=0.
    load_path=system._load_path
    for time,row in data.items():
        if row['spring_energy_n_mm'] is None or not np.isfinite(row['spring_energy_n_mm']) or row['spring_energy_n_mm']<0:raise RuntimeError('Missing/nonfinite/negative contact spring energy')
        displacements=row['displacement'];stresses=row['stress']
        if len(displacements)!=len(stresses) or [v[:2] for v in displacements]!=[v[:2] for v in stresses]:raise RuntimeError('Contact displacement/stress integration points do not match')
        if row['contact_elements']!=len(stresses):raise RuntimeError('Contact integration-point count disagrees with independently printed CNUM')
        energy_frames=[f for f in frames if f['name']=='CELS' and np.isclose(f['time'],time,rtol=1e-5,atol=1e-8)]
        if len(energy_frames)!=1 or (not energy_frames[0]['values'] and (row['contact_elements']!=0 or row['spring_energy_n_mm']!=0)):raise RuntimeError('An empty contact energy field is permitted only with zero contact count and zero energy')
        pair_peaks={r['id']:{'maximum_penetration_mm':0.,'maximum_pressure_mpa':0.,'maximum_shear_mpa':0.} for r in records}
        for d,s in zip(displacements,stresses):
            record=face_owner[tuple(d[:2])];gap=float(d[2]);pressure=float(s[2]);shear=float(np.linalg.norm(s[3:]));penetration=max(-gap,0.)
            scale=max(abs(pressure),record['normal_penalty_n_mm3']*penetration,1e-300)
            penalty_error=max(penalty_error,abs(pressure-record['normal_penalty_n_mm3']*penetration)/scale)
            coulomb_error=max(coulomb_error,max(shear-record['friction_coefficient']*pressure,0.)/max(abs(pressure),shear,1e-300),max(-pressure,0.)/scale)
            maximum_penetration=max(maximum_penetration,penetration);penetration_ratio=max(penetration_ratio,penetration/record['declared_maximum_penetration_mm'])
            peak=pair_peaks[record['id']];peak['maximum_penetration_mm']=max(peak['maximum_penetration_mm'],penetration);peak['maximum_pressure_mpa']=max(peak['maximum_pressure_mpa'],pressure);peak['maximum_shear_mpa']=max(peak['maximum_shear_mpa'],shear)
        applied=load_path.at(load_path.output_time(time)).reshape(-1,3)
        vectors=vector_history[time];raw=np.array([vectors['reactions'][n] for n in system.ids]);reacted=np.where(constrained,raw-applied,0.)
        xyz=system.xyz+(np.array([vectors['displacements'][n] for n in system.ids]) if system.study.settings.large_deformation else 0.)
        forces={};moments={};scale=max(load_path.force_scale_n,1e-300)
        for body,nodes in system.body_nodes.items():
            indices=[system.index[n] for n in nodes];external=applied[indices]+reacted[indices]
            forces[body]=external.sum(axis=0);moments[body]=np.cross(xyz[indices],external).sum(axis=0)
        for record in records:
            values=row['resultants'].get(record['id'])
            if values is None or set(values)!={'resultant','normal','shear'}:raise RuntimeError('Missing complete contact pair force/moment resultants')
            total=np.array(values['resultant']);components=np.array(values['normal'])+np.array(values['shear'])
            if np.linalg.norm(total-components)>1e-5*max(np.linalg.norm(total),scale):raise RuntimeError('Contact normal/shear resultants do not sum to the total')
            for role,sign in [('secondary',1),('primary',-1)]:
                body=record[f'{role}_body_id'];forces[body]+=sign*total[:3];moments[body]+=sign*total[3:]
        length=max(float(np.linalg.norm(np.ptp(xyz,axis=0))),1e-12)
        checks.append({'time_s':time,'force_balance_error':max(float(np.linalg.norm(f))/scale for f in forces.values()),'moment_balance_error':max(float(np.linalg.norm(m))/(scale*length) for m in moments.values()),'body_force_defect_n':{b:f.tolist() for b,f in forces.items()},'body_moment_defect_n_mm':{b:m.tolist() for b,m in moments.items()},'pairs':pair_peaks,'spring_energy_n_mm':row['spring_energy_n_mm']})
    contact_frames=[f for f in frames if f['name']=='CONTACT' and np.isclose(f['time'],times[-1],rtol=1e-5,atol=1e-8)]
    if len(contact_frames)!=1 or set(contact_frames[0]['values'])!=set(system.ids) or any(len(v)!=6 or not np.isfinite(v).all() for v in contact_frames[0]['values'].values()):raise RuntimeError('Incomplete final nodal contact field')
    result['field']['nodal_contact']=contact_frames[0]['values']
    result['field']['contact_secondary_node_ids']=sorted({n for r in records for t in system.raw_mesh['surfaces'][r['secondary_region_id']] for n in t})
    result['field']['contact_components']=['clipped_normal_gap_mm','tangential_relative_1_mm','tangential_relative_2_mm','pressure_mpa','shear_1_mpa','shear_2_mpa']
    result['field']['contact_gap_convention']='COPEN stores penetration as negative; positive opening is clipped to zero by CalculiX and is not measured by this field'
    result['interfaces']=[{**r,'final_secondary_resultant_n':data[times[-1]]['resultants'][r['id']]['resultant'][:3],**checks[-1]['pairs'][r['id']]} for r in records]
    result['checks'].update({'contact_initial_support_audit':contact.support_audit,'contact_body_equilibrium':checks,'contact_body_force_balance_error':max(v['force_balance_error'] for v in checks),'contact_body_moment_balance_error':max(v['moment_balance_error'] for v in checks),'maximum_contact_penetration_mm':maximum_penetration,'maximum_contact_penetration_limit_ratio':penetration_ratio,'contact_pressure_law_error':penalty_error,'contact_coulomb_bound_error':coulomb_error,'contact_active_set_stability_qualified':False,'frictional_work_qualified':not any(r['friction_coefficient'] for r in records)})
    result['warnings'].extend(['Experimental face-to-face contact: pairing updates between increments; step/penalty sensitivity and active-set stability require qualification.','Contact spring energy is recoverable penalty energy, not accumulated frictional dissipation.','Positive contact opening is clipped in solver output; zero COPEN does not establish closed contact.'])
    write_json(directory/'contact.json',{'pairs':records,'initial_support_audit':contact.support_audit,'history':data,'checks':checks,'contact_point_order':'solver integration-point order within each element/face; coordinates and integration weights are not printed by CONTACT PRINT','frictional_dissipation_n_mm':None})
