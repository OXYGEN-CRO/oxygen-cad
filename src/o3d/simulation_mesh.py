"""Mesh generation inputs and geometric reference fixtures (no CAD import at module load)."""
from __future__ import annotations
from .simulation_models import MeshRecord, Element, Region


def block_mesh(geometry_key, size=(10., 2., 2.), divisions=(4, 2, 2), body_id="part", origin=(0., 0., 0.)):
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
    return MeshRecord(geometry_key=geometry_key,nodes=nodes,elements=elements,regions=regions,source="Explicit structured block numerical fixture; not CAD geometry")


def exterior_triangles(nodes,elements):
    import numpy as np
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
    for region in mesh.regions:
        for tri in region.triangles:
            if tuple(sorted(tri)) not in boundary:raise ValueError(f"Region {region.id} includes non-exterior element faces")

