import json

import pytest

from o3d.catalog.mcmaster_public import as_part, extract, ingest, validate_url
from o3d.catalog.models import Search

URL = "https://www.mcmaster.com/socket-head-cap-screws"


def page(products):
    return ('<script type="application/ld+json">' + json.dumps({"@type": "ProductGroup",
        "name": "Fixture screws", "description": "Do not infer every variant is steel",
        "hasVariant": products}) + '</script>').encode()


def product(**changes):
    return {"@type": "Product", "sku": "90035A110", "url": "https://www.mcmaster.com/90035A110/",
        "offers": {"price": 7.99, "availability": "InStock"},
        "additionalProperty": [{"name": "Lg., mm", "value": "6"},
            {"name": "Threading", "value": "Fully Threaded"},
            {"name": "Price", "value": "$7.99"}], **changes}


def test_extracts_literal_sku_properties_without_offers_or_guessed_family_facts():
    rows = extract(page([product()]), URL)
    part = as_part(rows[0], URL)
    assert part.supplier_sku == "90035A110"
    assert part.product_url.endswith('/90035A110/')
    assert part.manufacturer is None and part.assets == [] and part.ports == []
    assert part.attributes['cad_status'] == 'not_retrieved'
    assert part.attributes['sku_properties'] == [
        {"name": "Lg., mm", "value": "6"}, {"name": "Threading", "value": "Fully Threaded"}]
    assert all(spec.unit is None for spec in part.specs)
    assert '7.99' not in json.dumps(rows) and 'steel' not in json.dumps(rows)


def test_conflicting_duplicate_variant_observations_are_preserved():
    altered = product(additionalProperty=[{"name": "Lg., mm", "value": "7"}])
    rows = extract(page([product(), altered]), URL)
    assert len(rows) == 1 and len(rows[0]['observations']) == 2
    assert [x['value'] for x in as_part(rows[0], URL).attributes['sku_properties'] if x['name']=='Lg., mm'] == ['6', '7']


@pytest.mark.parametrize('url', ['https://example.com/screws', 'http://www.mcmaster.com/screws',
    'https://www.mcmaster.com/orders', 'https://www.mcmaster.com/screws?private=1',
    'https://www.mcmaster.com/_/private', 'https://name:secret@www.mcmaster.com/screws'])
def test_rejects_external_private_and_query_urls(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_sku_url_mismatch_rejected():
    with pytest.raises(ValueError, match='mismatch'):
        extract(page([product(url='https://www.mcmaster.com/12345A1/')]), URL)


def test_empty_page_does_not_claim_products():
    assert extract(b'<html><body></body></html>', URL) == []


def test_real_storage_idempotence_source_redaction_and_search(catalog):
    class FixtureFetcher:
        def get(self, url, **kwargs):
            assert kwargs['allowed_hosts'] == {'www.mcmaster.com'}
            return page([product()])
    for _ in range(2):
        result = ingest(catalog, [URL], fetcher=FixtureFetcher())
        assert result['unique_skus'] == 1 and result['cad_files'] == 0
    found = catalog.search(Search(q='90035A110', provider='mcmaster-public'))
    assert found['total'] == 1 and found['items'][0]['cad_count'] == 0
    record = catalog.get(str(found['items'][0]['id']))
    evidence = catalog.objects.read(record['provenance']['object_key'])
    assert b'7.99' not in evidence and b'InStock' not in evidence
    assert json.loads(evidence)['original_response_retained'] is False
