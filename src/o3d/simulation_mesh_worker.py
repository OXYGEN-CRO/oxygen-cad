"""Runs in the separate Netgen environment; never imports OCP/application models."""

def netgen_mesh(request):
    """Called only by the isolated Netgen interpreter; supports per-body STEP files."""
    import numpy as np
    from netgen.occ import OCCGeometry
    nodes,elements,regions={},[],[]
    for part in request["parts"]:
        shape=OCCGeometry(part["step"]).shape
        selected=[r for r in request["regions"] if r["body_id"]==part["body_id"]]
        for region in selected:
            matches=[f for f in shape.faces if np.linalg.norm(np.array(list(f.center))-region["center_mm"])<1e-5 and abs(f.mass-region["area_mm2"])<max(1e-5,region["area_mm2"]*1e-6)]
            if len(matches)!=1:raise ValueError(f"Missing/ambiguous STEP face for region {region['id']}; explicit repair required")
            matches[0].name=region["id"]
        result=OCCGeometry(shape).GenerateMesh(maxh=request["mesh_size_mm"],grading=.3,optsteps3d=3)
        offset=len(nodes)
        used={int(p.nr) for e in result.Elements3D() for p in e.points}
        ids={n:offset+i+1 for i,n in enumerate(sorted(used))}
        for n in sorted(used):nodes[ids[n]]=list(result[n].p)
        for e in result.Elements3D():
            pts=[ids[int(p.nr)] for p in e.points]
            if len(pts)!=4:raise ValueError("Expected linear tetrahedra in reference worker")
            elements.append({"id":len(elements)+1,"body_id":part["body_id"],"nodes":pts})
        by_region={r["id"]:[] for r in selected}
        for e in result.Elements2D():
            name=result.FaceDescriptor(e.index).bcname
            if name in by_region:by_region[name].append([ids[int(p.nr)] for p in e.points])
        for region in selected:
            if not by_region[region["id"]]:raise ValueError(f"Empty transferred boundary: {region['id']}")
            regions.append({**region,"triangles":by_region[region["id"]]})
    return {"id":request.get("id","mesh"),"geometry_key":request["geometry_key"],"nodes":nodes,"elements":elements,"regions":regions,"source":"Netgen per-body exact STEP; area/centroid checked, named region mapping","mesh_size_mm":request["mesh_size_mm"],"element_family":"C3D4"}


if __name__=="__main__":
    import json,sys
    from pathlib import Path
    # Netgen environment does not depend on the application Pydantic environment.
    Path(sys.argv[2]).write_text(json.dumps(netgen_mesh(json.loads(Path(sys.argv[1]).read_text()))))
