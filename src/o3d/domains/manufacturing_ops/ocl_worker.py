"""Isolated OpenCAMLib process. Only our bounded JSON protocol is executed.

The host selects this Python interpreter; project data never supplies executable
paths. Debian python3-opencamlib supplies CPython-compatible native bindings.
"""
import json
import sys
from pathlib import Path


def main():
    from opencamlib import ocl
    request = json.loads(Path(sys.argv[1]).read_text())
    surface = ocl.STLSurf()
    for triangle in request["triangles"]:
        surface.addTriangle(ocl.Triangle(*(ocl.Point(*point) for point in triangle)))
    cutter = ocl.BallCutter(request["diameter_mm"], request["flute_length_mm"])
    batch = ocl.BatchDropCutter()
    batch.setThreads(2)
    batch.setSTL(surface)
    batch.setCutter(cutter)
    for x, y in request["xy"]:
        batch.appendPoint(ocl.CLPoint(x, y, request["floor_z"]))
    batch.run()
    result = {"points": [[p.x, p.y, p.z] for p in batch.getCLPoints()],
              "engine": "OpenCAMLib", "algorithm": "BatchDropCutter / BallCutter", "version": ocl.version(),
              "binding_sha256": __import__("hashlib").sha256(Path(ocl.__file__).read_bytes()).hexdigest()}
    Path(sys.argv[2]).write_text(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
