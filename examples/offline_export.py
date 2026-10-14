"""Emit a complete synthetic Singer export with all network access blocked.

Run from the source checkout after installing it:
    python examples/offline_export.py > synthetic-records.jsonl
"""
from __future__ import annotations

import io
import json
from copy import deepcopy
from unittest.mock import patch
from urllib.parse import urlparse

import requests

from tap_sanctionskit import TapSanctionsKit

RESULT = {
    "id": "01234567-89ab-4cde-8f01-23456789abcd",
    "environment": "sandbox", "status": "no_match", "createdAt": "2026-10-14T09:00:00Z",
    "matches": [], "coverage": [],
    "versions": {"dataset": "offline-fixture", "matchingEngine": "offline-fixture", "policy": "offline-fixture"},
    "disclaimer": "Fictional offline test fixture. No screening was performed.",
    "fixtureContext": {"subject": "Example Development Fixture", "synthetic": True},
}
AUDIT = {
    "id": "01234567-89ab-4cde-8f01-23456789abce",
    "caseId": "01234567-89ab-4cde-8f01-23456789abcf",
    "actorId": "offline-reviewer", "actorEmail": "reviewer@example.test",
    "decision": "open", "businessDisposition": "pending", "appliedControls": None,
    "notes": "Fictional offline review; no decision about a real person.",
    "createdAt": "2026-10-14T09:01:00Z", "screeningId": RESULT["id"],
    "subjectName": "Example Development Fixture", "evidenceAvailable": True,
}


def synthetic_send(session, request, **kwargs):
    if request.method != "GET" or urlparse(request.url).hostname != "www.sanctionskit.com":
        raise AssertionError("Unexpected offline request")
    path = urlparse(request.url).path
    if path == "/api/v1/results":
        item = RESULT
    elif path == "/api/v1/audit":
        item = AUDIT
    else:
        raise AssertionError("Unexpected offline endpoint")
    response = requests.Response()
    response.status_code = 200
    response.url = request.url
    response.request = request
    response._content = json.dumps({"data": {"items": [deepcopy(item)], "nextCursor": None}}).encode()
    response._content_consumed = True
    response.raw = io.BytesIO(response._content)
    return response


def main():
    with patch("socket.socket.connect", side_effect=AssertionError("Network disabled")), patch.object(
        requests.Session, "send", synthetic_send
    ):
        # This deliberately invalid placeholder is never sent over the network.
        TapSanctionsKit(config={"api_key": "offline-not-a-real-key"}).sync_all()


if __name__ == "__main__":
    main()
