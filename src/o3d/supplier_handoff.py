"""Sourced supplier preflight and inspectable offline quote-request packages.

Checks describe only supplied capability rules. They never establish physical
manufacturability, provider acceptance, a live offer, or an executed submission.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

from . import handoff
from .model import geometry_key
from .identity import authorized
from .store import Conflict, Store, write_json
from .supplier_models import SupplierProfile, SupplierRequest, SupplierResponse, SuppliedSource

MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_FILES = 512
DISCLAIMER = 'Supplied rule checks only; manufacturability, physical fit, strength and supplier acceptance remain unverified.'
# These are source-field semantics, not invented provider limits. No unit conversions.
NUMERIC = {
    'max_x': ('x', '<='), 'max_y': ('y', '<='), 'max_z': ('z', '<='),
    'min_quantity': ('quantity', '>='), 'max_quantity': ('quantity', '<='),
    'minimum_tolerance': ('tolerance', '>='), 'min_wall': ('minimum_wall', '>='),
}
CORE_RULES = ('max_x', 'max_y', 'max_z', 'min_quantity', 'max_quantity')
CAD_FORMATS = {'step', 'stp', 'stl', '3mf', 'dxf', 'iges', 'igs'}


def _jsonable(model):
    return model.model_dump(mode='json')


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _positive_number(value):
    try:
        return type(value) in (int, float) and value > 0 and math.isfinite(value)
    except OverflowError:
        return False


def _time(now):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Evaluation time requires a timezone')
    return now


def _relative(value: str) -> PurePosixPath:
    # Both Windows and POSIX traversal syntax are rejected, also on Linux.
    path = PurePosixPath(value)
    if not value or '\\' in value or ':' in value or path.is_absolute() or any(p in {'', '.', '..'} for p in value.split('/')):
        raise ValueError('Expected a bounded relative artifact path')
    return path


def _safe(root: Path, value: str, *, file=True) -> Path:
    relative = _relative(value)
    if root.is_symlink():
        raise ValueError('Symlinks are not accepted in supplier paths')
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Symlinks are not accepted in supplier paths')
    if not current.resolve().is_relative_to(root.resolve()):
        raise ValueError('Supplier path escapes its bounded root')
    if file and (not current.is_file() or current.stat().st_size > MAX_FILE):
        raise ValueError('Missing or oversized supplier artifact')
    return current


def _read(path):
    if path.stat().st_size > MAX_FILE:
        raise ValueError('Oversized supplier JSON')
    return json.loads(path.read_text())


def capture_source(store: Store, content: str | bytes) -> dict:
    """Retain bounded user-supplied bytes, with no fetching or vendor verification."""
    if not isinstance(content, (str, bytes)):
        raise ValueError('Supplier evidence must be text or bytes')
    raw = content.encode('utf-8') if isinstance(content, str) else content
    if not raw or len(raw) > 2 * 1024 * 1024:
        raise ValueError('Supplied supplier evidence must contain 1 byte to 2 MiB')
    sha = hashlib.sha256(raw).hexdigest()
    with store.lock(), authorized(store, 'edit'):
        path = _safe(store.cache, 'supplier-sources/' + sha + '.bin', file=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if not path.is_file() or handoff.digest(path) != sha:
                raise ValueError('Existing immutable supplier source changed')
        else:
            # Exclusive creation preserves previously attached bytes.
            with path.open('xb') as output:
                output.write(raw)
        return {'capture': str(path.relative_to(store.cache)), 'sha256': sha,
                'size_bytes': len(raw), 'kind': 'supplied_capture',
                'provenance': 'user_supplied_bytes', 'vendor_verified': False}


def _source(store, source: SuppliedSource, now):
    relative = _relative(source.capture)
    if len(relative.parts) != 2 or relative.parts[0] != 'supplier-sources':
        raise ValueError('Supplier source must be a bounded supplier-sources/<file> capture')
    path = _safe(store.cache, source.capture)
    if handoff.digest(path) != source.sha256:
        raise ValueError('Retained supplier source hash changed')
    if source.captured_at > now:
        return 'unknown', 'Source capture timestamp is in the future'
    if source.expires_at <= now:
        return 'unknown', 'Source evidence expired'
    if source.kind == 'synthetic_fixture':
        return 'unknown', 'Synthetic fixture is not supplier capability evidence'
    return 'pass', 'Supplied retained source bytes verified; extraction and supplier certification unverified'


def _handoff(store, request):
    relative = _relative(request.handoff_manifest)
    if len(relative.parts) != 3 or relative.parts[0] != 'handoffs' or relative.name != 'manifest.json':
        raise ValueError('Select a generated handoffs/<id>/manifest.json inside Store.cache')
    manifest_path = _safe(store.cache, request.handoff_manifest)
    data = _read(manifest_path)
    files = data.get('files')
    if data.get('schema_version') != 1 or not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        raise ValueError('Invalid or oversized handoff manifest')
    total = 0
    for relative, expected in files.items():
        path = _safe(manifest_path.parent, relative)
        total += path.stat().st_size
        if total > MAX_TOTAL:
            raise ValueError('Handoff exceeds supplier package size limit')
        if not isinstance(expected, str) or handoff.digest(path) != expected:
            raise ValueError('Handoff artifact hash changed: ' + relative)
    # Validate these before the existing handoff validator follows them.
    _safe(manifest_path.parent, 'receipt.json')
    _safe(manifest_path.parent, 'handoff.zip')
    verified = handoff.status(store, request.handoff_manifest)
    if verified['state'] != 'current':
        raise ValueError('Stale handoff: ' + ', '.join(verified['reasons']))
    project = store.load()
    if data.get('revision') != project.revision or data.get('geometry_key') != geometry_key(project):
        raise ValueError('Handoff geometry/revision differs from current project')
    if 'native/report.json' not in files:
        raise ValueError('Current canonical handoff engineering report is required; regenerate the handoff')
    scope = handoff.single_solid_scope(_read(_safe(manifest_path.parent, 'native/report.json')))
    if data.get('geometry_scope') != scope:
        raise ValueError('Handoff geometry scope differs from its canonical report; regenerate the handoff')
    selected = {}
    for name in request.artifacts:
        _relative(name)
        if name not in files:
            raise ValueError('Selected artifact is not in verified handoff: ' + name)
        if name not in scope['selectable_artifacts'] or PurePosixPath(name).suffix.lstrip('.').casefold() not in CAD_FORMATS:
            raise ValueError('Select an actual CAD/export artifact: ' + name)
        selected[name] = files[name]
    return manifest_path, data, selected


def _checked_project(project, expected_revision=None, expected_signature=None):
    if expected_revision is not None and project.revision != expected_revision:
        raise Conflict(f'Revision conflict: expected {expected_revision}, current {project.revision}. Re-inspect before creating supplier evidence.')
    if expected_signature is not None and handoff.document_signature(project) != expected_signature:
        raise Conflict('Project document changed during supplier operation')
    return project


def _snapshot(store, expected_revision=None, expected_signature=None, *, action=None):
    if action is not None:
        with store.lock(), authorized(store, action):
            return _checked_project(store._load(), expected_revision, expected_signature)
    return _checked_project(store.load(), expected_revision, expected_signature)


def _evaluate(store, profile, request, now, expected_revision=None):
    project = _snapshot(store, expected_revision)
    manifest_path, manifest, selected = _handoff(store, request)
    if manifest.get('project_sha256') != handoff.document_signature(project) or manifest.get('revision') != project.revision:
        raise ValueError('Project changed during supplier preflight')
    state = store.state()
    if state['geometry_state'] != 'current':
        raise ValueError('Build current geometry before supplier preflight')
    checks = []
    def check(key, state, reason, **details):
        checks.append(dict(key=key, state=state, reason=reason, **details))
    source_state, source_reason = _source(store, profile.source, now)
    check('source', source_state, source_reason, source_url=profile.source.url, source_sha256=profile.source.sha256)
    check('provider', 'pass' if request.provider == profile.provider else 'fail', 'Exact selected provider identity', requested=request.provider, sourced=profile.provider)
    for key in ('process', 'material'):
        actual, wanted = getattr(profile.record, key), getattr(request, key)
        check(key, 'unknown' if actual is None else 'pass' if actual.casefold() == wanted.casefold() else 'fail',
              'Exact supplied record label; no synonym or process/material join inferred', requested=wanted, sourced=actual,
              source_field=key)
    formats = [PurePosixPath(name).suffix.lstrip('.').casefold() for name in selected]
    supported = profile.record.facts.get('supported_formats')
    known_formats = isinstance(supported, list) and bool(supported) and all(isinstance(f, str) and f for f in supported)
    check('formats', 'unknown' if not known_formats else 'pass' if all(f in {s.casefold() for s in supported} for f in formats) else 'fail',
          'Compare actual selected export suffixes to explicit supported_formats; no aliases inferred',
          requested=formats, sourced=supported, source_field='facts.supported_formats')
    # Use the immutable export's own measured report, not mutable build-cache
    # claims or caller-supplied dimensions. Find by schema fields, not CAD filename.
    dimension_reports = []
    for relative in manifest['files']:
        if PurePosixPath(relative).name.endswith('report.json'):
            report = _read(_safe(manifest_path.parent, relative))
            if isinstance(report, dict) and report.get('revision') == project.revision and report.get('geometry_key') == geometry_key(project) and 'dimensions_mm' in report:
                dimension_reports.append((relative, report['dimensions_mm']))
    dims = dimension_reports[0][1] if dimension_reports and all(value == dimension_reports[0][1] for _, value in dimension_reports) else None
    inputs = {'quantity': (request.quantity, 'count', 'supplied request')}
    if isinstance(dims, list) and len(dims) == 3 and all(_positive_number(v) for v in dims):
        inputs.update({axis: (value, 'mm', 'hash-verified handoff geometry report') for axis, value in zip('xyz', dims)})
    for key in ('tolerance', 'minimum_wall'):
        value = getattr(request, key)
        if value is not None:
            inputs[key] = (value.value, value.unit, 'supplied request; not independently measured')
    constraints = profile.record.constraints
    for key in dict.fromkeys([*CORE_RULES, *(c.key for c in constraints)]):
        rows = [c for c in constraints if c.key == key]
        if key not in NUMERIC:
            check(key, 'unknown', 'Constraint semantics unsupported; supplier review required')
            continue
        input_key, operator = NUMERIC[key]
        if not rows:
            check(key, 'unknown', 'Required explicit source rule is missing')
            continue
        actual = inputs.get(input_key)
        for row in rows:
            details = dict(source_field=row.source_field, sourced=row.value, unit=row.unit, operator=operator)
            if not row.source_field or not row.source_field.strip() or not _positive_number(row.value) or not row.unit or not row.unit.strip():
                check(key, 'unknown', 'Rule lacks a positive finite numeric value, exact unit or source_field', **details)
            elif actual is None:
                check(key, 'unknown', 'Required exact-unit input is missing', **details)
            elif actual[1] != row.unit:
                check(key, 'fail', 'Incompatible exact units; no conversion is inferred', requested=actual[0], requested_unit=actual[1], **details)
            else:
                passed = actual[0] <= row.value if operator == '<=' else actual[0] >= row.value
                check(key, 'pass' if passed else 'fail', 'Explicit numeric source comparison', requested=actual[0], input_provenance=actual[2], **details)
    if profile.record.facts.get('alias_only'):
        check('alias_only', 'unknown', 'Alias evidence does not describe a manufacturing capability')
    # A caller-supplied target tolerance/wall cannot be silently ignored.
    for input_key, rule in (('tolerance', 'minimum_tolerance'), ('minimum_wall', 'min_wall')):
        if getattr(request, input_key) is not None and not any(c.key == rule for c in constraints):
            check(rule, 'unknown', 'Requested measurement has no sourced comparison rule')
    outcome = 'fail' if any(c['state'] == 'fail' for c in checks) else 'unknown' if any(c['state'] == 'unknown' for c in checks) else 'pass'
    binding = {'revision': project.revision, 'project_sha256': handoff.document_signature(project),
               'geometry_key': geometry_key(project), 'request_id': request.request_id,
               'request_sha256': _hash(_jsonable(request)), 'profile_sha256': _hash(_jsonable(profile)),
               'handoff_manifest': request.handoff_manifest, 'handoff_manifest_sha256': handoff.digest(manifest_path),
               'artifacts': selected, 'source_sha256': profile.source.sha256,
               'measurement_evidence': {name: manifest['files'][name] for name, _ in dimension_reports}}
    checked_path, _, checked_selected = _handoff(store, request)
    _snapshot(store, project.revision, binding['project_sha256'])
    if handoff.digest(checked_path) != binding['handoff_manifest_sha256'] or checked_selected != selected:
        raise ValueError('Project or artifacts changed during supplier preflight')
    _source(store, profile.source, now)
    return {'state': 'request_ready' if outcome == 'pass' else 'manual_handoff', 'preflight_state': outcome,
            'checks': checks, 'reasons': [c['reason'] for c in checks if c['state'] != 'pass'],
            'binding': binding, 'binding_sha256': _hash(binding), 'evaluated_at': now.isoformat(),
            'manufacturability': 'unverified', 'supplier_acceptance': 'unknown', 'submission': 'not_sent',
            'geometry_scope': manifest['geometry_scope'],
            'warnings': [DISCLAIMER], 'inputs': {'profile': _jsonable(profile), 'request': _jsonable(request)}, 'selected': {k: getattr(request, k) for k in ('provider', 'process', 'material', 'quantity')}}


def preflight(store: Store, profile: SupplierProfile | dict, request: SupplierRequest | dict, *, now: datetime | None = None) -> dict:
    result = _evaluate(store, SupplierProfile.model_validate(profile), SupplierRequest.model_validate(request), _time(now))
    result['inputs'] = {'profile': profile if isinstance(profile, dict) else _jsonable(profile),
                        'request': request if isinstance(request, dict) else _jsonable(request)}
    return result


def create_request(store: Store, profile: SupplierProfile | dict, request: SupplierRequest | dict, *, now: datetime | None = None, expected_revision: int | None = None) -> dict:
    """Create a local package, including unresolved findings. Nothing is submitted."""
    supplied_inputs = {'profile': profile if isinstance(profile, dict) else _jsonable(profile),
                       'request': request if isinstance(request, dict) else _jsonable(request)}
    profile, request, now = SupplierProfile.model_validate(profile), SupplierRequest.model_validate(request), _time(now)
    project = _snapshot(store, expected_revision, action='export')
    signature = handoff.document_signature(project)
    result = _evaluate(store, profile, request, now, expected_revision=project.revision)
    result['inputs'] = supplied_inputs
    parent = _safe(store.cache, 'supplier-requests', file=False)
    parent.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='.pending-', dir=parent))
    try:
        handoff_path, data, _ = _handoff(store, request)
        # Copy only paths listed and hash-verified by the immutable handoff.
        for relative in data['files']:
            source = _safe(handoff_path.parent, relative)
            destination = folder / 'handoff' / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if handoff.digest(destination) != data['files'][relative]:
                raise ValueError('Copied handoff artifact hash changed: ' + relative)
        shutil.copyfile(handoff_path, folder / 'handoff' / 'manifest.json')
        source = _safe(store.cache, profile.source.capture)
        shutil.copyfile(source, folder / 'source-capture.bin')
        if handoff.digest(folder / 'source-capture.bin') != profile.source.sha256:
            raise ValueError('Copied supplier source hash changed')
        write_json(folder / 'profile.json', _jsonable(profile))
        write_json(folder / 'request.json', _jsonable(request))
        write_json(folder / 'preflight.json', result)
        if _evaluate(store, profile, request, now, expected_revision=project.revision)['binding_sha256'] != result['binding_sha256']:
            raise ValueError('Project or supplier inputs changed during packaging')
        files = {str(p.relative_to(folder)): handoff.digest(p) for p in sorted(folder.rglob('*')) if p.is_file()}
        # Explicit request/quote state belongs to this package, not catalog metadata.
        manifest = {'schema_version': 1, 'kind': 'supplier_request', 'binding': result['binding'],
                    'binding_sha256': result['binding_sha256'], 'created_at': now.isoformat(),
                    'state': result['state'], 'preflight_state': result['preflight_state'], 'files': files,
                    'submission': 'not_sent', 'warnings': [DISCLAIMER]}
        write_json(folder / 'manifest.json', manifest)
        with zipfile.ZipFile(folder / 'request.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
            for relative in [*files, 'manifest.json']:
                archive.write(_safe(folder, relative), relative)
        write_json(folder / 'receipt.json', {'manifest_sha256': handoff.digest(folder / 'manifest.json'),
                                           'archive_sha256': handoff.digest(folder / 'request.zip')})
        relative = str((folder / 'manifest.json').relative_to(store.cache))
        final = status(store, relative, profile, request, now=now)
        if final['package_state'] != 'current':
            raise ValueError('Supplier inputs changed before package completion')
        # Publish the complete immutable directory only after current role and
        # document checks, without calling locking helpers inside this lock.
        with store.lock(), authorized(store, 'export'):
            _checked_project(store._load(), project.revision, signature)
            published = parent / ('request-' + folder.name.removeprefix('.pending-'))
            if published.exists() or published.is_symlink():
                raise ValueError('Supplier package destination already exists')
            folder.rename(published)
            folder = published
        final.update(manifest=str((folder / 'manifest.json').relative_to(store.cache)),
                     artifact=str((folder / 'request.zip').relative_to(store.cache)))
        return final
    except Exception:
        shutil.rmtree(folder)
        raise


def _package(store, manifest):
    relative = _relative(manifest)
    if len(relative.parts) != 3 or relative.parts[0] != 'supplier-requests' or relative.name != 'manifest.json':
        raise ValueError('Expected supplier-requests/<id>/manifest.json inside Store.cache')
    path = _safe(store.cache, manifest)
    data = _read(path)
    if data.get('schema_version') != 1 or data.get('kind') != 'supplier_request':
        raise ValueError('Unsupported supplier request manifest')
    receipt = _read(_safe(path.parent, 'receipt.json'))
    archive = _safe(path.parent, 'request.zip')
    if receipt.get('manifest_sha256') != handoff.digest(path) or receipt.get('archive_sha256') != handoff.digest(archive):
        raise ValueError('Supplier package manifest/archive changed')
    files = data.get('files')
    if not isinstance(files, dict) or len(files) > MAX_FILES + 10:
        raise ValueError('Invalid supplier package files')
    total = 0
    for relative, expected in files.items():
        item = _safe(path.parent, relative)
        total += item.stat().st_size
        if total > MAX_TOTAL + MAX_FILE:
            raise ValueError('Supplier package exceeds size limit')
        if handoff.digest(item) != expected:
            raise ValueError('Supplier package artifact changed: ' + relative)
    for required in ('profile.json', 'request.json', 'preflight.json', 'source-capture.bin', 'handoff/manifest.json'):
        if required not in files:
            raise ValueError('Supplier package lacks required evidence: ' + required)
    if _hash(data['binding']) != data['binding_sha256']:
        raise ValueError('Supplier package binding changed')
    return path, data


def status(store: Store, manifest: str, profile: SupplierProfile | dict | None = None,
           request: SupplierRequest | dict | None = None, *, now: datetime | None = None) -> dict:
    now = _time(now)
    path, data = _package(store, manifest)
    saved_profile = SupplierProfile.model_validate(_read(_safe(path.parent, 'profile.json')))
    saved_request = SupplierRequest.model_validate(_read(_safe(path.parent, 'request.json')))
    profile = SupplierProfile.model_validate(profile) if profile is not None else saved_profile
    request = SupplierRequest.model_validate(request) if request is not None else saved_request
    reasons = []
    try:
        current = _evaluate(store, profile, request, now)
        if current['binding_sha256'] != data['binding_sha256']:
            reasons.append('Request, profile, revision or selected artifact binding changed')
    except ValueError as error:
        current = None
        reasons.append(str(error))
    result = _read(_safe(path.parent, 'preflight.json'))
    # Report saved identity and the live validity separately; never reuse old readiness.
    result.update(manifest=manifest, artifact=str((path.parent / 'request.zip').relative_to(store.cache)),
                  package_state='stale' if reasons else 'current', reasons=reasons, quote_state='unknown', quote=None)
    result['state'] = 'stale' if reasons else data['state']
    if current is not None and not reasons:
        result.update(preflight_state=current['preflight_state'], checks=current['checks'], reasons=current['reasons'], state=current['state'])
    expired = profile.source.expires_at <= now
    if expired and not reasons:
        result.update(state='expired', quote_state='expired')
    # Events are immutable siblings, so quote updates do not mutate packaged bytes.
    event_dir = _safe(store.cache, 'supplier-events', file=False)
    candidates = []
    if event_dir.is_dir():
        for event_path in event_dir.glob(data['binding_sha256'] + '-*.json'):
            event_path = _safe(event_dir, event_path.name)
            event = _read(event_path)
            recorded = datetime.fromisoformat(event['recorded_at'])
            if recorded.tzinfo is None or recorded > now:
                continue
            if event_path.name != data['binding_sha256'] + '-' + _hash(event) + '.json':
                raise ValueError('Immutable supplier response event changed')
            if event.get('manifest_sha256') != handoff.digest(path):
                continue
            response = SupplierResponse.model_validate(event['response'])
            if response.binding_sha256 != data['binding_sha256'] or response.request_id != saved_request.request_id:
                continue
            candidates.append((event['recorded_at'], event_path.name, response))
    if candidates:
        response = sorted(candidates, key=lambda item: (item[0], item[1]))[-1][2]
        evidence_state, reason = _source(store, response.source, now)
        result['quote'] = _jsonable(response)
        result['quote_evidence_state'] = evidence_state
        if not reasons and not expired:
            if response.source.expires_at <= now:
                result.update(state='expired', quote_state='expired')
            elif evidence_state == 'pass':
                result.update(state=response.state, quote_state=response.state, quote=_jsonable(response))
            else:
                result.update(state='unknown', quote_state='unknown')
                result['reasons'].append(reason)
    return result


def record_response(store: Store, manifest: str, response: SupplierResponse | dict, *, now: datetime | None = None, expected_revision: int | None = None) -> dict:
    """Record supplied, hash-bound correspondence; never send it or assert acceptance."""
    now, response = _time(now), SupplierResponse.model_validate(response)
    project = _snapshot(store, expected_revision, action='edit')
    signature = handoff.document_signature(project)
    path, data = _package(store, manifest)
    current = status(store, manifest, now=now)
    _snapshot(store, project.revision, signature, action='edit')
    if current['package_state'] != 'current' or current['state'] == 'expired':
        raise ValueError('Cannot attach response to stale or expired supplier request')
    if response.binding_sha256 != data['binding_sha256'] or response.request_id != data['binding']['request_id']:
        raise ValueError('Response belongs to a different request binding')
    evidence, reason = _source(store, response.source, now)
    if evidence != 'pass':
        raise ValueError('Response evidence must be current: ' + reason)
    _snapshot(store, project.revision, signature, action='edit')
    parent = _safe(store.cache, 'supplier-events', file=False)
    parent.mkdir(parents=True, exist_ok=True)
    value = {'manifest_sha256': handoff.digest(path), 'recorded_at': now.isoformat(), 'response': _jsonable(response)}
    name = data['binding_sha256'] + '-' + _hash(value) + '.json'
    event_path = parent / name
    created = not event_path.exists()
    try:
        if not created and _read(_safe(parent, name)) != value:
            raise ValueError('Existing immutable supplier response changed')
        if created:
            with store.lock(), authorized(store, 'edit'):
                _checked_project(store._load(), project.revision, signature)
                write_json(event_path, value)
        final = status(store, manifest, now=now)
        _snapshot(store, project.revision, signature, action='edit')
        if final['package_state'] != 'current':
            raise ValueError('Supplier inputs changed before response completion')
        return final
    except Exception:
        if created:
            event_path.unlink(missing_ok=True)
        raise
