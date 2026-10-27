import asyncio
from types import SimpleNamespace

import httpx
import pytest

from tests.test_mandi import (
    TestMandiRequestPayload as RequestTests,
    _make_item,
    load_module,
    mandi,
)


@pytest.fixture
def mandi_request(mandi):
    return RequestTests()._make_request(mandi, price_date="01-07-2026", price_date_to="10-07-2026")


def test_pagination_preserves_context_deduplicates_and_advances_dates(mandi, mandi_request, monkeypatch):
    base = mandi_request.get_payload()
    item = _make_item(mandi, "1", arrival_date="05-07-2026")
    pages = iter([
        mandi._build_merged_response(base, [item, item]),
        mandi._build_merged_response(base, [_make_item(mandi, "2", arrival_date="01-07-2026")]),
    ])
    payloads = []

    def post(url, *, json, timeout):
        assert url == "https://example.test/search"
        payloads.append(json)
        return httpx.Response(200, json=next(pages).model_dump(mode="json"))

    monkeypatch.setattr(mandi.httpx, "post", post)
    items, context = mandi._fetch_all_pages("https://example.test/search", base, "01-07-2026", "10-07-2026")
    assert [item.id for item in items] == ["1", "2"]
    assert context == {}
    assert len(payloads) == 2
    assert [payload["message"]["intent"]["tags"][-1]["value"] for payload in payloads] == [
        "10-07-2026", "04-07-2026"
    ]
    assert base["message"]["intent"]["tags"][-1]["value"] == "10-07-2026"
    for key in ("message_id", "transaction_id"):
        assert len({base["context"][key], *(payload["context"][key] for payload in payloads)}) == 3
    assert all(payload["context"]["tags"] == base["context"]["tags"] for payload in payloads)


@pytest.mark.parametrize("consent,expected_calls", [(False, 1), (True, 2)])
def test_nearby_pagination_waits_for_consent(mandi, mandi_request, monkeypatch, consent, expected_calls):
    base = mandi_request.get_payload()
    response = mandi._build_merged_response(base, [_make_item(mandi, "1", arrival_date="05-07-2026")])
    response.responses[0].message.catalog.tags = [
        mandi.Tag(descriptor=mandi.Descriptor(code="search-context"), list=[
            mandi.TagItem(descriptor=mandi.Descriptor(code="status"), value="nearby_only")
        ])
    ]
    calls = []

    def page(*args):
        calls.append(args)
        return response

    monkeypatch.setattr(mandi, "_request_mandi_page", page)
    items, context = mandi._fetch_all_pages(
        "https://example.test/search", base, "01-07-2026", "10-07-2026", consent
    )
    assert len(calls) == expected_calls
    assert len(items) == 1
    assert context == {"status": "nearby_only"}


@pytest.mark.parametrize("kind", ["timeout", "connection", "status", "json", "shape"])
def test_page_failure_is_not_empty_success(mandi, mandi_request, monkeypatch, caplog, kind):
    def post(*args, **kwargs):
        if kind == "timeout":
            raise httpx.ReadTimeout("test timeout")
        if kind == "connection":
            raise httpx.ConnectError("test connection")
        if kind == "status":
            return httpx.Response(500)
        if kind == "json":
            return httpx.Response(200, text="invalid json")
        return httpx.Response(200, json={"message": {}})
    monkeypatch.setattr(mandi.httpx, "post", post)
    assert mandi._fetch_all_pages(
        "https://example.test/search", mandi_request.get_payload(), "01-07-2026", "10-07-2026"
    ) is None
    assert caplog.records


def test_later_failure_preserves_fetched_items(mandi, mandi_request, monkeypatch):
    base = mandi_request.get_payload()
    response = mandi._build_merged_response(base, [_make_item(mandi, "1", arrival_date="05-07-2026")])
    pages = iter([response, None])
    monkeypatch.setattr(mandi, "_request_mandi_page", lambda *args: next(pages))
    items, _ = mandi._fetch_all_pages("https://example.test/search", base, "01-07-2026", "10-07-2026")
    assert [item.id for item in items] == ["1"]


def test_direct_provider_response_is_accepted(mandi, mandi_request, monkeypatch):
    response = mandi._build_merged_response(
        mandi_request.get_payload(), [_make_item(mandi, "1", arrival_date="01-07-2026")]
    )
    monkeypatch.setattr(mandi.httpx, "post", lambda *args, **kwargs: httpx.Response(
        201, json=response.responses[0].model_dump(mode="json")
    ))
    parsed = mandi._request_mandi_page("https://example.test/search", mandi_request.get_payload(), 0)
    assert [item.id for item in parsed._collect_items()] == ["1"]


@pytest.mark.parametrize("arrival,start", [(None, "01-07-2026"), ("05-07-2026", None)])
def test_pagination_stops_without_date_bounds(mandi, arrival, start):
    assert mandi._next_mandi_page_to_date([_make_item(mandi, "1", arrival_date=arrival)], start) is None


@pytest.mark.parametrize("mode", ["success", "no_data", "nearby", "failure", "config", "range"])
def test_public_mandi_tool(mandi, monkeypatch, mode):
    ctx = SimpleNamespace(deps=SimpleNamespace(session_id="test-session", question_id="test-question"))
    monkeypatch.setenv("BAP_ENDPOINT", "https://example.test")
    items = [] if mode == "no_data" else [_make_item(mandi, "1", arrival_date="01-07-2026")]
    context = {"status": "nearby_only", "market": "Nearby Market"} if mode == "nearby" else {}
    calls = []

    def fetch(*args, **kwargs):
        calls.append(args)
        return None if mode == "failure" else (items, context)

    monkeypatch.setattr(mandi, "_fetch_all_pages", fetch)
    if mode == "config":
        monkeypatch.delenv("BAP_ENDPOINT")
    result = asyncio.run(mandi.get_mandi_prices(
        ctx, 20.011, 73.79, "Nashik", "Onion", "01-07-2026",
        "10-09-2026" if mode == "range" else "10-07-2026"
    ))
    if mode == "success":
        assert "Commodity: Onion" in result
    elif mode == "nearby":
        assert "NO_DATA_AT_REQUESTED_MANDI" in result and "Commodity: Onion" not in result
    elif mode == "no_data":
        assert "NO_DATA" in result and "Onion at Nashik" in result
    elif mode == "range":
        assert "RANGE_TOO_LONG" in result and not calls
    else:
        assert "NO_DATA" not in result
        assert "error" in result.lower() or "unavailable" in result.lower()
