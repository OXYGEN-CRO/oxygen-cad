"""Real nonlinear solver checks against independent reference equations."""
import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError
from scipy.integrate import simpson, solve_bvp

from o3d.analysis import nonlinear_output_times, nonlinear_result, validate_study
from o3d.model import LoadPoint, Material, PlasticPoint, Project, Requirement, Study
from o3d.solver import simulate


def plastic_setup(store, increments=4):
    p=store.load()
    p.study.type="nonlinear_static"
    p.study.large_deformation=False
    p.study.mesh_size_mm=4
    p.study.refinement_steps=3
    p.study.nonlinear_increments=increments
    p.study.stress_probe_mm=None
    p.study.probe_point_mm=(80,0,0)
    p.study.forces[0].vector_n=(10800,0,0)
    p.study.load_path=[LoadPoint(time=0,factor=0),LoadPoint(time=.5,factor=1),LoadPoint(time=1,factor=0)]
    p.material.poisson_ratio=0
    p.material.plastic_curve=[PlasticPoint(stress_mpa=250,plastic_strain=0),PlasticPoint(stress_mpa=350,plastic_strain=.01)]
    p.material.plastic_curve_source="Synthetic analytical fixture: von Mises stress versus equivalent plastic strain, not production data"
    p.requirements=[Requirement(id="peak_extension",kind="max_displacement",value=.2)]
    return p


def test_plastic_load_unload_peak_residual_step_sensitivity_and_exports(store):
    p=plastic_setup(store)
    result=simulate(store,p)
    # Uniaxial bar: stress=10800/40=270 MPa, Ep=10000 MPa,
    # plastic strain=(270-250)/10000=.002. Clamp end effects are small.
    expected_residual=80*.002
    expected_peak=expected_residual+80*270/200000
    peak=next(v for v in result["increments"] if v["time"]==.5)
    assert peak["probe_displacement_mm"]==pytest.approx(expected_peak,rel=.03)
    assert result["final_max_displacement_mm"]==pytest.approx(expected_residual,rel=.04)
    assert result["max_equivalent_plastic_strain"]>.0018
    assert result["completed_load_path"] and result["converged"]
    assert result["force_balance_error"]<.01 and result["moment_balance_error"]<.01
    assert result["state"]=="failed"  # Peak exceeds .2 even though unloaded residual does not.
    assert result["findings"][0]["value"]>.25
    assert [v["time"] for v in result["increments"]]==[.25,.5,.75,1]
    for increment in result["increments"]:
        field=json.loads(store.artifact(increment["field_artifact"]).read_text())
        assert len(field["equivalent_plastic_strain"])==len(field["nodes"])
        assert field["time"]==increment["time"]
    p.study.nonlinear_increments=8
    refined=simulate(store,p)
    assert refined["max_displacement_mm"]==pytest.approx(result["max_displacement_mm"],rel=.01)
    assert refined["final_max_displacement_mm"]==pytest.approx(result["final_max_displacement_mm"],rel=.01)
    assert len(refined["increments"])==8
    from o3d.artifacts import export_bundle
    import zipfile
    release=export_bundle(store,p)
    with zipfile.ZipFile(store.artifact(release["artifact"])) as archive:
        assert archive.read("analysis/mesh_2/increment-8.json")
        assert b"*PLASTIC,HARDENING=ISOTROPIC" in archive.read("analysis/mesh_2/analysis.inp")
        saved=json.loads(archive.read("analysis/result.json"))
        assert saved["field_artifact"]=="field.json"
        for increment in saved["increments"]:
            assert archive.read("analysis/"+increment["field_artifact"])
        report=json.loads(archive.read("report.json"))
        assert archive.read(report["analysis"]["increments"][-1]["field_artifact"])


def test_large_deformation_against_independent_elastica_boundary_value_solution(store):
    p=store.load()
    p.study.type="nonlinear_static"
    p.study.large_deformation=True
    p.study.forces[0].vector_n=(0,0,-1000)
    p.study.stress_probe_mm=None
    p.study.probe_point_mm=(80,0,0)
    p.study.nonlinear_increments=4
    p.study.mesh_size_mm=4
    p.requirements=[]
    result=simulate(store,p)
    # Inextensible planar elastica, dead vertical tip load. Independent BVP,
    # not a second implementation of the FE deck or the small-deflection formula.
    length,EI,load=80,200000*10*4**3/12,1000
    s=np.linspace(0,length,400)
    solution=solve_bvp(lambda x,y:np.stack((y[1],-load/EI*np.cos(y[0]))),
                       lambda a,b:np.array([a[0],b[1]]),s,np.zeros((2,len(s))),tol=1e-8)
    assert solution.success
    theta=solution.sol(s)[0]
    expected=np.hypot(simpson(np.cos(theta),x=s)-length,simpson(np.sin(theta),x=s))
    assert result["probe_displacement_mm"]==pytest.approx(expected,rel=.02)
    assert result["max_displacement_mm"]<.99*load*length**3/(3*EI)
    assert result["state"]=="passed" and result["completed_load_path"]
    assert result["moment_balance_error"]<.01
    assert result["displacement_to_extent_ratio"]>.05
    assert not any("outside this adapter" in w for w in result["warnings"])


def test_nonlinear_input_and_incomplete_history_refusal(store,tmp_path):
    p=plastic_setup(store)
    p.material.plastic_curve_source=None
    assert any("source" in error for error in validate_study(p))
    p.study.type="linear_static"
    assert any("silently linearize" in error for error in validate_study(p))
    assert any("histories" in error for error in validate_study(p))
    p=store.load();p.study.type="nonlinear_static";p.study.large_deformation=False
    assert any("requires large deformation" in error for error in validate_study(p))
    for curve in [[], [{"time":0,"factor":1},{"time":1,"factor":1}], [{"time":0,"factor":0},{"time":.5,"factor":1}]]:
        if curve:
            with pytest.raises(ValidationError):Study(load_path=curve)
    material=store.load().material.model_dump()
    for points in [[{"stress_mpa":250,"plastic_strain":.01}],
                   [{"stress_mpa":250,"plastic_strain":0},{"stress_mpa":240,"plastic_strain":.01}]]:
        with pytest.raises(ValidationError):Material.model_validate(material|{"plastic_curve":points})
    (tmp_path/"analysis.dat").write_text("displacements (vx,vy,vz) for set ALLNODES and time 0.5\n1 0 0 0\n")
    (tmp_path/"analysis.sta").write_text("1 1 1 2 .5 .5 .5\n")
    with pytest.raises(RuntimeError,match="complete requested"):
        nonlinear_result(tmp_path,{},plastic_setup(store),[],None,None,store)
    (tmp_path/"analysis.sta").write_text("1 2 1 2 1 1 .5\n")
    with pytest.raises(RuntimeError,match="final increment"):
        nonlinear_result(tmp_path,{},plastic_setup(store),[],None,None,store)
    (tmp_path/"analysis.dat").write_text("displacements (vx,vy,vz) for set ALLNODES and time 1.0\n1 0 0 0\n")
    with pytest.raises(RuntimeError,match="missing requested"):
        nonlinear_result(tmp_path,{},plastic_setup(store),[],None,None,store)
