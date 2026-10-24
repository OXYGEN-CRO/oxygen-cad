# JLCPCB read-only Components API adapter

`o3d.catalog.jlcpcb.JLCPCBClient` reads component details by exact JLCPCB/LCSC
`C`-code and retrieves single library or feed pages. It returns transient data.
It does not persist stock, prices,
datasheets or other restricted API data, and has no quote, upload or order API.
The public catalog capabilities are available separately without JLCAPI keys.

## Access and validation status

As of 2026-10-24, credentials have been supplied privately, but **successful
authenticated Components API access remains unverified**. The official
[JLCAPI portal](https://api.jlcpcb.com/) advertises Components, PCB, stencil and
3D printing APIs and instructs users to apply for access and create an app.
The [official documentation entry](https://api.jlcpcb.com/docs/start) exposed no
endpoint or authentication contract to this unauthenticated session. Enrollment,
permission approval and confirmation against the account's vendor documentation are still
required before relying on live results.

The implemented protocol comes from the primary source code of an independent
community integration, pinned at
[`mixelpixx/KiCAD-MCP-Server` cbb59a6203997f5e916be7e0480d63fe5519134a](https://github.com/mixelpixx/KiCAD-MCP-Server/blob/cbb59a6203997f5e916be7e0480d63fe5519134a/python/commands/jlcpcb.py),
methods `_build_signature_string`, `_sign`, `_get_auth_header`, `_post_signed`
and `get_component_detail`. Its
[integration guide](https://github.com/mixelpixx/KiCAD-MCP-Server/blob/cbb59a6203997f5e916be7e0480d63fe5519134a/docs/JLCPCB_INTEGRATION.md)
describes the same detail endpoint. This evidence is **community sourced and
independently unverified against approved vendor docs**, not an official SDK.

Two further primary community implementations agree on the host, canonical
signing bytes and detail endpoint:
[`Eyalm321/jlcpcb-mcp` 2c54c6c10fdd5e446887285a581da47e31a80677](https://github.com/Eyalm321/jlcpcb-mcp/blob/2c54c6c10fdd5e446887285a581da47e31a80677/src/official-client.ts)
and [`i2cjak/jlcpcb_api` 9d99e01a7a3b7fbd14a897ba4b1402eab8956626](https://github.com/i2cjak/jlcpcb_api/blob/9d99e01a7a3b7fbd14a897ba4b1402eab8956626/src/jlcpcb_api/auth.py).
The catalog pagination and feed paths are recorded in their
[tool definitions](https://github.com/Eyalm321/jlcpcb-mcp/blob/2c54c6c10fdd5e446887285a581da47e31a80677/src/tools/official.ts)
and [request models](https://github.com/i2cjak/jlcpcb_api/blob/9d99e01a7a3b7fbd14a897ba4b1402eab8956626/src/jlcpcb_api/models.py).

An unauthenticated read-only POST for `C25804` reached this endpoint on
2026-10-24 and returned HTTP 400 with JSON content type. No credentials were used;
that refusal establishes neither authentication compatibility nor data access.
A subsequent signed detail request for the same code returned HTTP 403 and the
provider JSON API code 403 with the message
`API insufficient permissions, access denied`. It returned no component data.
The [i2cjak integration notes](https://github.com/i2cjak/jlcpcb_api/blob/9d99e01a7a3b7fbd14a897ba4b1402eab8956626/README.md)
document that exact error as a missing API scope and instruct enabling the
required permissions in JLC's console. This is community evidence for the likely
cause; it does not independently prove credential validity or vendor approval.
The offline tests include this observed error, synthetic successful fixtures and
an independently computed signing vector. No successful authenticated integration
is claimed.

## Configuration and use

Supply these environment variables through your process or secret manager:

- `JLCPCB_APP_ID`
- `JLCPCB_ACCESS_KEY` (alias: `JLCPCB_API_KEY`)
- `JLCPCB_SECRET_KEY` (alias: `JLCPCB_API_SECRET`)

Primary names take precedence over aliases. This adapter never loads `.env`
files. Never commit credentials or response offers.

```python
from o3d.catalog.jlcpcb import JLCPCBClient

with JLCPCBClient.from_env() as client:
    status = client.configuration_status()  # Safe to show; no credential values.
    if status["configured"]:
        details = client.get_component_details(["C25804"])
```

`configuration_status()` reports `configured`, `missing_credentials`, `endpoint`,
`docs_url`, `read_endpoints`, `protocol_evidence`, `last_request` and
`real_authenticated_verified`. Configured
credentials alone do not imply approved access. The last field starts false and
becomes true only after this instance completes a real network request with HTTP
success, integer API code 200 and a valid response for that method. Injected test transport
responses never set it. It is an in-memory observation, not persisted proof of
continuing access or a vendor certification. A later denial never erases that
historical successful-request observation. `last_request` records only endpoint,
numeric HTTP/API codes, outcome, returned-item count and whether the transport
was real; it contains no credentials or response bodies. A failed request records
zero returned items, even when the provider includes an error payload.

Missing credentials raise `JLCPCBConfigurationError` before any request.
`JLCPCBError` covers HTTP, transport, API, JSON and response shape failures.
`JLCPCBPermissionError` is its specific subclass for provider JSON API code 403;
its `http_status` and `api_code` attributes preserve sanitized diagnostic codes.
Errors omit response bodies and auth headers. Empty input returns an empty list
without a request. Provider omissions remain omissions; they are not fabricated
zero stock. Input accepts a list or tuple of at most 1000 exact `C`-codes, each
`C` followed by 1–12 digits; larger batches must be scheduled explicitly by the
caller. Successful raw records are returned without inferred units or currency.

`get_component_library_page(current_page=1, page_size=30)` reads one page from
`getComponentLibraryList`, with JSON `currentPage` and `pageSize` (1–100).
`get_component_feed(last_key=None)` reads one provider-sized batch from
`getComponentInfos`, sending `{}` initially or `{"lastKey":"…"}` to continue.
The feed has no documented page-size argument; it retains the 10 MB response
limit. Cursors are at most 2048 characters without controls. Neither method
follows another page automatically or persists its data. Both return the
provider's raw `data` object/list because a successful live response shape is
still unverified; callers must validate that shape before importing records.

## Wire contract and boundaries

The fixed HTTPS host is `open.jlcpcb.com`; only these POST paths are allowed:

- `/overseas/openapi/component/getComponentDetailByCode`
- `/overseas/openapi/component/getComponentLibraryList`
- `/overseas/openapi/component/getComponentInfos`

No configurable destination, redirects, proxy environment, retries, or extra
endpoints are used. Requests time out after 30 seconds per network operation;
decoded response content is bounded at 10 MB.

The body is compact UTF-8 JSON, for example `{"componentCodes":["C25804"]}`.
It is serialized once; the exact bytes signed are the bytes transmitted. The
canonical signing bytes are:

```text
POST\n
/overseas/openapi/component/getComponentDetailByCode\n
<Unix timestamp in seconds>\n
<32-character nonce>\n
<exact JSON body bytes>\n
```

HMAC-SHA256 uses the UTF-8 secret key and produces a standard Base64 signature.
The `Authorization` value is
`JOP appid="…",accesskey="…",nonce="…",timestamp="…",signature="…"`.
This follows the pinned implementation; it must still be checked against the
documentation supplied to the approved JLCAPI app. HTTP success alone is
insufficient: the response must also contain integer `code: 200`. A detail
response must contain a list (directly under `data`, or under
`data.componentDetailResponseVOList`) containing only requested codes.

Run the offline suite with
`PYTHONPATH=src pytest -q tests/catalog/test_jlcpcb.py`. It covers canonical
signature bytes, aliases, missing credentials, redaction, limits, refusal and
redirect handling, malformed responses and API-level failure codes.
