from __future__ import annotations

import json
import subprocess
import sys
import traceback
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from jsonschema import Draft202012Validator, FormatChecker
from singer_sdk.exceptions import ConfigValidationError

from tap_sanctionskit import TapSanctionsKit
from tap_sanctionskit.client import ExportError

CURSOR_A = "10000000-0000-4000-8000-000000000001"
CURSOR_B = "10000000-0000-4000-8000-000000000002"
FAKE_KEY = "private-test-marker-never-networked"


def page(items, cursor=None):
    return {"data": {"items": items, "nextCursor": cursor}}


def tap(**config):
    return TapSanctionsKit(config={"api_key": FAKE_KEY, **config})


def records(name, **config):
    return list(tap(**config).streams[name].get_records(None))


def test_discovery_requires_no_http_and_advertises_full_table(transport):
    obj = tap()
    catalog = obj.catalog.to_dict()
    assert {s["stream"] for s in catalog["streams"]} == {"results", "audit"}
    for stream in obj.streams.values():
        assert stream.replication_method == "FULL_TABLE"
        assert stream.replication_key is None
        assert stream.primary_keys == ["id"]
    assert transport[1] == []
    assert obj.config_jsonschema["properties"]["api_key"]["secret"] is True
    assert obj.config_jsonschema["properties"]["audit_query"]["secret"] is True


def test_results_preserve_complete_raw_evidence_and_future_fields(result, transport):
    result["futureField"] = {"arbitrary": ["é", None, {"nested": 0}]}
    transport[0].append(page([result]))
    rows = records("results")
    assert json.loads(rows[0]["record_json"]) == result
    assert rows[0]["status"] == "no_match"
    assert rows[0]["environment"] == "sandbox"
    assert rows[0]["created_at"] == result["createdAt"]
    request = transport[1][0]["request"]
    assert request.method == "GET"
    assert request.headers["Authorization"] == "Bearer " + FAKE_KEY
    assert parse_qs(urlparse(request.url).query) == {"limit": ["25"], "summary": ["false"]}
    assert transport[1][0]["kwargs"]["allow_redirects"] is False
    assert transport[1][0]["kwargs"]["verify"] is True
    assert transport[1][0]["trust_env"] is False


def test_audit_retention_changes_do_not_rewrite_human_decisions(audit, transport):
    audit.update(decision="confirmed", businessDisposition="restrict", notes="[removed under retention policy]", evidenceAvailable=False, subjectName=None, screeningId=None)
    transport[0].append(page([audit]))
    row = records("audit")[0]
    assert row["decision"] == "confirmed"
    assert row["business_disposition"] == "restrict"
    assert row["evidence_available"] is False
    assert row["screening_id"] is None
    assert json.loads(row["record_json"]) == audit


def test_short_and_empty_pages_continue_until_null_with_frozen_filters(audit, transport):
    second = deepcopy(audit); second["id"] = CURSOR_B
    transport[0].extend([page([audit], CURSOR_A), page([], CURSOR_B), page([second])])
    rows = records("audit", page_size=100, audit_query="Example Development Fixture", audit_review="open")
    assert [r["id"] for r in rows] == [audit["id"], second["id"]]
    params = [parse_qs(urlparse(c["request"].url).query) for c in transport[1]]
    base = {"limit": ["100"], "q": ["Example Development Fixture"], "review": ["open"]}
    assert params == [base, {**base, "cursor": [CURSOR_A]}, {**base, "cursor": [CURSOR_B]}]


def test_full_page_with_null_cursor_stops_without_extra_request(result, transport):
    transport[0].append(page([result]))
    assert len(records("results", page_size=1)) == 1
    assert len(transport[1]) == 1


@pytest.mark.parametrize("cursors", [[CURSOR_A, CURSOR_A], [CURSOR_A, CURSOR_B, CURSOR_A]])
def test_repeated_cursor_fails_instead_of_looping(cursors, transport):
    transport[0].extend(page([], cursor) for cursor in cursors)
    with pytest.raises(ExportError, match="repeated"):
        records("results")
    assert len(transport[1]) == len(cursors)


def test_max_pages_is_failure_not_successful_truncation(result, transport):
    transport[0].append(page([result], CURSOR_A))
    stream = tap(max_pages=1).streams["results"].get_records(None)
    assert next(stream)["id"] == result["id"]
    with pytest.raises(ExportError, match="max_pages"):
        next(stream)
    assert len(transport[1]) == 1


@pytest.mark.parametrize("body", [
    {}, {"error": {"message": "private-response-marker"}}, {"data": {}},
    {"data": {"items": []}}, {"data": {"items": {}, "nextCursor": None}},
    {"data": {"items": [], "nextCursor": 7}}, {"data": {"items": [], "nextCursor": "private-cursor-marker"}},
    b'{"data":{"items":[],"nextCursor":null},"data":{"items":[],"nextCursor":null}}',
    b'{"data":{"items":[],"nextCursor":NaN}}', b'{"data":{"items":[],"nextCursor":null,"extra":1e999}}', b'not-json-private-response-marker',
])
def test_malformed_pages_fail_without_private_response_details(body, transport, caplog):
    transport[0].append(body)
    with pytest.raises(ExportError) as error:
        records("results")
    rendered = "".join(traceback.format_exception(error.value)) + caplog.text
    for secret in ("private-response-marker", "private-cursor-marker", FAKE_KEY):
        assert secret not in rendered


@pytest.mark.parametrize("field,value", [("id", "bad-uuid"), ("createdAt", "yesterday"), ("environment", "unknown"), ("matches", None), ("status", "cleared")])
def test_invalid_result_contract_rejects_entire_page_before_yield(result, field, value, transport):
    invalid = deepcopy(result); invalid[field] = value
    transport[0].append(page([result, invalid]))
    iterator = tap().streams["results"].get_records(None)
    with pytest.raises(ExportError, match="contract"):
        next(iterator)


def test_compact_summary_cannot_be_mistaken_for_full_result(transport):
    transport[0].append(page([{"id": CURSOR_A, "status": "no_match", "createdAt": "2026-10-14T00:00:00Z"}]))
    with pytest.raises(ExportError, match="contract"):
        records("results")


@pytest.mark.parametrize("status", [301, 302, 400, 401, 403, 404, 429, 500, 503])
def test_http_failure_is_sanitized_and_never_retried(status, transport, caplog):
    transport[0].append((status, {"error": {"message": FAKE_KEY + " private-body-marker"}}))
    query = "private-query-marker"
    with pytest.raises(ExportError, match=f"HTTP {status}") as error:
        records("audit", audit_query=query)
    rendered = "".join(traceback.format_exception(error.value)) + caplog.text
    assert all(x not in rendered for x in [FAKE_KEY, "private-body-marker", "private-query-marker"])
    assert len(transport[1]) == 1


@pytest.mark.parametrize("failure", [requests.Timeout, requests.ConnectionError, requests.exceptions.SSLError])
def test_transport_failure_does_not_chain_secret_exception(failure, transport, caplog):
    transport[0].append(failure("private-transport-marker " + FAKE_KEY))
    with pytest.raises(ExportError) as error:
        records("results")
    rendered = "".join(traceback.format_exception(error.value)) + caplog.text
    assert "private-transport-marker" not in rendered and FAKE_KEY not in rendered
    assert len(transport[1]) == 1


def test_response_size_guard_is_failure(transport, monkeypatch):
    monkeypatch.setattr("tap_sanctionskit.client.MAX_RESPONSE_BYTES", 4)
    transport[0].append(page([]))
    with pytest.raises(ExportError, match="size limit"):
        records("results")


@pytest.mark.parametrize("config", [
    {"page_size": 0}, {"page_size": 101}, {"page_size": True}, {"max_pages": 0},
    {"request_timeout": 0}, {"api_key": "private-key-marker\n"}, {"api_key": {"private-key-marker": "secret"}},
    {"audit_query": "x" * 201}, {"audit_review": "unreviewed"}, {"start_date": "2026-10-01"},
    {"base_url": "https://private-host-marker.test"}, {"results_status": "no_match"},
    {"stream_maps": {"results": {"__filter__": "True"}}}, {"batch_config": {}},
])
def test_invalid_config_is_rejected_without_logging_values(config, caplog):
    with pytest.raises(ConfigValidationError) as error:
        tap(**config)
    rendered = "".join(traceback.format_exception(error.value)) + caplog.text
    assert "private-key-marker" not in rendered and "private-host-marker" not in rendered


def test_incremental_catalog_request_rejected(transport):
    catalog = tap().catalog.to_dict()
    catalog["streams"][0]["replication_method"] = "INCREMENTAL"
    catalog["streams"][0]["replication_key"] = "created_at"
    with pytest.raises(ConfigValidationError, match="FULL_TABLE"):
        TapSanctionsKit(config={"api_key": FAKE_KEY}, catalog=catalog).streams


def test_prior_state_does_not_skip_records_or_resume_a_cursor(result, transport):
    transport[0].extend([page([result]), page([])])
    obj = TapSanctionsKit(config={"api_key": FAKE_KEY}, state={"bookmarks": {"results": {"replication_key": "created_at", "replication_key_value": "2099-01-01T00:00:00Z"}}})
    assert list(obj.streams["results"].get_records(None))[0]["id"] == result["id"]
    assert "cursor" not in parse_qs(urlparse(transport[1][0]["request"].url).query)


def test_offline_example_emits_valid_singer_messages_and_preserves_json():
    script = Path(__file__).parents[1] / "examples" / "offline_export.py"
    run = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, check=True)
    messages = [json.loads(line) for line in run.stdout.splitlines()]
    schemas = {m["stream"]: m["schema"] for m in messages if m["type"] == "SCHEMA"}
    rows = [m for m in messages if m["type"] == "RECORD"]
    assert set(schemas) == {"results", "audit"} and len(rows) == 2
    for item in rows:
        Draft202012Validator(schemas[item["stream"]], format_checker=FormatChecker()).validate(item["record"])
        original = json.loads(item["record"]["record_json"])
        assert original["id"] == item["record"]["id"]
        if item["stream"] == "results":
            assert original["fixtureContext"]["synthetic"] is True
        else:
            assert original["actorEmail"] == "reviewer@example.test"
    assert "offline-not-a-real-key" not in run.stdout + run.stderr


def invoke_cli(tmp_path, selected, config=None):
    from click.testing import CliRunner
    configured = {"api_key": FAKE_KEY, **(config or {})}
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(configured))
    catalog = tap().catalog.to_dict()
    for entry in catalog["streams"]:
        root = next(m for m in entry["metadata"] if m["breadcrumb"] == [])
        root["metadata"]["selected"] = entry["stream"] in selected
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(catalog))
    return CliRunner().invoke(TapSanctionsKit.cli, ["--config", str(config_file), "--catalog", str(catalog_file)])


def test_cli_late_page_failure_is_nonzero_after_partial_output(tmp_path, result, transport):
    transport[0].extend([page([result], CURSOR_A), (503, {"private-body-marker": FAKE_KEY})])
    run = invoke_cli(tmp_path, {"results"})
    assert run.exit_code != 0
    messages = [json.loads(line) for line in run.stdout.splitlines()]
    emitted = [m for m in messages if m["type"] == "RECORD"]
    assert len(emitted) == 1 and emitted[0]["record"]["id"] == result["id"]
    rendered = run.output + "".join(traceback.format_exception(run.exception))
    assert FAKE_KEY not in rendered and "private-body-marker" not in rendered
    assert len(transport[1]) == 2


def test_cli_selection_only_calls_selected_endpoint(tmp_path, audit, transport):
    transport[0].append(page([audit]))
    run = invoke_cli(tmp_path, {"audit"})
    assert run.exit_code == 0, run.output
    messages = [json.loads(line) for line in run.stdout.splitlines()]
    assert {m["stream"] for m in messages if m["type"] == "RECORD"} == {"audit"}
    assert [urlparse(c["request"].url).path for c in transport[1]] == ["/api/v1/audit"]


def test_cli_invalid_secret_does_not_emit_value(tmp_path, transport):
    secret = "private-invalid-cli-key\n"
    run = invoke_cli(tmp_path, {"results"}, {"api_key": secret})
    assert run.exit_code != 0
    assert "private-invalid-cli-key" not in run.output
    assert transport[1] == []


def test_config_changes_cannot_change_filters_mid_traversal(audit, transport):
    transport[0].extend([page([audit], CURSOR_A), page([])])
    stream = tap(audit_query="original-filter", audit_review="open").streams["audit"]
    rows = stream.get_records(None)
    next(rows)
    stream._config["audit_query"] = "changed-filter"
    stream._config["audit_review"] = "dismissed"
    assert list(rows) == []
    second_query = parse_qs(urlparse(transport[1][1]["request"].url).query)
    assert second_query["q"] == ["original-filter"] and second_query["review"] == ["open"]
