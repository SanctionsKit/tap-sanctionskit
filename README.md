# tap-sanctionskit

A Singer extractor for your account's retained [SanctionsKit](https://www.sanctionskit.com/) screening results and human review audit records. It uses the [Singer SDK](https://sdk.meltano.com/) and reads only `GET /results` and `GET /audit`.

Both streams use **FULL_TABLE** replication. This is an export of what the account can currently read, not a complete historical archive. It does not screen subjects, change cases or make compliance decisions.

This is a source-installable development version (`0.1.0.dev0`). A published PyPI package, Meltano Hub listing, production qualification or hosted API test is not claimed.

## Install from this source checkout

Python 3.11 or later is required. From the directory containing `pyproject.toml`:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install .
tap-sanctionskit --about --format json
```

## Run the fictional example without an account

```sh
python examples/offline_export.py > synthetic-records.jsonl
```

The example blocks socket connections, supplies synthetic HTTP responses and runs the real Singer SDK export. It writes two `SCHEMA` and two `RECORD` messages, plus SDK state messages. No key, account or screening is used. To inspect the complete original record, parse the `record_json` string in a `RECORD` message. The example is a transport demonstration, not a sanctions decision about any person.

## Configure an account export

Create an API key with the `results:read` scope using your existing account. A sandbox key accesses that sandbox's synthetic evidence; production keys require the appropriate paid account and access. This tap does not create keys, seed sandbox records or grant access. A successful empty page means no matching retained records were returned, not that a screening took place.

Keep credentials in a protected config file or your orchestrator's secret store. Do not put them in command-line arguments, checked-in files or logs. This illustrative config is not a working credential:

```json
{
  "api_key": "replace-in-your-secret-store",
  "page_size": 25
}
```

The Singer SDK supports environment configuration, including `TAP_SANCTIONSKIT_API_KEY`; consult your orchestrator's secret injection controls. Discover the catalog without an HTTP request:

```sh
tap-sanctionskit --config config.json --discover > catalog.json
```

Mark only the streams you need as selected in your Singer catalog, then export:

```sh
tap-sanctionskit --config config.json --catalog catalog.json > records.jsonl
```

The default tap selects both streams. The output contains retained personal data, submitted inputs where present, analyst identity, notes and evidence. Store it with appropriate access controls and retention. Output redirection does not encrypt it. Test your chosen Singer target separately; no destination connector or target-specific replacement behavior is bundled or qualified here.

| Setting | Default | Meaning |
| --- | --- | --- |
| `api_key` | required | Secret account key with `results:read`. |
| `page_size` | `25` | Between 1 and 100. Smaller pages reduce response size. |
| `audit_query` | omitted | Optional audit search over retained names or resource IDs, at most 200 characters. Sensitive; keep it out of logs. |
| `audit_review` | omitted | Optional `open`, `confirmed` or `dismissed` audit filter. Unreviewed results have no audit decisions. |
| `request_timeout` | `60` | Read timeout in seconds, 1–300. Connection timeout is 10 seconds. |
| `max_pages` | `10000` | Per-stream safety limit. Reaching it with another cursor is an error, not a truncated success. |

Audit filters remain identical across all pages of a run. The `results` stream explicitly requests `summary=false` and does not expose summary-only search/status/review/origin filters. There is no `start_date`, incremental watermark, resumable cursor, custom host or automatic retry. TLS verification is enabled; redirects are refused. Process proxy settings, `.netrc` credentials and environment TLS overrides are not inherited by the HTTP session.

## Streams and complete evidence

| Stream | Queryable columns | Complete source record |
| --- | --- | --- |
| `results` | `id`, `created_at`, `environment`, `status` | `record_json` |
| `audit` | `id`, `created_at`, `case_id`, `screening_id`, `decision`, `business_disposition`, `evidence_available` | `record_json` |

The primary key is `id` within each stream and account configuration. Keep sandbox, production and different accounts in separate destination scopes. `record_json` contains the complete API item, including nested evidence, matching policy, coverage, warnings, disclaimers and future additional fields. JSON formatting/key ordering can change; parse it to recover the original data. Full response envelope fields are not exported.

`potential_match` and `no_match` are screening outcomes with the source's interpretation limits, not automatic business approvals or legal clearance. Audit `decision`, `businessDisposition` and `evidenceAvailable` are distinct source fields; the tap never derives one from another.

## Full refresh, retention and failed runs

- The public API currently provides cursor traversal, not a reliable incremental export filter. Every run starts without a cursor. An old Singer state file does not make this an incremental sync. Incremental catalog configuration is rejected.
- A short or empty page can still have a cursor. Only `nextCursor: null` completes traversal. Missing/invalid cursors, repeated cursors, invalid records, redirects, HTTP failures and the safety limit fail the run. A page is validated before any of its records are emitted.
- A run can emit earlier pages before a later failure. Check the process exit status. Stage output and replace or reconcile the account-scoped destination only after the **whole selected export succeeds**. Merely upserting IDs leaves expired/deleted records behind; Singer state messages are not proof that both streams completed.
- Standard retention may preserve submitted personal data. Minimal retention omits the input and expires retained evidence after 24 hours. Audit notes can become `[removed under retention policy]`, and linked evidence availability can change even when the original human decision remains. The tap exports the current returned values; it does not reconstruct removed records or notes.
- Deletions or retention changes in SanctionsKit do not automatically remove an earlier exported copy. Your destination needs its own retention and reconciliation policy. This tap emits no deletion tombstones.
- The two endpoints and their pages are separate reads. The public contract does not promise a transactionally consistent snapshot during concurrent writes. Records can change while a run is in progress. Do not treat the export as an immutable evidence archive or a completeness certification.
- There is no automatic retry of 429, 5xx or transport failures. Schedule a fresh run after resolving the problem. Lower `page_size` if a response exceeds the 64 MiB decoded body limit.

Normal errors omit API keys, query values, raw response bodies and transport exception details. Do not enable external HTTP tracing or record sensitive orchestrator configuration. Application error handling cannot protect logs produced by unrelated tooling.

## Tests

```sh
python -m pip install '.[test]'
python -m pytest -q
```

Tests block real socket connections and exercise the installed package, actual Singer SDK, prepared HTTP requests, cursor behavior, contract failures, retention changes, credential-safe failures and the runnable example. They do not establish live account access, hosted API behavior or a successful Meltano destination load. See [CONTRACT.md](CONTRACT.md) for the pinned API contract and limits.

MIT licensed.
