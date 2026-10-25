"""Sample native piecewise-constant fields at the finer mesh's physical cell centres."""
import numpy as np
from scipy.spatial import cKDTree


def sampled_change(coarse_mesh,coarse,fine,density_kg_m3,cancel=None):
    elements=sorted(coarse_mesh.elements,key=lambda e:e.id)
    xyz=np.array([[coarse_mesh.nodes[n] for n in e.nodes] for e in elements])*1e-3
    inverse=np.linalg.inv(np.swapaxes(xyz[:,1:]-xyz[:,:1],1,2));centres=xyz.mean(axis=1);tree=cKDTree(centres)
    points=np.array(fine['centers_m']);velocity=np.array(coarse['velocity_m_s']);pressure=np.array(coarse['pressure_gauge_pa'])
    coarse_volume=np.array(coarse['volumes_m3']);pressure-=np.dot(coarse_volume,pressure)/sum(coarse_volume)
    sampled_u=np.zeros((len(points),3));sampled_p=np.zeros(len(points));found=np.zeros(len(points),dtype=bool)
    for start in range(0,len(points),512):
        if cancel and cancel.is_set():
            from .solver import Cancelled
            raise Cancelled('Flow field comparison cancelled')
        indexes=np.arange(start,min(start+512,len(points)))
        for size in [32,64,128,256]:
            pending=indexes[~found[indexes]]
            if not len(pending):break
            _,candidates=tree.query(points[pending],k=min(size,len(elements)));candidates=np.array(candidates).reshape(len(pending),-1)
            local=np.einsum('pcij,pcj->pci',inverse[candidates],points[pending,None,:]-xyz[candidates,0,:])
            bary=np.concatenate([1-local.sum(axis=2,keepdims=True),local],axis=2)
            inside=(bary.min(axis=2)>=-1e-10)&(bary.max(axis=2)<=1+1e-10)
            valid=inside.any(axis=1);rows=np.flatnonzero(valid);chosen=inside[valid].argmax(axis=1);tets=candidates[rows,chosen];coordinates=bary[rows,chosen]
            # Native chunks are the regions where one tetrahedral barycentric
            # coordinate is maximal. At a shared chunk boundary use the mean
            # of its two/four traces; never extrapolate outside the old domain.
            weights=np.isclose(coordinates,coordinates.max(axis=1,keepdims=True),atol=1e-10,rtol=0).astype(float);weights/=weights.sum(axis=1,keepdims=True)
            native=4*tets[:,None]+np.arange(4)[None,:];target=pending[valid]
            sampled_u[target]=np.einsum('pi,pij->pj',weights,velocity[native]);sampled_p[target]=np.einsum('pi,pi->p',weights,pressure[native]);found[target]=True
            if size>=len(elements):break
    if not found.all():raise ValueError('Fine fluid cells cannot be located in the prior fluid volume within the bounded search; extrapolation is prohibited')
    volume=np.array(fine['volumes_m3']);u=np.array(fine['velocity_m_s']);p=np.array(fine['pressure_gauge_pa']);p-=np.dot(volume,p)/sum(volume)
    u_scale=max(np.linalg.norm(u,axis=1).max(),np.linalg.norm(velocity,axis=1).max(),1e-12)
    p_scale=max(float(np.ptp(p)),float(np.ptp(pressure)),density_kg_m3*u_scale**2,1e-12)
    return {'sampled_velocity_field_change':float(np.sqrt(np.dot(volume,np.sum((u-sampled_u)**2,axis=1))/sum(volume))/u_scale),
        'sampled_pressure_field_change':float(np.sqrt(np.dot(volume,(p-sampled_p)**2)/sum(volume))/p_scale)}
