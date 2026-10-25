"""Canonical body-preserving, mass-weighted C3D10 elastic-mode correlation."""
import hashlib
import numpy as np
from .mode_tracking import (geometry,basis,quadrature,nodal_modes,Sampler,correlate_samples,
    MAX_SAMPLE_POINTS,MIN_CORRELATION,CLUSTER_GAP,QUADRATURE_TOLERANCE)


def track_modes(previous,current,requested,cancel=None):
    old_mesh,new_mesh=previous["mesh"],current["mesh"]
    old_ids=list(map(int,old_mesh["nodes"]));new_ids=list(map(int,new_mesh["nodes"]))
    old_index={n:i for i,n in enumerate(old_ids)};new_index={n:i for i,n in enumerate(new_ids)}
    if len(old_mesh["elements"])*(4**3+5**3)>MAX_SAMPLE_POINTS:
        raise ValueError(f"Canonical mode correlation exceeds {MAX_SAMPLE_POINTS} physical quadrature points")
    weighted_mass="density_tonne_mm3" in previous
    weight_key="density_tonne_mm3" if weighted_mass else "volume_weights"
    density=previous[weight_key]
    if density!=current[weight_key]:raise ValueError("Mode correlation requires unchanged body/physical integration weights")
    if set(previous["element_bodies"])!=set(current["element_bodies"]) or set(density)!=set(previous["element_bodies"]):
        raise ValueError("Mode correlation body ownership changed")
    old=nodal_modes(old_mesh,previous["fields"]);new=nodal_modes(new_mesh,current["fields"])
    body_data=[]
    for body in sorted(density):
        if density[body]<=0:raise ValueError("Modal correlation requires positive sourced density")
        bodies=[]
        for bundle in (previous,current):
            mesh=bundle["mesh"]
            elements=[e for e,b in zip(mesh["elements"],bundle["element_bodies"]) if b==body]
            used={n for e in elements for n in e}
            bodies.append({"nodes":{int(n):p for n,p in mesh["nodes"].items() if int(n) in used},"elements":elements})
        ids,_,connectivity,xyz=geometry(bodies[0])
        new_body_ids=list(bodies[1]["nodes"])
        body_data.append((body,connectivity,xyz,old[[old_index[n] for n in ids]],new[[new_index[n] for n in new_body_ids]],Sampler(bodies[1])))
    audits=[]
    for order in (4,5):
        reference,gauss=quadrature(order);shape,gradients=basis(reference)
        old_samples=[];new_samples=[];weights=[];body_masses={};evidence=hashlib.sha256()
        for body,connectivity,xyz,old_body,new_body,sampler in body_data:
            local=xyz-xyz[:,:1]
            points=(xyz[:,:1]+np.einsum("qn,eni->eqi",shape,local)).reshape(-1,3)
            jac=np.einsum("eni,qnj->eqij",local,gradients)
            mass=(np.linalg.det(jac)*gauss[None,:]*density[body]).ravel()
            if not np.isfinite(mass).all() or np.any(mass<=0):raise ValueError("Nonpositive physical mass quadrature prevents valid modal correlation")
            a=np.einsum("qn,enmc->eqmc",shape,old_body[connectivity]).reshape(-1,len(previous["fields"]),3)
            b=(sampler.interpolation(points,cancel)@new_body.reshape(len(new_body),-1)).reshape(-1,len(current["fields"]),3)
            old_samples.append(a);new_samples.append(b);weights.append(mass);body_masses[body]=float(mass.sum())
            evidence.update(body.encode()+points.tobytes()+mass.tobytes())
        audit=correlate_samples(np.concatenate(old_samples),np.concatenate(new_samples),np.concatenate(weights),previous["values"],current["values"],requested)
        audit.update({"quadrature_order":order,"sample_count":sum(len(w) for w in weights),("body_mass_tonne" if weighted_mass else "body_volume_mm3"):body_masses,"points_mass_sha256":evidence.hexdigest()})
        audits.append(audit)
    low,high=audits
    difference=np.abs(np.asarray(low["mac_matrix"])-np.asarray(high["mac_matrix"]))
    sensitivity=float(max(difference[:requested].max(),difference[:,:requested].max()))
    return {"schema_version":1,"method":"body-preserving physical C3D10 MAC and repeated-family principal angles",
        "inner_product":("positive physical volume times each declared body density; no cross-body interpolation" if weighted_mass else "positive physical volume; no artificial mass or cross-body interpolation"),
        "minimum_correlation":MIN_CORRELATION,"relative_cluster_gap":CLUSTER_GAP,
        "quadrature_tolerance":QUADRATURE_TOLERANCE,"quadrature_sensitivity":sensitivity,"quadrature_checks":audits,
        "matching":high["matching"],"groups":high["groups"],"quantity_changes":high["quantity_changes"],
        "passed":bool(low["passed"] and high["passed"] and sensitivity<=QUADRATURE_TOLERANCE)}
