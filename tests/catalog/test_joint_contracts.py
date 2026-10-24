import json
from o3d.catalog.models import Part
from o3d.joints import library, contract_from_catalog
from o3d.cli import app
from o3d.store import Store
from o3d.model import Project
from typer.testing import CliRunner
from fastapi.testclient import TestClient
from o3d.catalog.api import create_app

def test_existing_catalog_roundtrip_and_native_import(catalog, tmp_path, monkeypatch):
    contract = library()[0]
    document = catalog.document('test','https://example.com/contract',b'{"fixture":true}')
    part = Part(external_id=contract.id,name=contract.mpn,manufacturer=contract.manufacturer,mpn=contract.mpn,
                attributes={'engineering_contract':contract.model_dump(mode='json')})
    identifier = catalog.upsert('test',part,document)
    row=catalog.get(str(identifier))
    recovered=contract_from_catalog(row)
    assert recovered.mpn==contract.mpn and recovered.catalog_part_id==str(identifier)
    with TestClient(create_app(catalog.conn.info.dsn)) as client:
        r=client.get(f'/v1/parts/{identifier}/engineering')
        assert r.status_code==200 and r.json()['mpn']==contract.mpn
    monkeypatch.setenv('O3D_CATALOG_DATABASE_URL',catalog.conn.info.dsn)
    store=Store(tmp_path/'project');store.init(Project())
    result=CliRunner().invoke(app,['--project',str(store.root),'joint','import-catalog',str(identifier),'--expected-revision','1'])
    assert result.exit_code==0,result.output
    assert Store(store.root).load().engineering_brief.components[0].catalog_part_id==str(identifier)
