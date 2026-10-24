"""Exact vendor SKU → sourced CAD → bounded local geometry inspection.

Inspection never changes source facts or catalog asset download permissions. Public
access and a successful parse establish neither redistribution rights nor fit.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import ipaddress
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit

from pydantic import BaseModel, ConfigDict, Field

MAX_DOWNLOAD = 20_000_000
MAX_EXPANDED = 50_000_000
MAX_MEMBERS = 100
VENDOR_HOSTS = frozenset({host for domain in (
    'revrobotics.com', 'gobilda.com', 'servocity.com', 'cubemars.com', 'robotis.com'
) for host in (domain, 'www.' + domain)})
# Repository scope is checked on every redirect as well as the initial request.
OFFICIAL_REPOSITORIES = frozenset({('adafruit', 'Adafruit_CAD_Parts'), ('luxonis', 'depthai-hardware')})
CAD_EXTENSIONS = {'step', 'stp', 'zip', 'f3d'}
CAD_LABEL = re.compile(r'(?:^|[_\s-])(?:cad|step|stp|f3d|models?)(?:$|[_\s-])', re.I)
URL_LABEL = re.compile(r'(?:url|uri|href|link|download|reference|path|models?)', re.I)


class VendorCADSearch(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    q: str = Field(default='', max_length=500)
    provider: str | None = None
    sku: str | None = None
    format: str | None = None
    geometry_validated: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)


def _https(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password and parsed.port in (None, 443)
    except ValueError:
        return False


def cad_links(payload: dict) -> list[dict]:
    """Only literal URL fields labelled CAD, or URL fields with a CAD suffix.

    CAD booleans, marketing mentions, prose and arbitrary URLs under a vaguely
    named ancestor do not become downloadable assets. Nothing is synthesized.
    """
    found = {}

    def visit(value, path=(), cad_context=False, url_context=False):
        if isinstance(value, dict):
            # Typical sourced asset objects carry their label beside their URL.
            label = str(value.get('kind') or value.get('format') or value.get('type') or '')
            object_cad = bool(CAD_LABEL.search(label))
            for key, child in value.items():
                key = str(key)
                visit(child, (*path, key), cad_context or bool(CAD_LABEL.search(key)) or object_cad,
                      bool(URL_LABEL.search(key)))
        elif isinstance(value, list):
            for i, child in enumerate(value):
                visit(child, (*path, str(i)), cad_context, url_context)
        elif _https(value):
            suffix = Path(unquote(urlsplit(value).path)).suffix.lower().lstrip('.')
            if url_context and (cad_context or suffix in CAD_EXTENSIONS):
                found.setdefault(value, {'url': value, 'format': suffix if suffix in CAD_EXTENSIONS else 'reference',
                    'source_field': '.'.join(path), 'validation_level': 'source_provided',
                    'physical_accuracy': 'unverified'})

    visit(payload)
    return list(found.values())


def _record_item(row):
    payload = row.get('payload') or {}
    facts = payload.get('facts') or {}
    sku = payload.get('sku') or facts.get('sku') or payload.get('supplier_sku') or facts.get('supplier_sku') or row.get('external_id')
    # order_path is often prose. It qualifies only when the source supplied a URL.
    product = payload.get('product_url') or facts.get('product_url')
    order = payload.get('order_path') or payload.get('order_url') or facts.get('order_url')
    product = product if _https(product) else None
    order = order if _https(order) else None
    links = cad_links(payload)
    if not sku or not (product or order) or not links:
        return None
    source = {'record_id': str(row['id']), 'source_id': str(row.get('source_id') or ''),
              'source_url': row.get('evidence_url') or payload.get('source_url'),
              'source_sha256': row.get('evidence_sha256'), 'source_verification': row.get('evidence_verification'),
              'captured_at': str(row.get('evidence_captured_at') or ''),
              'bundle_id': str(row.get('evidence_bundle_id') or '')}
    return {'id': str(row['id']), 'record_id': str(row['id']), 'external_id': row.get('external_id'),
            'sku': str(sku), 'name': row['name'], 'provider': row['provider_key'],
            'origin_provider': payload.get('origin_provider') or row['provider_key'],
            'product_url': product, 'order_url': order, 'cad_links': links,
            'license': payload.get('cad_license') or facts.get('cad_license') or 'unknown',
            'license_url': payload.get('cad_license_url') or facts.get('cad_license_url'),
            'validation_level': 'source_provided', 'physical_accuracy': 'unverified',
            'availability': 'unknown', 'geometry_scope': 'unverified', 'source_chain': source}


class VendorCAD:
    def __init__(self, catalog):
        self.catalog = catalog
        self.conn = catalog.conn
        self.report_root = catalog.objects.root / 'vendor-cad'

    def _rows(self, record_id=None, provider=None):
        clauses = ["r.record_type IN ('purchased_part','hardware')"]
        args = {}
        if record_id is not None:
            clauses.append('r.id=%(record_id)s')
            args['record_id'] = record_id
        if provider:
            clauses.append('r.provider_key=%(provider)s')
            args['provider'] = provider
        return self.conn.execute('SELECT r.id,r.provider_key,r.external_id,r.name,r.source_id,r.payload,'
            's.url AS evidence_url,s.sha256 AS evidence_sha256,s.verification AS evidence_verification,'
            's.captured_at AS evidence_captured_at,s.bundle_id AS evidence_bundle_id '
            'FROM manufacturing_records r JOIN manufacturing_sources s ON s.id=r.source_id WHERE '
            + ' AND '.join(clauses) + ' ORDER BY r.name,r.id', args)

    @staticmethod
    def _report_name(record_id, url):
        # Never interpolate untrusted record IDs into a filesystem path.
        record_hash = hashlib.sha256(str(record_id).encode()).hexdigest()
        return record_hash + '-' + hashlib.sha256(url.encode()).hexdigest() + '.json'

    def _read_report(self, record_id, url):
        path = self.report_root / self._report_name(record_id, url)
        if not path.exists():
            return None
        try:
            pointer = json.loads(path.read_text())
            return json.loads(self.catalog.objects.read(pointer['object_key']))
        except (ValueError, KeyError, OSError):
            return None

    def _save_report(self, report):
        encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False).encode()
        digest, key = self.catalog.objects.put(encoded)
        self.report_root.mkdir(parents=True, exist_ok=True)
        pointer = {'object_key': key, 'sha256': digest, 'record_id': report['record_id'], 'cad_url': report['cad_url']}
        path = self.report_root / self._report_name(report['record_id'], report['cad_url'])
        fd, temporary = tempfile.mkstemp(prefix='.inspection-', dir=self.report_root)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(pointer, stream, sort_keys=True)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {**report, 'report_sha256': digest, 'report_object_key': key}

    def search(self, request: VendorCADSearch | dict):
        if isinstance(request, dict):
            request = VendorCADSearch.model_validate(request)
        items, scanned, eligible, total = [], 0, 0, 0
        for row in self._rows(provider=request.provider):
            scanned += 1
            item = _record_item(row)
            if item is None:
                continue
            eligible += 1
            if request.sku and request.sku.casefold() != item['sku'].casefold():
                continue
            if request.q and request.q.casefold() not in json.dumps(item, default=str).casefold():
                continue
            if request.format and not any(link['format'] == request.format.lower() for link in item['cad_links']):
                continue
            for link in item['cad_links']:
                report = self._read_report(item['id'], link['url'])
                if report:
                    link['inspection'] = report
                    link['validation_level'] = report['validation_level']
            if any(link['validation_level'] == 'geometry_validated' for link in item['cad_links']):
                item['validation_level'] = 'geometry_validated'
            if request.geometry_validated and item['validation_level'] != 'geometry_validated':
                continue
            # All eligibility and query filters happen before pagination. Retain
            # only the requested window, even for large evidence collections.
            if request.offset <= total < request.offset + request.limit:
                items.append(item)
            total += 1
        return {'items': items, 'source_chains': [i['source_chain'] for i in items],
                'scanned_records': scanned, 'eligible_records': eligible, 'total': total,
                'limit': request.limit, 'offset': request.offset,
                'warnings': ['Source-linked CAD is not proof of SKU geometry, physical fit, availability or redistribution rights']}

    def inspect(self, record_id, cad_url=None, expected_dimensions_mm=None, tolerance_mm=0.05,
                cad_python=None, fetcher=None):
        if not math.isfinite(tolerance_mm) or tolerance_mm < 0:
            raise ValueError('Numerical comparison tolerance must be finite and nonnegative')
        if expected_dimensions_mm is not None and (len(expected_dimensions_mm) != 3 or
                any(not math.isfinite(v) or v <= 0 for v in expected_dimensions_mm)):
            raise ValueError('Expected dimensions must contain three positive finite millimeter values')
        rows = list(self._rows(record_id=record_id))
        if not rows or not (item := _record_item(rows[0])):
            raise ValueError('Record has no exact purchasable SKU with sourced CAD links')
        links = item['cad_links']
        if cad_url is None:
            direct = [link for link in links if link['format'] in {'step', 'stp', 'zip'}]
            if len(direct) != 1:
                raise ValueError('Select one exact sourced CAD URL; candidates: ' + ', '.join(link['url'] for link in links))
            cad_url = direct[0]['url']
        if cad_url not in {link['url'] for link in links}:
            raise ValueError('CAD URL is not a literal CAD link in the selected record')
        _validate_url(cad_url)
        report = {'record_id': item['id'], 'sku': item['sku'], 'origin_provider': item['origin_provider'],
            'product_url': item['product_url'], 'order_url': item['order_url'], 'cad_url': cad_url,
            'source_chain': item['source_chain'], 'inspected_at': datetime.now(timezone.utc).isoformat(),
            'status': 'unverified', 'validation_level': 'source_provided', 'physical_accuracy': 'unverified',
            'license': item['license'], 'license_url': item['license_url'], 'geometry_scope': 'unverified',
            'sku_revision_match': 'unverified',
            'redistribution': 'unknown', 'design_use': 'review_required', 'unit': 'unknown',
            'expected_dimensions_mm': expected_dimensions_mm, 'numerical_tolerance_mm': tolerance_mm,
            'comparison_note': 'Axis-order-independent bounding-box comparison only; this is not a manufacturing tolerance or physical-fit validation'}
        try:
            body, final_url = _download(cad_url, fetcher)
            report['resolved_url'] = final_url
            report['download_sha256'], report['download_object_key'] = self._put_readonly(body)
            report['download_bytes'] = len(body)
            filename = PurePosixPath(unquote(urlsplit(final_url).path)).name
            if zipfile.is_zipfile(io.BytesIO(body)):
                report['archive_sha256'] = report['download_sha256']
                filename, body = _select_step(body, item['sku'], item['external_id'])
                report['archive_member'] = filename
            if not _step_signature(body):
                report.update(status='unsupported_format', error='Selected bytes are not an ISO-10303-21 STEP file; references and F3D require a source-provided STEP export')
                return self._save_report(report)
            report['step_sha256'], report['step_object_key'] = self._put_readonly(body)
            report['step_bytes'] = len(body)
            report['validation_level'] = 'step_signature_verified'
            parser = cad_python or os.environ.get('O3D_CAD_PYTHON')
            if not parser and importlib.util.find_spec('build123d') is not None:
                parser = sys.executable
            if not parser:
                report.update(status='parser_unavailable', error='STEP bytes saved; set O3D_CAD_PYTHON to an interpreter with build123d for measured validation')
                return self._save_report(report)
            geometry = _parse_step(body, parser)
            report['geometry'] = geometry
            report['source_length_unit'] = _step_length_unit(body)
            if not geometry.get('valid'):
                report.update(status='invalid_geometry', error='Geometry parser did not establish valid geometry')
                return self._save_report(report)
            dims = geometry.get('bounds_mm')
            if not isinstance(dims, list) or len(dims) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in dims):
                raise ValueError('Geometry parser did not return finite positive bounds in millimeters')
            if geometry.get('unit') != 'mm' or report['source_length_unit'] == 'unknown':
                report.update(status='unit_unverified', error='Geometry dimensions lack an explicit parser-established millimeter unit')
                return self._save_report(report)
            report.update(status='geometry_validated' if geometry.get('solid_count', 0) > 0 else 'parsed_surface_geometry',
                validation_level='geometry_validated' if geometry.get('solid_count', 0) > 0 else 'parsed_surface_geometry',
                solid_model=geometry.get('solid_count', 0) > 0, unit='mm')
            if expected_dimensions_mm is not None:
                deviations = [abs(a-b) for a, b in zip(sorted(dims), sorted(expected_dimensions_mm))]
                report['dimension_comparison'] = {'state': 'within_numerical_tolerance' if all(d <= tolerance_mm for d in deviations) else 'outside_numerical_tolerance',
                    'absolute_deviations_mm': deviations, 'axis_order': 'sorted', 'tolerance_mm': tolerance_mm}
        except ArchiveSelectionError as exc:
            report.update(status='archive_selection_required', error=str(exc), choices=exc.choices)
        except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile, RuntimeError) as exc:
            report.update(status='inspection_failed', error=str(exc))
        return self._save_report(report)

    def _put_readonly(self, body):
        digest, key = self.catalog.objects.put(body)
        (self.catalog.objects.root / key).chmod(0o444)
        return digest, key


def _validate_url(url):
    if not _https(url):
        raise ValueError('Inspection requires an allowlisted HTTPS URL without credentials')
    parsed = urlsplit(url)
    if parsed.hostname in VENDOR_HOSTS:
        return
    if parsed.hostname == 'raw.githubusercontent.com':
        parts = tuple(unquote(parsed.path).strip('/').split('/'))
        if len(parts) >= 4 and '..' not in parts and (parts[:2] in OFFICIAL_REPOSITORIES or parts[0] == 'ROBOTIS-GIT'):
            return
    raise ValueError('CAD host or official repository is outside the fixed inspection allowlist')


def _public_dns(url):
    hostname = urlsplit(url).hostname
    addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
        raise ValueError('CAD host resolves to a private, reserved or non-public address')
    return addresses[0][4][0]


def _download(url, fetcher=None):
    """Manual redirects: validate each hop; no cookies, auth or environment proxy."""
    import httpx
    client = None if fetcher else httpx.Client(timeout=30, follow_redirects=False, trust_env=False,
        headers={'User-Agent': 'OxygenCAD-VendorInspection/0.1'})
    try:
        for _ in range(6):
            _validate_url(url)
            if fetcher:
                # Offline seam returns a response-like object; redirects remain
                # under this function's policy, never inside the supplied fetcher.
                response = fetcher(url)
                status, headers, body = response.status_code, response.headers, response.content
                if len(body) > MAX_DOWNLOAD:
                    raise ValueError('CAD download exceeds 20 MB inspection limit')
            else:
                address = _public_dns(url)
                hostname = urlsplit(url).hostname
                # Pin the checked address to the TCP connection. Preserve SNI
                # and certificate validation for the original vendor hostname.
                request_url = httpx.URL(url).copy_with(host=address)
                client.cookies.clear()
                with client.stream('GET', request_url, headers={'Host': hostname},
                        extensions={'sni_hostname': hostname}) as response:
                    status, headers = response.status_code, response.headers
                    if status in {301, 302, 303, 307, 308}:
                        body = b''
                    elif status != 200:
                        raise ValueError(f'CAD source returned HTTP {status}; inspection does not bypass access restrictions')
                    else:
                        chunks, size = [], 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > MAX_DOWNLOAD:
                                raise ValueError('CAD download exceeds 20 MB inspection limit')
                            chunks.append(chunk)
                        body = b''.join(chunks)
            if status in {301, 302, 303, 307, 308}:
                if not headers.get('location'):
                    raise ValueError('CAD redirect lacks Location')
                url = urljoin(url, headers['location'])
                continue
            if status != 200:
                raise ValueError(f'CAD source returned HTTP {status}; inspection does not bypass access restrictions')
            return body, url
        raise ValueError('CAD redirect limit reached')
    except httpx.HTTPError as exc:
        raise ValueError('CAD download failed: ' + str(exc)) from exc
    finally:
        if client:
            client.close()


class ArchiveSelectionError(ValueError):
    def __init__(self, message, choices):
        super().__init__(message)
        self.choices = choices


def _select_step(body, sku, external_id):
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        members = archive.infolist()
        if len(members) > MAX_MEMBERS or sum(info.file_size for info in members) > MAX_EXPANDED:
            raise ValueError('CAD archive exceeds member-count or 50 MB expanded-size limit')
        for info in members:
            name = info.filename.replace('\\', '/')
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or re.match(r'^[A-Za-z]:', name) or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe CAD archive path or symlink')
            if info.flag_bits & 1:
                raise ValueError('Encrypted CAD archives are unsupported')
        candidates = [info for info in members if not info.is_dir() and PurePosixPath(info.filename).suffix.lower() in {'.step', '.stp'}]
        identifiers = [str(v).casefold() for v in (sku, external_id) if v]
        def matches(name, identifier):
            tokens = re.findall(r'[a-z0-9]+', identifier)
            pattern = r'(?<![a-z0-9])' + r'[^a-z0-9]*'.join(re.escape(token) for token in tokens) + r'(?![a-z0-9])'
            return bool(tokens and re.search(pattern, PurePosixPath(name).stem.casefold()))
        matching = [info for info in candidates if any(matches(info.filename, identifier) for identifier in identifiers)]
        if len(matching) == 1:
            selected = matching[0]
        else:
            raise ArchiveSelectionError('Select an unambiguous STEP member matching the exact SKU or sourced base identifier', [info.filename for info in candidates])
        with archive.open(selected) as stream:
            data = stream.read(MAX_EXPANDED + 1)
        if len(data) > MAX_EXPANDED:
            raise ValueError('Expanded CAD file exceeds 50 MB limit')
        return selected.filename, data


def _step_signature(body):
    return body.lstrip().startswith(b'ISO-10303-21;') and b'END-ISO-10303-21;' in body[-4096:]


def _step_length_unit(body):
    text = body.decode('ascii', errors='ignore').upper()
    match = re.search(r'SI_UNIT\s*\(\s*(\$|\.[A-Z]+\.)\s*,\s*\.METRE\.\s*\)', text)
    if match:
        return {'$': 'm', '.MILLI.': 'mm', '.CENTI.': 'cm', '.MICRO.': 'um'}.get(match[1], 'declared_si_length')
    if re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'(?:INCH|INCHES|IN)'", text):
        return 'inch'
    return 'unknown'


# A fixed program parses data as STEP; source content is never Python code. OCCT's
# STEP reader normalizes declared source length units to its millimeter unit.
_GEOMETRY_PROGRAM = r'''
import json, sys
from build123d import import_step
shape = import_step(sys.argv[1])
bbox = shape.bounding_box()
valid = shape.is_valid() if callable(shape.is_valid) else shape.is_valid
print(json.dumps({"valid": bool(valid), "unit": "mm", "bounds_mm": [float(bbox.size.X), float(bbox.size.Y), float(bbox.size.Z)], "volume_mm3": float(shape.volume), "solid_count": len(shape.solids()), "face_count": len(shape.faces())}, allow_nan=False))
'''


def _parse_step(body, interpreter):
    with tempfile.TemporaryDirectory(prefix='oxygen-vendor-cad-') as directory:
        path = Path(directory) / 'source.step'
        path.write_bytes(body)
        path.chmod(0o444)
        # No caller API tokens, AWS keys, proxies or loader injection reach OCCT.
        environment = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR') if key in os.environ}
        environment.update(HOME=directory, TMPDIR=directory, PYTHONNOUSERSITE='1')
        result = subprocess.run([str(interpreter), '-I', '-c', _GEOMETRY_PROGRAM, str(path)],
            capture_output=True, text=True, timeout=60, check=False, env=environment, cwd=directory)
        if result.returncode:
            raise ValueError('CAD parser failed: ' + result.stderr[-1500:])
        try:
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise ValueError('CAD parser did not return a geometry report') from exc
