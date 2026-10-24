import copy
import hashlib

import pytest

from o3d.catalog.manufacturing import ManufacturingCatalog
from o3d.catalog.manufacturing_models import ManufacturingJob, ManufacturingSearch
from o3d.catalog.models import NumericFilter, Provider


@pytest.fixture
def manufacturing(catalog):
    db = ManufacturingCatalog(catalog)
    db.migrate()
    catalog.conn.execute('TRUNCATE manufacturing_providers CASCADE')
    return db


def bundle(tmp_path, *, provider='Fixture Machining', external_id='cnc-aluminum', record_type='capability'):
    body = b'Public CNC aluminum: maximum X 120 mm; count 1 to 100; tolerance +/-0.1 mm.'
    path = tmp_path / 'source.txt'
    path.write_bytes(body)
    return {
        'task':'fixture/manufacturing', 'provider':provider,
        'sources':[{'url':'https://example.com/cnc','path':str(path),'sha256':hashlib.sha256(body).hexdigest(),
                    'status':200,'captured_at':'2026-10-24T12:00:00Z'}],
        'records':[{'external_id':external_id,'name':'Aluminum CNC','record_type':record_type,
            'process':'CNC milling','material':'Aluminum 6061','source_url':'https://example.com/cnc',
            'product_url':'https://example.com/cnc','api_access':'web_only','license_status':'unknown',
            'facts':{'supported_formats':['STEP'], 'unknown_field':{'retained':True}},
            'constraints':[
                {'key':'max_x','value':120,'unit':'mm','source_field':'maximum_x'},
                {'key':'min_quantity','value':1,'unit':'count','source_field':'minimum quantity'},
                {'key':'max_quantity','value':100,'unit':'count','source_field':'maximum quantity'},
                {'key':'minimum_tolerance','value':0.1,'unit':'mm','source_field':'absolute +/- tolerance'}]}],
        'summary':{'records':1},'blockers':[]}


def test_additive_migration_and_roundtrip(manufacturing, tmp_path):
    original = manufacturing.conn.execute('SELECT sha256 FROM schema_versions WHERE version=1').fetchone()
    assert manufacturing.migrate() == {'version':2,'changed':False}
    assert manufacturing.catalog.migrate() == {'version':1,'changed':False}
    assert manufacturing.conn.execute('SELECT sha256 FROM schema_versions WHERE version=1').fetchone() == original
    report = manufacturing.ingest_bundle(bundle(tmp_path))
    assert report['records'] == report['verified_sources'] == 1
    search = manufacturing.search(ManufacturingSearch(q='Aluminum',process='CNC milling'))
    assert search['total'] == 1
    row = search['items'][0]
    assert row['facts']['unknown_field'] == {'retained':True}
    assert row['provenance']['captured_at'].isoformat().startswith('2026-10-24T12:00:00')
    assert manufacturing.catalog.objects.read(row['provenance']['object_key']).startswith(b'Public CNC')
    assert row['availability']['state'] == 'unknown'
    assert row['manufacturability']['state'] == 'unverified'
    again = manufacturing.ingest_bundle(bundle(tmp_path))
    assert again['bundle_id'] == report['bundle_id']
    assert manufacturing.status()['manufacturing_records'] == 1


def test_numeric_requires_source_unit_and_real_number(manufacturing,tmp_path):
    data = bundle(tmp_path)
    data['records'][0]['constraints'] += [
        {'key':'unsourced','value':9,'unit':'mm'},
        {'key':'string_size','value':'9','unit':'mm','source_field':'size'},
        {'key':'unitless','value':9,'source_field':'size'}]
    manufacturing.ingest_bundle(data)
    assert manufacturing.search(ManufacturingSearch(numeric=[NumericFilter(key='max_x',unit='mm',min=120)]))['total'] == 1
    for key, unit in [('max_x','in'),('unsourced','mm'),('string_size','mm'),('unitless','mm')]:
        assert manufacturing.search(ManufacturingSearch(numeric=[NumericFilter(key=key,unit=unit,min=1)]))['total'] == 0
    assert manufacturing.search(ManufacturingSearch(offset=99))['total'] == 1


def test_capture_verification_quarantine_and_observational_sources(manufacturing,tmp_path):
    for mode in ('bad_hash','missing_time','missing_file','observation','blocked_http'):
        data=bundle(tmp_path,external_id=mode)
        if mode=='bad_hash': data['sources'][0]['sha256']='0'*64
        if mode=='missing_time': data['sources'][0].pop('captured_at')
        if mode=='missing_file': data['sources'][0]['path']='/no/such/file'
        if mode=='observation': data['sources'][0]['http_verified']=False
        if mode=='blocked_http': data['sources'][0]['status']=403
        report=manufacturing.ingest_bundle(data)
        assert report['quarantined']==1
        assert report['records']==0
    assert manufacturing.status()['manufacturing_sources']==5


def test_inline_source_and_canonical_alias_ids(manufacturing,tmp_path):
    db=ManufacturingCatalog(manufacturing.catalog,provider_aliases={'Former Fixture':'Fixture Machining'})
    data=bundle(tmp_path,provider='Former Fixture')
    source=data['sources'][0]
    source['content']=tmp_path.joinpath('source.txt').read_text()
    source.pop('path')
    db.ingest_bundle(data)
    first=db.search(ManufacturingSearch())['items'][0]['id']
    data['provider']='Fixture Machining'
    db.ingest_bundle(data)
    assert db.search(ManufacturingSearch(provider='Former Fixture'))['items'][0]['id']==first
    assert db.status()['manufacturing_records']==1


def test_existing_provider_permission_and_explicit_policy(manufacturing,tmp_path):
    manufacturing.catalog.register_provider(Provider(id='restricted-vendor',name='Restricted Vendor',website='https://example.com',
        access='live-only',license='proprietary',terms_url='https://example.com/terms'))
    data=bundle(tmp_path,provider='Restricted Vendor',record_type='purchased_part')
    report=manufacturing.ingest_bundle(data)
    assert report['quarantined']==1
    row=manufacturing.conn.execute('SELECT * FROM manufacturing_sources WHERE bundle_id=%s',(report['bundle_id'],)).fetchone()
    assert row['object_key'] is None
    # Public normalized capability metadata is independent of vendor stock caching.
    data['records'][0]['record_type']='capability'
    assert manufacturing.ingest_bundle(data)['records']==1
    data=bundle(tmp_path,provider='Another Vendor',record_type='hardware')
    data['records'][0]['notes']=['Terms prohibit automated scraping of the product catalog.']
    assert manufacturing.ingest_bundle(data)['quarantined']==1
    data=bundle(tmp_path,provider='Policy Vendor')
    data['records'][0]['allow_ingestion']=False
    assert manufacturing.ingest_bundle(data)['quarantined']==1


def test_offer_snapshots_not_cached(manufacturing,tmp_path):
    data=bundle(tmp_path,record_type='purchased_part')
    data['records'][0]['facts']['stock']=18
    report=manufacturing.ingest_bundle(data)
    assert report['quarantined']==1
    row=manufacturing.conn.execute('SELECT object_key FROM manufacturing_sources WHERE bundle_id=%s',(report['bundle_id'],)).fetchone()
    assert row['object_key'] is None
    assert manufacturing.conn.execute('SELECT external_id,reason FROM manufacturing_quarantine').fetchone()['external_id']=='cnc-aluminum'


def test_variant_collision_not_silently_overwritten(manufacturing,tmp_path):
    data=bundle(tmp_path)
    second=copy.deepcopy(data['records'][0])
    second['facts']['color']='red'
    data['records'].append(second)
    report=manufacturing.ingest_bundle(data)
    assert report['records']==1
    assert report['quarantined']==1
    assert 'color' not in manufacturing.search(ManufacturingSearch())['items'][0]['facts']
    changed=bundle(tmp_path)
    changed['records'][0]['process']='Turning'
    assert manufacturing.ingest_bundle(changed)['quarantined']==1
    assert manufacturing.search(ManufacturingSearch())['items'][0]['process']=='CNC milling'


def test_conservative_manufacturing_match(manufacturing,tmp_path):
    manufacturing.ingest_bundle(bundle(tmp_path))
    good=manufacturing.match(ManufacturingJob(process='CNC milling',material='Aluminum 6061',formats=['STEP'],
        quantity=5,dimensions={'x':100},tolerance=0.2))
    assert good['items'][0]['state']=='candidate'
    assert 'quote required' in good['items'][0]['reasons'][-1]
    for kwargs in ({'dimensions':{'x':121}}, {'quantity':101}, {'tolerance':0.01}, {'formats':['STL']}):
        assert manufacturing.match(ManufacturingJob(**kwargs))['items'][0]['state']=='rejected'
    for kwargs in ({'dimensions':{'y':1}},{'dimensions':{'x':1},'dimension_unit':'in'},
                   {'numeric':[NumericFilter(key='unknown',unit='mm',min=1)]}):
        assert manufacturing.match(ManufacturingJob(**kwargs))['items'][0]['state']=='needs_review'


def test_immutable_migration_checksum(manufacturing):
    manufacturing.conn.execute("UPDATE schema_versions SET sha256='wrong' WHERE version=2")
    try:
        with pytest.raises(ValueError,match='Schema 2 checksum changed'):
            manufacturing.migrate()
    finally:
        import pathlib
        import o3d.catalog.manufacturing as module
        schema=pathlib.Path(module.__file__).with_name('manufacturing_schema.sql').read_bytes()
        manufacturing.conn.execute('UPDATE schema_versions SET sha256=%s WHERE version=2',(hashlib.sha256(schema).hexdigest(),))


def test_derived_metadata_is_distinct_and_requires_original_verification(manufacturing,tmp_path):
    data=bundle(tmp_path,record_type='purchased_part')
    data['provider']={'key':'fixture-machining','name':'Fixture Machining','website':'https://example.com','country':'US'}
    source=data['sources'][0]
    source.update(source_kind='derived_metadata',http_verified=False,derived_from_original_verified=True,original_http_status=200,
        original_sha256='f'*64,original_capture_path='/evidence/original.html',redactions=['stock','price'])
    data['records'][0]['facts']['availability_unknown']=True
    report=manufacturing.ingest_bundle(data)
    assert report['records']==1
    row=manufacturing.search(ManufacturingSearch())['items'][0]
    assert row['provenance']['verification']=='verified_derived_metadata'
    assert row['provenance']['metadata']['original_sha256']=='f'*64
    assert row['supplier']['website']=='https://example.com'
    assert manufacturing.status()['providers'][0]['metadata']['country']=='US'
    for bad_value in (False,None):
        source['derived_from_original_verified']=bad_value
        data['records'][0]['external_id']='invalid-'+str(bad_value)
        report=manufacturing.ingest_bundle(data)
        assert report['quarantined']==1
        assert report['records']==0


def test_different_task_variant_facts_do_not_overwrite(manufacturing,tmp_path):
    data=bundle(tmp_path)
    manufacturing.ingest_bundle(data)
    data['task']='another/research/group'
    data['records'][0]['facts']['color']='blue'
    assert manufacturing.ingest_bundle(data)['quarantined']==1
    assert 'color' not in manufacturing.search(ManufacturingSearch())['items'][0]['facts']


def test_material_job_candidate_filters_before_pagination(manufacturing,tmp_path):
    data=bundle(tmp_path,external_id='material-milling',record_type='material')
    data['records'][0]['name']='Zinc aluminum service material'
    target=copy.deepcopy(data['records'][0])
    for index in range(101):
        unrelated=copy.deepcopy(target)
        unrelated['external_id']=f'other-{index}'
        unrelated['name']=f'AAA unrelated turning {index}'
        unrelated['process']='Turning'
        unrelated['record_type']='capability'
        data['records'].append(unrelated)
    unknown=copy.deepcopy(target)
    unknown['external_id']='unknown-process'
    unknown['process']=None
    data['records'].append(unknown)
    assert manufacturing.ingest_bundle(data)['records']==103
    matched=manufacturing.match(ManufacturingJob(process='cnc MILLING',material='ALUMINUM 6061',limit=1))
    assert matched['total']==2
    assert matched['items'][0]['record']['record_type']=='material'
    assert matched['items'][0]['record']['external_id']=='material-milling'
    assert matched['items'][0]['state']=='candidate'
    next_page=manufacturing.match(ManufacturingJob(process='CNC milling',material='Aluminum 6061',limit=1,offset=1))
    assert next_page['items'][0]['state']=='needs_review'
    assert manufacturing.match(ManufacturingJob(process='Laser cutting'))['total']==1


@pytest.mark.parametrize('field,value', [('price',12),('unitPrice',4.50),('quantityAvailable',5),('currentStock',7),('pricing',{'tiers':[12]})])
def test_extra_top_level_offer_fields_are_not_cached(manufacturing,tmp_path,field,value):
    data=bundle(tmp_path,record_type='purchased_part')
    data['records'][0][field]=value
    report=manufacturing.ingest_bundle(data)
    assert report['records']==0
    assert report['quarantined']==1
    source=manufacturing.conn.execute('SELECT object_key FROM manufacturing_sources WHERE bundle_id=%s',(report['bundle_id'],)).fetchone()
    assert source['object_key'] is None


def test_capability_offer_words_do_not_withhold_purchased_sources(manufacturing,tmp_path):
    data=bundle(tmp_path,record_type='purchased_part')
    capability=copy.deepcopy(data['records'][0])
    capability['external_id']='capability-raw-stock-materials'
    capability['record_type']='capability'
    capability['facts']['stock_materials']=['Aluminum 6061']
    data['records'].append(capability)
    report=manufacturing.ingest_bundle(data)
    assert report['records']==2
    assert report['quarantined']==0
