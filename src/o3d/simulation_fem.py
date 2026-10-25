"""Small-strain tetrahedral FE reference worker using NumPy and SciPy.

C3D4: constant strain, consistent surface traction, lumped translational mass.
This is an original, bounded implementation, not a claim of solver certification.
Only the worker imports SciPy. No stabilization hides unsupported rigid modes.
"""
from __future__ import annotations
import math
import warnings
from dataclasses import dataclass
import numpy as np
from scipy import sparse, linalg
from scipy.sparse import linalg as sla
from .simulation_models import MeshRecord, StudyRecord, MaterialRecord, LoadCase
from .solver import Cancelled
from .simulation_units import GRAMS_PER_TONNE, density_tonne_mm3, fluid_density_tonne_mm3


def unit(value):
    v = np.asarray(value, dtype=float)
    size = np.linalg.norm(v)
    if size < 1e-14:
        raise ValueError("Direction vector must be nonzero")
    return v / size


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled("Simulation cancelled")


def elastic_matrix(material):
    if material.youngs_modulus_mpa is None or material.poisson_ratio is None:
        raise ValueError(f"Material {material.id} requires sourced elasticity")
    e, nu = material.youngs_modulus_mpa, material.poisson_ratio
    mu, lam = e / (2 * (1 + nu)), e * nu / ((1 + nu) * (1 - 2 * nu))
    d = np.zeros((6, 6))
    d[:3, :3] = lam
    np.fill_diagonal(d[:3, :3], lam + 2 * mu)
    np.fill_diagonal(d[3:, 3:], mu)
    return d


def tetra(points):
    x = np.asarray(points)
    a = np.column_stack([np.ones(4), x])
    volume = abs(np.linalg.det(a)) / 6
    scale = max(np.ptp(x, axis=0).max(), 1e-10)
    if volume < scale ** 3 * 1e-12:
        raise ValueError("Degenerate tetrahedron: mesh is invalid")
    gradients = np.linalg.inv(a)[1:, :].T
    b = np.zeros((6, 12))
    for i, (gx, gy, gz) in enumerate(gradients):
        b[:, 3 * i:3 * i + 3] = [[gx, 0, 0], [0, gy, 0], [0, 0, gz], [gy, gx, 0], [0, gz, gy], [gz, 0, gx]]
    edges = [np.linalg.norm(x[i] - x[j]) for i in range(4) for j in range(i)]
    quality = 12 * (3 * volume) ** (2/3) / sum(e*e for e in edges)
    return volume, gradients, b, float(quality)


@dataclass
class System:
    mesh: MeshRecord
    study: StudyRecord
    materials: dict[str, MaterialRecord]
    mechanical: bool = True

    def __post_init__(self):
        self.ids = sorted(self.mesh.nodes)
        self.index = {n: i for i, n in enumerate(self.ids)}
        self.xyz = np.array([self.mesh.nodes[n] for n in self.ids], dtype=float)
        self.regions = {r.id: r for r in self.mesh.regions}
        self.assignments = {a.body_id: self.materials[a.material_id] for a in self.study.assignments}
        missing = {e.body_id for e in self.mesh.elements} - self.assignments.keys()
        if missing:
            raise ValueError(f"Missing material assignment for bodies: {sorted(missing)}")
        self.elements = []
        self.mass = np.zeros(len(self.ids))
        self.volume = 0.
        self.quality = 1.
        rows, cols, values = [], [], []
        self.thermal_rows, self.thermal_cols, self.thermal_values = [], [], []
        for e in self.mesh.elements:
            inds = [self.index[n] for n in e.nodes]
            v, grad, b, q = tetra(self.xyz[inds])
            mat = self.assignments[e.body_id]
            dofs = np.array([3*i+j for i in inds for j in range(3)])
            if self.mechanical:
                d = elastic_matrix(mat)
                ke = v * b.T @ d @ b
                rows.extend(np.repeat(dofs, 12)); cols.extend(np.tile(dofs, 12)); values.extend(ke.ravel())
            else:
                d = None
            if mat.density_g_cm3:
                self.mass[inds] += density_tonne_mm3(mat.density_g_cm3) * v / 4
            if mat.conductivity_w_mk:
                kt = v * mat.conductivity_w_mk / 1000 * grad @ grad.T
                self.thermal_rows.extend(np.repeat(inds, 4)); self.thermal_cols.extend(np.tile(inds, 4)); self.thermal_values.extend(kt.ravel())
            self.elements.append((e, inds, dofs, v, grad, b, d))
            self.volume += v
            self.quality = min(self.quality, q)
        self.k = sparse.coo_matrix((values, (rows, cols)), shape=(3*len(self.ids),)*2).tocsr()
        self.kt = sparse.coo_matrix((self.thermal_values, (self.thermal_rows, self.thermal_cols)), shape=(len(self.ids),)*2).tocsr()
        self.m = np.repeat(self.mass, 3)
        if self.quality < 1e-5:
            raise ValueError("Mesh quality below 1e-5; refine or repair geometry")

    def region(self, region_id):
        if region_id not in self.regions:
            raise ValueError(f"Missing region: {region_id}; repair mapping explicitly")
        return self.regions[region_id]

    def region_nodes(self, region_id):
        r = self.region(region_id)
        ids = sorted(set(r.node_ids) | {n for t in r.triangles for n in t})
        if not ids:
            raise ValueError(f"Region {region_id} has no mesh nodes")
        return [self.index[n] for n in ids]

    def triangles(self, region_id):
        region = self.region(region_id)
        if not region.triangles:
            raise ValueError(f"Region {region_id} requires oriented surface triangles")
        for tri in region.triangles:
            ind = [self.index[n] for n in tri]
            p = self.xyz[ind]
            cross = np.cross(p[1] - p[0], p[2] - p[0])
            area = np.linalg.norm(cross) / 2
            if area < 1e-12:
                raise ValueError("Degenerate boundary triangle")
            normal = unit(region.normal) if region.normal else unit(cross)
            yield ind, area, p.mean(axis=0), normal

    def transformation(self, case):
        directions = {}
        for support in case.supports:
            for i in self.region_nodes(support.region_id):
                if support.kind in {"fixed", "partial"}:
                    ds = np.eye(3)[np.asarray(support.components if support.kind == "partial" else (True,)*3)]
                elif support.kind == "frictionless":
                    direction = support.direction or self.region(support.region_id).normal
                    if direction is None:
                        raise ValueError("Frictionless support requires a sourced normal")
                    ds = [unit(direction)]
                else:
                    if support.direction is None:
                        raise ValueError("Pin support requires cylinder axis and origin")
                    axis = unit(support.direction)
                    radial = self.xyz[i] - np.asarray(support.origin_mm)
                    ds = [unit(radial - axis * radial.dot(axis))]
                directions.setdefault(i, []).extend(ds)
        rows, cols, data, c = [], [], [], 0
        for i in range(len(self.ids)):
            if i in directions:
                ds = np.array(directions[i])
                _, singular, vh = np.linalg.svd(ds, full_matrices=True)
                rank = int(sum(singular > 1e-9))
                basis = vh[rank:].T
            else:
                basis = np.eye(3)
            for j in range(basis.shape[1]):
                for axis in range(3):
                    if abs(basis[axis, j]) > 1e-14:
                        rows.append(i*3+axis); cols.append(c); data.append(basis[axis, j])
                c += 1
        return sparse.coo_matrix((data, (rows, cols)), shape=(3*len(self.ids), c)).tocsr()

    def loads(self, case):
        f = np.zeros(3*len(self.ids))
        for load in case.loads:
            vector = np.asarray(load.vector, dtype=float)
            if load.kind == "gravity":
                if self.mass.min() <= 0:
                    raise ValueError("Gravity requires sourced density on every body")
                masses = np.zeros(len(self.ids))
                for e, inds, _, v, *_ in self.elements:
                    if load.body_id is None or e.body_id == load.body_id:
                        masses[inds] += density_tonne_mm3(self.assignments[e.body_id].density_g_cm3) * v / 4
                f += (masses[:, None] * vector).ravel()
                continue
            if not load.region_id:
                raise ValueError(f"Load {load.id} requires a region")
            inds = self.region_nodes(load.region_id)
            if load.kind in {"moment", "remote"}:
                # Minimum-norm distribution preserves both requested force and moment.
                points = self.xyz[inds] - np.asarray(load.origin_mm)
                a = np.zeros((6, 3*len(inds)))
                for j, p in enumerate(points):
                    a[:3, 3*j:3*j+3] = np.eye(3)
                    x, y, z = p
                    a[3:, 3*j:3*j+3] = [[0, -z, y], [z, 0, -x], [-y, x, 0]]
                desired = np.concatenate([vector if load.kind == "remote" else np.zeros(3), vector if load.kind == "moment" else np.asarray(load.axis or (0, 0, 0))])
                distributed, *_ = np.linalg.lstsq(a, desired, rcond=1e-12)
                if np.linalg.norm(a@distributed-desired) > 1e-8*max(np.linalg.norm(desired), 1):
                    raise ValueError("Region cannot transmit the requested remote moment")
                for j, i in enumerate(inds):
                    f[3*i:3*i+3] += distributed[j*3:j*3+3]
                continue
            triangles = list(self.triangles(load.region_id))
            area_total = sum(a for _, a, _, _ in triangles)
            weights = None
            if load.kind == "bearing":
                if not load.axis:
                    raise ValueError("Bearing load requires cylinder axis")
                axis, direction = unit(load.axis), unit(vector)
                weights = []
                for _, area, center, _ in triangles:
                    radial = center - np.asarray(load.origin_mm)
                    radial -= axis * radial.dot(axis)
                    weights.append(area * max(0, unit(radial).dot(direction)))
                if sum(weights) < 1e-12:
                    raise ValueError("Bearing load has no projected loaded area")
            for j, (nodes, area, center, normal) in enumerate(triangles):
                if load.kind == "force":
                    resultant = vector * area / area_total
                elif load.kind == "bearing":
                    resultant = vector * weights[j] / sum(weights)
                elif load.kind == "pressure":
                    resultant = -load.magnitude * area * normal  # MPa=N/mm2, positive compression.
                elif load.kind == "hydrostatic":
                    if load.density_kg_m3 is None or np.linalg.norm(vector) <= 0:
                        raise ValueError("Hydrostatic load requires fluid density and gravity mm/s2")
                    # Clip at the free surface, then integrate original face shape
                    # functions times linear depth over each wet subtriangle.
                    points = self.xyz[nodes]
                    direction = unit(vector)
                    depths = (points-np.asarray(load.origin_mm))@direction
                    polygon = [(points[a], np.eye(3)[a], float(depths[a])) for a in range(3)]
                    clipped = []
                    for a in range(len(polygon)):
                        start, end = polygon[a-1], polygon[a]
                        if (start[2]>=0)!=(end[2]>=0):
                            fraction=start[2]/(start[2]-end[2])
                            clipped.append((start[0]+fraction*(end[0]-start[0]),start[1]+fraction*(end[1]-start[1]),0.))
                        if end[2]>=0:clipped.append(end)
                    integrated=np.zeros(3)
                    for a in range(1,len(clipped)-1):
                        sub=[clipped[0],clipped[a],clipped[a+1]]
                        subarea=np.linalg.norm(np.cross(sub[1][0]-sub[0][0],sub[2][0]-sub[0][0]))/2
                        shape=np.array([p[1] for p in sub]);head=np.array([p[2] for p in sub])
                        integrated+=subarea/12*(shape.sum(axis=0)*head.sum()+shape.T@head)
                    coefficient=fluid_density_tonne_mm3(load.density_kg_m3)*np.linalg.norm(vector)
                    for a,i in enumerate(nodes):f[3*i:3*i+3]-=normal*coefficient*integrated[a]
                    continue
                else:
                    raise ValueError(f"Unsupported load: {load.kind}")
                for i in nodes:
                    f[3*i:3*i+3] += resultant / 3
        return f

    def interface_stiffness(self, u=None, activate_all=False):
        rows, cols, vals = [], [], []
        initial = np.zeros(3*len(self.ids))
        results = []
        for interface in self.study.interfaces:
            if interface.kind in {"disabled", "thermal"}:
                continue
            if interface.stiffness_n_mm is None or not interface.node_pairs:
                raise ValueError("Mechanical interfaces require sourced pair stiffness and node pairs")
            if interface.kind == "separation" and interface.normal is None:
                raise ValueError("Separation interface requires oriented normal")
            directions = np.eye(3) if interface.kind == "bonded" else [unit(interface.normal)]
            for na, nb in interface.node_pairs:
                if na not in self.index or nb not in self.index or na == nb:
                    raise ValueError("Interface pair references missing or identical nodes")
                ia, ib = self.index[na], self.index[nb]
                for direction in directions:
                    ids = np.array([ia*3+j for j in range(3)] + [ib*3+j for j in range(3)])
                    v = np.concatenate([-direction, direction])
                    gap = float(v@u[ids]) + interface.initial_gap_mm if u is not None else interface.initial_gap_mm
                    active = interface.kind == "bonded" or gap <= 1e-12 or activate_all
                    if active:
                        ke = interface.stiffness_n_mm * np.outer(v, v)
                        rows.extend(np.repeat(ids, 6)); cols.extend(np.tile(ids, 6)); vals.extend(ke.ravel())
                        if interface.kind == "separation":
                            initial[ids] += interface.stiffness_n_mm * interface.initial_gap_mm * v
                    results.append({"interface_id": interface.id, "nodes": [na, nb], "active": bool(active), "gap_mm": gap, "normal_force_n": max(0., -interface.stiffness_n_mm*gap) if interface.kind == "separation" else None})
        for bolt in self.study.bolts:
            if bolt.node_a not in self.index or bolt.node_b not in self.index:
                raise ValueError("Bolt references missing nodes")
            a, b = self.index[bolt.node_a], self.index[bolt.node_b]
            delta = self.xyz[b] - self.xyz[a]
            length = np.linalg.norm(delta)
            direction = unit(delta)
            ids = np.array([a*3+j for j in range(3)] + [b*3+j for j in range(3)])
            v = np.concatenate([-direction, direction])
            k = bolt.youngs_modulus_mpa * bolt.area_mm2 / length
            rows.extend(np.repeat(ids, 6)); cols.extend(np.tile(ids, 6)); vals.extend((k*np.outer(v,v)).ravel())
            initial[ids] += bolt.preload_n * v
            results.append({"bolt_id": bolt.id, "axial_force_n": float(k * (v@u[ids] if u is not None else 0) + bolt.preload_n), "idealization": "axial_truss; no thread, washer, bending or torque conversion"})
        return sparse.coo_matrix((vals, (rows,cols)), shape=self.k.shape).tocsr(), initial, results

    def strain_fields(self, u, temperatures=None, reference=None):
        stress, strain, vm, kg_rows, kg_cols, kg_vals = {}, {}, {}, [], [], []
        for e, inds, dofs, volume, gradients, b, d in self.elements:
            eps = b@u[dofs]
            thermal = np.zeros(6)
            if temperatures is not None:
                alpha = self.assignments[e.body_id].expansion_per_k
                if alpha is None:
                    raise ValueError(f"Body {e.body_id} requires sourced thermal expansion")
                thermal[:3] = alpha * (np.mean(temperatures[inds]) - reference)
            sigma = d@(eps-thermal)
            sx, sy, sz, xy, yz, xz = sigma
            vm[e.id] = float(np.sqrt(.5*((sx-sy)**2+(sy-sz)**2+(sz-sx)**2)+3*(xy*xy+yz*yz+xz*xz)))
            stress[e.id], strain[e.id] = sigma.tolist(), eps.tolist()
            tensor = np.array([[sx,xy,xz],[xy,sy,yz],[xz,yz,sz]])
            kg = volume * gradients@tensor@gradients.T
            for axis in range(3):
                ids = np.array(inds)*3+axis
                kg_rows.extend(np.repeat(ids,4)); kg_cols.extend(np.tile(ids,4)); kg_vals.extend(kg.ravel())
        kg = sparse.coo_matrix((kg_vals,(kg_rows,kg_cols)),shape=self.k.shape).tocsr()
        return {"stress_mpa": stress, "engineering_strain": strain, "von_mises_mpa": vm}, kg


def solve_reduced(k, rhs, t):
    if t.shape[1]==0:return np.zeros(t.shape[0]),0.
    reduced = (t.T@k@t).tocsc()
    force = np.asarray(t.T@rhs).ravel()
    with warnings.catch_warnings():
        warnings.simplefilter("error", sla.MatrixRankWarning)
        try:
            factor = sla.splu(reduced)
            stiffness_scale=max(float(np.max(np.abs(reduced).sum(axis=1))),1e-30)
            pivots=np.abs(factor.U.diagonal())
            if len(pivots)!=len(force) or pivots.min()<stiffness_scale*1e-13:
                raise ValueError("Unrestrained rigid body modes or ill-conditioned stiffness; repair supports/load paths")
            q = factor.solve(force)
        except (sla.MatrixRankWarning, RuntimeError) as exc:
            raise ValueError("Mechanical system is singular; repair missing supports or disconnected load paths") from exc
    if not np.isfinite(q).all():
        raise ValueError("Mechanical system is singular")
    # Near-singular systems sometimes return finite huge answers. Reject loss of positive stiffness.
    scale = max(abs(reduced.diagonal()).max(), 1.)
    if len(q) <= 1200:
        smallest = linalg.eigvalsh(reduced.toarray(), subset_by_index=[0,0])[0]
        if smallest < scale*1e-12:
            raise ValueError("Unrestrained rigid body modes or nonpositive stiffness")
    u = np.asarray(t@q).ravel()
    residual = float(np.linalg.norm(t.T@(k@u-rhs))/max(np.linalg.norm(force),1e-12))
    return u, residual


def relief_load(system, f):
    if system.mass.min() <= 0:
        raise ValueError("Inertial relief requires positive sourced mass on every body")
    parents={n:n for n in system.ids}
    def root(n):
        while parents[n]!=n:parents[n]=parents[parents[n]];n=parents[n]
        return n
    for e,*_ in system.elements:
        first=root(e.nodes[0])
        for n in e.nodes[1:]:parents[root(n)]=first
    if len({root(n) for n in system.ids})!=1:raise ValueError("Inertial relief currently requires one connected solid mesh")
    xyz, m = system.xyz, system.mass
    center = (xyz*m[:,None]).sum(axis=0)/m.sum()
    r = xyz-center
    forces = f.reshape(-1,3)
    total, torque = forces.sum(axis=0), np.cross(r, forces).sum(axis=0)
    inertia = sum((mi*((ri@ri)*np.eye(3)-np.outer(ri,ri)) for ri,mi in zip(r,m)),np.zeros((3,3)))
    acceleration = total/m.sum()
    angular = np.linalg.solve(inertia,torque)
    relief = m[:,None]*(acceleration+np.cross(angular,r))
    return f-relief.ravel(), {"translation_acceleration_mm_s2": acceleration.tolist(), "angular_acceleration_rad_s2": angular.tolist(), "center_mm": center.tolist(), "inertia_tonne_mm2": inertia.tolist(), "original_force_n": total.tolist(), "original_moment_n_mm": torque.tolist()}


def static_solve(system, case, cancel=None, temperatures=None):
    f = system.loads(case)
    t = system.transformation(case)
    thermal_f = np.zeros_like(f)
    if temperatures is not None:
        reference = system.study.stress_free_temperature_k
        if reference is None:
            raise ValueError("Thermal stress requires stress-free reference temperature kelvin")
        for e, inds, dofs, volume, _, b, d in system.elements:
            alpha = system.assignments[e.body_id].expansion_per_k
            if alpha is None:
                raise ValueError("Thermal stress requires expansion coefficient for each body")
            eps = np.array([1,1,1,0,0,0]) * alpha*(np.mean(temperatures[inds])-reference)
            thermal_f[dofs] += volume*b.T@d@eps
    relief = None
    if system.study.settings.inertial_relief:
        if case.supports or system.study.type not in {"linear_static", "thermal_stress", "topology", "generative"}:
            raise ValueError("Inertial relief requires a free eligible model and cannot combine with buckling")
        if system.study.interfaces or system.study.bolts:
            raise ValueError("Inertial relief for assemblies/connectors is unavailable")
        f, relief = relief_load(system, f)
    elif not case.supports:
        raise ValueError("Static analysis requires supports or explicit inertial relief")
    u = np.zeros_like(f)
    active_last = None
    history = []
    for iteration in range(system.study.settings.max_iterations):
        check_cancel(cancel)
        ki, initial, interface = system.interface_stiffness(u, activate_all=(iteration==0))
        k = system.k+ki
        rhs = f+thermal_f-initial
        if relief:
            # Gauge constraints remove only the six rigid displacements. No elastic supports added.
            r = system.xyz-np.array(relief["center_mm"])
            rigid = np.zeros((len(f),6))
            for i,p in enumerate(r):
                rigid[3*i:3*i+3,:3]=np.eye(3)
                x,y,z=p
                rigid[3*i:3*i+3,3:]=[[0,z,-y],[-z,0,x],[y,-x,0]]
            q,_ = np.linalg.qr(rigid)
            g = sparse.csr_matrix(q)
            a = sparse.bmat([[k,g],[g.T,None]],format="csc")
            solution = sla.spsolve(a,np.concatenate([rhs,np.zeros(6)]))
            candidate=solution[:len(f)]
            residual=float(np.linalg.norm(k@candidate-rhs)/max(np.linalg.norm(rhs),1e-12))
        else:
            candidate,residual=solve_reduced(k,rhs,t)
        _,_,new_interfaces=system.interface_stiffness(candidate)
        active=tuple(x["active"] for x in new_interfaces if "active" in x)
        history.append({"iteration":iteration+1,"residual":residual,"active_pairs":sum(active)})
        u=candidate
        if active==active_last or not any(i.kind=="separation" for i in system.study.interfaces):
            break
        active_last=active
    else:
        raise ValueError("Contact active set did not converge; reduce load or repair interface data")
    ki,initial,interface=system.interface_stiffness(u)
    k=system.k+ki
    residual_vector=k@u+initial-thermal_f-f
    reactions=residual_vector.reshape(-1,3)
    applied=f.reshape(-1,3)
    force_scale=max(np.linalg.norm(applied,axis=1).sum(),np.linalg.norm(thermal_f.reshape(-1,3),axis=1).sum(),np.linalg.norm(initial.reshape(-1,3),axis=1).sum(),1e-12)
    characteristic=max(np.linalg.norm(np.ptp(system.xyz,axis=0)),1e-12)
    force_error=float(np.linalg.norm((reactions+applied).sum(axis=0))/force_scale)
    moment_error=float(np.linalg.norm(np.cross(system.xyz,reactions+applied).sum(axis=0))/(force_scale*characteristic))
    fields,kg=system.strain_fields(u,temperatures,system.study.stress_free_temperature_k)
    fields.update({"nodes":{n:system.xyz[i].tolist() for i,n in enumerate(system.ids)},"displacements_mm":{n:u[3*i:3*i+3].tolist() for i,n in enumerate(system.ids)},"reactions_n":{n:reactions[i].tolist() for i,n in enumerate(system.ids)}})
    values={"max_displacement_mm":float(np.linalg.norm(u.reshape(-1,3),axis=1).max()),"max_von_mises_mpa":max(fields["von_mises_mpa"].values()),"mass_g":float(system.mass.sum()*GRAMS_PER_TONNE) if all(m.density_g_cm3 is not None for m in system.assignments.values()) else None,"strain_energy_n_mm":float(.5*u@(system.k@u))}
    checks={"residual":residual,"force_balance_error":force_error,"moment_balance_error":moment_error,"contact_iterations":history}
    if not system.study.interfaces and not system.study.bolts and temperatures is None:
        external_work=float(.5*u@f)
        checks["energy_balance_error"]=abs(values["strain_energy_n_mm"]-external_work)/max(abs(external_work),abs(values["strain_energy_n_mm"]),1e-12)
    if relief:
        checks["inertial_relief"]=relief
    return {"values":values,"checks":checks,"field":fields,"interfaces":interface,"u":u,"kg":kg,"k":k,"t":t}


def thermal_solve(system, case, cancel=None):
    for body,mat in system.assignments.items():
        if mat.conductivity_w_mk is None:
            raise ValueError(f"Body {body} requires thermal conductivity provenance")
    n=len(system.ids)
    k=system.kt.copy().tolil()
    q=np.zeros(n)
    prescribed={}
    radiation=[]
    convection=[]
    for interface in system.study.interfaces:
        if interface.kind=="disabled":
            continue
        if interface.kind in {"bonded","separation"} and system.study.type=="thermal_stress":continue
        if interface.kind!="thermal" or interface.conductance_w_k is None:
            raise ValueError("Thermal solve requires explicit thermal pair conductance interfaces")
        for a,b in interface.node_pairs:
            if a not in system.index or b not in system.index:
                raise ValueError("Thermal interface references missing node")
            i,j=system.index[a],system.index[b]
            c=interface.conductance_w_k
            k[i,i]+=c;k[j,j]+=c;k[i,j]-=c;k[j,i]-=c
    for bc in case.thermal:
        if bc.kind=="body_heat":
            if bc.body_id not in system.assignments:
                raise ValueError("Body heat requires an assigned body")
            elems=[x for x in system.elements if x[0].body_id==bc.body_id]
            volume=sum(x[3] for x in elems)
            for _,inds,_,v,*_ in elems:
                q[inds]+=bc.value*v/volume/4  # value is total W on body.
            continue
        if not bc.region_id:
            raise ValueError("Thermal boundary requires a region")
        inds=system.region_nodes(bc.region_id)
        if bc.kind=="temperature":
            for i in inds:
                if i in prescribed and abs(prescribed[i]-bc.value)>1e-9:
                    raise ValueError("Conflicting temperatures on shared region nodes")
                prescribed[i]=bc.value
            continue
        triangles=list(system.triangles(bc.region_id))
        total=sum(a for _,a,*_ in triangles)
        for inds,area,*_ in triangles:
            if bc.kind in {"heat_power","heat_flux"}:
                q[inds]+=bc.value*area/(total if bc.kind=="heat_power" else 1)/3 # heat_flux W/mm2.
            elif bc.kind=="convection":
                # Consistent triangular Robin matrix; film coefficient W/(m2 K).
                c=bc.value*1e-6*area/12*(np.ones((3,3))+np.eye(3))
                for a,i in enumerate(inds):
                    q[i]+=bc.value*1e-6*area*bc.ambient_k/3
                    for b,j in enumerate(inds):
                        k[i,j]+=c[a,b]
                convection.append((inds,bc.value*1e-6*area/3,bc.ambient_k))
            else:
                for i in inds:
                    radiation.append((i,bc.value*5.670374419e-14*area/3,bc.ambient_k))
    if not prescribed and not radiation and not convection:
        raise ValueError("Steady thermal model needs temperature, convection or radiation heat rejection")
    k=k.tocsr()
    fixed=np.array(sorted(prescribed),dtype=int)
    free=np.array([i for i in range(n) if i not in prescribed],dtype=int)
    temperature=np.full(n,float(np.mean(list(prescribed.values()))) if prescribed else 300.)
    for i,val in prescribed.items():temperature[i]=val
    history=[]
    for iteration in range(system.study.settings.max_iterations):
        check_cancel(cancel)
        r=k@temperature-q
        jac=np.zeros(n)
        for i,c,ambient in radiation:
            r[i]+=c*(temperature[i]**4-ambient**4)
            jac[i]+=4*c*temperature[i]**3
        if not len(free):
            residual=0.;break
        tangent=k+sparse.diags(jac)
        with warnings.catch_warnings():
            warnings.simplefilter("error",sla.MatrixRankWarning)
            try:delta=sla.spsolve(tangent[free][:,free].tocsc(),-r[free])
            except (sla.MatrixRankWarning,RuntimeError) as exc:raise ValueError("Thermal model has an isolated unconstrained component") from exc
        if not np.isfinite(delta).all():raise ValueError("Thermal model is singular")
        step=1.
        while np.min(temperature[free]+step*delta)<=0:step*=.5
        temperature[free]+=step*delta
        residual=float(np.linalg.norm(r[free])/max(np.linalg.norm(q),np.linalg.norm(abs(k)@abs(temperature)),1e-12))
        history.append({"iteration":iteration+1,"relative_residual":residual,"temperature_update_k":float(np.max(np.abs(step*delta)))})
        if np.max(np.abs(delta))*step<1e-9 and residual<system.study.settings.residual_tolerance:
            break
    else:raise ValueError("Thermal radiation iteration did not converge")
    r=k@temperature-q
    for i,c,ambient in radiation:r[i]+=c*(temperature[i]**4-ambient**4)
    residual=float(np.linalg.norm(r[free])/max(np.linalg.norm(q),np.linalg.norm(abs(k)@abs(temperature)),1e-12)) if len(free) else 0.
    heat_out=sum(c*(temperature[i]**4-ambient**4) for i,c,ambient in radiation)
    heat_out+=sum(c*sum(temperature[inds]-ambient) for inds,c,ambient in convection)
    thermostat=float(r[fixed].sum()) if len(fixed) else 0.
    input_power=float(q.sum())-sum(c*len(inds)*ambient for inds,c,ambient in convection)
    heat_balance=abs(input_power+thermostat-heat_out)/max(abs(input_power),float(np.abs(r[fixed]).sum()) if len(fixed) else 0.,abs(heat_out),1e-9)
    flux={e.id:(-system.assignments[e.body_id].conductivity_w_mk/1000*grad.T@temperature[inds]).tolist() for e,inds,_,_,grad,*_ in system.elements}
    return {"values":{"max_temperature_k":float(temperature.max()),"min_temperature_k":float(temperature.min()),"applied_heat_w":input_power,"heat_rejected_w":float(heat_out),"thermostat_heat_w":thermostat},"checks":{"residual":residual,"heat_balance_error":float(heat_balance),"thermal_iterations":history},"field":{"nodes":{n:system.xyz[i].tolist() for i,n in enumerate(system.ids)},"temperature_k":{n:float(temperature[i]) for i,n in enumerate(system.ids)},"element_heat_flux_w_mm2":flux},"temperature":temperature}


def modal_solve(system,case,cancel=None):
    check_cancel(cancel)
    if system.mass.min()<=0:raise ValueError("Modal analysis requires sourced positive density on all bodies")
    if any(i.kind=="separation" for i in system.study.interfaces):raise ValueError("Modal separation contact is unsupported; define bonded interfaces")
    if system.study.settings.inertial_relief:raise ValueError("Modal study does not use inertial relief")
    t=system.transformation(case)
    ki,_,_=system.interface_stiffness()
    k=(t.T@(system.k+ki)@t).tocsc()
    m=(t.T@sparse.diags(system.m)@t).tocsc()
    if k.shape[0]==0:raise ValueError("Modal study has no unconstrained mechanical degrees of freedom")
    count=min(system.study.settings.mode_count,k.shape[0])
    if k.shape[0]>2500:
        vals,vecs=sla.eigsh(k,k=min(count,k.shape[0]-1),M=m,which="SM",tol=1e-8,maxiter=10000)
    else:vals,vecs=linalg.eigh(k.toarray(),m.toarray(),subset_by_index=[0,count-1])
    order=np.argsort(vals);vals=vals[order];vecs=vecs[:,order]
    modes=[]
    # Eigenvalue zero criterion relative to spectrum, not an arbitrary frequency cutoff.
    rigid_cutoff=max(abs(vals).max()*1e-9,1e-5)
    for j,val in enumerate(vals):
        phi=vecs[:,j];full=np.asarray(t@phi).ravel()
        lhs=k@phi;rhs=val*(m@phi)
        residual=float(np.linalg.norm(lhs-rhs)/max(np.linalg.norm(lhs),np.linalg.norm(rhs),np.linalg.norm(k.data)*np.linalg.norm(phi)*1e-13))
        participation=[]
        for axis in range(3):
            influence=np.zeros(len(system.m));influence[axis::3]=1
            generalized=float(full@(system.m*influence))
            participation.append({"axis":"XYZ"[axis],"effective_mass_tonne":generalized**2,"effective_mass_fraction":generalized**2/system.mass.sum()})
        modes.append({"mode":j+1,"frequency_hz":math.sqrt(max(0.,float(val)))/(2*math.pi),"eigenvalue_s_2":float(val),"rigid_body":bool(abs(val)<=rigid_cutoff),"unstable":bool(val < -rigid_cutoff),"residual":residual,"participation":participation,"mass_normalized_displacement":{n:full[i*3:i*3+3].tolist() for i,n in enumerate(system.ids)}})
    nonrigid=[x for x in modes if not x["rigid_body"] and not x["unstable"]]
    gram=vecs.T@m@vecs
    return {"values":{"min_frequency_hz":min((x["frequency_hz"] for x in nonrigid),default=0),"mass_g":float(system.mass.sum()*GRAMS_PER_TONNE)},"checks":{"residual":max((x["residual"] for x in modes if not x["rigid_body"]),default=0),"mass_orthogonality_error":float(np.max(np.abs(gram-np.eye(len(vals)))))},"modes":modes,"field":{"nodes":{n:system.xyz[i].tolist() for i,n in enumerate(system.ids)},"modes":modes}}


def buckling_solve(system,case,cancel=None):
    if system.study.settings.inertial_relief:raise ValueError("Buckling cannot combine with inertial relief")
    if any(i.kind=="separation" for i in system.study.interfaces):raise ValueError("Eigenvalue buckling with evolving contact is unavailable")
    base=static_solve(system,case,cancel)
    t=base["t"]
    a=(t.T@base["k"]@t).toarray()
    b=-(t.T@base["kg"]@t).toarray()
    if not len(a):raise ValueError("Buckling study has no unconstrained mechanical degrees of freedom")
    if len(a)>1500:raise ValueError("Reference buckling worker limited to 1500 unconstrained degrees; use a qualified large-model adapter")
    vals,vecs=linalg.eig(a,b)
    modes=[]
    for j,val in enumerate(vals):
        if not np.isfinite(val) or abs(val.imag)>1e-7 or abs(val.real)<1e-8:continue
        phi=vecs[:,j].real
        residual=float(np.linalg.norm(a@phi-val.real*b@phi)/max(np.linalg.norm(a@phi),1e-12))
        full=np.asarray(t@phi).ravel();full/=max(np.linalg.norm(full.reshape(-1,3),axis=1).max(),1e-12)
        modes.append({"load_factor":float(val.real),"residual":residual,"shape":{n:full[i*3:i*3+3].tolist() for i,n in enumerate(system.ids)}})
    modes.sort(key=lambda x:abs(x["load_factor"]))
    positive=[x for x in modes if x["load_factor"]>0]
    if not positive:raise ValueError("No positive buckling factor; inspect load sign and compressive prestress")
    modes=modes[:system.study.settings.mode_count]
    return {"values":{"min_buckling_factor":min(x["load_factor"] for x in positive)},"checks":{"residual":max(x["residual"] for x in modes),"prestress":base["checks"]},"modes":modes,"field":{"nodes":{n:system.xyz[i].tolist() for i,n in enumerate(system.ids)},"modes":modes},"warnings":["Linearized ideal eigenvalue instability; no imperfection-sensitive collapse or postbuckling prediction."]}


def event_solve(system,case,cancel=None,step_scale=1.):
    if system.mass.min()<=0:raise ValueError("Event simulation requires sourced density")
    if system.study.settings.large_deformation:raise ValueError("Event worker is small strain; large motion requires an explicit nonlinear adapter")
    if system.study.settings.inertial_relief:raise ValueError("Events assemble physical inertia; inertial relief is inapplicable")
    t=system.transformation(case)
    # Orthogonal node-local bases retain a diagonal lumped mass after reduction.
    if t.shape[1]==0:raise ValueError("Event study has no unconstrained mechanical degrees of freedom")
    mass=np.asarray(t.power(2).T@system.m).ravel()
    initial_k,_,_=system.interface_stiffness(activate_all=True)
    k=(t.T@(system.k+initial_k)@t).tocsr()
    minv=1/np.sqrt(mass)
    scaled=sparse.diags(minv)@k@sparse.diags(minv)
    if len(mass)<3:largest=float(linalg.eigvalsh(scaled.toarray())[-1])
    else:largest=float(sla.eigsh(scaled,k=1,which="LA",return_eigenvectors=False)[0])
    stable=2/math.sqrt(largest)
    settings=system.study.settings
    dt=(settings.time_step_s or stable*.7)*step_scale
    if dt>stable*.9:raise ValueError(f"Requested timestep exceeds 90% of central-difference stability limit {stable:.6g} s")
    steps=math.ceil(settings.duration_s/dt)
    if steps>settings.max_time_steps:raise ValueError(f"Event requires {steps} steps, exceeding budget {settings.max_time_steps}; no mass scaling applied")
    dt=settings.duration_s/steps
    if case.history and case.history[-1][0]<settings.duration_s:raise ValueError("Load history does not cover the entire event")
    if not case.history and case.loads:raise ValueError("Event loads require an explicit time multiplier history")
    load=system.loads(case)
    def external(time):return load*np.interp(time,[p[0] for p in case.history],[p[1] for p in case.history]) if case.history else np.zeros_like(load)
    def internal(q):
        u=np.asarray(t@q).ravel()
        ki,preload,interfaces=system.interface_stiffness(u)
        return np.asarray(t.T@((system.k+ki)@u+preload)).ravel(),interfaces
    q=np.zeros(t.shape[1]);velocity=np.asarray(t.T@np.tile(case.initial_velocity_mm_s,len(system.ids))).ravel()
    fi,interfaces=internal(q)
    force=np.asarray(t.T@external(0)).ravel()
    acc=(force-fi)/mass-settings.damping_per_s*velocity
    initial_energy=float(.5*np.dot(mass*velocity,velocity))
    work,dissipation=0.,0.
    history=[]
    max_error=0.
    peak_displacement=0.;peak_stress=0.;peak_energy_scale=max(initial_energy,1e-12);peak_energy_residual=0.
    stress_operators=np.array([d@b for _,_,_,_,_,b,d in system.elements])
    element_dofs=np.array([dofs for _,_,dofs,*_ in system.elements])
    stress_envelope=np.zeros(len(system.elements));energy_pairs=[]
    for step in range(steps+1):
        if step%100==0:check_cancel(cancel)
        kinetic=float(.5*np.dot(mass*velocity,velocity))
        # Contact with gap: spring energy is only the closed penetration squared.
        u=np.asarray(t@q).ravel();strain=float(.5*u@(system.k@u))
        for interface in system.study.interfaces:
            if interface.kind in {"disabled","thermal"}:continue
            for a,b in interface.node_pairs:
                ia,ib=system.index[a],system.index[b]
                delta=u[ib*3:ib*3+3]-u[ia*3:ia*3+3]
                if interface.kind=="bonded":strain+=.5*interface.stiffness_n_mm*float(delta@delta)
                else:strain+=.5*interface.stiffness_n_mm*min(0,float(delta@unit(interface.normal))+interface.initial_gap_mm)**2
        if system.study.bolts:raise ValueError("Event bolt pretension sequence is unavailable; use static bolt analysis")
        step_displacement=float(np.linalg.norm(u.reshape(-1,3),axis=1).max())
        peak_displacement=max(peak_displacement,step_displacement)
        sigma=np.einsum("eij,ej->ei",stress_operators,u[element_dofs])
        sx,sy,sz,xy,yz,xz=sigma.T
        equivalent=np.sqrt(.5*((sx-sy)**2+(sy-sz)**2+(sz-sx)**2)+3*(xy*xy+yz*yz+xz*xz))
        stress_envelope=np.maximum(stress_envelope,equivalent)
        peak_stress=max(peak_stress,float(equivalent.max()))
        if not np.isfinite(peak_stress+step_displacement+kinetic+strain):raise ValueError("Event integration diverged")
        energy_pairs.append((kinetic,strain))
        peak_energy_scale=max(peak_energy_scale,abs(work),kinetic+strain)
        peak_energy_residual=max(peak_energy_residual,abs(kinetic+strain+dissipation-work-initial_energy))
        error=abs(kinetic+strain+dissipation-work-initial_energy)/max(abs(work),abs(initial_energy),abs(strain),1e-12)
        max_error=max(max_error,error)
        if step==steps or step%max(1,steps//200)==0:
            history.append({"time_s":step*dt,"kinetic_energy_n_mm":kinetic,"strain_energy_n_mm":strain,"external_work_n_mm":work,"damping_energy_n_mm":dissipation,"relative_energy_error":float(error),"energy_residual_n_mm":float(kinetic+strain+dissipation-work-initial_energy),"max_displacement_mm":float(np.linalg.norm(u.reshape(-1,3),axis=1).max())})
        if step==steps:break
        next_q=q+dt*velocity+.5*dt*dt*acc
        next_force=np.asarray(t.T@external((step+1)*dt)).ravel()
        next_internal,interfaces=internal(next_q)
        half_velocity=velocity+.5*dt*acc
        next_acc=(next_force-next_internal-settings.damping_per_s*mass*half_velocity)/(mass*(1+.5*dt*settings.damping_per_s))
        next_velocity=half_velocity+.5*dt*next_acc
        work+=float(.5*(force+next_force)@(next_q-q))
        dissipation+=float(settings.damping_per_s*np.dot(mass*((velocity+next_velocity)/2),(velocity+next_velocity)/2)*dt)
        q,velocity,acc,force=next_q,next_velocity,next_acc,next_force
    energy_scale=peak_energy_scale
    for h in history:h["relative_energy_error"]=abs(h["energy_residual_n_mm"])/energy_scale
    max_error=peak_energy_residual/energy_scale
    fields,_=system.strain_fields(u)
    fields.update({"nodes":{n:system.xyz[i].tolist() for i,n in enumerate(system.ids)},"displacements_mm":{n:u[3*i:3*i+3].tolist() for i,n in enumerate(system.ids)},"time_history":history,"field_time_s":settings.duration_s,"field_state":"final","element_von_mises_envelope_mpa":{e.id:float(stress_envelope[i]) for i,(e,*_) in enumerate(system.elements)}})
    ratio=history[-1]["kinetic_energy_n_mm"]/max(history[-1]["strain_energy_n_mm"],1e-12)
    peak_internal=max(pair[1] for pair in energy_pairs)
    max_ratio=max((kinetic/max(strain,1e-12) for kinetic,strain in energy_pairs if strain>.05*peak_internal),default=0.)
    return {"values":{"max_displacement_mm":peak_displacement,"final_displacement_mm":history[-1]["max_displacement_mm"],"max_von_mises_mpa":peak_stress},"checks":{"residual":0.,"energy_balance_error":float(max_error),"final_kinetic_internal_ratio":float(ratio),"max_kinetic_internal_ratio":float(max_ratio),"time_step_s":dt,"stability_limit_s":stable,"time_steps":steps,"mass_scaling":False},"field":fields,"time_history":history,"interfaces":interfaces,"warnings":["Small-strain central-difference event; no damage, element deletion or rate-dependent material model."]}
