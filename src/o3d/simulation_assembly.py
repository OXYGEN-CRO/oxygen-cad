"""Canonical full-quadratic perfect-bonded assembly elasticity.

Bulk stiffness is shared with the consistent quadratic adapter. Physical surface
constraints are separately assembled and exactly eliminated, never penalties.
"""
from pathlib import Path
import hashlib
import numpy as np
from scipy import sparse
from .simulation_eigen import QuadraticElasticSystem,MAX_COMPONENT_DOFS,check_cancel
from .simulation_bonding import BondedSurfaces
from .simulation_fem import solve_reduced
from .quadratic import weights_and_gradients
from .store import write_json


class BondedAssemblySystem(QuadraticElasticSystem):
    def __init__(self,mesh,study,materials,cancel=None):
        if study.type!='linear_static' or study.bolts or study.settings.inertial_relief:
            raise ValueError('Quadratic perfect-bonded assembly currently requires linear static without bolts/inertial relief')
        if any(i.kind!='disabled' and (i.kind!='bonded' or i.formulation!='quadratic_surface_mortar') for i in study.interfaces):
            raise ValueError('Quadratic assembly does not reinterpret paired springs, separation or thermal interfaces')
        # Assemble only the declared bulk material operator, then consume every
        # active interface through its distinct explicit kinematic operator.
        bulk=study.model_copy(update={'interfaces':[]})
        super().__init__(mesh,bulk,materials,cancel)
        self.study=study
        self.bonding=BondedSurfaces(self,cancel)


def stress_fields(system,u,cancel=None):
    corner=np.eye(4)[:,1:];edges=[(0,1),(1,2),(2,0),(0,3),(1,3),(2,3)]
    nodes=np.vstack([corner,*[(corner[a]+corner[b])[None,:]/2 for a,b in edges]])
    alpha=.5854101966249685;beta=.1381966011250105
    points=np.array([[alpha if j==i else beta for j in range(4)] for i in range(4)])[:,1:]
    gradients=np.array([weights_and_gradients(p)[1] for p in np.vstack([nodes,points])])
    recovery=np.zeros((len(system.ids),6));weights=np.zeros(len(system.ids));samples={};body_peaks={b:0. for b in system.assignments}
    for start in range(0,len(system.mesh.elements),128):
        check_cancel(cancel)
        inds=system.connectivity[start:start+128];xyz=system.xyz[inds];displacements=u.reshape(-1,3)[inds]
        jac=np.einsum('eni,qnj->eqij',xyz-xyz[:,:1],gradients)
        if np.any(np.linalg.det(jac)<=0):raise ValueError('Invalid physical stress evaluation Jacobian')
        physical=np.einsum('qni,eqij->eqnj',gradients,np.linalg.inv(jac))
        du=np.einsum('ena,eqnb->eqab',displacements-displacements[:,:1],physical)
        strain=(du+du.transpose(0,1,3,2))/2
        cells=system.mesh.elements[start:start+128];materials=[system.assignments[e.body_id] for e in cells]
        young=np.array([m.youngs_modulus_mpa for m in materials]);nu=np.array([m.poisson_ratio for m in materials]);mu=young/(2*(1+nu));lam=young*nu/((1+nu)*(1-2*nu))
        stress=2*mu[:,None,None,None]*strain+lam[:,None,None,None]*np.trace(strain,axis1=2,axis2=3)[:,:,None,None]*np.eye(3)
        components=np.stack([stress[:,:,0,0],stress[:,:,1,1],stress[:,:,2,2],stress[:,:,0,1],stress[:,:,1,2],stress[:,:,2,0]],axis=2)
        for j,e in enumerate(cells):
            volume=system.cell_volumes[start+j]
            np.add.at(recovery,inds[j],components[j,:10]*volume);np.add.at(weights,inds[j],volume)
            samples[e.id]={i+1:v.tolist() for i,v in enumerate(components[j,10:])}
            body_peaks[e.body_id]=max(body_peaks[e.body_id],float(von_mises(components[j,10:]).max()))
    recovery/=weights[:,None]
    return recovery,samples,body_peaks,points


def von_mises(t):
    t=np.asarray(t);a,b,c,xy,yz,zx=np.moveaxis(t,-1,0)
    return np.sqrt(.5*((a-b)**2+(b-c)**2+(c-a)**2)+3*(xy*xy+yz*yz+zx*zx))


def bonded_solve(system,case,directory,cancel=None):
    if (case.history or any(load.history for load in case.loads)) or case.thermal or any(case.initial_velocity_mm_s) or system.study.settings.large_deformation:
        raise ValueError('Linear bonded assembly does not translate thermal/history/velocity/large-deformation inputs')
    t,fixed=system.bonding.transformation(case,cancel)
    if t.shape[1]>MAX_COMPONENT_DOFS:raise ValueError('Bonded assembly exceeds 60000 independent DOFs; larger-model qualification is required')
    f=system.loads(case);scale=float(system.k.diagonal().max())
    u,_=solve_reduced(system.k/scale,f/scale,t);check_cancel(cancel)
    r=system.k@u-f
    residual=float(np.linalg.norm(t.T@r)/max(float(np.linalg.norm(t.T@f)),1e-300))
    interfaces,unbalanced=system.bonding.constraint_forces(r,u)
    reactions=np.zeros_like(f);indices=sorted(fixed);reactions[indices]=unbalanced[indices]
    force=(f+reactions).reshape(-1,3);force_scale=max(float(np.linalg.norm(f.reshape(-1,3),axis=1).sum()),1e-300)
    length=max(float(np.linalg.norm(np.ptp(system.xyz,axis=0))),1e-12)
    force_error=float(np.linalg.norm(force.sum(axis=0))/force_scale)
    moment_error=float(np.linalg.norm(np.cross(system.xyz,force).sum(axis=0))/(force_scale*length))
    strain_energy=float(.5*u@(system.k@u));external_work=float(.5*u@f)
    energy_error=abs(strain_energy-external_work)/max(abs(strain_energy),abs(external_work),1e-300)
    displacement={n:u.reshape(-1,3)[i].tolist() for i,n in enumerate(system.ids)}
    tensors,samples,peaks,points=stress_fields(system,u,cancel)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    sparse.save_npz(directory/'bulk-stiffness.npz',system.k);sparse.save_npz(directory/'constraint-transformation.npz',t)
    write_json(directory/'dof-order.json',{'node_ids':system.ids,'components':['X','Y','Z'],'ordering':'node then world component','fixed_dofs':indices})
    write_json(directory/'interfaces.json',{'interfaces':interfaces,'constraints':system.bonding.relations,'dependent_order':system.bonding.order,'formulation':'common-refinement full-quadratic variational mortar; L2 projection eliminated as MPCs'})
    for index,(d,b) in enumerate(system.bonding.matrices):
        sparse.save_npz(directory/f'mortar-{index}-secondary.npz',sparse.csr_matrix(d));sparse.save_npz(directory/f'mortar-{index}-cross.npz',sparse.csr_matrix(b))
    evidence=directory/'interfaces.json'
    nodal=von_mises(tensors)
    fields={'nodes':{n:system.xyz[i].tolist() for i,n in enumerate(system.ids)},'displacements_mm':displacement,
        'nodal_recovered_von_mises_mpa':{n:float(nodal[i]) for i,n in enumerate(system.ids)},'nodal_stress_mpa':{n:tensors[i].tolist() for i,n in enumerate(system.ids)},
        'support_reactions_n':{n:reactions.reshape(-1,3)[i].tolist() for i,n in enumerate(system.ids)},
        'applied_nodal_forces_n':{n:f.reshape(-1,3)[i].tolist() for i,n in enumerate(system.ids)},
        'integration_point_stress_mpa':samples,'stress_tensor_components':['xx','yy','zz','xy','yz','zx'],
        'stress_sampling_reference_coordinates':points.tolist(),'stress_sampling':'four standard C3D10 postprocessing points; bulk operator integration has separate sensitivity checks'}
    values={'max_displacement_mm':float(np.linalg.norm(u.reshape(-1,3),axis=1).max()),'max_von_mises_mpa':float(nodal.max()),'strain_energy_n_mm':strain_energy}
    if system.mass_g is not None:values['mass_g']=system.mass_g
    probe=system.displacement_probe(displacement)
    if probe is not None:values['probe_displacement_mm']=probe['magnitude_mm']
    gap=0.
    for n,weights in system.bonding.relations.items():gap=max(gap,float(np.linalg.norm(np.array(displacement[n])-sum(w*np.array(displacement[m]) for m,w in weights.items()))))
    summary=[{k:v for k,v in item.items() if k not in {'primary_nodal_forces_n','secondary_nodal_forces_n','secondary_nodes'}} for item in interfaces]
    return {'values':values,'field':fields,'displacement_probe':{'final':probe,'peak':probe,'history':[probe]} if probe is not None else None,
        'checks':{'residual':residual,'force_balance_error':force_error,'moment_balance_error':moment_error,'energy_balance_error':energy_error,
            'maximum_constraint_displacement_error_mm':gap,'maximum_relative_interface_jump':max((i['relative_trace_jump'] for i in interfaces),default=0.),'displacement_to_extent_ratio':values['max_displacement_mm']/length,'bulk_integration':system.integration},
        'interfaces':summary,'interface_evidence':{'path':str(evidence.relative_to(directory.parent.parent)),'sha256':hashlib.sha256(evidence.read_bytes()).hexdigest(),'bytes':evidence.stat().st_size},
        'mesh_quality':system.quality_record,'constitutive_screening':{b:{'material_id':m.id,'integration_point_von_mises_peak_mpa':peaks[b],'declared_yield_strength_mpa':m.yield_strength_mpa,
            'exceeds_declared_yield':None if m.yield_strength_mpa is None else peaks[b]>m.yield_strength_mpa*(1+1e-6),'source':m.source,'condition':m.condition,
            'scope':'Sampled linear elastic stress versus supplied yield is a screen, not calibrated failure or continuous stress bounds'} for b,m in system.assignments.items()},
        'solver':'Oxygen C3D10 full-quadratic bonded assembly NumPy/SciPy worker',
        'warnings':['Perfect zero-thickness variational mortar bond; no adhesive compliance, separation, friction, preload, damage or physical interface calibration.',
            'Recovered nodal stress is volume-weighted and remains a screening field; four integration-point samples are not a continuous stress bound.']}
