"""CalculiX adapter for canonical structural solids.

An explicit, bounded subset: C3D4/C3D10, isotropic elasticity/J2 tabular isotropic
hardening, world-axis zero supports and dead nodal loads. Unsupported contact,
connector and follower semantics refuse before writing an input deck.
"""
from __future__ import annotations
import re
from pathlib import Path
import numpy as np
from .solver import run_process, solver_binary, parse_stress, calculix_number
from .simulation_fem import System
from .simulation_units import density_tonne_mm3


def write_calculix(path, system: System, case, nonlinear=True, increment_scale=1.):
    study=system.study
    if max(system.ids)>100000 or any(e.id>150000 for e in system.mesh.elements):
        raise ValueError("CalculiX label budget exceeded; compact imported node/element labels before solving")
    if study.interfaces or study.bolts or study.settings.inertial_relief:
        raise ValueError("CalculiX nonlinear adapter does not yet translate interfaces, bolts or inertial relief")
    if any(s.kind not in {"fixed","partial"} for s in case.supports):
        raise ValueError("CalculiX adapter accepts fixed/world-axis partial supports only")
    if not case.supports:raise ValueError("CalculiX nonlinear solve requires mechanical supports")
    if case.thermal:raise ValueError("Nonlinear thermal transfer is not implemented by this adapter")
    if hasattr(system,"validate_supports"):system.validate_supports(case)
    force=system.loads(case)
    lines=["*HEADING","Oxygen versioned study: mm N s tonne; dead-load small-strain or NLGEOM", "*NODE"]
    for n in system.ids:
        lines.append(f"{n},"+",".join(calculix_number(x) for x in system.mesh.nodes[n]))
    for body_index,body in enumerate(sorted(system.assignments)):
        elements=[e for e in system.mesh.elements if e.body_id==body]
        name=f"BODY{body_index}"
        lines.append(f"*ELEMENT,TYPE={system.mesh.element_family},ELSET={name}")
        lines.extend(f"{e.id},"+",".join(map(str,e.nodes)) for e in elements)
        material=system.assignments[body]
        lines.extend([f"*MATERIAL,NAME=MAT{body_index}","*ELASTIC",f"{calculix_number(material.youngs_modulus_mpa)},{calculix_number(material.poisson_ratio)}"])
        if material.density_g_cm3:
            lines.extend(["*DENSITY",calculix_number(density_tonne_mm3(material.density_g_cm3))])
        if nonlinear and material.plastic_curve:
            lines.append("*PLASTIC")
            lines.extend(f"{calculix_number(stress)},{calculix_number(strain)}" for stress,strain in material.plastic_curve)
        lines.append(f"*SOLID SECTION,ELSET={name},MATERIAL=MAT{body_index}")
    lines.extend(["*NSET,NSET=ALLNODES"])
    for i in range(0,len(system.ids),16):lines.append(",".join(map(str,system.ids[i:i+16])))
    lines.append("*BOUNDARY")
    fixed=set()
    for support in case.supports:
        for i in system.region_nodes(support.region_id):
            for axis,active in enumerate((True,True,True) if support.kind=="fixed" else support.components,1):
                if active:
                    lines.append(f"{system.ids[i]},{axis},{axis},0")
                    fixed.add(system.ids[i])
    lines.extend(["*NSET,NSET=SUPPORTS"])
    for i in range(0,len(fixed),16):lines.append(",".join(map(str,sorted(fixed)[i:i+16])))
    increment=study.settings.nonlinear_initial_increment*increment_scale
    history=case.history or [(0.,0.),(1.,1.)]
    if history[0]!=(0.,0.):raise ValueError("Nonlinear load history must start at time zero and zero multiplier")
    for (previous_time,_),(time,factor) in zip(history,history[1:]):
        duration=time-previous_time
        lines.extend(["*STEP,INC=1000"+(",NLGEOM" if study.settings.large_deformation else ""),"*STATIC",",".join(calculix_number(v) for v in (increment*duration,duration,1e-7*duration,increment*duration)),"*CLOAD,OP=NEW"])
        for i,n in enumerate(system.ids):
            for axis in range(3):
                if force[i*3+axis]!=0:lines.append(f"{n},{axis+1},{calculix_number(force[i*3+axis]*factor)}")
        lines.extend(["*NODE PRINT,NSET=ALLNODES,FREQUENCY=1","U","*NODE PRINT,NSET=ALLNODES,FREQUENCY=1","RF","*EL PRINT,ELSET=BODY0,FREQUENCY=1","S,PEEQ,ENER","*NODE FILE,FREQUENCY=1","U,RF","*EL FILE,FREQUENCY=1","S,PEEQ","*END STEP"])
    path.write_text("\n".join(lines)+"\n")
    return force


def _last_increment(path,expected_time=1.):
    rows=[]
    for line in path.read_text(errors="replace").splitlines():
        values=line.split()
        if len(values)>=7 and all(x.isdigit() for x in values[:3]):
            try:rows.append({"step":int(values[0]),"increment":int(values[1]),"attempt":values[2],"iterations":int(values[3]),"total_time":float(values[4]),"step_time":float(values[5]),"increment_time":float(values[6])})
            except ValueError:continue
    if not rows or abs(rows[-1]["total_time"]-expected_time)>max(1e-7,expected_time*1e-6):
        raise RuntimeError("CalculiX did not complete the entire load step; partial results refused")
    return rows


def _convergence(path):
    rows=[]
    if not path.exists():return rows
    for line in path.read_text(errors="replace").splitlines():
        parts=line.split()
        if len(parts)>=7 and all(p.isdigit() for p in parts[:4]):
            try:rows.append({"step":int(parts[0]),"increment":int(parts[1]),"attempt":int(parts[2]),"iteration":int(parts[3]),"contact_elements":int(parts[4]),"force_residual_percent":float(parts[5]),"displacement_correction_percent":float(parts[6])})
            except ValueError:pass
    return rows


def _parse_node_print(path):
    displacement,reactions={},{}
    target=None
    started=False
    for line in path.read_text(errors="replace").splitlines():
        lower=line.lower().strip()
        if lower.startswith("displacements ("):
            displacement={};target=displacement;started=False
        elif lower.startswith("forces ("):
            reactions={};target=reactions;started=False
        elif target is not None:
            parts=line.split()
            if len(parts)==4 and parts[0].isdigit():
                target[int(parts[0])]=[float(v.replace("D","E")) for v in parts[1:]];started=True
            elif started:
                target=None
    if not displacement or not reactions:raise RuntimeError("Missing CalculiX displacement/reaction fields")
    return displacement,reactions


def _displacement_history(path):
    rows=[];current=None
    for line in path.read_text(errors="replace").splitlines():
        if line.strip().lower().startswith("displacements ("):
            current={"time":float(line.split()[-1]),"max_displacement_mm":0.}
            rows.append(current)
        elif current is not None:
            parts=line.split()
            if len(parts)==4 and parts[0].isdigit():current["max_displacement_mm"]=max(current["max_displacement_mm"],float(np.linalg.norm([float(v.replace("D","E")) for v in parts[1:]])))
            elif parts:current=None
    if not rows:raise RuntimeError("Missing load-displacement history")
    return rows


def calculix_solve(system,case,directory: Path,cancel=None,nonlinear=True,increment_scale=1.):
    directory.mkdir(parents=True,exist_ok=True)
    force=write_calculix(directory/"analysis.inp",system,case,nonlinear,increment_scale)
    run_process([solver_binary(),"analysis"],directory,directory/"solver.log",cancel,timeout=240)
    log=(directory/"solver.log").read_text(errors="replace")
    if "*ERROR" in log.upper() or "JOB FINISHED" not in log.upper():
        raise RuntimeError("CalculiX did not report a completed job; inspect solver.log")
    increments=_last_increment(directory/"analysis.sta",case.history[-1][0] if case.history else 1.)
    convergence=_convergence(directory/"analysis.cvg")
    displacement,reactions=_parse_node_print(directory/"analysis.dat")
    if set(displacement)!=set(system.ids):raise RuntimeError("CalculiX result misses mesh nodes")
    if set(reactions)!=set(system.ids):raise RuntimeError("CalculiX result misses nodal force output")
    if not all(np.isfinite(values).all() for field in (displacement,reactions) for values in field.values()):raise RuntimeError("Nonfinite CalculiX output")
    stress=parse_stress(directory/"analysis.frd")
    u=np.array([displacement[n] for n in system.ids]).ravel()
    applied=force.reshape(-1,3)*(case.history[-1][1] if case.history else 1.)
    reacted=np.zeros_like(applied)
    constrained=np.zeros_like(applied,dtype=bool)
    for support in case.supports:
        components=(True,True,True) if support.kind=="fixed" else support.components
        for i in system.region_nodes(support.region_id):
            for axis,active in enumerate(components):
                if active:
                    if system.ids[i] not in reactions:
                        raise RuntimeError("CalculiX result misses a constrained-node force")
                    # RF is reaction + applied nodal force, not reaction alone.
                    # All translated loads here are CLOADs; subtract their final
                    # history-scaled values on each constrained component.
                    reacted[i,axis]=reactions[system.ids[i]][axis]-applied[i,axis]
                    constrained[i,axis]=True
    scale=max(np.linalg.norm(applied,axis=1).sum(),np.linalg.norm(force.reshape(-1,3),axis=1).sum(),1e-12)
    force_error=float(np.linalg.norm((applied+reacted).sum(axis=0))/scale)
    coordinates=system.xyz+(u.reshape(-1,3) if system.study.settings.large_deformation else 0)
    length=max(np.linalg.norm(np.ptp(coordinates,axis=0)),1e-12)
    moment_error=float(np.linalg.norm(np.cross(coordinates,applied+reacted).sum(axis=0))/(scale*length))
    last_by_increment={(r["step"],r["increment"]):r for r in convergence}
    residual=max((abs(r["force_residual_percent"])/100 for r in last_by_increment.values()),default=None)
    if residual is None and not nonlinear:
        raw=np.array([reactions[n] for n in system.ids])
        residual=float(np.linalg.norm((raw-applied)[~constrained])/max(np.linalg.norm(applied[~constrained]),1e-12))
    if residual is None:raise RuntimeError("CalculiX convergence residuals unavailable; no verification inferred from process exit")
    plastic_values=[]
    active=False
    for line in (directory/"analysis.frd").read_text(errors="replace").splitlines():
        if line.startswith(" -4"):active="PE" in line and "DISP" not in line
        elif active and line.startswith(" -1"):
            values=re.findall(r"[+-]?\d\.\d+(?:[ED][+-]\d+)",line[13:])
            plastic_values.extend(float(v.replace("D","E")) for v in values)
    history=_displacement_history(directory/"analysis.dat")
    result = {"load_displacement_history":history,"values":{"max_displacement_mm":max(h["max_displacement_mm"] for h in history),"final_displacement_mm":float(np.linalg.norm(u.reshape(-1,3),axis=1).max()),"max_von_mises_mpa":max(stress.values()),"max_equivalent_plastic_strain":max(plastic_values,default=0.)},"checks":{"residual":float(residual),"force_balance_error":force_error,"moment_balance_error":moment_error,"increments":increments,"nonlinear_convergence":convergence},"field":{"nodes":{n:system.xyz[i].tolist() for i,n in enumerate(system.ids)},"displacements_mm":displacement,"nodal_extrapolated_von_mises_mpa":stress,"support_reactions_n":{n:reacted[i].tolist() for i,n in enumerate(system.ids)},"solver_external_forces_n":reactions,"applied_nodal_forces_n":{n:applied[i].tolist() for i,n in enumerate(system.ids)}},"solver":"CalculiX","reaction_convention":"support reaction = CalculiX RF - final applied CLOAD on each constrained DOF","warnings":["Nodal stress is extrapolated; fixture peaks can be singular.","J2 isotropic hardening follows supplied true-stress/plastic-strain data; no cyclic/rate calibration is inferred."]}
    from .analysis import parse_frd,parse_vector_history
    frames=parse_frd(directory/"analysis.frd")
    vectors=parse_vector_history(directory/"analysis.dat")
    envelope={n:0. for n in system.ids}
    increment_checks=[];probes=[]
    load_history=case.history or [(0.,0.),(1.,1.)]
    for row in history:
        when=row["time"]
        candidates=[f for f in frames if f["name"]=="STRESS" and np.isclose(f["time"],when,rtol=1e-5,atol=1e-8)]
        if len(candidates)!=1 or set(candidates[0]["values"])!=set(system.ids):raise RuntimeError("Incomplete or ambiguous increment stress field")
        stress_frame=candidates[0]["values"]
        for n,values in stress_frame.items():
            if len(values)<6:raise RuntimeError("Incomplete increment stress tensor")
            sx,sy,sz,xy,yz,zx=values[:6]
            equivalent=float(np.sqrt(.5*((sx-sy)**2+(sy-sz)**2+(sz-sx)**2)+3*(xy**2+yz**2+zx**2)))
            envelope[n]=max(envelope[n],equivalent)
        row["max_von_mises_mpa"]=max(float(np.sqrt(.5*((v[0]-v[1])**2+(v[1]-v[2])**2+(v[2]-v[0])**2)+3*sum(x*x for x in v[3:6]))) for v in stress_frame.values())
        entry=vectors.get(when)
        if entry is None or any(set(entry.get(kind,{}))!=set(system.ids) for kind in ("displacements","reactions")):raise RuntimeError("Incomplete increment displacement/force history")
        factor=float(np.interp(when,[p[0] for p in load_history],[p[1] for p in load_history]))
        loaded=force.reshape(-1,3)*factor
        raw=np.array([entry["reactions"][n] for n in system.ids])
        supported=np.where(constrained,raw-loaded,0.)
        positions=system.xyz+np.array([entry["displacements"][n] for n in system.ids]) if system.study.settings.large_deformation else system.xyz
        increment_scale=max(float(np.linalg.norm(loaded,axis=1).sum()),float(np.linalg.norm(force.reshape(-1,3),axis=1).sum()),1e-12)
        increment_length=max(float(np.linalg.norm(np.ptp(positions,axis=0))),1e-12)
        increment_checks.append({"time_s":when,"load_factor":factor,"force_balance_error":float(np.linalg.norm((loaded+supported).sum(axis=0))/increment_scale),"moment_balance_error":float(np.linalg.norm(np.cross(positions,loaded+supported).sum(axis=0))/(increment_scale*increment_length))})
        if hasattr(system,"displacement_probe"):
            probe=system.displacement_probe(entry["displacements"])
            if probe is not None:
                probe["time_s"]=when;probes.append(probe)
    result["values"]["max_von_mises_mpa"]=max(envelope.values())
    result["values"]["final_von_mises_mpa"]=max(stress.values())
    result["field"].update({"nodal_von_mises_envelope_mpa":envelope,"field_state":"final","field_time_s":history[-1]["time"]})
    result["checks"].update({"increment_equilibrium":increment_checks,"force_balance_error":max(x["force_balance_error"] for x in increment_checks),"moment_balance_error":max(x["moment_balance_error"] for x in increment_checks),"residual_method":"CalculiX nonlinear convergence records" if nonlinear else "Free-component nodal equilibrium from printed RF and applied CLOAD; output precision can limit the reported residual"})
    if system.mesh.element_family=="C3D10":
        result["mesh_quality"]=system.quality_record
        result["values"]["mass_g"]=system.mass_g
        result["checks"]["displacement_to_extent_ratio"]=result["values"]["max_displacement_mm"]/max(float(np.linalg.norm(np.ptp(system.xyz,axis=0))),1e-12)
    if probes:
        peak=max(probes,key=lambda p:p["magnitude_mm"])
        result["values"]["probe_displacement_mm"]=peak["magnitude_mm"]
        result["displacement_probe"]={"final":probes[-1],"peak":peak,"history":probes}
    return result
