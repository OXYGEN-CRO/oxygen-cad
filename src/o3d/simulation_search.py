"""Evaluated SIMP compliance/volume search on supplied tetrahedral design space.

Unrestricted manufacturing only. Density output is guidance. A thresholded
solid is independently reanalysed and its limitations remain in every outcome.
"""
from __future__ import annotations
import copy
import numpy as np
from scipy import sparse
from scipy.spatial import cKDTree
from .simulation_fem import System, static_solve, check_cancel, modal_solve, buckling_solve
from .simulation_models import MeshRecord, StudyRecord


def optimize(system,case,cancel=None):
    search=system.study.search
    if search is None:raise ValueError("Topology search requires explicit design-space settings")
    if system.study.interfaces or system.study.bolts or system.study.settings.inertial_relief:
        raise ValueError("Density optimization of interfaces, bolts or inertial relief is unavailable")
    if len(system.elements)>12000:raise ValueError("SIMP reference worker limited to 12000 tetrahedra")
    if any(l.kind not in {"force","pressure","moment","remote","bearing"} for l in case.loads):
        raise ValueError("Topology supports design-independent surface loads only")
    ids=[e.id for e,*_ in system.elements];index={e:i for i,e in enumerate(ids)}
    preserved=set(search.preserved_element_ids);obstacles=set(search.obstacle_element_ids)
    if not preserved<=index.keys() or not obstacles<=index.keys() or preserved&obstacles:
        raise ValueError("Preserve/obstacle elements must be disjoint existing element IDs")
    boundary_nodes=set()
    for item in [*case.supports,*case.loads]:
        if item.region_id:boundary_nodes.update(system.ids[i] for i in system.region_nodes(item.region_id))
    required={e.id for e,*_ in system.elements if set(e.nodes)&boundary_nodes}
    if not required<=preserved:
        raise ValueError("Explicitly preserve all elements touching supports and loaded regions")
    volumes=np.array([x[3] for x in system.elements]);target=search.volume_fraction*sum(volumes)
    fixed=np.array([e in preserved for e in ids]);void=np.array([e in obstacles for e in ids])
    if volumes[fixed].sum()+volumes[~fixed].sum()*search.minimum_density>target:
        raise ValueError("Target volume is smaller than explicitly preserved volume")
    centers=np.array([system.xyz[inds].mean(axis=0) for _,inds,*_ in system.elements])
    tree=cKDTree(centers)
    rows,cols,weights=[],[],[]
    for i,center in enumerate(centers):
        for j in tree.query_ball_point(center,search.filter_radius_mm):
            rows.append(i);cols.append(j);weights.append(max(0,search.filter_radius_mm-np.linalg.norm(center-centers[j])))
    filt=sparse.coo_matrix((weights,(rows,cols)),shape=(len(ids),)*2).tocsr()
    filt_sum=np.asarray(filt.sum(axis=1)).ravel()
    rho=np.full(len(ids),max(search.minimum_density,(target-volumes[fixed].sum())/max(volumes[~fixed].sum(),1e-12)))
    rho[fixed]=1;rho[void]=search.minimum_density
    local=[];arows=[];acols=[]
    for _,_,dofs,v,_,b,d in system.elements:
        local.append(v*b.T@d@b)
        arows.extend(np.repeat(dofs,12));acols.extend(np.tile(dofs,12))
    local=np.array(local);original=system.k
    history=[];previous=None
    try:
        for iteration in range(search.iterations):
            check_cancel(cancel)
            stiffness=(search.minimum_density+(1-search.minimum_density)*rho**search.penalization)
            values=(local*stiffness[:,None,None]).ravel()
            system.k=sparse.coo_matrix((values,(arows,acols)),shape=original.shape).tocsr()
            solved=static_solve(system,case,cancel)
            u=solved["u"]
            energy=np.array([u[dofs]@ke@u[dofs] for (_,_,dofs,*_),ke in zip(system.elements,local)])
            compliance=float(system.loads(case)@u)
            derivative=-search.penalization*(1-search.minimum_density)*rho**(search.penalization-1)*energy
            derivative=np.asarray(filt@(rho*derivative)).ravel()/np.maximum(rho*filt_sum,1e-12)
            lo,hi=1e-15,max(float(np.max(-derivative/volumes))*100,1.)
            candidate=rho.copy()
            for _ in range(70):
                lam=(lo+hi)/2
                candidate=np.clip(rho*np.sqrt(np.maximum(-derivative,0)/(volumes*lam)),rho-.15,rho+.15)
                candidate=np.clip(candidate,search.minimum_density,1)
                candidate[fixed]=1;candidate[void]=search.minimum_density
                if candidate@volumes>target:lo=lam
                else:hi=lam
            change=float(abs(candidate-rho).max())
            compliance_change=abs(compliance-previous)/max(abs(compliance),1e-12) if previous is not None else None
            history.append({"iteration":iteration+1,"compliance_n_mm":compliance,"volume_fraction":float(rho@volumes/sum(volumes)),"density_change":change,"compliance_change":compliance_change,"residual":solved["checks"]["residual"]})
            previous=compliance;rho=candidate
            if iteration>=3 and change<.01 and compliance_change<system.study.settings.convergence_tolerance:break
    finally:system.k=original
    keep={ids[i] for i,r in enumerate(rho) if r>=.5 or ids[i] in preserved}
    retained=[e for e in system.mesh.elements if e.id in keep]
    nodes={n for e in retained for n in e.nodes}
    # No smoothing/reconstruction is implied; analyze the actual retained tetrahedral solid.
    regions=[r.model_copy(update={"node_ids":[n for n in r.node_ids if n in nodes],"triangles":[t for t in r.triangles if set(t)<=nodes]}) for r in system.mesh.regions]
    reconstructed=system.mesh.model_copy(update={"id":"thresholded-solid","elements":retained,"nodes":{n:system.mesh.nodes[n] for n in nodes},"regions":regions,"source":"Thresholded SIMP tetrahedral solid; independent reanalysis, not editable CAD"})
    reanalysis=None;error=None
    try:
        independent=System(reconstructed,system.study,system.materials)
        reanalysis=static_solve(independent,case,cancel)
        requested={limit.quantity for limit in system.study.limits}
        if "min_frequency_hz" in requested:
            reanalysis["values"].update(modal_solve(independent,case,cancel)["values"])
        if "min_buckling_factor" in requested:
            reanalysis["values"].update(buckling_solve(independent,case,cancel)["values"])
    except (ValueError,RuntimeError) as exc:error=str(exc)
    return {"values":reanalysis["values"] if reanalysis else {},"checks":{"residual":max(h["residual"] for h in history),"optimization_converged":bool(history[-1]["density_change"]<.01),"threshold_reanalysis_available":bool(reanalysis),"target_volume_fraction":search.volume_fraction,"density_volume_fraction":float(rho@volumes/sum(volumes)),"retained_volume_fraction":float(sum(x[3] for x in system.elements if x[0].id in keep)/system.volume)},"field":{"nodes":{n:system.mesh.nodes[n] for n in system.ids},"element_density":{e:float(rho[i]) for i,e in enumerate(ids)},"retained_element_ids":sorted(keep)},"optimization_history":history,"reanalysis":({k:v for k,v in reanalysis.items() if k not in {"u","kg","k","t"}} if reanalysis else {"error":error}),"reconstructed_mesh":reconstructed.model_dump(mode="json"),"warnings":["Density optimization is compliance/volume only; other limits are evaluated on the thresholded outcome.","Unrestricted manufacturing; no additive, milling, casting or minimum-feature guarantee.","Thresholded tetrahedra are mesh guidance. Editable CAD conversion and independent final-CAD reanalysis remain required."]}


def generative(system,case,cancel=None):
    search=system.study.search
    if search is None or not search.candidate_material_ids:raise ValueError("Generative search requires explicit sourced material alternatives")
    if len(system.study.assignments)!=1:raise ValueError("Generative material alternatives currently require a single material assignment")
    outcomes=[]
    for material_id in search.candidate_material_ids:
        check_cancel(cancel)
        if material_id not in system.materials:raise ValueError(f"Missing candidate material: {material_id}")
        candidate=system.study.model_copy(deep=True)
        candidate.assignments[0].material_id=material_id
        try:
            result=optimize(System(system.mesh,candidate,system.materials),case,cancel)
            findings=[]
            for limit in candidate.limits:
                value=result["values"].get(limit.quantity)
                findings.append({"id":limit.id,"quantity":limit.quantity,"value":value,"limit":limit.value,"satisfied":bool(value is not None and (value<=limit.value if limit.operator=="le" else value>=limit.value))})
            outcomes.append({"id":f"outcome-{material_id}","material_id":material_id,"material_revision":system.materials[material_id].revision,"manufacturing":"unrestricted","evaluated":bool(result["checks"]["threshold_reanalysis_available"]),"all_limits_satisfied":bool(findings) and all(f["satisfied"] for f in findings),"findings":findings,"result":result})
        except (ValueError,RuntimeError) as exc:outcomes.append({"id":f"outcome-{material_id}","material_id":material_id,"evaluated":False,"error":str(exc)})
    evaluated=[o for o in outcomes if o["evaluated"]]
    return {"values":{"evaluated_outcomes":len(evaluated),"total_outcomes":len(outcomes)},"checks":{"residual":max((o["result"]["checks"]["residual"] for o in evaluated),default=1.),"all_outcomes_evaluated":len(evaluated)==len(outcomes)},"outcomes":outcomes,"field":{},"warnings":["Finite material/SIMP alternatives; no global-optimum or manufacturing-process parity claim."]}
