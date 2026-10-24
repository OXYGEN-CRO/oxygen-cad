"""Shared process cancellation contract (threading.Event or callback)."""


def cancelled(cancel):
    if cancel is None:
        return False
    return bool(cancel.is_set() if hasattr(cancel, "is_set") else cancel())
