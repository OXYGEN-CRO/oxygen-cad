"""Runs exclusively in the isolated Netgen environment (never imports OCP)."""
import json
import sys
from pathlib import Path

import numpy as np
from netgen.occ import OCCGeometry


def mesh(request):
    shape = OCCGeometry(request["step"]).shape
    for region in request["regions"]:
        matches = [face for face in shape.faces
                   if np.linalg.norm(np.array(list(face.center)) - region["center"]) < 1e-4
                   and abs(face.mass - region["area"]) < max(1e-4, region["area"] * 1e-6)]
        if len(matches) != 1:
            raise ValueError(f"STEP face transfer is missing or ambiguous for {region['name']}")
        matches[0].name = region["name"]
    geo = OCCGeometry(shape)
    result = geo.GenerateMesh(maxh=request["mesh_size"], grading=0.3, optsteps3d=3)
    result.SecondOrder()
    nodes = {int(p.nr): list(result[p].p) for element in result.Elements3D() for p in element.points}
    if len(nodes) > 200000:
        raise ValueError("Analysis exceeds the alpha limit of 200,000 nodes; increase mesh size")
    permutation = [0, 1, 3, 2, 4, 8, 6, 5, 7, 9]
    elements = []
    for element in result.Elements3D():
        points = [int(p.nr) for p in element.points]
        if len(points) != 10:
            raise ValueError("Expected quadratic tetrahedra")
        elements.append([points[i] for i in permutation])
    surfaces = {}
    for element in result.Elements2D():
        name = result.FaceDescriptor(element.index).bcname
        if any(region["name"] == name for region in request["regions"]):
            points = [int(p.nr) for p in element.points]
            surfaces.setdefault(name, []).append([points[i] for i in [0, 1, 2, 5, 3, 4]])
    for region in request["regions"]:
        if not surfaces.get(region["name"]):
            raise ValueError(f"No boundary mesh associated with {region['name']}")
    return {"nodes": nodes, "elements": elements, "surfaces": surfaces}


if __name__ == "__main__":
    request = json.loads(Path(sys.argv[1]).read_text())
    output = mesh(request)
    Path(sys.argv[2]).write_text(json.dumps(output))
