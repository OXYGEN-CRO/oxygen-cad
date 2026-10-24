# McMaster public metadata research

`o3d-parts scrape-mcmaster urls.json` reads 1–30 explicitly selected public
McMaster family URLs from a JSON array. It identifies itself as OxygenPartsBot,
spaces requests by two seconds, and uses bounded same-host HTTPS requests.
Choose URLs actually present in the supplier's public sitemap. This is a bounded
research import, not a full McMaster mirror or a CAD download connector.

```json
["https://www.mcmaster.com/a286-super-alloy-socket-head-cap-screws",
 "https://www.mcmaster.com/idler-rotary-shafts"]
```

```sh
o3d-parts scrape-mcmaster urls.json
o3d-parts search 90035A110 --provider mcmaster-public
```

The parser extracts only literal SKU/product URL associations and SKU-level
PropertyValue observations from public JSON-LD. It preserves original headers,
fractions, strings and repeated/conflicting observations without guessing units,
material, thread sizes, OEM identities or mounting ports. Family names describe
the source group; family marketing descriptions are excluded. A family page can
contain several product groups; its URL is not a specification for every SKU.
Across selected pages the first occurrence of a SKU is kept; duplicates are
counted and all derived page evidence remains available. Repeating an import
upserts stable provider/SKU IDs. It does not establish discontinuation or complete
supplier coverage.

Only derived metadata snapshots are stored, with original response hashes,
source URLs and observation times. Prices, stock, offers, images and CAD files
are not retained. Empty pages are reported as `no_structured_products`.
An HTTP 200 response alone does not establish successful extraction.

McMaster data is proprietary. This user-requested local research import does
not grant rights to publish or redistribute a McMaster catalog or CAD models.
The provider entry explicitly records external reuse restrictions. Consult
[the supplier terms](https://www.mcmaster.com/termsandconditions/) for production
integration; do not represent this source as an open licensed library. Its
[official API](https://www.mcmaster.com/help/api) provides product/CAD retrieval
to approved customers with a client certificate and subscribed part numbers.
No customer account, certificate, login, private endpoint or CAD permission is
created by this connector.
