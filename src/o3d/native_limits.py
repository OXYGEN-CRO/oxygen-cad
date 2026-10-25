"""Bounded canonical-document and JSON-lines transport budgets.

Documents include real imported mesh geometry. Keep the request budget larger
than the saved-document budget so editing context and protocol fields fit too.
The Rust adapter uses the same 34 MiB request limit; no upstream kernel limit
or filesystem access policy is changed here.
"""

MAX_NATIVE_DOCUMENT_BYTES = 32 * 1024 * 1024
MAX_NATIVE_REQUEST_BYTES = 34 * 1024 * 1024
