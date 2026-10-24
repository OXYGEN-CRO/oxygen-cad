"""Export a planar STEP cross-section or inspect a cut-profile DXF."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys


def inspect_dxf(source, tolerance=1e-5):
    import ezdxf
    from ezdxf import bbox
    doc=ezdxf.readfile(source);entities=list(doc.modelspace());counts=Counter(e.dxftype() for e in entities)
    points=Counter();seen=set();duplicates=0;unsupported=[];degenerate=[]
    def point(v): return tuple(round(float(v[i])/tolerance) for i in range(3))
    for entity in entities:
        kind=entity.dxftype();handle=entity.dxf.handle;signature=None
        if kind=='LINE':
            a,b=point(entity.dxf.start),point(entity.dxf.end)
            if a==b:degenerate.append(handle)
            else:points.update([a,b])
            signature=(kind,*sorted([a,b]))
        elif kind=='CIRCLE':
            if entity.dxf.radius<=tolerance:degenerate.append(handle)
            signature=(kind,point(entity.dxf.center),round(entity.dxf.radius/tolerance))
        elif kind=='ARC':
            a,b=point(entity.start_point),point(entity.end_point);points.update([a,b])
            signature=(kind,point(entity.dxf.center),round(entity.dxf.radius/tolerance),round(entity.dxf.start_angle,6),round(entity.dxf.end_angle,6))
        elif kind=='LWPOLYLINE':
            vertices=list(entity.get_points('xyb'));z=entity.dxf.elevation
            if len(vertices)<2:degenerate.append(handle)
            elif not entity.closed:points.update([point((*vertices[0][:2],z)),point((*vertices[-1][:2],z))])
            # Reversal with bulges is not normalized; closure is checked but full overlap is unmeasured.
            signature=(kind,entity.closed,tuple(tuple(round(v/tolerance) for v in row) for row in vertices),round(z/tolerance))
        else:unsupported.append({'handle':handle,'type':kind})
        if signature in seen:duplicates+=1
        if signature is not None:seen.add(signature)
    ext=bbox.extents(entities);dimensions=tuple(ext.size) if ext.has_data else None
    endpoints=[{'point_in_declared_units':[v*tolerance for v in key],'degree':degree} for key,degree in points.items() if degree!=2]
    units=doc.header.get('$INSUNITS',0)
    return {'file':str(Path(source).resolve()),'insunits':units,'units':'mm' if units==4 else 'in' if units==1 else 'unknown',
            'entity_counts':dict(counts),'dimensions_in_declared_units':dimensions,'unjoined_or_branched_endpoints':endpoints,
            'exact_duplicates':duplicates,'degenerate_entities':degenerate,'unsupported_entities':unsupported,
            'empty':not entities,'planarity_z_values':sorted({round(e.dxf.elevation,8) for e in entities if e.dxftype()=='LWPOLYLINE'}),
            'limitations':['No complete self-intersection, partial-overlap or minimum-web analysis.',
                           'All entities are inspected together; review layer intent before treating open engraving/bend lines as cut errors.',
                           'Endpoint coordinates are in declared drawing units; no unit conversion is performed.']}


def section(source,z,output):
    import build123d as bd
    if not math.isfinite(z):raise ValueError('z must be finite mm')
    if Path(output).suffix.lower()!='.dxf':raise ValueError('Output must end in .dxf')
    if Path(output).exists():raise ValueError('Output already exists')
    shape=bd.import_step(source)
    cut=bd.section(shape,section_by=bd.Plane.XY.offset(z)).moved(bd.Location((0,0,-z)))
    if not len(cut.edges()):raise ValueError('Section does not intersect the solid')
    exporter=bd.ExportDXF(unit=bd.Unit.MM);exporter.add_shape(cut,layer='CUT')
    Path(output).parent.mkdir(parents=True,exist_ok=True);exporter.write(output)
    result=inspect_dxf(output);result['section_z_mm']=z
    result['purpose']='Planar cross-section, not bent-sheet unfolding or tool compensation'
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    s=sub.add_parser('section');s.add_argument('step',type=Path);s.add_argument('--z',type=float,required=True);s.add_argument('--out',type=Path,required=True)
    i=sub.add_parser('inspect');i.add_argument('dxf',type=Path)
    a=p.parse_args()
    try:print(json.dumps(section(a.step,a.z,a.out) if a.action=='section' else inspect_dxf(a.dxf),indent=2,allow_nan=False))
    except (ImportError,OSError,ValueError) as e:
        print(json.dumps({'error':str(e)}),file=sys.stderr);return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
