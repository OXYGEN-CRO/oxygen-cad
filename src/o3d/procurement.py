"""Offline BOM/offer evaluation. No network, storage, conversion or order placement."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, localcontext

from .catalog.offer_policy import contains_offer_values
from .procurement_models import (
    ProcurementBOM, ProcurementEvaluationRequest, bounded_decimal,
)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _money_add(left, right):
    with localcontext() as context:
        context.prec = 50
        return left + right


def _money_multiply(price, quantity):
    with localcontext() as context:
        context.prec = 50
        return price * quantity


def _complete(identity):
    return identity is not None and all(isinstance(v, str) and bool(v.strip()) for v in identity.model_dump().values())


def _provenance(value):
    return value is not None and bool(value.source_url and value.source_sha256)


def _identity_key(identity):
    return json.dumps(identity.model_dump(), sort_keys=True) if _complete(identity) else None


def _metadata_check(bom):
    # Reuse catalog's storage boundary. An explicit offer selection is just a
    # transient reference; offer observations must never enter BOM metadata.
    for line in bom.lines:
        if contains_offer_values(line.model_dump(mode='json', exclude={'offer_id'})):
            raise ValueError('BOM contains offer values; supply observations separately from component metadata')


def inspect_bom(data: dict | ProcurementBOM) -> dict:
    """Validate and inspect a BOM (not an engineering Project); preserve classification."""
    bom = data if isinstance(data, ProcurementBOM) else ProcurementBOM.model_validate(data)
    _metadata_check(bom)
    normalized = bom.model_dump(mode='json')
    return {'bom': normalized, 'bom_sha256': _hash(normalized),
            'counts': {kind: sum(line.classification == kind for line in bom.lines)
                       for kind in ('purchased', 'custom', 'unknown')},
            'lines': [{'id': line.id, 'classification': line.classification,
                       'identity_state': 'exact_source_linked' if _complete(line.identity) else 'unknown',
                       'quantity_state': 'declared' if line.quantity is not None else 'unknown'} for line in bom.lines]}


def _offer(line, offer, quantity, request, now):
    result = {'offer_id': offer.id, 'state': 'unknown', 'reasons': [],
              'lead_time_state': offer.lead_time_state, 'lead_time_days': offer.lead_time_days,
              'stock_state': offer.stock_state, 'stock_quantity': offer.stock_quantity,
              'observed_at': offer.observed_at.isoformat() if offer.observed_at else None,
              'expires_at': offer.expires_at.isoformat() if offer.expires_at else None,
              'provenance': offer.provenance.model_dump(mode='json') if offer.provenance else None,
              'identity': offer.identity.model_dump(mode='json') if offer.identity else None,
              'offer_sha256': _hash(offer.model_dump(mode='json'))}
    reasons = result['reasons']
    def state(value, reason):
        result['state'] = value
        reasons.append(reason)
        return result
    if offer.support == 'unsupported' or offer.stock_state == 'unsupported':
        return state('unsupported', 'Offer source or stock observation is unsupported')
    if line.identity and offer.identity:
        if any(a is not None and b is not None and a != b for a, b in zip(line.identity.model_dump().values(), offer.identity.model_dump().values())):
            return state('unsupported', 'Provider/SKU/asset/source hash differs from the BOM identity')
    if not _complete(line.identity) or not _complete(offer.identity):
        return state('unknown', 'Exact provider, SKU, asset and source hash are required')
    if offer.expires_at and offer.expires_at <= now:
        return state('expired', 'Offer has expired at the evaluation clock')
    if not offer.observed_at or not offer.expires_at or not _provenance(offer.provenance):
        return state('unknown', 'Observation time, expiry and source provenance are required')
    if offer.observed_at > now:
        return state('unknown', 'Observation is later than the evaluation clock')
    if not request.region or not offer.regions:
        return state('unknown', 'Destination and explicit offer regions are required')
    if request.region not in offer.regions:
        return state('unsupported', 'Offer does not support the requested region')
    if quantity is None or line.quantity is None:
        return state('unknown', 'Requested quantity is unknown')
    result['pricing_quantity'] = quantity
    if offer.minimum_order_quantity is None:
        return state('unknown', 'Minimum order quantity is unknown')
    if quantity < offer.minimum_order_quantity:
        return state('insufficient', 'Requested quantity is below the minimum order quantity; no automatic overbuy')
    if offer.stock_state in ('out_of_stock', 'backorder'):
        return state('insufficient', 'Stock is unavailable or backordered; no availability inferred from lead time')
    if offer.stock_state != 'in_stock' or offer.stock_quantity is None:
        return state('unknown', 'Stock state or available quantity is unknown')
    if offer.stock_quantity < quantity:
        return state('insufficient', 'Observed stock is below combined demand for this offer and identity')
    eligible = [p for p in offer.quantity_breaks if p.minimum_quantity <= quantity]
    if not offer.currency or not eligible:
        return state('unknown', 'Currency or applicable quantity price is unknown')
    price = max(eligible, key=lambda p: p.minimum_quantity)
    result.update(currency=offer.currency, unit_price=format(bounded_decimal(price.unit_price), 'f'),
                  extended_price=format(_money_multiply(bounded_decimal(price.unit_price), line.quantity), 'f'),
                  quantity_break=price.minimum_quantity)
    return state('fresh_available', 'Fresh supplied snapshot covers declared quantity and region')


def _alternative(line, alternative):
    result = {'id': alternative.id, 'line_id': alternative.line_id, 'state': 'unknown', 'reasons': [],
              'identity': alternative.identity.model_dump(mode='json') if alternative.identity else None,
              'source_sha256s': sorted({p.provenance.source_sha256 for p in alternative.properties if _provenance(p.provenance)}),
              'alternative_sha256': _hash(alternative.model_dump(mode='json'))}
    reasons = result['reasons']
    if line is None:
        reasons.append('BOM line does not exist')
        return result
    missing, mismatches = [], []
    properties = {(p.kind, p.key): p for p in alternative.properties}
    hard = [r for r in line.requirements if r.hard]
    for requirement in hard:
        prop = properties.get((requirement.kind, requirement.key))
        if prop is None or not _provenance(prop.provenance):
            missing.append(requirement.key)
            continue
        if prop.unit != requirement.unit:
            missing.append(requirement.key + ': units differ; conversion unsupported')
            continue
        if requirement.operator == 'eq':
            passed = prop.value == requirement.value
        else:
            try:
                actual, expected = bounded_decimal(prop.value), bounded_decimal(requirement.value)
            except ValueError:
                missing.append(requirement.key + ': numeric evidence is invalid')
                continue
            passed = actual >= expected if requirement.operator == 'gte' else actual <= expected
        if not passed:
            mismatches.append(requirement.key)
    if mismatches:
        result['state'] = 'rejected'
        reasons.append('Sourced hard constraint failed: ' + ', '.join(mismatches))
    elif line.classification != 'purchased' or not _complete(line.identity) or not _complete(alternative.identity):
        reasons.append('Purchased line and exact source-linked identities are required')
    elif not hard or not line.hard_constraints_complete or {r.kind for r in hard} != {'interface', 'performance'}:
        reasons.append('Declare complete hard interface and performance constraints before assessing substitution')
    elif missing:
        reasons.append('Missing hard-constraint evidence: ' + ', '.join(missing))
    else:
        result['state'] = 'constraints_satisfied'
        reasons.append('Only declared hard constraints are satisfied; physical fit, qualification and supply remain unverified')
    return result


def evaluate_procurement(data: dict | ProcurementEvaluationRequest) -> dict:
    """Evaluate a supplied snapshot. Defaults to current UTC; explicit clock is disclosed."""
    request = data if isinstance(data, ProcurementEvaluationRequest) else ProcurementEvaluationRequest.model_validate(data)
    inspection = inspect_bom(request.bom)
    now = request.as_of or datetime.now(timezone.utc)
    # Each observation ID is one supplied offer/order scope, including its
    # currency and declared destination regions. Different observations never
    # establish shared MOQ, volume tiers or inventory merely by naming one SKU.
    def add_quantity(left, right):
        return None if left is None or right is None else left + right

    explicit_demand, automatic_demand, automatic_lines = {}, {}, {}
    for line in request.bom.lines:
        key = _identity_key(line.identity)
        if line.classification != 'purchased' or not key:
            continue
        if line.offer_id is not None:
            scope = (key, line.offer_id)
            explicit_demand[scope] = add_quantity(explicit_demand.get(scope, 0), line.quantity)
        else:
            automatic_demand[key] = add_quantity(automatic_demand.get(key, 0), line.quantity)
            automatic_lines.setdefault(key, line)

    # Resolve one automatic offer per exact-identity cohort. A candidate may
    # combine that cohort only with demand explicitly selecting the SAME offer.
    # Recompute all final amounts below after this assignment so an unrelated
    # explicit offer can never inherit the automatic cohort's quantity break.
    automatic_offers = {}
    for key, line in automatic_lines.items():
        assessed = [_offer(line, offer, add_quantity(automatic_demand[key], explicit_demand.get((key, offer.id), 0)), request, now)
                    for offer in request.offers
                    if offer.identity and line.identity.provider == offer.identity.provider and line.identity.sku == offer.identity.sku]
        eligible = [offer for offer in assessed if offer['state'] == 'fresh_available']
        if eligible and len({offer['currency'] for offer in eligible}) == 1:
            automatic_offers[key] = min(eligible, key=lambda offer: (Decimal(offer['unit_price']), offer['offer_id']))['offer_id']
    demand = dict(explicit_demand)
    for key, offer_id in automatic_offers.items():
        scope = (key, offer_id)
        demand[scope] = add_quantity(demand.get(scope, 0), automatic_demand[key])
    lines, totals = [], {}
    for line in request.bom.lines:
        item = {'id': line.id, 'name': line.name, 'classification': line.classification,
                'quantity': line.quantity, 'identity': line.identity.model_dump(mode='json') if line.identity else None,
                'requirements': [r.model_dump(mode='json') for r in line.requirements],
                'state': 'unknown', 'reasons': [], 'offers': [], 'selected_offer_id': None}
        if line.classification != 'purchased':
            item['state'] = 'custom' if line.classification == 'custom' else 'unknown'
            item['reasons'] = ['Custom manufacturing cost is not quoted' if line.classification == 'custom' else 'Procurement classification is unknown']
        else:
            # Include exact/partial matches, plus an explicitly selected mismatched
            # offer so a stale or wrong SKU never disappears without explanation.
            candidates = [o for o in request.offers if o.id == line.offer_id or
                          (line.identity and o.identity and line.identity.provider == o.identity.provider and line.identity.sku == o.identity.sku)]
            key = _identity_key(line.identity)
            selected_id = line.offer_id if line.offer_id is not None else automatic_offers.get(key)
            def offer_quantity(offer):
                if line.offer_id is None and selected_id is None:
                    # Unresolved automatic selection retains its prospective
                    # demand for honest unknown/mixed-currency diagnostics.
                    return add_quantity(automatic_demand.get(key), explicit_demand.get((key, offer.id), 0))
                return demand.get((key, offer.id), line.quantity)
            item['offers'] = [_offer(line, o, offer_quantity(o), request, now) for o in candidates]
            available = [o for o in item['offers'] if o['state'] == 'fresh_available' and (selected_id is None or o['offer_id'] == selected_id)]
            if available and len({o['currency'] for o in available}) == 1:
                selected = min(available, key=lambda o: (Decimal(o['unit_price']), o['offer_id']))
                item.update(state='fresh_available', selected_offer_id=selected['offer_id'],
                            currency=selected['currency'], unit_price=selected['unit_price'], extended_price=selected['extended_price'])
                totals[selected['currency']] = _money_add(totals.get(selected['currency'], Decimal(0)), Decimal(selected['extended_price']))
            elif available:
                item['reasons'] = ['Multiple currencies available; explicitly select offer_id; no currency conversion']
            elif line.offer_id and not any(o['offer_id'] == line.offer_id for o in item['offers']):
                item['reasons'] = ['Selected offer is absent from the supplied snapshot']
            else:
                relevant = [o for o in item['offers'] if not line.offer_id or o['offer_id'] == line.offer_id]
                states = {o['state'] for o in relevant}
                item['state'] = next(iter(states)) if len(states) == 1 else 'unknown'
                item['reasons'] = sorted({reason for o in relevant for reason in o['reasons']}) or ['No supplied offer observation']
        lines.append(item)
    by_id = {line.id: line for line in request.bom.lines}
    complete = bool(lines) and all(line['state'] == 'fresh_available' for line in lines)
    normalized = request.model_dump(mode='json')
    report = {'bom_sha256': inspection['bom_sha256'], 'input_sha256': _hash(normalized),
              'evaluated_at': now.isoformat(), 'clock': 'explicit_snapshot' if request.as_of else 'current_utc',
              'evidence_mode': 'supplied_snapshot', 'region': request.region, 'lines': lines,
              'totals': [{'currency': currency, 'amount': format(amount, 'f'),
                          'scope': 'complete_supplied_bom' if complete else 'partial_known_lines'} for currency, amount in sorted(totals.items())],
              'complete': complete, 'excluded_line_ids': [line['id'] for line in lines if line['state'] != 'fresh_available'],
              'alternatives': [_alternative(by_id.get(a.line_id), a) for a in request.alternatives],
              'limitations': ['Supplied observations only; no live quote or reservation',
                              'No currency conversion; tax, shipping and custom manufacturing costs excluded',
                              'Matching names are not substitution evidence; qualification and physical fit unverified',
                              'Offer observations are transient runtime data; provider storage and reuse policy still applies']}
    report['assessment_sha256'] = _hash(report)
    return report
