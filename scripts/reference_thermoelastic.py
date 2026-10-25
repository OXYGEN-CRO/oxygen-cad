"""Independent scikit-fem sequential thermoelastic checker; no Oxygen imports.

The scalar phase and neutral mesh-format conversion come from reference_fem.
scikit-fem supplies all interpolation, derivatives, forms and quadrature.
"""
import json
import hashlib
from pathlib import Path
import sys

import numpy as np
from skfem import Basis, ElementVector, ElementTetP2, LinearForm, asm, condense, solve
from skfem.helpers import div
from skfem.models.elasticity import linear_elasticity, lame_parameters
from reference_fem import geometry, run as thermal_reference


def run(request):
    thermal=thermal_reference(request["thermal_request"])
    mesh,nodes,_,index,_,mid_edge,_=geometry(request["thermal_request"]["mesh"])
    order=request["mechanical_quadrature_order"]
    if order not in {4,6}:raise ValueError("Reference mechanical quadrature must be degree4 or6")
    scalar=Basis(mesh,ElementTetP2(),intorder=order)
    basis=Basis(mesh,ElementVector(ElementTetP2()),intorder=order)
    dofs={n:(basis.nodal_dofs[:,index[n]] if n in index else basis.edge_dofs[:,mid_edge[n]]) for n in nodes}
    scalar_dofs={n:int(scalar.nodal_dofs[0,index[n]] if n in index else scalar.edge_dofs[0,mid_edge[n]]) for n in nodes}
    values=np.zeros(scalar.N)
    for node,dof in scalar_dofs.items():values[dof]=thermal["field"]["temperature_c"][node]+273.15
    material=request["material"]
    elastic=float(material["youngs_modulus_mpa"]);nu=float(material["poisson_ratio"]);alpha=float(material["expansion_per_k"])
    if elastic<=0 or not -1<nu<.5 or not np.isfinite([elastic,nu,alpha]).all():raise ValueError("Invalid reference material")
    reference=float(request["stress_free_temperature_k"])
    stiffness=asm(linear_elasticity(*lame_parameters(elastic,nu)),basis)
    beta=elastic*alpha/(1-2*nu)
    @LinearForm
    def eigenstrain(v,w):return beta*w.change*div(v)
    load=asm(eigenstrain,basis,change=scalar.interpolate(values-reference))
    fixed=[]
    for node,components in request["node_constraints"].items():
        if len(components)!=3:raise ValueError("Invalid reference axis constraint")
        for axis,active in enumerate(components):
            if active:fixed.append(int(dofs[int(node)][axis]))
    fixed=np.unique(fixed);free=np.setdiff1d(np.arange(basis.N),fixed)
    u=solve(*condense(stiffness,load,D=fixed))
    residual=stiffness@u-load
    norm=float(np.linalg.norm(residual[free])/max(np.linalg.norm(load),1e-30))
    if not np.isfinite(u).all() or norm>1e-8:raise ValueError("Independent thermoelastic equation failed")
    return {"displacements_mm":{n:u[d].tolist() for n,d in dofs.items()},
            "temperature_k":{n:float(values[d]) for n,d in scalar_dofs.items()},
            "equation_residual":norm,"thermal_reference":thermal,"mechanical_quadrature_order":order,
            "scope":"Independent sequential linear isotropic FE on the declared mesh/material/constraints; no physical validation",
            "adapter_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


if __name__=="__main__":
    source=Path(sys.argv[1]);output=Path(sys.argv[2])
    output.write_text(json.dumps(run(json.loads(source.read_text())),allow_nan=False,indent=2)+"\n")
