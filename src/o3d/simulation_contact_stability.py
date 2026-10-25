"""Current frozen-map contact diagnostics, distinct from physical stability.

Local Coulomb return-map derivatives are independently checked against upstream
Fortran kernels. A native four-point C3D10 strain-kernel audit establishes the
linear bulk rigid space. Current contact rows then test restraint of that space.
Sliding coupling is nonsymmetric: full-rank rows never certify its stability.
"""
from __future__ import annotations
import hashlib
import numpy as np
from .quadratic import weights_and_gradients
from .store import write_json

RANK_TOLERANCE=1e-10


def closed_response(normal,gap,relative,initial,kn,kt,friction,area):
    """Internal force and derivative for a generated closed, frozen contact point.

    The stored tangential displacement is incremental in the frozen normal plane;
    it is not the final world displacement projected from an assumed virgin state.
    """
    normal=np.asarray(normal,dtype=float);relative=np.asarray(relative,dtype=float)
    if gap>0:raise ValueError('Closed-point tangent requires compressive contact; opening is a separate active-set branch')
    projector=np.eye(3)-np.outer(normal,normal);pressure=-kn*gap
    force=-pressure*normal;derivative=kn*np.outer(normal,normal)
    state=None;branch='frictionless';trial_norm=0.;limit=friction*pressure
    if friction:
        initial=np.asarray(initial,dtype=float)
        tangential=initial[6:9]+projector@(relative-initial[3:6])
        elastic=tangential-initial[:3];trial=kt*elastic;trial_norm=float(np.linalg.norm(trial))
        plastic=initial[:3].copy()
        if trial_norm<=limit or trial_norm==0:
            shear=trial;derivative+=kt*projector;branch='stick'
        else:
            direction=trial/trial_norm;shear=limit*direction
            derivative+=kt*limit/trial_norm*(np.eye(3)-np.outer(direction,direction))@projector
            derivative-=friction*kn*np.outer(direction,normal)
            plastic+=(trial_norm-limit)/kt*direction;branch='slip'
        force+=shear;state=np.r_[plastic,relative,tangential]
    return {'force_n':area*force,'tangent_n_mm':area*derivative,'state':state,'branch':branch,
            'trial_shear_mpa':trial_norm,'friction_limit_mpa':limit}


def rigid(point,center,length):
    x,y,z=(point-center)/length
    return np.column_stack((np.eye(3),np.array([[0,z,-y],[-z,0,x],[y,-x,0]])))


def bulk_kernel(system):
    """Check all six rigid and 24 strain directions of every native-order cell."""
    if system.study.settings.large_deformation or any(m.plastic_curve for m in system.assignments.values()):
        return {'checked':False,'reason':'Nonlinear material/geometric bulk tangent is not exported; linear bulk positivity cannot be substituted'}
    alpha=.5854101966249685;beta=.1381966011250105
    points=np.array([[alpha if j==i else beta for j in range(4)] for i in range(4)])[:,1:]
    derivatives=[weights_and_gradients(point)[1] for point in points]
    minimum=1.;worst=None;rigid_error=0.
    for element in system.mesh.elements:
        xyz=np.array([system.mesh.nodes[n] for n in element.nodes]);center=xyz.mean(axis=0);length=float(np.linalg.norm(np.ptp(xyz,axis=0)))
        material=system.assignments[element.body_id];nu=material.poisson_ratio
        mu=1/(2*(1+nu));lam=nu/((1+nu)*(1-2*nu))
        c=np.zeros((6,6));c[:3,:3]=lam;c[np.arange(3),np.arange(3)]+=2*mu;c[3:,3:]=2*mu*np.eye(3)
        constitutive=np.linalg.cholesky(c).T;rows=[];weights=[]
        for gradient in derivatives:
            jac=(xyz-xyz[0]).T@gradient;weight=float(np.linalg.det(jac))/24
            if weight<=0:raise ValueError('Contact bulk kernel has a nonpositive native quadrature Jacobian')
            physical=gradient@np.linalg.inv(jac);b=np.zeros((6,10,3))
            for axis in range(3):b[axis,:,axis]=physical[:,axis]
            for row,(a,d) in enumerate([(0,1),(1,2),(2,0)],3):
                b[row,:,a]=physical[:,d]/np.sqrt(2);b[row,:,d]=physical[:,a]/np.sqrt(2)
            rows.append(constitutive@b.reshape(6,30)*length);weights.append(weight)
        h=np.vstack([np.sqrt(w/sum(weights))*row for w,row in zip(weights,rows)])
        basis=np.vstack([rigid(point,center,length) for point in xyz])
        error=float(np.linalg.norm(h@basis)/(np.linalg.norm(h)*np.linalg.norm(basis)))
        singular=np.linalg.svd(h,compute_uv=False);ratio=float(singular[-1]/singular[0])
        if ratio<minimum:minimum=ratio;worst=element.id
        rigid_error=max(rigid_error,error)
    checked=minimum>RANK_TOLERANCE and rigid_error<1e-11
    return {'checked':checked,'elements':len(system.mesh.elements),'quadrature':'four native C3D10 points; full ten-node Kelvin strain operator',
        'minimum_nonrigid_singular_ratio':minimum,'worst_element':worst,'maximum_rigid_kernel_residual':rigid_error,
        'rank_tolerance':RANK_TOLERANCE,'scope':'Positive isotropic constitutive metric with exactly six local rigid modes; no global conditioning or nonlinear tangent certificate'}


class Rows:
    def __init__(self,dimension,initial=None):
        self.dimension=dimension;self.matrix=np.empty((0,dimension)) if initial is None else initial.copy();self.pending=[]
    def add(self,row):
        norm=float(np.linalg.norm(row))
        if norm:self.pending.append(row/norm)
        if len(self.pending)>=256:self.finish()
    def finish(self):
        if self.pending:
            self.matrix=np.linalg.qr(np.vstack([self.matrix,*self.pending]),mode='r');self.pending=[]
        return self.matrix
    def spectrum(self):
        matrix=self.finish()
        if not len(matrix):return 0,np.zeros(0),np.eye(self.dimension)
        _,s,v=np.linalg.svd(matrix,full_matrices=True);rank=int(np.sum(s>s[0]*RANK_TOLERANCE))
        return rank,s,v[rank:].T


class ContactStateAudit:
    def __init__(self,system,case):
        self.system=system;self.case=case;self.dimension=6*len(system.solid_components);self.history=[];self.snapshots={}
        self.enabled=len(system.solid_components)<=32
        self.bulk=bulk_kernel(system) if self.enabled else {'checked':False,'reason':'Current rigid-space audit exceeds 32 solids'}
        self.centers=[];self.lengths=[]
        self.worst=None
        if not self.enabled:return
        for nodes in system.solid_components:
            xyz=np.array([system.mesh.nodes[n] for n in nodes]);self.centers.append(xyz.mean(axis=0));self.lengths.append(float(np.linalg.norm(np.ptp(xyz,axis=0))))
        supports=Rows(self.dimension)
        if self.enabled:
            for support in case.supports:
                for index in system.region_nodes(support.region_id):
                    node=system.ids[index];component=system.component_of[node]
                    for axis,active in enumerate((True,True,True) if support.kind=='fixed' else support.components):
                        if active:
                            row=np.zeros(self.dimension);row[6*component:6*component+6]=self.basis(system.mesh.nodes[node],component)[axis];supports.add(row)
        self.supports=supports.finish();_,_,self.allowed=supports.spectrum()

    def basis(self,point,component):return rigid(np.asarray(point),self.centers[component],self.lengths[component])

    def start(self,meta):
        if not self.enabled:return
        self.meta=meta;self.normal=Rows(self.dimension,self.supports);self.actual=Rows(self.dimension,self.supports);self.possible=Rows(self.dimension,self.supports)
        self.projected=np.zeros((self.dimension,self.dimension));self.counts={'normal':0,'stick':0,'slip':0,'boundary':0}
        self.force_error=0.;self.state_error=0.;self.asymmetry=0.

    def point(self,record,normal,area,gap,relative,pressure,tau,initial,state,secondary,primary):
        if not self.enabled:return
        jump=np.zeros((3,self.dimension))
        for role,sign,point in [('secondary',1,secondary),('primary',-1,primary)]:
            component=record[f'{role}_component'];jump[:,6*component:6*component+6]+=sign*self.basis(point,component)
        normal_row=normal@jump;self.possible.add(normal_row)
        kn=record['normal_penalty_n_mm3'];kt=record['tangential_penalty_n_mm3'] or 0.;friction=record['friction_coefficient']
        extent=max(self.lengths);pressure_resolution=kn*extent*np.finfo(float).eps*128
        axis=np.eye(3)[np.argmin(abs(normal))]
        first=axis-float(axis@normal)*normal;first/=np.linalg.norm(first)
        second=np.cross(normal,first);second/=np.linalg.norm(second)
        tangent_rows=np.array([first@jump,second@jump])
        if pressure<=pressure_resolution:
            self.counts['boundary']+=1
            if friction:
                for row in tangent_rows:self.possible.add(row)
            return
        if gap>0:raise ValueError('Compressive native contact point has positive gap; no tangent inferred')
        response=closed_response(normal,gap,relative,initial,kn,kt,friction,area)
        observed=area*(-pressure*normal+tau);force_scale=max(area*pressure,float(area*np.linalg.norm(tau)),area*pressure_resolution)
        self.force_error=max(self.force_error,float(np.linalg.norm(observed-response['force_n']))/force_scale)
        if friction:
            self.state_error=max(self.state_error,float(np.linalg.norm(response['state']-state))/extent)
        self.normal.add(normal_row);self.actual.add(normal_row);self.counts['normal']+=1
        derivative=response['tangent_n_mm'];self.projected+=jump.T@derivative@jump
        self.asymmetry=max(self.asymmetry,float(np.linalg.norm(derivative-derivative.T)/np.linalg.norm(derivative)))
        if friction:
            margin=response['trial_shear_mpa']-response['friction_limit_mpa']
            resolution=max(kt*extent*np.finfo(float).eps*128,pressure_resolution*friction)
            if abs(margin)<=resolution:
                self.counts['boundary']+=1
                for row in tangent_rows:self.possible.add(row)
            elif margin<0:
                self.counts['stick']+=1
                for row in tangent_rows:self.actual.add(row);self.possible.add(row)
            else:
                self.counts['slip']+=1
                norm=float(np.linalg.norm(tau))
                if norm==0:
                    self.counts['boundary']+=1
                    for row in tangent_rows:self.possible.add(row)
                    return
                direction=tau/norm
                transverse=np.cross(normal,direction);transverse/=np.linalg.norm(transverse)
                row=transverse@jump;self.actual.add(row);self.possible.add(row)

    def finish(self,time):
        if not self.enabled:return
        normal_rank,_,_=self.normal.spectrum();actual_rank,singular,_=self.actual.spectrum();possible_rank,_,witness=self.possible.spectrum()
        if self.force_error>1e-7 or self.state_error>1e-10:raise ValueError('Accepted contact force/state differs from the closed-point return map; tangent audit refused')
        relative=float(singular[-1]/singular[0]) if len(singular)==self.dimension and singular[0]>0 else 0.
        mechanism=self.bulk['checked'] and possible_rank<self.dimension
        row={'time_s':time,'step':self.meta['step'],'increment':self.meta['increment'],'rigid_dofs':self.dimension,
             'normal_restraint_rank':normal_rank,'guaranteed_constitutive_rank':actual_rank,'maximum_possible_rank':possible_rank,
             'minimum_relative_constraint_singular_value':relative,'point_states':self.counts.copy(),
             'numerical_rigid_mechanism':mechanism,'remaining_possible_rigid_modes':self.dimension-possible_rank,
             'local_return_force_relative_error':self.force_error,'local_return_state_relative_error':self.state_error,
             'maximum_local_tangent_asymmetry':self.asymmetry,
             'positive_frozen_operator_kernel_structure':self.bulk['checked'] and actual_rank==self.dimension and not self.counts['slip'] and not self.counts['boundary']}
        operator=self.allowed.T@self.projected@self.allowed
        row['supported_rigid_tangent_sha256']=hashlib.sha256(np.asarray(operator,dtype='<f8').tobytes()).hexdigest()
        row['supported_rigid_tangent_singular_values_n_mm']=np.linalg.svd(operator,compute_uv=False).tolist() if len(operator) else []
        score=(possible_rank,relative)
        if self.worst is None or score<self.worst:
            self.worst=score;self.snapshots={'normal':self.normal.matrix.copy(),'constitutive':self.actual.matrix.copy(),
                'possible':self.possible.matrix.copy(),'supported_rigid_tangent':operator,'allowed_rigid_basis':self.allowed.copy(),
                'mechanism_witnesses':witness[:,:6].copy()};self.worst_time=time
        self.history.append(row)

    def write(self,directory):
        rows=self.history
        value={'performed':self.enabled,'bulk_kernel':self.bulk,'frames':len(rows),
               'rigid_dofs':self.dimension,'sliding_frames':sum(row['point_states']['slip']>0 for row in rows),
               'boundary_frames':sum(row['point_states']['boundary']>0 for row in rows),
               'numerical_rigid_mechanism_detected':any(row['numerical_rigid_mechanism'] for row in rows) if self.enabled else None,
               'minimum_normal_restraint_rank':min((row['normal_restraint_rank'] for row in rows),default=None),
               'minimum_constitutive_rank':min((row['guaranteed_constitutive_rank'] for row in rows),default=None),
               'minimum_possible_rank':min((row['maximum_possible_rank'] for row in rows),default=None),
               'all_frozen_operator_kernel_structures_positive':bool(rows) and all(row['positive_frozen_operator_kernel_structure'] for row in rows),
               'full_contact_stability_qualified':False,'rank_tolerance':RANK_TOLERANCE,
               'scope':'Accepted frozen-map local derivatives and current bulk rigid-space restraint only. Nonsymmetric sliding, deformable-mode conditioning/coupling, changing-map/one-sided transitions, nonlinear bulk and physical stability remain distinct and unqualified.'}
        write_json(directory/'contact-stability.json',{'summary':value,'history':rows,
            'worst_operator_time':getattr(self,'worst_time',None),'component_count':len(self.system.solid_components),
            'component_nodes':[sorted(nodes) for nodes in self.system.solid_components] if self.enabled else [],
            'rigid_basis':'Per connected solid: reference centroid translations and rotations normalized by its extent; witness amplitudes are arbitrary, not a predicted displacement'})
        if self.snapshots:np.savez_compressed(directory/'contact-restraint-operators.npz',**self.snapshots)
        return value
