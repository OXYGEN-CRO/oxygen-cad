"""CalculiX C3D10 geometry, boundary connectivity and physical-point probes."""
from __future__ import annotations
import numpy as np

EDGES=((0,1),(1,2),(2,0),(0,3),(1,3),(2,3))
FACES=((0,1,2,4,5,6),(0,1,3,4,8,7),(0,2,3,6,9,7),(1,2,3,5,9,8))
GRADIENTS=np.array([[-1.,-1.,-1.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]])


def weights_and_gradients(reference):
    bary=np.r_[1-np.sum(reference),reference]
    weights=np.r_[bary*(2*bary-1),[4*bary[a]*bary[b] for a,b in EDGES]]
    gradients=np.vstack([(4*bary[:,None]-1)*GRADIENTS,
                         [4*(bary[a]*GRADIENTS[b]+bary[b]*GRADIENTS[a]) for a,b in EDGES]])
    return weights,gradients


def boundary_faces(elements,require_connected=False):
    faces,edges,corners,mids={}, {}, set(), set()
    parent=list(range(len(elements)));seen=set()
    def find(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    for index,element in enumerate(elements):
        tetra=tuple(sorted(element[:4]))
        if tetra in seen:raise ValueError("Duplicate volume element")
        seen.add(tetra)
        corners.update(element[:4]);mids.update(element[4:])
        for (a,b),node in zip(EDGES,element[4:]):
            key=tuple(sorted((element[a],element[b])))
            previous=edges.setdefault(key,node)
            if previous!=node:raise ValueError("Nonconforming quadratic mesh: a shared edge has different midside nodes")
        for indices in FACES:
            face=tuple(element[i] for i in indices);key=tuple(sorted(face[:3]))
            count,previous,owner=faces.get(key,(0,face,index))
            if count>=2:raise ValueError("Nonmanifold volume mesh: more than two tetrahedra share a face")
            if count and set(previous)!=set(face):raise ValueError("Nonconforming quadratic mesh at a shared face")
            if count:parent[find(index)]=find(owner)
            faces[key]=(count+1,face,index)
    if corners.intersection(mids):raise ValueError("Nonconforming mesh: a node is both a corner and a midside node")
    if require_connected and len({find(i) for i in range(len(elements))})!=1:
        raise ValueError("Disconnected volume mesh: elements must connect through complete faces, not only points or edges")
    return {key:face for key,(count,face,_) in faces.items() if count==1},edges


def locate_probe(mesh,point):
    point=np.asarray(point,dtype=float)
    if point.shape!=(3,) or not np.isfinite(point).all():raise ValueError("Probe must be a finite 3D point")
    nodes={int(n):np.asarray(p,dtype=float) for n,p in mesh["nodes"].items()}
    elements=np.asarray(mesh["elements"],dtype=np.int64)
    indices={n:i for i,n in enumerate(nodes)}
    positions=np.asarray(list(nodes.values()))
    connectivity=np.fromiter((indices[int(n)] for n in elements.ravel()),dtype=np.int64,count=elements.size).reshape(elements.shape)
    coordinates=positions[connectivity]
    origins=coordinates[:,0]
    local_coordinates=coordinates-origins[:,None,:]
    target_points=point-origins
    # Quadratic Bernstein control points enclose the curved image. Lagrange
    # nodes or the four corners alone do not give a valid bounding box.
    controls=np.concatenate([local_coordinates[:,:4],np.stack([2*local_coordinates[:,i+4]-.5*(local_coordinates[:,a]+local_coordinates[:,b]) for i,(a,b) in enumerate(EDGES)],axis=1)],axis=1)
    lengths=np.maximum(np.linalg.norm(np.ptp(controls,axis=1),axis=1),1e-12)
    tolerances=np.maximum.reduce([lengths*1e-9,np.max(np.abs(coordinates),axis=(1,2))*np.finfo(float).eps*32,np.full(len(elements),1e-12)])
    candidates=np.flatnonzero(np.all(target_points>=controls.min(1)-tolerances[:,None],axis=1)&np.all(target_points<=controls.max(1)+tolerances[:,None],axis=1))
    for index in candidates:
        element=elements[index];local=local_coordinates[index];target=target_points[index];tolerance=tolerances[index]
        try:start=np.linalg.solve(local[1:4].T,target)
        except np.linalg.LinAlgError:continue
        for seed in (start,np.full(3,.25)):
            reference=seed.copy()
            for _ in range(30):
                weights,gradients=weights_and_gradients(reference)
                residual=weights @ local-target
                norm=float(np.linalg.norm(residual))
                bary=np.r_[1-reference.sum(),reference]
                if norm<=tolerance:
                    if bary.min()>=-1e-7 and bary.max()<=1+1e-7:return element,weights
                    break
                try:delta=np.linalg.solve(local.T @ gradients,residual)
                except np.linalg.LinAlgError:break
                if not np.isfinite(delta).all():break
                accepted=False
                for fraction in (1.,.5,.25,.125,.0625,.03125):
                    candidate=reference-fraction*delta
                    w,_=weights_and_gradients(candidate)
                    if np.linalg.norm(w @ local-target)<norm:
                        reference=candidate;accepted=True;break
                if not accepted:break
    raise ValueError("The named probe could not be located inside the analysis mesh; choose an interior point away from idealized supports")


def surface_integrals(mesh,triangles):
    """Integrate quadratic triangle shape functions and physical first moments.

    Tensor-product Gauss4 under the Duffy square-to-triangle map. For planar
    C3D10 boundary faces the geometry determinant is quadratic, so this integrates
    shape*area and coordinate*area exactly up to floating-point quadrature error.
    Curved (nonplanar) surfaces are not an advertised load-boundary capability.
    """
    nodes={int(n):np.asarray(p,dtype=float) for n,p in mesh["nodes"].items()}
    ids=np.asarray(triangles,dtype=np.int64)
    xyz=np.array([[nodes[int(n)] for n in tri] for tri in ids])
    roots,gauss=np.polynomial.legendre.leggauss(4)
    roots=(roots+1)/2;gauss=gauss/2
    weights=[];gradients=[];integration=[]
    d=np.array([[-1.,-1.],[1.,0.],[0.,1.]])
    for i,u in enumerate(roots):
        for j,v in enumerate(roots):
            bary=np.array([1-u-(1-u)*v,u,(1-u)*v])
            weights.append(np.r_[bary*(2*bary-1),[4*bary[a]*bary[b] for a,b in ((0,1),(1,2),(2,0))]])
            gradients.append(np.vstack([(4*bary[:,None]-1)*d,[4*(bary[a]*d[b]+bary[b]*d[a]) for a,b in ((0,1),(1,2),(2,0))]]))
            integration.append(gauss[i]*gauss[j]*(1-u))
    weights=np.asarray(weights);gradients=np.asarray(gradients)
    local=xyz-xyz[:,0,None,:]
    jacobian=np.einsum('tni,qnj->tqij',local,gradients)
    area_elements=np.linalg.norm(np.cross(jacobian[:,:,:,0],jacobian[:,:,:,1]),axis=2)
    integrated=np.einsum('qn,tq,q->tn',weights,area_elements,np.asarray(integration))
    # Suppress quadrature roundoff at zero-weight nodes, per face rather than
    # relative to other loads. Legitimate curved-face corner weights remain.
    integrated[np.abs(integrated)<4e-15*np.sum(np.abs(integrated),axis=1)[:,None]]=0
    area=float(integrated.sum())
    if not np.isfinite(integrated).all() or area<=0:raise ValueError("Boundary region has zero or nonfinite integrated area")
    first_moment=np.einsum('tn,tni->i',integrated,xyz)
    return integrated,area,first_moment
