"""Generate an allowlisted steady laminar incompressible OpenFOAM case."""
from pathlib import Path
import numpy as np
from .simulation_flow_mesh import write_mesh,foam_header,number


def write_case(mesh,material,case,settings,directory,residual_tolerance):
    directory=Path(directory)
    if settings is None:raise ValueError('Declare the fluid-volume idealization and steady laminar incompressible model')
    if case.supports or case.loads or case.thermal or case.history or any(case.initial_velocity_mm_s):
        raise ValueError('Steady flow does not translate structural, thermal, transient or initial-velocity conditions')
    if not case.flow:raise ValueError('Explicit fluid boundary conditions are required')
    if material.density_g_cm3 is None or material.dynamic_viscosity_pa_s is None:
        raise ValueError('Fluid density and dynamic viscosity with provenance are required')
    if material.plastic_curve:raise ValueError('Plastic solid curves are not a Newtonian fluid law')
    if not any(b.kind=='pressure' for b in case.flow) and settings.pressure_reference_pa is None:
        raise ValueError('A closed fluid volume requires an explicit gauge-pressure reference')
    density=material.density_g_cm3*1000;viscosity=material.dynamic_viscosity_pa_s
    mesh_data=write_mesh(mesh,case.flow,directory)
    (directory/'system').mkdir();(directory/'0').mkdir()
    def write(path,kind,body):
        path=directory/path;path.write_text(foam_header(kind,path.name)+body+'\n')
    maximum=settings.maximum_iterations
    write('system/controlDict','dictionary',f'''application simpleFoam;
startFrom startTime; startTime 0; stopAt endTime; endTime {maximum}; deltaT 1;
writeControl timeStep; writeInterval 1; purgeWrite 1;
writeFormat ascii; writePrecision 17; writeCompression off; timeFormat general; timePrecision 12;
runTimeModifiable false; functions {{}};''')
    write('system/fvSchemes','dictionary','''ddtSchemes { default steadyState; }
gradSchemes { default leastSquares; }
divSchemes { default none; div(phi,U) bounded Gauss linearUpwind grad(U); div((nuEff*dev2(T(grad(U))))) Gauss linear; }
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
fluxRequired { default no; p; }''')
    write('system/fvSolution','dictionary',f'''solvers
{{
p {{ solver GAMG; tolerance 1e-12; relTol 0.01; smoother GaussSeidel; }}
U {{ solver smoothSolver; smoother symGaussSeidel; tolerance 1e-12; relTol 0.01; }}
}}
SIMPLE {{ nNonOrthogonalCorrectors 2; consistent no;
pRefCell 0; pRefValue {number((settings.pressure_reference_pa or 0)/density)};
residualControl {{ p {number(residual_tolerance)}; U {number(residual_tolerance)}; }} }}
relaxationFactors {{ fields {{ p 0.3; }} equations {{ U 0.7; }} }}''')
    write('constant/transportProperties','dictionary',f'transportModel Newtonian;\nnu [0 2 -1 0 0 0 0] {number(viscosity/density)};')
    write('constant/turbulenceProperties','dictionary','simulationType laminar;')
    by_id={b.id:b for b in case.flow};u=[];p=[]
    for patch in mesh_data['patches']:
        boundary=by_id[patch['boundary_id']];kind=boundary.kind;name=patch['name']
        if kind in {'moving_wall','velocity','no_slip'}:
            velocity=boundary.velocity_m_s if kind!='no_slip' else (0,0,0)
            if kind=='moving_wall':
                for face in mesh_data['faces'][patch['start_face']:patch['start_face']+patch['face_count']]:
                    points=mesh_data['coordinates_m'][face['nodes']];normal=np.cross(points[1]-points[0],points[2]-points[0]);normal/=np.linalg.norm(normal)
                    if abs(np.dot(velocity,normal))>1e-10*max(float(np.linalg.norm(velocity)),1e-12):
                        raise ValueError('A moving wall in a fixed steady fluid mesh must have tangential velocity')
            ub='type fixedValue; value uniform ('+' '.join(number(v) for v in velocity)+');';pb='type zeroGradient;'
        elif kind=='pressure':ub='type zeroGradient;';pb=f'type fixedValue; value uniform {number(boundary.pressure_gauge_pa/density)};'
        else:ub=pb='type symmetryPlane;'
        u.append(name+' { '+ub+' }');p.append(name+' { '+pb+' }')
    write('0/U','volVectorField','dimensions [0 1 -1 0 0 0 0];\ninternalField uniform (0 0 0);\nboundaryField\n{\n'+'\n'.join(u)+'\n}')
    write('0/p','volScalarField','dimensions [0 2 -2 0 0 0 0];\ninternalField uniform 0;\nboundaryField\n{\n'+'\n'.join(p)+'\n}')
    return mesh_data|{'density_kg_m3':density,'dynamic_viscosity_pa_s':viscosity,'kinematic_viscosity_m2_s':viscosity/density}
