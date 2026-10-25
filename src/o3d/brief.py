"""Trace explanatory intent to existing, measured requirement evidence.

No geometry, load, material, check or solver configuration is inferred here.
Binding captures evidence identities; edits use the ordinary Store command path.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import re
from pathlib import Path

from .brief_models import BriefEvidence, EngineeringBrief
from .model import Project, digest, geometry_key, study_key, verification_key

GEOMETRY_KINDS = {"max_mass", "max_dimension", "min_clearance"}
UNITS = {"max_mass": "g", "max_dimension": "mm", "min_clearance": "mm",
         "max_displacement": "mm", "max_temperature": "degC", "min_frequency": "Hz",
         "min_buckling_factor": "dimensionless", "max_stress": "MPa"}


def load_brief(value: EngineeringBrief | dict | str | Path) -> EngineeringBrief:
    """Validate an in-memory brief, JSON text, or an explicitly supplied Path."""
    if isinstance(value, Path):
        return EngineeringBrief.model_validate_json(value.read_text())
    if isinstance(value, str):
        return EngineeringBrief.model_validate_json(value)
    if isinstance(value, EngineeringBrief):
        value = value.model_dump()
    return EngineeringBrief.model_validate(value)


def _brief(project, brief):
    value = project.engineering_brief if brief is None else brief
    return load_brief(value) if value is not None else None


def _study_id(requirement, link):
    return link.study_id or requirement.study_id


def _parameter_issue(project, name):
    """Keep host aliases honest without inferring new native parameter mirrors.

    Native expressions beyond explicit numeric/unit literals remain unresolved.
    A retained legacy parameter is provenance, not proof its native target exists.
    """
    parameter = project.parameters.get(name)
    if project.native_document is None:
        return None if parameter is not None else ("missing_parameter", f"Parameter {name} does not exist")
    aliases = project.domains.get("modeling", {}).get("legacy_parameter_map", {})
    if not isinstance(aliases, dict):
        return "ambiguous_parameter", f"Canonical parameter aliases for {name} are invalid"
    native_name = aliases.get(name, name)
    rows = [p for p in project.native_document.get("params", []) if isinstance(p, dict) and p.get("name") == native_name]
    if not rows:
        return "missing_parameter", f"Canonical parameter {native_name} no longer exists; retained host mirror {name} is not an active reference"
    if len(rows) != 1 or len([alias for alias, target in aliases.items() if target == native_name]) > 1:
        return "ambiguous_parameter", f"Canonical parameter {native_name} has ambiguous definitions or host aliases"
    if parameter is None:
        return "unresolved_parameter", f"Native parameter {native_name} has no explicit host value/unit mirror; native-only expression bindings are not supported"
    native = rows[0]
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(mm|cm|m|in|ft|deg|rad)?\s*", str(native.get("expr", "")))
    if match is None:
        return "unresolved_parameter", f"Native parameter {native_name} is an expression; its host mirror requires explicit native expression evaluation"
    declared = native.get("unit")
    supplied = match.group(2) or declared
    lengths = {"mm": 1, "cm": 10, "m": 1000, "in": 25.4, "ft": 304.8}
    angles = {"deg": 1, "rad": 180 / math.pi}
    if parameter.unit == "mm" and declared in lengths and supplied in lengths:
        scale = lengths[supplied]
    elif parameter.unit == "deg" and declared in angles and supplied in angles:
        scale = angles[supplied]
    elif declared == supplied == "" and (parameter.unit == "dimensionless" or
            (name in aliases and parameter.unit in {"g", "N", "MPa"})):
        # Explicit legacy aliases preserve host physical metadata (g/N/MPa) that
        # the native expression grammar represents as unitless numerical inputs.
        scale = 1
    else:
        return "unit_mismatch", f"Canonical parameter {native_name} unit {declared!r} cannot establish host {parameter.unit}"
    value = float(match.group(1)) * scale
    if not math.isfinite(value) or not math.isclose(value, parameter.value, rel_tol=1e-12, abs_tol=1e-12):
        return "parameter_mirror_mismatch", f"Host parameter {name} differs from canonical parameter {native_name}"
    return None


def validate_brief(project: Project, brief=None) -> dict:
    """Semantic reference validation. Invalid schema raises Pydantic ValidationError.

    Broken references are returned for explicit repair instead of silently retargeted.
    """
    brief = _brief(project, brief)
    issues = []
    if brief is None:
        return {"valid": True, "issues": []}

    def issue(code, message, link_id=None, input_id=None):
        issues.append({"code": code, "message": message, "link_id": link_id, "input_id": input_id})

    counts = Counter(r.id for r in project.requirements)
    for identifier, count in counts.items():
        if count > 1:
            issue("ambiguous_requirement", f"Requirement ID {identifier} occurs {count} times")
    requirements = {r.id: r for r in project.requirements}
    inputs = {i.id: i for i in brief.inputs}
    for item in brief.inputs:
        if item.parameter is not None:
            parameter = project.parameters.get(item.parameter)
            problem = _parameter_issue(project, item.parameter)
            if problem is not None:
                issue(*problem, input_id=item.id)
            elif item.unit is not None and item.unit != parameter.unit:
                issue("unit_mismatch", f"{item.unit} does not match parameter {item.parameter} unit {parameter.unit}; no implicit conversion", input_id=item.id)
            elif item.value is not None and item.value != parameter.value:
                issue("parameter_value_mismatch", f"Input {item.id} differs from parameter {item.parameter}", input_id=item.id)
    for link in brief.links:
        requirement = requirements.get(link.requirement_id)
        if requirement is None:
            issue("missing_requirement", f"Requirement {link.requirement_id} does not exist", link.id)
        elif counts[link.requirement_id] > 1:
            issue("ambiguous_requirement", f"Requirement {link.requirement_id} is ambiguous", link.id)
        elif requirement.kind in GEOMETRY_KINDS:
            if link.study_id is not None:
                issue("unexpected_study", "Geometry requirements are global; remove this study reference", link.id)
        else:
            selected = _study_id(requirement, link)
            if selected is None:
                issue("ambiguous_study", "Select a study explicitly for an unscoped simulation requirement", link.id)
            elif selected != "default" and selected not in project.named_studies:
                issue("missing_study", f"Study {selected} does not exist", link.id)
            elif requirement.study_id is not None and selected != requirement.study_id:
                issue("study_scope_mismatch", f"Requirement is scoped to {requirement.study_id}", link.id)
        for name in link.parameter_refs:
            problem = _parameter_issue(project, name)
            if problem is not None:
                issue(*problem, link.id)
        for identifier in link.input_refs:
            if identifier not in inputs:
                issue("missing_input", f"Brief input {identifier} does not exist", link.id)
    return {"valid": not issues, "issues": issues}


def _signatures(project, brief, link, requirement):
    selected = _study_id(requirement, link)
    # All explanatory inputs participate: a changed global assumption can affect any link.
    intent = {"task": brief.task, "inputs": [i.model_dump() for i in brief.inputs],
              "link": link.model_dump(exclude={"evidence"}),
              "parameters": {name: project.parameters[name].model_dump() for name in link.parameter_refs}}
    if project.native_document is not None:
        aliases = project.domains.get("modeling", {}).get("legacy_parameter_map", {})
        names = set(link.parameter_refs) | {item.parameter for item in brief.inputs if item.parameter is not None}
        intent["native_parameter_targets"] = {name: aliases.get(name, name) for name in names}
    return {"geometry_signature": geometry_key(project),
            "material_signature": digest(project.material.model_dump() if project.material else None),
            "requirement_signature": digest(requirement.model_dump()),
            "input_signature": digest(intent),
            "study_signature": None if requirement.kind in GEOMETRY_KINDS else study_key(project, selected)}


def _unknown_inputs(brief, link):
    return [item.id for item in brief.inputs
            if (item.id in link.input_refs or item.kind == "assumption")
            and item.value is None and (item.unit is not None or item.provenance is None)]


def _source(store, project, requirement, link):
    if requirement.kind in GEOMETRY_KINDS:
        path = store.cache / "builds" / geometry_key(project) / "checks.json"
        source = "checks"
    else:
        path = store.cache / "studies" / study_key(project, _study_id(requirement, link)) / "result.json"
        source = "study"
    if not path.is_file():
        return source, None
    try:
        data = json.loads(path.read_text())
        digest(data)  # Reject nonfinite evidence before deriving any status.
        return source, data if isinstance(data, dict) else None
    except (ValueError, TypeError, OSError):
        return source, None


def _measurement(project, requirement, link, source, data):
    """Consume the native check/solver finding contract, including its input keys."""
    if data is None:
        return "unknown", "No current native evidence", None
    if source == "checks":
        if data.get("key") != geometry_key(project) or data.get("input_key") != verification_key(project):
            return "stale", "Native checks have changed inputs", None
        if requirement.kind == "max_mass" and project.native_document is not None:
            evidence = data.get("mass_evidence")
            if not isinstance(evidence, dict) or evidence.get("basis") != "canonical-native-body-materials-v1":
                return "unknown", "Rerun checks: canonical mass needs explicit per-body native material evidence", None
            if evidence.get("geometry_key") != geometry_key(project) or evidence.get("native_document_sha256") != digest(project.native_document):
                return "stale", "Canonical material mass evidence belongs to a different native document", None
            if evidence.get("state") != "complete" or evidence.get("mass_g") != data.get("mass_g"):
                return "unknown", "Canonical material mass evidence is incomplete", None
        findings = data.get("findings")
        if not isinstance(findings, list):
            return "unknown", "Native evidence has no finding list", None
        candidates = [f for f in findings if isinstance(f, dict) and f.get("feature") == requirement.id]
        candidates = [f for f in candidates if f.get("code") == requirement.kind]
    else:
        selected = _study_id(requirement, link)
        if (data.get("key") != study_key(project, selected) or data.get("geometry_key") != geometry_key(project)
                or data.get("study_id") != selected):
            return "stale", "Native study has changed inputs", None
        if data.get("converged") is not True:
            return "unknown", "Study is not converged", None
        findings = data.get("findings")
        if not isinstance(findings, list):
            return "unknown", "Native evidence has no finding list", None
        candidates = [f for f in findings if isinstance(f, dict) and f.get("id") == requirement.id]
    if len(candidates) != 1:
        return "unknown", "Missing or ambiguous measured requirement finding", None
    finding = candidates[0]
    value = finding.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "unknown", "Requirement has no finite measured value", None
    if finding.get("limit") != requirement.value:
        return "stale", "Measured finding uses a different limit", None
    if source == "checks" and requirement.kind == "max_mass" and project.native_document is not None and value != data.get("mass_g"):
        return "unknown", "Requirement finding differs from measured canonical material mass", None
    passed = value >= requirement.value if requirement.kind.startswith("min_") else value <= requirement.value
    if source == "checks":
        consistent = finding.get("severity") == ("info" if passed else "error")
    else:
        consistent = finding.get("status") == ("passed" if passed else "failed") and finding.get("passed") is passed
    if not consistent:
        # E.g. an intersecting reference may fail clearance despite a reported distance.
        if source == "checks" and finding.get("severity") == "error":
            return "failed", "Native requirement check failed", value
        return "unknown", "Native finding does not establish a consistent result", value
    if data.get("state") not in ("passed", "failed"):
        return "unknown", "Native evidence is incomplete or inconclusive", value
    if passed and data["state"] != "passed":
        return "unknown", "Native evidence contains failures", value
    return ("passed" if passed else "failed"), "Measured native requirement finding", value


def evaluate(store, brief=None) -> dict:
    """Read-only traceability report for the current saved project and optional draft."""
    project = store.load()
    brief = _brief(project, brief)
    validation = validate_brief(project, brief)
    rows = []
    if brief is None:
        return {"schema_version": 1, "revision": project.revision, "state": "not_configured", "links": [], **validation}
    requirements = {r.id: r for r in project.requirements}
    for link in brief.links:
        row = {"id": link.id, "requirement_id": link.requirement_id, "study_id": link.study_id,
               "status": "unknown", "reason": "", "value": None, "unit": None, "evidence": None}
        # Any broken explanatory reference blocks certification, including global inputs.
        problems = [i for i in validation["issues"] if i["link_id"] in (None, link.id)]
        requirement = requirements.get(link.requirement_id)
        if requirement is not None and requirement.kind not in GEOMETRY_KINDS:
            row["study_id"] = _study_id(requirement, link)
        if problems:
            row["reason"] = "; ".join(i["message"] for i in problems)
        elif not requirement.enabled:
            row["reason"] = "Requirement is disabled"
        elif _unknown_inputs(brief, link):
            row["reason"] = "Linked inputs or global assumptions remain unknown: " + ", ".join(_unknown_inputs(brief, link))
        else:
            row["unit"] = UNITS[requirement.kind]
            signatures = _signatures(project, brief, link, requirement)
            if link.evidence is None:
                row["reason"] = "Evidence is not bound to this brief; bind current measured evidence explicitly"
            elif any(getattr(link.evidence, k) != v for k, v in signatures.items()):
                row.update(status="stale", reason="Bound geometry, material, requirement, study or brief inputs changed")
            else:
                source, data = _source(store, project, requirement, link)
                if source != link.evidence.source or (data is not None and digest(data) != link.evidence.source_sha256):
                    row.update(status="stale", reason="Native evidence identity changed; review and rebind")
                else:
                    status, reason, value = _measurement(project, requirement, link, source, data)
                    row.update(status=status, reason=reason, value=value)
                row["evidence"] = link.evidence.model_dump(mode="json")
        rows.append(row)
    statuses = {r["status"] for r in rows}
    state = next((s for s in ("failed", "stale", "unknown") if s in statuses), "passed" if rows else "unknown")
    return {"schema_version": 1, "revision": project.revision, "state": state, "links": rows, **validation}


evaluate_brief = evaluate


def bind_evidence(store, brief=None) -> EngineeringBrief:
    """Return a brief bound to current measured native results; never writes a project.

    Bindings for unknown, disabled, or invalid links are cleared. Caller persists the
    returned brief through a revision-checked Store Command after human review.
    """
    project = store.load()
    brief = _brief(project, brief)
    if brief is None:
        raise ValueError("This project has no engineering brief")
    validation = validate_brief(project, brief)
    if not validation["valid"]:
        raise ValueError("Repair brief references before binding: " + "; ".join(i["message"] for i in validation["issues"]))
    requirements = {r.id: r for r in project.requirements}
    for link in brief.links:
        link.evidence = None
        requirement = requirements[link.requirement_id]
        if not requirement.enabled or _unknown_inputs(brief, link):
            continue
        source, data = _source(store, project, requirement, link)
        status, _, _ = _measurement(project, requirement, link, source, data)
        if status in ("passed", "failed"):
            link.evidence = BriefEvidence(source=source, source_sha256=digest(data), **_signatures(project, brief, link, requirement))
    return load_brief(brief)
