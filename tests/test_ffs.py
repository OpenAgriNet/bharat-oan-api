"""FFS workflow tests: isolated transport by default; opt-in verified mock HTTP."""
import asyncio
import base64
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import importlib.util
import os
import re
from types import SimpleNamespace
from types import ModuleType
from pathlib import Path
import sys
import time
import uuid
from urllib.request import urlopen

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from fastapi import HTTPException
import httpx
from pydantic import SecretStr
from pydantic_ai import Agent, Tool
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
import pytest

from agents.deps import FarmerContext
from agents.skill_policy import SkillState
from agents.skills import SKILL_LOADER, prepare_skill_tools, render_prompt
from agents.tools.ffs import ffs_send_otp, ffs_verify_otp, ffs_get_entitlement, ffs_find_shops, ffs_create_booking
from app.services import ffs

USER = {"sub": "test-caller", "channel": "test", "auth_token_id": "verified-bearer-digest"}
KEY, IV = "K" * 32, "I" * 16


class MemoryCache:
    def __init__(self):
        self.values, self.locks = {}, {}
        self.client = self

    async def get(self, key, namespace=None):
        return deepcopy(self.values.get((namespace, key)))

    async def set(self, key, value, ttl=None, namespace=None):
        self.values[(namespace, key)] = json.loads(json.dumps(value))

    @asynccontextmanager
    async def lock(self, key, **kwargs):
        async with self.locks.setdefault(key, asyncio.Lock()):
            yield


class Backend:
    def __init__(self):
        self.calls, self.failed, self.fail_path, self.fail_after_write = [], False, None, False
        self.stock = {}
        self.booking_payloads = []
        self.land = [{"farmlandPlotRegistryId": 101 + i, "farmlandOwnershipId": 201 + i,
                      "ownershipMappingId": 301 + i, "landAreaHectares": 1.23 if i == 0 else 0.96,
                      "stateLgdCode": 27, "districtLgdCode": 496, "subDistrictLgdCode": 4245,
                      "villageLgdCode": 561972, "villageName": "Mock Village", "surveyNumber": str(i + 1),
                      "subSurveyNumber": "A", "crops": [], "claimApprovalStatus": None,
                      "disputeStatus": "NoDispute"} for i in range(2)]
        self.crops = [{"cropCode": "0101900", "cropName": "गहू"}, {"cropCode": "0101000", "cropName": "मका"},
                      {"cropCode": "0801200", "cropName": "सोयाबीन"}]
        self.gfr = [{"gfrType": 1, "recommendations": [
            {"productGroupId": 11, "offeredBags": 6, "noOfBagsRequired": 7, "kgPerBag": 45, "alreadyPurchasedBags": 1},
            {"productGroupId": 12, "offeredBags": 2, "noOfBagsRequired": 3, "kgPerBag": 50, "alreadyPurchasedBags": 0}]}]

    def count(self, suffix):
        return sum(path.endswith(suffix) for path, _ in self.calls)

    async def respond(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content and not path.startswith(ffs.USER) else None
        if path.startswith(ffs.USER):
            body = json.loads(unpad(AES.new(KEY.encode(), AES.MODE_CBC, IV.encode()).decrypt(base64.b64decode(request.content)), 16))
        self.calls.append((path, deepcopy(body)))
        if self.fail_path and path.endswith(self.fail_path) and not self.failed and not self.fail_after_write:
            self.failed = True
            raise httpx.ReadTimeout("test failure", request=request)
        meta = {}
        if path.endswith("/loginByFarmerId"):
            data = None
        elif path.endswith("/validateLoginByFarmerId"):
            if body["otp"] != "123456":
                return httpx.Response(400, json={"code": 400, "data": None})
            claims = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 900}).encode()).decode().rstrip("=")
            data = {"userToken": "test." + claims + ".signature", "userId": 42, "userAadhaarHash": "test-hash",
                    "userFullName": "Mock Farmer", "userFarmerId": "99999999999", "userStateLGDCode": 27,
                    "userMobileNumber": "9999999999"}
        elif path.endswith("getConfigurationByState"):
            data = [{"stateLgdCode": 27, "retailerListDisplayLevel": "D", "productSelectionMode": "GFR_ONLY"}]
        elif path.endswith("getCurrentSeasonAndYear"):
            data = {"season": {"seasonId": 2, "seasonName": "Rabi"}, "year": {"yearId": 1, "yearValue": "2026"}}
        elif path.endswith("/bharatVistar/applicantFetch"):
            data = {"landData": deepcopy(self.land)}
        elif path.endswith("getCropListByState"):
            data = self.crops
        elif path.endswith("/land/crop/add"):
            land = next(l for l in self.land if l["farmlandPlotRegistryId"] == body["farmlandPlotRegistryId"])
            data = {"farmlandPlotRegistryId": land["farmlandPlotRegistryId"], "cropCode": body["cropCode"],
                    "seasonId": body["seasonId"], "yearId": body["yearId"]}
            land["crops"] = [data]
            if self.fail_after_write and not self.failed and land["farmlandPlotRegistryId"] == 102:
                self.failed = True
                raise httpx.ReadTimeout("response lost after write", request=request)
        elif path.endswith("getApplicantDashboardQuota"):
            data = []
        elif path.endswith("getRecommendationsByGFRV4"):
            data = self.gfr
        elif path.endswith("getProductsByAvailability"):
            data = [{"productId": 1089, "productGroupId": 11, "plantId": 99, "bagWeightKg": 45},
                    {"productId": 1005, "productGroupId": 12, "plantId": 99, "bagWeightKg": 50}]
        elif path.endswith("getRetailersByProductAvailabilityV4"):
            limit = {"V": 2, "T": 6, "D": 8, "S": 8}[body["territoryLevel"]]
            data = [{"retailerId": i, "retailerName": f"Shop {i}", "address1": "Mock address", "availableProducts": [
                {"products": [{"productId": 1089, "noBagsAvailable": self.stock.get(i, (10 + i, 10 + i))[0]}]},
                {"products": [{"productId": 1005, "noBagsAvailable": self.stock.get(i, (10 + i, 10 + i))[1]}]}]} for i in range(limit)]
            meta = {"totalPages": 1, "totalRecords": limit}
        elif path.endswith("createMultipleBookingV2"):
            self.booking_payloads.append(deepcopy(body))
            data = {
                "isValid": True,
                "message": "All 1 booking(s) created successfully",
                "bookingResults": [{
                    "retailerId": body["bookingRequest"][0]["retailerId"],
                    "procurementId": 123456,
                    "procurementApplicationCode": "PRC-TEST-001",
                    "isWaitlisted": False,
                }],
                "totalBookingsRequested": 1,
                "totalBookingsSuccessful": 1,
                "totalBookingsFailed": 0,
                "applicantUserId": body["applicantUserId"],
            }
        else:
            raise AssertionError("Unexpected API: " + path)
        return httpx.Response(200, json={"code": 200, "status": "success", "data": data, **meta})


@pytest.fixture
def setup(monkeypatch):
    memory, backend = MemoryCache(), Backend()
    monkeypatch.setattr(ffs, "cache", memory)
    monkeypatch.setattr(ffs.settings, "ffs_base_url", "http://ffs.test")
    monkeypatch.setattr(ffs.settings, "ffs_esa_key", SecretStr(KEY))
    monkeypatch.setattr(ffs.settings, "ffs_esa_iv", SecretStr(IV))
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(transport=httpx.MockTransport(backend.respond), **kw))
    return SimpleNamespace(memory=memory, backend=backend, client_class=original_client)


def context(turn="turn-1", session="session-1", otp=None, query="book fertilizer", owner=None, selective=False):
    return SimpleNamespace(deps=FarmerContext(query=query, session_id=session, question_id=turn,
        ffs_owner=owner if owner is not None else ffs.owner_id(USER), ffs_otp=SecretStr(otp) if otp else None,
        skills=SkillState(enabled_skills=["ffs.booking"], preloaded_skills=[] if selective else ["ffs.booking"])))


async def login(session="session-1"):
    assert (await ffs_send_otp(context(session=session), "99999999999", "Maharashtra"))["status"] == "otp_sent"
    out = await ffs_verify_otp(context("turn-2", session, "123456"))
    assert out["status"] == "ok"
    return out


def selection(view, crop="maize"):
    village = view["data"]["villages"][0]
    return village["village_ref"], [ffs.CropSelection(land_ref=p["land_ref"], crop_name=crop) for p in village["plots"]]


def test_whole_flow_and_repeats_do_not_repeat_writes(setup):
    async def run():
        village, crops = selection(await login())
        ctx = context("turn-3")
        out = await ffs_get_entitlement(ctx, village, crops)
        assert [p["bags"] for p in out["data"]["products"]] == [6, 2]
        assert setup.backend.count("/land/crop/add") == 2
        assert await ffs_get_entitlement(context("turn-4"), village, crops) == out
        assert setup.backend.count("/land/crop/add") == 2
        shops = await ffs_find_shops(context("turn-5"))
        assert shops["status"] == "ok" and len(shops["data"]["shops"]) == 3
        assert shops["data"]["booking_enabled"] is True
        assert await ffs_find_shops(context("turn-5")) == shops
        more = await ffs_find_shops(context("turn-6"), "more")
        assert not ({x["shop_ref"] for x in shops["data"]["shops"]} & {x["shop_ref"] for x in more["data"]["shops"]})
        encoded = json.dumps([out, shops, more])
        for secret in ("userToken", "test-hash", "9999999999", "plantId", "farmlandPlotRegistryId"):
            assert secret not in encoded
    asyncio.run(run())


@pytest.mark.parametrize("name", ["maize", "MAIZE", "makka", "makki", "मक्का", "मका", "મકાઈ", "ਮੱਕੀ", "মকাই", "மக்காச்சோளம்", "మొక్కజొన్న", "ചോളം", "ମକା"])
def test_maize_aliases(setup, name):
    assert ffs.resolve_crop(name, setup.backend.crops) == ffs.MAIZE


@pytest.mark.parametrize("name", ["maiz", "maizee", "makkaa", "makkai"])
def test_maize_near_matches(setup, name):
    assert ffs.resolve_crop(name, setup.backend.crops) == ffs.MAIZE


def test_clear_other_crop_and_unrelated_input(setup):
    assert ffs.resolve_crop("wheat", setup.backend.crops) == "0101900"
    assert ffs.resolve_crop("गहू", setup.backend.crops) == "0101900"
    assert ffs.resolve_crop("banana spaceship", setup.backend.crops) is None
    assert ffs.resolve_crop("", setup.backend.crops) is None
    duplicates = [{"cropCode": "1000200", "cropName": "गवार"}, {"cropCode": "0701300", "cropName": "गवार"}]
    assert ffs.resolve_crop("गवार", duplicates) is None


def test_validate_all_plots_before_first_crop_write(setup):
    async def run():
        village, crops = selection(await login())
        crops[1].crop_name = "unknown spaceship"
        out = await ffs_get_entitlement(context("turn-3"), village, crops)
        assert out["status"] == "needs_clarification"
        assert setup.backend.count("/land/crop/add") == 0
    asyncio.run(run())


def test_partial_crop_update_reconciles_without_otp_or_duplicate_write(setup):
    async def run():
        village, crops = selection(await login())
        setup.backend.fail_after_write = True
        out = await ffs_get_entitlement(context("turn-3"), village, crops)
        assert out["status"] == "partial_crop_update"
        out = await ffs_get_entitlement(context("turn-4"), village, crops)
        assert out["status"] == "ok"
        assert setup.backend.count("/land/crop/add") == 2
        assert setup.backend.count("loginByFarmerId") == 1
        assert setup.backend.count("validateLoginByFarmerId") == 1
        assert setup.backend.count("/bharatVistar/applicantFetch") == 2
    asyncio.run(run())


def test_setup_resume_keeps_token_and_does_not_reverify_otp(setup):
    async def run():
        setup.backend.fail_path = "/bharatVistar/applicantFetch"
        await ffs_send_otp(context(), "99999999999", "27")
        out = await ffs_verify_otp(context("turn-2", otp="123456"))
        assert out["status"] == "setup_incomplete"
        out = await ffs_verify_otp(context("turn-3"))
        assert out["status"] == "ok"
        assert setup.backend.count("loginByFarmerId") == setup.backend.count("validateLoginByFarmerId") == 1
        assert setup.backend.count("getCurrentSeasonAndYear") == 1
    asyncio.run(run())


def test_otp_send_uncertainty_and_concurrency_deduplicate(setup):
    async def run():
        setup.backend.fail_path = "/loginByFarmerId"
        out = await ffs_send_otp(context(), "99999999999", "27")
        assert out["status"] == "unavailable"
        repeats = await asyncio.gather(*(ffs_send_otp(context("new"), "99999999999", "27") for _ in range(3)))
        assert all(x["status"] == "already_started" for x in repeats)
        assert setup.backend.count("loginByFarmerId") == 1
        assert (await ffs_send_otp(context("resend", query="hello"), "99999999999", "27", True))["status"] == "needs_clarification"
        assert (await ffs_send_otp(context("resend", query="resend the OTP"), "99999999999", "27", True))["status"] == "otp_sent"
    asyncio.run(run())


def test_native_digits_masked_before_model_and_wrong_owner_cannot_load_history(setup):
    async def run():
        await ffs_send_otp(context(), "९९९९९९९९९९९", "महाराष्ट्र")
        safe, otp, owner, state = await ffs.prepare_chat_turn("My OTP is १२३४५६", "session-1", USER)
        assert safe == "My OTP is [FFS OTP supplied]"
        assert otp.get_secret_value() == "123456" and state["otp_supplied"]
        deps = context(otp=otp.get_secret_value()).deps
        assert "123456" not in repr(deps) + deps.model_dump_json()
        assert "123456" not in json.dumps({"otp": otp}, default=str)
        with pytest.raises(HTTPException) as error:
            await ffs.prepare_chat_turn("show plots", "session-1", {"sub": "other"})
        assert error.value.status_code == 403
        setup.memory.values.pop((ffs.NAMESPACE, ffs.session_key("session-1")))
        with pytest.raises(HTTPException):
            await ffs.prepare_chat_turn("show old history", "session-1", {"sub": "other"})
    asyncio.run(run())


def test_wrong_otp_limit_and_expired_token(setup):
    async def run():
        await ffs_send_otp(context(), "99999999999", "27")
        for i in range(3):
            out = await ffs_verify_otp(context(str(i), otp="000000"))
            assert out["status"] == "invalid_otp"
        assert (await ffs_verify_otp(context("fourth", otp="123456")))["status"] == "needs_otp"
        assert setup.backend.count("validateLoginByFarmerId") == 3
        await ffs_send_otp(context("new", query="resend OTP"), "99999999999", "27", True)
        await ffs_verify_otp(context("verify-new", otp="123456"))
        state = setup.memory.values[(ffs.NAMESPACE, ffs.session_key("session-1"))]
        state["token_expires"] = time.time() - 1
        assert (await ffs_find_shops(context("expired")))["status"] == "needs_otp"
        assert setup.backend.count("loginByFarmerId") == 2
    asyncio.run(run())


def test_chat_ingress_masks_otp_and_checks_owner_before_history(setup, monkeypatch, caplog):
    """Load the actual router with offline auth/model construction, without external services."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("AZURE_GPT54_MINI_BASE_URL", "https://unused.invalid/v1")
    monkeypatch.setenv("AZURE_GPT54_MINI_API_KEY", "test")
    monkeypatch.setenv("AGRINET_GEMMA_BASE_URL", "http://unused.invalid/v1")
    monkeypatch.setenv("AGRINET_GEMMA_MODEL_NAME", "test")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    asyncio.run(ffs_send_otp(context(), "99999999999", "27"))
    user = dict(USER)
    auth = ModuleType("app.auth.jwt_auth")
    async def current_user():
        return user
    auth.get_current_user = current_user
    monkeypatch.setitem(sys.modules, "app.auth.jwt_auth", auth)
    root = Path(__file__).resolve().parents[1]
    package = ModuleType("app.routers")
    package.__path__ = [str(root / "app/routers")]
    monkeypatch.setitem(sys.modules, "app.routers", package)
    spec = importlib.util.spec_from_file_location("ffs_ingress_router", root / "app/routers/chat.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    history_reads, forwarded = [], []
    async def history(session):
        history_reads.append(session)
        return session, []
    async def stream(**kwargs):
        forwarded.append(kwargs)
        yield "data: ok\n\n"
    async def image_payload(image, query, session_id):
        return session_id, query, []
    monkeypatch.setattr(module, "get_session_history", history)
    monkeypatch.setattr(module, "stream_chat_messages", stream)
    monkeypatch.setattr(module, "prepare_image_analyze_payload", image_payload)
    app = FastAPI()
    app.include_router(module.router, prefix="/api")
    with TestClient(app) as client:
        response = client.get("/api/chat/", params={"query": "१२३४५६", "session_id": "session-1", "qid": "otp-turn"})
        assert response.status_code == 200
        assert forwarded[0]["query"] == "[FFS OTP supplied]"
        assert forwarded[0]["ffs_otp"].get_secret_value() == "123456"
        assert "123456" not in caplog.text and "१२३४५६" not in caplog.text
        image = client.post("/api/chat/analyze-image", data={"query": "१२३४५६", "session_id": "session-1"},
                            files={"image": ("test.png", b"image", "image/png")})
        assert image.status_code == 200
        assert image.headers["X-QID"] == forwarded[1]["qid"]
        assert forwarded[1]["query"] == "[FFS OTP supplied]"
        user["sub"] = "different-caller"
        assert client.get("/api/chat/", params={"query": "show plots", "session_id": "session-1"}).status_code == 403
        monkeypatch.setattr(ffs.settings, "ffs_base_url", "")
        assert client.get("/api/chat/", params={"query": "show plots", "session_id": "session-1"}).status_code == 403
        assert history_reads == ["session-1"]


def test_zero_offered_bags_and_gfr_fallback(setup):
    async def run():
        village, crops = selection(await login())
        setup.backend.gfr[0]["recommendations"][0]["offeredBags"] = 0
        del setup.backend.gfr[0]["recommendations"][1]["offeredBags"]
        out = await ffs_get_entitlement(context("turn-3"), village, crops)
        assert [p["bags"] for p in out["data"]["products"]] == [0, 3]
    asyncio.run(run())


def test_shop_territory_priority_partial_stock_and_widening_cursor(setup):
    async def run():
        village, crops = selection(await login())
        await ffs_get_entitlement(context("3"), village, crops)
        setup.backend.stock = {0: (20, 1), 1: (5, 30), 2: (30, 0), 3: (3, 6), 4: (10, 10), 5: (9, 9)}
        initial = await ffs_find_shops(context("4"))
        shops = initial["data"]["shops"]
        assert [p["name"] for p in shops] == ["Shop 0", "Shop 1", "Shop 4"]
        assert shops[0]["coverage"] == ["urea"] and shops[1]["coverage"] == ["DAP"]
        widened = await ffs_find_shops(context("5"), "widen")
        assert {p["name"] for p in widened["data"]["shops"]} == {"Shop 6", "Shop 7"}
        assert all(p["complete_supply"] and p["territory"] == "D" for p in widened["data"]["shops"])
        assert (await ffs_find_shops(context("6"), "more"))["data"]["shops"] == []
    asyncio.run(run())


def test_create_booking_requires_confirmation_and_submits_once(setup):
    async def run():
        village, crops = selection(await login())
        await ffs_get_entitlement(context("turn-3"), village, crops)
        shops = await ffs_find_shops(context("turn-4"))
        shop_ref = shops["data"]["shops"][0]["shop_ref"]

        preview = await ffs_create_booking(context("turn-5"), shop_ref)
        assert preview["status"] == "needs_confirmation"
        token = preview["data"]["confirmation_token"]

        mismatch = await ffs_create_booking(context("turn-6"), shop_ref, True, "wrong-token")
        assert mismatch["status"] == "needs_confirmation"
        assert setup.backend.count("createMultipleBookingV2") == 0

        booked = await ffs_create_booking(context("turn-7"), shop_ref, True, token)
        assert booked["status"] == "ok"
        assert booked["data"]["booking"]["is_valid"] is True
        assert booked["data"]["booking"]["bookings"][0]["booking_code"] == "PRC-TEST-001"
        assert setup.backend.count("createMultipleBookingV2") == 1
    asyncio.run(run())


def test_zero_entitlement_and_alternative_tabs_fail_without_inventing_bags(setup):
    async def run():
        village, crops = selection(await login())
        for row in setup.backend.gfr[0]["recommendations"]:
            row["offeredBags"] = 0
        out = await ffs_get_entitlement(context("3"), village, crops)
        assert out["status"] == "zero_entitlement"
        assert (await ffs_find_shops(context("4")))["status"] == "zero_entitlement"
        assert setup.backend.count("getRetailersByProductAvailabilityV4") == 0
        setup.backend.gfr.append(deepcopy(setup.backend.gfr[0]))
        crops[0].crop_name = "wheat"
        assert (await ffs_get_entitlement(context("5"), village, crops))["status"] == "unavailable"
    asyncio.run(run())


def test_invalid_key_missing_identity_and_unloaded_skill_make_no_request(setup, monkeypatch):
    async def run():
        monkeypatch.setattr(ffs.settings, "ffs_esa_key", SecretStr("bad-key"))
        assert (await ffs_send_otp(context(), "99999999999", "27"))["status"] == "unavailable"
        assert (await ffs_send_otp(context(owner=""), "99999999999", "27"))["status"] == "unavailable"
        assert (await ffs_send_otp(context(selective=True), "99999999999", "27"))["status"] == "unavailable"
        assert setup.backend.calls == []
    asyncio.run(run())


def test_owner_is_bound_to_bearer_not_public_subject():
    same_subject_other_token = {**USER, "auth_token_id": "another-verified-token"}
    assert ffs.owner_id(USER) != ffs.owner_id(same_subject_other_token)
    assert ffs.owner_id({"sub": USER["sub"]}) == ""


def agent_flow(setup, check_transport=True):
    steps = 0
    history = []
    session = "ffs-agent-" + uuid.uuid4().hex
    def model(messages, info):
        nonlocal steps
        steps += 1
        if steps == 1:
            assert {t.name for t in info.function_tools} == {"load_skill"}
            return ModelResponse(parts=[ToolCallPart("load_skill", {"skill": "ffs.booking"})])
        calls = {
            2: ("ffs_send_otp", {"farmer_id": "99999999999", "state": "Maharashtra"}),
            4: ("ffs_verify_otp", {}),
            6: ("ffs_get_entitlement", {"village_ref": "village-1", "crop_selections": [
                {"land_ref": "land-1", "crop_name": "makkaa"}, {"land_ref": "land-2", "crop_name": "maize"}]}),
            8: ("ffs_find_shops", {}),
        }
        assert {t.name for t in info.function_tools} == {"ffs_send_otp", "ffs_verify_otp", "ffs_get_entitlement", "ffs_find_shops", "ffs_create_booking"}
        if steps in calls:
            name, args = calls[steps]
            return ModelResponse(parts=[ToolCallPart(name, args)])
        return ModelResponse(parts=[TextPart("No booking has been placed.")])
    agent = Agent(FunctionModel(model), deps_type=FarmerContext, tools=[
        Tool(t, takes_ctx=True, sequential=True) for t in (ffs_send_otp, ffs_verify_otp, ffs_get_entitlement, ffs_find_shops, ffs_create_booking)
    ] + [SKILL_LOADER], prepare_tools=prepare_skill_tools)
    @agent.system_prompt(dynamic=True)
    def prompt(ctx):
        ctx.deps.skills.restore(ctx.messages)
        return render_prompt(ctx.deps.skills, "en", {})
    for turn, (query, otp) in enumerate([("book fertilizer", None), ("[FFS OTP supplied]", "123456"),
                                       ("maize on both plots", None), ("yes, find shops", None)], 1):
        deps = context(str(turn), session=session, otp=otp, query=query, selective=True).deps
        response = agent.run_sync(query, deps=deps, message_history=history)
        history = response.all_messages()
    if check_transport:
        assert setup.backend.count("loginByFarmerId") == 1
        assert setup.backend.count("/land/crop/add") == 2
        assert setup.backend.count("getRetailersByProductAvailabilityV4") >= 2
    assert re.search(r"(?<!\d)123456(?!\d)", str(history)) is None
    return session


def test_real_pydantic_loading_and_four_tool_http_chain(setup):
    agent_flow(setup)


@pytest.mark.skipif(not os.environ.get("FFS_MOCK_TEST_URL"), reason="Set FFS_MOCK_TEST_URL for verified local HTTP mock")
def test_running_mock_http_flow(setup, monkeypatch):
    base = os.environ["FFS_MOCK_TEST_URL"].rstrip("/")
    with urlopen(base + "/health", timeout=5) as response:
        health = json.load(response)
        assert response.headers.get("X-FFS-Mock") == "true"
    assert health["service"] == "ffs-mock" and health["live_forwarding"] is False and health["booking_enabled"] is False
    monkeypatch.setattr(httpx, "AsyncClient", setup.client_class)
    monkeypatch.setattr(ffs.settings, "ffs_base_url", base)
    monkeypatch.setattr(ffs.settings, "ffs_esa_key", SecretStr(os.environ["FFS_ESA_KEY"]))
    monkeypatch.setattr(ffs.settings, "ffs_esa_iv", SecretStr(os.environ["FFS_ESA_IV"]))
    real_cache = None
    if os.environ.get("FFS_REDIS_TEST_PORT"):
        from aiocache import Cache
        from aiocache.serializers import JsonSerializer
        real_cache = Cache(Cache.REDIS, endpoint="127.0.0.1", port=int(os.environ["FFS_REDIS_TEST_PORT"]),
                           serializer=JsonSerializer())
        monkeypatch.setattr(ffs, "cache", real_cache)
    async def run():
        session = "ffs-http-test-" + uuid.uuid4().hex
        view = await login(session)
        village, crops = selection(view)
        out = await ffs_get_entitlement(context("3", session), village, crops)
        assert out["status"] == "ok", out
        assert [p["bags"] for p in out["data"]["products"]] == [6, 2]
        shops = await ffs_find_shops(context("4", session))
        assert shops["status"] == "ok" and len(shops["data"]["shops"]) == 3
        assert shops["data"]["booking_enabled"] is True
        saved = await ffs.get_session(session)
        assert saved["token"] and len(saved["selection"]) == 2
        assert (await ffs_find_shops(context("4", session))) == shops
        with pytest.raises(HTTPException):
            await ffs.prepare_chat_turn("old plots", session, {"sub": "another-caller"})
        if real_cache:
            assert 0 < await real_cache.client.ttl(real_cache._build_key(ffs.session_key(session), namespace=ffs.NAMESPACE)) <= 1200
            await real_cache.close()
    asyncio.run(run())
    if real_cache:
        # A fresh client pool exercises persistence across independently-created services.
        real_cache = Cache(Cache.REDIS, endpoint="127.0.0.1", port=int(os.environ["FFS_REDIS_TEST_PORT"]),
                           serializer=JsonSerializer())
        monkeypatch.setattr(ffs, "cache", real_cache)
    agent_flow(setup, check_transport=False)
