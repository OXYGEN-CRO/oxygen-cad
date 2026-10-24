"""Pure, offline import planning for immutable supplier research captures.

Nothing is fetched, executed, or written. Unknown reuse rights remain unknown;
worker restrictions are import gates, not legal conclusions made by this module.
A verified capture establishes provenance, never physical fit or live supply.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .offer_policy import strip_offer_fields

# Conservative first-party domains and aliases. Shared hosting is deliberately
# absent: a github.com or cloud-storage URL alone cannot establish ownership.
_DOMAIN_ROWS = """
3erp 3erp.com threeerp 3erp_injection
advancedpcb advancedpcb.com advancedcircuits
amphenol amphenol-cs.com
ark arkelectron.com
arrk arrk.com arrk_prototyping
arduino arduino.cc
cuav cuav.net
cubepilot cubepilot.org
cui cui.com belfuse.com
feetech feetechrc.com
foxtech foxtech.com foxtechfpv.com
grepow grepow.com genstattu.com tattu
harmonic-drive harmonicdrive.net harmonic_drive
hebi hebi.us hebirobotics.com shophebi.com
hiwin hiwin.com hiwin.us
hubs hubs.com protolabsnetwork.com protolabs_network
igus igus.com igus.ca igus-cad.com igus_motion
materialise materialise.com imaterialise
item item24.com
jlcpcb jlcpcb.com jlc_components
jlccnc jlccnc.com
jlc3dp jlc3dp.com
jst jst.com jst-mfg.com
lin-engineering linengineering.com lin_engineering
matek mateksys.com
maxon maxongroup.com maxongroup.us
mcmaster mcmaster.com mcmaster_plastics_access mcmaster_stock_access
mean-well meanwell.com mean_well
misumi misumi-ec.com
myactuator myactuator.com myactuator.us
norelem norelemusa.com
odrive odriverobotics.com
online-metals onlinemetals.com online_metals
openbuilds openbuilds.com openbuildspartstore.com
oriental-motor orientalmotor.com oriental_motor
oshpark oshpark.com
ouster ouster.com ouster.io
partsbadger parts-badger.com
phoenix-contact phoenixcontact.com
pihut thepihut.com
rbtx rbtx.com
realsense realsenseai.com
recom recom-power.com
rev-robotics revrobotics.com rev_robotics
robotis robotis.com robotis.us
rochling roechling.com
rock-west rockwestcomposites.com rock_west
sdpsi sdp-si.com
seeed seeedstudio.com seeed.cc
slant3d slant3d.com slant3dapi.com
steadywin steadywin-motor.com steadywin.cn
stepperonline omc-stepperonline.com
tap-plastics tapplastics.com tap_plastics
tailorsteel 247tailorsteel.com
te-connectivity te.com
tme tme.eu
thomson thomsonlinear.com
thyssenkrupp thyssenkrupp-materials-na.com thyssenkrupp-materials-services.com thyssenkrupp-materials.co.uk
traco tracopower.com
ublox u-blox.com
vex vexrobotics.com
vicor vicorpower.com
wayken waykenrm.com wayken_vacuum_casting
wurth-elektronik we-online.com
xometry xometry.com xometry.eu xometry.pro xometry-eu xometry_injection
easy-composites easycomposites.co.uk easy_composites
metal-supermarkets metalsupermarkets.com metal_supermarkets
farnell element14.com farnell_newark
octopart nexar.com octopart_nexar
rs-components rs-online.com rsdelivers.com rs_components
rutronik rutronik.com rutronik24.com
trustedparts trustedparts.com
hlh-rapid hlhrapid.com hlh_rapid
star-rapid starrapid.com star_rapid
bosch-rexroth boschrexroth.com bosch_rexroth
hokuyo hokuyo-usa.com
livox livoxtech.com
craftcloud craftcloud3d.com
"""
_SIMPLE_DOMAINS = """cubemars mjbots robstride tmotor waveshare fictiv fractory geomiq pcbway protolabs rapiddirect weerg cicoil harting harwin hirose molex samtec wago auterion betafpv holybro iflight modalai speedybee arrow digikey lcsc mouser sourcengine adafruit dfrobot pololu raspberrypi robotshop sparkfun icomold faulhaber thk circuithub elecrow eurocircuits macrofab nextpcb pcbgogo molicel facfox sculpteo shapeways treatstock fathommfg protiq quickparts rapidmade benewake luxonis nvidia septentrio sick slamtec laserboost laserhub oshcut ponoko protocase sendcutsend alro dragonplate goodfellow accu andymark gobilda servocity"""
_DOMAINS: dict[str, str] = {}
_ALIASES: dict[str, str] = {}
for _line in _DOMAIN_ROWS.strip().splitlines():
    _key, *_items = _line.split()
    _ALIASES[_key] = _key
    for _item in _items:
        if '.' in _item:
            _DOMAINS[_item] = _key
        else:
            _ALIASES[_item.replace('_', '-')] = _key
for _key in _SIMPLE_DOMAINS.split():
    _DOMAINS[_key + '.com'] = _key
    _ALIASES[_key] = _key
for _domain, _key in {'amfg.ai': 'amfg', 'facturee.de': 'facturee', 'aisler.net': 'aisler', 'kitronik.co.uk': 'kitronik', 'pimoroni.com': 'pimoroni', 'panasonic.com': 'panasonic', 'panasonic.eu': 'panasonic', '8020.net': '8020', 'accu.co.uk': 'accu', 'fathommfg.com': 'fathom'}.items():
    _DOMAINS[_domain] = _key
    _ALIASES[_key] = _key

_API = {'public', 'requires_key', 'partner', 'web_only', 'unknown'}
_TYPES = {'capability', 'material', 'purchased_part', 'hardware', 'finish'}
_SCRIPT = re.compile(r'<\s*(script|style|iframe|object|embed)\b[^>]*>.*?<\s*/\s*\1\s*>', re.I | re.S)
_EVENT = re.compile(r'\s+on\w+\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)', re.I)
_RESTRICT = re.compile(
    r'(?:automated|database|data[ -]?mining|scrap\w*|redistribut\w*|commercial reuse).{0,100}'
    r'(?:prohibit\w*|forbid\w*|not (?:allowed|permitted|authori[sz]ed)|disallow\w*|restrict\w*)'
    r'|(?:prohibit\w*|forbid\w*|not (?:allowed|permitted|authori[sz]ed)|disallow\w*).{0,100}'
    r'(?:automat\w*|scrap\w*|redistribut\w*|data[ -]?mining|database|commercial reuse)'
    r'|(?:ingestion|import).{0,40}(?:prohibited|blocked|not.allowed|manual.review)'
    r'|(?:audit.only|no.ingestion|manual.review.required|do.not.ingest|not.approved.for.ingestion)', re.I | re.S)


def _safe(value):
    """Keep JSON facts as inert data; remove executable markup, not prose."""
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items() if str(k).lower() not in {'__proto__', 'constructor', 'prototype'}}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    if isinstance(value, str):
        value = _EVENT.sub('', _SCRIPT.sub('', value))
        return re.sub(r'(?i)(?:javascript\s*:|data\s*:\s*text/html)', '[removed unsafe scheme]', value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _host(url):
    try:
        u = urlsplit(str(url))
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.port not in (None, 443):
            return None
        return u.hostname.lower().rstrip('.')
    except (TypeError, ValueError):
        return None


def _domain_key(host):
    return next((key for domain, key in _DOMAINS.items() if host == domain or host.endswith('.' + domain)), None)


def _slug(value):
    return re.sub(r'[^a-z0-9]+', '-', str(value).lower()).strip('-')


def _json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _status(value):
    # Do not turn a sentence containing '200' into an HTTP result, or True into 1.
    if isinstance(value, bool):
        return None
    if isinstance(value, int) or (isinstance(value, str) and re.fullmatch(r'[1-5]\d\d', value)):
        return int(value)
    return None


def _restriction(value):
    if isinstance(value, dict):
        for key, item in value.items():
            k = _slug(key)
            if k in {'ingestion-allowed', 'automated-ingestion-allowed', 'automatic-ingestion-allowed', 'allow-ingestion', 'database-import-allowed', 'reuse-allowed', 'catalog-or-cad-ingestion', 'supplier-bulk-ingestion'} and item is False:
                return f'worker restriction: {key}=false'
            if k in {'manual-review-required', 'requires-manual-review', 'manual-review', 'audit-only'} and item is True:
                return f'worker restriction: {key}=true'
            if k in {'ingestion-status', 'database-import-status', 'evidence-use', 'use', 'license-status', 'reuse-status'} and isinstance(item, str) and re.search(r'blocked|prohibited|restricted|manual.review|audit.only|not.allowed|permission.required', item, re.I):
                return f'worker restriction: {key}={item}'
            found = _restriction(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _restriction(item)
            if found:
                return found
    elif isinstance(value, str) and _RESTRICT.search(value):
        return 'worker reports restrictive terms or an ingestion/manual-review gate'
    return None


def _resolve(root, leaf, rawpath):
    if not isinstance(rawpath, str) or not rawpath:
        raise ValueError('missing source path')
    supplied = Path(rawpath)
    candidates = [supplied] if supplied.is_absolute() else [leaf / supplied, root / supplied]
    # Resolve symlinks before checking containment, even for missing paths.
    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            continue
        if resolved.is_file():
            return resolved
    raise ValueError('source path missing or outside research root')


def _capture(root, leaf, source, provider_key):
    if not isinstance(source, dict):
        raise ValueError('source must be an object')
    url = source.get('url') or source.get('source_url')
    host = _host(url)
    if not host or _domain_key(host) != provider_key:
        raise ValueError('source URL is not an allowlisted official HTTPS domain for provider')
    final = source.get('final_url') or source.get('resolved_url')
    if final and (not _host(final) or _domain_key(_host(final)) != provider_key):
        raise ValueError('source redirects outside official HTTPS provider domains')
    path = _resolve(root, leaf, source.get('path') or source.get('capture_path'))
    raw = path.read_bytes()
    if not raw:
        raise ValueError('empty retained source')
    digest = hashlib.sha256(raw).hexdigest()
    try:
        wrapper = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        wrapper = {}
    if not isinstance(wrapper, dict):
        wrapper = {}
    if {'task', 'provider', 'records', 'sources'} <= wrapper.keys():
        raise ValueError('worker results are not a retained primary source body')
    bodyfield = source.get('body_field')
    bodykeys = [bodyfield] if isinstance(bodyfield, str) else ['body', 'content', 'text', 'html']
    bodies = {key: wrapper[key].encode('utf-8') for key in bodykeys if isinstance(wrapper.get(key), str)}
    # An envelope has retrieval metadata plus body. A product JSON object with a
    # 'content' field alone is still an exact file, not necessarily an envelope.
    is_wrapper = bool(bodies and any(k in wrapper for k in ('url', 'status', 'sha256', 'captured_at', 'capture_method')))
    declared = source.get('sha256')
    scope = str(source.get('sha256_scope') or source.get('hash_scope') or '').lower()
    file_declared = source.get('capture_file_sha256')
    if file_declared and file_declared != digest:
        raise ValueError('source envelope/file SHA256 mismatch')
    body_digests = {hashlib.sha256(body).hexdigest() for body in bodies.values()}
    unretained = 'not_retained' in scope or 'not retained' in scope
    if declared:
        if not isinstance(declared, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', declared):
            raise ValueError('invalid source SHA256')
        declared = declared.lower()
        explicit_body = any(x in scope for x in ('body', 'content_utf8', 'content utf', 'extracted content')) and not any(x in scope for x in ('file bytes', 'envelope file', 'complete saved'))
        if unretained:
            # A lost original response hash is not verifiable. Require a separate
            # retained-file hash and retained-text hash; report the distinction.
            retained_hash = source.get('saved_content_sha256') or source.get('content_sha256')
            if file_declared != digest or retained_hash not in body_digests:
                raise ValueError('original body not retained and retained representation is not hash verified')
        elif explicit_body:
            if declared not in body_digests and not (not is_wrapper and declared == digest):
                raise ValueError('source body SHA256 mismatch')
        elif any(x in scope for x in ('file', 'envelope')) and declared != digest:
            raise ValueError('source file SHA256 mismatch under declared file scope')
        elif declared != digest and not (is_wrapper and declared in body_digests):
            raise ValueError('source SHA256 does not match retained file or embedded body')
    elif not file_declared:
        raise ValueError('missing source SHA256')
    for field in ('saved_content_sha256', 'body_sha256', 'content_sha256'):
        if source.get(field) and is_wrapper and source[field] not in body_digests and not ('original HTTP' in str(source.get('content_hash_scope', ''))):
            raise ValueError(f'{field} does not match retained embedded text')
    visible_body = next(iter(bodies.values()), raw).decode('utf-8', errors='replace')
    headings = ' '.join(re.findall(r'<(?:title|h1)\b[^>]*>(.*?)</(?:title|h1)>', visible_body[:50000], re.I | re.S))
    error_text = re.sub(r'<[^>]+>', ' ', headings or visible_body[:250]).strip()
    if re.search(r'access.denied|forbidden|just a moment|attention required|captcha|request.blocked|sourceerror|internal.server.error', error_text, re.I):
        raise ValueError('source error/access-denial body; HTTP status alone is insufficient')
    status = _status(source.get('status', source.get('http_status')))
    wrapper_status = _status(wrapper.get('status')) if is_wrapper else None
    if wrapper_status is not None and status is not None and wrapper_status != status:
        raise ValueError('declared status conflicts with retained capture metadata')
    if status is not None and not 200 <= status < 300:
        raise ValueError(f'unsuccessful HTTP status {status}')
    if source.get('error') or (is_wrapper and wrapper.get('error')):
        raise ValueError('source capture reports an error')
    captured = next((source.get(k) or wrapper.get(k) for k in ('captured_at', 'capture_timestamp', 'captured_at_utc', 'timestamp', 'captured_utc', 'capture_utc') if source.get(k) or wrapper.get(k)), None)
    if not isinstance(captured, str):
        raise ValueError('missing source capture timestamp')
    try:
        if datetime.fromisoformat(captured.replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('source capture timestamp requires a timezone')
    except ValueError as exc:
        raise ValueError('invalid or timezone-free source capture timestamp') from exc
    observation = json.dumps({k: source.get(k) or wrapper.get(k) for k in ('source_kind', 'source_type', 'artifact_type', 'evidence_status', 'capture_method', 'retrieval_method', 'status_note', 'extraction')}).lower()
    flags = []
    if any(x in observation for x in ('search-index', 'search index', 'index-only', 'index_only', 'image-alt', 'image alt', 'uncaptured', 'sourceerror', 'web.run', 'web reader')):
        flags.append('reader/index observation does not verify origin HTTP')
    if is_wrapper and not any(body.strip() for body in bodies.values()):
        raise ValueError('capture envelope has no retained body')
    verified = status is not None and not flags
    output = _safe(source)
    output.update(url=url, path=str(path), sha256=digest, declared_sha256=declared,
                  declared_hash_scope=scope or 'inferred from matching retained bytes',
                  hash_scope='exact_file_bytes', status=status if verified else None,
                  captured_at=captured, verification='http_verified' if verified else 'observational_unverified',
                  http_verified=verified, retained_representation_hash_verified=True,
                  original_response_hash_verified=not unretained and not is_wrapper and not flags,
                  warnings=flags)
    return output


def _records(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [dict(v, external_id=v.get('external_id', k)) for k, v in value.items() if isinstance(v, dict)]
    return []


def _record(raw):
    if not isinstance(raw, dict):
        raise ValueError('record must be an object')
    record = _safe(raw)
    rtype = record.get('record_type') or record.get('type')
    if rtype not in _TYPES:
        raise ValueError('unknown record type')
    if not isinstance(record.get('name'), str) or not record['name'].strip():
        raise ValueError('missing record name')
    record['record_type'] = rtype
    if not isinstance(record.get('facts'), dict):
        record['facts'] = {'reported_facts': record.get('facts')} if record.get('facts') is not None else {}
    external_id = record.get('external_id')
    if external_id is None or external_id == '':
        # This is a local evidence identity, never a fabricated supplier SKU.
        record['external_id'] = 'evidence:' + hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        record['identity_basis'] = 'local evidence fingerprint; supplier identifier absent'
        record['identifier_kind'] = 'local_evidence_id'
        record['original_external_id'] = None
    else:
        record['external_id'] = str(external_id)
    constraints = record.get('constraints', [])
    if isinstance(constraints, dict):
        constraints = [{'key': key, **(value if isinstance(value, dict) else {'value': value})} for key, value in constraints.items()]
    record['reported_constraints'] = _safe(raw.get('constraints', []))
    record['constraints'] = [item for item in constraints if isinstance(item, dict) and isinstance(item.get('key'), str) and item['key'] and 'value' in item] if isinstance(constraints, list) else []
    record['api_access'] = record.get('api_access') if record.get('api_access') in _API else 'unknown'
    record['license_status'] = str(record.get('license_status') or 'unknown')
    notes = record.get('notes', [])
    record['notes'] = [str(n) for n in notes] if isinstance(notes, list) else [str(notes)]
    record['supply_status'] = 'unknown'
    record['reuse_permission'] = 'unknown'  # 'explicit' alone does not grant reuse.
    for key in ('process', 'material', 'product_url', 'order_path'):
        if record.get(key) is not None and not isinstance(record[key], str):
            record[key] = json.dumps(record[key], ensure_ascii=False, sort_keys=True)
    return record



def _derived_source(source, records):
    """Store an inert factual summary, with original capture proof kept locally.

    This is explicitly derived metadata, never a verbatim HTTP response body.
    The immutable original is neither altered nor copied into the supplier DB.
    """
    provenance = {k: source.get(k) for k in ('url', 'path', 'sha256', 'declared_sha256', 'declared_hash_scope', 'captured_at', 'status', 'verification', 'original_response_hash_verified')}
    admitted = [r for r in records if r['source_url'] == source['url']]
    content = json.dumps({'source_url': source['url'], 'original_capture_provenance': provenance,
                          'records': admitted, 'redaction_provenance': 'Supplier facts only; ephemeral offer fields omitted. Original capture remains in research tree.'}, ensure_ascii=False, sort_keys=True)
    verified = source['http_verified']
    return {'url': source['url'], 'content': content, 'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest(),
            'captured_at': source['captured_at'], 'status': source['status'],
            'source_kind': 'derived_metadata', 'verification': 'verified_derived_metadata' if verified else 'observational_unverified',
            'http_verified': False, 'derived_from_original_verified': verified,
            'original_http_status': source['status'], 'original_sha256': source['sha256'],
            'original_path': source['path'], 'original_url': source['url'],
            'original_declared_sha256': source.get('declared_sha256'),
            'original_declared_hash_scope': source.get('declared_hash_scope'),
            'original_response_hash_verified': source.get('original_response_hash_verified'),
            'hash_scope': 'exact_derived_content_utf8', 'warnings': source.get('warnings', [])}


def normalize_evidence_tree(root: Path) -> dict:
    """Return deterministic provider bundles and a reasoned quarantine report.

    Only ``assignments/<group>/<leaf>/results.json`` is consumed. Group summaries
    can repeat thousands of rows and never constitute independent evidence.
    Input captures and worker results remain untouched. The returned source path
    points to immutable retained bytes for the backend's object store to copy.
    """
    root = Path(root).resolve()
    assignments = root / 'assignments' if (root / 'assignments').is_dir() else root
    quarantined = []
    excluded_fields = []
    bundles = {}
    seen = {}
    identities = defaultdict(dict)
    counts = Counter()
    for resultpath in sorted(assignments.glob('*/*/results.json')):
        counts['leaves'] += 1
        origin = str(resultpath.relative_to(root))
        try:
            if not resultpath.resolve().is_relative_to(root):
                raise ValueError('results path outside research root')
            payload = _json(resultpath)
            if not isinstance(payload, dict):
                raise ValueError('results must be an object')
        except (OSError, ValueError) as exc:
            quarantined.append({'results_path': origin, 'reason': str(exc), 'kind': 'results'})
            continue
        provider = payload.get('provider')
        p = provider if isinstance(provider, dict) else {'name': str(provider or resultpath.parent.name)}
        key = _slug(p.get('key') or p.get('id') or resultpath.parent.name)
        key = _ALIASES.get(key, key)
        # Known scoped assignments share one canonical provider identity.
        for suffix in ('-injection', '-sheet', '-tube', '-routing'):
            if key.endswith(suffix):
                base = key[:-len(suffix)]
                key = _ALIASES.get(base, base)
        bundle = bundles.setdefault(key, {'task': 'normalized supplier evidence: ' + key, 'provider': {'key': key, 'name': str(p.get('name') or key), 'website': None}, 'records': [], 'sources': [], 'blockers': [], 'summary': {}, 'research_origins': [], 'worker_metadata': []})
        for metadata_key, metadata_value in _safe(p).items():
            if metadata_key not in {'key', 'id', 'name', 'website'}:
                bundle['provider'].setdefault(metadata_key, metadata_value)
        bundle['research_origins'].append(origin)
        bundle['worker_metadata'].append({'results_path': origin, 'provider': _safe(provider), 'summary': _safe(payload.get('summary', {})), 'access_metadata': _safe({k: v for k, v in payload.items() if k not in {'records', 'sources', 'task', 'provider', 'summary', 'blockers'}})})
        bundle['blockers'].extend(_safe(payload.get('blockers', [])) if isinstance(payload.get('blockers'), list) else [str(payload.get('blockers'))])
        sources = []
        for rawsource in payload.get('sources', []):
            try:
                source = _capture(root, resultpath.parent, rawsource, key)
                sources.append(source)
                if not bundle['provider']['website']:
                    bundle['provider']['website'] = 'https://' + _host(source['url'])
                if not any(s['url'] == source['url'] and s['sha256'] == source['sha256'] for s in bundle['sources']):
                    bundle['sources'].append(source)
                counts['http_verified_sources' if source['http_verified'] else 'observational_sources'] += 1
            except (OSError, ValueError, TypeError) as exc:
                quarantined.append({'results_path': origin, 'kind': 'source', 'source': _safe(rawsource), 'reason': str(exc)})
        gate = _restriction({k: v for k, v in payload.items() if k not in {'records', 'sources', 'task', 'provider'}})
        if gate:
            bundle['_metadata_only'] = True
        for rawrecord in _records(payload.get('records')):
            counts['input_records'] += 1
            try:
                record = _record(rawrecord)
                source_url = record.get('source_url')
                cited = [s for s in sources if s['url'] == source_url and s['http_verified']]
                provenance_notes = json.dumps({k: rawrecord.get(k) for k in ('evidence_status', 'source_kind', 'source_type', 'provenance')}).lower()
                if any(marker in provenance_notes for marker in ('index-only', 'index_only', 'image-alt-only', 'image_alt_only', 'uncaptured', 'sourceerror')):
                    raise ValueError('record facts use index/image-alt/uncaptured evidence only')
                if not cited:
                    raise ValueError('record has no captured hash-verified successful primary HTTP source')
                restriction = gate or _restriction(rawrecord)
                source_gate = next((_restriction(s) for s in cited if _restriction(s)), None)
                if restriction or source_gate:
                    bundle['_metadata_only'] = True
                    raise ValueError(restriction or source_gate)
                signature = json.dumps([key, record['record_type'], record['external_id'], source_url, record['facts'], record['constraints'], record.get('process'), record.get('material'), record['name']], sort_keys=True, ensure_ascii=False)
                group = resultpath.parent.parent.name
                if signature in seen and seen[signature] != group:
                    counts['duplicate_records'] += 1
                    continue
                seen[signature] = group
                identity = (key, record['record_type'], record['external_id'])
                fingerprint = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                previous = identities[identity]
                assigned_id = previous.get(fingerprint)
                if assigned_id is None and previous:
                    assigned_id = record['external_id'] + ':variant-' + fingerprint[:16]
                if assigned_id is not None and assigned_id != record['external_id']:
                    record['original_external_id'] = record['external_id']
                    record['facts']['original_external_id'] = record['external_id']
                    record['external_id'] = assigned_id
                    record['identity_basis'] = 'distinct evidence variant sharing supplier identifier'
                previous[fingerprint] = record['external_id']
                record['evidence_results_path'] = origin
                record['source_verification'] = 'http_verified'
                if record['record_type'] in {'purchased_part', 'hardware'}:
                    record, removed = strip_offer_fields(record)
                    if removed:
                        excluded_fields.append({'results_path': origin, 'external_id': record['external_id'], 'source_url': source_url, 'excluded_paths': removed, 'reason': 'ephemeral offers retained only in immutable original research captures'})
                bundle['records'].append(record)
            except (ValueError, TypeError) as exc:
                quarantined.append({'results_path': origin, 'kind': 'record', 'record': _safe(rawrecord), 'reason': str(exc)})
    output = []
    for key in sorted(bundles):
        bundle = bundles[key]
        if bundle.pop('_metadata_only', False) or any(r['record_type'] in {'purchased_part', 'hardware'} for r in bundle['records']) or any(_restriction(s) for s in bundle['sources']):
            bundle['sources'] = [_derived_source(s, bundle['records']) for s in bundle['sources']]
        bundle['summary'] = {'record_count': len(bundle['records']), 'source_count': len(bundle['sources']), 'license_clearance': 'unknown', 'availability': 'unknown_unless_ephemeral_live_lookup'}
        output.append(bundle)
    for name in ('leaves', 'input_records', 'http_verified_sources', 'observational_sources', 'duplicate_records'):
        counts.setdefault(name, 0)
    counts.update({'providers': len(output), 'records': sum(len(b['records']) for b in output), 'quarantined': len(quarantined),
                   'quarantined_records': sum(q['kind'] == 'record' for q in quarantined),
                   'quarantined_sources': sum(q['kind'] == 'source' for q in quarantined)})
    return {'bundles': output, 'quarantined': quarantined, 'summary': dict(counts), 'excluded_fields': excluded_fields}
