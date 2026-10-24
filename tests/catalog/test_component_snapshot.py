import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from pydantic import ValidationError

from o3d.catalog.component_snapshot import ComponentSearch, ComponentSnapshot, SQLITE_URL, _read_sqlite, _record


def artifact(tmp_path, rows=None, name='parts.sqlite3'):
    path = tmp_path / name
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE jlc_components (lcsc INTEGER PRIMARY KEY,mfr TEXT,
            manufacturer TEXT,package TEXT,description TEXT,category TEXT,subcategory TEXT,
            datasheet TEXT,attributes TEXT,attrition TEXT,fetched_at INTEGER,basic INTEGER,
            preferred INTEGER,assembly INTEGER,library_type TEXT,stock INTEGER,price TEXT,
            last_on_stock INTEGER,assembly_process TEXT)''')
        for row in rows or [dict(lcsc=123,mfr='LM-123',manufacturer='Fixture Semi',package='SOT-23',
                description='Precision voltage regulator',category='Power',subcategory='LDO',
                datasheet='https://example.com/lm123.pdf',
                attributes=json.dumps({'Voltage':'3.3V','Size':{'value':3,'unit':'mm'},
                    'Nested':{'price':12,'model':'kept'},'Stock':99}),
                attrition=json.dumps({'quantity':2,'unit':'count'}),fetched_at=1700000000,
                basic=1,preferred=0,assembly=1,library_type='Basic',stock=99,
                price='[{"quantity":1,"price":0.12}]',last_on_stock=1710000000,
                assembly_process='SMT')]:
            columns=','.join(row)
            db.execute(f'INSERT INTO jlc_components ({columns}) VALUES ({",".join("?" for _ in row)})',list(row.values()))
    raw=path.read_bytes()
    metadata=dict(url=SQLITE_URL,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),status=200,
        captured_at='2026-10-24T12:00:00Z',last_modified='Sat, 24 Oct 2026 05:56:46 GMT',http_verified=True)
    return path,metadata


@pytest.fixture
def snapshot(catalog):
    db=ComponentSnapshot(catalog)
    db.migrate()
    db.conn.execute('TRUNCATE component_snapshots CASCADE')
    return db


def test_bulk_roundtrip_provenance_and_redaction(snapshot,tmp_path):
    path,metadata=artifact(tmp_path)
    before=path.read_bytes()
    schema1=snapshot.conn.execute('SELECT sha256 FROM schema_versions WHERE version=1').fetchone()
    assert snapshot.migrate()=={'version':3,'changed':False}
    report=snapshot.import_sqlite(path,metadata,batch_size=1)
    assert report['processed']==report['upserted']==1
    assert report['complete'] and report['batches']==1
    assert snapshot.conn.execute('SELECT sha256 FROM schema_versions WHERE version=1').fetchone()==schema1
    row=snapshot.get('123')
    assert row['lcsc']=='C123' and row['mpn']=='LM-123'
    assert row['supplier']=='JLCPCB' and row['origin']=='community_unofficial'
    assert row['source_fetched_at'].year==2023
    assert row['source_captured_at'].year==2026
    assert row['provenance']['sha256']==metadata['sha256']
    assert row['provenance']['object_pointer']==str(path)
    assert row['provenance']['verification']=='verified_capture'
    assert row['raw_metadata']['assembly_process']=='SMT'
    assert row['attributes']['Nested']=={'model':'kept'}
    assert 'Stock' not in row['attributes']
    for key in ('stock','price','last_on_stock'):
        assert key not in row['raw_metadata']
    assert row['attrition']=={'quantity':2,'unit':'count'}
    assert row['availability']['state']=='unknown'
    assert row['manufacturability']['state']=='unverified'
    assert row['cad']['download_allowed'] is False and row['license_status']=='unknown'
    assert next(c for c in row['constraints'] if c['key']=='Size')['unit']=='mm'
    assert next(c for c in row['constraints'] if c['key']=='Voltage')['unit'] is None
    assert path.read_bytes()==before
    assert snapshot.get(row['id'])['lcsc']=='C123'
    assert snapshot.import_sqlite(path,metadata)['snapshot_id']==report['snapshot_id']
    assert snapshot.status()['component_records']==1


def test_search_exact_attributes_pagination_and_injection(snapshot,tmp_path):
    path,meta=artifact(tmp_path,[dict(lcsc=n,mfr=f'LM-{n}',manufacturer='Fixture',package='SOT-23',
        category='Power',description='Voltage regulator',attributes='{"Voltage":"3.3V"}') for n in (1,2,3)])
    snapshot.import_sqlite(path,meta,batch_size=2)
    for request in (ComponentSearch(q='regulator'),ComponentSearch(q='C2'),ComponentSearch(q='LM-2'),
            ComponentSearch(lcsc='2'),ComponentSearch(mpn='lm-2'),
            ComponentSearch(manufacturer='Fixture',package='SOT-23',category='Power',attributes={'Voltage':'3.3V'})):
        assert snapshot.search(request)['total']>0
    result=snapshot.search(ComponentSearch(q='regulator',limit=1,offset=1))
    assert result['total']==3 and len(result['items'])==1 and result['items'][0]['lcsc']=='C2'
    assert snapshot.search(ComponentSearch(offset=99))['total']==3
    assert snapshot.search(ComponentSearch(offset=99))['items']==[]
    assert snapshot.search(ComponentSearch(manufacturer="';DROP TABLE component_records;--"))['total']==0
    assert snapshot.search(ComponentSearch(q="';DROP TABLE component_records;--"))['total']==0
    assert snapshot.search(ComponentSearch(attributes={'Voltage':"' OR true --"}))['total']==0
    with pytest.raises(KeyError): snapshot.get("';DROP TABLE component_records;--")
    assert snapshot.status()['active_component_records']==3


def test_bounded_batches_and_full_snapshot_retires_missing(snapshot,tmp_path):
    path,meta=artifact(tmp_path,[dict(lcsc=n,mfr=f'X{n}') for n in range(1,6)])
    report=snapshot.import_sqlite(path,meta,batch_size=2,max_rows=3)
    assert (report['processed'],report['batches'],report['complete'])==(3,2,False)
    assert snapshot.status()['active_component_records']==3
    report=snapshot.import_sqlite(path,meta,batch_size=2)
    assert report['complete'] and report['upserted']==5 and report['batches']==3
    assert snapshot.status()['component_records']==5
    other,othermeta=artifact(tmp_path,[dict(lcsc=1,mfr='Updated')],name='new.sqlite3')
    snapshot.import_sqlite(other,othermeta,max_rows=1)
    assert snapshot.status()['active_component_records']==5
    snapshot.import_sqlite(other,othermeta)
    assert snapshot.status()['active_component_records']==1
    assert snapshot.status()['component_records']==5
    assert snapshot.get('C2')['active'] is False


def test_mpn_prefix_literal_wildcards_and_exact_ranking(snapshot,tmp_path):
    path,meta=artifact(tmp_path,[
        dict(lcsc=1,mfr='STM32F407',description='Family microcontroller'),
        dict(lcsc=2,mfr='STM32F103',description='Family microcontroller C9'),
        dict(lcsc=9,mfr='STM32',description='Exact family identifier'),
        dict(lcsc=10,mfr='ABC\\123',description='Backslash literal'),
        dict(lcsc=11,mfr='ABCX123',description='Different model'),
    ])
    snapshot.import_sqlite(path,meta)
    result=snapshot.search(ComponentSearch(q='STM32'))
    assert result['total']==3
    assert result['items'][0]['mpn']=='STM32'
    assert {row['mpn'] for row in result['items']}=={'STM32','STM32F407','STM32F103'}
    assert snapshot.search(ComponentSearch(q='stm32f407'))['items'][0]['lcsc']=='C1'
    assert snapshot.search(ComponentSearch(q='C9'))['items'][0]['lcsc']=='C9'
    for query in ('STM%', 'STM_', '%', '_', "STM32%' OR true --"):
        assert snapshot.search(ComponentSearch(q=query))['total']==0
    assert snapshot.search(ComponentSearch(q='ABC\\'))['items'][0]['mpn']=='ABC\\123'


def test_malformed_json_retained_without_opaque_offers(snapshot,tmp_path):
    path,meta=artifact(tmp_path,[dict(lcsc=1,attributes='{broken',attrition='[broken'),
        dict(lcsc=2,attributes='{"Stock": 50, broken')])
    snapshot.import_sqlite(path,meta)
    row=snapshot.get('C1')
    assert row['attributes']=={} and row['raw_metadata']['attributes']=='{broken'
    assert row['attrition']=='[broken' and row['parse_warnings']
    assert '50' not in json.dumps(snapshot.get('C2')['raw_metadata'])


def test_hash_size_and_source_validation_before_write(snapshot,tmp_path):
    path,meta=artifact(tmp_path)
    for altered in ({'sha256':'0'*64},{'bytes':1},{'url':'https://example.com/fake.sqlite3'}):
        with pytest.raises(ValueError): snapshot.import_sqlite(path,{**meta,**altered})
    assert snapshot.status()['component_snapshots']==0
    # Bare local bytes carry explicitly unverified provenance.
    snapshot.import_sqlite(path,{})
    assert snapshot.get('C123')['provenance']['verification']=='observational_unverified'
    assert snapshot.get('C123')['source_captured_at'] is None


def test_sqlite_rejects_view_virtual_table_and_writes(tmp_path):
    path,_=artifact(tmp_path)
    with _read_sqlite(path) as db:
        assert db.execute('SELECT * FROM main.jlc_components').fetchone()['lcsc']==123
        with pytest.raises(sqlite3.DatabaseError): db.execute('DELETE FROM jlc_components')
        with pytest.raises(sqlite3.DatabaseError): db.execute("SELECT load_extension('x') FROM jlc_components")
    for kind in ('view','virtual'):
        path=tmp_path/f'{kind}.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE VIEW jlc_components AS SELECT 1 AS lcsc' if kind=='view' else
                'CREATE VIRTUAL TABLE jlc_components USING fts5(lcsc)')
        with pytest.raises(ValueError,match='regular'):
            with _read_sqlite(path): pass


def test_model_bounds_and_normalization():
    for values in ({'limit':0},{'limit':101},{'offset':-1},{'bad':True}):
        with pytest.raises(ValidationError): ComponentSearch(**values)
    values=_record(dict(lcsc=1,attributes='{"size":1e999}',description='x\x00y'),'00000000-0000-0000-0000-000000000000')
    assert values[6]=='x\ufffdy'
    assert _record({'lcsc':"x' OR true"},'unused') is None
