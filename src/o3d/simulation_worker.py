"""Cancellable isolated scientific worker for simulation-domain jobs."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from .simulation_models import SimulationDomain

ADAPTER_VERSION="oxygen-tet4-1"


def clean(value):
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if k not in {"u","kg","k","t","temperature"}}
    if isinstance(value,(list,tuple)):return [clean(v) for v in value]
    if hasattr(value,"tolist"):return value.tolist()
    return value


def numerical_checks(result,study):
    settings=study.settings
    checks=result["checks"]
    failures=[]
    if checks.get("residual",1)>max(settings.residual_tolerance,.005 if result.get("solver")=="CalculiX" else 0):failures.append("equation residual")
    for metric in ("force_balance_error","moment_balance_error","heat_balance_error"):
        if checks.get(metric,0)>settings.balance_tolerance:failures.append(metric)
    if checks.get("energy_balance_error",0)>settings.energy_tolerance:failures.append("energy balance")
    if checks.get("mass_orthogonality_error",0)>settings.residual_tolerance:failures.append("modal mass orthogonality")
    if study.type=="quasi_static_event" and checks.get("max_kinetic_internal_ratio",1)>settings.quasi_static_energy_ratio:failures.append("quasi-static kinetic/internal energy ratio across significant strain-energy history")
    if "prestress" in checks:
        if any(checks["prestress"].get(k,0)>settings.balance_tolerance for k in ("force_balance_error","moment_balance_error")):failures.append("prestress equilibrium")
    if study.type in {"topology","generative"}:failures.append("final editable CAD reconstruction/reanalysis pending")
    return failures


def solve(request,directory,cancel=None):
    import numpy,scipy,platform
    from .simulation_fem import System,static_solve,thermal_solve,modal_solve,buckling_solve,event_solve
    from .simulation_search import optimize,generative
    from .extended_solver import calculix_solve
    from .simulation_mesh import validate_boundary
    domain=SimulationDomain.model_validate(request["data"])
    study=next(s for s in domain.studies if s.id==request["study_id"])
    model=next(m for m in domain.models if m.id==study.model_id)
    case=next(c for c in study.cases if c.id==request["case_id"])
    if study.type in {"steady_thermal","electronics_cooling"} and (case.loads or study.bolts):raise ValueError("Mechanical loads/bolts are not translated into a thermal study")
    if study.type not in {"steady_thermal","electronics_cooling","thermal_stress"} and case.thermal:raise ValueError("Thermal boundaries require a thermal or thermal-stress study")
    if study.type=="modal" and case.loads:raise ValueError("Preloaded modal transfer is unavailable; remove mechanical loads for free/constrained modes")
    if study.type not in {"dynamic_event","quasi_static_event"} and case.history:raise ValueError("Time-dependent/unload histories are currently available only in the event adapter")
    meshes=[model.mesh,*model.refinements]
    if meshes[0] is None:raise ValueError("Generate or supply a volume mesh before solving")
    materials={m.id:m for m in domain.materials}
    iterations=[];result=None;previous=None;converged=False
    previous_resolution=None;seen_discretizations=set()
    for i,mesh in enumerate(meshes):
        validate_boundary(mesh)
        system=System(mesh,study,materials,mechanical=study.type not in {"steady_thermal","electronics_cooling"})
        from .model import digest
        # Compare actual cell geometry, not user-controlled mesh/node/element IDs.
        canonical=sorted((e.body_id,sorted(tuple(round(v,12) for v in mesh.nodes[n]) for n in e.nodes)) for e in mesh.elements)
        discretization=digest(canonical)
        if discretization in seen_discretizations:raise ValueError("Identical discretization cannot demonstrate mesh convergence")
        seen_discretizations.add(discretization)
        resolution=(len(mesh.nodes),len(mesh.elements),max(x[3] for x in system.elements))
        if previous_resolution and not (resolution[0]>previous_resolution[0] and resolution[1]>previous_resolution[1] and resolution[2]<previous_resolution[2]*(1-1e-6)):
            raise ValueError("Mesh convergence requires genuinely finer cells, more nodes and more elements")
        previous_resolution=resolution
        if study.type=="linear_static":result=static_solve(system,case,cancel)
        elif study.type in {"steady_thermal","electronics_cooling"}:
            result=thermal_solve(system,case,cancel)
            if study.type=="electronics_cooling":result["warnings"]=["Prescribed-film solid electronics thermal model. No air domain, fan-flow, buoyancy or conjugate CFD solve; no fluid result is implied."]
        elif study.type=="thermal_stress":
            thermal=thermal_solve(system,case,cancel)
            result=static_solve(system,case,cancel,thermal["temperature"])
            result["values"].update(thermal["values"])
            result["field"].update(thermal["field"])
            result["checks"]["heat_balance_error"]=thermal["checks"]["heat_balance_error"]
            result["checks"]["thermal_residual"]=thermal["checks"]["residual"]
        elif study.type=="modal":result=modal_solve(system,case,cancel)
        elif study.type=="buckling":result=buckling_solve(system,case,cancel)
        elif study.type=="nonlinear_static":
            result=calculix_solve(system,case,directory/f"mesh-{i}"/"increment-1",cancel)
            refined=calculix_solve(system,case,directory/f"mesh-{i}"/"increment-half",cancel,increment_scale=.5)
            delta=abs(refined["values"]["max_displacement_mm"]-result["values"]["max_displacement_mm"])/max(abs(refined["values"]["max_displacement_mm"]),1e-12)
            result=refined;result["checks"]["load_step_change"]=delta
        elif study.type in {"dynamic_event","quasi_static_event"}:
            coarse=event_solve(system,case,cancel)
            result=event_solve(system,case,cancel,step_scale=.5)
            delta=abs(result["values"]["final_displacement_mm"]-coarse["values"]["final_displacement_mm"])/max(abs(result["values"]["final_displacement_mm"]),1e-12)
            result["checks"]["time_step_change"]=delta
        elif study.type=="topology":result=optimize(system,case,cancel)
        elif study.type=="generative":result=generative(system,case,cancel)
        else:raise ValueError(f"{study.type}: no qualified integrated solver adapter is available")
        quantities={k:v for k,v in result["values"].items() if k in {"max_displacement_mm","max_von_mises_mpa","max_temperature_k","min_frequency_hz","min_buckling_factor"}}
        # Element stress maxima are screening fields, not a nonsingular stress-convergence probe.
        convergence_quantities={k:v for k,v in quantities.items() if k!="max_von_mises_mpa"}
        changes={k:abs(v-previous[k])/max(abs(v),1e-12) for k,v in convergence_quantities.items() if previous is not None and k in previous}
        converged=bool(changes) and all(v<=study.settings.convergence_tolerance for v in changes.values())
        iterations.append({"mesh_id":mesh.id,"nodes":len(mesh.nodes),"elements":len(mesh.elements),"mesh_size_mm":mesh.mesh_size_mm,"minimum_mean_ratio_quality":system.quality,"values":quantities,"relative_changes":changes})
        previous=convergence_quantities
    result=clean(result)
    failures=numerical_checks(result,study)
    if result["checks"].get("time_step_change",0)>study.settings.convergence_tolerance:failures.append("time-step sensitivity")
    if result["checks"].get("load_step_change",0)>study.settings.convergence_tolerance:failures.append("load-step sensitivity")
    result["checks"]["mesh_converged"]=converged
    if not converged:failures.append("mesh convergence not demonstrated")
    findings=[]
    for limit in study.limits:
        value=result["values"].get(limit.quantity)
        met=None if value is None else bool(value<=limit.value if limit.operator=="le" else value>=limit.value)
        strength_screening=limit.quantity=="max_von_mises_mpa"
        findings.append({"id":limit.id,"quantity":limit.quantity,"value":value,"limit":limit.value,"operator":limit.operator,"satisfied":met,"source":limit.source,"evidence":"screening peak stress; no nonsingular stress probe convergence" if strength_screening else "specified numerical model","verified":not failures and not strength_screening and met is not None})
    result.update({"schema_version":1,"adapter_version":ADAPTER_VERSION,"input_key":request["input_key"],"geometry_key":request["geometry_key"],"revision":request["revision"],"study_id":study.id,"case_id":case.id,"study_type":study.type,"numerical_state":"inconclusive" if failures else "verified_numerically","verification_gaps":failures,"requirements_state":"unassessed" if not findings else "inconclusive" if failures or any(not f["verified"] for f in findings) else "not_satisfied" if any(f["satisfied"] is False for f in findings) else "satisfied","findings":findings,"mesh_iterations":iterations,"software":{"adapter":ADAPTER_VERSION,"numpy":numpy.__version__,"scipy":scipy.__version__,"python":platform.python_version()},"physical_validation":"not_supplied","units":{"length":"mm","force":"N","time":"s","mass":"tonne","temperature":"K","thermal_power":"W","stress":"MPa","heat_flux":"W/mm2"},"field_location":{"stress":"element_constant" if study.type!="nonlinear_static" else "nodal_extrapolated","displacement":"node","temperature":"node","mode_shape":"mass-normalized amplitude; not physical displacement"}})
    result.setdefault("solver","Oxygen C3D4 NumPy/SciPy reference worker")
    result.setdefault("warnings",[])
    result["warnings"].extend(["Numerical verification applies only to the stated discretization, inputs and assumptions; no hardware correlation was supplied.","Linear tetrahedra can be overly stiff in bending and near incompressibility; demonstrate mesh convergence for the requested quantity."])
    return result


if __name__=="__main__":
    from .store import write_json
    request=json.loads(Path(sys.argv[1]).read_text())
    destination=Path(sys.argv[2])
    import signal
    from threading import Event
    cancellation=Event()
    def cancel_signal(signum,frame):cancellation.set()
    signal.signal(signal.SIGTERM,cancel_signal)
    signal.signal(signal.SIGINT,cancel_signal)
    result=solve(request,destination.parent,cancellation)
    write_json(destination,result)
