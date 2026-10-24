"""Versioned single-body study adapters and reproducible numerical evidence.

Every advertised calculation executes an external solver. Unsupported physics
remain explicit, and numerical verification is separate from physical validation.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import time
from functools import lru_cache
from pathlib import Path

import numpy as np

from .analysis_mesh import exterior_triangles, generate_mesh, prepare_geometry
from .model import digest, geometry_key, study_key
from .store import now, write_json

ADAPTER_VERSION = "calculix-studies-v2"
LABELS = {"linear_static": "Static stress", "modal": "Modal frequencies", "buckling": "Structural buckling",
          "thermal": "Steady thermal", "thermal_stress": "Thermal stress", "nonlinear_static": "Nonlinear static stress",
          "quasistatic_event": "Quasi-static event", "dynamic_event": "Dynamic event", "shape_optimization": "Shape optimization",
          "electronics_cooling": "Electronics cooling", "injection_molding": "Injection molding"}
IMPLEMENTED = {"linear_static", "modal", "buckling", "thermal", "thermal_stress"}
THERMAL = {"thermal", "thermal_stress"}


def capabilities():
    return {"schema_version": 1, "adapter_version": ADAPTER_VERSION,
            "scope": "One connected isotropic solid; planar boundary regions; C3D10 elements; mm/N/s/MPa",
            "studies": [{"type": key, "label": label, "status": "experimental" if key in IMPLEMENTED else "unavailable",
                         "solver": "CalculiX" if key in IMPLEMENTED else None,
                         "physical_validation": "not_performed"} for key, label in LABELS.items()],
            "meshers": ["netgen", "gmsh"],
            "unsupported": ["assemblies/contact", "anisotropic constitutive laws", "shells/beams",
                            "plasticity/damage", "convection/radiation", "preloaded modal analysis"]}


def validate_study(project):
    study, material = project.study, project.material
    errors = []
    if study.type not in IMPLEMENTED:
        return [f"Unsupported study type: {LABELS[study.type]}; no validated adapter is installed"]
    if material is None:
        errors.append("Specify material properties, density and data source")
    elif study.type != "thermal" and (material.youngs_modulus_mpa is None or material.poisson_ratio is None):
        errors.append("Specify material elastic modulus and Poisson ratio")
    if study.type != "thermal" and not study.fixed_faces:
        errors.append("Select at least one fully fixed planar face")
    if study.type in ("linear_static", "buckling") and (not study.forces or not any(np.linalg.norm(f.vector_n) > 0 for f in study.forces)):
        errors.append("Specify a nonzero force and its application face")
    if study.type == "modal" and study.forces:
        errors.append("Unloaded modal analysis does not accept applied forces; clear them or use a supported preload adapter")
    if study.type == "thermal" and (study.fixed_faces or study.forces):
        errors.append("Pure thermal analysis does not accept mechanical supports or forces")
    if study.type in THERMAL:
        if material is not None and material.thermal_conductivity_w_m_k is None:
            errors.append("Specify thermal conductivity and its data source")
        if not study.temperatures:
            errors.append("Select at least one prescribed-temperature face for steady heat rejection")
    elif study.temperatures or study.heat_sources:
        errors.append("This mechanical study does not accept thermal boundary conditions")
    if study.type == "thermal_stress":
        if material is not None and material.thermal_expansion_per_k is None:
            errors.append("Specify thermal expansion coefficient")
        if study.reference_temperature_c is None:
            errors.append("Specify the stress-free reference temperature")
    names = study.fixed_faces + [f.face for f in study.forces]
    if len(names) != len(set(names)):
        errors.append("Load and fixed regions must be distinct and unique")
    thermal_names = [v.face for v in study.temperatures] + [v.face for v in study.heat_sources]
    if len(thermal_names) != len(set(thermal_names)):
        errors.append("Temperature and heat-source faces must be distinct and unique")
    if not study.assumptions_confirmed:
        errors.append("Review and confirm the selected study's modeling assumptions")
    if study.type in ("modal", "buckling") and study.stress_probe_mm is not None:
        errors.append("Eigenmode studies have arbitrary amplitudes and do not accept stress probes")
    return errors


def set_lines(name, values):
    ordered = sorted(values)
    return [f"*NSET,NSET={name}"] + [", ".join(map(str, ordered[i:i+16])) for i in range(0, len(ordered), 16)]


def region_nodes(mesh, name):
    return {n for tri in mesh["surfaces"][name] for n in tri}


def distribute_scalar(mesh, name, total):
    nodes = {int(k): np.array(v) for k, v in mesh["nodes"].items()}
    triangles = mesh["surfaces"][name]
    areas = [np.linalg.norm(np.cross(nodes[t[1]]-nodes[t[0]], nodes[t[2]]-nodes[t[0]]))/2 for t in triangles]
    area = sum(areas)
    if area <= 0:
        raise ValueError("Boundary region has zero area")
    loads = {}
    for tri, a in zip(triangles, areas):
        for n in tri[3:]:
            loads[n] = loads.get(n, 0) + total*a/(3*area)
    return loads


def thermal_boundaries(mesh, study):
    prescribed, flux = {}, {}
    for i, boundary in enumerate(study.temperatures):
        for n in region_nodes(mesh, f"temperature_{i}"):
            if n in prescribed and prescribed[n] != boundary.temperature_c:
                raise ValueError("Different prescribed temperatures meet at a shared node; split the regions")
            prescribed[n] = boundary.temperature_c
    for i, source in enumerate(study.heat_sources):
        for n, value in distribute_scalar(mesh, f"heat_{i}", source.power_w*1000).items():
            flux[n] = flux.get(n, 0) + value
    if set(prescribed).intersection(flux):
        raise ValueError("A heat-source region overlaps prescribed-temperature nodes")
    return prescribed, flux


def write_deck(path, mesh, project, thermal=False, temperatures=None):
    from .solver import load_boundary
    study, material = project.study, project.material
    lines = ["*HEADING", f"Oxygen CAD {study.type}; consistent mm, N, s, MPa, tonne, degC", "*NODE"]
    lines += [f"{n}, {p[0]:.12g}, {p[1]:.12g}, {p[2]:.12g}" for n,p in sorted((int(k),v) for k,v in mesh["nodes"].items())]
    lines += ["*ELEMENT,TYPE=C3D10,ELSET=EALL"]
    lines += [f"{i}, "+", ".join(map(str,e)) for i,e in enumerate(mesh["elements"],1)]
    lines += set_lines("ALLNODES", map(int, mesh["nodes"]))
    lines += ["*MATERIAL,NAME=MAT"]
    if thermal:
        # W/(m K) has the same numerical value in N/(s K).
        lines += ["*CONDUCTIVITY", f"{material.thermal_conductivity_w_m_k:.12g}"]
    else:
        lines += ["*ELASTIC", f"{material.youngs_modulus_mpa:.12g}, {material.poisson_ratio:.12g}"]
        if study.type == "modal":
            lines += ["*DENSITY", f"{material.density_g_cm3*1e-9:.12g}"]
        if temperatures is not None:
            lines += [f"*EXPANSION,ZERO={study.reference_temperature_c:.12g}", f"{material.thermal_expansion_per_k:.12g}"]
    lines += ["*SOLID SECTION,ELSET=EALL,MATERIAL=MAT"]
    if thermal:
        prescribed, flux = thermal_boundaries(mesh, study)
        lines += set_lines("THERMAL_FIXED", prescribed)
        lines += ["*STEP", "*HEAT TRANSFER,STEADY STATE", "1.,1.", "*BOUNDARY"]
        lines += [f"{n},11,11,{t:.12g}" for n,t in sorted(prescribed.items())]
        if flux:
            lines += ["*CFLUX"] + [f"{n},11,{v:.12g}" for n,v in sorted(flux.items()) if abs(v)>1e-14]
        lines += ["*NODE PRINT,NSET=ALLNODES", "NT", "*NODE PRINT,NSET=THERMAL_FIXED", "RFL",
                  "*NODE FILE", "NT,RFL", "*EL FILE", "HFL", "*END STEP"]
        path.write_text("\n".join(lines)+"\n")
        return prescribed, flux
    fixed, forces = load_boundary(mesh, study)
    lines += set_lines("FIXED", fixed)
    lines += ["*BOUNDARY", "FIXED,1,3"]
    if temperatures is not None:
        lines += ["*INITIAL CONDITIONS,TYPE=TEMPERATURE", f"ALLNODES,{study.reference_temperature_c:.12g}"]
    lines += ["*STEP"]
    if study.type == "modal":
        lines += ["*FREQUENCY", str(study.mode_count)]
    elif study.type == "buckling":
        lines += ["*BUCKLE", str(study.mode_count)]
    else:
        lines += ["*STATIC"]
    if forces and study.type != "modal":
        lines += ["*CLOAD"]
        for n, force in sorted(forces.items()):
            lines += [f"{n},{i},{value:.12g}" for i,value in enumerate(force,1) if abs(value)>1e-14]
    if temperatures is not None:
        lines += ["*TEMPERATURE"] + [f"{n},{t:.12g}" for n,t in sorted(temperatures.items())]
    if study.type not in ("modal", "buckling"):
        lines += ["*NODE PRINT,NSET=ALLNODES", "U", "*NODE PRINT,NSET=FIXED", "RF", "*EL FILE", "S"]
    lines += ["*NODE FILE", "U", "*END STEP"]
    path.write_text("\n".join(lines)+"\n")
    return fixed, forces


def parse_frd(path):
    frames, frame = [], None
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith(" -4"):
            parts = line.split()
            frame = {"name": parts[1], "values": {}}
            frames.append(frame)
        elif frame is not None and line.startswith(" -1"):
            node = int(line[3:13])
            values = re.findall(r"[+-]?\d\.\d+(?:[ED][+-]\d+)",line[13:])
            frame["values"][node] = [float(v.replace("D","E")) for v in values]
        elif line.startswith(" -3"):
            frame = None
    for frame in frames:
        if not frame["values"] or not all(np.isfinite(v).all() for v in frame["values"].values()):
            raise RuntimeError("Solver field is empty or contains nonfinite values")
    return frames


def parse_scalar_dat(path, marker):
    values, active = {}, False
    for line in path.read_text().splitlines():
        if marker in line.lower():
            values, active = {}, True
        elif active:
            parts = line.split()
            if len(parts)==2 and parts[0].isdigit():
                values[int(parts[0])] = float(parts[1].replace("D","E"))
            elif parts and not parts[0].isdigit():
                active = False
    if not values or not all(math.isfinite(v) for v in values.values()):
        raise RuntimeError(f"Solver output missing finite {marker}")
    return values


def parse_eigenvalues(path, kind):
    values, active = [], False
    for line in path.read_text().splitlines():
        lower = line.lower()
        if ("eigenvalue" in lower and kind == "modal") or ("bucklingfactor" in re.sub(r"\s+", "", lower) and kind == "buckling"):
            active = True
        if active:
            parts = line.split()
            if parts and parts[0].isdigit() and len(parts) >= (4 if kind=="modal" else 2):
                try:
                    value = float(parts[3 if kind=="modal" else 1].replace("D","E"))
                except ValueError:
                    continue
                values.append(value)
            elif values and parts and not parts[0].isdigit():
                break
    if not values or not all(math.isfinite(v) for v in values):
        raise RuntimeError(f"Solver output contains no finite {kind} eigenvalues")
    return values


@lru_cache(maxsize=16)
def _binary_hash(path, mtime, size):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def solver_identity():
    from .solver import solver_binary
    path = Path(solver_binary()).resolve()
    stat = path.stat()
    files = [Path(__file__), Path(__file__).with_name("solver.py"), Path(__file__).with_name("analysis_mesh.py"),
             Path(__file__).with_name("mesh_worker.py"), Path(__file__).with_name("gmsh_worker.py")]
    manifest = {p.name: _binary_hash(str(p),p.stat().st_mtime_ns,p.stat().st_size) for p in files}
    lock = Path(__file__).resolve().parents[2]/"mesh-requirements.lock"
    if lock.exists():manifest[lock.name] = _binary_hash(str(lock),lock.stat().st_mtime_ns,lock.stat().st_size)
    return {"name": "CalculiX", "binary_sha256": _binary_hash(str(path), stat.st_mtime_ns, stat.st_size),
            "adapter_version": ADAPTER_VERSION, "adapter_files": manifest}


def run_solver(run, mesh, project, name, cancel=None, thermal=False, temperatures=None):
    from .solver import run_process, solver_binary
    boundary = write_deck(run/f"{name}.inp",mesh,project,thermal,temperatures)
    run_process([solver_binary(), name],run,run/f"{name}.log",cancel,project.study.solver_timeout_seconds)
    log = (run/f"{name}.log").read_text()
    if "*ERROR" in log.upper() or "JOB FINISHED" not in log.upper():
        raise RuntimeError("CalculiX did not finish a complete solve; inspect its log")
    frames = parse_frd(run/f"{name}.frd")
    return boundary, frames


def mechanical_result(run, mesh, project, frames, boundary, transfer):
    from .solver import interpolate_probe, parse_dat, parse_stress
    fixed, loads = boundary
    displacement,reactions = parse_dat(run/"analysis.dat")
    stress = parse_stress(run/"analysis.frd")
    nodes = {int(k):np.array(v) for k,v in mesh["nodes"].items()}
    if set(displacement)!=set(nodes) or set(stress)!=set(nodes) or not set(fixed).issubset(reactions):
        raise RuntimeError("Incomplete solver displacement or support-reaction field")
    applied = sum(loads.values(),np.zeros(3))
    reacted = sum((np.array(v) for v in reactions.values()),np.zeros(3))
    force_scale = max(sum(np.linalg.norm(v) for v in loads.values()),
                      sum(np.linalg.norm(v) for v in reactions.values()),1e-8)
    applied_moment = sum((np.cross(nodes[n],v) for n,v in loads.items()),np.zeros(3))
    reacted_moment = sum((np.cross(nodes[n],v) for n,v in reactions.items()),np.zeros(3))
    length = np.linalg.norm(np.ptp(np.array(list(nodes.values())),axis=0))
    moment_scale = max(force_scale*length,np.linalg.norm(applied_moment),1e-12)
    force_error = float(np.linalg.norm(applied+reacted)/force_scale)
    moment_error = float(np.linalg.norm(applied_moment+reacted_moment)/moment_scale)
    maximum = max(float(np.linalg.norm(v)) for v in displacement.values())
    selected_probe = project.study.probe_point_mm
    if selected_probe is None:
        load = next((r for r in transfer["regions"] if r["name"]==transfer["aliases"].get("load_0")),None)
        selected_probe = load["center"] if load else np.mean(np.array(list(nodes.values())),axis=0).tolist()
    probe = float(np.linalg.norm(interpolate_probe(mesh,displacement,selected_probe)))
    stress_probe = float(interpolate_probe(mesh,stress,project.study.stress_probe_mm)) if project.study.stress_probe_mm else None
    metrics = {"max_displacement_mm": maximum,"max_stress_mpa":max(stress.values()),
               "probe_displacement_mm":probe,"displacement_probe_mm":selected_probe,"stress_probe_mpa":stress_probe,
               "force_balance_error":force_error,"moment_balance_error":moment_error,
               "displacement_to_extent_ratio":maximum/max(length,1e-12)}
    field = {"displacements":displacement,"stress_mpa":stress}
    observed = [probe] + ([stress_probe] if stress_probe is not None else [])
    return metrics,field,observed,force_error<=.01 and moment_error<=.01 and maximum/max(length,1e-12)<=.05


def simulate(store, project, cancel=None):
    from .solver import Cancelled, interpolate_probe
    errors = validate_study(project)
    if errors:
        raise ValueError("; ".join(errors))
    if cancel and cancel.is_set():
        raise Cancelled("Job cancelled")
    key = study_key(project)
    destination = store.cache/"studies"/key
    destination.mkdir(parents=True,exist_ok=True)
    transfer = prepare_geometry(store,project)
    geometry = destination/"geometry"
    geometry.mkdir(exist_ok=True)
    shutil.copy2(transfer["step"],geometry/"part.step")
    portable_transfer = transfer | {"step":"geometry/part.step"}
    transfer["step"] = "../geometry/part.step"
    write_json(destination/"region-map.json",portable_transfer)
    write_json(destination/"study-package.json",{"schema_version":1,"project":project.model_dump(mode="json"),
               "geometry_key":geometry_key(project),"study_key":key,"unit_system":{"length":"mm","force":"N","time":"s","mass":"tonne","temperature":"degC"},
               "solver":solver_identity(),"regions":portable_transfer,
               "geometry_sha256":hashlib.sha256((geometry/"part.step").read_bytes()).hexdigest()})
    previous,iterations,converged = None,[],False
    start = time.monotonic()
    warnings = ["Numerical results apply to the recorded model and conditions; physical validation has not been performed."]
    for i in range(project.study.refinement_steps):
        if cancel and cancel.is_set():
            raise Cancelled("Job cancelled")
        run = destination/f"mesh-{i}"
        run.mkdir(exist_ok=True)
        refined = project.model_copy(deep=True)
        refined.study.mesh_size_mm /= 1.5**i
        mesh,quality = generate_mesh(run,transfer,refined,cancel)
        field = {"nodes":mesh["nodes"],"boundary_triangles":exterior_triangles(mesh["elements"]),
                 "schema_version":1,"location":"node","coordinate_frame":"world","mesher":mesh.get("mesher"),
                 "stress_convention":"extrapolated/averaged nodal von Mises; screening field"}
        metrics,observed,balanced = {},[],True
        temperature = None
        if project.study.type in THERMAL:
            (prescribed,flux),frames = run_solver(run,mesh,project,"thermal",cancel,thermal=True)
            temperature = parse_scalar_dat(run/"thermal.dat","temperatures")
            heat_reactions = parse_scalar_dat(run/"thermal.dat","heat generation")
            if set(temperature)!=set(map(int,mesh["nodes"])) or not set(prescribed).issubset(heat_reactions):
                raise RuntimeError("Incomplete thermal field or boundary heat-flow output")
            net = sum(flux.values())+sum(heat_reactions.values())
            scale = max(sum(abs(v) for v in flux.values()),sum(abs(v) for v in heat_reactions.values()),1e-12)
            error = abs(net)/scale
            point = project.study.probe_point_mm
            probe = float(interpolate_probe(mesh,temperature,point)) if point is not None else max(temperature.values())
            metrics.update({"max_temperature_c":max(temperature.values()),"min_temperature_c":min(temperature.values()),
                            "probe_temperature_c":probe,"heat_balance_error":error,
                            "heat_reactions_w":{n:v/1000 for n,v in heat_reactions.items()}})
            field["temperature_c"] = temperature
            observed.append(probe+273.15)
            balanced = error<=.01
        if project.study.type in ("modal","buckling"):
            _,frames = run_solver(run,mesh,project,"analysis",cancel)
            eigenvalues = parse_eigenvalues(run/"analysis.dat",project.study.type)
            modes = [f["values"] for f in frames if f["name"]=="DISP"]
            if len(eigenvalues)<project.study.mode_count or len(modes)<project.study.mode_count:
                raise RuntimeError("Solver did not return every requested eigenmode")
            if any(set(mode)!=set(map(int,mesh["nodes"])) for mode in modes):
                raise RuntimeError("Solver returned an incomplete eigenvector field")
            metric_name = "frequencies_hz" if project.study.type=="modal" else "buckling_factors"
            metrics[metric_name] = eigenvalues[:project.study.mode_count]
            field["modes"] = [{"index":n+1,"value":v,"vectors":modes[n]} for n,v in enumerate(metrics[metric_name])]
            field["displacements"] = modes[0]
            field["mode_amplitude"] = "arbitrary eigenvector normalization; not physical displacement"
            observed = metrics[metric_name]
            balanced = all(v>0 for v in observed)
        elif project.study.type in ("linear_static","thermal_stress"):
            boundary,frames = run_solver(run,mesh,project,"analysis",cancel,temperatures=temperature)
            mechanical,fields,outputs,equilibrium = mechanical_result(run,mesh,project,frames,boundary,transfer)
            metrics.update(mechanical);field.update(fields);observed.extend(outputs);balanced &= equilibrium
        if not observed or not np.isfinite(observed).all():
            raise RuntimeError("Missing or nonfinite study quantities")
        deltas = [abs(a-b)/max(abs(a),abs(b),1e-12) for a,b in zip(observed,previous)] if previous is not None else None
        converged = deltas is not None and max(deltas)<=project.study.convergence_tolerance
        iterations.append({"mesh_size_mm":refined.study.mesh_size_mm,"nodes":len(mesh["nodes"]),"elements":len(mesh["elements"]),
                           **metrics,"quantity_changes":deltas,"displacement_change":deltas[-len(outputs)] if deltas and project.study.type in ("linear_static","thermal_stress") else None,
                           "mesh_quality":quality})
        write_json(run/"normalized-field.json",field)
        write_json(run/"metrics.json",iterations[-1])
        previous = observed
        if converged and i >= min(2,project.study.refinement_steps-1):
            break
    findings=[]
    mappings = {"max_displacement":("max_displacement_mm",False),"max_temperature":("max_temperature_c",False),
                "min_frequency":("frequencies_hz",True),"min_buckling_factor":("buckling_factors",True),
                "max_stress":("stress_probe_mpa",False)}
    for requirement in project.requirements:
        if not requirement.enabled or requirement.kind not in mappings:
            continue
        metric,minimum = mappings[requirement.kind]
        value = metrics.get(metric)
        if isinstance(value,list):value=min(value)
        if value is None:
            findings.append({"id":requirement.id,"status":"not_evaluated","value":None,"limit":requirement.value,"passed":False})
        else:
            passed = value>=requirement.value if minimum else value<=requirement.value
            findings.append({"id":requirement.id,"status":"passed" if passed else "failed","value":value,"limit":requirement.value,"passed":bool(passed)})
    if not converged:warnings.append("Mesh refinement did not converge within the allowed steps")
    if not balanced:warnings.append("Balance or eigenvalue applicability check failed")
    if "max_stress_mpa" in metrics:
        warnings.append("Peak stress at supports/sharp edges may be singular; peak stress alone does not establish strength.")
        if project.study.stress_probe_mm is None:
            warnings.append("No named stress probe: stress is a screening field; convergence concerns displacement only.")
        if metrics["displacement_to_extent_ratio"]>.05:
            warnings.append("Displacement exceeds 5% of model extent: outside this adapter's small-deformation screening envelope.")
    if project.study.type in ("modal","buckling"):
        warnings.append("Mode amplitudes are arbitrary. Mesh comparison currently uses mode order; repeated/crossing modes require shape correlation.")
    if project.study.type=="buckling":warnings.append("Ideal linear eigenvalue buckling does not predict imperfect nonlinear collapse.")
    unevaluated = any(f["status"]=="not_evaluated" for f in findings)
    state = "inconclusive" if not converged or not balanced or unevaluated else "failed" if any(not f["passed"] for f in findings) else "passed"
    artifacts = {"study_package":f"studies/{key}/study-package.json","region_map":f"studies/{key}/region-map.json",
                 "geometry/part.step":f"studies/{key}/geometry/part.step"}
    for j in range(len(iterations)):
        for path in (destination/f"mesh-{j}").iterdir():
            if path.is_file():artifacts[f"mesh_{j}/{path.name}"]=str(path.relative_to(store.cache))
    result = {"schema_version":1,"type":project.study.type,"key":key,"geometry_key":geometry_key(project),"revision":project.revision,
              "state":state,"validation_level":"numerical_only","physical_validation":"not_performed",**metrics,
              "iterations":iterations,"converged":converged,"findings":findings,"warnings":warnings,"solver":"CalculiX",
              "solver_identity":solver_identity(),"mesher":mesh.get("mesher"),"material":project.material.model_dump(),
              "study":project.study.model_dump(),"elapsed_seconds":time.monotonic()-start,"completed_at":now(),
              "field_artifact":f"studies/{key}/field.json","artifacts":artifacts,
              "logs":[v for k,v in artifacts.items() if k.endswith(".log")]}
    result["input_deck_sha256"] = {k:hashlib.sha256(store.artifact(v).read_bytes()).hexdigest() for k,v in artifacts.items() if k.endswith(".inp")}
    write_json(destination/"field.json",field)
    write_json(destination/"result.json",result)
    write_json(store.cache/"last-study.json",result)
    return result
