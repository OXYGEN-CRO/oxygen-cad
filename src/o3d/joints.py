"""Reproducible static joint checks. No generated code, inferred physics or purchases."""
from __future__ import annotations
import json
from pathlib import Path
from .joint_models import ActuatorContract, JointRequirement
from .model import Command, Project, digest, geometry_key
from .store import Conflict, write_json, now

VERSION = 'static-joint-1'

def example():
    from .brief_models import EngineeringBrief
    value = json.loads(Path(__file__).with_name('data').joinpath('joint-example.json').read_text())
    return EngineeringBrief.model_validate(value).model_dump(mode='json', include={'joint_requirements', 'components'})

def library():
    return [ActuatorContract.model_validate(v) for v in json.loads(Path(__file__).with_name('data').joinpath('actuators.json').read_text())]

def signature(project):
    brief = project.engineering_brief
    refs = {name: project.parameters.get(name).model_dump() if name in project.parameters else None
            for task in (brief.joint_requirements if brief else []) for name in [*task.parameter_refs, *task.bindings.values()]}
    return digest({'version': VERSION, 'geometry': geometry_key(project), 'parameters': refs,
                   'requirements': [r.model_dump(mode='json') for r in brief.joint_requirements] if brief else [],
                   'components': [c.model_dump(mode='json') for c in brief.components] if brief else []})

def _check(code, status, reason, **values):
    return dict(code=code, status=status, reason=reason, **values)

def _aggregate(checks):
    return 'failed' if any(c['status'] == 'failed' for c in checks) else 'unknown' if not checks or any(c['status'] != 'passed' for c in checks) else 'passed'

def _demand(task, project=None):
    task = task.model_copy(deep=True)
    for field, name in task.bindings.items():
        parameter = project.parameters.get(name) if project else None
        if parameter is None:
            return None, _check('static_load', 'unknown', f'Bound parameter {name} no longer exists')
        from .joint_models import Quantity
        try:
            setattr(task, field, Quantity(value=parameter.value, unit=parameter.unit))
            getattr(task, field).si('mass' if field.endswith('mass') else 'length')
        except ValueError:
            return None, _check('static_load', 'unknown', f'Bound parameter {name} has incompatible units')
    fields = ['payload_mass', 'payload_arm', 'link_mass', 'link_com']
    missing = [f for f in fields if getattr(task, f) is None]
    if not task.static_model_confirmed: missing.append('static_model_confirmed')
    if not task.assumptions: missing.append('assumptions')
    if missing: return None, _check('static_load', 'unknown', 'Specify ' + ', '.join(missing))
    torque = 9.81 * (task.payload_mass.si('mass') * task.payload_arm.si('length') + task.link_mass.si('mass') * task.link_com.si('length'))
    return torque, _check('static_load', 'passed', 'Explicit horizontal planar payload + single-link gravity model; no dynamics or other loads', value=torque, unit='N*m', evidence_kind='calculation')

def compare(task: JointRequirement, component: ActuatorContract, project: Project):
    torque, load = _demand(task, project)
    checks = [load]
    if task.subject and task.subject not in {f.id for f in project.features} | {r.id for r in project.references}:
        checks.append(_check('subject', 'unknown', f'Subject {task.subject} no longer exists'))
    for ref in task.parameter_refs:
        if ref not in project.parameters: checks.append(_check('parameter', 'unknown', f'Parameter {ref} no longer exists'))
    if not component.interface_source_id:
        checks.append(_check('interface_source', 'unknown', 'Component interface claims have no source binding'))
    if not all([task.supply_min, task.supply_max, component.supply_min, component.supply_max]):
        checks.append(_check('supply', 'unknown', 'Supply operating range and component voltage limits are required'))
    else:
        lo, hi = task.supply_min.si('voltage'), task.supply_max.si('voltage')
        ok = component.supply_min.si('voltage') <= lo <= hi <= component.supply_max.si('voltage')
        checks.append(_check('supply', 'passed' if ok else 'failed', 'Full requested supply range must fit component limits'))
    for field in ['bus', 'protocol', 'connector']:
        required, actual = getattr(task, field), getattr(component, field)
        checks.append(_check(field, 'unknown' if not required or not actual else 'passed' if required == actual else 'failed',
                             f'{field}: requested {required or "unknown"}; component {actual or "unknown"}'))
    if not task.pinout or not component.pinout:
        checks.append(_check('pinout', 'unknown', 'Bind controller/harness and component pin assignments'))
    else:
        checks.append(_check('pinout', 'passed' if task.pinout == component.pinout else 'failed', 'Exact declared signal/power pin mappings compared'))
    if not task.mount or not component.mount:
        checks.append(_check('mount', 'unknown', 'Sourced aligned rectangular mounting patterns are required; no fit inferred from family CAD'))
    else:
        a, b = task.mount, component.mount
        match = a.thread == b.thread and abs(a.pattern_x.si('length') - b.pattern_x.si('length')) <= 1e-12 and abs(a.pattern_y.si('length') - b.pattern_y.si('length')) <= 1e-12
        # Nominal pattern mismatch can reject a direct mating; matching patterns still need tolerance/engagement checks.
        checks.append(_check('mount', 'unknown' if match else 'failed', 'Nominal grid matches; tolerance, fastener engagement and clearance need verification' if match else 'Declared aligned mount pattern/thread differs'))
    if torque is None or not task.supply_min or not task.supply_max:
        checks.append(_check('stall_screen', 'unknown', 'Load and operating voltage are unresolved'))
    else:
        points = [r for r in component.ratings if r.kind == 'stall' and abs(r.voltage.si('voltage') - task.supply_min.si('voltage')) < 1e-9 and task.supply_min == task.supply_max]
        if not points:
            checks.append(_check('stall_screen', 'unknown', 'No stall rating at this exact fixed voltage; no extrapolation'))
        else:
            # Conflicting applicable claims cannot be resolved by choosing the strongest number.
            limits = {r.torque.si('torque') for r in points}
            checks.append(_check('stall_screen', 'unknown' if len(limits) != 1 else 'passed' if torque <= min(limits) else 'failed',
                                 'Nominal stall screen only; does not establish sustained capability' if len(limits) == 1 else 'Conflicting stall ratings at this voltage',
                                 required_nm=torque, limits_nm=sorted(limits), source_ids=[r.source_id for r in points]))
    needed = ['hold_duration', 'ambient', 'cooling', 'duty_cycle', 'supply_min', 'supply_max']
    missing = [f for f in needed if getattr(task, f) is None]
    applicable = []
    if torque is not None and not missing:
        for r in component.ratings:
            if r.kind not in ('continuous', 'hold_test') or r.ambient_min is None or r.ambient_max is None or not r.cooling or r.duty_cycle_max is None: continue
            if task.supply_min != task.supply_max or abs(r.voltage.si('voltage') - task.supply_min.si('voltage')) > 1e-9: continue
            if not r.ambient_min.si('temperature') <= task.ambient.si('temperature') <= r.ambient_max.si('temperature') or r.cooling != task.cooling or r.duty_cycle_max < task.duty_cycle: continue
            if r.kind == 'hold_test' and (r.duration is None or r.duration.si('time') < task.hold_duration.si('time')): continue
            applicable.append(r)
    if not applicable:
        checks.append(_check('sustained', 'unknown', 'Missing task conditions: ' + ', '.join(missing) if missing else 'No applicable sustained/hold-test evidence at the specified voltage, ambient, cooling, duration and duty'))
    else:
        limits = {r.torque.si('torque') for r in applicable}
        checks.append(_check('sustained', 'unknown' if len(limits) != 1 else 'passed' if torque <= min(limits) else 'failed',
                             'Applicable declared sustained rating compared; no physical qualification implied' if len(limits) == 1 else 'Conflicting applicable sustained ratings',
                             required_nm=torque, limits_nm=sorted(limits), source_ids=[r.source_id for r in applicable]))
    return {'component_id': component.id, 'manufacturer': component.manufacturer, 'mpn': component.mpn, 'revision': component.revision,
            'status': _aggregate(checks), 'checks': checks, 'sources': [s.model_dump(mode='json') for s in component.sources],
            'next_actions': [c['reason'] for c in checks if c['status'] != 'passed']}

def evaluate_project(project):
    brief = project.engineering_brief
    rows = []
    for task in brief.joint_requirements if brief else []:
        candidates = [compare(task, component, project) for component in brief.components]
        candidates.sort(key=lambda c: ({'passed': 0, 'unknown': 1, 'failed': 2}[c['status']], c['mpn'], c['component_id']))
        selected = next((c for c in candidates if c['component_id'] == task.selected_component_id), None)
        rows.append({'requirement_id': task.id, 'authoring_state': task.state, 'statement': task.statement,
                     'demand': _demand(task, project)[1], 'candidates': candidates, 'selected_component_id': task.selected_component_id,
                     'status': selected['status'] if selected else 'unknown',
                     'reason': 'Selected candidate evaluated against declared checks' if selected else 'Select an exact candidate; missing selections cannot pass'})
    return {'version': VERSION, 'input_key': signature(project), 'revision': project.revision, 'requirements': rows,
            'state': _aggregate([{'status': r['status']} for r in rows]),
            'limits': ['Static horizontal single-link model only; no dynamic sizing or trained controller validation.',
                       'Mounting patterns are nominal screens; physical fit, fastener engagement and tolerances remain unresolved.',
                       'Source-backed vendor claims are not measured hardware qualification. No stock or purchasing guarantee.']}

def evaluate(store, expected_revision=None):
    with store.lock():
        project = store._load()
        if expected_revision is not None and expected_revision != project.revision: raise Conflict('Project changed before joint evaluation')
        report = evaluate_project(project)
        report['created_at'] = now()
        record = {'report': report, 'report_sha256': digest(report), 'snapshot': project.model_dump(mode='json')}
        key = report['input_key']
        path = store.cache / 'joints' / f'{key}.json'
        if path.exists() and _read_record(path, key) is None:
            raise ValueError('Cached joint evidence failed integrity checks; preserve and inspect the record')
        if not path.exists(): write_json(path, record)
        write_json(store.cache / 'last-joints.json', {'input_key': key})
        return status(store, project)

def _read_record(path, key):
    try:
        value = json.loads(path.read_text())
        if (digest(value['report']) != value['report_sha256'] or value['report']['input_key'] != key
                or signature(Project.model_validate(value['snapshot'])) != key):
            return None
        return value['report']
    except (ValueError, TypeError, KeyError, OSError):
        return None

def status(store, project=None):
    project = project or store.load()
    key = signature(project)
    current = store.cache / 'joints' / f'{key}.json'
    last = store.cache / 'last-joints.json'
    if current.is_file():
        report = _read_record(current, key)
        return {'freshness': 'current' if report else 'invalid', 'input_key': key, 'report': report}
    previous = None
    if last.is_file():
        previous_key = json.loads(last.read_text())['input_key']
        previous_path = store.cache / 'joints' / f'{previous_key}.json'
        if previous_path.is_file(): previous = _read_record(previous_path, previous_key)
    return {'freshness': 'stale' if previous else 'not_run', 'input_key': key, 'report': None, 'previous_report': previous}

def configure(store, data, expected_revision, actor='agent'):
    from .brief_models import EngineeringBrief
    if set(data) - {'joint_requirements', 'components'}: raise ValueError('Configuration accepts only joint_requirements and components')
    project = store.load()
    brief = project.engineering_brief or EngineeringBrief()
    value = brief.model_dump(mode='json') | data
    project.engineering_brief = EngineeringBrief.model_validate(value)
    return store.apply(Command(expected_revision=expected_revision, actor=actor, project=project, summary='Edit joint requirements and component contracts'))

def contract_from_catalog(row):
    """Typed view of existing catalog attributes, preserving exact part/MPN identity."""
    contract = ActuatorContract.model_validate(row.get('attributes', {}).get('engineering_contract'))
    if row.get('mpn') != contract.mpn or row.get('manufacturer') != contract.manufacturer:
        raise ValueError('Catalog identity does not match engineering contract')
    return contract.model_copy(update={'catalog_part_id': str(row['id'])})
