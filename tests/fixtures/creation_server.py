"""Production App/API, initially blank, with independent read-only STEP verification."""
import hashlib
import os
import tempfile

import uvicorn
from fastapi import HTTPException
from starlette.routing import Mount

from o3d.api import create_app
from o3d.model import Project
from o3d.store import Store


def creation_app(folder, project=None):
    store = Store(folder)
    store.init(project if project is not None else Project(name="New part"))
    app = create_app(folder, project_windows=False)

    @app.get("/api/test/creation/step")
    def independent_step(artifact: str):
        """Read the exported artifact, never the engine's reported measurements."""
        from OCP.STEPControl import STEPControl_Reader
        from OCP.BRepCheck import BRepCheck_Analyzer
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        from OCP.TopAbs import TopAbs_SOLID
        from OCP.TopExp import TopExp_Explorer

        path = store.artifact(artifact)
        if path.suffix.lower() not in {".step", ".stp"}:
            raise HTTPException(422, "Independent verification requires a STEP artifact")
        reader = STEPControl_Reader()
        if reader.ReadFile(str(path)) != 1:
            raise HTTPException(422, "The exported STEP could not be read independently")
        reader.TransferRoots()
        shape = reader.OneShape()
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        bounds = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, bounds, False, False)
        low, high = bounds.CornerMin(), bounds.CornerMax()
        center = props.CentreOfMass()
        explorer, solids, bodies = TopExp_Explorer(shape, TopAbs_SOLID), 0, []
        while explorer.More():
            solid = explorer.Current()
            solid_props = GProp_GProps()
            BRepGProp.VolumeProperties_s(solid, solid_props)
            solid_bounds = Bnd_Box()
            BRepBndLib.AddOptimal_s(solid, solid_bounds, False, False)
            first, last = solid_bounds.CornerMin(), solid_bounds.CornerMax()
            centroid = solid_props.CentreOfMass()
            bodies.append({"valid": BRepCheck_Analyzer(solid).IsValid(),
                           "volume_mm3": solid_props.Mass(),
                           "center_of_mass_mm": [centroid.X(), centroid.Y(), centroid.Z()],
                           "bounds": [first.X(), first.Y(), first.Z(), last.X(), last.Y(), last.Z()]})
            solids += 1
            explorer.Next()
        return {"valid": BRepCheck_Analyzer(shape).IsValid(), "solids": solids,
                "bodies": bodies,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "volume_mm3": props.Mass(), "center_of_mass_mm": [center.X(), center.Y(), center.Z()],
                "bounds": [low.X(), low.Y(), low.Z(), high.X(), high.Y(), high.Z()]}

    # Keep the fixture-only verifier before the production static catch-all.
    app.router.routes.sort(key=lambda route: isinstance(route, Mount))
    return app


if __name__ == "__main__":
    port = int(os.environ.get("O3D_TEST_PORT", "8974"))
    if port == 8787:
        raise RuntimeError("The user's live studio is not a browser fixture")
    with tempfile.TemporaryDirectory(prefix="oxygen-first-part-") as folder:
        uvicorn.run(creation_app(folder), host=os.environ.get("O3D_TEST_HOST", "127.0.0.1"), port=port)
