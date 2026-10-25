"""Exact-node sequential thermal transfer and thermoelastic verification scales.

Thermal eigenstrain is an internal constitutive term, never an external CLOAD.
The integrated equivalent vector below only scales residual/equilibrium checks.
"""
from __future__ import annotations

import numpy as np
from .model import digest
from .quadratic import scalar_bounds
from .solver import Cancelled, calculix_number
from .thermal_exchange import volume_quadrature


def prepare_transfer(system, temperatures, cancel=None):
    if system.mesh.element_family!="C3D10" or system.study.type!="thermal_stress":
        raise ValueError("Sequential thermal transfer requires a canonical quadratic thermal-stress study")
    reference=system.study.stress_free_temperature_k
    if reference is None:raise ValueError("Thermal stress requires an explicit stress-free temperature in kelvin")
    if set(temperatures)!=set(system.ids):raise ValueError("Thermal transfer must contain every exact structural node, with no extra IDs")
    original=np.array([temperatures[n] for n in system.ids],dtype=float)
    connectivity=np.array([[system.index[n] for n in e.nodes] for e in system.mesh.elements])
    if scalar_bounds(original[connectivity])[0]<=0:
        raise ValueError("Transferred temperature cannot be bounded above absolute zero")
    serialized=np.array([float(calculix_number(t)) for t in original])
    solver_reference=float(calculix_number(reference))
    delta=serialized-solver_reference
    error=float(np.max(np.abs(delta-(original-reference))))
    excursion=float(np.max(np.abs(original-reference)))
    if error>excursion*1e-8:
        raise ValueError("Solver temperature serialization cannot resolve the thermal excursion; no zero-eigenstrain result is accepted")
    if scalar_bounds(serialized[connectivity])[0]<=0:
        raise ValueError("Serialized temperature cannot be bounded above absolute zero")
    coefficients={}
    for body,material in system.assignments.items():
        if material.expansion_per_k is None:
            raise ValueError(f"Body {body} requires a sourced thermal expansion coefficient")
        coefficients[body]=float(calculix_number(material.youngs_modulus_mpa))*float(calculix_number(material.expansion_per_k))/(1-2*float(calculix_number(material.poisson_ratio)))
    force=np.zeros((len(system.ids),3))
    worst=0.;maximum_strain=0.
    for start in range(0,len(connectivity),128):
        if cancel is not None and cancel.is_set():raise Cancelled("Simulation cancelled")
        inds=connectivity[start:start+128]
        xyz=system.xyz[inds]
        cells=system.mesh.elements[start:start+128]
        beta=np.array([coefficients[e.body_id] for e in cells])
        candidates=[]
        for order in (4,6):
            shape,gradients,measure=volume_quadrature(order)
            jac=np.einsum("eni,qnj->eqij",xyz-xyz[:,:1],gradients)
            weights=np.linalg.det(jac)*measure[None,:]
            if not np.isfinite(weights).all() or np.any(weights<=0):
                raise ValueError("Thermal-strain integration requires positive finite Jacobians")
            physical=np.einsum("qni,eqij->eqnj",gradients,np.linalg.inv(jac))
            change=np.einsum("qn,en->eq",shape,delta[inds])
            candidates.append(np.einsum("eqni,eq,eq,e->eni",physical,weights,change,beta))
        norms=np.linalg.norm(candidates[1],axis=(1,2))
        difference=np.linalg.norm(candidates[1]-candidates[0],axis=(1,2))/np.maximum(norms,1e-30)
        worst=max(worst,float(np.max(difference)))
        if np.max(difference)>1e-5:raise ValueError("Thermal-strain load-scale quadrature did not converge; refine the mesh")
        np.add.at(force,inds.ravel(),candidates[1].reshape(-1,3))
        for e,values in zip(cells,delta[inds]):
            bounds=scalar_bounds(values[None,:])
            maximum_strain=max(maximum_strain,max(abs(v) for v in bounds)*abs(system.assignments[e.body_id].expansion_per_k))
    source={n:float(original[i]) for i,n in enumerate(system.ids)}
    target={n:float(serialized[i]) for i,n in enumerate(system.ids)}
    audit={"temperature_source_sha256":digest(source),"temperature_solver_sha256":digest(target),
           "node_count":len(system.ids),"stress_free_temperature_k":reference,
           "solver_stress_free_temperature_k":solver_reference,"maximum_delta_temperature_rounding_error_k":error,
           "maximum_free_thermal_strain_bound":maximum_strain,"thermal_scale_quadrature_change":worst,
           "thermal_equivalent_load_norm_n":float(np.linalg.norm(force)),
           "thermal_equivalent_load_sum_norm_n":float(np.linalg.norm(force,axis=1).sum()),
           "thermal_equivalent_load_resultant_n":force.sum(axis=0).tolist(),
           "thermal_equivalent_load_moment_n_mm":np.cross(system.xyz,force).sum(axis=0).tolist(),
           "convention":"Equivalent eigenstrain vector scales verification only; temperature is applied through EXPANSION/INITIAL CONDITIONS/TEMPERATURE, never duplicated as CLOAD"}
    return {"temperatures":target,"reference":solver_reference,"audit":audit}
