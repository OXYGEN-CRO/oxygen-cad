"""Deterministic supplied snapshots, never a live provider quote or network test."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from o3d.catalog.vendor_cad import _record_item
from o3d.procurement import evaluate_procurement, inspect_bom

HASH = 'a' * 64
SOURCE = {'source_url': 'https://www.revrobotics.com/rev-41-1300/', 'source_sha256': HASH}


@pytest.fixture
def snapshot():
    # Use the existing catalog source-chain shape; sourcing establishes identity,
    # not the synthetic offer prices or the declared procurement classification.
    record = _record_item({'id': 'record-one', 'name': 'Extrusion', 'provider_key': 'rev',
        'external_id': 'REV-41-1300', 'source_id': 'source-one',
        'evidence_sha256': HASH, 'evidence_url': SOURCE['source_url'],
        'payload': {'record_type': 'purchased_part', 'product_url': SOURCE['source_url'],
                    'facts': {'sku': 'REV-41-1300', 'step_url': 'https://www.revrobotics.com/content/cad/REV-41-1300.step'}}})
    identity = {'provider': record['provider'], 'sku': record['sku'],
                'asset_id': record['cad_links'][0]['url'], 'source_sha256': record['source_chain']['source_sha256']}
    return {'bom': {'lines': [{'id': 'rail', 'name': 'Extrusion', 'classification': 'purchased',
                              'quantity': 12, 'identity': identity}]},
            'region': 'DE', 'as_of': '2026-10-24T12:00:00Z',
            'offers': [{'id': 'supplied-fixture', 'identity': deepcopy(identity), 'provenance': deepcopy(SOURCE),
                        'observed_at': '2026-10-24T10:00:00Z', 'expires_at': '2026-10-25T10:00:00Z',
                        'currency': 'EUR', 'regions': ['DE'], 'minimum_order_quantity': 2,
                        'quantity_breaks': [{'minimum_quantity': 1, 'unit_price': '3.10'}, {'minimum_quantity': 10, 'unit_price': '2.90'}],
                        'stock_state': 'in_stock', 'stock_quantity': 100, 'lead_time_state': 'known', 'lead_time_days': 3}]}


def state(snapshot):
    return evaluate_procurement(snapshot)['lines'][0]['state']


def test_quantity_break_exact_decimal_and_no_mutation(snapshot):
    before = deepcopy(snapshot)
    report = evaluate_procurement(snapshot)
    assert report['complete'] is True
    assert report['totals'] == [{'currency': 'EUR', 'amount': '34.80', 'scope': 'complete_supplied_bom'}]
    assert report['lines'][0]['offers'][0]['pricing_quantity'] == 12
    assert report['clock'] == 'explicit_snapshot'
    assert snapshot == before


@pytest.mark.parametrize(('field', 'value', 'expected'), [
    ('stock_quantity', 11, 'insufficient'), ('stock_quantity', None, 'unknown'),
    ('stock_state', 'out_of_stock', 'insufficient'), ('stock_state', 'backorder', 'insufficient'),
    ('stock_state', 'unknown', 'unknown'), ('stock_state', 'unsupported', 'unsupported'),
    ('minimum_order_quantity', 13, 'insufficient'), ('minimum_order_quantity', None, 'unknown'),
    ('currency', None, 'unknown'), ('regions', [], 'unknown'), ('regions', ['US'], 'unsupported'),
    ('quantity_breaks', [], 'unknown'), ('observed_at', None, 'unknown'),
    ('expires_at', None, 'unknown'), ('provenance', None, 'unknown'),
    ('expires_at', '2026-10-24T12:00:00Z', 'expired'),
    ('observed_at', '2026-10-24T13:00:00Z', 'unknown'), ('support', 'unsupported', 'unsupported'),
])
def test_offer_states(snapshot, field, value, expected):
    snapshot['offers'][0][field] = value
    if value == 'out_of_stock':
        snapshot['offers'][0]['stock_quantity'] = 0
    assert state(snapshot) == expected
    assert not evaluate_procurement(snapshot)['totals']
    assert not evaluate_procurement(snapshot)['complete']


@pytest.mark.parametrize('field', ['provider', 'sku', 'asset_id', 'source_sha256'])
def test_exact_identity_mismatch_and_unknown(snapshot, field):
    snapshot['bom']['lines'][0]['offer_id'] = 'supplied-fixture'
    snapshot['offers'][0]['identity'][field] = 'b' * 64 if field == 'source_sha256' else 'different'
    assert state(snapshot) == 'unsupported'
    snapshot['offers'][0]['identity'][field] = None
    assert state(snapshot) == 'unknown'


def test_unknown_quantity_region_classification_and_custom_partial(snapshot):
    custom = {'id': 'bracket', 'name': 'Custom bracket', 'classification': 'custom', 'quantity': 2}
    unknown = {'id': 'connector', 'name': 'Connector'}
    snapshot['bom']['lines'] += [custom, unknown]
    report = evaluate_procurement(snapshot)
    assert report['totals'][0]['scope'] == 'partial_known_lines'
    assert report['excluded_line_ids'] == ['bracket', 'connector']
    assert [line['state'] for line in report['lines']] == ['fresh_available', 'custom', 'unknown']
    assert inspect_bom(snapshot['bom'])['counts'] == {'purchased': 1, 'custom': 1, 'unknown': 1}
    snapshot['bom']['lines'][0]['quantity'] = None
    assert state(snapshot) == 'unknown'
    snapshot['bom']['lines'][0]['quantity'] = 12
    snapshot['region'] = None
    assert state(snapshot) == 'unknown'


def test_combined_demand_breaks_and_no_stock_double_count(snapshot):
    snapshot['bom']['lines'][0]['quantity'] = 6
    other = deepcopy(snapshot['bom']['lines'][0])
    other['id'] = 'second-rail'
    snapshot['bom']['lines'].append(other)
    report = evaluate_procurement(snapshot)
    assert report['totals'][0]['amount'] == '34.80'
    assert all(line['unit_price'] == '2.90' for line in report['lines'])
    snapshot['offers'][0]['stock_quantity'] = 10
    assert evaluate_procurement(snapshot)['totals'] == []
    snapshot['bom']['lines'][1]['quantity'] = None
    assert all(line['state'] == 'unknown' for line in evaluate_procurement(snapshot)['lines'])


def test_mixed_currency_never_summed_or_implicitly_compared(snapshot):
    second = deepcopy(snapshot['bom']['lines'][0])
    second['id'] = 'motor'
    second['identity']['sku'] = 'OTHER-SKU'
    offer = deepcopy(snapshot['offers'][0])
    offer.update(id='usd-offer', identity=deepcopy(second['identity']), currency='USD')
    snapshot['bom']['lines'].append(second)
    snapshot['offers'].append(offer)
    report = evaluate_procurement(snapshot)
    assert {t['currency']: t['amount'] for t in report['totals']} == {'EUR': '34.80', 'USD': '34.80'}
    snapshot['offers'][1]['identity'] = deepcopy(snapshot['offers'][0]['identity'])
    assert state(snapshot) == 'unknown'
    snapshot['bom']['lines'][0]['offer_id'] = 'supplied-fixture'
    assert state(snapshot) == 'fresh_available'


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-1', '1e9999999', '0e99999999', '1.1234567', 1.25, True])
def test_invalid_money(snapshot, value):
    snapshot['offers'][0]['quantity_breaks'][0]['unit_price'] = value
    with pytest.raises(ValidationError):
        evaluate_procurement(snapshot)


@pytest.mark.parametrize(('where', 'field', 'value'), [
    ('offer', 'observed_at', 'not-a-time'), ('offer', 'expires_at', '2026-10-25T10:00:00'),
    ('offer', 'observed_at', 1792850000), ('offer', 'stock_quantity', float('inf')),
    ('offer', 'minimum_order_quantity', True), ('offer', 'stock_quantity', -1),
    ('line', 'quantity', 1.5), ('line', 'quantity', 1_000_000_001), ('line', 'quantity', True),
    ('root', 'as_of', '2026-10-24T12:00:00'), ('root', 'as_of', True),
])
def test_invalid_bounded_inputs(snapshot, where, field, value):
    target = snapshot['offers'][0] if where == 'offer' else snapshot['bom']['lines'][0] if where == 'line' else snapshot
    target[field] = value
    with pytest.raises(ValidationError):
        evaluate_procurement(snapshot)


def test_bad_provenance_and_duplicate_ids_are_rejected(snapshot):
    snapshot['offers'][0]['provenance']['source_url'] = 'https://user:secret@example.com/quote'
    with pytest.raises(ValidationError):
        evaluate_procurement(snapshot)
    snapshot['offers'][0]['provenance'] = SOURCE
    snapshot['offers'].append(deepcopy(snapshot['offers'][0]))
    with pytest.raises(ValidationError):
        evaluate_procurement(snapshot)


def test_expiry_order_and_incoherent_lead_time(snapshot):
    snapshot['offers'][0]['expires_at'] = '2026-10-24T09:00:00Z'
    with pytest.raises(ValidationError):
        evaluate_procurement(snapshot)
    snapshot['offers'][0]['expires_at'] = '2026-10-25T09:00:00Z'
    snapshot['offers'][0]['lead_time_state'] = 'unknown'
    with pytest.raises(ValidationError):
        evaluate_procurement(snapshot)


def test_source_and_constraint_edits_change_signatures(snapshot):
    first = evaluate_procurement(snapshot)
    snapshot['bom']['lines'][0]['identity']['source_sha256'] = 'b' * 64
    second = evaluate_procurement(snapshot)
    assert second['bom_sha256'] != first['bom_sha256']
    assert second['assessment_sha256'] != first['assessment_sha256']
    assert second['complete'] is False
    snapshot['offers'][0]['identity']['source_sha256'] = 'b' * 64
    third = evaluate_procurement(snapshot)
    assert third['complete'] is True
    assert third['input_sha256'] != second['input_sha256']


@pytest.fixture
def constrained(snapshot):
    line = snapshot['bom']['lines'][0]
    line['requirements'] = [
        {'key': 'mount', 'kind': 'interface', 'value': 'M4', 'operator': 'eq'},
        {'key': 'rated_load', 'kind': 'performance', 'value': '12', 'operator': 'gte', 'unit': 'N'},
    ]
    line['hard_constraints_complete'] = True
    alternative = {'id': 'candidate', 'line_id': 'rail', 'identity': {**line['identity'], 'sku': 'CANDIDATE'},
                   'properties': [{'key': 'mount', 'kind': 'interface', 'value': 'M4', 'provenance': SOURCE},
                                  {'key': 'rated_load', 'kind': 'performance', 'value': '15', 'unit': 'N', 'provenance': SOURCE}]}
    snapshot['alternatives'] = [alternative]
    return snapshot


def alternative_state(snapshot):
    return evaluate_procurement(snapshot)['alternatives'][0]['state']


def test_substitution_requires_explicit_complete_sourced_constraints(constrained):
    assert alternative_state(constrained) == 'constraints_satisfied'
    original = evaluate_procurement(constrained)
    constrained['alternatives'][0]['properties'][1]['value'] = '10'
    assert alternative_state(constrained) == 'rejected'
    assert evaluate_procurement(constrained)['assessment_sha256'] != original['assessment_sha256']
    constrained['alternatives'][0]['properties'][1]['provenance'] = None
    assert alternative_state(constrained) == 'unknown'


@pytest.mark.parametrize('change', ['missing_property', 'missing_hard_declaration', 'missing_identity', 'units', 'nonnumeric', 'name_only'])
def test_substitution_unknown(constrained, change):
    if change == 'missing_property':
        constrained['alternatives'][0]['properties'].pop()
    elif change == 'missing_hard_declaration':
        constrained['bom']['lines'][0]['hard_constraints_complete'] = False
    elif change == 'missing_identity':
        constrained['alternatives'][0]['identity']['asset_id'] = None
    elif change == 'units':
        constrained['alternatives'][0]['properties'][1]['unit'] = 'kN'
    elif change == 'nonnumeric':
        constrained['alternatives'][0]['properties'][1]['value'] = 'NaN'
    elif change == 'name_only':
        constrained['bom']['lines'][0]['requirements'] = []
    assert alternative_state(constrained) == 'unknown'


def test_default_clock_and_empty_bom():
    report = evaluate_procurement({'bom': {'lines': []}})
    assert report['clock'] == 'current_utc'
    assert report['evidence_mode'] == 'supplied_snapshot'
    assert report['complete'] is False
    assert not report['totals']


def test_money_precision_is_independent_of_callers_decimal_context(snapshot):
    from decimal import localcontext
    snapshot['offers'][0]['quantity_breaks'] = [{'minimum_quantity': 1, 'unit_price': '2.123456'}]
    with localcontext() as context:
        context.prec = 6
        report = evaluate_procurement(snapshot)
    assert report['totals'][0]['amount'] == '25.481472'


def test_distinct_selected_offers_do_not_share_quantity_discount_or_stock(snapshot):
    first = snapshot['bom']['lines'][0]
    first.update(quantity=5, offer_id='offer-a')
    second = deepcopy(first)
    second.update(id='second-rail', offer_id='offer-b')
    snapshot['bom']['lines'].append(second)
    snapshot['offers'][0].update(
        id='offer-a', currency='USD', stock_quantity=100,
        quantity_breaks=[{'minimum_quantity': 1, 'unit_price': '10.00'},
                         {'minimum_quantity': 10, 'unit_price': '1.00'}])
    other = deepcopy(snapshot['offers'][0])
    other['id'] = 'offer-b'
    snapshot['offers'].append(other)
    report = evaluate_procurement(snapshot)
    assert report['complete'] is True
    assert report['totals'] == [{'currency': 'USD', 'amount': '100.00', 'scope': 'complete_supplied_bom'}]
    assert [line['unit_price'] for line in report['lines']] == ['10.00', '10.00']
    for line in report['lines']:
        selected = next(offer for offer in line['offers'] if offer['offer_id'] == line['selected_offer_id'])
        assert selected['pricing_quantity'] == 5
    # Separate observations each cover their own five-item order, even when
    # neither observation can independently cover all ten BOM items.
    for offer in snapshot['offers']:
        offer['stock_quantity'] = 5
    assert evaluate_procurement(snapshot)['complete'] is True


def test_distinct_selected_offers_do_not_share_minimum_order_quantity(snapshot):
    first = snapshot['bom']['lines'][0]
    first.update(quantity=5, offer_id='offer-a')
    second = deepcopy(first)
    second.update(id='second-rail', offer_id='offer-b')
    snapshot['bom']['lines'].append(second)
    snapshot['offers'][0].update(id='offer-a', minimum_order_quantity=10)
    other = deepcopy(snapshot['offers'][0])
    other['id'] = 'offer-b'
    snapshot['offers'].append(other)
    report = evaluate_procurement(snapshot)
    assert report['complete'] is False
    assert report['totals'] == []
    assert [line['state'] for line in report['lines']] == ['insufficient', 'insufficient']


def test_same_selected_offer_can_share_quantity_discount(snapshot):
    first = snapshot['bom']['lines'][0]
    first.update(quantity=5, offer_id='supplied-fixture')
    second = deepcopy(first)
    second['id'] = 'second-rail'
    snapshot['bom']['lines'].append(second)
    report = evaluate_procurement(snapshot)
    assert report['complete'] is True
    assert report['totals'][0]['amount'] == '29.00'
    assert all(line['unit_price'] == '2.90' for line in report['lines'])


def test_automatic_and_explicit_same_offer_merge_without_double_claiming_stock(snapshot):
    first = snapshot['bom']['lines'][0]
    first.update(quantity=5, offer_id='supplied-fixture')
    second = deepcopy(first)
    second.update(id='second-rail', offer_id=None)
    snapshot['bom']['lines'].append(second)
    report = evaluate_procurement(snapshot)
    assert report['complete'] is True
    assert report['totals'][0]['amount'] == '29.00'
    assert all(line['offers'][0]['pricing_quantity'] == 10 for line in report['lines'])
    snapshot['offers'][0]['stock_quantity'] = 5
    report = evaluate_procurement(snapshot)
    assert report['complete'] is False
    assert report['excluded_line_ids'] == ['second-rail']
    assert report['totals'][0]['amount'] == '15.50'


def test_automatic_selection_can_use_separate_offer_stock(snapshot):
    first = snapshot['bom']['lines'][0]
    first.update(quantity=5, offer_id='supplied-fixture')
    second = deepcopy(first)
    second.update(id='second-rail', offer_id=None)
    snapshot['bom']['lines'].append(second)
    snapshot['offers'][0]['stock_quantity'] = 5
    other = deepcopy(snapshot['offers'][0])
    other['id'] = 'second-offer'
    snapshot['offers'].append(other)
    report = evaluate_procurement(snapshot)
    assert report['complete'] is True
    assert [line['selected_offer_id'] for line in report['lines']] == ['supplied-fixture', 'second-offer']
    assert report['totals'][0]['amount'] == '31.00'


def test_decimal_bounds_do_not_round_under_callers_low_precision():
    from decimal import localcontext
    from o3d.procurement_models import bounded_decimal
    with localcontext() as context:
        context.prec = 6
        with pytest.raises(ValueError):
            bounded_decimal("1000000000000.000001")
        assert bounded_decimal("1000000000000") == 1000000000000
