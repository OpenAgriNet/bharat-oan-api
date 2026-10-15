"""Check retained mandi and catalog behavior without contacting external services."""
import copy
import json
import sys

import httpx
import pytest

from tests.test_production_reconciliation import load_module


@pytest.fixture
def mandi(load_module):
    return load_module("agents/tools/mandi.py")


def payload():
    return {"context": {"action": "search", "timestamp": "2026-10-01T00:00:00Z",
        "message_id": "message", "transaction_id": "transaction", "domain": "schemes:vistaar",
        "version": "1.1.0", "tags": {"session_id": "session", "question_id": "question"}},
        "message": {"intent": {"tags": [{"code": "from_date", "value": "01-07-2026"},
                                     {"code": "to_date", "value": "10-07-2026"}]}}}


def response_page(*dates):
    items = [{"id": str(i), "descriptor": {"name": "Onion"}, "tags": [{
        "descriptor": {"code": "attributes"}, "list": [
            {"descriptor": {"code": "Market"}, "value": "Pune"},
            {"descriptor": {"code": "Arrival Date"}, "value": date},
        ]}]} for i, date in enumerate(dates)]
    return {"context": payload()["context"], "responses": [{"context": payload()["context"],
        "message": {"catalog": {"descriptor": {}, "providers": [{"id": "provider", "descriptor": {}, "items": items}]}}}]}


def fetched_items(result):
    # Dev additionally returns nearby-mandi provider context; production does not.
    return result[0] if isinstance(result, tuple) else result


@pytest.mark.parametrize("start, end, expected", [
    ("10-07-2026", None, ("10-07-2026", "10-07-2026")),
    ("01-07-2026", "10-07-2026", ("01-07-2026", "10-07-2026")),
    ("10-07-2026", "01-07-2026", ("01-07-2026", "10-07-2026")),
])
def test_mandi_date_contract(mandi, start, end, expected):
    assert mandi._resolve_date_range(start, end) == expected


def test_mandi_pages_move_back_deduplicate_and_preserve_tags(mandi, monkeypatch):
    sent = []
    pages = iter([response_page("10-07-2026", "05-07-2026"),
                  response_page("05-07-2026", "01-07-2026")])
    def post(url, **kw):
        sent.append(kw["json"])
        return httpx.Response(200, json=next(pages))
    monkeypatch.setattr(mandi.httpx, "post", post)
    original = payload()
    before = copy.deepcopy(original)
    items = fetched_items(mandi._fetch_all_pages("https://test/search", original, "01-07-2026", "10-07-2026"))
    assert len(items) == 3 and len(sent) == 2
    assert sent[1]["message"]["intent"]["tags"][1]["value"] == "04-07-2026"
    assert all(p["context"]["tags"] == before["context"]["tags"] for p in sent)
    assert len({p["context"]["message_id"] for p in sent}) == 2
    assert original == before


def test_mandi_stops_if_provider_repeats_page(mandi, monkeypatch):
    calls = []
    def post(*a, **kw):
        calls.append(kw)
        return httpx.Response(200, json=response_page("10-07-2026"))
    monkeypatch.setattr(mandi.httpx, "post", post)
    assert len(fetched_items(mandi._fetch_all_pages("https://test/search", payload(), "01-07-2026", "10-07-2026"))) == 1
    assert len(calls) == 2


@pytest.mark.parametrize("response", [httpx.Response(200, text="not json"), httpx.Response(200, json={"unexpected": True}), httpx.Response(503)])
def test_mandi_invalid_response_is_service_failure_not_no_data(mandi, monkeypatch, response):
    monkeypatch.setattr(mandi.httpx, "post", lambda *a, **kw: response)
    assert mandi._fetch_all_pages("https://test/search", payload(), "01-07-2026", "10-07-2026") is None


def test_mandi_keeps_fetched_items_when_later_page_is_malformed(mandi, monkeypatch):
    responses = iter([httpx.Response(200, json=response_page("10-07-2026")), httpx.Response(200, text="not json")])
    monkeypatch.setattr(mandi.httpx, "post", lambda *a, **kw: next(responses))
    assert len(fetched_items(mandi._fetch_all_pages("https://test/search", payload(), "01-07-2026", "10-07-2026"))) == 1


def test_commodity_sync_renames_merges_terms_and_adds_codes(load_module):
    module = load_module("scripts/sync_commodity_codes.py")
    entries = [{"code": 7, "name": "Red Gram", "terms": ["Red Gram", "अरहर"]},
               {"code": 45, "name": "Old name", "terms": ["Old name", "अरहर"]}]
    before = copy.deepcopy(entries)
    master = [{"commodity_id": 45, "commodity_name": "Red gram "},
              {"commodity_id": 999, "commodity_name": "New crop"}]
    updated, report = module.sync(entries, master)
    assert entries == before
    assert [e["code"] for e in updated] == [45, 999]
    assert updated[0]["name"] == "Red gram" and updated[0]["terms"].count("अरहर") == 1
    assert updated[1]["terms"] == ["New crop"]
    assert all(report[k] for k in ["removed", "renamed", "terms_replaced", "added"])


@pytest.mark.parametrize("mode", ["dry_run", "unknown_removed_code"])
def test_commodity_cli_leaves_file_intact_without_successful_write(load_module, monkeypatch, tmp_path, mode):
    module = load_module("scripts/sync_commodity_codes.py")
    codes = tmp_path / "codes.json"
    master = tmp_path / "master.json"
    codes.write_text(json.dumps([{"code": 100, "name": "Crop", "terms": ["Crop"]}]))
    before = codes.read_bytes()
    master.write_text(json.dumps([{"commodity_id": 100, "commodity_name": "Renamed"}] if mode == "dry_run" else []))
    argv = ["sync", str(codes), "--master", str(master)]
    monkeypatch.setattr(sys, "argv", argv + (["--dry-run"] if mode == "dry_run" else []))
    if mode == "unknown_removed_code":
        with pytest.raises(SystemExit, match="has no superseding code"):
            module.main()
    else:
        module.main()
    assert codes.read_bytes() == before
