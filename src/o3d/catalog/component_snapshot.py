"""Bulk metadata mirror of CDFER's public SQLite artifact; never a live JLC feed.

Only a regular jlc_components table is read. SQLite extensions, views, triggers,
virtual tables, source SQL and functions are never executed by this reader.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .db import stable_id
from .offer_policy import strip_offer_fields

SOURCE = 'community-cdfer'
SQLITE_URL = 'https://cdfer.github.io/jlcpcb-parts-database/jlcpcb-components.sqlite3'
_COLUMNS = ('id', 'source', 'lcsc', 'mpn', 'manufacturer', 'package', 'description',
    'category', 'subcategory', 'datasheet_url', 'library_type', 'basic', 'preferred',
    'assembly', 'attrition', 'attributes', 'raw_metadata', 'parse_warnings',
    'fields_removed', 'source_fetched_at', 'snapshot_id', 'search_text')


class ComponentSearch(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    q: str = Field(default='', max_length=500)
    lcsc: str | None = Field(default=None, max_length=32)
    mpn: str | None = Field(default=None, max_length=500)
    manufacturer: str | None = Field(default=None, max_length=500)
    package: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=500)
    attributes: dict = Field(default_factory=dict)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1_000_000)


def _lcsc(value):
    match = re.fullmatch(r'C?([0-9]{1,12})', str(value).strip(), re.I)
    return 'C' + str(int(match[1])) if match else None


def _timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, timezone.utc) if math.isfinite(value) else None
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return date.astimezone(timezone.utc) if date.tzinfo else None
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                return date.astimezone(timezone.utc) if date.tzinfo else None
            except (ValueError, TypeError):
                return None
    return None


def _json_value(value, field, warnings):
    if not isinstance(value, str) or not value.strip():
        return value
    def invalid_constant(value):
        raise ValueError('Non-finite JSON')
    try:
        return json.loads(value, parse_constant=invalid_constant)
    except (ValueError, TypeError, RecursionError):
        warnings.append(field + ': malformed JSON')
        # Opaque malformed JSON can conceal offer keys; keep its hash and the
        # original artifact pointer instead of retaining any such values.
        if re.search(r'price|stock|inventory|offer|availability|lead.?time', value, re.I):
            return {'withheld_malformed_json_sha256': hashlib.sha256(value.encode()).hexdigest()}
        return value


@contextmanager
def _read_sqlite(path):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        connection.enable_load_extension(False)
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('PRAGMA query_only=ON')
        entry = connection.execute("SELECT type,sql FROM sqlite_master WHERE name='jlc_components'").fetchone()
        if not entry or entry[0] != 'table' or not entry[1] or not re.match(r'^\s*CREATE\s+TABLE\b', entry[1], re.I):
            raise ValueError('Expected a regular jlc_components SQLite table')
        columns = {row[1] for row in connection.execute('PRAGMA main.table_info(jlc_components)')}
        if 'lcsc' not in columns:
            raise ValueError('jlc_components requires an lcsc column')
        def authorize(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and arg1 == 'jlc_components' and database == 'main' and trigger is None:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        connection.set_authorizer(authorize)
        connection.row_factory = sqlite3.Row
        yield connection
    finally:
        connection.close()


def _record(raw, snapshot_id):
    sku = _lcsc(raw.get('lcsc'))
    if not sku:
        return None
    warnings = []
    # SQLite BLOBs in unknown columns remain inspectable without breaking JSON.
    raw = {key: ({'blob_sha256': hashlib.sha256(value).hexdigest(), 'bytes': len(value)}
        if isinstance(value, bytes) else value) for key, value in raw.items()}
    for field in ('attributes', 'attrition'):
        if field in raw:
            raw[field] = _json_value(raw[field], field, warnings)
    clean, removed = strip_offer_fields(raw)
    attributes = clean.get('attributes')
    if not isinstance(attributes, dict):
        if attributes not in (None, ''):
            warnings.append('attributes: expected JSON object; original retained in raw_metadata')
        attributes = {}
    fetched = _timestamp(clean.get('fetched_at'))
    if clean.get('fetched_at') is not None and fetched is None:
        warnings.append('fetched_at: invalid or timezone missing')
    def text(field):
        value = clean.get(field)
        return str(value) if value is not None else None
    def flag(field):
        value = clean.get(field)
        return bool(value) if value in (0, 1) else None
    mpn = text('mfr') or text('mpn')
    description = text('description')
    search = ' '.join(str(value) for value in (sku, mpn, text('manufacturer'), text('package'),
        description, text('category'), text('subcategory'), json.dumps(attributes, ensure_ascii=False)) if value)
    # PostgreSQL text/jsonb cannot contain NUL; retain a replacement character.
    def safe(value):
        if isinstance(value, str):
            return value.replace('\x00', '\ufffd')
        if isinstance(value, dict):
            return {safe(str(k)): safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [safe(v) for v in value]
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        return value
    values = (stable_id(f'component:{SOURCE}:{sku}'), SOURCE, sku, mpn, text('manufacturer'),
        text('package'), description, text('category'), text('subcategory'), text('datasheet'),
        text('library_type'), flag('basic'), flag('preferred'), clean.get('assembly'),
        clean.get('attrition'), attributes, clean, warnings, removed, fetched, snapshot_id, search)
    return tuple(Jsonb(safe(v)) if i in (13, 14, 15, 16, 17, 18) else safe(v) for i, v in enumerate(values))


class ComponentSnapshot:
    def __init__(self, catalog):
        self.catalog, self.conn = catalog, catalog.conn

    def migrate(self):
        self.catalog.migrate()
        schema = Path(__file__).with_name('component_snapshot.sql').read_text()
        digest = hashlib.sha256(schema.encode()).hexdigest()
        with self.conn.transaction():
            self.conn.execute('SELECT pg_advisory_xact_lock(634917023)')
            row = self.conn.execute('SELECT sha256 FROM schema_versions WHERE version=3').fetchone()
            if row:
                if row['sha256'] != digest:
                    raise ValueError('Schema 3 checksum changed; add a migration rather than editing a deployed migration')
                return {'version': 3, 'changed': False}
            self.conn.execute(schema)
            self.conn.execute('INSERT INTO schema_versions(version,sha256) VALUES (3,%s)', (digest,))
        return {'version': 3, 'changed': True}

    def import_sqlite(self, path, metadata=None, batch_size=2000, max_rows=None):
        if type(batch_size) is not int or not 1 <= batch_size <= 10000:
            raise ValueError('batch_size must be between 1 and 10000')
        if max_rows is not None and (type(max_rows) is not int or max_rows < 0):
            raise ValueError('max_rows must be a nonnegative integer or None')
        path = Path(path).expanduser().resolve(strict=True)
        before = path.stat()
        if metadata is None:
            sidecar = path.with_suffix('.meta.json')
            metadata = json.loads(sidecar.read_text()) if sidecar.exists() else {}
        if not isinstance(metadata, dict):
            raise ValueError('Expected capture metadata object')
        metadata = dict(metadata)
        if metadata.get('url', SQLITE_URL) != SQLITE_URL:
            raise ValueError('Snapshot URL is outside the fixed public community source')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            if stream.read(16) != b'SQLite format 3\x00':
                raise ValueError('Expected SQLite 3 artifact')
            stream.seek(0)
            while chunk := stream.read(4 * 1024 * 1024):
                digest.update(chunk)
        digest = digest.hexdigest()
        hashed = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (hashed.st_size, hashed.st_mtime_ns, hashed.st_ino):
            raise ValueError('SQLite artifact changed while its hash was calculated')
        if metadata.get('sha256', digest) != digest:
            raise ValueError('Snapshot SHA-256 mismatch')
        declared_size = metadata.get('bytes', metadata.get('byte_size', before.st_size))
        if type(declared_size) is not int or declared_size != before.st_size:
            raise ValueError('Snapshot byte count mismatch')
        captured = _timestamp(metadata.get('captured_at'))
        verified = (metadata.get('url') == SQLITE_URL and metadata.get('sha256') == digest and
            metadata.get('status') == 200 and captured is not None and metadata.get('http_verified') is not False)
        snapshot_id = stable_id(f'component-snapshot:{SOURCE}:{digest}')
        report = dict(snapshot_id=snapshot_id, source=SOURCE, sha256=digest, byte_size=before.st_size,
            processed=0, upserted=0, skipped=0, batches=0, complete=False, warnings=[])
        # Validate the source before creating database state.
        with _read_sqlite(path) as sqlite:
            self.migrate()
            locked = self.conn.execute('SELECT pg_try_advisory_lock(634917024) AS locked').fetchone()['locked']
            if not locked:
                raise ValueError('Another component snapshot import is running')
            try:
                clean_metadata, _ = strip_offer_fields(metadata)
                self.conn.execute('''INSERT INTO component_snapshots
                    (id,source,url,sha256,byte_size,object_pointer,captured_at,last_modified,metadata,verification)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING''',
                    (snapshot_id, SOURCE, SQLITE_URL, digest, before.st_size, str(path), captured,
                     metadata.get('last_modified'), Jsonb(clean_metadata), 'verified_capture' if verified else 'observational_unverified'))
                self.conn.execute('CREATE TEMP TABLE IF NOT EXISTS component_snapshot_stage '
                    '(LIKE component_records INCLUDING DEFAULTS) ON COMMIT DELETE ROWS')
                cursor = sqlite.execute('SELECT * FROM main.jlc_components')
                columns = ','.join(_COLUMNS)
                updates = ','.join(f'{column}=excluded.{column}' for column in _COLUMNS if column not in ('id', 'source', 'lcsc'))
                while max_rows is None or report['processed'] < max_rows:
                    count = batch_size if max_rows is None else min(batch_size, max_rows - report['processed'])
                    rows = cursor.fetchmany(count)
                    if not rows:
                        report['complete'] = True
                        break
                    normalized = [value for row in rows if (value := _record(dict(row), snapshot_id)) is not None]
                    with self.conn.transaction():
                        # A bounded timeout applies to this batch only; never disable the connection's timeout.
                        self.conn.execute("SET LOCAL statement_timeout='120s'")
                        with self.conn.cursor().copy(f'COPY component_snapshot_stage ({columns}) FROM STDIN') as copy:
                            for values in normalized:
                                copy.write_row(values)
                        self.conn.execute(f'INSERT INTO component_records ({columns}) SELECT {columns} '
                            'FROM component_snapshot_stage ON CONFLICT(source,lcsc) DO UPDATE SET '
                            + updates + ',active=true,updated_at=now()')
                        report['processed'] += len(rows)
                        report['upserted'] += len(normalized)
                        report['skipped'] += len(rows) - len(normalized)
                        report['batches'] += 1
                        self.conn.execute('UPDATE component_snapshots SET processed=greatest(processed,%s) WHERE id=%s',
                            (report['processed'], snapshot_id))
                after = path.stat()
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    raise ValueError('SQLite artifact changed during import; completed batches remain auditable')
                with self.conn.transaction():
                    self.conn.execute("SET LOCAL statement_timeout='120s'")
                    self.conn.execute('UPDATE component_snapshots SET complete=complete OR %s WHERE id=%s',
                        (report['complete'], snapshot_id))
                    # Only a complete, unbounded traversal can retire missing components.
                    if report['complete'] and not report['skipped']:
                        self.conn.execute('UPDATE component_records SET active=false WHERE source=%s AND snapshot_id<>%s AND active',
                            (SOURCE, snapshot_id))
                return report
            finally:
                self.conn.execute('SELECT pg_advisory_unlock(634917024)')

    @staticmethod
    def _present(row):
        row = dict(row)
        row.pop('search_vector', None)
        row.pop('search_text', None)
        row['availability'] = {'state': 'unknown', 'reason': 'Snapshot metadata; no live offer checked'}
        row['manufacturability'] = {'state': 'unverified', 'reason': 'No manufacturing or assembly verification'}
        row['cad'] = {'state': 'none', 'download_allowed': False, 'license_status': 'unknown'}
        row['constraints'] = [{'key': key, 'value': value.get('value') if isinstance(value, dict) and 'value' in value else value,
            'unit': value.get('unit') if isinstance(value, dict) else None,
            'source_field': 'attributes.' + key} for key, value in row['attributes'].items()]
        return row

    def _select(self):
        return '''SELECT c.*,jsonb_build_object('id',s.id,'url',s.url,'sha256',s.sha256,
            'byte_size',s.byte_size,'object_pointer',s.object_pointer,'captured_at',s.captured_at,
            'last_modified',s.last_modified,'verification',s.verification) AS provenance,
            s.captured_at AS source_captured_at FROM component_records c
            JOIN component_snapshots s ON s.id=c.snapshot_id'''

    def search(self, request: ComponentSearch):
        clauses, args = ['c.active'], {'limit': request.limit, 'offset': request.offset}
        for field in ('manufacturer', 'package', 'category'):
            if (value := getattr(request, field)) is not None:
                clauses.append(f'c.{field}=%({field})s')
                args[field] = value
        if request.lcsc is not None:
            clauses.append('c.lcsc=%(lcsc)s')
            args['lcsc'] = _lcsc(request.lcsc) or ''
        if request.mpn is not None:
            clauses.append('lower(c.mpn)=lower(%(mpn)s)')
            args['mpn'] = request.mpn
        if request.attributes:
            clauses.append('c.attributes @> %(attributes)s::jsonb')
            args['attributes'] = Jsonb(request.attributes)
        order = 'c.lcsc,c.id'
        if query := request.q.strip():
            # MPN families such as STM32 are prefixes of a single full-text
            # lexeme. Treat every user character literally in the LIKE branch.
            prefix = query.lower().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            clauses.append("(c.search_vector @@ plainto_tsquery('simple',%(q)s) OR c.lcsc=%(q_lcsc)s "
                "OR lower(c.mpn)=lower(%(q)s) OR lower(c.mpn) LIKE %(mpn_prefix)s)")
            args.update(q=query, q_lcsc=_lcsc(query) or '', mpn_prefix=prefix)
            order = ('CASE WHEN c.lcsc=%(q_lcsc)s THEN 0 '
                'WHEN lower(c.mpn)=lower(%(q)s) THEN 1 ELSE 2 END,c.lcsc,c.id')
        where = ' WHERE ' + ' AND '.join(clauses)
        total = self.conn.execute('SELECT count(*) AS n FROM component_records c' + where, args).fetchone()['n']
        rows = self.conn.execute(self._select() + where + ' ORDER BY ' + order + ' LIMIT %(limit)s OFFSET %(offset)s', args).fetchall()
        return {'items': [self._present(row) for row in rows], 'total': total, 'limit': request.limit, 'offset': request.offset}

    def get(self, id_or_lcsc):
        try:
            identifier = str(UUID(str(id_or_lcsc)))
        except ValueError:
            identifier = None
        row = self.conn.execute(self._select() + ' WHERE c.source=%s AND (c.id=%s OR c.lcsc=%s)',
            (SOURCE, identifier, _lcsc(id_or_lcsc))).fetchone()
        if not row:
            raise KeyError(id_or_lcsc)
        return self._present(row)

    def status(self):
        row = self.conn.execute('SELECT count(*) AS total,count(*) FILTER(WHERE active) AS active FROM component_records').fetchone()
        sources = self.conn.execute('SELECT * FROM component_snapshots ORDER BY imported_at DESC,id').fetchall()
        return {'component_records': row['total'], 'active_component_records': row['active'],
            'component_snapshots': len(sources), 'sources': sources, 'availability': 'unknown',
            'license_status': 'unknown', 'origin': 'community_unofficial'}
