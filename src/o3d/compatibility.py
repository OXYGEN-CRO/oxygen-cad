"""Bounded mounting checks and canonical editable adapter proposals.

Comparison is pure. Proposal/acceptance use isolated current-engine compilation
and temporary independent STEP imports; they never mutate a Store or live design.
"""
from __future__ import annotations

import math
from pathlib import Path
import tempfile

from .model import Extrude, Hole, Parameter, Profile, Project, digest, geometry_key
from .compatibility_models import (
    AdapterAcceptance, AdapterHoleMeasurement, AdapterProposal, AdapterRequest,
    CompatibilityReason, CompatibilityRequest, CompatibilityResult, ComponentInterface,
    InterfaceSignature,
)

EPS = 1e-8  # mm numerical comparison allowance; never a manufacturing tolerance


def validate(value: ComponentInterface | dict) -> ComponentInterface:
    """Validate even an already-constructed model (including mutated models)."""
    return ComponentInterface.model_validate(value.model_dump() if isinstance(value, ComponentInterface) else value)


def source_signature(value: ComponentInterface | dict) -> InterfaceSignature:
    interface = validate(value)
    return InterfaceSignature(provider=interface.provider, sku=interface.sku,
                              asset_sha256=interface.asset_sha256, interface_id=interface.interface_id,
                              signature=digest(interface.model_dump(mode="json")))


def _scale(interface):
    return 25.4 if interface.units == "in" else 1.0


def _normal(interface):
    x, y = interface.frame.x_axis, interface.frame.y_axis
    return (x[1]*y[2]-x[2]*y[1], x[2]*y[0]-x[0]*y[2], x[0]*y[1]-x[1]*y[0])


def _point(interface, hole):
    frame = interface.frame
    return tuple(_scale(interface)*(frame.origin[i] + hole.center[0]*frame.x_axis[i] + hole.center[1]*frame.y_axis[i]) for i in range(3))


def _missing(interface, side):
    return [CompatibilityReason(code="missing_data", message=f"{side}: missing {name}")
            for name, value in (("asset_sha256", interface.asset_sha256), ("source/measured evidence", interface.evidence),
                                ("frame", interface.frame), ("hole geometry", interface.holes), ("tolerances", interface.tolerance)) if not value]


def _plane_problem(a, b):
    if a.frame.assembly_frame != b.frame.assembly_frame:
        return "Frames must name the same common assembly coordinate system"
    na, nb = _normal(a), _normal(b)
    if abs(abs(sum(x*y for x, y in zip(na, nb)))-1) > 1e-9:
        return "Nonparallel mounting planes are unsupported"
    delta = [_scale(b)*b.frame.origin[i] - _scale(a)*a.frame.origin[i] for i in range(3)]
    if abs(sum(d*n for d, n in zip(delta, na))) > EPS:
        return "Noncoplanar mounting planes are unsupported; supply an explicit common mounting plane"
    return None


def compare(value: CompatibilityRequest | dict) -> CompatibilityResult:
    request = CompatibilityRequest.model_validate(value.model_dump() if isinstance(value, CompatibilityRequest) else value)
    a, b = request.a, request.b
    sources = [source_signature(a), source_signature(b)]
    reasons = _missing(a, "a") + _missing(b, "b")
    if request.fastener_diameter_mm is None:
        reasons.append(CompatibilityReason(code="missing_fastener", message="An explicit fastener diameter in mm is required"))
    if reasons:
        return CompatibilityResult(status="unknown", sources=sources, reasons=reasons)
    problem = _plane_problem(a, b)
    if problem:
        return CompatibilityResult(status="unknown", sources=sources, reasons=[CompatibilityReason(code="unsupported_frame", message=problem)])
    ah, bh = {h.label: h for h in a.holes}, {h.label: h for h in b.holes}
    if ah.keys() != bh.keys():
        return CompatibilityResult(status="incompatible" if len(ah) != len(bh) else "unknown", sources=sources, reasons=[CompatibilityReason(
            code="hole_correspondence", message="Hole labels/count differ; explicit equal labels define the complete mounting correspondence")])
    failed = False
    for label in sorted(ah):
        distance = math.dist(_point(a, ah[label]), _point(b, bh[label]))
        da = _scale(a)*(ah[label].diameter-a.tolerance.diameter)
        db = _scale(b)*(bh[label].diameter-b.tolerance.diameter)
        limit = (da-request.fastener_diameter_mm)/2 + (db-request.fastener_diameter_mm)/2
        worst = distance + _scale(a)*a.tolerance.position + _scale(b)*b.tolerance.position
        fits = min(da, db) + EPS >= request.fastener_diameter_mm and worst <= limit + EPS
        failed |= not fits
        reasons.append(CompatibilityReason(code="hole_registration" if fits else "hole_mismatch", hole=label,
            measured_mm=worst, limit_mm=limit,
            message="Worst-case radial center mismatch versus combined minimum-diameter fastener clearance"))
    return CompatibilityResult(status="incompatible" if failed else "compatible", sources=sources, reasons=reasons)


def _adapter_dimensions(request):
    for side, interface in (("a", request.a), ("b", request.b)):
        missing = _missing(interface, side)
        if missing:
            raise ValueError("; ".join(r.message for r in missing))
    problem = _plane_problem(request.a, request.b)
    if problem:
        raise ValueError(problem)
    if any(abs(abs(_normal(i)[2])-1) > 1e-9 for i in (request.a, request.b)):
        raise ValueError("Adapter generator supports only common assembly XY mounting planes")
    holes = []
    for side, interface in (("a", request.a), ("b", request.b)):
        for index, hole in enumerate(interface.holes):
            holes.append((f"adapter_{side}_{index+1}", side, hole.label, _point(interface, hole),
                          hole.diameter*_scale(interface)+request.machining_clearance_mm,
                          (interface.tolerance.position + interface.tolerance.diameter/2)*_scale(interface)))
    # Worst-case sourced center/diameter errors are included in edge and web checks.
    xmin = min(p[0]-d/2-t for _, _, _, p, d, t in holes)-request.edge_clearance_mm
    xmax = max(p[0]+d/2+t for _, _, _, p, d, t in holes)+request.edge_clearance_mm
    ymin = min(p[1]-d/2-t for _, _, _, p, d, t in holes)-request.edge_clearance_mm
    ymax = max(p[1]+d/2+t for _, _, _, p, d, t in holes)+request.edge_clearance_mm
    w, h = request.width_mm or xmax-xmin, request.height_mm or ymax-ymin
    if w > 2000 or h > 2000:
        raise ValueError("Adapter plate exceeds the 2000 mm deterministic generator bound")
    return holes, (xmin+xmax)/2, (ymin+ymax)/2, holes[0][3][2], w, h


def _accept_source(request, project):
    holes, cx, cy, z, width, height = _adapter_dimensions(request)
    profile, extrude = project.features[:2]
    if not isinstance(profile, Profile) or not isinstance(extrude, Extrude):
        raise ValueError("Adapter feature graph must start with profile and extrusion")
    if profile.shape != "rectangle" or profile.plane != "XY" or profile.rotation != (0, 0, 0) or profile.suppressed or extrude.suppressed or extrude.operation != "add" or extrude.profile != profile.id:
        raise ValueError("Adapter plate modelling semantics changed")
    def close(actual, expected):
        if abs(actual-expected) > EPS:
            raise ValueError("Adapter dimensions or requested hole centers changed")
    def length(value):
        if isinstance(value, str) and project.parameters[value[1:]].unit != "mm":
            raise ValueError("Adapter dimensions require millimeter parameters")
        return project.resolve(value)
    for actual, expected in zip(profile.origin, (cx, cy, z)):
        close(actual, expected)
    close(length(profile.width), width)
    close(length(profile.height), height)
    close(length(extrude.distance), request.thickness_mm)
    if len(project.features) != len(holes)+2:
        raise ValueError("Adapter feature count changed")
    measurements = []
    for feature, (fid, side, label, point, diameter, uncertainty) in zip(project.features[2:], holes):
        if not isinstance(feature, Hole) or feature.id != fid or feature.axis != "Z" or not feature.through or feature.suppressed or feature.rows != 1 or feature.columns != 1:
            raise ValueError("Adapter hole semantics changed")
        for actual, expected in zip(feature.center, point):
            close(actual, expected)
        close(length(feature.diameter), diameter)
        edge = min(width/2-abs(point[0]-cx), height/2-abs(point[1]-cy))-diameter/2-uncertainty
        if edge + EPS < request.edge_clearance_mm:
            raise ValueError(f"{fid}: hole outside plate or insufficient edge clearance")
        measurements.append(AdapterHoleMeasurement(feature_id=fid, source_interface=side, label=label,
            center_mm=point, diameter_mm=diameter, edge_clearance_mm=edge))
    web = min(math.dist(a[3], b[3])-(a[4]+b[4])/2-a[5]-b[5]
              for index, a in enumerate(holes) for b in holes[index+1:])
    if web + EPS < request.web_clearance_mm:
        raise ValueError("Overlapping holes or insufficient machining web clearance; coincident holes are unsupported")
    return dict(width_mm=width, height_mm=height, thickness_mm=request.thickness_mm,
        holes=measurements, minimum_web_mm=web,
        expected_volume_mm3=(width*height-sum(math.pi*(h.diameter_mm/2)**2 for h in measurements))*request.thickness_mm)


def _canonical_acceptance(request, project, expected, *, shape=None, metadata=None):
    """Check the authoritative native geometry, never inactive legacy provenance."""
    if project.native_document is None or project.features:
        raise ValueError("Adapter acceptance requires a canonical native document without legacy features")
    document = project.native_document
    if document.get("links") or document.get("components") or document.get("occurrences") or any(feature.get("type") not in {"sketch", "extrude"} for feature in document["features"]):
        raise ValueError("Adapter acceptance only supports its generated sketch/extrude timeline without external links or assembly entities")
    if any(project.placement.translation) or any(project.placement.rotation):
        raise ValueError("Adapter placement changed")
    holes, cx, cy, z, width, height = _adapter_dimensions(request)
    parameters = {"adapter_width": width, "adapter_height": height, "adapter_thickness": request.thickness_mm}
    parameters.update({fid+"_diameter": diameter for fid, _, _, _, diameter, _ in holes})
    for name, value in parameters.items():
        parameter = project.parameters.get(name)
        if parameter is None or parameter.unit != "mm" or abs(parameter.value-value) > EPS:
            raise ValueError("Adapter parameter dimensions or units changed; regenerate against the edited request")
    if shape is None:
        from .native import compile_native
        with tempfile.TemporaryDirectory(prefix="oxygen-adapter-check-") as folder:
            shape, metadata = compile_native(project, Path(folder))
    import build123d as bd
    if shape is None or not shape.is_valid or len(shape.solids()) != 1:
        raise ValueError("Adapter must export one valid positive-volume solid")
    bodies = metadata.get("scene", {}).get("bodies", [])
    if len(bodies) != 1 or bodies[0].get("geometry_kind") != "solid":
        raise ValueError("Adapter scene contains unexpected bodies, surfaces, or meshes")
    tolerance = 1e-5  # Independent STEP/OCCT numerical tolerance, in mm.
    def check_vector(actual, target):
        if any(abs(a-b) > tolerance for a, b in zip(actual, target)):
            raise ValueError("Exported adapter dimensions or requested hole centers changed")
    bounds = shape.bounding_box()
    check_vector(list(bounds.min), (cx-width/2, cy-height/2, z))
    check_vector(list(bounds.max), (cx+width/2, cy+height/2, z+request.thickness_mm))
    if not math.isclose(shape.volume, expected["expected_volume_mm3"], rel_tol=1e-7, abs_tol=1e-5):
        raise ValueError("Exported adapter volume differs from the requested plate and holes")
    faces = list(shape.faces())
    cylinders = [face for face in faces if face.geom_type == bd.GeomType.CYLINDER]
    if len(cylinders) != len(holes) or len(faces) != len(holes)+6 or any(face.geom_type not in {bd.GeomType.CYLINDER, bd.GeomType.PLANE} for face in faces):
        raise ValueError("Exported adapter topology differs from the requested rectangular plate and through-holes")
    for _, _, _, point, diameter, _ in holes:
        matching = [face for face in cylinders
                    if math.hypot(face.axis_of_rotation.position.X-point[0], face.axis_of_rotation.position.Y-point[1]) <= tolerance
                    and abs(2*face.radius-diameter) <= tolerance]
        if len(matching) != 1:
            raise ValueError("Exported adapter hole center or diameter differs from the request")
        face = matching[0]
        hole_bounds = face.bounding_box()
        check_vector(list(hole_bounds.min), (point[0]-diameter/2, point[1]-diameter/2, z))
        check_vector(list(hole_bounds.max), (point[0]+diameter/2, point[1]+diameter/2, z+request.thickness_mm))
        cylinders.remove(face)
    return AdapterAcceptance(**expected, measured_volume_mm3=shape.volume, native_geometry_signature=geometry_key(project))


def _adapter_source(request, proposal):
    holes, cx, cy, z, width, height = _adapter_dimensions(request)
    parameters = {"adapter_width": Parameter(value=width), "adapter_height": Parameter(value=height),
                  "adapter_thickness": Parameter(value=request.thickness_mm)}
    for fid, _, _, _, diameter, _ in holes:
        parameters[fid+"_diameter"] = Parameter(value=diameter)
    if parameters.keys() & proposal.parameters.keys():
        raise ValueError("Adapter parameter IDs conflict with existing parameters; no values or locks were overwritten")
    proposal.parameters.update(parameters)
    proposal.features = [Profile(id="adapter_profile", name="Adapter plate", width="$adapter_width", height="$adapter_height", origin=(cx, cy, z)),
                         Extrude(id="adapter_plate", name="Plate thickness", profile="adapter_profile", distance="$adapter_thickness")]
    proposal.features += [Hole(id=fid, name=f"{side}:{label}", center=point, diameter=f"${fid}_diameter") for fid, side, label, point, _, _ in holes]
    return Project.model_validate(proposal.model_dump())


def propose_adapter(value: AdapterRequest | dict, project: Project | None = None) -> AdapterProposal:
    """Create a complete editable proposal; never write assets or mutate Store.

    A supplied project must have no native document or modeled features and identity
    placement. Existing studies, engineering/joint metadata, references, parameters
    and locks are preserved; generated parameter collisions fail.
    """
    request = AdapterRequest.model_validate(value.model_dump() if isinstance(value, AdapterRequest) else value)
    proposal = Project.model_validate(project.model_dump()) if project else Project(name="Sourced mounting adapter")
    if proposal.geometry_engine != "solidcraft" or proposal.native_document is not None or proposal.features or any(proposal.placement.translation) or any(proposal.placement.rotation):
        raise ValueError("Adapter proposal requires an empty modeled project with no native document and identity placement; use a separate project")
    if proposal.domains.get("modeling"):
        raise ValueError("Existing native modeling context or locks require a separate adapter project")
    proposal = _adapter_source(request, proposal)
    expected = _accept_source(request, proposal)
    from .native import compile_native
    with tempfile.TemporaryDirectory(prefix="oxygen-adapter-proposal-") as folder:
        shape, metadata = compile_native(proposal, Path(folder))
    proposal.native_document = metadata["canonical"]["document"]
    # Once native is canonical, a second legacy feature graph is misleading.
    proposal.features = []
    proposal.domains["modeling"] = {"schema_version": 1, "native_context": metadata["canonical"]["context"],
        "legacy_parameter_map": metadata["parameter_bindings"],
        "parameter_locks": {metadata["parameter_bindings"].get(name, name): True for name, p in proposal.parameters.items() if p.locked}}
    proposal = Project.model_validate(proposal.model_dump())
    return AdapterProposal(project=proposal, request=request, sources=[source_signature(request.a), source_signature(request.b)],
                           acceptance=_canonical_acceptance(request, proposal, expected, shape=shape, metadata=metadata))


def validate_adapter(proposal: AdapterProposal | dict) -> AdapterAcceptance:
    """Recheck editable source and source signatures after transport or edits."""
    value = AdapterProposal.model_validate(proposal.model_dump() if isinstance(proposal, AdapterProposal) else proposal)
    if value.sources != [source_signature(value.request.a), source_signature(value.request.b)]:
        raise ValueError("Source interface identity/signature changed; regenerate the proposal")
    expected = _accept_source(value.request, _adapter_source(value.request, Project()))
    return _canonical_acceptance(value.request, value.project, expected)
