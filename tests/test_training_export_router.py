"""Smoke tests for the training-export FastAPI router.

The full app pulls in boto3, Redis, JWT, and other heavy dependencies via
``app/routers/__init__.py``'s eager imports. To keep this suite hermetic
the test loads the router module directly by file path, bypassing the
package init, and mounts it on a minimal FastAPI app.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from helpers.training_export import SCHEMA_VERSION

REPO_ROOT = Path(__file__).resolve().parent.parent
ROUTER_PATH = REPO_ROOT / "app" / "routers" / "training_export.py"


def _load_router_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_training_export_router_under_test", ROUTER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def router_module() -> ModuleType:
    return _load_router_module()


@pytest.fixture
def client(router_module: ModuleType) -> TestClient:
    app = FastAPI()
    app.include_router(router_module.router)
    return TestClient(app)


def test_export_info_returns_active_schema_version(client: TestClient):
    response = client.get("/training/export/info")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == SCHEMA_VERSION
    assert "sft" in body["formats"]
    assert body["formats"]["sft"]["media_type"] == "application/x-ndjson"


def test_export_sft_streams_ndjson_with_schema_version_header(client: TestClient):
    response = client.get("/training/export/sft")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["x-training-export-schema-version"] == SCHEMA_VERSION
    assert "attachment" in response.headers["content-disposition"]


def test_export_sft_rejects_inverted_time_window(client: TestClient):
    response = client.get(
        "/training/export/sft",
        params={
            "since": "2026-05-10T12:00:00Z",
            "until": "2026-05-09T12:00:00Z",
        },
    )
    assert response.status_code == 400
    assert "since" in response.json()["detail"].lower()


def test_export_sft_with_real_traces(
    monkeypatch: pytest.MonkeyPatch, router_module: ModuleType
):
    """Each yielded trace is converted; output is one JSONL line per kept trace."""
    sample_traces = [
        {
            "id": "t-1",
            "input": {"query": "How to grow rice?"},
            "output": "Plant during monsoon.",
        },
        {
            "id": "t-2",
            "input": {"query": ""},  # empty query -> dropped by default
            "output": "",
        },
        {
            "id": "t-3",
            "input": {"query": "Best soil for tomatoes?"},
            "output": "Loamy, well-drained soil with pH 6.0-6.8.",
        },
    ]
    monkeypatch.setattr(
        router_module, "_fetch_traces",
        lambda *args, **kwargs: iter(sample_traces),
    )

    app = FastAPI()
    app.include_router(router_module.router)
    response = TestClient(app).get("/training/export/sft")
    assert response.status_code == 200

    lines = [line for line in response.text.splitlines() if line.strip()]
    assert len(lines) == 2  # the empty-query trace is dropped

    parsed = [json.loads(line) for line in lines]
    assert {p["trace_id"] for p in parsed} == {"t-1", "t-3"}
    assert all(p["schema_version"] == SCHEMA_VERSION for p in parsed)
