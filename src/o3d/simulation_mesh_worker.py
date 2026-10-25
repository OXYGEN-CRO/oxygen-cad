"""Runs in the separate Netgen environment; never imports OCP/application models."""

def netgen_mesh(request):
    """Called only by the isolated Netgen interpreter; supports per-body STEP files."""
    import numpy as np
    from netgen.occ import OCCGeometry
    nodes,elements,regions,volumes={},[],[],{}
    family=request.get("element_family","C3D4")
    if family not in {"C3D4","C3D10"}:raise ValueError("Select C3D4 or C3D10 elements")
    for part in request["parts"]:
        shape=OCCGeometry(part["step"]).shape
        volumes[part["body_id"]]=float(shape.mass)
        selected=[r for r in request["regions"] if r["body_id"]==part["body_id"]]
        for region in selected:
            matches=[f for f in shape.faces if np.linalg.norm(np.array(list(f.center))-region["center_mm"])<1e-5 and abs(f.mass-region["area_mm2"])<max(1e-5,region["area_mm2"]*1e-6)]
            if len(matches)!=1:raise ValueError(f"Missing/ambiguous STEP face for region {region['id']}; explicit repair required")
            matches[0].name=region["id"]
        result=OCCGeometry(shape).GenerateMesh(maxh=request["mesh_size_mm"],grading=.3,optsteps3d=3,curvaturesafety=4,segmentsperedge=2)
        if family=="C3D10":result.SecondOrder()
        offset=len(nodes)
        used={int(p.nr) for e in result.Elements3D() for p in e.points}
        ids={n:offset+i+1 for i,n in enumerate(sorted(used))}
        for n in sorted(used):nodes[ids[n]]=list(result[n].p)
        for e in result.Elements3D():
            pts=[ids[int(p.nr)] for p in e.points]
            if len(pts)!=(10 if family=="C3D10" else 4):raise ValueError("Mesher element order differs from the requested family")
            if family=="C3D10":pts=[pts[i] for i in (0,1,3,2,4,8,6,5,7,9)]
            elements.append({"id":len(elements)+1,"body_id":part["body_id"],"nodes":pts})
        by_region={r["id"]:[] for r in selected}
        for e in result.Elements2D():
            name=result.FaceDescriptor(e.index).bcname
            if name in by_region:
                pts=[ids[int(p.nr)] for p in e.points]
                if family=="C3D10":pts=[pts[i] for i in (0,1,2,5,3,4)]
                by_region[name].append(pts)
        for region in selected:
            if not by_region[region["id"]]:raise ValueError(f"Empty transferred boundary: {region['id']}")
            regions.append({**region,"triangles":by_region[region["id"]]})
    return {"id":request.get("id","mesh"),"geometry_key":request["geometry_key"],"nodes":nodes,"elements":elements,"regions":regions,"source":"Netgen per-body exact STEP; area/centroid checked, named region mapping","mesh_size_mm":request["mesh_size_mm"],"element_family":family,"cad_volumes_mm3":volumes}


if __name__=="__main__":
    import json,sys
    from pathlib import Path
    # Netgen environment does not depend on the application Pydantic environment.
    Path(sys.argv[2]).write_text(json.dumps(netgen_mesh(json.loads(Path(sys.argv[1]).read_text()))))
