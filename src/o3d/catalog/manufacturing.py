"""Sourced manufacturing metadata, isolated from purchased-part storage permissions.

A verified capture means its bytes matched a declared hash and its timestamp/status
were supplied. It does not establish factual truth, API execution or a live offer.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from .db import stable_id
from .manufacturing_models import ManufacturingJob, ManufacturingRecord, ManufacturingSearch

_RESTRICTED = re.compile(
    r'(?:no|prohibit\w*|forbid\w*|disallow\w*|not\s+(?:allow\w*|permit\w*))[^.\n]{0,70}'
    r'(?:automat\w*|scrap\w*|crawl\w*|bulk\s+download|data\s+(?:stor\w*|cach\w*))'
    r'|(?:permission|approval)[ -]required|(?:only|requires?)\s+(?:with\s+)?(?:prior\s+)?(?:written\s+)?(?:permission|approval)', re.I)


def provider_key(value: str) -> str:
    key = re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')
    if not key:
        raise ValueError('Provider must have a nonempty canonical key')
    return key


def _timestamp(source):
    value = source.get('captured_at') or source.get('timestamp') or source.get('capture_timestamp')
    if not value:
        return None
    try:
        date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return date.astimezone(timezone.utc) if date.tzinfo else None
    except ValueError:
        return None


def _offer_fields(value):
    # Share the same recursive policy with capture normalization and live feeds.
    # This includes extra top-level fields retained by ManufacturingRecord.
    from .offer_policy import contains_offer_values
    return contains_offer_values(value)


def _restriction_text(bundle):
    # Ignore innocuous 'no orders were performed' and unknown dataset licenses.
    fields = [bundle.get(k) for k in ('notes', 'blockers', 'license', 'terms', 'retention_policy', 'restrictions')]
    fields += [bundle.get('provider')]
    fields += [record.get('license_status') for record in bundle.get('records', []) if isinstance(record, dict)]
    fields += [record.get('notes') for record in bundle.get('records', []) if isinstance(record, dict)]
    return json.dumps(fields, default=str)


class ManufacturingCatalog:
    def __init__(self, catalog, provider_aliases: dict[str, str] | None = None):
        self.catalog = catalog
        self.conn = catalog.conn
        self.provider_aliases = {provider_key(k): provider_key(v) for k, v in (provider_aliases or {}).items()}

    def migrate(self):
        self.catalog.migrate()
        schema = Path(__file__).with_name('manufacturing_schema.sql').read_text()
        digest = hashlib.sha256(schema.encode()).hexdigest()
        with self.conn.transaction():
            self.conn.execute('SELECT pg_advisory_xact_lock(634917023)')
            row = self.conn.execute('SELECT sha256 FROM schema_versions WHERE version=2').fetchone()
            if row:
                if row['sha256'] != digest:
                    raise ValueError('Schema 2 checksum changed; add a migration rather than editing a deployed migration')
                return {'version': 2, 'changed': False}
            self.conn.execute(schema)
            self.conn.execute('INSERT INTO schema_versions(version,sha256) VALUES (2,%s)', (digest,))
        return {'version': 2, 'changed': True}

    def _key(self, name):
        key = provider_key(name)
        seen = set()
        while key in self.provider_aliases:
            if key in seen:
                raise ValueError('Provider alias cycle')
            seen.add(key)
            key = self.provider_aliases[key]
        return key

    def ingest_bundle(self, bundle: dict, base_dir: str | Path | None = None):
        self.migrate()
        supplied_provider = bundle.get('provider')
        if isinstance(supplied_provider, dict):
            name = supplied_provider.get('name') or supplied_provider.get('key') or supplied_provider.get('id')
            raw_key = supplied_provider.get('key') or supplied_provider.get('id') or name
        else:
            name = raw_key = supplied_provider
        if not isinstance(name, str) or not isinstance(raw_key, str):
            raise ValueError('Bundle requires a provider name or {key,name}')
        key = self._key(raw_key)
        task = str(bundle.get('task', ''))
        if not task:
            raise ValueError('Bundle requires task provenance')
        encoded = json.dumps(bundle, sort_keys=True, default=lambda x: x.decode('utf-8', errors='replace') if isinstance(x, bytes) else str(x)).encode()
        bundle_hash = hashlib.sha256(encoded).hexdigest()
        bundle_id = stable_id(f'manufacturing-bundle:{key}:{task}:{bundle_hash}')
        policy = self.conn.execute('SELECT access FROM providers WHERE id=%s', (key,)).fetchone()
        restricted = bool(_RESTRICTED.search(_restriction_text(bundle)))
        purchased = any(r.get('record_type') in {'purchased_part', 'hardware'} for r in bundle.get('records', []) if isinstance(r, dict))
        # Never retain a restricted stock/price snapshot inside a source body either.
        offer_snapshot = any(_offer_fields(r) for r in bundle.get('records', [])
            if isinstance(r, dict) and r.get('record_type') in {'purchased_part','hardware'})
        supplied_policy = supplied_provider.get('storage_access') or supplied_provider.get('access') if isinstance(supplied_provider,dict) else None
        deny_purchased = restricted or bool(policy and policy['access'] != 'stored') or supplied_policy in {'live-only','permission-required'}
        explicitly_blocked = bundle.get('allow_ingestion') is False or bundle.get('ingestion_status') in {'blocked', 'quarantine', 'quarantined', 'rejected'}
        deny_purchased = deny_purchased or explicitly_blocked
        deny_body = explicitly_blocked or (purchased and (deny_purchased or offer_snapshot))
        withheld_urls = {r.get('source_url') for r in bundle.get('records', []) if isinstance(r,dict) and
            (r.get('allow_ingestion') is False or r.get('ingestion_status') in {'blocked','quarantine','quarantined','rejected'})}
        supplier_metadata = supplied_provider if isinstance(supplied_provider,dict) else {'name':name,'key':key}
        summary = bundle.get('summary')
        summary = dict(summary) if isinstance(summary,dict) else {'source_summary':summary}
        summary['_provider_metadata'] = supplier_metadata
        report = {'bundle_id': bundle_id, 'provider_key': key, 'records': 0, 'quarantined': 0,
                  'sources': 0, 'verified_sources': 0, 'warnings': []}
        if deny_body:
            report['warnings'].append('Purchased-source body retention withheld by permission or offer-snapshot policy')
        with self.conn.transaction():
            self.conn.execute('INSERT INTO manufacturing_providers(key,name,aliases) VALUES (%s,%s,%s) '
                'ON CONFLICT(key) DO UPDATE SET aliases=(SELECT ARRAY(SELECT DISTINCT unnest(manufacturing_providers.aliases || excluded.aliases))),updated_at=now()',
                (key, name, [name, raw_key]))
            self.conn.execute('INSERT INTO manufacturing_bundles(id,provider_key,task,payload_sha256,summary,blockers) '
                'VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                (bundle_id, key, task, bundle_hash, Jsonb(summary), Jsonb(bundle.get('blockers', []))))
            sources_by_url = {}
            for ordinal, source in enumerate(bundle.get('sources', [])):
                url = source.get('url')
                if not isinstance(url, str) or not url:
                    report['warnings'].append(f'Source {ordinal} has no URL')
                    continue
                content, problem = None, None
                path = source.get('path')
                if source.get('content') is not None:
                    content = source['content']
                    if isinstance(content, str):
                        content = content.encode()
                    if not isinstance(content, bytes):
                        problem = 'invalid_content'
                        content = None
                elif path:
                    capture_path = Path(path)
                    if not capture_path.is_absolute():
                        capture_path = Path(base_dir or '.') / capture_path
                    try:
                        content = capture_path.read_bytes()
                    except OSError:
                        problem = 'missing_capture'
                else:
                    problem = 'missing_capture'
                declared = source.get('sha256')
                actual = hashlib.sha256(content).hexdigest() if content is not None else None
                captured = _timestamp(source)
                status = source.get('status')
                if type(status) is not int:
                    status = None
                original_status = source.get('original_http_status')
                derived_verified = source.get('derived_from_original_verified') is True and bool(re.fullmatch(r'[0-9a-f]{64}', str(source.get('original_sha256','')))) and type(original_status) is int and 200 <= original_status < 300
                verification = problem or ('hash_mismatch' if actual != declared else
                    'missing_capture_time' if captured is None else
                    ('verified_derived_metadata' if derived_verified else 'observational_unverified') if source.get('source_kind') == 'derived_metadata' else
                    'observational_unverified' if source.get('http_verified') is False or source.get('verification') in {'observational_unverified', 'unverified', 'local_observation'} else 'verified_capture')
                object_key = None
                retain_body = content is not None and actual == declared and not ((deny_body or url in withheld_urls) and status is not None and 200 <= status < 300)
                if retain_body:
                    actual, object_key = self.catalog.objects.put(content)
                source_id = stable_id(f'manufacturing-source:{bundle_id}:{ordinal}:{url}:{actual}')
                metadata = {k: v for k, v in source.items() if k not in {'content'}}
                if not retain_body and content is not None:
                    metadata['body_retention'] = 'withheld'
                self.conn.execute('INSERT INTO manufacturing_sources(id,bundle_id,url,capture_path,http_status,captured_at,declared_sha256,sha256,object_key,verification,metadata) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                    (source_id,bundle_id,url,str(path) if path else None,status,captured,declared,actual,object_key,verification,Jsonb(metadata)))
                evidence = {'id': source_id, 'verification': verification, 'status': status, 'object_key': object_key}
                sources_by_url.setdefault(url, []).append(evidence)
                report['sources'] += 1
                report['verified_sources'] += verification in {'verified_capture', 'verified_derived_metadata'}
            seen_records = {}
            for ordinal, raw in enumerate(bundle.get('records', [])):
                reason = None
                try:
                    record = ManufacturingRecord.model_validate(raw)
                except ValidationError as error:
                    record = None
                    reason = 'Invalid record schema: ' + '; '.join('.'.join(map(str,e['loc'])) for e in error.errors())
                if record:
                    candidates = sources_by_url.get(record.source_url, [])
                    source = next((s for s in candidates if s['verification'] in {'verified_capture', 'verified_derived_metadata'} and s['status'] is not None and 200 <= s['status'] < 300 and s['object_key']), None)
                    identity = (record.external_id, record.record_type)
                    semantic = record.model_dump(exclude={'notes'})
                    if raw.get('allow_ingestion') is False or raw.get('ingestion_status') in {'blocked','quarantine','quarantined','rejected'} or explicitly_blocked:
                        reason = 'Record explicitly withheld from ingestion by evidence policy'
                    elif identity in seen_records and seen_records[identity] != semantic:
                        reason = 'Duplicate identifier has distinct source variant facts; assign a distinct variant external_id'
                    elif record.record_type in {'purchased_part', 'hardware'} and deny_purchased:
                        reason = 'Purchased-data retention requires provider permission; access evidence only retained'
                    elif record.record_type in {'purchased_part', 'hardware'} and _offer_fields(raw):
                        reason = 'Offer/stock snapshots are excluded; use a permitted live-offer adapter'
                    elif not source:
                        reason = 'Record lacks a hash-verified successful captured source with timestamp and retained bytes'
                    else:
                        record_id = stable_id(f'manufacturing-record:{key}:{record.record_type}:{record.external_id}')
                        existing = self.conn.execute('SELECT process,material,payload FROM manufacturing_records WHERE id=%s', (record_id,)).fetchone()
                        if existing and any(existing[k] != getattr(record,k) for k in ('process', 'material')):
                            reason = 'External identifier collision changes process/material; use distinct source variant identifiers'
                        elif existing and (existing['payload'].get('task') != task or existing['payload'].get('source_url') != record.source_url) and any(existing['payload'].get(k) != semantic.get(k) for k in ('name','facts','constraints')):
                            reason = 'Identifier collides with distinct evidence from another task/source; assign a distinct source variant external_id'
                if reason:
                    self.conn.execute('INSERT INTO manufacturing_quarantine(bundle_id,ordinal,external_id,record_type,reason) VALUES (%s,%s,%s,%s,%s) '
                        'ON CONFLICT(bundle_id,ordinal) DO UPDATE SET reason=excluded.reason',
                        (bundle_id,ordinal,raw.get('external_id') if isinstance(raw,dict) else None,raw.get('record_type') if isinstance(raw,dict) else None,reason))
                    report['quarantined'] += 1
                    continue
                seen_records[(record.external_id, record.record_type)] = record.model_dump(exclude={'notes'})
                payload = record.model_dump()
                # Preserve task, supplier and original unknown fields in the source JSON.
                payload.update(task=task, provider=name, provider_metadata=supplier_metadata)
                search_text = ' '.join([key, name, record.name, record.external_id, record.process or '', record.material or '', json.dumps(payload, sort_keys=True)])
                self.conn.execute('INSERT INTO manufacturing_records(id,provider_key,external_id,record_type,name,process,material,api_access,source_id,payload,search_text) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET name=excluded.name, '
                    'api_access=excluded.api_access,source_id=excluded.source_id,payload=excluded.payload,search_text=excluded.search_text,updated_at=now()',
                    (record_id,key,record.external_id,record.record_type,record.name,record.process,record.material,record.api_access,source['id'],Jsonb(payload),search_text))
                self.conn.execute('DELETE FROM manufacturing_constraints WHERE record_id=%s', (record_id,))
                for position, constraint in enumerate(record.constraints):
                    number = float(constraint.value) if type(constraint.value) in (int,float) and math.isfinite(constraint.value) else None
                    self.conn.execute('INSERT INTO manufacturing_constraints(record_id,ordinal,key,value,numeric_value,unit,source_field) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                        (record_id,position,constraint.key,Jsonb(constraint.value),number,constraint.unit,constraint.source_field))
                self.conn.execute('DELETE FROM manufacturing_quarantine WHERE bundle_id=%s AND ordinal=%s', (bundle_id,ordinal))
                report['records'] += 1
        return report

    def get(self, record_id: str):
        row = self.conn.execute('SELECT * FROM manufacturing_records WHERE id=%s', (record_id,)).fetchone()
        if not row:
            raise KeyError(record_id)
        row.pop('search_text')
        row.pop('search_vector')
        payload = row.pop('payload')
        result = {**payload, **row}
        result['provenance'] = self.conn.execute('SELECT * FROM manufacturing_sources WHERE id=%s', (row['source_id'],)).fetchone()
        supplier = self.conn.execute('SELECT * FROM manufacturing_providers WHERE key=%s', (row['provider_key'],)).fetchone()
        result['supplier'] = {**result.get('provider_metadata',{}), **supplier}
        result['availability'] = {'state':'unknown','reason':'Metadata only; no live price, stock or quote checked'}
        result['manufacturability'] = {'state':'unverified','reason':'Capability evidence does not establish fit, feasibility or a quote'}
        return result

    def search(self, request: ManufacturingSearch):
        if isinstance(request, dict):
            request = ManufacturingSearch.model_validate(request)
        clauses, args = ['true'], {'q':request.q, 'limit':request.limit, 'offset':request.offset,
            'pattern':'%' + request.q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'}
        for key in ('provider', 'record_type', 'process', 'material', 'api_access'):
            value = getattr(request, key)
            if value is not None:
                column = 'provider_key' if key == 'provider' else key
                args[key] = self._key(value) if key == 'provider' else value
                clauses.append(f'r.{column}=%({key})s')
        if request.facts:
            args['facts'] = Jsonb({'facts':request.facts})
            clauses.append('r.payload @> %(facts)s::jsonb')
        for index, numeric in enumerate(request.numeric):
            args[f'key{index}'], args[f'unit{index}'] = numeric.key, numeric.unit
            limits = [f'c.key=%(key{index})s',f'c.unit=%(unit{index})s',"c.source_field IS NOT NULL AND c.source_field<>''",'c.numeric_value IS NOT NULL']
            for bound, operator in (('min','>='),('max','<=')):
                if (value := getattr(numeric,bound)) is not None:
                    args[f'{bound}{index}'] = value
                    limits.append(f'c.numeric_value{operator}%({bound}{index})s')
            clauses.append('EXISTS (SELECT 1 FROM manufacturing_constraints c WHERE c.record_id=r.id AND ' + ' AND '.join(limits) + ')')
        clauses.append("(%(q)s='' OR r.search_vector @@ websearch_to_tsquery('simple',%(q)s) OR r.name ILIKE %(pattern)s OR r.external_id=%(q)s)")
        where = ' AND '.join(clauses)
        total = self.conn.execute('SELECT count(*) AS n FROM manufacturing_records r WHERE ' + where,args).fetchone()['n']
        rows = self.conn.execute('SELECT r.id,ts_rank_cd(r.search_vector,websearch_to_tsquery(\'simple\',%(q)s)) AS score '
            'FROM manufacturing_records r WHERE ' + where + ' ORDER BY score DESC,r.name,r.id LIMIT %(limit)s OFFSET %(offset)s',args).fetchall()
        return {'items':[self.get(str(row['id'])) for row in rows], 'total':total,'limit':request.limit,'offset':request.offset,
                'retrieval':'exact-fulltext','warnings':['Metadata only; supplier review and a current quote are required']}

    def status(self):
        result = {}
        for table in ('manufacturing_records','manufacturing_sources','manufacturing_bundles','manufacturing_quarantine'):
            result[table] = self.conn.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n']
        result['providers'] = self.conn.execute("SELECT p.*,(SELECT b.summary->'_provider_metadata' FROM manufacturing_bundles b WHERE b.provider_key=p.key ORDER BY b.ingested_at DESC,b.id DESC LIMIT 1) AS metadata,count(r.id) AS records FROM manufacturing_providers p "
            'LEFT JOIN manufacturing_records r ON r.provider_key=p.key GROUP BY p.key ORDER BY p.key').fetchall()
        result['record_types'] = self.conn.execute('SELECT record_type,count(*) AS records FROM manufacturing_records GROUP BY record_type ORDER BY record_type').fetchall()
        result['verification'] = self.conn.execute('SELECT verification,count(*) AS sources FROM manufacturing_sources GROUP BY verification ORDER BY verification').fetchall()
        result['availability'] = 'No live offers stored'
        return result

    def match(self, request: ManufacturingJob):
        """Conservative source matching, with no rotation, unit conversion or inferred bounds.

        Recognized source keys: max_x/max_y/max_z (exact dimension unit),
        min_quantity/max_quantity (unit 'count'), minimum_tolerance (exact tolerance
        unit; smaller is better), supported_formats (facts list of exact strings).
        A tolerance is an absolute +/- magnitude, never total band or an ISO class.
        Canonical constraints must explicitly state that semantic in source_field.
        For other supplier-specific facts pass numeric filters with their exact keys.
        """
        if isinstance(request, dict):
            request = ManufacturingJob.model_validate(request)
        # Select the relevant source rows before pagination. Both process-level
        # capability evidence and provider material/process combinations can describe
        # custom manufacture. Exact source labels are compared without synonyms.
        clauses = ["record_type IN ('capability','material')"]
        args = {'limit':request.limit,'offset':request.offset}
        if request.provider:
            clauses.append('provider_key=%(provider)s')
            args['provider'] = self._key(request.provider)
        unknown_rank = []
        for field in ('process','material'):
            if (value := getattr(request,field)) is not None:
                clauses.append(f'({field} IS NULL OR lower({field})=lower(%({field})s))')
                args[field] = value
                unknown_rank.append(f'(CASE WHEN {field} IS NULL THEN 1 ELSE 0 END)')
        where = ' AND '.join(clauses)
        total = self.conn.execute('SELECT count(*) AS n FROM manufacturing_records WHERE ' + where,args).fetchone()['n']
        rank = ' + '.join(unknown_rank) or '0'
        rows = self.conn.execute('SELECT id,(' + rank + ') AS unknown_rank FROM manufacturing_records WHERE ' + where +
            ' ORDER BY unknown_rank,name,id LIMIT %(limit)s OFFSET %(offset)s',args).fetchall()
        found = {'items':[self.get(str(row['id'])) for row in rows], 'total':total,
            'warnings':['Metadata only; supplier review and a current quote are required',
                'Explicit source process/material mismatches are excluded before pagination; missing values require review']}
        items = []
        for record in found['items']:
            reasons, missing, rejected = [], False, False
            for key in ('process','material'):
                requirement = getattr(request,key)
                if requirement:
                    actual = record.get(key)
                    if actual is None:
                        reasons.append(f'{key}: no explicit sourced value')
                        missing = True
                    elif actual.casefold() != requirement.casefold():
                        reasons.append(f'{key}: sourced value {actual!r} differs from requested {requirement!r}')
                        rejected = True
            supported = record.get('facts',{}).get('supported_formats')
            if request.formats:
                if not isinstance(supported,list) or not all(isinstance(f,str) for f in supported):
                    missing = True
                    reasons.append('formats: no explicit supported_formats source list')
                elif any(fmt.casefold() not in {f.casefold() for f in supported} for fmt in request.formats):
                    rejected = True
                    reasons.append('formats: requested format is outside the explicit supported list')
            requirements = list(request.numeric)
            from .models import NumericFilter
            for axis, size in request.dimensions.items():
                requirements.append(NumericFilter(key='max_'+axis,unit=request.dimension_unit,min=size))
            if request.quantity is not None:
                requirements += [NumericFilter(key='min_quantity',unit='count',max=request.quantity),
                                 NumericFilter(key='max_quantity',unit='count',min=request.quantity)]
            if request.tolerance is not None:
                requirements.append(NumericFilter(key='minimum_tolerance',unit=request.tolerance_unit,max=request.tolerance))
            for requirement in requirements:
                constraints = [c for c in record['constraints'] if c.get('key') == requirement.key and c.get('unit') == requirement.unit
                    and c.get('source_field') and type(c.get('value')) in (int,float)]
                if not constraints:
                    missing = True
                    reasons.append(f'{requirement.key}: missing explicit numeric source bound in {requirement.unit}')
                elif not all((requirement.min is None or c['value'] >= requirement.min) and
                    (requirement.max is None or c['value'] <= requirement.max) for c in constraints):
                    rejected = True
                    reasons.append(f'{requirement.key}: explicit source bound fails requested filter in {requirement.unit}')
            if record.get('facts',{}).get('alias_only'):
                missing = True
                reasons.append('Alias evidence alone does not describe a manufacturing capability')
            state = 'rejected' if rejected else 'needs_review' if missing else 'candidate'
            reasons.append('Supplier engineering review and current quote required; this is not a manufacturability guarantee')
            items.append({'record':record,'state':state,'reasons':reasons})
        return {'items':items,'total':found['total'],'limit':request.limit,'offset':request.offset,
                'warnings':found['warnings']}
