"""Reproduce a ten-family CAD/mesh transfer bake-off on disposable fixtures.

These are synthetic task-shaped fixtures, not customer designs or physical tests.
No material/load value is inferred, and no stress/thermal study is solved here.
"""
from __future__ import annotations
import argparse
import json
import time
from pathlib import Path

from o3d.analysis import solver_identity
from o3d.analysis_mesh import generate_mesh,prepare_geometry
from o3d.model import Project,geometry_key
from o3d.quadratic import surface_integrals
from o3d.store import Store,write_json

ROOT=Path(__file__).resolve().parents[1]


def fixtures():
    def plate(name,width,height,thickness,extra=(),mesh_size=4):
        return {"name":name,"features":[{"id":"profile","type":"profile","width":width,"height":height},
                {"id":"base","type":"extrude","profile":"profile","distance":thickness},*extra],
                "study":{"fixed_faces":["part:zmin"],"forces":[{"face":"part:zmax","vector_n":[0,0,0]}],"mesh_size_mm":mesh_size}}
    items={}
    items["cantilever"]=json.loads((ROOT/"examples/cantilever.json").read_text())
    items["camera-bracket"]=json.loads((ROOT/"examples/bracket.json").read_text())
    items["plate-hole"]=plate("Plate with central bore",40,30,5,[{"id":"hole","type":"hole","diameter":10,"center":[0,0,0]}])
    items["bolt-pattern"]=plate("Four-hole motor mounting plate",40,30,5,[{"id":"holes","type":"hole","diameter":4,"center":[-12,-8,0],"columns":2,"rows":2,"spacing":[24,16]}])
    items["thin-panel"]=plate("Thin enclosure panel",40,30,1.2,mesh_size=3)
    items["slotted-link"]=plate("Slotted link",50,16,5,[{"id":"slot_profile","type":"profile","width":28,"height":6},
                                     {"id":"slot","type":"extrude","profile":"slot_profile","distance":5,"operation":"cut"}])
    items["filleted-block"]=plate("Filleted mounting block",30,20,8,[{"id":"round","type":"fillet","selection":"part:vertical_edges","size":2}],mesh_size=3)
    items["ribbed-panel"]=plate("Ribbed mount panel",60,30,4,[{"id":"rib_profile","type":"profile","plane":"YZ","width":4,"height":8,"origin":[-30,0,8]},
                                             {"id":"rib","type":"extrude","profile":"rib_profile","distance":60}])
    items["ribbed-panel"]["study"].update(fixed_faces=["part:ymin"],forces=[{"face":"part:ymax","vector_n":[0,0,0]}])
    items["tube"]={"name":"Hollow sensor support tube","features":[{"id":"outer","type":"profile","shape":"circle","radius":8,"plane":"YZ"},
                    {"id":"tube","type":"extrude","profile":"outer","distance":40},
                    {"id":"inner","type":"profile","shape":"circle","radius":6,"plane":"YZ"},
                    {"id":"bore","type":"extrude","profile":"inner","distance":40,"operation":"cut"}],
                    "study":{"fixed_faces":["part:xmin"],"forces":[{"face":"part:xmax","vector_n":[0,0,0]}],"mesh_size_mm":3}}
    items["open-enclosure"]={"name":"Open instrument enclosure","features":[{"id":"block","type":"native","command":"solid.box","params":{"length":30,"width":20,"height":10}},
                            {"id":"shell","type":"native","command":"solid.shell","params":{"faces":[[15,10,10]],"thickness":2}}],
                            "study":{"fixed_faces":["part:xmin"],"forces":[{"face":"part:xmax","vector_n":[0,0,0]}],"mesh_size_mm":3}}
    result={}
    for name,data in items.items():
        data["material"]=None;data["requirements"]=[]
        data["study"].update(assumptions_confirmed=False,stress_probe_mm=None)
        result[name]=Project.model_validate(data)
    return result


def qualify(output,meshers=("netgen","gmsh"),refinements=2):
    output=Path(output).absolute()
    if output.exists():raise ValueError("Choose a new output directory; qualification evidence is not overwritten")
    output.mkdir(parents=True)
    identity=solver_identity()
    records=[];start=time.monotonic()
    cases=fixtures()
    for name,project in cases.items():
        store=Store(output/name/"project");store.init(project)
        try:transfer=prepare_geometry(store,project)
        except Exception as error:
            records.append({"fixture":name,"stage":"geometry","state":"failed","error":str(error)})
            continue
        write_json(output/name/"transfer.json",transfer)
        for mesher in meshers:
            for level in range(refinements):
                candidate=project.model_copy(deep=True)
                candidate.study.mesher=mesher;candidate.study.mesh_size_mm/=1.5**level
                candidate.study.max_nodes=50000
                run=output/name/mesher/f"mesh-{level}";run.mkdir(parents=True)
                before=time.monotonic()
                try:
                    mesh,quality=generate_mesh(run,transfer,candidate)
                    regions=[]
                    for region in transfer["regions"]:
                        _,area,moment=surface_integrals(mesh,mesh["surfaces"][region["name"]])
                        center=moment/area
                        regions.append({"name":region["name"],"selection":region["selection"],"cad_area_mm2":region["area"],
                                        "mesh_area_mm2":area,"area_relative_error":abs(area-region["area"])/region["area"],
                                        "cad_center_mm":region["center"],"mesh_center_mm":center.tolist()})
                    maximum=max(r["area_relative_error"] for r in regions)
                    passed=maximum<=.02
                    records.append({"fixture":name,"geometry_key":geometry_key(project),"mesher":mesher,"level":level,
                                    "mesh_size_mm":candidate.study.mesh_size_mm,"state":"passed" if passed else "failed",
                                    "quality":quality,"regions":regions,"elapsed_seconds":time.monotonic()-before,
                                    "error":None if passed else "Selected-region integrated area differs from CAD by more than 2%"})
                except Exception as error:
                    records.append({"fixture":name,"mesher":mesher,"level":level,"state":"failed","error":str(error),"elapsed_seconds":time.monotonic()-before})
                write_json(output/"report.json",{"state":"running","records":records})
                print(f"{name}/{mesher}/{level}: {records[-1]['state']}",flush=True)
    count=len(cases)*len(meshers)*refinements
    complete=len(records)==count and all(r["state"]=="passed" for r in records)
    report={"schema_version":1,"state":"passed" if complete else "failed","expected_meshes":count,"fixture_count":len(cases),
            "meshers":list(meshers),"refinements":refinements,"software":identity,"elapsed_seconds":time.monotonic()-start,
            "acceptance":{"selected_area_relative_error":.02,"integrated_volume_relative_error":.1,"positive_jacobian_lower_to_upper":1e-6},
            "scope":"Synthetic single-solid CAD/meshing fixtures; not ten customer parts, solver comparison or physical validation",
            "records":records}
    write_json(output/"report.json",report)
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--refinements',type=int,choices=(1,2,3),default=2)
    parser.add_argument('--meshers',nargs='+',choices=('netgen','gmsh'),default=['netgen','gmsh'])
    args=parser.parse_args()
    result=qualify(args.output,args.meshers,args.refinements)
    print(json.dumps({"state":result["state"],"expected_meshes":result["expected_meshes"],"elapsed_seconds":result["elapsed_seconds"]},indent=2))
    raise SystemExit(0 if result['state']=='passed' else 2)
