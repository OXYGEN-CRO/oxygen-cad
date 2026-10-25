"""Lightweight exact solver/source fingerprints without importing numerical libraries."""
from __future__ import annotations
from importlib.metadata import version,distribution,PackageNotFoundError
from pathlib import Path


def fingerprint_file(path):
    from .simulation_solver_profile import file_hash
    return file_hash(path)


def software_fingerprint(study_type,element_family="C3D4"):
    root=Path(__file__).parent
    sources={name:fingerprint_file(root/name) for name in ("simulation_models.py","simulation_loading.py","simulation_fem.py","simulation_worker.py","simulation_search.py","extended_solver.py","simulation_mesh.py","simulation_mesh_worker.py","simulation_units.py","simulation_quadratic.py","simulation_quadratic_thermal.py","simulation_thermoelastic.py","simulation_eigen.py","simulation_buckling.py","simulation_assembly.py","simulation_bonding.py","simulation_mortar.py","simulation_contact.py","simulation_contact_evidence.py","simulation_contact_stability.py","simulation_contact_global.py","simulation_solver_profile.py","data/calculix-contact.json","simulation_mode_tracking.py","mode_tracking.py","quadratic.py","thermal_exchange.py","analysis_mesh.py","mesh_quality_worker.py","solver.py","analysis.py","domains/simulation.py","simulation_provenance.py")}
    sources['simulation_native_tangent.py']=fingerprint_file(root/'simulation_native_tangent.py')
    sources['simulation_accepted_bulk.py']=fingerprint_file(root/'simulation_accepted_bulk.py')
    sources['simulation_material_derivatives.py']=fingerprint_file(root/'simulation_material_derivatives.py')
    sources['simulation_material_history.py']=fingerprint_file(root/'simulation_material_history.py')
    for name in ('simulation_flow.py','simulation_flow_evidence.py','simulation_flow_convergence.py','simulation_flow_case.py','simulation_flow_mesh.py','simulation_flow_fields.py','simulation_flow_profile.py','data/openfoam-laminar.json'):
        sources[name]=fingerprint_file(root/name)
    packages={}
    for name in ("numpy","scipy"):
        try:packages[name]=version(name)
        except PackageNotFoundError:packages[name]="unavailable"
    result={"source_sha256":sources,"packages":packages,"element_family":element_family}
    if study_type=='general_cfd':
        from .simulation_flow_profile import profile
        import os
        result['openfoam_runtime']=profile()['identity'] if os.environ.get('O3D_OPENFOAM_ROOT') else 'unavailable'
    if study_type=="nonlinear_static" or element_family=="C3D10":
        # Resolve a declared backend, never execute a project-controlled command.
        import os
        from .solver import solver_binary
        try:binary=solver_binary()
        except RuntimeError:binary=None
        result["calculix_binary_sha256"]=fingerprint_file(binary) if binary and Path(binary).is_file() else "unavailable"
        if binary and Path(binary).is_file():
            from .simulation_solver_profile import validated_build,loaded_libraries
            build=validated_build(binary)
            if build:
                result['calculix_build_receipt_sha256']=build['receipt_sha256']
                result['calculix_runtime_libraries_sha256']=build['runtime_libraries_sha256']
                from .simulation_solver_profile import validated_material_probe
                probe=validated_material_probe(build)
                result['material_replay_tool']={k:v for k,v in probe.items() if k not in ('binary','runtime_library_path')} if probe else 'unavailable'
            else:
                tools=root.parents[1]/'.tools/root/usr/lib/x86_64-linux-gnu'
                paths=[str(tools)] if tools.exists() else []
                if os.environ.get('LD_LIBRARY_PATH'):paths.append(os.environ['LD_LIBRARY_PATH'])
                result['calculix_runtime_libraries_sha256']=loaded_libraries(binary,os.pathsep.join(paths)) or 'unavailable'
    if element_family=="C3D10" and study_type in {"linear_static","nonlinear_static","modal","buckling"}:
        runtime={}
        patterns={"numpy":["numpy/_core/_umath_linalg*.so","numpy/_core/_multiarray_umath*.so","numpy.libs/*","numpy/.dylibs/*"],
                  "scipy":["scipy/linalg/_flapack*.so","scipy/sparse/linalg/_eigen/arpack/_arpack*.so","scipy/sparse/linalg/_dsolve/_superlu*.so","scipy.libs/*","scipy/.dylibs/*"]}
        for package,globs in patterns.items():
            try:prefix=Path(distribution(package).locate_file(""))
            except PackageNotFoundError:continue
            for pattern in globs:
                for path in sorted(prefix.glob(pattern)):
                    if path.is_file():runtime[str(path.relative_to(prefix))]=fingerprint_file(path)
        result["scientific_runtime_sha256"]=runtime
    if element_family=="C3D10":
        from .solver import mesher_python
        python=Path(mesher_python());prefix=python.parent.parent
        runtime={"python":fingerprint_file(python) if python.is_file() else "unavailable"}
        for pattern in ("lib/libgmsh.so*","lib/python*/site-packages/gmsh.py","lib/python*/site-packages/netgen*/*.so*","lib/python*/site-packages/gmsh*.dist-info/METADATA","lib/python*/site-packages/netgen*.dist-info/METADATA"):
            for path in sorted(prefix.glob(pattern)):
                if path.is_file():runtime[str(path.relative_to(prefix))]=fingerprint_file(path)
        result["mesher_runtime_sha256"]=runtime
    return result
