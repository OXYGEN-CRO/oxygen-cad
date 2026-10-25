"""Versioned multi-study simulation domain and revision-bound artifact execution."""
from __future__ import annotations
import copy
import hashlib
import html
import json
import re
import sys
import zipfile
import secrets
from ..simulation_provenance import software_fingerprint
from ..simulation_worker import ADAPTER_VERSION
from pathlib import Path
from ..model import digest,geometry_key
from ..simulation_models import SimulationDomain,StudyRecord,AnalysisModel,MeshRecord,Region
from ..store import now,write_json

CAPABILITIES=[
    ("SIM-01","Linear static stress","experimental","C3D4 isotropic multi-body/multi-case linear elasticity; mesh refinement and equation/equilibrium checks"),
    ("SIM-02","Loads and supports","experimental","Planar traction/pressure, conservative moments/remote forces, gravity, bearing-direction traction and hydrostatic pressure; fixed/partial/frictionless/cylindrical pin"),
    ("SIM-03","Inertial relief","experimental","Free connected isotropic static solid; explicit translation/angular acceleration and six rigid gauge constraints"),
    ("SIM-04","Assembly contact","experimental","Sourced paired-node elastic bonded and frictionless separation penalties; gap/active-set checks; no general sliding/friction/self-contact"),
    ("SIM-05","Bolts/connectors","experimental","Axial elastic truss connector with sourced preload/stiffness; no thread/washer/bending/torque-derived preload"),
    ("SIM-06","Nonlinear static","experimental","CalculiX C3D4 finite deformation and supplied J2 isotropic-hardening curves; dead loads and axis supports; no translated contact/bolts"),
    ("SIM-07","Modal","experimental","Lumped mass eigenpairs, rigid modes, participation, residual and mass orthogonality; bonded elastic interfaces; no preload transfer"),
    ("SIM-08","Structural buckling","experimental","Initial-stress generalized eigenproblem with explicit sign/load-factor convention; no imperfect collapse/postbuckling"),
    ("SIM-09","Steady thermal","experimental","Solid conduction, heat power/flux, convection, nonlinear radiation, thermal pair conductance and energy balance"),
    ("SIM-10","Thermal stress","experimental","Sequential same-mesh temperature/expansion transfer with stress-free reference temperature"),
    ("SIM-11","Quasi-static Event","experimental","Small-strain explicit transient subset; timestep sensitivity, energy and final kinetic/internal ratio; no large-motion event parity"),
    ("SIM-12","Dynamic Event","experimental","Small-strain lumped-mass central difference, piecewise-linear load histories, initial velocity and frictionless paired contact; no deletion/damage"),
    ("SIM-13","Electronics cooling","experimental","Prescribed-film solid/power thermal subset; no air domain/fan/flow/CHT adapter"),
    ("SIM-14","Shape/topology optimization","experimental","Filtered SIMP compliance/volume, preserve/obstacle cells and thresholded-solid reanalysis; no editable CAD conversion"),
    ("SIM-15","Generative design","experimental","Evaluated unrestricted SIMP/material alternatives and post-search displacement/mass/modal/buckling limit checks; no manufacturing-specific/global optimum claim"),
    ("SIM-16","General CFD","unavailable","Outside ordinary Fusion native scope; no integrated flow adapter"),
    ("SIM-17","Fatigue, harmonic/random response, transient heat transfer","unavailable","Additional engineering scope; calibrated histories/models and dedicated adapters not integrated"),
    ("SIM-18","Study models, comparisons and reports","implemented","Independent versioned models/materials/studies/cases, clone, exact snapshots, stale detection, comparisons, HTML reports and downloadable fields"),
    ("SIM-19","Analysis handoff","experimental","Versioned neutral study package and restricted CalculiX deck with explicit transfer-loss report; no Ansys .sdz authoring"),
    ("SIM-20","Injection molding/process analysis","unavailable","No polymer filling/packing/warpage solver or calibrated material/process adapter"),
]


def validate(data):
    return SimulationDomain.model_validate(data).model_dump(mode="json")


def _domain(project,payload):
    data=payload.get("data",getattr(project,"domains",{}).get("simulation",{"schema_version":1}))
    return SimulationDomain.model_validate(data)


def _study(domain,study_id):
    study_id=study_id or domain.active_study_id
    study=next((s for s in domain.studies if s.id==study_id),None)
    if study is None:raise ValueError("Select an existing simulation study")
    return study,next(m for m in domain.models if m.id==study.model_id)


def input_key(project,domain,study):
    model=next(m for m in domain.models if m.id==study.model_id)
    material_ids={a.material_id for a in study.assignments}|set(study.search.candidate_material_ids if study.search else [])
    return digest({"adapter":ADAPTER_VERSION,"software":software_fingerprint(study.type),"geometry":geometry_key(project),"native_document":getattr(project,"native_document",None),"model":model.model_dump(mode="json"),"study":study.model_dump(mode="json"),"materials":[m.model_dump(mode="json") for m in domain.materials if m.id in material_ids]})


def preflight(project,domain,study,model):
    errors=[]
    current=geometry_key(project)
    if model.geometry_key!=current:errors.append("Analysis model belongs to different geometry; rebuild/remap explicitly")
    if not study.assumptions_confirmed:errors.append("Review and confirm the study assumptions")
    if not study.cases:errors.append("Add at least one explicit load case")
    if not model.mesh:errors.append("Generate or supply a volume mesh")
    for mesh in [model.mesh,*model.refinements]:
        if mesh and mesh.geometry_key!=current:errors.append(f"Mesh {mesh.id} belongs to stale geometry")
        if mesh and {e.body_id for e in mesh.elements}!=set(model.body_ids):errors.append("Mesh body identifiers do not match the analysis model")
    if study.type in {"general_cfd","fatigue","harmonic","transient_thermal","injection_molding"}:errors.append(f"No integrated {study.type} adapter; study is unavailable")
    if study.type=="nonlinear_static" and not study.settings.large_deformation and not any(m.plastic_curve for m in domain.materials if m.id in {a.material_id for a in study.assignments}):errors.append("Nonlinear study requires large deformation or a sourced plastic constitutive curve")
    return errors


def _last_path(store,study_id):
    # IDs are human strings; use a digest for cache path construction.
    return store.cache/"domains"/"simulation"/"latest"/(digest(study_id)+".json")


def _latest(store,project,domain,study):
    path=_last_path(store,study.id)
    if not path.exists():return None
    result=json.loads(path.read_text())
    result["stale"]=result["input_key"]!=input_key(project,domain,study)
    return result


def _mesh(store,project,domain,study,model,payload,cancel):
    from ..geometry import build
    from ..solver import run_process,mesher_python
    gkey=geometry_key(project)
    built=build(store,project)
    scene=built.get("scene") or {}
    bodies=scene.get("bodies",[])
    if bodies:
        selected=[b for b in bodies if b["id"] in model.body_ids]
        if len(selected)!=len(model.body_ids):raise ValueError("Analysis body IDs no longer exist in native geometry; repair explicitly")
        if any(b["geometry_kind"]!="solid" for b in selected):raise ValueError("This adapter requires volume solids; surfaces and display meshes cannot be tetrahedralized as solids")
        parts=[{"body_id":b["id"],"step":str(store.artifact(b["artifacts"]["step"]))} for b in selected]
        faces=[{**f,"body_id":b["id"]} for b in selected for f in b["faces"]]
    else:
        if model.body_ids!=["part"]:raise ValueError("Legacy geometry build has no authoritative multi-body sidecar")
        parts=[{"body_id":"part","step":str(store.artifact(built["artifacts"]["step"]))}]
        faces=[{**f,"body_id":"part"} for f in built["faces"]]
    required={item.region_id for case in study.cases for item in [*case.supports,*case.loads,*case.thermal] if item.region_id}|{r.id for r in model.regions}
    region_requests={r.id:r.model_dump(mode="json") for r in model.regions}
    region_requests.update({r["id"]:r for r in payload.get("regions",[])})
    regions=[]
    for name in sorted(required):
        reference=region_requests.get(name,{"id":name,"selection":name})
        selection=reference.get("selection",name)
        matches=[f for f in faces if (not reference.get("body_id") or f["body_id"]==reference["body_id"]) and (selection==f["id"] or selection in f.get("aliases",[]))]
        if len(matches)!=1:raise ValueError(f"Missing or ambiguous region {name}; select body and face explicitly")
        face=matches[0]
        if face.get("kind") not in {"PLANE","plane"}:raise ValueError("Automatic region transfer currently supports planar faces; supply a verified mesh for curved regions")
        regions.append(Region(id=name,selection=selection,body_id=face["body_id"],center_mm=face["center"],area_mm2=face["area_mm2"],normal=face.get("normal")).model_dump(mode="json"))
    size=float(payload.get("mesh_size_mm",4))
    steps=int(payload.get("refinements",2))
    if not 0<size<100000 or not 1<=steps<=4:raise ValueError("Mesh size must be positive and refinement count 1..4")
    signature=digest({"geometry":gkey,"bodies":model.body_ids,"regions":regions,"size":size,"steps":steps})
    destination=store.cache/"domains"/"simulation"/"meshes"/signature/secrets.token_hex(8)
    destination.mkdir(parents=True,exist_ok=True)
    meshes=[]
    for i in range(steps):
        request={"id":f"mesh-{i}","geometry_key":gkey,"parts":parts,"regions":regions,"mesh_size_mm":size/1.5**i}
        write_json(destination/f"request-{i}.json",request)
        run_process([mesher_python(),Path(__file__).resolve().parents[1]/"simulation_mesh_worker.py",destination/f"request-{i}.json",destination/f"mesh-{i}.json"],destination,destination/f"netgen-{i}.log",cancel,timeout=180)
        mesh=MeshRecord.model_validate_json((destination/f"mesh-{i}.json").read_text())
        # Reorient exterior triangles by their owning solid tetrahedron.
        from ..simulation_mesh import exterior_triangles
        oriented={tuple(sorted(t)):t for t in exterior_triangles(mesh.nodes,mesh.elements)}
        for region in mesh.regions:region.triangles=[oriented[tuple(sorted(t))] for t in region.triangles]
        meshes.append(mesh)
    model.geometry_key=gkey;model.mesh=meshes[0];model.refinements=meshes[1:]
    return {"data":domain.model_dump(mode="json"),"changed":True,"mesh_summary":[{"id":m.id,"nodes":len(m.nodes),"elements":len(m.elements),"mesh_size_mm":m.mesh_size_mm} for m in meshes]}


def _run(store,project,domain,study,model,payload,cancel):
    from ..solver import run_process
    errors=preflight(project,domain,study,model)
    if errors:raise ValueError("; ".join(errors))
    key=input_key(project,domain,study)
    run_id=secrets.token_hex(8)
    destination=store.cache/"domains"/"simulation"/"runs"/key/run_id
    destination.mkdir(parents=True,exist_ok=True)
    snapshot={"data":domain.model_dump(mode="json"),"study_id":study.id,"input_key":key,"geometry_key":geometry_key(project),"revision":project.revision,"software":software_fingerprint(study.type),"run_id":run_id}
    write_json(destination/"inputs.json",snapshot)
    cases=[c for c in study.cases if not payload.get("case_id") or c.id==payload["case_id"]]
    if not cases:raise ValueError("Load case does not exist")
    results=[]
    for case in cases:
        case_dir=destination/digest(case.id)
        case_dir.mkdir(exist_ok=True)
        request=snapshot|{"case_id":case.id}
        write_json(case_dir/"request.json",request)
        run_process([sys.executable,"-m","o3d.simulation_worker",case_dir/"request.json",case_dir/"result.json"],case_dir,case_dir/"worker.log",cancel,timeout=900)
        result=json.loads((case_dir/"result.json").read_text())
        fields=result.pop("field")
        write_json(case_dir/"field.json",fields)
        result["field_artifact"]=str((case_dir/"field.json").relative_to(store.cache))
        result["input_artifact"]=str((destination/"inputs.json").relative_to(store.cache))
        result["completed_at"]=now()
        # Full normalized output and raw logs are retained, never only a completion badge.
        result["artifact"]=str((case_dir/"result.json").relative_to(store.cache))
        write_json(case_dir/"result.json",result)
        results.append(result)
    aggregate={"schema_version":1,"study_id":study.id,"input_key":key,"run_id":run_id,"revision":project.revision,"geometry_key":geometry_key(project),"results":results,"completed_at":now(),"stale":False}
    # Original snapshot remains inspectable if geometry changed while the worker ran.
    try:
        current=store.load()
        current_domain=SimulationDomain.model_validate(getattr(current,"domains",{}).get("simulation",domain.model_dump(mode="json")))
        current_study,_=_study(current_domain,study.id)
        aggregate["stale"]=input_key(current,current_domain,current_study)!=key
    except (ValueError,FileNotFoundError):aggregate["stale"]=True
    write_json(destination/"result.json",aggregate)
    write_json(_last_path(store,study.id),aggregate)
    return aggregate


def _report(store,result):
    key=digest(result)
    destination=store.cache/"domains"/"simulation"/"reports"/key
    destination.mkdir(parents=True,exist_ok=True)
    rows=[]
    for case in result["results"]:
        rows.append(f"<h2>{html.escape(case['case_id'])}</h2><p>{html.escape(case['numerical_state'])}; requirements: {html.escape(case['requirements_state'])}</p>")
        rows.append("<pre>"+html.escape(json.dumps({k:case[k] for k in ("values","checks","verification_gaps","findings","warnings","software","physical_validation")},indent=2))+"</pre>")
    document="<!doctype html><html lang='en'><meta charset='utf-8'><title>Oxygen simulation evidence</title><style>body{max-width:1000px;margin:2rem auto;font:16px system-ui;padding:1rem}pre{white-space:pre-wrap;background:#eee;padding:1rem}h1,h2{line-height:1.2}</style><h1>Simulation evidence</h1>"+f"<p>Study {html.escape(result['study_id'])}; revision {result['revision']}; stale: {result['stale']}</p><p>Input digest: {html.escape(result['input_key'])}</p>"+"".join(rows)+"</html>"
    path=destination/"report.html";path.write_text(document)
    return {"artifact":str(path.relative_to(store.cache)),"input_key":result["input_key"],"stale":result["stale"]}


def execute(store,project,operation,payload,cancel=None):
    domain=_domain(project,payload)
    if operation in {"inspect","capabilities","validate"}:
        studies=[]
        for study in domain.studies:
            model=next(m for m in domain.models if m.id==study.model_id)
            studies.append({"id":study.id,"name":study.name,"type":study.type,"input_key":input_key(project,domain,study),"errors":preflight(project,domain,study,model),"last_result":_latest(store,project,domain,study)})
        return {"schema_version":1,"capabilities":[{"id":i,"family":f,"status":s,"scope":sc} for i,f,s,sc in CAPABILITIES],"studies":studies,"units":"mm, N, s, tonne, MPa, K, W"}
    study,model=_study(domain,payload.get("study_id"))
    if operation=="clone_study":
        identifier=str(payload.get("new_id",""))
        if not identifier or any(s.id==identifier for s in domain.studies):raise ValueError("Supply a new unique study identifier")
        cloned=study.model_copy(deep=True);cloned.id=identifier;cloned.name=str(payload.get("name",study.name+" copy"))
        domain.studies.append(cloned);domain.active_study_id=cloned.id
        return {"data":validate(domain.model_dump(mode="json")),"changed":True}
    if operation=="clone_model":
        identifier=str(payload.get("new_id",""))
        if not identifier or any(m.id==identifier for m in domain.models):raise ValueError("Supply a new unique model identifier")
        cloned=model.model_copy(deep=True);cloned.id=identifier;cloned.name=str(payload.get("name",model.name+" copy"))
        domain.models.append(cloned);study.model_id=identifier
        return {"data":validate(domain.model_dump(mode="json")),"changed":True}
    if operation=="mesh":return _mesh(store,project,domain,study,model,payload,cancel)
    if operation in {"solve","run"}:return _run(store,project,domain,study,model,payload,cancel)
    if operation=="detect_contacts":
        if not model.mesh:raise ValueError("Mesh the analysis model before contact detection")
        from scipy.spatial import cKDTree
        import numpy as np
        mesh=model.mesh;regions={r.id:r for r in mesh.regions}
        a,b=regions.get(payload.get("region_a")),regions.get(payload.get("region_b"))
        if a is None or b is None or a.id==b.id:raise ValueError("Select two distinct existing mesh regions")
        tolerance=float(payload.get("tolerance_mm",.01))
        if not 0<tolerance<=10:raise ValueError("Detection tolerance must be 0..10 mm")
        aid=sorted(set(a.node_ids)|{n for t in a.triangles for n in t});bid=sorted(set(b.node_ids)|{n for t in b.triangles for n in t})
        tree=cKDTree([mesh.nodes[n] for n in bid]);pairs=[];ambiguous=[]
        for n in aid:
            matches=tree.query_ball_point(mesh.nodes[n],tolerance)
            if len(matches)==1:pairs.append([n,bid[matches[0]]])
            elif len(matches)>1:ambiguous.append(n)
        if len({p[1] for p in pairs})!=len(pairs):raise ValueError("Contact detection is many-to-one; repair the mesh or choose manual pairs")
        return {"proposed_node_pairs":pairs,"ambiguous_nodes":ambiguous,"unmatched_nodes":[n for n in aid if n not in {p[0] for p in pairs} and n not in ambiguous],"tolerance_mm":tolerance,"requires_review":True,"changed":False}
    if operation=="compare":
        selected=payload.get("study_ids",[study.id])
        if len(selected)>20:raise ValueError("Compare at most twenty studies")
        values=[]
        for sid in selected:
            s,_=_study(domain,sid);last=_latest(store,project,domain,s)
            if last:values.append(last)
        return {"studies":values,"physical_validation":"not_supplied"}
    if operation in {"results","report","handoff"}:
        last=_latest(store,project,domain,study)
        if not last:raise ValueError("This study has no completed results")
        if operation=="results":return last
        if operation=="report":return _report(store,last)
        return _handoff(store,project,domain,study,model,last)
    raise ValueError(f"Unsupported simulation operation: {operation}")


def _handoff(store,project,domain,study,model,last):
    if last["stale"]:raise ValueError("Study inputs changed; recompute before exporting a current analysis package. Historical result artifacts remain available.")
    key=input_key(project,domain,study)
    destination=store.cache/"domains"/"simulation"/"handoff"/key
    destination.mkdir(parents=True,exist_ok=True)
    package=destination/"analysis-package.zip"
    losses=["Not an Ansys .sdz archive; Ansys study/selection semantics require recreation.","Neutral inputs are complete; deck export is restricted to translated CalculiX structural cases."]
    decks={}
    if model.mesh:
        from ..simulation_fem import System
        from ..extended_solver import write_calculix
        try:
            system=System(model.mesh,study,{m.id:m for m in domain.materials})
            if study.type not in {"linear_static","nonlinear_static"}:raise ValueError("Study type has no handoff deck adapter")
            for case in study.cases:
                path=destination/(digest(case.id)+".inp")
                write_calculix(path,system,case,nonlinear=study.type=="nonlinear_static")
                decks[case.id]=path
        except ValueError as exc:losses.append("CalculiX deck unavailable: "+str(exc))
    manifest={"schema_version":1,"format":"oxygen-neutral-analysis-package-v1","input_key":key,"revision":project.revision,"stale_results":last["stale"],"geometry_key":geometry_key(project),"transfer_losses":losses,"exported_decks":list(decks),"geometry_included":False,"adapter_version":ADAPTER_VERSION}
    with zipfile.ZipFile(package,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json",json.dumps(manifest,indent=2))
        archive.writestr("simulation.json",json.dumps(domain.model_dump(mode="json"),indent=2))
        archive.writestr("results.json",json.dumps(last,indent=2))
        for cid,path in decks.items():archive.write(path,"calculix/"+path.name)
    return {"artifact":str(package.relative_to(store.cache)),"manifest":manifest}
