"""Canonical quadratic solid conduction in mm, W and kelvin.

All ten shape functions participate in conduction and sources. Robin and
prescribed-surroundings radiation use consistent six-node boundary quadrature.
No elasticity, airflow, inferred interface or nodal heat-source lumping.
"""
from __future__ import annotations

import warnings
import numpy as np
from scipy import sparse
from scipy.sparse import linalg as sla

from .quadratic import EDGES, surface_quadrature
from .simulation_quadratic import QuadraticGeometry
from .solver import Cancelled
from .store import write_json
from .thermal_exchange import volume_quadrature

SIGMA_W_MM2_K4 = 5.670374419e-14


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():raise Cancelled("Simulation cancelled")


class QuadraticThermalSystem(QuadraticGeometry):
    def __init__(self, mesh, study, materials, cancel=None):
        super().__init__(mesh, cancel)
        self.study = study
        self.assignments = {a.body_id: materials[a.material_id] for a in study.assignments}
        if self.body_nodes.keys() != self.assignments.keys():
            raise ValueError("Quadratic thermal bodies and material assignments must match exactly")
        if study.interfaces or study.bolts or study.settings.inertial_relief:
            raise ValueError("Quadratic thermal interface/connector/inertial-relief transfer is unavailable")
        for body, material in self.assignments.items():
            if material.conductivity_w_mk is None:
                raise ValueError(f"Body {body} requires sourced thermal conductivity")
        self.connectivity = np.array([[self.index[n] for n in e.nodes] for e in self.mesh.elements])
        self.kt, self.conduction_quadrature = self.assemble(cancel)

    def element_data(self, start, order):
        """Return full shape gradients in physical mm coordinates, never corners."""
        inds = self.connectivity[start:start+128]
        xyz = self.xyz[inds]
        shape, gradients, measure = volume_quadrature(order)
        jac = np.einsum("eni,qnj->eqij", xyz-xyz[:, :1], gradients)
        weights = np.linalg.det(jac)*measure[None, :]
        if not np.isfinite(weights).all() or np.any(weights <= 0):
            raise ValueError("Thermal quadrature has nonpositive curved-cell Jacobian")
        physical = np.einsum("qni,eqij->eqnj", gradients, np.linalg.inv(jac))
        return inds, shape, weights, physical

    def assemble(self, cancel):
        rows, columns, entries = [], [], []
        worst, selected = 0., 6
        for start in range(0, len(self.connectivity), 128):
            check_cancel(cancel)
            conductivities = np.array([self.assignments[e.body_id].conductivity_w_mk/1000
                                      for e in self.mesh.elements[start:start+128]])
            def stiffness(order):
                inds, _, weights, gradients = self.element_data(start, order)
                local = np.einsum("eqni,eqmi,eq,e->enm", gradients, gradients, weights, conductivities)
                return inds, local
            inds, lower = stiffness(4)
            _, upper = stiffness(6)
            change = np.linalg.norm(upper-lower, axis=(1, 2))/np.maximum(np.linalg.norm(upper, axis=(1, 2)), 1e-30)
            if np.max(change) > 1e-5:
                _, highest = stiffness(8)
                change = np.linalg.norm(highest-upper, axis=(1, 2))/np.maximum(np.linalg.norm(highest, axis=(1, 2)), 1e-30)
                upper = highest;selected = 8
            worst = max(worst, float(np.max(change)))
            if np.max(change) > 1e-5:
                raise ValueError("Curved thermal conduction quadrature did not converge; refine the mesh")
            rows.extend(np.repeat(inds, 10, axis=1).ravel())
            columns.extend(np.tile(inds, (1, 10)).ravel())
            entries.extend(upper.ravel())
        matrix = sparse.coo_matrix((entries, (rows, columns)), shape=(len(self.ids), len(self.ids))).tocsr()
        return matrix, {"maximum_relative_change": worst, "maximum_order_used": selected, "tolerance": 1e-5}

    def surface(self, region_id, order):
        self.region_nodes(region_id)
        faces = self.raw_mesh["surfaces"].get(region_id)
        if not faces:raise ValueError("Thermal power/flux/exchange needs complete six-node boundary faces")
        ids, _, shape, weights = surface_quadrature(self.raw_mesh, faces, order=order)
        inds = np.array([[self.index[int(n)] for n in face] for face in ids])
        return inds, shape, weights

    def temperature_bounds(self, temperature):
        # Bernstein coefficients enclose each complete quadratic temperature
        # polynomial, including values between nodes and inside curved cells.
        values = temperature[self.connectivity]
        edges = np.array([2*values[:, i+4]-(values[:, a]+values[:, b])/2
                          for i, (a, b) in enumerate(EDGES)]).T
        if not np.isfinite(values).all() or not np.isfinite(edges).all():
            raise ValueError("Quadratic temperature field has nonfinite polynomial bounds")
        return float(min(values[:, :4].min(), edges.min())), float(max(values[:, :4].max(), edges.max()))


def thermal_solve(system, case, directory, cancel=None):
    """Safeguarded Newton solve; balance includes thermostat reaction power."""
    if case.supports or case.loads or case.history or any(case.initial_velocity_mm_s):
        raise ValueError("Steady quadratic thermal studies do not translate mechanical supports/loads/history/velocity")
    n = len(system.ids)
    q = np.zeros(n)
    prescribed, anchored = {}, set()
    anchor_values = {i: [] for i in range(len(system.solid_components))}
    exchange = []
    boundary_audits = []
    for bc in case.thermal:
        if bc.kind == "body_heat":
            if bc.body_id not in system.assignments:
                raise ValueError("Body heat requires an assigned body")
            if bc.region_id is not None:raise ValueError("Body heat uses an explicit body, not an ignored surface region")
            for node, integral in system.body_integrals[bc.body_id].items():
                q[system.index[node]] += bc.value*integral/system.body_volumes[bc.body_id]
            continue
        if not bc.region_id:raise ValueError("Thermal boundary requires an explicit region")
        inds = system.region_nodes(bc.region_id)
        if bc.body_id is not None and bc.body_id != system.regions[bc.region_id].body_id:
            raise ValueError("Thermal boundary body differs from its region body")
        if bc.kind == "temperature":
            for i in inds:
                if i in prescribed and abs(prescribed[i]-bc.value) > 1e-9:
                    raise ValueError("Conflicting temperatures on shared region nodes")
                prescribed[i] = bc.value
                component = system.component_of[system.ids[i]]
                anchored.add(component)
                anchor_values[component].append(bc.value)
            continue
        lower = system.surface(bc.region_id, 6)
        higher = system.surface(bc.region_id, 8)
        contributions = []
        for face_inds, shape, weights in (lower, higher):
            load = np.einsum("qn,tq->tn", shape, weights)
            if bc.kind in {"heat_power", "heat_flux"}:
                contributions.append(load*bc.value/(weights.sum() if bc.kind == "heat_power" else 1))
            else:
                contributions.append(np.einsum("qn,qm,tq->tnm", shape, shape, weights))
        change = float(np.linalg.norm(contributions[1]-contributions[0])/max(np.linalg.norm(contributions[1]), 1e-30))
        if change > 1e-5:raise ValueError("Thermal boundary quadrature did not converge; refine the mesh")
        boundary_audits.append({"boundary_id": bc.id, "relative_change": change, "orders": [6, 8]})
        if bc.kind in {"heat_power", "heat_flux"}:
            np.add.at(q, higher[0].ravel(), contributions[1].ravel())
        else:
            exchange.append((bc, lower, higher))
            if bc.value > 0:
                components = {system.component_of[system.ids[i]] for i in higher[0].ravel()}
                anchored.update(components)
                for component in components:anchor_values[component].append(bc.ambient_k)
    if anchored != set(range(len(system.solid_components))):
        raise ValueError("Every disconnected thermal solid needs a positive temperature or nonzero exchange anchor")
    fixed = np.array(sorted(prescribed), dtype=int)
    free = np.array([i for i in range(n) if i not in prescribed], dtype=int)
    component_reference = {i: float(np.mean(values)) for i, values in anchor_values.items()}
    reference = np.array([component_reference[system.component_of[node]] for node in system.ids])
    temperature = reference.copy()
    for i, value in prescribed.items():temperature[i] = value

    def boundary_terms(values, data, bc):
        inds, shape, weights = data
        at_quadrature = reference[inds[:, 0]][:, None]+np.einsum("qn,tn->tq", shape, values[inds]-reference[inds])
        if np.any(at_quadrature <= 0):raise ValueError("Boundary temperature reaches nonpositive absolute kelvin")
        if bc.kind == "convection":
            coefficient = bc.value*1e-6
            flux = coefficient*(at_quadrature-bc.ambient_k)
            derivative = np.full_like(flux, coefficient)
        else:
            coefficient = bc.value*SIGMA_W_MM2_K4
            # Factorized difference avoids catastrophic cancellation near ambient.
            flux = coefficient*(at_quadrature-bc.ambient_k)*(at_quadrature+bc.ambient_k)*(at_quadrature**2+bc.ambient_k**2)
            derivative = 4*coefficient*at_quadrature**3
        nodal = np.einsum("qn,tq,tq->tn", shape, weights, flux)
        tangent = np.einsum("qn,qm,tq,tq->tnm", shape, shape, weights, derivative)
        return nodal, tangent, float(np.sum(weights*flux)), float(np.sum(weights*np.abs(flux)))

    def equation(values, tangent=True):
        # Constants are in the exact nullspace analytically; shifting avoids an
        # ambient-temperature denominator that can hide tiny heat-load defects.
        conductive = system.kt@(values-reference)
        residual = conductive-q
        rows, columns, entries = [], [], []
        heat_out, absolute_exchange = 0., 0.
        for bc, _, higher in exchange:
            nodal, local, power, absolute = boundary_terms(values, higher, bc)
            inds = higher[0]
            np.add.at(residual, inds.ravel(), nodal.ravel())
            heat_out += power;absolute_exchange += absolute
            if tangent:
                rows.extend(np.repeat(inds, 6, axis=1).ravel())
                columns.extend(np.tile(inds, (1, 6)).ravel())
                entries.extend(local.ravel())
        scale = max(float(np.linalg.norm(q)), float(np.linalg.norm(conductive)), absolute_exchange, 1e-30)
        norm = float(np.linalg.norm(residual[free])/scale) if len(free) else 0.
        jacobian = system.kt+sparse.coo_matrix((entries, (rows, columns)), shape=(n, n)).tocsr() if tangent else None
        return residual, jacobian, norm, heat_out, absolute_exchange

    history = []
    for iteration in range(system.study.settings.max_iterations):
        check_cancel(cancel)
        residual, jacobian, norm, _, _ = equation(temperature)
        if norm <= min(system.study.settings.residual_tolerance*.1, 1e-10):break
        with warnings.catch_warnings():
            warnings.simplefilter("error", sla.MatrixRankWarning)
            try:delta = sla.spsolve(jacobian[free][:, free].tocsc(), -residual[free])
            except (sla.MatrixRankWarning, RuntimeError) as exc:
                raise ValueError("Quadratic thermal system is singular") from exc
        if not np.isfinite(delta).all():raise ValueError("Quadratic thermal solution is nonfinite")
        # Aim beyond the declared equation tolerance, but stop once a correction
        # is below temperature representation and the declared residual still
        # passes with tenfold margin. Never normalize against ambient kelvin.
        if norm <= system.study.settings.residual_tolerance*.1 and np.max(np.abs(delta)) <= np.max(np.abs(temperature))*np.finfo(float).eps*64:
            break
        old_norm = float(np.linalg.norm(residual[free]))
        step = 1.
        for _ in range(40):
            candidate = temperature.copy();candidate[free] += step*delta
            if system.temperature_bounds(candidate)[0] > 0:
                trial = equation(candidate, tangent=False)
                if np.linalg.norm(trial[0][free]) <= old_norm*(1-1e-4*step):break
            step *= .5
        else:raise ValueError("Thermal Newton line search cannot resolve a positive decreasing-residual step at the declared tolerance; heat source/temperature precision may be insufficient")
        temperature = candidate
        history.append({"iteration": iteration+1, "relative_residual_before_update": norm,
                        "temperature_update_k": float(np.max(np.abs(step*delta))), "step_scale": step})
    else:raise ValueError("Quadratic thermal iteration did not converge")
    residual, _, norm, heat_out, absolute_exchange = equation(temperature, tangent=False)
    lower_bound, upper_bound = system.temperature_bounds(temperature)
    if lower_bound <= 0:raise ValueError("Quadratic temperature field cannot be bounded above absolute zero; refine the mesh")
    radiation_audit = []
    for bc, lower, higher in exchange:
        lo, _, _, _ = boundary_terms(temperature, lower, bc)
        hi, _, _, _ = boundary_terms(temperature, higher, bc)
        error = float(np.linalg.norm(hi-lo)/max(np.linalg.norm(hi), np.linalg.norm(q)*1e-12, 1e-30))
        if error > 1e-5:raise ValueError("Solved thermal exchange quadrature did not converge; refine the boundary")
        radiation_audit.append({"boundary_id": bc.id, "relative_change": error, "orders": [6, 8]})
    applied = float(q.sum())
    thermostat = float(residual[fixed].sum()) if len(fixed) else 0.
    balance = abs(applied+thermostat-heat_out)/max(float(np.abs(q).sum()), float(np.abs(residual[fixed]).sum()), absolute_exchange, 1e-30)
    flux = {}
    quadrature_min, quadrature_max = float(temperature.min()), float(temperature.max())
    for start in range(0, len(system.connectivity), 128):
        check_cancel(cancel)
        inds, shape, weights, gradients = system.element_data(start, 8)
        at_quadrature = reference[inds[:, 0]][:, None]+np.einsum("qn,en->eq", shape, (temperature-reference)[inds])
        quadrature_min = min(quadrature_min, float(at_quadrature.min()))
        quadrature_max = max(quadrature_max, float(at_quadrature.max()))
        for e, grad, w, t in zip(system.mesh.elements[start:start+128], gradients, weights, (temperature-reference)[inds]):
            vector = -system.assignments[e.body_id].conductivity_w_mk/1000*np.einsum("qni,n->qi", grad, t)
            flux[e.id] = (np.sum(vector*w[:, None], axis=0)/w.sum()).tolist()
    result = {"values": {"max_temperature_k": upper_bound, "min_temperature_k": lower_bound,
                         "applied_heat_w": applied, "heat_rejected_w": heat_out, "thermostat_heat_w": thermostat,
                         "temperature_rise_k": upper_bound-lower_bound},
              "checks": {"residual": norm, "heat_balance_error": balance, "thermal_iterations": history,
                         "conduction_quadrature": system.conduction_quadrature, "boundary_quadrature": boundary_audits,
                         "solved_exchange_quadrature": radiation_audit,
                         "temperature_bernstein_bounds_k": [lower_bound, upper_bound],
                         "sampled_temperature_extrema_k": [quadrature_min, quadrature_max],
                         "temperature_convergence_scale_k": max(float(np.max(np.abs(temperature-reference))),
                             max(max(values)-min(values) for values in anchor_values.values()))},
              "field": {"nodes": {n: system.xyz[i].tolist() for i, n in enumerate(system.ids)},
                        "temperature_k": {n: float(temperature[i]) for i, n in enumerate(system.ids)},
                        "element_heat_flux_w_mm2": flux}, "temperature": temperature,
              "mesh_quality": system.quality_record, "solver": "Oxygen canonical C3D10 NumPy/SciPy solid conduction",
              "warnings": ["Reported temperature limits use conservative Bernstein bounds of the full interpolated field; sampled nodal/quadrature extrema are separate checks."]}
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory/"thermal-solution.json", {k: v for k, v in result.items() if k != "temperature"})
    return result
