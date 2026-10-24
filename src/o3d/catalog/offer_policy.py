"""Keep ephemeral purchase offers out of persisted component metadata."""
from __future__ import annotations

import re

_MONEY = re.compile(r"(?:[$€£]\s*\d|\b(?:USD|EUR|GBP|CNY)\s*\d|\d(?:[\d,.]*)\s*(?:USD|EUR|GBP|CNY)\b)", re.I)


def _key(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def is_offer_field(key: str, value=None) -> bool:
    key = _key(str(key))
    # Audit paths and explicit unknown states contain no observed offer values.
    if key.endswith("_unknown") and value is True:
        return False
    if key.endswith(("_fields_redacted", "_fields_removed", "_fields_excluded")) and isinstance(value, list):
        return False
    if key in {"availability", "stock_status"}:
        if value is None or (isinstance(value, str) and value.lower() in {"unknown", "unverified", "not_checked"}):
            return False
        if isinstance(value, dict) and value.get("state") == "unknown" and set(value) <= {"state", "reason"}:
            return False
    tokens = set(key.split("_"))
    return bool(tokens & {"price", "prices", "pricing", "stock", "inventory", "availability", "offer", "offers", "purchasable"}) or {"lead", "time"} <= tokens or key in {
        "quantityavailable", "quantity_available", "available_quantity", "is_in_stock", "is_purchasable", "backorders"}


def contains_offer_values(value) -> bool:
    if isinstance(value, dict):
        if isinstance(value.get("key"), str) and "value" in value and is_offer_field(value["key"], value["value"]):
            return True
        return any(is_offer_field(key, item) or contains_offer_values(item) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_offer_values(item) for item in value)
    if isinstance(value, str):
        return bool(_MONEY.search(value))
    return False


def strip_offer_fields(value, path: str = ""):
    """Return (metadata, removed JSON paths); never return excluded offer values."""
    removed = []
    if isinstance(value, dict):
        if isinstance(value.get("key"), str) and "value" in value and is_offer_field(value["key"], value["value"]):
            return None, [path or "value"]
        result = {}
        for key, item in value.items():
            location = f"{path}.{key}" if path else str(key)
            if is_offer_field(key, item):
                removed.append(location)
            else:
                clean, nested = strip_offer_fields(item, location)
                if clean is not None or not nested:
                    result[key] = clean
                removed.extend(nested)
        return result, removed
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            clean, nested = strip_offer_fields(item, f"{path}[{index}]")
            if clean is not None or not nested:
                result.append(clean)
            removed.extend(nested)
        return result, removed
    if isinstance(value, str) and _MONEY.search(value):
        return None, [path or "value"]
    return value, removed
