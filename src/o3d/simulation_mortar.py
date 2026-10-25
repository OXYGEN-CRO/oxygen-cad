"""Common-refinement quadratic surface mortar with physical curved measures.

Chart-affine P2 graph patches may curve in 3D. Non-affine projected edges refuse
until a curved intersection integrator is qualified; no corner faceting fallback.
"""
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import splu
from .simulation_eigen import check_cancel

MAX_MORTAR_ENTRIES=3_000_000


def cross(a,b):return a[0]*b[1]-a[1]*b[0]


def area(poly):
    if len(poly)<3:return 0.
    return abs(sum(cross(poly[i]-poly[0],poly[i+1]-poly[0]) for i in range(1,len(poly)-1)))/2


def clip(subject,triangle):
    polygon=[np.array(p) for p in subject]
    orientation=1 if cross(triangle[1]-triangle[0],triangle[2]-triangle[0])>0 else -1
    for a,b in zip(triangle,np.roll(triangle,-1,axis=0)):
        output=[]
        if not polygon:break
        for start,end in zip(polygon,polygon[1:]+polygon[:1]):
            ds=orientation*cross(b-a,start-a);de=orientation*cross(b-a,end-a)
            if (ds>=-1e-13)!=(de>=-1e-13):output.append(start+ds/(ds-de)*(end-start))
            if de>=-1e-13:output.append(end)
        polygon=[]
        for point in output:
            if not polygon or np.linalg.norm(point-polygon[-1])>1e-12:polygon.append(point)
        if len(polygon)>1 and np.linalg.norm(polygon[-1]-polygon[0])<1e-12:polygon.pop()
    return polygon


def basis(points):
    from .simulation_bonding import triangle_basis
    pairs=[triangle_basis(q) for q in points]
    return np.array([p[0] for p in pairs]),np.array([p[1] for p in pairs])


def quadrature(order):
    roots,weights=np.polynomial.legendre.leggauss(order);roots=(roots+1)/2;weights=weights/2
    return (np.array([(u,(1-u)*v) for u in roots for v in roots]),
            np.array([weights[i]*weights[j]*(1-u) for i,u in enumerate(roots) for j in range(order)]))


def graph_chart(xyz,axes,origin,length):
    uv=(xyz-origin)@axes.T/length
    expected=np.array([(uv[a]+uv[b])/2 for a,b in [(0,1),(1,2),(2,0)]])
    if np.max(abs(uv[3:]-expected))>1e-10:raise ValueError('General curved mortar edge intersections are not qualified; projected quadratic edges must be chart-affine, not replaced by corners')
    matrix=(uv[1:3]-uv[0]).T
    if abs(np.linalg.det(matrix))<1e-14:raise ValueError('Mortar surface chart is singular')
    return uv,np.linalg.inv(matrix)


def mortar_map(system,primary_id,secondary_id,tolerance,cancel=None):
    from .simulation_bonding import SurfaceProjector,triangle_basis
    primary=SurfaceProjector(system,primary_id);slave=SurfaceProjector(system,secondary_id)
    secondary_ids=sorted(set(slave.nodes.ravel()));primary_ids=sorted(set(primary.nodes.ravel()))
    ns,nm=len(secondary_ids),len(primary_ids)
    if max(ns*ns,ns*nm)>MAX_MORTAR_ENTRIES:raise ValueError('Mortar factor/transfer exceeds three million dense entries; larger/dual-space qualification is required')
    si={n:i for i,n in enumerate(secondary_ids)};mi={n:i for i,n in enumerate(primary_ids)}
    d=np.zeros((ns,ns));b=np.zeros((ns,nm));patches=[];pairs=[];maximum_geometry=0.;worst_quadrature=0.;total_area=0.;quadrature_errors=np.zeros(2)
    for index,xyz in enumerate(slave.xyz):
        check_cancel(cancel)
        _,g=triangle_basis([1/3,1/3]);normal=np.cross(*((xyz-xyz[0]).T@g).T)
        curvature=np.array([xyz[e+3]-(xyz[a]+xyz[b])/2 for e,(a,b) in enumerate([(0,1),(1,2),(2,0)])])
        _,singular,vh=np.linalg.svd(curvature)
        length=max(float(np.linalg.norm(np.ptp(xyz,axis=0))),1e-12)
        if singular[0]>length*1e-11:
            if singular[1]>singular[0]*1e-8:raise ValueError('General curved mortar edge intersections are not qualified; this patch has no affine graph chart')
            axes=vh[1:]
        else:axes=np.linalg.svd(normal[None,:],full_matrices=True)[2][1:]
        origin=xyz[0]
        uv,inv=graph_chart(xyz,axes,origin,length);slave_area=area(uv[:3]);coverage=0.;polygons=[];owners=set()
        for j,master in enumerate(primary.xyz):
            if np.any(slave.hi[index]<primary.lo[j]-tolerance) or np.any(primary.hi[j]<slave.lo[index]-tolerance):continue
            muv,minv=graph_chart(master,axes,origin,length)
            polygon=clip(uv[:3],muv[:3]);overlap=area(polygon)
            if overlap<slave_area*1e-13:continue
            for previous in polygons:
                # Every common polygon is convex; triangulate the previous
                # polygon to check for any positive-area double coverage.
                for k in range(1,len(previous)-1):
                    if area(clip(polygon,np.array([previous[0],previous[k],previous[k+1]])))>slave_area*1e-10:raise ValueError('Ambiguous overlapping primary mortar coverage')
            polygons.append(polygon);coverage+=overlap
            owners.add(system.component_of[int(primary.nodes[j,0])])
            sn=[si[int(n)] for n in slave.nodes[index]];mn=[mi[int(n)] for n in primary.nodes[j]]
            for k in range(1,len(polygon)-1):
                triangle=np.array([polygon[0],polygon[k],polygon[k+1]])
                corners=np.array([triangle[0],triangle[1],triangle[2],(triangle[0]+triangle[1])/2,(triangle[1]+triangle[2])/2,(triangle[2]+triangle[0])/2])
                qs=(corners-uv[0])@inv.T;qm=(corners-muv[0])@minv.T
                a,_=basis(qs);c,_=basis(qm);difference=a@xyz-c@master
                controls=difference.copy()
                for e,(left,right) in enumerate([(0,1),(1,2),(2,0)]):controls[e+3]=2*difference[e+3]-(difference[left]+difference[right])/2
                error=float(np.linalg.norm(controls,axis=1).max());maximum_geometry=max(maximum_geometry,error)
                if error>tolerance:raise ValueError('Mortar graph patches do not coincide within the rigid-motion geometry bound; no coordinate adjustment is permitted')
                def integrate(order):
                    q,w=quadrature(order);chart=triangle[0]+q@(triangle[1:]-triangle[0]);ps=(chart-uv[0])@inv.T;pm=(chart-muv[0])@minv.T
                    n,gs=basis(ps);m,gm=basis(pm)
                    js=np.einsum('ni,qnj->qij',xyz-xyz[0],gs);jm=np.einsum('ni,qnj->qij',master-master[0],gm)
                    nsurf=np.cross(js[:,:,0],js[:,:,1]);msurf=np.cross(jm[:,:,0],jm[:,:,1])
                    cosine=np.einsum('qi,qi->q',nsurf,msurf)/(np.linalg.norm(nsurf,axis=1)*np.linalg.norm(msurf,axis=1))
                    if np.any(cosine>-.9):raise ValueError('Mortar primary/secondary outward normals are not opposed')
                    mapping=inv@(triangle[1:]-triangle[0]).T
                    physical=js@mapping;measure=np.linalg.norm(np.cross(physical[:,:,0],physical[:,:,1]),axis=1)*w
                    return n.T@(measure[:,None]*n),n.T@(measure[:,None]*m),n,m,measure
                low=integrate(4);high=integrate(6)
                errors=np.array([np.linalg.norm(high[h]-low[h]) for h in (0,1)])
                change=max(errors[h]/max(np.linalg.norm(high[h]),1e-300) for h in (0,1))
                if change>1e-10:
                    final=integrate(8);errors=np.array([np.linalg.norm(final[h]-high[h]) for h in (0,1)]);change=max(errors[h]/max(np.linalg.norm(final[h]),1e-300) for h in (0,1));high=final
                # Accumulate absolute matrix errors: microscopic intersection
                # slivers must not set a relative denominator for the whole
                # operator. The sum bounds cancellation across common cells.
                quadrature_errors+=errors
                worst_quadrature=max(worst_quadrature,float(change));d[np.ix_(sn,sn)]+=high[0];b[np.ix_(sn,mn)]+=high[1]
                patches.append((slave.nodes[index].tolist(),primary.nodes[j].tolist(),high[2],high[3],high[4]));total_area+=float(high[4].sum())
        if abs(coverage-slave_area)>slave_area*1e-10:raise ValueError('Mortar secondary surface is not completely covered; extrapolation/coordinate adjustment is prohibited')
        if len(owners)!=1:raise ValueError('Secondary mortar face crosses disconnected primary solids; split the interface')
        pairs.append((system.component_of[int(slave.nodes[index,0])],next(iter(owners))))
    sensitivity=max(quadrature_errors[0]/max(np.linalg.norm(d),1e-300),quadrature_errors[1]/max(np.linalg.norm(b),1e-300))
    if sensitivity>1e-10:raise ValueError('Mortar operator quadrature did not converge; refine the curved interface')
    factor=splu(sparse.csc_matrix(d));transfer=factor.solve(b)
    residual=float(np.linalg.norm(d@transfer-b)/max(np.linalg.norm(b),1e-300))
    if residual>1e-11 or not np.isfinite(transfer).all():raise ValueError('Mortar projection solve is unresolved')
    mapping={int(n):{int(m):float(w) for m,w in zip(primary_ids,transfer[i]) if abs(w)>1e-14} for i,n in enumerate(secondary_ids)}
    audit={'method':'common-refinement full-quadratic surface mortar; secondary trace as multiplier space','projection_residual':residual,
        'maximum_geometry_bound_mm':maximum_geometry,'operator_quadrature_sensitivity':float(sensitivity),'maximum_local_quadrature_change':worst_quadrature,'quadrature_error_accounting':'sum of absolute common-cell matrix differences divided by complete operator norm; no sliver cancellation','integrated_area_mm2':total_area,
        'common_triangles':len(patches),'primary_nodes':nm,'secondary_nodes':ns,'chart_domain':'quadratic 3D graph patches with chart-affine projected edges; general curved edge intersections refuse'}
    return mapping,audit,patches,pairs,d,b
