"""Native laminar incompressible flow with explicit model and numerical evidence."""
from pathlib import Path
import re
import numpy as np
from .simulation_flow_case import write_case
from .simulation_flow_fields import read_field,values


def validate_inputs(study,model,materials):
    from .simulation_models import SolverSettings
    defaults=SolverSettings()
    for name in type(defaults).model_fields:
        if name not in {'residual_tolerance','balance_tolerance','convergence_tolerance'} and getattr(study.settings,name)!=getattr(defaults,name):
            raise ValueError('Fluid flow does not translate solid/event solver setting: '+name)
    if study.flow is None:raise ValueError('Declare the fluid-volume idealization and steady laminar incompressible model')
    if study.interfaces or study.bolts or study.search or study.stress_free_temperature_k is not None:
        raise ValueError('Fluid flow does not translate solid interfaces, bolts, search or thermal-stress settings')
    if study.settings.large_deformation or study.settings.inertial_relief or study.settings.probe_point_mm is not None or study.settings.probe_body_id is not None:
        raise ValueError('Fluid flow does not translate solid deformation, inertial relief or displacement probes')
    if len(model.body_ids)!=1 or len(study.assignments)!=1 or study.assignments[0].body_id!=model.body_ids[0]:
        raise ValueError('Assign exactly one explicit fluid material to one fluid volume; conjugate solid regions are unavailable')
    material=materials[study.assignments[0].material_id]
    for prop in ['density_g_cm3','dynamic_viscosity_pa_s']:
        if getattr(material,prop) is None or not material.property_sources.get(prop,'').strip():
            raise ValueError('Fluid '+prop+' requires its own value and property source')
    if material.plastic_curve:raise ValueError('Plastic solid curves are not a Newtonian fluid law')
    return material


def mesh_report(text):
    # checkMesh may return exit code zero while reporting Failed mesh checks.
    if not re.search(r'^Mesh OK\.$',text,re.M) or re.search(r'Failed\s+\d+\s+mesh checks|FOAM FATAL|\*\*\*',text):
        raise ValueError('Native full topology/geometry check did not qualify the finite-volume mesh; inspect checkMesh.log')
    return {'full_topology_geometry_passed':True}


def final_iteration(directory,log,tolerance):
    match=re.search(r'^SIMPLE solution converged in (\d+) iterations\s*$',log,re.M)
    times=list(re.finditer(r'^Time = (\d+)\s*$',log,re.M))
    if not match or not times or 'FOAM FATAL' in log:raise ValueError('Native SIMPLE iteration did not converge; inspect simpleFoam.log')
    latest=int(times[-1].group(1));tail=log[times[-1].end():]
    residuals={}
    for name in ['Ux','Uy','Uz','p']:
        matches=re.findall(r'Solving for '+name+r', Initial residual = ([^,\s]+)',tail)
        if not matches:raise ValueError('Native convergence report lacks '+name+' equation evidence')
        residuals[name]=max(float(v) for v in matches)
    if not all(np.isfinite(v) and v<=tolerance for v in residuals.values()):raise ValueError('Native final equation residual exceeds the requested tolerance')
    steps=[int(p.name) for p in Path(directory).iterdir() if p.is_dir() and re.fullmatch(r'[1-9]\d*',p.name)]
    if not steps or max(steps)!=latest or int(match.group(1))!=latest:raise ValueError('Native written field and converged iteration differ')
    return latest,residuals


def solve_mesh(mesh,material,case,settings,solver_settings,directory,runtime,cancel=None):
    from .solver import run_process
    from .store import write_json
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    data=write_case(mesh,material,case,settings,directory,solver_settings.residual_tolerance)
    run_process([runtime['binaries']['checkMesh'],'-case',directory,'-allTopology','-allGeometry'],directory,directory/'checkMesh.log',cancel,timeout=180,environment_overrides=runtime['environment'])
    quality=mesh_report((directory/'checkMesh.log').read_text())
    run_process([runtime['binaries']['simpleFoam'],'-case',directory],directory,directory/'simpleFoam.log',cancel,timeout=600,environment_overrides=runtime['environment'])
    iteration,residuals=final_iteration(directory,(directory/'simpleFoam.log').read_text(),solver_settings.residual_tolerance)
    folder=directory/str(iteration);count=len(data['cell_ids'])
    velocity,_=read_field(folder/'U',count,3,[0,1,-1,0,0,0,0],'volVectorField')
    pressure,_=read_field(folder/'p',count,1,[0,2,-2,0,0,0,0],'volScalarField');pressure*=data['density_kg_m3']
    internal,patch_fields=read_field(folder/'phi',data['internal_faces'],1,[0,3,-1,0,0,0,0],'surfaceScalarField')
    if set(patch_fields)!={p['name'] for p in data['patches']}:raise ValueError('Native flux patches differ from the executed mesh')
    flux=list(internal);patch_flux=[]
    for patch in data['patches']:
        face_flux=values(patch_fields[patch['name']].get('value'),patch['face_count'],1)
        flux.extend(face_flux);patch_flux.append({'boundary_id':patch['boundary_id'],'region_id':patch['region_id'],
            'volume_flux_m3_s':float(sum(face_flux)),'mass_flux_kg_s':float(sum(face_flux)*data['density_kg_m3'])})
    flux=np.array(flux);divergence=np.zeros(count);throughput=np.zeros(count)
    for face,q in zip(data['faces'],flux,strict=True):
        divergence[face['owner']]+=q;throughput[face['owner']]+=abs(q)
        if face['neighbour'] is not None:divergence[face['neighbour']]-=q;throughput[face['neighbour']]+=abs(q)
    boundary=flux[data['internal_faces']:];scale=max(float(sum(abs(boundary))),float(sum(data['volumes_m3']))**(2/3)*float(np.linalg.norm(velocity,axis=1).max()),1e-300)
    balance=abs(float(sum(boundary)))/scale;local=float(sum(abs(divergence)))/scale
    speed=np.linalg.norm(velocity,axis=1);volumes=data['volumes_m3'];volume=float(sum(volumes))
    extent=float(np.ptp(data['coordinates_m'],axis=0).max());maximum=float(max(speed))
    checks={'residual':max(residuals.values()),'equation_residuals':residuals,'mass_balance_error':balance,
        'local_continuity_error':local,'maximum_cell_relative_flux_imbalance':float(max(abs(divergence)/np.maximum(throughput,1e-300))),
        'native_mesh':quality,'pressure_field_spatial_accuracy_qualified':False,'simple_iterations':iteration,'reynolds_number_extent_speed':maximum*extent/data['kinematic_viscosity_m2_s'],
        'reynolds_scope':'Diagnostic maximum extent and cell-centre speed; laminar regime/steady stability is a declared model assumption, not established by this number'}
    metrics={'max_speed_m_s':maximum,'rms_speed_m_s':float(np.sqrt(np.dot(volumes,speed**2)/volume)),
        'pressure_range_pa':float(np.ptp(pressure)),'mass_g':data['density_kg_m3']*volume*1000}
    # Visual recovery only: average adjacent vertex control-volume chunks with
    # their SI volumes. All reported quantities above use actual native cells.
    node_ids=data['node_ids'];indexes={n:i for i,n in enumerate(node_ids)};weights=np.zeros(len(node_ids));u=np.zeros((len(node_ids),3));p=np.zeros(len(node_ids))
    for n,v,uv,pv in zip(data['source_vertex_ids'],volumes,velocity,pressure,strict=True):
        i=indexes[n];weights[i]+=v;u[i]+=uv*v;p[i]+=pv*v
    u/=weights[:,None];p/=weights
    field={'nodes':{n:list(mesh.nodes[n]) for n in node_ids},'velocity_m_s':{n:u[i].tolist() for i,n in enumerate(node_ids)},
        'pressure_gauge_pa':{n:float(p[i]) for i,n in enumerate(node_ids)},
        'native_flow_cells':{'centers_m':data['centers_m'].tolist(),'volumes_m3':volumes.tolist(),'source_element_ids':data['source_element_ids'],
            'source_vertex_ids':data['source_vertex_ids'],'velocity_m_s':velocity.tolist(),'pressure_gauge_pa':pressure.tolist(),
            'face_volume_flux_m3_s':flux.tolist(),'cell_net_volume_flux_m3_s':divergence.tolist()},
        'flow_display_scope':'Volume-weighted recovery of native finite-volume chunks at source mesh vertices; visual interpolation only; no displacement deformation'}
    write_json(directory/'runtime-profile.json',runtime['identity'])
    record={'schema':1,'model':'steady_laminar_incompressible_newtonian','source_geometry_unit':'mm','native_length_unit':'m',
        'density_kg_m3':data['density_kg_m3'],'dynamic_viscosity_pa_s':data['dynamic_viscosity_pa_s'],
        'kinematic_pressure_conversion':'p_Pa = p_native * density_kg_m3','native_cells':count,'native_points':len(data['coordinates_m']),
        'source_tetrahedra':len(mesh.elements),'patches':data['patches'],'patch_fluxes':patch_flux,'checks':checks,'values':metrics}
    write_json(directory/'flow-evidence.json',record)
    from .simulation_flow_evidence import bind
    bound=bind(directory)
    return {'bound_evidence':bound,'values':metrics,'checks':checks,'patch_fluxes':patch_flux,'field':field,'native_cells':count}


def solve_study(request,domain,study,model,case,directory,software,cancel=None):
    from .simulation_flow_profile import profile
    from .simulation_mesh import validate_boundary,display_triangles,exterior_triangles
    from .simulation_provenance import software_fingerprint
    from .simulation_worker import ADAPTER_VERSION
    from .model import digest
    runtime=profile();material=validate_inputs(study,model,{m.id:m for m in domain.materials})
    if not study.assumptions_confirmed:raise ValueError('Review and confirm the flow assumptions')
    if model.geometry_key!=request['geometry_key']:raise ValueError('Fluid analysis model belongs to stale geometry')
    meshes=[model.mesh,*model.refinements]
    if meshes[0] is None:raise ValueError('Supply an explicit fluid-volume control mesh')
    previous=None;previous_mesh=None;previous_fields=None;previous_resolution=None;seen=set();iterations=[];evidence=[];converged=False
    for i,mesh in enumerate(meshes):
        if mesh.geometry_key!=request['geometry_key'] or {e.body_id for e in mesh.elements}!=set(model.body_ids):raise ValueError('Fluid mesh belongs to stale geometry or different bodies')
        validate_boundary(mesh)
        canonical=sorted(sorted(tuple(mesh.nodes[n]) for n in e.nodes) for e in mesh.elements);key=digest(canonical)
        if key in seen:raise ValueError('Identical discretization cannot demonstrate flow mesh convergence')
        seen.add(key)
        result=solve_mesh(mesh,material,case,study.flow,study.settings,Path(directory)/f'mesh-{i}'/'flow',runtime,cancel)
        evidence.append({'path':f'mesh-{i}/flow',**result.pop('bound_evidence')})
        cell_volumes=result['field']['native_flow_cells']['volumes_m3'];resolution=(len(mesh.nodes),len(mesh.elements),max(cell_volumes))
        if previous_resolution and not (resolution[0]>previous_resolution[0] and resolution[1]>previous_resolution[1] and resolution[2]<previous_resolution[2]*(1-1e-6)):
            raise ValueError('Flow convergence requires genuinely finer cells, more nodes and more elements')
        previous_resolution=resolution;current=result['values'];changes={}
        if previous is not None:
            speed_scale=max(current['max_speed_m_s'],previous['max_speed_m_s'],1e-12)
            pressure_scale=max(current['pressure_range_pa'],previous['pressure_range_pa'],material.density_g_cm3*1000*speed_scale**2,1e-12)
            for name in ['max_speed_m_s','rms_speed_m_s','pressure_range_pa']:
                changes[name]=abs(current[name]-previous[name])/(pressure_scale if name=='pressure_range_pa' else speed_scale)
        if previous_fields is not None:
            from .simulation_flow_convergence import sampled_change
            changes.update(sampled_change(previous_mesh,previous_fields,result['field']['native_flow_cells'],material.density_g_cm3*1000,cancel))
        previous_mesh=mesh;previous_fields=result['field']['native_flow_cells']
        converged=bool(changes) and max(changes.values())<=study.settings.convergence_tolerance
        iterations.append({'mesh_id':mesh.id,'nodes':len(mesh.nodes),'elements':len(mesh.elements),'element_family':mesh.element_family,
            'native_finite_volumes':result['native_cells'],'mesh_size_mm':mesh.mesh_size_mm,'values':current,'relative_changes':changes})
        previous=current
    result['field'].update({'boundary_triangles':display_triangles(mesh),'boundary_faces':exterior_triangles(mesh.nodes,mesh.elements),
        'element_connectivity':{e.id:list(e.nodes) for e in mesh.elements}})
    failures=['pressure-field spatial accuracy on barycentric control volumes is not qualified']
    for name in ['mass_balance_error','local_continuity_error']:
        if result['checks'][name]>study.settings.balance_tolerance:failures.append(name)
    if not converged:failures.append('flow mesh convergence not demonstrated')
    result['checks'].update({'mesh_converged':converged,'convergence_scope':'Native cell-centre maximum speed, volume RMS speed, pressure range and physical fine-cell sampled native-field changes (pressure demeaned); no continuous/global field-error bound',
        'residual_tolerance_used':study.settings.residual_tolerance,'converged_quantities':list(iterations[-1]['relative_changes']) if converged else []})
    findings=[]
    for limit in study.limits:
        value=result['values'].get(limit.quantity);met=None if value is None else bool(value<=limit.value if limit.operator=='le' else value>=limit.value)
        findings.append({'id':limit.id,'quantity':limit.quantity,'value':value,'limit':limit.value,'operator':limit.operator,'satisfied':met,'source':limit.source,
            'evidence':'specified steady laminar incompressible numerical model','verified':not failures and met is not None})
    result.update({'schema_version':1,'adapter_version':ADAPTER_VERSION,'input_key':request['input_key'],'geometry_key':request['geometry_key'],'revision':request['revision'],
        'study_id':study.id,'case_id':case.id,'study_type':study.type,'numerical_state':'inconclusive' if failures else 'verified_numerically','verification_gaps':failures,
        'requirements_state':'unassessed' if not findings else 'inconclusive' if failures or any(not f['verified'] for f in findings) else 'not_satisfied' if any(f['satisfied'] is False for f in findings) else 'satisfied',
        'findings':findings,'mesh_iterations':iterations,'flow_evidence':evidence,'software':{'adapter':ADAPTER_VERSION,'fingerprint':software},'physical_validation':'not_supplied',
        'solver':'OpenCFD OpenFOAM 1912 simpleFoam · steady laminar incompressible Newtonian',
        'units':{'length':'mm (source/display); m (native fluid solver)','velocity':'m/s','pressure':'Pa gauge','mass_flux':'kg/s','volume_flux':'m3/s','dynamic_viscosity':'Pa s'},
        'field_location':{'velocity':'native cell-centre; source-vertex volume recovery for display','pressure':'native cell-centre; source-vertex volume recovery for display'},
        'warnings':['Experimental pressure-field accuracy hold: the pressure-driven analytical benchmark has unresolved spatial pressure oscillations; every flow result remains inconclusive.',
            'Explicit fluid volume required; this adapter does not subtract solid geometry to invent an air domain.',
            'Steady laminar incompressible Newtonian flow only. Turbulence, compressibility, buoyancy, free surfaces, transient flow and conjugate heat transfer are unavailable.',
            'Numerical checks apply only to the stated model and reported convergence quantities. No hardware or flow-regime correlation was supplied.']})
    if software_fingerprint(study.type,'C3D4')!=software:raise ValueError('Flow solver software changed during execution; results refused')
    return result
