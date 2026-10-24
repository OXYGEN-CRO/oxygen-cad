"""Isolated Gmsh process; never imports the CAD application's OCP libraries."""
import json
import sys
from pathlib import Path

import gmsh
import numpy as np


def mesh(request):
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.NumThreads", 2)
        gmsh.model.add("oxygen-analysis")
        gmsh.model.occ.importShapes(request["step"])
        gmsh.model.occ.synchronize()
        regions = {}
        for region in request["regions"]:
            matches = []
            for _, tag in gmsh.model.getEntities(2):
                center = gmsh.model.occ.getCenterOfMass(2, tag)
                area = gmsh.model.occ.getMass(2, tag)
                bounds = np.array(gmsh.model.getBoundingBox(2, tag)).reshape(2, 3)
                if (np.linalg.norm(np.array(center) - region["center"]) < 1e-4
                        and abs(area-region["area"]) < max(1e-4, region["area"]*1e-6)
                        and np.max(np.abs(bounds-np.array(region["bounds"]))) < 1e-4):
                    matches.append(tag)
            if len(matches) != 1:
                raise ValueError(f"STEP face transfer missing or ambiguous: {region['name']}")
            regions[region["name"]] = matches[0]
        gmsh.option.setNumber("Mesh.MeshSizeMax", request["mesh_size"])
        gmsh.option.setNumber("Mesh.MeshSizeMin", request["mesh_size"]*.3)
        gmsh.model.mesh.generate(3)
        gmsh.model.mesh.setOrder(2)
        tags, coordinates, _ = gmsh.model.mesh.getNodes()
        if len(tags) > request.get("max_nodes", 200000):
            raise ValueError("Analysis node limit exceeded; increase mesh size")
        nodes = {int(n): list(map(float, p)) for n, p in zip(tags, np.array(coordinates).reshape(-1, 3))}
        types, _, blocks = gmsh.model.mesh.getElements(3)
        elements = []
        for kind, block in zip(types, blocks):
            if kind != 11:
                raise ValueError("Gmsh must produce quadratic tetrahedra")
            elements.extend([[int(v[i]) for i in [0,1,2,3,4,5,6,7,9,8]] for v in np.array(block).reshape(-1,10)])
        surfaces = {}
        for name, tag in regions.items():
            types, _, blocks = gmsh.model.mesh.getElements(2, tag)
            surfaces[name] = []
            for kind, block in zip(types, blocks):
                if kind != 9:
                    raise ValueError("Expected quadratic surface triangles")
                surfaces[name].extend(np.array(block).reshape(-1,6).astype(int).tolist())
        return {"nodes": nodes, "elements": elements, "surfaces": surfaces,
                "mesher": {"name": "Gmsh", "version": gmsh.__version__}}
    finally:
        gmsh.finalize()


if __name__ == "__main__":
    Path(sys.argv[2]).write_text(json.dumps(mesh(json.loads(Path(sys.argv[1]).read_text()))))
