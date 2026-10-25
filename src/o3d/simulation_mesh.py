"""Mesh generation inputs and geometric reference fixtures (no CAD import at module load)."""
from __future__ import annotations
from .simulation_models import MeshRecord, Element, Region


def block_mesh(geometry_key, size=(10., 2., 2.), divisions=(4, 2, 2), body_id="part", origin=(0., 0., 0.),element_family="C3D4"):
    """Six conforming C3D4 per structured brick; only an explicit numerical fixture."""
    nx,ny,nz=divisions
    if min(divisions)<1 or max(divisions)>50:raise ValueError("Block divisions must be 1..50")
    nodes={};grid={}
    for i in range(nx+1):
        for j in range(ny+1):
            for k in range(nz+1):
                n=len(nodes)+1;grid[i,j,k]=n
                nodes[n]=tuple(origin[a]+size[a]*t/divisions[a] for a,t in enumerate((i,j,k)))
    elements=[]
    pattern=((0,1,3,7),(0,3,2,7),(0,2,6,7),(0,6,4,7),(0,4,5,7),(0,5,1,7))
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                corners=[grid[i+di,j+dj,k+dk] for di,dj,dk in ((0,0,0),(1,0,0),(0,1,0),(1,1,0),(0,0,1),(1,0,1),(0,1,1),(1,1,1))]
                for tet in pattern:elements.append(Element(id=len(elements)+1,body_id=body_id,nodes=tuple(corners[t] for t in tet)))
    boundary=exterior_triangles(nodes,elements)
    regions=[]
    for axis in range(3):
        for end in range(2):
            coord=origin[axis]+size[axis]*end
            tris=[t for t in boundary if all(abs(nodes[n][axis]-coord)<1e-8 for n in t)]
            normal=[0.,0.,0.];normal[axis]=2*end-1
            regions.append(Region(id=f"{body_id}:{'xyz'[axis]}{'max' if end else 'min'}",body_id=body_id,triangles=tris,normal=tuple(normal)))
    result=MeshRecord(geometry_key=geometry_key,nodes=nodes,elements=elements,regions=regions,source="Explicit structured block numerical fixture; not CAD geometry")
    if element_family=="C3D10":return quadratic_reference_mesh(result)
    if element_family!="C3D4":raise ValueError("Choose C3D4 or C3D10 for the explicit reference block")
    return result


def quadratic_reference_mesh(mesh):
    """Elevate an explicit straight mesh for references; never a curved-CAD mesher."""
    from .quadratic import EDGES
    if mesh.element_family!="C3D4":raise ValueError("Reference elevation requires a C3D4 mesh")
    nodes=dict(mesh.nodes);edges={};elements=[];next_node=max(nodes)+1
    for element in mesh.elements:
        mids=[]
        for a,b in EDGES:
            key=tuple(sorted((element.nodes[a],element.nodes[b])))
            if key not in edges:
                n=next_node;next_node+=1
                nodes[n]=tuple((nodes[key[0]][i]+nodes[key[1]][i])/2 for i in range(3));edges[key]=n
            mids.append(edges[key])
        elements.append({**element.model_dump(),"nodes":(*element.nodes,*mids)})
    regions=[]
    for region in mesh.regions:
        triangles=[(*tri,*(edges[tuple(sorted((tri[a],tri[b])))] for a,b in ((0,1),(1,2),(2,0)))) for tri in region.triangles]
        regions.append({**region.model_dump(),"triangles":triangles})
    return MeshRecord.model_validate({**mesh.model_dump(),"nodes":nodes,"elements":elements,"regions":regions,"element_family":"C3D10","source":mesh.source+"; affine P2 elevation, no curved geometry inferred"})


def exterior_triangles(nodes,elements):
    import numpy as np
    if elements and len(elements[0].nodes)==10:
        from .quadratic import boundary_faces,FACES
        boundary,_=boundary_faces([e.nodes for e in elements])
        output=[]
        for element in elements:
            for face in FACES:
                tri=tuple(element.nodes[i] for i in face)
                if tuple(sorted(tri[:3])) not in boundary:continue
                opposite=next(i for i in range(4) if i not in face[:3])
                p=np.array([nodes[n] for n in tri[:3]])
                if np.dot(np.cross(p[1]-p[0],p[2]-p[0]),np.array(nodes[element.nodes[opposite]])-p[0])>0:
                    tri=(tri[0],tri[2],tri[1],tri[5],tri[4],tri[3])
                output.append(tri)
        return output
    faces={}
    for element in elements:
        for a,b,c,d in ((0,1,2,3),(0,3,1,2),(0,2,3,1),(1,3,2,0)):
            tri=tuple(element.nodes[i] for i in (a,b,c));key=tuple(sorted(tri))
            p=np.array([nodes[n] for n in tri]);opposite=np.array(nodes[element.nodes[d]])
            if np.dot(np.cross(p[1]-p[0],p[2]-p[0]),opposite-p[0])>0:tri=(tri[0],tri[2],tri[1])
            if key in faces:faces[key]=None
            else:faces[key]=tri
    return [t for t in faces.values() if t is not None]


def validate_boundary(mesh):
    boundary={tuple(sorted(t)) for t in exterior_triangles(mesh.nodes,mesh.elements)}
    if mesh.element_family=="C3D10":
        from .quadratic import boundary_faces
        _,edges=boundary_faces([e.nodes for e in mesh.elements])
    for region in mesh.regions:
        if len({tuple(sorted(t)) for t in region.triangles})!=len(region.triangles):
            raise ValueError(f"Region {region.id} repeats an element face")
        for tri in region.triangles:
            if tuple(sorted(tri)) not in boundary:raise ValueError(f"Region {region.id} includes non-exterior element faces")
            if mesh.element_family=="C3D10" and any(edges[tuple(sorted((tri[a],tri[b])))]!=mid for (a,b),mid in zip(((0,1),(1,2),(2,0)),tri[3:])):
                raise ValueError(f"Region {region.id} has invalid quadratic face-node ordering")


def display_triangles(mesh):
    faces=exterior_triangles(mesh.nodes,mesh.elements)
    if mesh.element_family=="C3D4":return faces
    return [tri for a,b,c,ab,bc,ca in faces for tri in ((a,ab,ca),(ab,b,bc),(ca,bc,c),(ab,bc,ca))]


def displacement_probe(mesh,point,body,field,cache):
    """Sample a declared physical point; never replace an outside point by a node."""
    if point is None:return None
    import numpy as np
    key=(tuple(point),body)
    if key not in cache:
        bodies={e.body_id for e in mesh.elements}
        if body is None:
            if len(bodies)!=1:raise ValueError("A multi-body displacement probe requires its body ID")
            body=next(iter(bodies))
        if body not in bodies:raise ValueError("Probe body does not exist in the analysis model")
        cells=[e.nodes for e in mesh.elements if e.body_id==body]
        nodes={n:mesh.nodes[n] for e in cells for n in e}
        if mesh.element_family=="C3D10":
            from .quadratic import locate_probe
            element,weights=locate_probe({"nodes":nodes,"elements":cells},point)
        else:
            xyz=np.array([[nodes[n] for n in e] for e in cells]);target=np.array(point)
            scale=max(float(np.linalg.norm(np.ptp(xyz.reshape(-1,3),axis=0))),1e-12)
            candidates=np.flatnonzero(np.all(target>=xyz.min(axis=1)-scale*1e-9,axis=1)&np.all(target<=xyz.max(axis=1)+scale*1e-9,axis=1))
            found=None
            for i in candidates:
                bary=np.linalg.solve((xyz[i,1:]-xyz[i,0]).T,target-xyz[i,0])
                weights=np.r_[1-bary.sum(),bary]
                if weights.min()>=-1e-7 and weights.max()<=1+1e-7:found=(cells[i],weights);break
            if found is None:raise ValueError("The named probe could not be located inside its analysis body")
            element,weights=found
        cache[key]=(element,weights,body)
    element,weights,actual_body=cache[key]
    value=weights@np.array([field[int(n)] for n in element])
    return {"point_mm":list(point),"body_id":actual_body,"displacement_mm":value.tolist(),"magnitude_mm":float(np.linalg.norm(value)),"method":f"{mesh.element_family} interpolation at the same declared physical coordinate on every refinement"}

