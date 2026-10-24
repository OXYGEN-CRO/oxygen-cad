"""Whole-element C3D10 quality through isolated Gmsh adaptive Bernstein bounds.

This module imports no mesher in the application process. Its script entry point
runs in the pinned mesher environment for both Netgen and Gmsh output.
"""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path


def mesh_fingerprint(mesh):
    data={"nodes":{str(n):list(map(float,p)) for n,p in mesh["nodes"].items()},
          "elements":[list(map(int,e)) for e in mesh["elements"]]}
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def audit(mesh):
    import gmsh
    import numpy as np
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal",0)
        gmsh.option.setNumber("General.NumThreads",2)
        gmsh.logger.start()
        gmsh.model.add("oxygen-mesh-audit")
        tag=gmsh.model.addDiscreteEntity(3)
        nodes=sorted((int(n),p) for n,p in mesh["nodes"].items())
        gmsh.model.mesh.addNodes(3,tag,[n for n,_ in nodes],np.asarray([p for _,p in nodes]).ravel())
        # CalculiX and Gmsh exchange the last two midside nodes. No geometry or
        # connectivity changes: this is only the element-order convention.
        elements=np.asarray(mesh["elements"],dtype=np.int64)
        tags=np.arange(1,len(elements)+1,dtype=np.uint64)
        gmsh.model.mesh.addElementsByType(tag,11,tags,elements[:,[0,1,2,3,4,5,6,7,9,8]].ravel())
        lower=np.asarray(gmsh.model.mesh.getElementQualities(tags,"minDetJac"))
        upper=np.asarray(gmsh.model.mesh.getElementQualities(tags,"maxDetJac"))
        # det(J) is cubic on a quadratic tetrahedron. Degree-four quadrature
        # integrates it without substituting the straight-corner volume.
        points,weights=gmsh.model.mesh.getIntegrationPoints(11,"Gauss4")
        _,determinants,_=gmsh.model.mesh.getJacobians(11,points,tag)
        samples=np.asarray(determinants).reshape(len(elements),len(weights))
        volumes=samples @ np.asarray(weights)
        if any(len(v)!=len(elements) or not np.isfinite(v).all() for v in (lower,upper,volumes)):
            raise ValueError("Incomplete or nonfinite curved-element quality output")
        messages=gmsh.logger.get()
        if any(line.startswith("Error:") for line in messages):
            raise ValueError("Gmsh quality evaluation failed: "+"; ".join(messages[-5:]))
        return {"schema_version":1,"mesh_sha256":mesh_fingerprint(mesh),"element_type":"C3D10",
                "method":"Gmsh adaptive Bernstein lower/upper Jacobian bounds",
                "backend":{"name":"Gmsh","version":gmsh.__version__,
                           "library_sha256":hashlib.sha256(Path(gmsh.lib._name).read_bytes()).hexdigest()},
                "element_ids":tags.tolist(),"jacobian_lower_mm3":lower.tolist(),"jacobian_upper_mm3":upper.tolist(),
                "integrated_volume_mm3":volumes.tolist(),"quadrature":"Gauss4",
                "sampled_gauss4_minimum_mm3":samples.min(axis=1).tolist(),
                "scope":"Floating-point local element mapping; no formal interval, global overlap or physical validation proof"}
    finally:
        gmsh.finalize()


if __name__=="__main__":
    Path(sys.argv[2]).write_text(json.dumps(audit(json.loads(Path(sys.argv[1]).read_text())),allow_nan=False))
