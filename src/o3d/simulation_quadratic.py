"""Canonical C3D10 geometry and conservative mechanical transfer to CalculiX.

Consumes the domain's exact mesh, body/material assignments and load cases.
There is no reconstructed Project or corner-only fallback. Quality runs in the
isolated mesher interpreter. Signed quadratic body/face weights are intentional.
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np

from .quadratic import surface_quadrature
from .simulation_mesh import exterior_triangles, validate_boundary, displacement_probe
from .simulation_units import density_tonne_mm3, GRAMS_PER_TONNE
from .solver import Cancelled, mesher_python, run_process, calculix_number
from .store import write_json
from .thermal_exchange import volume_quadrature


class QuadraticGeometry:
    def __init__(self, mesh, cancel=None):
        if mesh.element_family != "C3D10":
            raise ValueError("Quadratic adapter requires the complete C3D10 mesh")
        if max(mesh.nodes)>100000 or any(e.id>150000 for e in mesh.elements):
            raise ValueError("CalculiX label budget exceeded; compact imported node/element labels before solving")
        self.source_mesh=mesh
        solver_nodes={n:tuple(float(calculix_number(v)) for v in p) for n,p in mesh.nodes.items()}
        self.coordinate_rounding_error_mm=max(abs(v-solver_nodes[n][i]) for n,p in mesh.nodes.items() for i,v in enumerate(p))
        spans=np.ptp(np.array(list(mesh.nodes.values())),axis=0)
        if self.coordinate_rounding_error_mm>max(float(spans.min())*1e-8,1e-12):
            raise ValueError("Solver coordinate precision is insufficient at this model origin; translate the analysis nearer the origin explicitly")
        mesh=mesh.model_copy(update={"nodes":solver_nodes})
        self.mesh = mesh
        self._probe_cache={}
        self.ids = sorted(mesh.nodes)
        self.index = {n: i for i, n in enumerate(self.ids)}
        self.xyz = np.array([mesh.nodes[n] for n in self.ids], dtype=float)
        bodies = {e.body_id for e in mesh.elements}
        validate_boundary(mesh)
        from .quadratic import boundary_faces
        _,_,components=boundary_faces([e.nodes for e in mesh.elements],return_components=True)
        self.solid_components=[{n for i in group for n in mesh.elements[i].nodes} for group in components]
        self.component_of={}
        for i,nodes in enumerate(self.solid_components):
            for n in nodes:
                if n in self.component_of:raise ValueError("Quadratic solids meet only at points or edges; separate their connectivity and declare a qualified interface")
                self.component_of[n]=i
        oriented = {tuple(sorted(t)): t for t in exterior_triangles(mesh.nodes, mesh.elements)}
        self.regions = {r.id: r for r in mesh.regions}
        self.raw_mesh = {"nodes": mesh.nodes, "elements": [list(e.nodes) for e in mesh.elements],
                         "surfaces": {r.id: [list(oriented[tuple(sorted(t))]) for t in r.triangles] for r in mesh.regions if r.triangles}}
        self.body_nodes = {body: {n for e in mesh.elements if e.body_id == body for n in e.nodes} for body in bodies}
        for region in mesh.regions:
            if region.body_id not in bodies or not (set(region.node_ids) | {n for t in region.triangles for n in t}) <= self.body_nodes[region.body_id]:
                raise ValueError(f"Region {region.id} contains nodes outside its declared body")
        from .analysis_mesh import validate_mesh
        self.quality_record = validate_mesh(self.raw_mesh, max_nodes=100000, require_connected=False)
        self.quality = self.quality_record["minimum_corner_quality"]
        self.body_integrals = {body: {} for body in bodies}
        self.cell_volumes = []
        shape, gradients, measure = volume_quadrature()
        for start in range(0, len(mesh.elements), 256):
            if cancel is not None and cancel.is_set(): raise Cancelled("Simulation cancelled")
            cells = mesh.elements[start:start+256]
            xyz = np.array([[mesh.nodes[n] for n in e.nodes] for e in cells])
            jacobian = np.einsum("eni,qnj->eqij", xyz-xyz[:, :1], gradients)
            weights = np.linalg.det(jacobian)*measure[None, :]
            if not np.isfinite(weights).all() or np.any(weights <= 0):
                raise ValueError("Quadratic mesh has nonpositive volume quadrature")
            integrals = np.einsum("qn,eq->en", shape, weights)
            self.cell_volumes.extend(weights.sum(axis=1).tolist())
            for element, values in zip(cells, integrals):
                body = self.body_integrals[element.body_id]
                for n, value in zip(element.nodes, values): body[n] = body.get(n, 0.)+float(value)
        self.volume = float(sum(self.cell_volumes))
        self.max_element_volume_mm3 = max(self.cell_volumes)
        self.body_volumes = {body: float(sum(values.values())) for body, values in self.body_integrals.items()}

    def qualify(self, directory, cancel=None):
        """Check the exact solver-order mesh, including every curved midside node."""
        from .analysis_mesh import validate_mesh
        directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
        write_json(directory/"mesh.json", self.raw_mesh)
        run_process([mesher_python(), Path(__file__).with_name("mesh_quality_worker.py"),
                     directory/"mesh.json", directory/"curved-quality.json"],
                    directory, directory/"quality.log", cancel, timeout=180)
        audit = json.loads((directory/"curved-quality.json").read_text())
        self.quality_record = validate_mesh(self.raw_mesh, max_nodes=100000, curved_audit=audit, require_connected=False)
        if not np.allclose(audit["integrated_volume_mm3"], self.cell_volumes, rtol=1e-9, atol=1e-12):
            raise ValueError("Independent curved-cell volumes disagree with load integration")
        comparisons = {}
        for body, expected in self.mesh.cad_volumes_mm3.items():
            error = abs(self.body_volumes[body]-expected)/expected
            if error > .1: raise ValueError(f"Body {body} mesh volume differs from declared STEP volume by more than 10%")
            comparisons[body] = {"integrated_volume_mm3": self.body_volumes[body], "declared_cad_volume_mm3": expected, "relative_error": error}
        self.quality_record["body_volume_comparisons"] = comparisons
        self.quality_record["body_integrated_volumes_mm3"] = self.body_volumes
        self.quality_record["solver_coordinate_format"]="Scientific notation, 13 significant digits, at most 20 characters; quality audit uses parsed solver coordinates"
        self.quality_record["maximum_coordinate_rounding_error_mm"]=self.coordinate_rounding_error_mm
        self.quality_record["audit_index_to_solver_element_id"]={str(i+1):e.id for i,e in enumerate(self.mesh.elements)}
        from .mesh_quality_worker import mesh_fingerprint
        self.quality_record["source_mesh_sha256"]=mesh_fingerprint({"nodes":self.source_mesh.nodes,"elements":[e.nodes for e in self.source_mesh.elements]})
        write_json(directory/"mesh-quality.json", self.quality_record)
        return self.quality_record

    def region_nodes(self, identifier):
        if identifier not in self.regions: raise ValueError(f"Missing region {identifier}; repair the mapping explicitly")
        region = self.regions[identifier]
        nodes = set(region.node_ids) | {n for triangle in region.triangles for n in triangle}
        if not nodes: raise ValueError(f"Region {identifier} has no mesh nodes")
        return [self.index[n] for n in sorted(nodes)]


class QuadraticSystem(QuadraticGeometry):
    def __init__(self,mesh,study,materials,cancel=None):
        super().__init__(mesh,cancel)
        self.study,self.materials=study,materials
        self.assignments={a.body_id:materials[a.material_id] for a in study.assignments}
        bodies={e.body_id for e in mesh.elements}
        if bodies!=self.assignments.keys():raise ValueError("Quadratic mesh bodies and material assignments must match exactly")
        for material in self.assignments.values():
            if material.youngs_modulus_mpa is None or material.poisson_ratio is None:raise ValueError(f"Material {material.id} requires sourced elasticity")
            if material.plastic_curve and study.type!="nonlinear_static":raise ValueError("Quadratic elastic analysis cannot silently ignore a plastic curve")
        self.mass_g=(sum(self.body_volumes[b]*density_tonne_mm3(self.assignments[b].density_g_cm3)*GRAMS_PER_TONNE for b in bodies)
                     if all(self.assignments[b].density_g_cm3 is not None for b in bodies) else None)

    def validate_supports(self, case):
        """Refuse any unrestrained rigid motion of a disconnected solid group."""
        constrained={}
        for support in case.supports:
            if support.kind not in {"fixed","partial"}:raise ValueError("Quadratic CalculiX supports fixed/world-axis partial constraints; no support was approximated")
            axes=(True,True,True) if support.kind=="fixed" else support.components
            for i in self.region_nodes(support.region_id):
                for axis,active in enumerate(axes):
                    if active:constrained.setdefault(self.component_of[self.ids[i]],set()).add((self.ids[i],axis))
        for component,nodes in enumerate(self.solid_components):
            xyz=np.array([self.mesh.nodes[n] for n in nodes]);center=xyz.mean(axis=0)
            scale=max(float(np.linalg.norm(np.ptp(xyz,axis=0))),1e-12)
            rows=[]
            for n,axis in constrained.get(component,set()):
                x,y,z=(np.array(self.mesh.nodes[n])-center)/scale
                rotation=np.array([[0,z,-y],[-z,0,x],[y,-x,0]])
                rows.append(np.r_[np.eye(3)[axis],rotation[axis]])
            if len(rows)<6 or np.linalg.matrix_rank(np.array(rows),tol=1e-9)<6:
                raise ValueError("Quadratic model has unrestrained rigid body modes; repair each disconnected load path's supports")

    def loads(self, case):
        force = np.zeros((len(self.ids), 3))
        for load in case.loads:
            vector = np.asarray(load.vector, dtype=float)
            if load.kind == "gravity":
                selected = [load.body_id] if load.body_id else list(self.assignments)
                if any(b not in self.assignments for b in selected): raise ValueError("Gravity references a missing body")
                for body in selected:
                    density = self.assignments[body].density_g_cm3
                    if density is None: raise ValueError(f"Gravity requires sourced density on body {body}")
                    for n, integral in self.body_integrals[body].items():
                        force[self.index[n]] += density_tonne_mm3(density)*integral*vector
                continue
            if load.region_id not in self.regions: raise ValueError(f"Load {load.id} requires an existing region")
            region = self.regions[load.region_id]
            if load.body_id and load.body_id != region.body_id: raise ValueError("Load body differs from its region body")
            inds = self.region_nodes(load.region_id)
            if load.kind in {"moment", "remote"}:
                points = self.xyz[inds]-np.asarray(load.origin_mm)
                matrix = np.zeros((6, 3*len(inds)))
                for j, (x, y, z) in enumerate(points):
                    matrix[:3, 3*j:3*j+3] = np.eye(3)
                    matrix[3:, 3*j:3*j+3] = [[0, -z, y], [z, 0, -x], [-y, x, 0]]
                desired = np.r_[vector if load.kind == "remote" else np.zeros(3), vector if load.kind == "moment" else np.asarray(load.axis or (0, 0, 0))]
                values, *_ = np.linalg.lstsq(matrix, desired, rcond=1e-12)
                if np.linalg.norm(matrix@values-desired) > 1e-8*max(np.linalg.norm(desired), 1):
                    raise ValueError("Region cannot transmit the specified remote force/moment")
                force[inds] += values.reshape(-1, 3)
                continue
            if load.kind not in {"force", "pressure"}:
                raise ValueError(f"Quadratic {load.kind} transfer is not integrated; no load was approximated or discarded")
            triangles = self.raw_mesh["surfaces"].get(load.region_id)
            if not triangles: raise ValueError(f"Load {load.id} requires quadratic boundary triangles")
            integrated=[]
            for order in (6,8):
                ids, _, shape, measures, normals = surface_quadrature(self.raw_mesh, triangles, order=order, return_normals=True)
                if load.kind == "force":
                    values = np.einsum("qn,tq->tn", shape, measures)[:, :, None]*vector/measures.sum()
                else:
                    if load.magnitude is None: raise ValueError("Pressure requires its magnitude in MPa")
                    values = -load.magnitude*np.einsum("qn,tq,tqi->tni", shape, measures, normals)
                integrated.append(values)
            sensitivity=np.linalg.norm(integrated[1]-integrated[0])/max(float(np.linalg.norm(integrated[1])),1e-30)
            if sensitivity>1e-5:raise ValueError("Quadratic traction quadrature did not converge; refine the loaded boundary")
            values=integrated[1]
            for triangle, nodal in zip(ids, values):
                for n, value in zip(triangle, nodal): force[self.index[int(n)]] += value
        return force.ravel()

    def displacement_probe(self, field):
        return displacement_probe(self.mesh,self.study.settings.probe_point_mm,self.study.settings.probe_body_id,field,self._probe_cache)
