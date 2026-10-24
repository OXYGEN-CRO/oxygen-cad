"""Report exact STEP facts useful for a human-directed manufacturing review."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys


def facts(path):
    import build123d as bd
    part=bd.import_step(path);box=part.bounding_box()
    return {'file':str(Path(path).resolve()),'units':'mm','valid':bool(part.is_valid),'solids':len(part.solids()),
            'dimensions_mm':tuple(box.size),'volume_mm3':part.volume,'area_mm2':part.area,
            'face_types':dict(Counter(face.geom_type.name for face in part.faces())),
            'circular_edge_radii_mm':sorted({round(edge.radius,8) for edge in part.edges() if edge.geom_type==bd.GeomType.CIRCLE}),
            'unmeasured':['tool_access','minimum_wall','internal_corner_reach','sheet_unfolding','bend_allowance','tolerance_capability']}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('step',type=Path);a=p.parse_args()
    try:print(json.dumps(facts(a.step),indent=2,allow_nan=False));return 0
    except (ImportError,OSError,ValueError) as e:
        print(json.dumps({'error':str(e)}),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
