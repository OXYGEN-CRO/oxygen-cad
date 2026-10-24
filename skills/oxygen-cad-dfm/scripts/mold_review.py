"""Sample draft and pull visibility; these are candidate facts, not a moldability verdict."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys


def measure(path,axis,min_draft,samples=128):
    import numpy as np
    import trimesh
    helper=Path(__file__).resolve().parents[2]/'oxygen-cad-dfam/scripts/mesh_review.py'
    spec=importlib.util.spec_from_file_location('oxygen_mesh_review',helper);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    mesh=trimesh.load(path,force='mesh',process=False);mesh.merge_vertices()
    if not len(mesh.faces) or len(mesh.faces)>200000 or not np.isfinite(mesh.vertices).all():raise ValueError('Nonfinite, empty or oversized mesh')
    if not mesh.is_watertight or not mesh.is_winding_consistent or mesh.volume<=0:raise ValueError('Needs a consistently oriented closed solid mesh')
    up=np.eye(3)['xyz'.index(axis)];dot=mesh.face_normals@up
    draft=np.degrees(np.arcsin(np.clip(np.abs(dot),0,1)));side=np.abs(dot)<.70710678
    area=float(mesh.area_faces[side & (draft<min_draft)].sum())
    eps=max(float(np.linalg.norm(mesh.extents))*1e-7,1e-7);occluded=0
    indices=np.random.default_rng(0).choice(len(mesh.faces),min(samples,len(mesh.faces)),replace=False)
    for i in indices:
        direction=up if dot[i]>=0 else -up
        origin=mesh.triangles_center[i]+mesh.face_normals[i]*eps
        if len(m.distances(mesh.triangles,origin,direction,eps)):occluded+=1
    transverse=np.delete(mesh.vertices,'xyz'.index(axis),axis=1)
    return {'file':str(Path(path).resolve()),'units':'mm','pull_axis':axis,'draft_threshold_degrees':min_draft,
            'side_face_area_below_threshold_mm2':area,'sampled_faces':len(indices),'pull_occlusion_candidates':occluded,
            'projected_bounding_rectangle_mm2':float(np.prod(np.ptp(transverse,axis=0))),
            'limitations':['Planar triangle draft sampling; no exact curved-face minimum.',
                           'Pull occlusion indicates candidates, not verified undercuts or a tooling split.',
                           'Projected bounding rectangle is an envelope, not silhouette area or clamp-force sizing.',
                           'Parting line, side actions, resin shrinkage and mold fill are unmeasured.']}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mesh',type=Path);p.add_argument('--pull',choices=['x','y','z'],required=True);p.add_argument('--min-draft-deg',type=float,required=True);a=p.parse_args()
    try:
        if not 0<=a.min_draft_deg<45:raise ValueError('Draft angle must be finite and in [0,45)')
        print(json.dumps(measure(a.mesh,a.pull,a.min_draft_deg),indent=2,allow_nan=False));return 0
    except (ImportError,OSError,ValueError) as e:
        print(json.dumps({'error':str(e)}),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
