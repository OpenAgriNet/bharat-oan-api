import asyncio
import json
import sys
from io import StringIO
from unittest.mock import mock_open

import httpx
import pytest

from tests.test_production_reconciliation import load_module


@pytest.fixture
def commodity(load_module, monkeypatch):
    entries = [
        {"code": 1, "name": "Wheat", "terms": ["Wheat", "Gahu", "Gehun", "Godhi", "Gothambu"]},
        {"code": 2, "name": "Onion", "terms": ["Onion"]},
    ]
    with monkeypatch.context() as patch:
        patch.setattr("builtins.open", mock_open(read_data=json.dumps(entries)))
        return load_module("agents/tools/commodity.py")


def test_search_and_display_preserve_all_aliases(commodity):
    entry = commodity.COMMODITY_ENTRIES[0]
    assert str(entry) == "| **Wheat** | Wheat, Gahu, Gehun, Godhi, ... | `1` |"
    assert "..." not in str(commodity.COMMODITY_ENTRIES[1])
    assert "Gothambu (100%)" in asyncio.run(commodity.search_commodity("gothambu"))
    assert "No commodity matches" in asyncio.run(commodity.search_commodity("unrelated", threshold=1))
    with pytest.raises(ValueError):
        asyncio.run(commodity.search_commodity("wheat", threshold=2))


def test_master_sync_preserves_aliases_and_does_not_mutate_input(load_module):
    module = load_module("scripts/sync_commodity_codes.py")
    entries = [
        {"code": 7, "name": "Red Gram", "terms": ["Red Gram", "Tur"]},
        {"code": 1, "name": "Old Wheat", "terms": ["Old Wheat", "Gahu"]},
        {"code": 2, "name": "Old Onion", "terms": ["Onion alias"]},
        {"code": 3, "name": "Rice ", "terms": ["Rice"]},
    ]
    original = json.dumps(entries)
    master = [
        {"commodity_id": code, "commodity_name": name}
        for code, name in [(45, "Tur"), (1, "Wheat"), (2, "Onion"), (3, "Rice"), (5, "Corn")]
    ]
    updated, report = module.sync(entries, master)
    assert [entry["code"] for entry in updated] == [1, 2, 3, 5, 45]
    assert updated[0]["terms"] == ["Wheat", "Gahu"]
    assert updated[1]["terms"] == ["Onion alias", "Onion"]
    assert updated[-1]["terms"] == ["Red Gram", "Tur"]
    assert all(report[key] for key in report)
    assert json.dumps(entries) == original
    assert not module._add_term(updated[-1]["terms"], "tur")
    with pytest.raises(SystemExit, match="has no superseding code"):
        module.sync([{"code": 999, "name": "Unknown", "terms": []}], master)


@pytest.mark.parametrize("saved,dry_run", [(False, False), (True, True)])
def test_sync_cli_can_preview_or_write_using_isolated_files(load_module, monkeypatch, capsys, saved, dry_run):
    module = load_module("scripts/sync_commodity_codes.py")
    entries = [{"code": 1, "name": "Wheat", "terms": ["Wheat"]}]
    master = [{"commodity_id": 1, "commodity_name": "New Wheat"}]
    reads = {"codes.json": json.dumps(entries), "master.json": json.dumps(master)}
    writes = []
    writer = mock_open()

    def open_file(path, mode="r", **kwargs):
        if mode == "w":
            writes.append(path)
            return writer()
        return StringIO(reads[path])

    args = ["sync", "codes.json"]
    if saved:
        args += ["--master", "master.json"]
    if dry_run:
        args += ["--dry-run"]
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr("builtins.open", open_file)
    monkeypatch.setattr(module, "fetch_master", lambda: master)
    module.main()
    assert "renamed (1)" in capsys.readouterr().out
    assert writes == ([] if dry_run else ["codes.json"])
    if not dry_run:
        output = "".join(call.args[0] for call in writer().write.call_args_list)
        assert json.loads(output)[0]["name"] == "New Wheat"


def test_fetch_master_uses_configured_api_and_reports_http_errors(load_module, monkeypatch):
    module = load_module("scripts/sync_commodity_codes.py")
    monkeypatch.setenv("AGMARKNET_BASE_URL", "https://example.test/")
    monkeypatch.setenv("AGMARKNET_ACCESS_NAME", "test-access")
    monkeypatch.setenv("AGMARKNET_PASSWORD", "test-placeholder")
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: httpx.Response(200, json={"token": "test-token"}))
    result = [{"commodity_id": 1, "commodity_name": "Wheat"}]

    def get(url, *, params, timeout):
        assert url == "https://example.test/v1/fetch-agmarknet-master-data"
        assert params == {"option": "2", "token": "test-token"}
        return httpx.Response(200, json=result, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    assert module.fetch_master() == result
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(
        503, request=httpx.Request("GET", "https://example.test")
    ))
    with pytest.raises(httpx.HTTPStatusError):
        module.fetch_master()
