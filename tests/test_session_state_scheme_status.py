"""Session state written by the KCC, AIF, SHC, SMAM and GFR tools (app.services.session_state)."""
import asyncio
from types import SimpleNamespace

import pytest

from agents.deps import FarmerContext
from agents.tools import aif, gfr, kcc, shc_scheme_status, smam_scheme_status


def make_ctx():
    return SimpleNamespace(deps=FarmerContext(session_id="s", query="q"))


def state(ctx, section):
    return ctx.deps.session_state.get(section, {})


def beckn(action, code, fields=None, items=None):
    """on_init outcome sits on items[0].tags[0]; on_status on order.tags[0] (+ optional items)."""
    tag = {
        "descriptor": {"code": code, "short_desc": "desc"},
        "list": [{"descriptor": {"code": k}, "value": v} for k, v in (fields or {}).items()],
    }
    order = {"items": [{"tags": [tag]}]} if action == "init" else {"tags": [tag]}
    if items:
        order["items"] = items
    return {"responses": [{"message": {"order": order}}]}


def fake_request(monkeypatch, module, *envelopes):
    queue = list(envelopes)
    monkeypatch.setattr(module, "_request", lambda action, payload: queue.pop(0))


# --- KCC ---------------------------------------------------------------------

def test_kcc_otp_sent_then_multiple_applications(monkeypatch):
    ctx = make_ctx()
    apps = [{"tags": [{"list": [{"descriptor": {"code": "application_no"}, "value": n}]}]} for n in ("111", "222")]
    fake_request(monkeypatch, kcc,
                 beckn("init", "otp_sent"),
                 beckn("status", "multiple_applications", items=apps))

    kcc.initiate_kcc_otp(ctx, "+91 98765 43210")
    assert state(ctx, "kcc") == {"mobile": "9876543210", "otp_step": "sent"}

    kcc.check_kcc_application_status(ctx, "9876543210", "123456")
    assert state(ctx, "kcc") == {"mobile": "9876543210", "otp_step": "verified", "application_nos": ["111", "222"]}
    assert "123456" not in str(ctx.deps.session_state)


def test_kcc_wrong_otp_and_failed_send_store_nothing_new(monkeypatch):
    ctx = make_ctx()
    fake_request(monkeypatch, kcc, beckn("init", "otp_failed"))
    kcc.initiate_kcc_otp(ctx, "9876543210")
    assert state(ctx, "kcc") == {}

    fake_request(monkeypatch, kcc, beckn("init", "otp_sent"), beckn("status", "invalid_otp"))
    kcc.initiate_kcc_otp(ctx, "9876543210")
    kcc.check_kcc_application_status(ctx, "9876543210", "000000")
    assert state(ctx, "kcc") == {"mobile": "9876543210", "otp_step": "sent"}


def test_kcc_new_otp_resets_verified_and_list(monkeypatch):
    ctx = make_ctx()
    ctx.deps.session_state["kcc"] = {"mobile": "9876543210", "otp_step": "verified", "application_nos": ["1", "2"]}
    fake_request(monkeypatch, kcc, beckn("init", "otp_sent"))
    kcc.initiate_kcc_otp(ctx, "9123456780")
    assert state(ctx, "kcc") == {"mobile": "9123456780", "otp_step": "sent"}


def test_kcc_unavailable_stores_nothing(monkeypatch):
    ctx = make_ctx()

    def boom(action, payload):
        raise kcc._KccUnavailable(kcc.UNAVAILABLE)

    monkeypatch.setattr(kcc, "_request", boom)
    assert kcc.initiate_kcc_otp(ctx, "9876543210") == kcc.UNAVAILABLE
    assert ctx.deps.session_state == {}


# --- AIF ---------------------------------------------------------------------

def test_aif_flow_sent_verified_loan(monkeypatch):
    ctx = make_ctx()
    fake_request(monkeypatch, aif,
                 beckn("init", "otp_sent"),
                 beckn("init", "otp_verified"),
                 beckn("status", "loan_status", {"status": "Sanctioned", "loan_application_number": "555"}),
                 beckn("status", "no_grievances"))

    aif.initiate_aif_otp(ctx, "106545")
    assert state(ctx, "aif") == {"beneficiary_id": "106545", "otp_step": "sent"}
    aif.verify_aif_otp(ctx, "123456", "106545")
    assert state(ctx, "aif") == {"beneficiary_id": "106545", "otp_step": "verified"}
    aif.check_aif_loan_status(ctx, "106545", "555")
    aif.check_aif_grievance_status(ctx, "106545")
    assert state(ctx, "aif") == {"beneficiary_id": "106545", "otp_step": "verified", "loan_application_number": "555"}
    assert "123456" not in str(ctx.deps.session_state)


def test_aif_failures_keep_state_and_new_beneficiary_starts_over(monkeypatch):
    ctx = make_ctx()
    ctx.deps.session_state["aif"] = {"beneficiary_id": "106545", "otp_step": "verified", "loan_application_number": "555"}
    fake_request(monkeypatch, aif,
                 beckn("init", "invalid_otp", {"retryable": "true"}),
                 beckn("status", "loan_not_found"),
                 beckn("init", "otp_sent"))

    aif.verify_aif_otp(ctx, "000000", "106545")
    aif.check_aif_loan_status(ctx, "106545", "999")
    assert state(ctx, "aif") == {"beneficiary_id": "106545", "otp_step": "verified", "loan_application_number": "555"}

    aif.initiate_aif_otp(ctx, "200001")
    assert state(ctx, "aif") == {"beneficiary_id": "200001", "otp_step": "sent"}


# --- SHC / SMAM (async httpx) -------------------------------------------------

class FakeAsyncClient:
    body = None
    status_code = 200

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        cls = type(self)
        return SimpleNamespace(status_code=cls.status_code, json=lambda: cls.body, text="")


CONTEXT = {"domain": "d", "action": "on_init", "version": "1.1.0", "timestamp": "t",
           "message_id": "m", "transaction_id": "t"}


def shc_body(items):
    providers = [{"id": "p", "items": items}]
    return {"context": CONTEXT, "responses": [
        {"context": CONTEXT, "message": {"order": {"provider": {}, "providers": providers}}}]}


@pytest.fixture
def fake_async_client(monkeypatch):
    monkeypatch.setenv("BAP_ENDPOINT", "http://bap")
    for module in (shc_scheme_status, smam_scheme_status):
        monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.status_code = 200
    return FakeAsyncClient


def test_shc_remembers_phone_and_cycle_only_with_data(fake_async_client):
    ctx = make_ctx()
    fake_async_client.body = shc_body([])
    asyncio.run(shc_scheme_status.check_shc_status(ctx, "9876543210", "2024-25"))
    assert state(ctx, "shc") == {}

    fake_async_client.body = shc_body([{"id": "card-1", "tags": []}])
    asyncio.run(shc_scheme_status.check_shc_status(ctx, "+91 98765 43210", "2024-25"))
    assert state(ctx, "shc") == {"phone_number": "9876543210", "cycle": "2024-25"}

    fake_async_client.status_code = 500
    asyncio.run(shc_scheme_status.check_shc_status(ctx, "9123456780", "2023-24"))
    assert state(ctx, "shc") == {"phone_number": "9876543210", "cycle": "2024-25"}


def smam_body(status, providers):
    catalog = {"descriptor": {}, "providers": providers,
               "tags": [{"descriptor": {}, "list": [{"descriptor": {"code": "status"}, "value": status}]}]}
    return {"responses": [{"context": {"action": "on_search"}, "message": {"catalog": catalog}}]}


def test_smam_remembers_search_only_when_found(fake_async_client):
    ctx = make_ctx()
    fake_async_client.body = smam_body("failure", [])
    asyncio.run(smam_scheme_status.check_smam_scheme_status(ctx, "application_no", "APP/1"))
    assert state(ctx, "smam") == {}

    fake_async_client.body = smam_body("success", [{"id": "a", "descriptor": {"name": "APP/1"}}])
    asyncio.run(smam_scheme_status.check_smam_scheme_status(ctx, "mobile", "+919876543210"))
    assert state(ctx, "smam") == {"search_type": "mobile", "search_value": "9876543210"}


# --- GFR ---------------------------------------------------------------------

def registry_item(cid, name, season="Rabi", state_id="STATE-0001", district_id="DIST-01"):
    details = {"season": season, "irrigationType": "Irrigated", "GFRavailable": "Yes",
               "stateId": state_id, "districtId": district_id}
    return {"id": cid, "descriptor": {"name": name},
            "tags": [{"descriptor": {"code": "crop_details"},
                      "list": [{"descriptor": {"code": k}, "value": v} for k, v in details.items()]}]}


def gfr_body(items):
    return {"responses": [{"message": {"catalog": {"providers": [{"id": "gfr", "items": items}]}}}]}


@pytest.fixture
def fake_post(monkeypatch):
    monkeypatch.setenv("BAP_ENDPOINT", "http://bap")
    box = {}
    monkeypatch.setattr(gfr.httpx, "post", lambda *a, **k: SimpleNamespace(
        status_code=200, json=lambda: box["body"], text=""))
    return box


def test_gfr_registry_keeps_short_listings_and_merges(fake_post):
    ctx = make_ctx()
    fake_post["body"] = gfr_body([registry_item("C1", "Wheat")])
    gfr.gfr_get_crop_registries(ctx, 25.0, 85.0, crop_name_contains="wheat")
    fake_post["body"] = gfr_body([registry_item("C2", "Paddy", season="Kharif")])
    gfr.gfr_get_crop_registries(ctx, 25.0, 85.0, crop_name_contains="paddy")
    assert state(ctx, "gfr") == {
        "state_id": "STATE-0001", "district_id": "DIST-01",
        "crops": {"C1": "Wheat (Rabi, Irrigated)", "C2": "Paddy (Kharif, Irrigated)"},
    }

    # A long unfiltered listing does not flood state; the known crops stay.
    fake_post["body"] = gfr_body([registry_item(f"X{i}", f"Crop {i}") for i in range(20)])
    gfr.gfr_get_crop_registries(ctx, 25.0, 85.0)
    assert set(state(ctx, "gfr")["crops"]) == {"C1", "C2"}

    # Another state: old crop IDs no longer apply.
    fake_post["body"] = gfr_body([registry_item("D1", "Maize", state_id="STATE-0002", district_id="DIST-09")])
    gfr.gfr_get_crop_registries(ctx, 20.0, 80.0)
    assert state(ctx, "gfr") == {"state_id": "STATE-0002", "district_id": "DIST-09",
                                 "crops": {"D1": "Maize (Rabi, Irrigated)"}}


def test_gfr_registry_empty_stores_nothing(fake_post):
    ctx = make_ctx()
    fake_post["body"] = gfr_body([])
    assert gfr.gfr_get_crop_registries(ctx, 25.0, 85.0) == "No crops found."
    assert ctx.deps.session_state == {}


def test_gfr_recommendation_remembers_phone_and_cycle(fake_post):
    ctx = make_ctx()
    ctx.deps.session_state["gfr"] = {"state_id": "STATE-0001", "district_id": "DIST-01", "crops": {"C1": "Wheat"}}

    fake_post["body"] = gfr_body([])
    gfr.gfr_get_recommendations(ctx, "STATE-0001", ["C1"], "9876543210", "2025-26")
    assert "phone_no" not in state(ctx, "gfr")

    fake_post["body"] = gfr_body([{"id": "r1", "descriptor": {"name": "Wheat"},
                                   "tags": [{"list": [{"descriptor": {"code": "n"}, "value": "120"}]}]}])
    gfr.gfr_get_recommendations(ctx, "STATE-0001", ["C1"], "+91 98765 43210", "2025-26")
    assert state(ctx, "gfr") == {"state_id": "STATE-0001", "district_id": "DIST-01", "crops": {"C1": "Wheat"},
                                 "phone_no": "9876543210", "cycle": "2025-26"}
