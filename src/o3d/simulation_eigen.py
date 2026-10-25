"""Canonical C3D10 consistent stiffness/mass and constrained/free vibration.

Every midside DOF participates. Rigid spaces come from each connected solid and
its actual constraints, never an arbitrary frequency cutoff. Elastic modes are
mass normalized; their separate display shapes have arbitrary unit amplitude.
"""
from pathlib import Path
import numpy as np
from scipy import sparse, linalg
from scipy.sparse import linalg as sla

from .simulation_quadratic import QuadraticSystem
from .simulation_units import density_tonne_mm3, GRAMS_PER_TONNE
from .thermal_exchange import volume_quadrature
from .solver import Cancelled
from .store import write_json

MAX_ASSEMBLY_ENTRIES=40_000_000
MAX_COMPONENT_DOFS=60_000
MAX_FIELD_COMPONENTS=12_000_000


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():raise Cancelled("Simulation cancelled")


class QuadraticEigenSystem(QuadraticSystem):
    def __init__(self,mesh,study,materials,cancel=None):
        super().__init__(mesh,study,materials,cancel)
        if study.interfaces or study.bolts or study.settings.inertial_relief:
            raise ValueError("Quadratic eigenanalysis interface/connector/inertial-relief transfer is unavailable")
        if any(m.density_g_cm3 is None for m in self.assignments.values()):
            raise ValueError("Quadratic modal analysis requires sourced positive density on every body")
        self.connectivity=np.array([[self.index[n] for n in e.nodes] for e in self.mesh.elements])
        if len(mesh.elements)*900>MAX_ASSEMBLY_ENTRIES:
            raise ValueError(f"Quadratic eigenmatrix assembly exceeds {MAX_ASSEMBLY_ENTRIES} entries; larger-model qualification is required")
        self.k,self.m,self.integration=self.assemble(cancel)

    def element_matrices(self,start,order):
        inds=self.connectivity[start:start+64]
        xyz=self.xyz[inds]
        shape,gradients,measure=volume_quadrature(order)
        jac=np.einsum("eni,qnj->eqij",xyz-xyz[:,:1],gradients)
        weights=np.linalg.det(jac)*measure[None,:]
        if not np.isfinite(weights).all() or np.any(weights<=0):raise ValueError("Eigenmatrix quadrature has nonpositive or nonfinite Jacobians")
        physical=np.einsum("qni,eqij->eqnj",gradients,np.linalg.inv(jac))
        materials=[self.assignments[e.body_id] for e in self.mesh.elements[start:start+64]]
        young=np.array([m.youngs_modulus_mpa for m in materials]);nu=np.array([m.poisson_ratio for m in materials])
        mu=young/(2*(1+nu));lam=young*nu/((1+nu)*(1-2*nu))
        product=np.einsum("eqna,eqmb,eq->enamb",physical,physical,weights)
        dot=np.einsum("enama->enm",product)
        stiffness=(lam[:,None,None,None,None]*product+mu[:,None,None,None,None]*(product.transpose(0,1,4,3,2)+np.einsum("enm,ab->enamb",dot,np.eye(3))))
        density=np.array([density_tonne_mm3(m.density_g_cm3) for m in materials])
        mass=np.einsum("qn,qm,eq,e->enm",shape,shape,weights,density)
        return stiffness.reshape(-1,30,30),mass

    def assemble(self,cancel):
        cells=len(self.mesh.elements)
        kr=np.empty(cells*900,dtype=np.int32);kc=np.empty_like(kr);kv=np.empty(cells*900)
        mr=np.empty(cells*100,dtype=np.int32);mc=np.empty_like(mr);mv=np.empty(cells*100)
        worst_k=worst_m=0.;maximum_order=6
        for start in range(0,cells,64):
            check_cancel(cancel)
            low_k,low_m=self.element_matrices(start,4)
            high_k,high_m=self.element_matrices(start,6)
            def changes(a,b):return np.linalg.norm(a-b,axis=(1,2))/np.maximum(np.linalg.norm(b,axis=(1,2)),1e-30)
            dk,dm=changes(low_k,high_k),changes(low_m,high_m)
            if max(dk.max(),dm.max())>1e-5:
                final_k,final_m=self.element_matrices(start,8)
                dk,dm=changes(high_k,final_k),changes(high_m,final_m)
                high_k,high_m=final_k,final_m;maximum_order=8
            worst_k=max(worst_k,float(dk.max()));worst_m=max(worst_m,float(dm.max()))
            if max(dk.max(),dm.max())>1e-5:raise ValueError("Curved modal stiffness/mass integration did not converge; refine the mesh")
            inds=self.connectivity[start:start+64];count=len(inds)
            dofs=(inds[:,:,None]*3+np.arange(3)[None,None,:]).reshape(count,30)
            a,b=start*900,(start+count)*900
            kr[a:b]=np.repeat(dofs,30,axis=1).ravel();kc[a:b]=np.tile(dofs,(1,30)).ravel();kv[a:b]=high_k.ravel()
            a,b=start*100,(start+count)*100
            mr[a:b]=np.repeat(inds,10,axis=1).ravel();mc[a:b]=np.tile(inds,(1,10)).ravel();mv[a:b]=high_m.ravel()
        size=len(self.ids)
        k=sparse.coo_matrix((kv,(kr,kc)),shape=(3*size,)*2).tocsr()
        scalar_mass=sparse.coo_matrix((mv,(mr,mc)),shape=(size,)*2).tocsr()
        m=sparse.kron(scalar_mass,sparse.eye(3),format="csr")
        for name,matrix in [("stiffness",k),("mass",m)]:
            if not np.isfinite(matrix.data).all() or np.any(matrix.diagonal()<=0):raise ValueError(f"Invalid quadratic {name} matrix")
            if sparse.linalg.norm(matrix-matrix.T)>sparse.linalg.norm(matrix)*1e-12:raise ValueError(f"Asymmetric quadratic {name} matrix")
        mass=float(scalar_mass.sum())
        expected=self.mass_g/GRAMS_PER_TONNE
        if abs(mass-expected)>expected*1e-9:raise ValueError("Consistent modal mass disagrees with independently integrated material volume")
        return k,m,{"maximum_stiffness_change":worst_k,"maximum_mass_change":worst_m,"maximum_order_used":maximum_order,
                    "consistent_mass_tonne":mass,"volume_mass_tonne":expected,"mass_model":"full consistent C3D10; no row-sum lumping"}

    def constrained(self,case):
        fixed=set()
        for support in case.supports:
            if support.kind not in {"fixed","partial"}:raise ValueError("Quadratic eigenanalysis supports fixed/world-axis partial components only")
            components=(True,True,True) if support.kind=="fixed" else support.components
            for i in self.region_nodes(support.region_id):
                for axis,active in enumerate(components):
                    if active:fixed.add(3*i+axis)
        return fixed


def modal_solve(system,case,directory,cancel=None):
    if system.study.type!="modal":raise ValueError("Quadratic modal solve requires a modal study")
    if case.loads or case.thermal or case.history or any(case.initial_velocity_mm_s):
        raise ValueError("Unloaded modal analysis does not translate preload, thermal fields, history or initial velocity")
    requested=system.study.settings.mode_count;window=requested+4
    fixed=system.constrained(case)
    candidates=[];rigid_records=[];component_checks=[]
    for component,nodes in enumerate(system.solid_components):
        check_cancel(cancel)
        local_nodes=np.array(sorted(system.index[n] for n in nodes))
        dofs=(local_nodes[:,None]*3+np.arange(3)).ravel()
        active=np.array([i for i,d in enumerate(dofs) if d not in fixed],dtype=int)
        if not len(active):continue
        if len(active)>MAX_COMPONENT_DOFS:raise ValueError(f"Modal component exceeds {MAX_COMPONENT_DOFS} unconstrained DOFs; larger-model qualification is required")
        free=dofs[active]
        k=system.k[free][:,free].tocsc();m=system.m[free][:,free].tocsc()
        points=system.xyz[local_nodes];center=points.mean(axis=0)
        scale=max(float(np.linalg.norm(np.ptp(points,axis=0))),1e-12)
        rigid=np.zeros((len(dofs),6))
        rigid.reshape(-1,3,6)[:,:,:3]=np.eye(3)
        for i,(x,y,z) in enumerate((points-center)/scale):rigid[3*i:3*i+3,3:]=[[0,z,-y],[-z,0,x],[y,-x,0]]
        constrained=np.array([i for i,d in enumerate(dofs) if d in fixed],dtype=int)
        allowed=linalg.null_space(rigid[constrained],rcond=1e-10) if len(constrained) else np.eye(6)
        rigid=rigid[active]@allowed
        r=rigid.shape[1]
        if r:
            eigen,basis=linalg.eigh(rigid.T@m@rigid)
            if eigen.min()<=0:raise ValueError("Rigid modal mass is not positive definite")
            rigid=rigid@basis/np.sqrt(eigen)[None,:]
            error=float(np.linalg.norm(k@rigid)/(max(sla.norm(k),1e-30)*np.linalg.norm(rigid)))
            if error>1e-10:raise ValueError("Analytical rigid motions are not stiffness null modes")
            rigid_records.append((component,free,rigid,error))
        elastic_count=min(window,len(free)-r)
        if not elastic_count:continue
        count=elastic_count+r
        ks=float(k.diagonal().max());ms=float(m.diagonal().max())
        a,b=k/ks,m/ms
        if len(free)<=1200:
            _,vectors=linalg.eigh(a.toarray(),b.toarray(),subset_by_index=[0,count-1])
            method="LAPACK generalized symmetric eigensolve"
        else:
            shift=1e-6
            factor=sla.splu(a+shift*b)
            def inverse(v):
                check_cancel(cancel)
                return factor.solve(v)
            operator=sla.LinearOperator(a.shape,matvec=inverse,dtype=float)
            _,vectors=sla.eigsh(a,k=count,M=b,sigma=-shift,which="LM",OPinv=operator,
                ncv=min(len(free),max(2*count+1,40)),tol=1e-11,maxiter=20000,
                v0=np.random.default_rng(0).standard_normal(len(free)))
            method="ARPACK generalized shift-invert; negative numerical shift only"
        check_cancel(cancel)
        vectors=vectors/np.sqrt(ms)
        if r:vectors-=rigid@(rigid.T@(m@vectors))
        gram=vectors.T@m@vectors
        values,axes=linalg.eigh((gram+gram.T)/2)
        keep=values>values.max()*1e-8
        if int(keep.sum())!=elastic_count:raise ValueError("Rigid-space projection leaves an incomplete or ambiguous elastic spectral window")
        q=vectors@axes[:,keep]/np.sqrt(values[keep])[None,:]
        small=q.T@k@q
        eigen,rotation=linalg.eigh((small+small.T)/2)
        if not np.isfinite(eigen).all() or np.any(eigen<=0):raise ValueError("Nonpositive elastic eigenvalue; no frequency cutoff hides an unresolved mechanism")
        vectors=q@rotation
        component_checks.append({"component":component,"free_dofs":len(free),"rigid_modes":r,"computed_elastic_modes":elastic_count,"method":method})
        for j,value in enumerate(eigen):candidates.append((float(value),component,free,vectors[:,j]))
    candidates.sort(key=lambda row:row[0])
    if len(candidates)<window:raise ValueError("Insufficient elastic degrees of freedom for requested modes plus four spectral guards")
    candidates=candidates[:window]
    rigid_count=sum(row[2].shape[1] for row in rigid_records)
    if (window+rigid_count)*len(system.ids)*3>MAX_FIELD_COMPONENTS:raise ValueError("Modal field output exceeds the bounded component budget")
    full=np.zeros((3*len(system.ids),rigid_count+window));rigid_error=0.;column=0
    for _,free,vectors,error in rigid_records:
        full[np.ix_(free,np.arange(column,column+vectors.shape[1]))]=vectors
        column+=vectors.shape[1];rigid_error=max(rigid_error,error)
    eigenvalues=np.zeros(rigid_count+window)
    for j,(value,_,free,vector) in enumerate(candidates):
        full[free,rigid_count+j]=vector;eigenvalues[rigid_count+j]=value
    mass_product=system.m@full
    gram=full.T@mass_product
    orthogonality=float(np.max(np.abs(gram-np.eye(len(gram)))))
    free=np.array([i for i in range(len(full)) if i not in fixed],dtype=int)
    modes=[]
    for j,value in enumerate(eigenvalues):
        vector=full[:,j];elastic=system.k@vector;inertial=value*mass_product[:,j]
        residual=float(np.linalg.norm((elastic-inertial)[free])/max(np.linalg.norm(elastic[free]),np.linalg.norm(inertial[free]),1e-300)) if value else rigid_error
        shape=vector.reshape(-1,3);display=shape/np.linalg.norm(shape,axis=1).max()
        participation=[]
        for axis in range(3):
            influence=np.zeros(len(vector));influence[axis::3]=1
            effective=float(vector@(system.m@influence))**2
            participation.append({"axis":"XYZ"[axis],"effective_mass_tonne":effective,"effective_mass_fraction":effective/system.integration["consistent_mass_tonne"]})
        modes.append({"mode":j+1,"frequency_hz":float(np.sqrt(value)/(2*np.pi)),"eigenvalue_s_2":float(value),
            "rigid_body":bool(j<rigid_count),"unstable":False,"residual":residual,"participation":participation,
            "mass_normalized_displacement":{n:shape[i].tolist() for i,n in enumerate(system.ids)},
            "shape":{n:display[i].tolist() for i,n in enumerate(system.ids)}})
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    sparse.save_npz(directory/"stiffness.npz",system.k);sparse.save_npz(directory/"consistent-mass.npz",system.m)
    write_json(directory/"dof-order.json",{"node_ids":system.ids,"components":["X","Y","Z"],"ordering":"node then world component"})
    write_json(directory/"all-modes.json",{"modes":modes,"requested_elastic_modes":requested,"guard_modes":4,"components":component_checks})
    shown=modes[:rigid_count+requested]
    bundle={"mesh":system.raw_mesh,"element_bodies":[e.body_id for e in system.mesh.elements],
            "density_tonne_mm3":{b:density_tonne_mm3(m.density_g_cm3) for b,m in system.assignments.items()},
            "fields":[m["mass_normalized_displacement"] for m in modes[rigid_count:]],"values":[m["frequency_hz"] for m in modes[rigid_count:]]}
    return {"values":{"min_frequency_hz":modes[rigid_count]["frequency_hz"],"mass_g":system.mass_g},
            "checks":{"residual":max(m["residual"] for m in modes[rigid_count:]),"mass_orthogonality_error":orthogonality,
                      "rigid_stiffness_error":rigid_error,"rigid_mode_count":rigid_count,"modal_integration":system.integration},
            "field":{"nodes":{n:system.xyz[i].tolist() for i,n in enumerate(system.ids)},"modes":shown},"modes":shown,
            "modal_metadata":{"requested_elastic_modes":requested,"computed_guard_modes":4,"rigid_modes_reported_separately":rigid_count,
                              "display_shape":"largest nodal norm is one; arbitrary reference amplitude, not physical displacement"},
            "mesh_quality":system.quality_record,"solver":"Oxygen C3D10 consistent-mass NumPy/SciPy modal worker",
            "warnings":["Modes are unloaded linear isotropic vibration; no preload/contact calibration or physical modal validation is implied."],
            "_mode_bundle":bundle}
