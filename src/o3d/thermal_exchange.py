"""Consistent C3D10 heat inputs and prescribed-environment surface exchange.

Temperatures in the document are Celsius; the Stefan-Boltzmann law uses kelvin.
Boundary powers below are watts; CalculiX decks convert W to N mm/s separately.
No airflow, film coefficient, emissivity or environmental temperature is inferred.
"""
from __future__ import annotations
from functools import lru_cache
import numpy as np
from .quadratic import FACES, surface_quadrature, weights_and_gradients

STEFAN_BOLTZMANN_W_M2_K4 = 5.670374419e-8
EXCHANGE_QUADRATURE_TOLERANCE = 1e-4


def _surface_owners(mesh, names):
    # CalculiX tetrahedron face labels differ from the helper's local face order.
    faces = {tuple(sorted(t[:3])): [] for name in names for t in mesh['surfaces'][name]}
    for index, element in enumerate(mesh['elements'], 1):
        for face, label in zip(FACES, (1, 2, 4, 3)):
            nodes = tuple(element[i] for i in face)
            key=tuple(sorted(nodes[:3]))
            if key in faces:faces[key].append((index, label, set(nodes)))
    return faces


def element_surfaces(mesh, name, faces=None):
    if faces is None:faces=_surface_owners(mesh,[name])
    result = []
    for triangle in mesh['surfaces'][name]:
        matches = faces.get(tuple(sorted(triangle[:3])), [])
        if len(matches) != 1 or matches[0][2] != set(triangle):
            raise ValueError(f'{name}: thermal exchange requires complete exterior quadratic faces')
        result.append(matches[0][:2])
    if len(set(result)) != len(result):raise ValueError(f'{name}: duplicate thermal exchange faces')
    return result


def exchange_deck(mesh, study):
    lines = []
    names=[f'{kind}_{i}' for kind,boundaries in [('convection',study.convection),('radiation',study.radiation)] for i in range(len(boundaries))]
    owners=_surface_owners(mesh,names) if names else {}
    for kind, boundaries, label in [('convection', study.convection, 'F'), ('radiation', study.radiation, 'R')]:
        used = set()
        if boundaries:lines.append('*FILM' if kind == 'convection' else '*RADIATE')
        for i, boundary in enumerate(boundaries):
            selected = element_surfaces(mesh, f'{kind}_{i}', owners)
            if used.intersection(selected):raise ValueError(f'Overlapping {kind} boundaries would replace an earlier solver load')
            used.update(selected)
            coefficient = boundary.coefficient_w_m2_k*1e-3 if kind == 'convection' else boundary.emissivity
            lines.extend(f'{e},{label}{face},{boundary.ambient_temperature_c:.12g},{coefficient:.12g}' for e, face in selected)
    return lines


@lru_cache(maxsize=1)
def volume_quadrature():
    roots, gauss = np.polynomial.legendre.leggauss(4)
    roots, gauss = (roots+1)/2, gauss/2
    shape, gradients, measure = [], [], []
    for i, u in enumerate(roots):
        for j, v in enumerate(roots):
            for k, w in enumerate(roots):
                values, derivative = weights_and_gradients(np.array([u, (1-u)*v, (1-u)*(1-v)*w]))
                shape.append(values);gradients.append(derivative)
                measure.append(gauss[i]*gauss[j]*gauss[k]*(1-u)**2*(1-v))
    return np.asarray(shape), np.asarray(gradients), np.asarray(measure)


def internal_heat_loads(mesh, power_w):
    nodes = {int(n): np.asarray(p) for n, p in mesh['nodes'].items()}
    cells = np.asarray(mesh['elements'])
    xyz = np.asarray([[nodes[int(n)] for n in e] for e in cells])
    shape, gradients, measure = volume_quadrature()
    jacobian = np.einsum('eni,qnj->eqij', xyz-xyz[:, :1], gradients)
    weights = np.linalg.det(jacobian)*measure[None, :]
    if not np.isfinite(weights).all() or np.any(weights <= 0):raise ValueError('Internal heat requires positive finite volume quadrature')
    volume = float(weights.sum())
    consistent = np.einsum('qn,eq->en', shape, weights)*(power_w*1000/volume)
    flux = {}
    for element, values in zip(cells, consistent):
        for node, value in zip(element, values):flux[int(node)] = flux.get(int(node), 0.)+float(value)
    return flux, volume


def _boundary_exchange(mesh, temperatures, boundary, name, radiation, order):
    ids, _, shape, measures = surface_quadrature(mesh, mesh['surfaces'][name], order)
    values = {int(n): value for n, value in temperatures.items()}
    field = np.asarray([[values[int(n)] for n in face] for face in ids])
    t = field @ shape.T
    if radiation:
        kelvin, ambient = t+273.15, boundary.ambient_temperature_c+273.15
        if np.any(kelvin <= 0):raise ValueError('Radiation surface quadrature reaches nonpositive absolute temperature')
        flux = boundary.emissivity*STEFAN_BOLTZMANN_W_M2_K4*1e-6*(kelvin**4-ambient**4)
    else:flux = boundary.coefficient_w_m2_k*1e-6*(t-boundary.ambient_temperature_c)
    return float(np.sum(measures*flux)), float(np.sum(measures*np.abs(flux))), float(measures.sum()), ids, np.einsum('fq,qn->fn',measures*flux,shape)


def heat_exchange(mesh, study, temperatures):
    records, passed, nodal = [], True, {}
    for kind, boundaries in [('convection', study.convection), ('radiation', study.radiation)]:
        for i, boundary in enumerate(boundaries):
            name = f'{kind}_{i}'
            lower, *_ = _boundary_exchange(mesh, temperatures, boundary, name, kind == 'radiation', 6)
            power, absolute, area, ids, integrals = _boundary_exchange(mesh, temperatures, boundary, name, kind == 'radiation', 8)
            for face,values in zip(ids,integrals):
                for node,value in zip(face,values):nodal[int(node)] = nodal.get(int(node),0.)+float(value)
            sensitivity = abs(power-lower)/max(absolute, 1e-9)
            passed &= sensitivity <= EXCHANGE_QUADRATURE_TOLERANCE
            records.append({'kind': kind, 'face': boundary.face, 'source': boundary.source,
                            'power_out_w': power, 'absolute_exchange_w': absolute, 'area_mm2': area,
                            'quadrature_sensitivity': sensitivity})
    return {'convection_w': sum(r['power_out_w'] for r in records if r['kind'] == 'convection'),
            'radiation_w': sum(r['power_out_w'] for r in records if r['kind'] == 'radiation'),
            'boundaries': records, 'quadrature_passed': bool(passed),
            'nodal_power_out_w': nodal,
            'quadrature_tolerance': EXCHANGE_QUADRATURE_TOLERANCE,
            'radiation_model': 'gray diffuse surface to prescribed uniform surroundings, unit view factor; no cavity/occlusion solve'}
