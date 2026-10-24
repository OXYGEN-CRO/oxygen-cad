from o3d.catalog.offer_policy import contains_offer_values, strip_offer_fields


def test_nested_camelcase_and_top_level_offers_are_removed():
    source = {"price": 12, "pricing": {"tiers": [1, 2]}, "facts": {"unitPrice": 5, "quantityAvailable": 7,
        "currentStock": 4, "voltage": 12, "dimensions": {"x": 8}}, "name": "Motor"}
    assert contains_offer_values(source)
    metadata, removed = strip_offer_fields(source)
    assert metadata == {"facts": {"voltage": 12, "dimensions": {"x": 8}}, "name": "Motor"}
    assert set(removed) == {"price", "pricing", "facts.unitPrice", "facts.quantityAvailable", "facts.currentStock"}
    assert not contains_offer_values(metadata)


def test_audit_unknowns_and_static_quantity_limits_remain_metadata():
    value = {"availability": {"state": "unknown", "reason": "No live request"}, "stock_unknown": True,
        "offer_fields_redacted": ["price", "stock"], "min_quantity": 1, "max_quantity": 100,
        "quantity": 2, "supported_formats": ["step"]}
    assert not contains_offer_values(value)
    assert strip_offer_fields(value) == (value, [])


def test_offer_constraint_values_are_removed_as_whole_entries():
    value = {"constraints": [{"key": "quantityAvailable", "value": 12, "unit": "count"},
        {"key": "max_quantity", "value": 100, "unit": "count"}]}
    assert contains_offer_values(value)
    result, paths = strip_offer_fields(value)
    assert result["constraints"] == [{"key": "max_quantity", "value": 100, "unit": "count"}]
    assert paths == ["constraints[0]"]


def test_purchasability_lead_times_and_monetary_notes_are_not_metadata():
    value = {"facts": {"purchasable": True, "lead_time_quote": "52 weeks", "lead_pitch": 2.54},
        "notes": ["Homepage advertises $369", "Source mounting dimensions are unknown"]}
    clean, removed = strip_offer_fields(value)
    assert clean == {"facts": {"lead_pitch": 2.54}, "notes": ["Source mounting dimensions are unknown"]}
    assert set(removed) == {"facts.purchasable", "facts.lead_time_quote", "notes[0]"}
    assert not contains_offer_values(clean)
