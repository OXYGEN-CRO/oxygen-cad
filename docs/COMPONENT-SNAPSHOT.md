# Public community component snapshot

`ComponentSnapshot` imports the public CDFER SQLite artifact at
`https://cdfer.github.io/jlcpcb-parts-database/jlcpcb-components.sqlite3`.
It is an unofficial community metadata source, distinct from JLCPCB's primary
API. It does not bypass an API application or account approval and makes no
authenticated supplier requests. The importer itself performs no network I/O.

```python
from o3d.catalog.db import Catalog
from o3d.catalog.component_snapshot import ComponentSearch, ComponentSnapshot

with Catalog() as catalog:
    components = ComponentSnapshot(catalog)
    components.migrate()
    report = components.import_sqlite(
        '/private/captures/jlcpcb-components.sqlite3',
        metadata={
            'url': 'https://cdfer.github.io/jlcpcb-parts-database/jlcpcb-components.sqlite3',
            'sha256': '<SHA-256 of exact downloaded artifact>',
            'bytes': 389853184,
            'status': 200,
            'captured_at': '2026-10-24T12:00:00Z',
            'last_modified': 'Sat, 24 Oct 2026 05:56:46 GMT',
        },
        batch_size=2000,
    )
    page = components.search(ComponentSearch(q='regulator', package='SOT-23'))
    part = components.get('C123')
    state = components.status()
```

When metadata is omitted, a sibling `jlcpcb-components.meta.json` is read if
present. SHA-256 and declared byte count are validated once before import.
Verified capture provenance requires the fixed URL, matching hash, HTTP 200,
and an original timezone-aware capture timestamp. Local artifacts without that
evidence remain `observational_unverified`. Neither state verifies component
facts or current availability. Keep artifacts and sidecars outside the repository.

Migration 3 is additive and checksum protected using the existing migration
advisory lock. It leaves migrations 1 and 2 untouched. Stable component UUIDs
derive from `community-cdfer` and normalized LCSC `C` identifiers. Snapshot rows
hold one URL, hash, size, capture time, HTTP Last-Modified value and local artifact
pointer; every component references its snapshot rather than duplicating the file.
The local pointer is an internal provenance reference, not a downloadable asset.

SQLite opens immutable and read-only with trusted schema and extension loading
disabled. Only a regular `jlc_components` table can be read, through a constant
SELECT with an authorizer that denies functions and other tables. Embedded SQL,
views and virtual tables are not executed. Unknown source columns are retained
after the shared recursive offer policy removes stock, prices, offers and lead
times. JSON attributes and attrition remain inspectable; malformed JSON is
preserved with warnings, except opaque strings concealing offer fields, for
which only an audit hash is retained. The private source artifact preserves the
original capture. No offers become active catalog metadata.

The importer uses COPY into a temporary staging table followed by one upsert per
batch, with a transaction and 120-second statement timeout per batch. It never
disables the connection timeout globally. Only one snapshot import can run at a
time. `max_rows` bounds traversal for a pilot and marks it partial. Completed
batches survive interruption and can be replayed idempotently. A full traversal
without skipped identifiers retires missing components from search; previous
records remain retrievable by identifier for audit. A partial import never
retires missing components. Do not mutate an artifact during import.

Search supports exact LCSC/MPN, manufacturer/package/category facets, PostgreSQL
full-text queries, exact JSON attribute containment, and bounded pagination.
JSON attributes preserve source values and units; the importer never infers a
unit, normalizes an electrical value, or invents numeric manufacturing limits.
Source `fetched_at` remains separate from snapshot capture and deployment time.
An artifact updated today does not establish that every component was fetched
today. Missing timestamps and source fields remain unknown.

All responses label the supplier `JLCPCB`, the origin `community_unofficial`,
availability `unknown`, and manufacturability `unverified`. Source library type,
basic/preferred flags, assembly information, attrition and datasheet references
are metadata, not present-day assembly guarantees. Datasheets are links only.
The upstream project's code license does not establish a dataset or CAD license:
`license_status` stays `unknown`; this adapter fetches no CAD or datasheet files
and makes no CAD download permission claim.
