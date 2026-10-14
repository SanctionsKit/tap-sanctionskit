from __future__ import annotations
import io
import json
import socket
from copy import deepcopy
from pathlib import Path
import runpy

import pytest
import requests

FIXTURES = runpy.run_path(str(Path(__file__).parents[1] / "examples" / "offline_export.py"))

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Test attempted a real network connection")
    monkeypatch.setattr(socket.socket, "connect", blocked)

@pytest.fixture
def result():
    return deepcopy(FIXTURES["RESULT"])

@pytest.fixture
def audit():
    return deepcopy(FIXTURES["AUDIT"])

@pytest.fixture
def transport(monkeypatch):
    queued, calls = [], []
    def send(session, request, **kwargs):
        calls.append({"request": request, "kwargs": kwargs, "trust_env": session.trust_env})
        if not queued:
            raise AssertionError("Unexpected extra HTTP request")
        value = queued.pop(0)
        if isinstance(value, Exception):
            raise value
        status, body = value if isinstance(value, tuple) else (200, value)
        response = requests.Response()
        response.status_code = status
        response.request = request
        response.url = request.url
        response._content = body if isinstance(body, bytes) else json.dumps(body).encode()
        response._content_consumed = True
        response.raw = io.BytesIO(response._content)
        return response
    monkeypatch.setattr(requests.Session, "send", send)
    return queued, calls
