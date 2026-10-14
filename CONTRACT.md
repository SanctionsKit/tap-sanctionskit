# Contract and qualification boundary

The two packaged validation schemas are resolved subsets of the public OpenAPI document retrieved from `https://www.sanctionskit.com/openapi.json` on 2026-10-14 at 16:39:19 UTC. SHA-256 of that complete source document:

`ee35d211051ac59466f15ac101fe9fd8b572641c5328a6b605359cf869705c6c`

- `schemas/result.json`: `components.schemas.ScreeningResult`, with local component references resolved. Plain `GET /results` items use this contract; compact summaries do not.
- `schemas/audit.json`: `components.schemas.AuditPage.properties.items.items`, with local component references resolved.
- Endpoint: `https://www.sanctionskit.com/api/v1`; authentication: bearer API key; required scope: `results:read`.
- Each successful response is an object with `data.items` and `data.nextCursor`. Cursors are UUIDs in the query contract. `null` ends traversal. Limit is 1–100. Filters stay unchanged across pages.
- Neither stream uses a timestamp filter or manufactures incremental replication. `created_at` is exported for analysis, not used as a sync bookmark.

The packaged schemas validate required fields, nested types, known enums, UUIDs and date-time strings before emitting a page. Additional fields remain available wherever the published schema permits them; full input records are serialized into `record_json`. A future incompatible API change fails explicitly and requires a reviewed contract update. Schemas are local, and no schema resolution or discovery call accesses a remote account.

Synthetic transport tests run against Singer SDK 0.54.7. Runtime support is bounded to `>=0.54.7,<0.55` until later versions are reviewed. The package uses the SDK's `Tap` and `Stream` interfaces for discovery, catalogs, selection, messages and state, with a small custom read-only HTTP client to avoid copying sensitive server error bodies into logs. It does not claim a hosted API test, target compatibility test, PyPI release, Hub acceptance or production deployment.

Safety limits and credential handling:

- Fixed HTTPS origin; only GET `/results` and GET `/audit` are accepted.
- Bearer header only; TLS verification; no redirects, automatic retries, `.netrc` or environment proxy/TLS overrides.
- Connection/read timeouts; 64 MiB decoded response body cap; configurable page-count cap that fails rather than silently truncates.
- Duplicate JSON members and non-finite numbers are rejected. Record-validation errors omit source values.
- There is no screening, model invocation, case mutation or cross-account query in the implementation.

For the current API contract and access details, use the [OpenAPI document](https://www.sanctionskit.com/openapi.json) and [SanctionsKit documentation](https://www.sanctionskit.com/docs).
