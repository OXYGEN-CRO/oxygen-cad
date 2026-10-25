"""Conforming tetrahedral fluid-volume mesh to finite-volume polyMesh, in SI."""
from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np

MAX_CELLS=150000
MAX_FACES=600000


def foam_header(kind,name):
    return f'FoamFile\n{{\nversion 2.0;\nformat ascii;\nclass {kind};\nobject {name};\n}}\n'


def number(value):
    value=float(value)
    if not np.isfinite(value):raise ValueError('Nonfinite finite-volume input')
    return format(value,'.17g')


def write_mesh(mesh,boundaries,directory):
    """Convex conservative control volumes; no default wall patch or quadratic reduction."""
    if mesh.element_family!='C3D4':raise ValueError('Finite-volume flow requires an explicit C3D4 fluid mesh; no quadratic corner fallback')
    if 4*len(mesh.elements)>MAX_CELLS:raise ValueError('Fluid control mesh exceeds 150000 finite volumes after subdivision')
    if len({e.body_id for e in mesh.elements})!=1:raise ValueError('Single homogeneous fluid volume required; solid/conjugate regions are not discarded')
    ids=sorted(mesh.nodes);point_keys={};points=[]
    def point(nodes):
        key=tuple(sorted(nodes))
        if key not in point_keys:
            point_keys[key]=len(points);points.append(np.array([mesh.nodes[n] for n in key]).mean(axis=0)*1e-3)
        return point_keys[key]
    # Within each tetrahedron the four regions lambda_a >= lambda_b for every b
    # are convex hexahedra. Keep them separate across tetrahedra: agglomerating
    # a vertex's chunks can create concave median-dual cells.
    from itertools import permutations,combinations
    faces={};volumes=[];centers=[];cell_ids=[];parent_ids=[];vertices=[];boundary_subfaces={}
    for e in sorted(mesh.elements,key=lambda e:e.id):
        nodes=list(e.nodes);xyz=np.array([mesh.nodes[n] for n in nodes])*1e-3
        # C3D4 connectivity can have either orientation (including Netgen's).
        # Geometry and native outward face normals are constructed separately;
        # the source vertex/chunk order remains exact, without moving a node.
        det=abs(float(np.linalg.det((xyz[1:]-xyz[0]).T)))
        if not np.isfinite(det) or det<=0:raise ValueError('Fluid tetrahedron has zero/nonfinite SI volume')
        tetra_center=point(nodes)
        for vertex in nodes:
            others=[n for n in nodes if n!=vertex];cell=len(centers);local_faces=[];volume=0.;moment=np.zeros(3)
            for b,c,d in permutations(others):
                v=np.array([points[point([vertex])],points[point([vertex,b])],points[point([vertex,b,c])],points[tetra_center]])
                weight=abs(float(np.linalg.det((v[1:]-v[0]).T)))/6
                volume+=weight;moment+=weight*v.mean(axis=0)
            if abs(volume-det/24)>max(det,1e-300)*1e-12:raise ValueError('Fluid subdivision does not conserve tetrahedral volume')
            center=moment/volume
            centers.append(center);volumes.append(volume);cell_ids.append(cell);parent_ids.append(e.id);vertices.append(vertex)
            for b,c in combinations(others,2):
                triangle=tuple(sorted([vertex,b,c]))
                face=[point([vertex]),point([vertex,b]),point(triangle),point([vertex,c])]
                boundary_subfaces.setdefault(triangle,set()).add(tuple(sorted(face)))
                local_faces.append(face)
            for b in others:
                c,d=[n for n in others if n!=b]
                local_faces.append([point([vertex,b]),point([vertex,b,c]),tetra_center,point([vertex,b,d])])
            for nodes_on_face in local_faces:
                v=np.array([points[n] for n in nodes_on_face]);face_center=v.mean(axis=0)
                normal=sum((np.cross(v[i]-face_center,v[(i+1)%4]-face_center) for i in range(4)),np.zeros(3))/2
                if np.dot(normal,center-face_center)>0:nodes_on_face.reverse()
                key=tuple(sorted(nodes_on_face))
                if key in faces:
                    if faces[key]['neighbour'] is not None:raise ValueError('Nonmanifold fluid control-volume face')
                    faces[key]['neighbour']=cell
                else:faces[key]={'nodes':nodes_on_face,'owner':cell,'neighbour':None}
    if len(faces)>MAX_FACES:raise ValueError('Fluid mesh exceeds 600000 faces')
    internal=sorted((f for f in faces.values() if f['neighbour'] is not None),key=lambda f:(f['owner'],f['neighbour']))
    graph=[[] for _ in centers]
    for f in internal:graph[f['owner']].append(f['neighbour']);graph[f['neighbour']].append(f['owner'])
    seen={0};queue=deque([0])
    while queue:
        for neighbour in graph[queue.popleft()]:
            if neighbour not in seen:seen.add(neighbour);queue.append(neighbour)
    if len(seen)!=len(centers):raise ValueError('Disconnected fluid pockets require separate pressure references; no pocket is ignored')
    regions={r.id:r for r in mesh.regions};assigned={};patches=[]
    for b in sorted(boundaries,key=lambda b:b.id):
        if b.region_id not in regions:raise ValueError('Fluid boundary references a missing mesh region')
        region=regions[b.region_id]
        if not region.triangles:raise ValueError('Fluid boundary requires explicit oriented triangles')
        patch=[];normals=[]
        for triangle in region.triangles:
            keys=boundary_subfaces.get(tuple(sorted(triangle)))
            if not keys or len(keys)!=3:raise ValueError('Fluid boundary is not a source tetrahedral face')
            for key in sorted(keys):
                face=faces.get(key)
                if face is None or face['neighbour'] is not None:raise ValueError('Fluid boundary is not an exterior control-volume face')
                if key in assigned:raise ValueError('Overlapping fluid boundary assignments')
                assigned[key]=b.id;patch.append(face)
                v=np.array([points[n] for n in face['nodes']]);normal=np.cross(v[1]-v[0],v[2]-v[0]);normals.append(normal/np.linalg.norm(normal))
        if b.kind=='symmetry' and np.max(np.linalg.norm(np.array(normals)-normals[0],axis=1))>1e-8:
            raise ValueError('A symmetry-plane patch must be planar and consistently oriented')
        patches.append({'name':f'patch_{len(patches):04d}','boundary_id':b.id,'region_id':b.region_id,'kind':b.kind,
            'type':'wall' if b.kind in {'no_slip','moving_wall'} else 'symmetryPlane' if b.kind=='symmetry' else 'patch',
            'faces':sorted(patch,key=lambda f:(f['owner'],f['nodes']))})
    exterior=[f for f in faces.values() if f['neighbour'] is None]
    if len(assigned)!=len(exterior):raise ValueError('Every exterior fluid face requires exactly one explicit boundary condition')
    ordered=[*internal];patch_records=[]
    for p in patches:
        patch_records.append({k:v for k,v in p.items() if k!='faces'}|{'start_face':len(ordered),'face_count':len(p['faces'])})
        ordered.extend(p['faces'])
    destination=Path(directory)/'constant/polyMesh';destination.mkdir(parents=True,exist_ok=False)
    def write(name,kind,items):
        (destination/name).write_text(foam_header(kind,name)+str(len(items))+'\n(\n'+'\n'.join(items)+'\n)\n')
    write('points','vectorField',['('+' '.join(number(v) for v in p)+')' for p in points])
    write('faces','faceList',[str(len(f['nodes']))+'('+' '.join(str(n) for n in f['nodes'])+')' for f in ordered])
    write('owner','labelList',[str(f['owner']) for f in ordered])
    write('neighbour','labelList',[str(f['neighbour']) for f in internal])
    write('boundary','polyBoundaryMesh',[f"{p['name']}\n{{ type {p['type']}; nFaces {p['face_count']}; startFace {p['start_face']}; }}" for p in patch_records])
    for name in ('pointZones','faceZones','cellZones'):write(name,'regIOobject',[])
    return {'node_ids':ids,'cell_ids':cell_ids,'source_element_ids':parent_ids,'source_vertex_ids':vertices,'centers_m':np.array(centers),'volumes_m3':np.array(volumes),
        'faces':ordered,'internal_faces':len(internal),'patches':patch_records,'coordinates_m':np.array(points)}
