"""Cancellable isolated scientific worker for simulation-domain jobs."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from .simulation_models import SimulationDomain

ADAPTER_VERSION="oxygen-canonical-8"


def clean(value):
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if k not in {"u","kg","k","t","temperature"}}
    if isinstance(value,(list,tuple)):return [clean(v) for v in value]
    if hasattr(value,"tolist"):return value.tolist()
    return value


def numerical_checks(result,study):
    settings=study.settings
    checks=result["checks"]
    failures=[]
    if checks.get("residual",1)>settings.residual_tolerance:failures.append("equation residual")
    if checks.get("thermal_residual",0)>settings.residual_tolerance:failures.append("thermal equation residual")
    for body,screen in result.get("constitutive_screening",{}).items():
        if screen["exceeds_declared_yield"]:failures.append(f"Body {body} integration-point stress exceeds declared elastic yield")
    if checks.get("maximum_free_thermal_strain_bound",0)>.05:failures.append("linear thermal-strain screening limit exceeded")
    for metric in ("force_balance_error","moment_balance_error","heat_balance_error"):
        if checks.get(metric,0)>settings.balance_tolerance:failures.append(metric)
    if checks.get("energy_balance_error",0)>settings.energy_tolerance:failures.append("energy balance")
    if checks.get("mass_orthogonality_error",0)>settings.residual_tolerance:failures.append("modal mass orthogonality")
    if study.type=="quasi_static_event" and checks.get("max_kinetic_internal_ratio",1)>settings.quasi_static_energy_ratio:failures.append("quasi-static kinetic/internal energy ratio across significant strain-energy history")
    if checks.get("stiffness_orthogonality_error",0)>settings.residual_tolerance:failures.append("buckling elastic-stiffness orthogonality")
    if checks.get("reference_displacement_to_extent_ratio",0)>.05:failures.append("linear reference-load deformation screen exceeded")
    if checks.get("geometric_integration",{}).get("maximum_sampled_strain_norm",0)>.05:failures.append("linear reference-load strain screen exceeded")
    if checks.get("maximum_relative_interface_jump",0)>settings.convergence_tolerance:failures.append("mortar strong trace jump requires further interface refinement")
    if "prestress" in checks:
        if checks["prestress"].get("residual",0)>settings.residual_tolerance:failures.append("prestress equation residual")
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
    from .simulation_provenance import software_fingerprint
    family=model.mesh.element_family if model.mesh else "C3D4"
    actual_software=software_fingerprint(study.type,family)
    if request.get("software",actual_software)!=actual_software:raise ValueError("Solver software changed after snapshot; resubmit the study")
    case=next(c for c in study.cases if c.id==request["case_id"])
    if study.settings.large_deformation and study.type!="nonlinear_static":raise ValueError("Large deformation requires the CalculiX nonlinear-static adapter")
    if study.settings.probe_point_mm is not None and study.type not in {"linear_static","nonlinear_static","thermal_stress"}:raise ValueError("Physical displacement probes require a supported static study; no arbitrary mode amplitude or unused probe is accepted")
    if study.settings.inertial_relief and study.type not in {"linear_static","thermal_stress","topology","generative"}:raise ValueError("Inertial relief is unavailable for this study type")
    if study.type=="modal" and any(b.preload_n for b in study.bolts):raise ValueError("Preloaded modal connector transfer is unavailable")
    if study.type not in {"steady_thermal","electronics_cooling","thermal_stress"} and any(i.kind=="thermal" for i in study.interfaces):raise ValueError("Thermal interfaces require a thermal or thermal-stress study")
    if study.type in {"steady_thermal","electronics_cooling"} and (case.loads or study.bolts):raise ValueError("Mechanical loads/bolts are not translated into a thermal study")
    if study.type not in {"steady_thermal","electronics_cooling","thermal_stress"} and case.thermal:raise ValueError("Thermal boundaries require a thermal or thermal-stress study")
    if study.type=="thermal_stress" and any(case.initial_velocity_mm_s):raise ValueError("Thermal stress does not translate initial velocity")
    if study.type=="modal" and case.loads:raise ValueError("Preloaded modal transfer is unavailable; remove mechanical loads for free/constrained modes")
    if study.type not in {"dynamic_event","quasi_static_event","nonlinear_static"} and case.history:raise ValueError("Load histories require an event or nonlinear-static study")
    meshes=[model.mesh,*model.refinements]
    if meshes[0] is None:raise ValueError("Generate or supply a volume mesh before solving")
    if any(mesh.element_family!=family for mesh in meshes):raise ValueError("Refinement levels must use the same element family")
    if family=="C3D10" and study.type not in {"linear_static","nonlinear_static","steady_thermal","electronics_cooling","thermal_stress","modal","buckling"}:raise ValueError(f"Canonical quadratic {study.type} transfer is not integrated; no corner-only fallback is permitted")
    materials={m.id:m for m in domain.materials}
    iterations=[];result=None;previous=None;converged=False
    previous_resolution=None;seen_discretizations=set();previous_thermal_scale=0.;previous_eigen=None
    for i,mesh in enumerate(meshes):
        validate_boundary(mesh)
        if family=="C3D10":
            from .simulation_quadratic import QuadraticSystem
            if study.type=="linear_static" and study.interfaces:
                from .simulation_assembly import BondedAssemblySystem
                system=BondedAssemblySystem(mesh,study,materials,cancel)
            elif study.type in {"steady_thermal","electronics_cooling"}:
                from .simulation_quadratic_thermal import QuadraticThermalSystem
                system=QuadraticThermalSystem(mesh,study,materials,cancel)
            elif study.type in {"modal","buckling"}:
                from .simulation_eigen import QuadraticEigenSystem,QuadraticElasticSystem
                system=(QuadraticEigenSystem if study.type=="modal" else QuadraticElasticSystem)(mesh,study,materials,cancel)
            else:system=QuadraticSystem(mesh,study,materials,cancel)
            system.qualify(directory/f"mesh-{i}"/"quality",cancel)
        else:
            system=System(mesh,study,materials,mechanical=study.type not in {"steady_thermal","electronics_cooling"})
        from .model import digest
        # Compare actual cell geometry, not user-controlled mesh/node/element IDs.
        canonical=sorted((e.body_id,sorted(tuple(round(v,12) for v in system.mesh.nodes[n]) for n in e.nodes)) for e in system.mesh.elements)
        discretization=digest(canonical)
        if discretization in seen_discretizations:raise ValueError("Identical discretization cannot demonstrate mesh convergence")
        seen_discretizations.add(discretization)
        resolution=(len(mesh.nodes),len(mesh.elements),system.max_element_volume_mm3)
        if previous_resolution and not (resolution[0]>previous_resolution[0] and resolution[1]>previous_resolution[1] and resolution[2]<previous_resolution[2]*(1-1e-6)):
            raise ValueError("Mesh convergence requires genuinely finer cells, more nodes and more elements")
        previous_resolution=resolution
        if study.type=="linear_static":
            if family=="C3D10" and study.interfaces:
                from .simulation_assembly import bonded_solve
                result=bonded_solve(system,case,directory/f"mesh-{i}"/"bonded",cancel)
            else:result=calculix_solve(system,case,directory/f"mesh-{i}"/"linear",cancel,nonlinear=False) if family=="C3D10" else static_solve(system,case,cancel)
        elif study.type in {"steady_thermal","electronics_cooling"}:
            if family=="C3D10":
                from .simulation_quadratic_thermal import thermal_solve as quadratic_thermal_solve
                result=quadratic_thermal_solve(system,case,directory/f"mesh-{i}"/"thermal",cancel)
            else:result=thermal_solve(system,case,cancel)
            if study.type=="electronics_cooling":result["warnings"]=["Prescribed-film solid electronics thermal model. No air domain, fan-flow, buoyancy or conjugate CFD solve; no fluid result is implied."]
        elif study.type=="thermal_stress":
            if family=="C3D10":
                from .simulation_quadratic_thermal import QuadraticThermalSystem,thermal_solve as quadratic_thermal_solve
                thermal_system=QuadraticThermalSystem(mesh,study,materials,cancel)
                thermal_system.qualify(directory/f"mesh-{i}"/"thermal-quality",cancel)
                thermal_case=case.model_copy(update={"supports":[],"loads":[]})
                thermal=quadratic_thermal_solve(thermal_system,thermal_case,directory/f"mesh-{i}"/"thermal",cancel)
                result=calculix_solve(system,case,directory/f"mesh-{i}"/"thermal-stress",cancel,nonlinear=False,temperatures=thermal["field"]["temperature_k"])
                result["checks"]["thermal_checks"]=thermal["checks"]
                result["checks"]["temperature_convergence_scale_k"]=thermal["checks"]["temperature_convergence_scale_k"]
                result["warnings"].extend(thermal["warnings"])
                result["thermal_transfer"]["phase_split"]={"thermal_boundary_ids":[b.id for b in case.thermal],"structural_support_ids":[b.id for b in case.supports],"structural_load_ids":[b.id for b in case.loads]}
            else:
                thermal=thermal_solve(system,case,cancel)
                result=static_solve(system,case,cancel,thermal["temperature"])
            result["values"].update(thermal["values"])
            result["field"].update(thermal["field"])
            result["checks"]["heat_balance_error"]=thermal["checks"]["heat_balance_error"]
            result["checks"]["thermal_residual"]=thermal["checks"]["residual"]
        elif study.type=="modal":
            if family=="C3D10":
                from .simulation_eigen import modal_solve as quadratic_modal_solve
                result=quadratic_modal_solve(system,case,directory/f"mesh-{i}"/"modal",cancel)
            else:result=modal_solve(system,case,cancel)
        elif study.type=="buckling":
            if family=="C3D10":
                from .simulation_buckling import buckling_solve as quadratic_buckling_solve
                result=quadratic_buckling_solve(system,case,directory/f"mesh-{i}"/"buckling",cancel)
            else:result=buckling_solve(system,case,cancel)
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
        quantities={k:v for k,v in result["values"].items() if k in {"max_displacement_mm","probe_displacement_mm","max_von_mises_mpa","max_temperature_k","min_temperature_k","temperature_rise_k","min_frequency_hz","min_buckling_factor"}}
        # Element stress maxima are screening fields, not a nonsingular stress-convergence probe.
        convergence_quantities={k:v for k,v in quantities.items() if k!="max_von_mises_mpa"}
        changes={k:abs(v-previous[k])/max(abs(v),1e-12) for k,v in convergence_quantities.items() if previous is not None and k in previous}
        if family=="C3D10" and study.type in {"steady_thermal","electronics_cooling","thermal_stress"}:
            scale=max(result["checks"]["temperature_convergence_scale_k"],previous_thermal_scale,1e-9)
            for quantity in {"max_temperature_k","min_temperature_k","temperature_rise_k"}:
                if previous is not None and quantity in previous and quantity in convergence_quantities:
                    changes[quantity]=abs(convergence_quantities[quantity]-previous[quantity])/scale
            previous_thermal_scale=result["checks"]["temperature_convergence_scale_k"]
        if family=="C3D10" and study.type in {"modal","buckling"}:
            bundle=result.pop("_mode_bundle")
            tracking={"passed":False,"reason":"A second genuinely finer mesh is required"}
            if previous_eigen is not None:
                from .simulation_mode_tracking import track_modes
                try:tracking=track_modes(previous_eigen,bundle,study.settings.mode_count,cancel)
                except (ValueError,numpy.linalg.LinAlgError) as error:tracking={"passed":False,"reason":str(error)}
            previous_eigen=bundle
            result["mode_tracking"]=tracking
            result["checks"]["mode_tracking_passed"]=tracking["passed"]
            from .store import write_json
            write_json(directory/f"mesh-{i}"/study.type/"mode-tracking.json",tracking)
            changes={f"elastic_mode_{j+1}_frequency_hz" if study.type=="modal" else f"mode_{j+1}_load_factor":v for j,v in enumerate(tracking.get("quantity_changes",[]))}
        converged=bool(changes) and all(v<=study.settings.convergence_tolerance for v in changes.values())
        if family=="C3D10" and study.type in {"modal","buckling"}:converged=converged and result["checks"]["mode_tracking_passed"]
        iterations.append({"mesh_id":mesh.id,"nodes":len(mesh.nodes),"elements":len(mesh.elements),"element_family":family,"mesh_size_mm":mesh.mesh_size_mm,("minimum_mean_ratio_quality" if family=="C3D4" else "minimum_corner_determinant_edge_ratio"):system.quality,"values":quantities,"relative_changes":changes})
        previous=convergence_quantities
    from .simulation_mesh import display_triangles,exterior_triangles
    result["field"]["boundary_triangles"]=display_triangles(mesh)
    result["field"]["boundary_faces"]=exterior_triangles(mesh.nodes,mesh.elements)
    result["field"]["element_connectivity"]={e.id:list(e.nodes) for e in mesh.elements}
    result=clean(result)
    failures=numerical_checks(result,study)
    if family=="C3D10" and study.type in {"linear_static","nonlinear_static","thermal_stress"} and study.settings.probe_point_mm is None:failures.append("fixed physical displacement probe not supplied")
    if family=="C3D10" and study.type in {"linear_static","thermal_stress"} and result["checks"].get("displacement_to_extent_ratio",1)>.05:failures.append("small-deformation screening limit exceeded")
    if family=="C3D10" and study.type in {"modal","buckling"} and not result["checks"].get("mode_tracking_passed",False):failures.append("physical mode-shape correlation not established")
    if result["checks"].get("time_step_change",0)>study.settings.convergence_tolerance:failures.append("time-step sensitivity")
    if result["checks"].get("load_step_change",0)>study.settings.convergence_tolerance:failures.append("load-step sensitivity")
    result["checks"]["residual_tolerance_used"]=study.settings.residual_tolerance
    result["checks"]["mesh_converged"]=converged
    result["checks"]["converged_quantities"]=list(iterations[-1]["relative_changes"]) if converged else []
    if not converged:failures.append("mesh convergence not demonstrated")
    findings=[]
    for limit in study.limits:
        value=result["values"].get(limit.quantity)
        met=None if value is None else bool(value<=limit.value if limit.operator=="le" else value>=limit.value)
        strength_screening=limit.quantity=="max_von_mises_mpa"
        findings.append({"id":limit.id,"quantity":limit.quantity,"value":value,"limit":limit.value,"operator":limit.operator,"satisfied":met,"source":limit.source,"evidence":"screening peak stress; no nonsingular stress probe convergence" if strength_screening else "specified numerical model","verified":not failures and not strength_screening and met is not None})
    result.update({"schema_version":1,"adapter_version":ADAPTER_VERSION,"input_key":request["input_key"],"geometry_key":request["geometry_key"],"revision":request["revision"],"study_id":study.id,"case_id":case.id,"study_type":study.type,"numerical_state":"inconclusive" if failures else "verified_numerically","verification_gaps":failures,"requirements_state":"unassessed" if not findings else "inconclusive" if failures or any(not f["verified"] for f in findings) else "not_satisfied" if any(f["satisfied"] is False for f in findings) else "satisfied","findings":findings,"mesh_iterations":iterations,"software":{"adapter":ADAPTER_VERSION,"numpy":numpy.__version__,"scipy":scipy.__version__,"python":platform.python_version(),"fingerprint":actual_software},"physical_validation":"not_supplied","units":{"length":"mm","force":"N","time":"s","mass":"tonne","temperature":"K","thermal_power":"W","stress":"MPa","heat_flux":"W/mm2"},"field_location":{"stress":"element_constant" if study.type!="nonlinear_static" else "nodal_extrapolated","displacement":"node","temperature":"node","mode_shape":"mass-normalized amplitude; not physical displacement"}})
    if study.type=="buckling":result["field_location"]["mode_shape"]="unit-amplitude reference shape; not physical displacement"
    if family=="C3D10":
        if study.type in {"linear_static","nonlinear_static","thermal_stress"}:result["field_location"]["stress"]="nodal_extrapolated"
        if study.type=="linear_static" and study.interfaces:result["field_location"]["stress"]="volume-weighted nodal recovery, screening only"
        if study.type=="thermal_stress":result["field_location"]["integration_point_stress"]="element_integration_point"
        if study.type in {"steady_thermal","electronics_cooling","thermal_stress"}:result["field_location"].update({"heat_flux":"element_volume_average", "temperature_extrema":"conservative Bernstein polynomial bounds"})
    if software_fingerprint(study.type,family)!=actual_software:raise ValueError("Solver software changed during execution; results refused")
    result.setdefault("solver","Oxygen C3D4 NumPy/SciPy reference worker")
    result.setdefault("warnings",[])
    result["warnings"].append("Numerical verification applies only to the stated discretization, inputs and assumptions; no hardware correlation was supplied.")
    if family=="C3D4":result["warnings"].append("Linear tetrahedra can be overly stiff in bending and near incompressibility; demonstrate mesh convergence for the requested quantity.")
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
