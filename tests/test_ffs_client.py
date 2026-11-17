"""FFS tools: provider calls go through the BAP; the provider itself is stubbed."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from agents.tools import ffs

CTX = SimpleNamespace(deps=SimpleNamespace(session_id="s1", question_id="q1", skills=SimpleNamespace(active={"ffs.booking"})))


class MemoryCache:
    def __init__(self):
        self.values = {}

    async def get(self, key, namespace=None):
        return self.values.get((namespace, key))

    async def set(self, key, value, ttl=None, namespace=None):
        self.values[(namespace, key)] = value


@pytest.fixture
def provider(monkeypatch):
    """Replaces the BAP hop; `replies` are popped per provider call."""
    calls, replies = [], []
    monkeypatch.setenv("BAP_ENDPOINT", "http://bap.invalid")
    monkeypatch.setattr(ffs, "cache", MemoryCache())

    async def fake(session_id, question_id, operation, args, diagnostics):
        calls.append((operation, json.loads(json.dumps(args, default=lambda m: m.model_dump()))))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(ffs, "_provider", fake)
    return calls, replies


def test_tool_normalises_digits_and_drops_omitted_arguments(provider):
    calls, replies = provider
    replies.append(ffs.result("otp_sent", "Ask for the OTP"))
    answer = asyncio.run(ffs.ffs_send_otp(CTX, "१२३४५६७८९०१", "९"))
    assert answer["status"] == "otp_sent"
    assert calls == [("send_otp", {"farmer_id": "12345678901", "state_lgd_code": "9", "resend": False})]


def test_skill_must_be_loaded_before_any_provider_call(provider):
    calls, _ = provider
    ctx = SimpleNamespace(deps=SimpleNamespace(session_id="s1", question_id="q1", skills=SimpleNamespace(active=set())))
    assert asyncio.run(ffs.ffs_verify_otp(ctx, "123456"))["status"] == "unavailable"
    assert calls == []


def test_crop_resolution_is_run_here_and_the_call_repeated_with_the_matches(provider, monkeypatch):
    calls, replies = provider
    request = {"kind": "crops", "names": ["bajri"], "catalogue": [{"cropCode": "1", "cropName": "Bajra"}], "state_code": "9"}
    replies += [ffs.result("needs_resolution", "match", **request), ffs.result("ok", "done")]

    async def resolve(names, crops, **kwargs):
        return [SimpleNamespace(status="matched", crop_code="1", candidates=[])]

    monkeypatch.setattr(ffs, "resolve_crops", resolve)
    answer = asyncio.run(ffs.ffs_get_entitlement(CTX, "village-1", crop_name="bajri"))
    assert answer["status"] == "ok"
    assert [call[0] for call in calls] == ["entitlement", "entitlement"]
    assert calls[1][1]["resolution"] == [{"name": "bajri", "status": "matched", "crop_code": "1", "candidates": []}]


def test_failed_product_matching_never_reaches_the_provider_again(provider, monkeypatch):
    calls, replies = provider
    replies.append(ffs.result("needs_resolution", "match", kind="products", names=["dap"], catalogue=[]))

    async def broken(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(ffs, "resolve_products", broken)
    answer = asyncio.run(ffs.ffs_find_shops(CTX, order=[ffs.OrderItem(product="dap", bags=1)]))
    assert answer["status"] == "unavailable"
    assert len(calls) == 1
    assert calls[0][1]["order"] == [{"product": "dap", "product_id": None, "bags": 1}]


def test_transport_failure_after_a_booking_request_is_uncertain(provider):
    _, replies = provider
    replies.append(httpx.ReadTimeout("slow"))
    assert asyncio.run(ffs.ffs_create_booking(CTX, "retailer-6", confirmed=True))["status"] == "booking_uncertain"
    replies.append(httpx.ReadTimeout("slow"))
    assert asyncio.run(ffs.ffs_find_shops(CTX))["status"] == "unavailable"


def test_chat_context_is_empty_until_ffs_is_used(provider):
    calls, replies = provider
    assert asyncio.run(ffs.prepare_chat_turn("s1")) == {}
    assert calls == []
    replies += [ffs.result("otp_sent", "x"), {"stage": "otp_pending"}]
    asyncio.run(ffs.ffs_send_otp(CTX, "12345678901", "9"))
    assert asyncio.run(ffs.prepare_chat_turn("s1")) == {"stage": "otp_pending"}
