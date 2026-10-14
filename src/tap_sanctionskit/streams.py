"""Full refresh streams using the Singer SDK and the public API contract."""
from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from singer_sdk import Stream
from singer_sdk.exceptions import ConfigValidationError

from tap_sanctionskit.client import AccountClient, ExportError

COMMON = {
    "id": {"type": "string", "format": "uuid"},
    "created_at": {"type": "string", "format": "date-time"},
    "record_json": {"type": "string", "description": "Complete API record serialized as JSON; includes retained private evidence."},
}


class AccountStream(Stream):
    primary_keys = ["id"]
    replication_key = None
    path: str
    contract_name: str

    def __init__(self, tap: Any) -> None:
        super().__init__(tap)
        contract = json.loads(files("tap_sanctionskit").joinpath("schemas", self.contract_name).read_text())
        self._record_validator = Draft202012Validator(contract, format_checker=FormatChecker())

    def apply_catalog(self, catalog: Any) -> None:
        super().apply_catalog(catalog)
        if self.replication_key or self.replication_method != "FULL_TABLE":
            raise ConfigValidationError("SanctionsKit streams only support FULL_TABLE replication, without a replication key.")
        if list(self.primary_keys) != ["id"]:
            raise ConfigValidationError("SanctionsKit streams require the id primary key.")

    def query_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": self.config.get("page_size", 25)}
        if self.path == "/results":
            params["summary"] = "false"
        else:
            if self.config.get("audit_query") is not None:
                params["q"] = self.config["audit_query"]
            if self.config.get("audit_review") is not None:
                params["review"] = self.config["audit_review"]
        return params

    def project(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": record["id"],
            "created_at": record["createdAt"],
            "record_json": json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
        }

    def get_records(self, context: Any = None):
        # Freeze the filters for this traversal. Cursor state never carries across runs.
        base_params = self.query_params()
        cursor = None
        seen_cursors: set[str] = set()
        with AccountClient(self.config["api_key"], self.config.get("request_timeout", 60)) as client:
            for _ in range(self.config.get("max_pages", 10000)):
                params = dict(base_params)
                if cursor is not None:
                    params["cursor"] = cursor
                page = client.page(self.path, params)
                next_cursor = page["nextCursor"]
                if next_cursor is not None and next_cursor.lower() in seen_cursors:
                    raise ExportError("SanctionsKit repeated a pagination cursor; the export is incomplete.")
                # Validate an entire page before yielding any of its records.
                for record in page["items"]:
                    if not self._record_validator.is_valid(record):
                        raise ExportError("SanctionsKit returned a record outside the supported contract; the export is incomplete.")
                for record in page["items"]:
                    yield self.project(record)
                if next_cursor is None:
                    return
                seen_cursors.add(next_cursor.lower())
                cursor = next_cursor
        raise ExportError("max_pages reached while another cursor exists; increase the limit and rerun a full refresh.")


class ResultsStream(AccountStream):
    name = "results"
    path = "/results"
    contract_name = "result.json"
    schema = {"type": "object", "properties": {
        **COMMON,
        "environment": {"type": "string", "enum": ["sandbox", "production"]},
        "status": {"type": "string", "enum": ["potential_match", "no_match"]},
    }, "required": [*COMMON, "environment", "status"], "additionalProperties": False}

    def project(self, record: dict[str, Any]) -> dict[str, Any]:
        return {**super().project(record), "environment": record["environment"], "status": record["status"]}


class AuditStream(AccountStream):
    name = "audit"
    path = "/audit"
    contract_name = "audit.json"
    schema = {"type": "object", "properties": {
        **COMMON,
        "case_id": {"type": "string", "format": "uuid"},
        "screening_id": {"type": ["string", "null"], "format": "uuid"},
        "decision": {"type": "string", "enum": ["open", "confirmed", "dismissed"]},
        "business_disposition": {"type": "string", "enum": ["pending", "allow", "restrict", "escalate"]},
        "evidence_available": {"type": "boolean"},
    }, "required": [*COMMON, "case_id", "screening_id", "decision", "business_disposition", "evidence_available"], "additionalProperties": False}

    def project(self, record: dict[str, Any]) -> dict[str, Any]:
        return {**super().project(record), "case_id": record["caseId"], "screening_id": record["screeningId"],
                "decision": record["decision"], "business_disposition": record["businessDisposition"],
                "evidence_available": record["evidenceAvailable"]}
