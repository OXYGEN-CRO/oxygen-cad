"""Full-node canonical linearized elastic buckling under a declared dead load.

K u = f supplies reference stress; (K + lambda Kg) phi = 0 supplies
positive load multipliers. Density is needed only for a gravity reference load.
No imperfection, collapse amplitude, contact or independent preload is inferred.
"""
from pathlib import Path
import hashlib
import numpy as np
from scipy import sparse,linalg
from scipy.sparse import linalg as sla

from .simulation_eigen import check_cancel,MAX_COMPONENT_DOFS,MAX_FIELD_COMPONENTS
from .thermal_exchange import volume_quadrature
from .store import write_json


def geometric_matrix(system,u,cancel=None):
    cells=len(system.mesh.elements);rows=np.empty(cells*100,dtype=np.int32)
    columns=np.empty_like(rows);values=np.empty(cells*100)
    fields={};orders={};peaks={body:0. for body in system.assignments}
    worst=0.;max_order=6;compression=0.;stress_scale=0.;strain_bound=0.
    def integrate(start,order):
        inds=system.connectivity[start:start+64];xyz=system.xyz[inds]
        _,gradients,measure=volume_quadrature(order)
        jac=np.einsum("eni,qnj->eqij",xyz-xyz[:,:1],gradients)
        weights=np.linalg.det(jac)*measure[None,:]
        if not np.isfinite(weights).all() or np.any(weights<=0):raise ValueError("Buckling geometric quadrature has invalid Jacobians")
        physical=np.einsum("qni,eqij->eqnj",gradients,np.linalg.inv(jac))
        gradient=np.einsum("ena,eqnb->eqab",u.reshape(-1,3)[inds],physical)
        strain=(gradient+gradient.transpose(0,1,3,2))/2
        materials=[system.assignments[e.body_id] for e in system.mesh.elements[start:start+64]]
        young=np.array([m.youngs_modulus_mpa for m in materials]);nu=np.array([m.poisson_ratio for m in materials])
        mu=young/(2*(1+nu));lam=young*nu/((1+nu)*(1-2*nu))
        stress=2*mu[:,None,None,None]*strain+lam[:,None,None,None]*np.trace(strain,axis1=2,axis2=3)[:,:,None,None]*np.eye(3)
        matrix=np.einsum("eqna,eqab,eqmb,eq->enm",physical,stress,physical,weights)
        return matrix,stress,strain
    for start in range(0,cells,64):
        check_cancel(cancel)
        used_order=6
        low,_,_=integrate(start,4);high,stress,strain=integrate(start,6)
        change=np.linalg.norm(low-high,axis=(1,2))/np.maximum(np.linalg.norm(high,axis=(1,2)),1e-300)
        if change.max()>1e-5:
            final,stress,strain=integrate(start,8)
            change=np.linalg.norm(high-final,axis=(1,2))/np.maximum(np.linalg.norm(final,axis=(1,2)),1e-300)
            high=final;max_order=8;used_order=8
        worst=max(worst,float(change.max()))
        if change.max()>1e-5:raise ValueError("Buckling geometric-stiffness quadrature did not converge; refine the mesh")
        inds=system.connectivity[start:start+64];count=len(inds);a,b=start*100,(start+count)*100
        rows[a:b]=np.repeat(inds,10,axis=1).ravel();columns[a:b]=np.tile(inds,(1,10)).ravel();values[a:b]=high.ravel()
        principal=np.linalg.eigvalsh(stress)
        compression=min(compression,float(principal.min()));stress_scale=max(stress_scale,float(np.abs(principal).max()))
        strain_bound=max(strain_bound,float(np.linalg.norm(strain,axis=(2,3)).max()))
        for element,tensors in zip(system.mesh.elements[start:start+64],stress):
            # Actual high-order samples, not extrapolated nodal stress.
            orders[element.id]=used_order
            fields[element.id]=[[float(t[0,0]),float(t[1,1]),float(t[2,2]),float(t[0,1]),float(t[1,2]),float(t[2,0])] for t in tensors]
            dev=tensors-np.trace(tensors,axis1=1,axis2=2)[:,None,None]*np.eye(3)/3
            peaks[element.body_id]=max(peaks[element.body_id],float(np.sqrt(1.5*np.sum(dev*dev,axis=(1,2))).max()))
    scalar=sparse.coo_matrix((values,(rows,columns)),shape=(len(system.ids),)*2).tocsr()
    kg=sparse.kron(scalar,sparse.eye(3),format="csr")
    if not np.isfinite(kg.data).all() or sla.norm(kg-kg.T)>max(sla.norm(kg),1e-300)*1e-12:raise ValueError("Invalid or asymmetric geometric stiffness")
    if stress_scale==0 or compression>=-stress_scale*1e-10:raise ValueError("No resolvable compressive reference prestress; check the declared load sign")
    return kg,{"maximum_change":worst,"maximum_order_used":max_order,"minimum_sampled_principal_stress_mpa":compression,"maximum_sampled_strain_norm":strain_bound,"stress_quadrature_order_by_element":orders,"stress_components":["xx","yy","zz","xy","yz","zx"],"quadrature":"Duffy-product Gauss-Legendre on the reference tetrahedron; positive physical weights"},fields,peaks


def buckling_solve(system,case,directory,cancel=None):
    if system.study.type!="buckling":raise ValueError("Quadratic buckling solve requires a buckling study")
    if case.thermal or case.history or any(load.history for load in case.loads) or any(case.initial_velocity_mm_s) or system.study.settings.large_deformation:
        raise ValueError("Linearized buckling does not translate thermal fields, history, initial velocity or large-deformation settings")
    system.validate_supports(case)
    fixed=system.constrained(case);free=np.array([i for i in range(3*len(system.ids)) if i not in fixed],dtype=int)
    requested=system.study.settings.mode_count;window=requested+4
    if len(free)<=window:raise ValueError("Insufficient free DOFs for buckling modes plus four spectral guards")
    if len(free)>MAX_COMPONENT_DOFS:raise ValueError(f"Buckling exceeds {MAX_COMPONENT_DOFS} free DOFs; larger-model qualification is required")
    if window*len(system.ids)*3>MAX_FIELD_COMPONENTS:raise ValueError("Buckling fields exceed the bounded component budget")
    force=system.loads(case)
    norm=float(np.linalg.norm(force))
    if not norm:raise ValueError("Buckling requires a nonzero declared reference load")
    k=system.k[free][:,free].tocsc();ks=float(k.diagonal().max());a=k/ks
    factor=sla.splu(a)
    u=np.zeros(len(force));u[free]=factor.solve(force[free]/ks)
    check_cancel(cancel)
    residual=system.k@u-force
    equation=float(np.linalg.norm(residual[free])/max(norm,1e-300))
    reactions=np.zeros_like(force);fixed_ids=sorted(fixed);reactions[fixed_ids]=residual[fixed_ids]
    total=(force+reactions).reshape(-1,3)
    scale=max(float(np.linalg.norm(force.reshape(-1,3),axis=1).sum()),1e-300)
    extent=max(float(np.linalg.norm(np.ptp(system.xyz,axis=0))),1e-300)
    prestress={"residual":equation,"force_balance_error":float(np.linalg.norm(total.sum(axis=0))/scale),
               "moment_balance_error":float(np.linalg.norm(np.cross(system.xyz,total).sum(axis=0))/(scale*extent))}
    if not np.isfinite(u).all() or equation>system.study.settings.residual_tolerance:raise ValueError("Reference prestress equation did not converge")
    kg,integration,stress,peaks=geometric_matrix(system,u,cancel)
    b=-kg[free][:,free].tocsc();bs=float(sla.norm(b))
    if bs==0:raise ValueError("Reference load produces no geometric stiffness")
    # Largest positive reciprocal factors identify the lowest positive critical
    # multipliers without treating an indefinite geometric matrix as SPD mass.
    if len(free)<=1200:
        mu,vectors=linalg.eigh(b.toarray()/bs,a.toarray(),subset_by_index=[len(free)-window,len(free)-1])
        method="LAPACK symmetric reciprocal buckling; elastic stiffness metric"
    else:
        def inverse(v):check_cancel(cancel);return factor.solve(v)
        operator=sla.LinearOperator(a.shape,matvec=inverse,dtype=float)
        mu,vectors=sla.eigsh(b/bs,k=window,M=a,Minv=operator,which="LA",tol=1e-11,maxiter=20000,
            ncv=min(len(free),max(2*window+1,40)),v0=np.random.default_rng(0).standard_normal(len(free)))
        method="ARPACK largest algebraic reciprocal factors; elastic stiffness metric"
    check_cancel(cancel)
    order=np.argsort(mu)[::-1];mu=mu[order];vectors=vectors[:,order]/np.sqrt(ks)
    if not np.isfinite(mu).all() or np.any(mu<=0):raise ValueError("Insufficient positive buckling factors for the requested complete spectral window")
    factors=ks/(bs*mu)
    if not np.isfinite(factors).all() or np.any(factors<=0):raise ValueError("Buckling load multipliers are nonfinite or unresolved")
    full=np.zeros((len(force),window));full[free]=vectors
    gram=full.T@(system.k@full);orthogonality=float(np.max(np.abs(gram-np.eye(window))))
    modes=[]
    for j,value in enumerate(factors):
        vector=full[:,j];elastic=system.k@vector;geometric=value*(kg@vector)
        error=float(np.linalg.norm((elastic+geometric)[free])/max(np.linalg.norm(elastic[free]),np.linalg.norm(geometric[free]),1e-300))
        shape=vector.reshape(-1,3);display=shape/np.linalg.norm(shape,axis=1).max()
        modes.append({"mode":j+1,"load_factor":float(value),"residual":error,
                      "stiffness_normalized_shape":{n:shape[i].tolist() for i,n in enumerate(system.ids)},
                      "shape":{n:display[i].tolist() for i,n in enumerate(system.ids)}})
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    sparse.save_npz(directory/"stiffness.npz",system.k);sparse.save_npz(directory/"geometric-stiffness.npz",kg)
    write_json(directory/"dof-order.json",{"node_ids":system.ids,"components":["X","Y","Z"],"ordering":"node then world component","constrained_dofs":fixed_ids})
    reference={"displacements_mm":{n:u.reshape(-1,3)[i].tolist() for i,n in enumerate(system.ids)},
               "integration_point_stress_mpa":stress,"applied_nodal_forces_n":{n:force.reshape(-1,3)[i].tolist() for i,n in enumerate(system.ids)},
               "support_reactions_n":{n:reactions.reshape(-1,3)[i].tolist() for i,n in enumerate(system.ids)},"checks":prestress}
    write_json(directory/"reference-load.json",reference)
    write_json(directory/"all-modes.json",{"modes":modes,"requested_modes":requested,"guard_modes":4,"method":method})
    screening={body:{"material_id":material.id,"reference_von_mises_peak_mpa":peaks[body],
        "critical_load_von_mises_peak_mpa":peaks[body]*factors[0],"declared_yield_strength_mpa":material.yield_strength_mpa,
        "exceeds_declared_yield":None if material.yield_strength_mpa is None else peaks[body]*factors[0]>material.yield_strength_mpa*(1+1e-6),
        "source":material.source,"condition":material.condition,
        "scope":"Linear elastic critical-load stress versus supplied yield is a screening check, not collapse or material calibration"}
        for body,material in system.assignments.items()}
    bundle={"mesh":system.raw_mesh,"element_bodies":[e.body_id for e in system.mesh.elements],
            "volume_weights":{body:1. for body in system.assignments},
            "fields":[m["shape"] for m in modes],"values":factors.tolist()}
    return {"values":{"min_buckling_factor":float(factors[0])},
            "checks":{"residual":max(m["residual"] for m in modes),"prestress":prestress,"stiffness_orthogonality_error":orthogonality,
                      "stiffness_integration":system.integration,"geometric_integration":integration,
                      "reference_displacement_to_extent_ratio":float(np.linalg.norm(u.reshape(-1,3),axis=1).max()/extent)},
            "field":{"nodes":{n:system.xyz[i].tolist() for i,n in enumerate(system.ids)},"modes":modes[:requested]},"modes":modes[:requested],
            "reference_load":{"checks":prestress,"sampled_stress_elements":len(stress),"node_count":len(system.ids),
                "maximum_displacement_mm":float(np.linalg.norm(u.reshape(-1,3),axis=1).max())},
            "reference_load_evidence":{"path":str((directory/"reference-load.json").relative_to(directory.parent.parent)),
                "sha256":hashlib.sha256((directory/"reference-load.json").read_bytes()).hexdigest(),"bytes":(directory/"reference-load.json").stat().st_size},
            "constitutive_screening":screening,"mesh_quality":system.quality_record,
            "buckling_metadata":{"requested_modes":requested,"computed_guard_modes":4,"method":method,
                "equation":"(K + load_factor * Kg(reference_load)) shape = 0","load_convention":"positive multiplier scales every declared dead reference load together; no independent preload",
                "amplitude":"unit largest nodal shape norm; no physical displacement or collapse amplitude","density":"not required except for gravity reference loads"},
            "solver":"Oxygen C3D10 NumPy/SciPy linearized elastic buckling worker",
            "warnings":["Ideal elastic dead-load instability; no imperfections, nonlinear collapse, follower-load stiffness, contact or physical calibration is implied."],
            "_mode_bundle":bundle}
