"""Exact independent projection and drawing publication; loaded only by jobs."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import zipfile

from .drawings import Drawings, Document, Reference, data_for, read_step_pmi

ORIENTATIONS = {
    "front": ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "back": ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
    "top": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "bottom": ((1, 0, 0), (0, -1, 0), (0, 0, -1)),
    "right": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
    "left": ((0, -1, 0), (0, 0, 1), (-1, 0, 0)),
    "isometric": ((2**-.5, -2**-.5, 0), (6**-.5, 6**-.5, 2/6**.5), (-3**-.5, -3**-.5, 3**-.5)),
}
PAPER = {"A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "Letter": (279.4, 215.9), "B": (431.8, 279.4)}
GDT = {"position": "⌖", "flatness": "▱", "straightness": "—", "perpendicularity": "⊥", "parallelism": "∥",
       "circularity": "○", "cylindricity": "⌭", "profile": "⌒", "runout": "↗"}


def dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise RuntimeError("Drawing operation cancelled")


def load_geometry(store, project):
    import build123d as bd
    from ..geometry import build
    from ..model import geometry_key
    key = geometry_key(project)
    path = store.cache / "builds" / key / "build.json"
    if not path.exists():
        build(store, project)
    metadata = json.loads(path.read_text())
    artifact = metadata.get("artifacts", {}).get("brep")
    if not artifact or not store.artifact(artifact).is_file():
        raise ValueError("Drawing views require exact BRep geometry; mesh-only sources are unsupported")
    shape = bd.import_brep(store.artifact(artifact))
    scene_path = store.cache / "builds" / key / "native-scene.json"
    if not scene_path.exists():
        scene_path = store.cache / "builds" / key / "scene.json"
    scene = json.loads(scene_path.read_text()) if scene_path.exists() else {}
    bodies = scene.get("bodies", [])
    exact = list(shape.solids())
    if not bodies:
        bodies = [{"id": "part", "name": project.name, "geometry_kind": "solid"}]
    # Ordering is a transient per-build lookup, never a persisted topology reference.
    if len(bodies) != len(exact):
        if len(bodies) != 1:
            raise ValueError("Native scene/BRep body count mismatch; body associations require repair")
        exact = [shape]
    return shape, metadata, {b["id"]: (b, s) for b, s in zip(bodies, exact)}


def resolve(reference, project, shape):
    """No nearest-face or arbitrary index fallback is permitted."""
    import build123d as bd
    from ..geometry import resolve_face, select_edges, vector
    r = reference
    if r.kind == "point":
        return {"point": list(r.point), "association": "authored_point"}
    if r.kind == "parameter":
        if r.target not in project.parameters:
            raise ValueError(f"Missing parameter reference {r.target}")
        return {"point": list(r.point), "value_mm": project.parameters[r.target].value, "association": r.target}
    if r.kind == "hole":
        feature = next((f for f in project.features if f.id == r.target and f.type == "hole" and not f.suppressed), None)
        if feature is None or r.row >= feature.rows or r.column >= feature.columns:
            raise ValueError(f"Missing hole reference {r.target}[{r.row},{r.column}]; select a replacement")
        p = list(feature.center)
        other = [i for i in range(3) if i != "XYZ".index(feature.axis)]
        p[other[0]] += r.column * feature.spacing[0]
        p[other[1]] += r.row * feature.spacing[1]
        from OCP.gp import gp_Pnt
        transformed=gp_Pnt(*p).Transformed(bd.Location(project.placement.translation, project.placement.rotation).wrapped.Transformation())
        p=[transformed.X(),transformed.Y(),transformed.Z()]
        return {"point": p, "value_mm": project.resolve(feature.diameter), "radius_mm": project.resolve(feature.diameter)/2,
                "count": feature.rows*feature.columns, "association": r.target}
    geometry = resolve_face(shape, r.target) if r.kind == "face" else select_edges(shape, r.target)[0]
    p = geometry.center()
    if r.location != "center":
        box = geometry.bounding_box()
        p = box.min if r.location == "min" else box.max
    result = {"point": vector(p), "association": r.target}
    if geometry.geom_type == bd.GeomType.CIRCLE:
        result.update(radius_mm=geometry.radius, value_mm=geometry.radius*2)
    if r.kind == "face" and geometry.geom_type == bd.GeomType.CYLINDER:
        result.update(radius_mm=geometry.radius, value_mm=geometry.radius*2)
    return result


def edge_points(shape):
    import build123d as bd
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    if shape.IsNull():
        return []
    paths = []
    for edge in bd.Compound(shape).edges():
        curve = BRepAdaptor_Curve(edge.wrapped)
        sampling = GCPnts_QuasiUniformDeflection(curve, 0.02)
        if not sampling.IsDone():
            raise ValueError("Unable to discretize projected exact curve within 0.02 mm deflection")
        paths.append([[sampling.Value(i).X(), sampling.Value(i).Y(), sampling.Value(i).Z()] for i in range(1, sampling.NbPoints()+1)])
    return paths


def project_shape(shape, orientation, hidden=True, tangent=False):
    from OCP.HLRAlgo import HLRAlgo_Projector
    from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
    x, _, n = ORIENTATIONS[orientation]
    algo = HLRBRep_Algo()
    algo.Add(shape.wrapped)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*n), gp_Dir(*x))))
    algo.Update()
    algo.Hide()
    result = HLRBRep_HLRToShape(algo)
    paths = []
    names = [("VCompound", "visible"), ("OutLineVCompound", "visible")]
    if tangent:
        names.append(("Rg1LineVCompound", "tangent"))
    if hidden:
        names += [("HCompound", "hidden"), ("OutLineHCompound", "hidden")]
    for method, style in names:
        paths.extend({"points": [p[:2] for p in pts], "style": style} for pts in edge_points(getattr(result, method)()))
    return paths


def section_shape(shape, origin, normal):
    import build123d as bd
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace
    from OCP.gp import gp_Pln, gp_Pnt, gp_Dir
    plane = gp_Pln(gp_Pnt(*origin), gp_Dir(*normal))
    section = BRepAlgoAPI_Section(shape.wrapped, plane, False)
    section.Approximation(True)
    section.Build()
    if not section.IsDone():
        raise ValueError("Exact section operation failed")
    boundary = edge_points(section.Shape())
    half = BRepPrimAPI_MakeHalfSpace(BRepBuilderAPI_MakeFace(plane).Face(), gp_Pnt(*[a+b for a,b in zip(origin,normal)])).Solid()
    cut = shape.cut(bd.Solid(half))
    return cut, boundary


def hatch(boundary, axes, spacing=3):
    """Even/odd scanline hatch handles cavity loops without filling their holes."""
    segments = []
    x, y, _ = axes
    for path in boundary:
        points = [(dot(p,x), dot(p,y)) for p in path]
        segments.extend(zip(points, points[1:]))
    if not segments:
        return []
    values = [p[1]-p[0] for s in segments for p in s]
    result = []
    start = math.floor(min(values)/spacing)*spacing
    for i in range(min(1000, int((max(values)-start)/spacing)+1)):
        c = start+i*spacing
        intersections = []
        for (ax,ay),(bx,by) in segments:
            va, vb = ay-ax-c, by-bx-c
            if (va <= 0 < vb) or (vb <= 0 < va):
                t = va/(va-vb)
                intersections.append(ax+t*(bx-ax))
        intersections.sort()
        for a,b in zip(intersections[::2],intersections[1::2]):
            if b-a > 1e-7:
                result.append({"points": [[a,a+c],[b,b+c]], "style": "hatch"})
    return result


def clip_circle(paths, center, radius):
    result = []
    for path in paths:
        for a,b in zip(path["points"],path["points"][1:]):
            ax,ay = a[0]-center[0],a[1]-center[1]
            dx,dy = b[0]-a[0],b[1]-a[1]
            A, B, C = dx*dx+dy*dy, 2*(ax*dx+ay*dy), ax*ax+ay*ay-radius*radius
            if A < 1e-18:
                continue
            disc = B*B-4*A*C
            intervals = [0.,1.]
            if disc >= 0:
                intervals += [t for t in ((-B-math.sqrt(disc))/(2*A),(-B+math.sqrt(disc))/(2*A)) if 0 < t < 1]
            intervals.sort()
            for lo,hi in zip(intervals,intervals[1:]):
                mid=(lo+hi)/2
                if (ax+mid*dx)**2+(ay+mid*dy)**2 <= radius*radius+1e-8:
                    result.append({"style":path["style"],"points":[[a[0]+t*dx-center[0],a[1]+t*dy-center[1]] for t in (lo,hi)]})
    return result


def fit_limits(value, fit):
    # ISO 286-2 supported nominal bands; micrometres. Refuse extrapolation.
    if not 3 < value <= 30:
        raise ValueError("ISO H7/g6 fit lookup is supported only for nominal sizes >3 through 30 mm")
    h7,g6low,g6high = (12,-12,-4) if value <= 6 else (15,-14,-5) if value <= 10 else (18,-17,-6) if value <= 18 else (21,-20,-7)
    return {"H7": (0,h7/1000), "g6": (g6low/1000,g6high/1000), "H7/g6": (g6low/1000,h7/1000)}[fit]


def dimension_text(value, annotation, units):
    divisor = 25.4 if units == "inch" else 1
    p = annotation.precision
    fmt = lambda v: f"{v/divisor:.{p}f}"
    text = fmt(value)
    t = annotation.tolerance
    limits = None
    if t.kind == "symmetric":
        text += " ±"+fmt(t.upper_mm)
        limits = (value-t.upper_mm,value+t.upper_mm)
    elif t.kind in ("deviation","limits"):
        limits=(value+t.lower_mm,value+t.upper_mm)
        text = fmt(limits[1])+" / "+fmt(limits[0]) if t.kind == "limits" else text+" +"+fmt(t.upper_mm)+" / "+fmt(t.lower_mm)
    elif t.kind == "fit":
        lo,hi=fit_limits(value,t.fit)
        limits=(value+lo,value+hi)
        text += " "+t.fit
    if annotation.kind == "diameter": text="⌀"+text
    if annotation.kind == "radius": text="R"+text
    if annotation.basic: text="["+text+"]"
    if annotation.dual_units:
        other = value if units == "inch" else value/25.4
        text += f" [{other:.{p}f} {'mm' if units == 'inch' else 'in'}]"
    return text, limits


def bom_rows(store, project, bodies):
    # Native BOM has definition keys and includes suppressed/hidden distinctions.
    domain = getattr(project,"domains",{}).get("assemblies",{})
    rows = domain.get("bom", []) if isinstance(domain,dict) else []
    if getattr(project,"native_document",None):
        try:
            from .assemblies import bom_snapshot
        except ImportError:
            pass
        else:
            snapshot=bom_snapshot(store,project)
            rows=snapshot.get("rows",snapshot.get("items",[])) if isinstance(snapshot,dict) else snapshot
    if rows:
        return [{**r,"name":r.get("description") or r.get("part_number") or r.get("name") or r.get("key"),"quantity":r.get("count",r.get("quantity",1))} for r in rows]
    return [{"key":key,"name":body[0].get("name",key),"quantity":1,"body_ids":[key]} for key,body in bodies.items()]


def layout(store, project, doc, shape, bodies, cancel=None):
    from ..model import geometry_key
    key=geometry_key(project)
    if doc.source_geometry_key != key:
        raise ValueError(f"Drawing {doc.id} is stale: source geometry changed; refresh associations before publication")
    if doc.source_revision > project.revision:
        raise ValueError("Drawing source revision is newer than the project")
    datum_ids={a.datum for s in doc.sheets for a in s.annotations if a.kind=="datum"}
    pages=[]
    bom=bom_rows(store,project,bodies)
    for si,sheet in enumerate(doc.sheets):
        check_cancel(cancel)
        w,h=PAPER.get(sheet.size,(sheet.width_mm,sheet.height_mm))
        page={"id":sheet.id,"width_mm":w,"height_mm":h,"paths":[],"texts":[],"circles":[],"measurements":[],"views":[],"bom":bom}
        def line(points,style="visible"):
            page["paths"].append({"points":points,"style":style})
        def text(p,value,size=3):
            page["texts"].append({"position":list(p),"text":str(value),"height":size})
        line([[8,8],[w-8,8],[w-8,h-8],[8,h-8],[8,8]])
        line([[w-168,h-38],[w-8,h-38]])
        line([[w-168,h-38],[w-168,h-8]])
        text((w-164,h-31),doc.title,4)
        text((w-164,h-25),f"{doc.number}  |  {doc.standard}  |  {doc.projection.upper()} ANGLE  |  {doc.units}")
        text((w-164,h-19),f"SOURCE r{doc.source_revision}  ISSUED r{project.revision}  SHEET {si+1}/{len(doc.sheets)}",2.5)
        text((w-164,h-13),"REV "+(doc.revisions[-1].code if doc.revisions else "—")+"  "+"  ".join(f"{k}: {v}" for k,v in doc.title_block.items()),2.5)
        contexts={}
        for view in sheet.views:
            check_cancel(cancel)
            orientation=view.orientation
            position=list(view.position)
            parent=contexts.get(view.parent)
            scale=view.scale
            source=shape
            if view.bodies:
                import build123d as bd
                missing=set(view.bodies)-set(bodies)
                if missing: raise ValueError(f"Missing drawing bodies: {sorted(missing)}")
                source=bd.Compound(children=[bodies[k][1] for k in view.bodies])
            if view.kind in ("section","detail"):
                orientation=parent["orientation"]
                source=parent["shape"]
            if view.kind=="projected":
                sign=1 if doc.projection=="third" else -1
                if orientation in ("top","bottom"):
                    position[0]=parent["position"][0]
                    position[1]=parent["position"][1]+(-1 if orientation=="top" else 1)*sign*abs(position[1]-parent["position"][1])
                elif orientation in ("right","left"):
                    position[1]=parent["position"][1]
                    position[0]=parent["position"][0]+(1 if orientation=="right" else -1)*sign*abs(position[0]-parent["position"][0])
                scale=parent["scale"]
            boundary=[]
            if view.kind=="section":
                excluded=set(view.exclude_bodies)
                if excluded-set(bodies): raise ValueError("Section exclusion references missing body")
                if excluded:
                    import build123d as bd
                    cut_bodies=[]
                    for body_id,(_,body) in bodies.items():
                        if view.bodies and body_id not in view.bodies: continue
                        if body_id in excluded: cut_bodies.append(body)
                        else:
                            cut,edges=section_shape(body,view.section_origin,view.section_normal)
                            cut_bodies.append(cut);boundary+=edges
                    source=bd.Compound(children=cut_bodies)
                else: source,boundary=section_shape(source,view.section_origin,view.section_normal)
            paths=project_shape(source,orientation,view.hidden,view.tangent)
            paths+=hatch(boundary,ORIENTATIONS[orientation])
            center=(0,0)
            if view.kind=="detail":
                center=view.detail_center
                paths=clip_circle(paths,center,view.detail_radius)
                page["circles"].append({"center":position,"radius":view.detail_radius*scale,"style":"visible"})
            convert=lambda p:[position[0]+p[0]*scale,position[1]-p[1]*scale]
            for path in paths: line([convert(p) for p in path["points"]],path["style"])
            ctx={"orientation":orientation,"position":position,"scale":scale,"shape":source,"center":center}
            contexts[view.id]=ctx
            page["views"].append({"id":view.id,"orientation":orientation,"scale":scale,"position":position,"kind":view.kind,"parent":view.parent,"edge_paths":len(paths)})
            label=view.label or ("SECTION "+view.id if view.kind=="section" else "DETAIL "+view.id if view.kind=="detail" else orientation.upper())
            text((position[0],position[1]+25),f"{label}  SCALE {scale:g}:1",2.5)
            if view.kind=="section":
                pctx=parent
                px,py,_=ORIENTATIONS[pctx["orientation"]]
                plane=view.section_origin
                anchor=[pctx["position"][0]+dot(plane,px)*pctx["scale"],pctx["position"][1]-dot(plane,py)*pctx["scale"]]
                line([[anchor[0]-20,anchor[1]],[anchor[0]+20,anchor[1]]],"center")
                text((anchor[0]-22,anchor[1]-2),view.label or view.id,2.5)
        for a in sheet.annotations:
            ctx=contexts[a.view]
            x,y,_=ORIENTATIONS[ctx["orientation"]]
            refs=[resolve(r,project,shape) for r in a.references]
            def sheetpoint(r):
                return [ctx["position"][0]+(dot(r["point"],x)-ctx["center"][0])*ctx["scale"],ctx["position"][1]-(dot(r["point"],y)-ctx["center"][1])*ctx["scale"]]
            points=[sheetpoint(r) for r in refs]
            pos=[ctx["position"][0]+a.position[0],ctx["position"][1]+a.position[1]]
            value=None; limits=None
            if a.kind in ("linear","aligned"):
                p,q=[(dot(r["point"],x),dot(r["point"],y)) for r in refs[:2]]
                value=abs(p[0]-q[0]) if a.kind=="linear" and a.axis=="x" else abs(p[1]-q[1]) if a.kind=="linear" else math.dist(p,q)
                if value<1e-9: raise ValueError(f"Dimension {a.id} has coincident projected references")
                label,limits=dimension_text(value,a,doc.units)
                for point in points[:2]: line([point,pos],"dimension")
                line([points[0],points[1]],"dimension")
            elif a.kind=="angular":
                p,c,q=[(dot(r["point"],x),dot(r["point"],y)) for r in refs[:3]]
                u,v=[p[i]-c[i] for i in (0,1)],[q[i]-c[i] for i in (0,1)]
                if math.hypot(*u)*math.hypot(*v)<1e-12: raise ValueError("Angular references are coincident")
                value=math.degrees(math.acos(max(-1,min(1,dot(u,v)/(math.hypot(*u)*math.hypot(*v))))))
                label=f"{value:.{a.precision}f}°"
                line([points[0],points[1],points[2]],"dimension")
            elif a.kind in ("diameter","radius"):
                if "radius_mm" not in refs[0]: raise ValueError(f"{a.id}: radial dimension requires a circular edge, cylindrical face or hole")
                value=refs[0]["radius_mm"]*(2 if a.kind=="diameter" else 1)
                label,limits=dimension_text(value,a,doc.units)
                line([points[0],pos],"dimension")
            elif a.kind=="center":
                p=points[0]
                line([[p[0]-4,p[1]],[p[0]+4,p[1]]],"center");line([[p[0],p[1]-4],[p[0],p[1]+4]],"center")
                continue
            elif a.kind=="centerline":
                line(points[:2],"center");continue
            elif a.kind=="hole_note":
                if "value_mm" not in refs[0]:raise ValueError("Hole note requires a measured hole or circular geometry")
                label=f"{refs[0].get('count',1)}X ⌀{refs[0]['value_mm']/(25.4 if doc.units=='inch' else 1):.{a.precision}f} "+a.text
            elif a.kind=="datum": label="["+a.datum+"]"
            elif a.kind=="gdt":
                missing=set(a.datums)-datum_ids
                if missing:raise ValueError(f"Undefined datum references: {sorted(missing)}")
                modifier={"RFS":"","MMC":" Ⓜ","LMC":" Ⓛ"}[a.material_condition]
                label=" | ".join([GDT[a.characteristic],f"{a.tolerance_mm/(25.4 if doc.units=='inch' else 1):g}"+modifier,*a.datums])
                width=max(12,len(label)*1.8)
                line([[pos[0]-1,pos[1]-4],[pos[0]+width,pos[1]-4],[pos[0]+width,pos[1]+1],[pos[0]-1,pos[1]+1],[pos[0]-1,pos[1]-4]],"dimension")
            elif a.kind=="balloon":
                match=[(i+1,r) for i,r in enumerate(bom) if r["key"]==a.item]
                if len(match)!=1:raise ValueError(f"Balloon {a.id} has missing or ambiguous BOM key {a.item}")
                label=str(match[0][0]);page["circles"].append({"center":[pos[0]+1,pos[1]-1],"radius":3,"style":"dimension"})
            elif a.kind=="surface": label="⌯ "+a.text
            elif a.kind=="weld": label="△ "+a.text
            elif a.kind=="edge": label="EDGE "+a.text
            else:label=a.text
            if points and a.kind not in ("linear","aligned","angular","diameter","radius"):
                line([points[0],pos],"dimension")
            text(pos,label)
            page["measurements"].append({"id":a.id,"kind":a.kind,"value_mm":value if a.kind!="angular" else None,"value_degrees":value if a.kind=="angular" else None,"limits_mm":limits,"text":label,"references":[r["association"] for r in refs]})
        for table in sheet.tables:
            rows=table.rows
            columns=table.columns
            if table.kind=="parts":
                columns=["ITEM","PART","QTY"];rows=[[str(i+1),r["name"],str(r["quantity"])] for i,r in enumerate(bom)]
            elif table.kind=="revision":
                columns=["REV","DATE","DESCRIPTION","BY"];rows=[[r.code,r.date,r.description,r.author] for r in doc.revisions]
            elif table.kind=="holes":
                columns=["HOLE","X ("+doc.units+")","Y ("+doc.units+")","DIAMETER"]
                if table.view is None:raise ValueError("Hole table requires an attached view")
                x,y,_=ORIENTATIONS[contexts[table.view]["orientation"]]
                rows=[]
                for i,r in enumerate(table.references):
                    resolved=resolve(r,project,shape)
                    if "value_mm" not in resolved:raise ValueError("Hole table needs circular references")
                    d=25.4 if doc.units=="inch" else 1
                    rows.append([str(i+1),f"{dot(resolved['point'],x)/d:.3f}",f"{dot(resolved['point'],y)/d:.3f}",f"{resolved['value_mm']/d:.3f}"])
            cell=35
            for ri,row in enumerate([columns,*rows]):
                for ci,value in enumerate(row):text((table.position[0]+ci*cell+1,table.position[1]+ri*6+4),value,2.5)
                line([[table.position[0],table.position[1]+ri*6],[table.position[0]+len(columns)*cell,table.position[1]+ri*6]],"dimension")
        # Publication rejects clipped geometry/annotations, including any explicit sheet placement.
        for path in page["paths"]:
            if any(not (-.1 <= p[0] <= w+.1 and -.1 <= p[1] <= h+.1) for p in path["points"]):
                raise ValueError(f"Sheet {sheet.id} has geometry outside its paper boundary; reposition or rescale the view")
        pages.append(page)
    return pages


def execute(store, project, operation, payload, cancel=None):
    from ..model import geometry_key
    from ..store import write_json
    data=data_for(project)
    key=geometry_key(project)
    if operation=="template":
        template=next((t for t in data.templates if t.id==payload.get("template")),None)
        if template is None:raise ValueError("Unknown drawing template")
        doc=Document(id=payload.get("id","drawing1"),title=payload.get("title",project.name),source_revision=project.revision,
                     source_geometry_key=key,standard=template.standard,units=template.units,projection=template.projection,
                     template=template.id,title_block=template.title_block,sheets=[template.sheet.model_copy(deep=True)])
        return {"document":doc.model_dump(mode="json")}
    if operation=="import_pmi":
        ref=next((r for r in project.references if r.id==payload.get("reference")),None)
        if ref is None:raise ValueError("Select an immutable imported STEP reference")
        path=(store.root/ref.file).resolve()
        if not path.is_relative_to(store.root.resolve()):raise ValueError("STEP source escapes project")
        content=path.read_bytes()
        if hashlib.sha256(content).hexdigest()!=ref.sha256:raise ValueError("Imported STEP hash mismatch")
        result=read_step_pmi(content.decode("utf-8",errors="replace"))
        result.update(source_sha256=ref.sha256,source_reference=ref.id,source_revision=project.revision)
        artifact=f"drawings/pmi/{ref.sha256}.json"
        write_json(store.cache/artifact,result)
        return result|{"artifact":artifact}
    if operation=="reopen":
        # Client submits extracted document.json; paths are never read from payload.
        return {"data":Drawings.model_validate(payload.get("data",{})).model_dump(mode="json")}
    if operation not in ("inspect","refresh","preview","publish","export_mbd","inspect_pmi"):
        raise ValueError(f"Unknown drawing operation {operation}")
    check_cancel(cancel)
    shape,metadata,bodies=load_geometry(store,project)
    if operation in ("inspect_pmi","export_mbd"):
        records=[]
        for pmi in data.pmi:
            if pmi.source_geometry_key!=key:raise ValueError(f"3D annotation {pmi.id} is stale")
            resolved=resolve(pmi.reference,project,shape)
            record=pmi.model_dump(mode="json")|{"resolved":resolved}
            if pmi.kind=="size":
                if "value_mm" not in resolved:raise ValueError("3D size tolerance requires a measured parameter/circle/hole")
                record["inspection"]={"nominal_mm":resolved["value_mm"],"lower_mm":resolved["value_mm"]+pmi.tolerance.lower_mm,"upper_mm":resolved["value_mm"]+pmi.tolerance.upper_mm}
            records.append(record)
        result={"schema_version":1,"format":"Oxygen semantic MBD JSON","source_revision":project.revision,"source_geometry_key":key,"annotations":records,"omissions":["STEP AP242 semantic PMI export is unsupported."]}
        if operation=="export_mbd":
            artifact=f"drawings/mbd/{project.revision}-{key}/annotations.o3d-mbd.json"
            write_json(store.cache/artifact,result);result["artifact"]=artifact
        return result
    selected=payload.get("document")
    docs=[d for d in data.documents if selected is None or d.id==selected]
    if not docs:raise ValueError("No drawing document selected")
    if operation in ("inspect","refresh"):
        states=[]
        refreshed=data.model_copy(deep=True)
        for doc in docs:
            failures=[]
            for sheet in doc.sheets:
                for a in sheet.annotations:
                    for r in a.references:
                        try:resolve(r,project,shape)
                        except ValueError as e:failures.append({"annotation":a.id,"reference":r.model_dump(mode="json"),"message":str(e)})
            states.append({"document":doc.id,"state":"unresolved" if failures else "stale" if doc.source_geometry_key!=key else "current","source_revision":doc.source_revision,"source_geometry_key":doc.source_geometry_key,"issues":failures})
            if operation=="refresh" and not failures:
                item=next(d for d in refreshed.documents if d.id==doc.id)
                item.source_revision=project.revision;item.source_geometry_key=key
        return {"documents":states,"source_revision":project.revision,"source_geometry_key":key,**({"data":refreshed.model_dump(mode="json")} if operation=="refresh" else {})}
    from .drawing_vectors import svg, write_pdf, write_dxf
    outputs=[]
    for doc in docs:
        pages=layout(store,project,doc,shape,bodies,cancel)
        identity=hashlib.sha256(json.dumps(doc.model_dump(mode="json"),sort_keys=True).encode()).hexdigest()[:20]
        base=f"drawings/{doc.id}/{project.revision}-{identity}"
        folder=store.cache/base;folder.mkdir(parents=True,exist_ok=True)
        artifacts=[]
        for page in pages:
            check_cancel(cancel)
            (folder/f"{page['id']}.svg").write_text(svg(page))
            write_dxf(folder/f"{page['id']}.dxf",page)
            artifacts.append({"sheet":page["id"],"svg":f"{base}/{page['id']}.svg","dxf":f"{base}/{page['id']}.dxf"})
        result={"document":doc.id,"revision":project.revision,"source_revision":doc.source_revision,"source_geometry_key":key,
                "association_state":"current","artifacts":artifacts,"pages":pages,"curve_deflection_mm":.02}
        write_json(folder/"document.json",{"schema_version":1,"documents":[doc.model_dump(mode="json")],"templates":[t.model_dump(mode="json") for t in data.templates if t.id==doc.template],"pmi":[]})
        if operation=="publish":
            write_pdf(folder/"drawing.pdf",pages)
            result["pdf"]=f"{base}/drawing.pdf"
            write_json(folder/"manifest.json",{k:v for k,v in result.items() if k!="pages"})
            with zipfile.ZipFile(folder/"drawing.zip","w",zipfile.ZIP_DEFLATED) as archive:
                for file in sorted(folder.iterdir()):
                    if file.name!="drawing.zip":archive.write(file,file.name)
            result["artifact"]=f"{base}/drawing.zip"
        outputs.append(result)
    return {"documents":outputs,"revision":project.revision,"source_geometry_key":key}
