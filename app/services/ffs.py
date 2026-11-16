"""FFS authentication, resumable crop mapping and fertilizer shop discovery."""
import asyncio
import base64
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Literal
import hashlib
import json
import math
import re
import time

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis.exceptions import RedisError

from app.config import get_default_httpx_timeout, settings
from app.core.cache import cache
from agents.ffs_crop_resolver import resolve_crops
from agents.ffs_product_resolver import resolve_products
from helpers.ffs_model_view import compact_ffs_result
from helpers.utils import to_ascii_digits
from helpers.ffs_tracing import log_http_response, observation, response_summary

USER = "/ufa-user-service/ufa-user/v1/api"
MASTER = "/ufa-master-service/ufa-master/v1/api"
APPROVAL = "/ufa-approval-service/ufa-approval-service/v1/api"
FERT = "/ufa-fertilizer-service/ufa-fertilizer/v1/api"
APPLICANT_FETCH = "/bharatVistar/applicantFetch"
NAMESPACE = "ffs-session"
LEVELS = ("V", "T", "D", "S")
PRODUCTS = {11: 1089, 12: 1005}
# State/UT master snapshot from FFS getStateLgdMaster, verified 2026-11-11.
# Codes are API identifiers; state-language interpretation belongs to the model.
STATE_CODES = json.loads((Path(__file__).resolve().parents[2] / "assets/data/ffs_states.json").read_text())
# Provider identifiers, not a language/alias matcher. Advertise only state-supported codes.
COMMON_CROPS = {"0101900": "wheat", "0101000": "maize", "0101700": "paddy (rice)"}


class CropSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    land_ref: str
    crop_name: str = Field(min_length=1, max_length=100)
    crop_code: str | None = Field(default=None, description="For wheat, maize or paddy, pass the code from common_crops to skip the specialist. Otherwise omit.")


class OrderItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product: str = Field(min_length=1, max_length=150, description="Farmer's fertilizer wording; preserve named variants.")
    product_id: int | None = Field(default=None, description="1089 for generic urea, 1005 for generic DAP; otherwise only a returned candidate ID. Omit to resolve another product.")
    bags: int = Field(strict=True, ge=1)

    @field_validator("bags", mode="before")
    @classmethod
    def numeric_bags(cls, value):
        # Some tool-call serializers quote JSON integers. This is scalar type
        # conversion only; number words/ASR interpretation stays with the model.
        return int(value) if isinstance(value, str) else value


def result(status, next_action, **data):
    return {"status": status, "data": data, "next_action": next_action, "source": "FFS"}


def session_key(session_id):
    return hashlib.sha256(session_id.encode()).hexdigest()


class FFSError(Exception):
    def __init__(self, status, next_action, provider_error=None):
        self.status, self.next_action = status, next_action
        self.provider_error = provider_error


async def get_session(session_id):
    return await cache.get(session_key(session_id), namespace=NAMESPACE) or {}


async def prepare_chat_turn(session_id):
    """Restore FFS workflow context; user text and API authentication stay unchanged."""
    if not settings.ffs_base_url:
        return {}
    state = await get_session(session_id)
    context = {"stage": state.get("stage", "not_started"),
               "authenticated": bool(state.get("token") and state.get("token_expires", 0) > time.time())} if state else {}
    if state:
        context["village_scope"] = deepcopy(state.get("village_scope", {"mode": "single_village"}))
    if all(k in state for k in ("profile", "crops", "season")):
        view = FFSService(session_id, "")
        view.state = state
        farmer = view.farmer_view()["data"]
        context["villages"] = farmer["villages"]
        context["common_crops"] = farmer["common_crops"]
        if state.get("entitlement"):
            context["entitlement"] = state["entitlement"]["data"]
        if state.get("shops", {}).get("last_result"):
            context["last_shops"] = compact_ffs_result(state["shops"]["last_result"])["data"]
        if state.get("order"):
            context["order"] = [{k: row[k] for k in ("product_id", "name", "group", "bags", "kg_per_bag")} for row in state["order"]]
        if state.get("selected_shop"):
            context["selected_shop"] = view._shop_view(state["selected_shop"])
    return context


class FFSService:
    def __init__(self, session_id, question_id):
        self.session_id, self.question_id = session_id, question_id
        self.state = {}
        self.client = None
        self.diagnostics = {"api_calls": 0, "decisions": []}

    async def save(self):
        await cache.set(session_key(self.session_id), self.state, ttl=1200, namespace=NAMESPACE)

    def note(self, event, **fields):
        # Decisions contain only local references, counts and fixed labels.
        events = self.diagnostics["decisions"]
        if len(events) < 100:
            events.append({"event": event, **fields})
        else:
            self.diagnostics["decisions_omitted"] = self.diagnostics.get("decisions_omitted", 0) + 1

    async def call(self, method, prefix, path, body=None, *, encrypted=False, params=None):
        from urllib.parse import urlsplit
        metadata = {"session_id": self.session_id, "question_id": self.question_id,
                    "channel": "chat", "method": method, "host": urlsplit(settings.ffs_base_url).hostname,
                    "path": prefix + path, "encrypted": encrypted}
        with observation("ffs.api:" + path, metadata=metadata) as diagnostic:
            try:
                return await self._call(method, prefix, path, body, encrypted=encrypted,
                                        params=params, diagnostic=diagnostic)
            except FFSError as error:
                diagnostic["workflow_status"] = error.status
                raise

    async def _call(self, method, prefix, path, body=None, *, encrypted=False, params=None, diagnostic):
        url = settings.ffs_base_url.rstrip("/") + prefix + path
        headers = {"X-Session-Id": self.session_id, "Content-Type": "application/json"}
        if path == "/authenticate/user/loginByFarmerId":
            headers.update({"isForMobile": "false", "appType": "APPLICANT", "platform": "ANDROID"})
        if not encrypted:
            headers["Authorization"] = "Bearer " + self.state["token"]
        if settings.ffs_app_version:
            headers["version"] = settings.ffs_app_version
        kwargs = {"params": params}
        if encrypted:
            key, iv = settings.ffs_esa_key.get_secret_value().encode(), settings.ffs_esa_iv.get_secret_value().encode()
            if len(key) != 32 or len(iv) != 16:
                raise FFSError("unavailable", "FFS login encryption is not configured correctly")
            payload = json.dumps(body, separators=(",", ":")).encode()
            kwargs["content"] = base64.b64encode(AES.new(key, AES.MODE_CBC, iv).encrypt(pad(payload, 16)))
        elif body is not None:
            kwargs["json"] = body
        diagnostic["headers"] = {key: headers[key] for key in ("isForMobile", "appType", "platform", "version") if key in headers}
        diagnostic["authorization_present"] = "Authorization" in headers
        diagnostic["recaptcha_present"] = "X-Recaptcha-Token" in headers
        self.diagnostics["api_calls"] += 1
        response = await self.client.request(method, url, headers=headers, **kwargs)
        log_http_response(response, session_id=self.session_id, question_id=self.question_id)
        response_data = None
        try:
            response_data = response.json()
        except ValueError:
            pass
        diagnostic.update(response_summary(response.status_code, response_data))
        if path == "/bharatVistar/createMultipleBookingV2":
            # Booking has its own structural outcome check. HTTP 500 alone is
            # not the outcome; a FAILED/null envelope guarantees no write.
            if response.status_code == 401:
                self.state.pop("token", None)
                self.state["stage"] = "login_expired"
                await self.save()
            self.last_envelope = response_data
            self.last_http_status = response.status_code
            return response_data
        provider_error = {"http_status": response.status_code}
        if isinstance(response_data, dict):
            provider_error.update({key: response_data[key] for key in ("code", "status", "message") if key in response_data})
        if response.status_code == 401:
            self.state.pop("token", None)
            self.state["stage"] = "login_expired"
            await self.save()
            raise FFSError("needs_otp", "FFS rejected the login. Explain its response and offer a new OTP", provider_error)
        if encrypted and "validate" in path and response.status_code in {400, 429}:
            raise FFSError("invalid_otp" if response.status_code == 400 else "needs_otp",
                           "Explain the FFS response; ask for the correct OTP or offer a resend", provider_error)
        if not response.is_success:
            raise FFSError("unavailable", "Explain the FFS response; do not repeat a write automatically", provider_error)
        if not isinstance(response_data, dict):
            raise FFSError("unavailable", "FFS returned an invalid response")
        data = response_data
        if not isinstance(data, dict) or data.get("code") != 200:
            raise FFSError("invalid_otp" if encrypted and "validate" in path and isinstance(data, dict) and data.get("code") == 400 else "unavailable",
                           "Explain the FFS response; ask for the correct OTP" if encrypted and "validate" in path else "Explain the FFS response; FFS could not complete this request", provider_error)
        self.last_envelope = data
        return data.get("data")

    def authenticated(self):
        if not self.state.get("token") or self.state.get("token_expires", 0) <= time.time():
            raise FFSError("needs_otp", "Ask whether the farmer wants to log in to FFS")

    def ready(self):
        self.authenticated()
        if not all(k in self.state for k in ("season", "profile", "crops")):
            raise FFSError("setup_incomplete", "Call ffs_verify_otp with no arguments to resume loading")

    async def send_otp(self, farmer_id, state_lgd_code, resend=False):
        farmer_id = re.sub(r"[\s-]", "", to_ascii_digits(farmer_id))
        state_code = to_ascii_digits(state_lgd_code).strip()
        if not re.fullmatch(r"\d{11}", farmer_id) or state_code not in STATE_CODES:
            return result("needs_clarification", "Use the farmer's 11-digit Farmer ID and a state LGD code from the tool's list; clarify unknown or ambiguous details")
        same = self.state.get("farmer_id") == farmer_id and self.state.get("state_code") == state_code
        if self.state.get("otp_turn") == self.question_id:
            self.note("otp_send_suppressed", reason="same_turn")
            return result("already_started", "An OTP request was already attempted this turn")
        restart_needed = (
            self.state.get("stage") == "login_expired"
            or (self.state.get("token") and self.state.get("token_expires", 0) <= time.time())
            or (not self.state.get("token") and self.state.get("otp_attempts", 0) > 0)
        )
        if same and self.state.get("stage") != "not_started" and not resend and not restart_needed:
            self.note("otp_send_suppressed", reason="already_started")
            return result("already_started", "OTP already requested; ask for it or offer an explicit resend if needed")
        self.note("otp_requested", state_code=state_code, resend=bool(resend))
        self.state = {"farmer_id": farmer_id, "state_code": state_code,
                      "village_scope": {"mode": "single_village"},
                      "stage": "otp_outcome_unknown",
                      "otp_turn": self.question_id, "otp_attempts": 0}
        await self.save()
        await self.call("POST", USER, "/authenticate/user/loginByFarmerId", {
            "userStateLgdCode": state_code, "userFarmerId": farmer_id}, encrypted=True)
        self.note("otp_send_accepted")
        self.state["stage"] = "otp_pending"
        await self.save()
        return result("otp_sent", "Ask for the six-digit SMS OTP")

    async def verify_otp(self, otp=None):
        otp = re.sub(r"[\s-]", "", to_ascii_digits(otp)) if otp is not None else None
        if not self.state.get("token"):
            if self.state.get("stage") not in {"otp_pending", "otp_outcome_unknown"}:
                return result("needs_otp", "Ask whether to send a new OTP")
            if not otp or not re.fullmatch(r"\d{6}", to_ascii_digits(otp)):
                return result("needs_otp", "Ask the farmer for the six-digit SMS OTP")
            if self.state.get("verify_turn") == self.question_id:
                return result("invalid_otp", "Ask for a new OTP entry; this attempt was already processed")
            self.state["verify_turn"] = self.question_id
            self.note("otp_verification_attempted", previous_failures=self.state["otp_attempts"])
            await self.save()
            try:
                data = await self.call("POST", USER, "/authenticate/user/validateLoginByFarmerId", {
                    "userStateLgdCode": self.state["state_code"], "userFarmerId": self.state["farmer_id"],
                    "otp": to_ascii_digits(otp)}, encrypted=True)
            except FFSError as error:
                if error.status == "invalid_otp":
                    self.state["otp_attempts"] += 1
                    await self.save()
                    if self.state["otp_attempts"] >= 3:
                        error.next_action = "Three OTP verifications failed. Explain the FFS response and offer a resend; do not send automatically"
                raise
            if not isinstance(data, dict) or not data.get("userToken") or not data.get("userId") or not data.get("userAadhaarHash"):
                raise FFSError("unavailable", "FFS login returned an incomplete profile")
            try:
                claims = json.loads(base64.urlsafe_b64decode(data["userToken"].split(".")[1] + "==="))
                expires = float(claims["exp"])
                if not math.isfinite(expires) or expires <= time.time():
                    raise ValueError()
            except (ValueError, KeyError, IndexError, TypeError):
                raise FFSError("unavailable", "FFS login returned an invalid token expiry")
            self.state.update(token=data["userToken"], token_expires=expires,
                              user={k: data.get(k) for k in ("userId", "userAadhaarHash", "userFullName", "userMobileNumber")}, stage="loading")
            self.note("otp_verified")
            self.state.pop("verify_turn", None)
            await self.save()
        self.authenticated()
        s = self.state
        for name, prefix, path, method, body, params in [
            ("season", MASTER, "/common/getCurrentSeasonAndYear", "GET", None, None),
            ("profile", APPROVAL, APPLICANT_FETCH, "POST", "profile", None),
            ("crops", MASTER, "/common/getCropListByState", "GET", None, {"stateLgdCode": s["state_code"]}),
        ]:
            if name in s:
                self.note("setup_step_reused", step=name)
                continue
            self.note("setup_step_fetch", step=name)
            if body == "profile":
                body = {"stateCode": int(s["state_code"]), "aadhaarHash": s["user"]["userAadhaarHash"],
                        "seasonId": s["season"]["season"]["seasonId"], "yearId": s["season"]["year"]["yearId"]}
            # Public reference data is shared per provider/state, never farmer-specific.
            crop_cache_key = hashlib.sha256(settings.ffs_base_url.encode()).hexdigest() + ":" + s["state_code"]
            data = await cache.get(crop_cache_key, namespace="ffs-crop-catalog") if name == "crops" else None
            cached_crops = name == "crops" and data is not None
            if not cached_crops:
                data = await self.call(method, prefix, path, body, params=params)
            if name == "profile":
                valid = isinstance(data, dict) and isinstance(data.get("landData"), list)
                if valid:
                    rows = data["landData"]
                    ids = ("farmlandPlotRegistryId", "farmlandOwnershipId", "stateLgdCode", "districtLgdCode",
                           "subDistrictLgdCode", "villageLgdCode")
                    valid = all(isinstance(l, dict) and all(type(l.get(k)) is int and l[k] > 0 for k in ids)
                                and isinstance(l.get("crops"), list) and all(isinstance(c, dict) for c in l["crops"])
                                and str(l["stateLgdCode"]) == s["state_code"] for l in rows)
                    if valid:
                        valid = len({l["farmlandPlotRegistryId"] for l in rows}) == len(rows)
            elif name == "season":
                valid = isinstance(data, dict) and all(isinstance(data.get(k), dict) and
                    type(data[k].get(k + "Id")) is int and data[k][k + "Id"] > 0 for k in ("season", "year"))
            else:
                valid = isinstance(data, list) and all(isinstance(row, dict) for row in data)
                if valid and name == "crops":
                    valid = all(isinstance(row.get("cropCode"), str) and isinstance(row.get("cropName"), str) for row in data)
            if not valid:
                envelope = getattr(self, "last_envelope", {})
                details = {key: envelope[key] for key in ("code", "status", "message") if key in envelope}
                if isinstance(data, dict) and data.get("message"):
                    details["message"] = data["message"]
                raise FFSError("setup_incomplete", "Explain the FFS response. Setup can resume through ffs_verify_otp without another OTP", details)
            if name == "crops":
                data = [{"cropCode": row["cropCode"], "cropName": row["cropName"]} for row in data]
                if not cached_crops:
                    await cache.set(crop_cache_key, data, ttl=86400, namespace="ffs-crop-catalog")
                self.note("crop_catalog", cached=cached_crops, count=len(data))
            s[name] = data
            await self.save()
        if s["stage"] == "loading":
            s["stage"] = "farmer_ready"
        await self.save()
        return self.farmer_view()

    def land_map(self):
        return {f"land-{i + 1}": l for i, l in enumerate(self.state["profile"]["landData"])}

    def villages(self):
        return {f"village-{i + 1}": code for i, code in enumerate(dict.fromkeys(
            str(l["villageLgdCode"]) for l in self.land_map().values()))}

    def farmer_view(self):
        s, villages = self.state, []
        for ref, code in self.villages().items():
            plots = []
            for land_ref, land in self.land_map().items():
                if str(land["villageLgdCode"]) != code:
                    continue
                plots.append({"land_ref": land_ref, "survey": str(land.get("surveyNumber", "")),
                              "sub_survey": str(land.get("subSurveyNumber", "")), "area_ha": land.get("landAreaHectares")})
            villages.append({"village_ref": ref, "name": next(l.get("villageName", "") for l in self.land_map().values()
                             if str(l["villageLgdCode"]) == code), "plots": plots})
        supported = {row["cropCode"] for row in s["crops"]}
        return result("ok" if villages else "no_lands", "Explain that this booking flow supports one village at a time. Ask only for missing village and crops" if villages else
                      "Explain that FFS returned no land records", farmer_name=s["user"].get("userFullName"), villages=villages,
                      village_scope=deepcopy(s.get("village_scope", {"mode": "single_village"})),
                      common_crops=[{"crop_name": name, "crop_code": code} for code, name in COMMON_CROPS.items() if code in supported])

    async def entitlement(self, village_ref, crop_selections=None, crop_name=None, crop_code=None, selected_plots_only=False):
        self.ready()
        s, lands = self.state, self.land_map()
        village = self.villages().get(village_ref)
        village_lands = {ref for ref, land in lands.items() if str(land["villageLgdCode"]) == village}
        if not village or not village_lands:
            return result("needs_clarification", "Choose a returned village with land records")
        if crop_selections is not None and (crop_name is not None or crop_code is not None):
            consistent = all(
                (crop_code is not None and item.crop_code == crop_code)
                or (item.crop_name == crop_name and (crop_code is None or item.crop_code in {None, crop_code}))
                for item in crop_selections)
            if not consistent:
                return result("needs_clarification", "The village crop and per-plot mappings conflict. Supply the farmer's intended per-plot mappings")
            crop_selections = [item.model_copy(update={"crop_code": item.crop_code or crop_code}) for item in crop_selections]
            crop_name = crop_code = None
        if crop_name is not None:
            if crop_selections is not None or selected_plots_only or not crop_name.strip():
                return result("needs_clarification", "For one crop on the whole village, supply crop_name and optional crop_code only; for fewer plots use crop_selections and selected_plots_only=true")
            crop_selections = [CropSelection(land_ref=ref, crop_name=crop_name, crop_code=crop_code)
                               for ref in lands if ref in village_lands]
        elif crop_code is not None:
            return result("needs_clarification", "Supply the farmer's crop wording with the common crop code")
        if not crop_selections or len({x.land_ref for x in crop_selections}) != len(crop_selections):
            return result("needs_clarification", "Supply one crop for the village or one crop mapping per selected plot")
        requested = {item.land_ref for item in crop_selections}
        if not requested <= village_lands:
            return result("needs_clarification", "This booking flow supports one village at a time. Explain this and ask which village to use; do not call separately and add allowances. A new village selection replaces the previous one. No selection was changed",
                          village_scope=deepcopy(s.get("village_scope", {"mode": "single_village"})))
        if not selected_plots_only and requested != village_lands:
            missing = [{"land_ref": ref, "survey": str(lands[ref].get("surveyNumber", ""))}
                       for ref in lands if ref in village_lands - requested]
            return result("needs_clarification", "Collect the missing crop mappings. Use selected_plots_only=true only if the farmer explicitly requested fewer plots", missing_plots=missing)
        codes = {row["cropCode"]: row["cropName"] for row in s["crops"]}
        for item in crop_selections:
            land = lands.get(item.land_ref)
            area = land.get("landAreaHectares")
            if type(area) not in (int, float) or not math.isfinite(area) or area <= 0:
                return result("unavailable", "FFS returned an invalid plot area; do not save crops or estimate quantities")
            if item.crop_code is not None and (item.crop_code not in COMMON_CROPS or item.crop_code not in codes):
                return result("needs_clarification", "Use a code from common_crops, or omit crop_code to resolve the farmer's crop wording")
        supplied = sum(item.crop_code is not None for item in crop_selections)
        if supplied:
            self.note("common_crop_codes_used", plots=supplied)
        names = list(dict.fromkeys(item.crop_name for item in crop_selections if item.crop_code is None))
        resolved = s.setdefault("resolved_crops", {})
        for name in {item.crop_name for item in crop_selections if item.crop_code is not None}:
            supplied_codes = {item.crop_code for item in crop_selections if item.crop_name == name and item.crop_code is not None}
            if len(supplied_codes) == 1:
                resolved[name] = next(iter(supplied_codes))
            else:
                resolved.pop(name, None)
        missing = [name for name in names if resolved.get(name) not in codes]
        if missing:
            try:
                matches = await resolve_crops(missing, s["crops"], state_code=s["state_code"],
                                              session_id=self.session_id, question_id=self.question_id)
            except Exception as error:
                self.note("crop_resolution_failed", error_type=type(error).__name__)
                return result("unavailable", "Crop resolution failed; no crops were saved. Retry without restarting OTP login")
            unresolved = []
            for name, match in zip(missing, matches):
                if match.status == "matched":
                    resolved[name] = match.crop_code
                else:
                    unresolved.append({"crop_name": name, "status": match.status,
                        "candidates": [{"crop_code": code, "crop_name": codes[code]} for code in match.candidates]})
            await self.save()
            if unresolved:
                return result("needs_clarification", "Ask the farmer which intended crop they mean using these candidates; do not default to maize",
                              crops=unresolved)
        elif names:
            self.note("crop_resolution_reused", count=len(names))
        selected = [{"land_ref": item.land_ref, "crop_code": item.crop_code or resolved[item.crop_name]} for item in crop_selections]
        signature = json.dumps([village_ref, selected], sort_keys=True)
        if s.get("selection_signature") == signature and "entitlement" in s:
            self.note("entitlement_reused")
            return deepcopy(s["entitlement"])
        previous_scope = s.get("village_scope", {})
        scope = {"mode": "single_village", "village_ref": village_ref,
                 "village_name": lands[selected[0]["land_ref"]].get("villageName", "")}
        if previous_scope.get("village_ref") and previous_scope["village_ref"] != village_ref:
            scope["replaces_village"] = {k: previous_scope[k] for k in ("village_ref", "village_name")}
        for key in ("entitlement", "shops", "selected_shop", "products", "gfr", "quota", "pending_booking",
                    "booking_limits", "booking_tool_exchanges", "order", "product_catalog_groups",
                    "resolved_products", "product_candidates"):
            s.pop(key, None)
        s["village_scope"] = scope
        s["selection_signature"], s["selection"] = signature, selected
        s["stage"] = "mapping_crops"
        await self.save()
        if s.get("uncertain_plot"):
            self.note("crop_mapping_reconciliation")
            fresh = await self.call("POST", APPROVAL, APPLICANT_FETCH, {
                "stateCode": int(s["state_code"]), "aadhaarHash": s["user"]["userAadhaarHash"],
                "seasonId": s["season"]["season"]["seasonId"], "yearId": s["season"]["year"]["yearId"]})
            if not isinstance(fresh, dict) or not isinstance(fresh.get("landData"), list):
                raise FFSError("partial_crop_update", "Land refresh failed; crop update outcome remains unknown")
            current = {str(l["farmlandPlotRegistryId"]): l for l in fresh["landData"]}
            for item in selected:
                land = lands[item["land_ref"]]
                if str(land["farmlandPlotRegistryId"]) not in current:
                    raise FFSError("partial_crop_update", "A selected plot is no longer returned by FFS. Explain this and ask which returned plots the farmer wants")
                land["crops"] = current[str(land["farmlandPlotRegistryId"])].get("crops", [])
            s.pop("uncertain_plot")
            await self.save()
        for item in selected:
            land = lands[item["land_ref"]]
            season, year = s["season"]["season"]["seasonId"], s["season"]["year"]["yearId"]
            if any(c.get("cropCode") == item["crop_code"] and c.get("seasonId") == season and c.get("yearId") == year for c in land.get("crops", [])):
                self.note("crop_mapping_reused", land_ref=item["land_ref"])
                continue
            self.note("crop_mapping_write", land_ref=item["land_ref"],
                      crop_code=item["crop_code"] if re.fullmatch(r"[0-9]{1,12}", item["crop_code"]) else "invalid")
            s["uncertain_plot"] = item["land_ref"]
            await self.save()
            data = await self.call("POST", APPROVAL, "/applicant/land/crop/add", {
                "applicantUserId": s["user"]["userId"], "farmlandPlotRegistryId": land["farmlandPlotRegistryId"],
                "farmlandOwnershipRegistryId": land["farmlandOwnershipId"], "cropCode": item["crop_code"],
                "seasonId": season, "yearId": year, "stateLgdCode": int(s["state_code"])})
            if not isinstance(data, dict) or data.get("cropCode") != item["crop_code"] or data.get("farmlandPlotRegistryId") != land["farmlandPlotRegistryId"]:
                raise FFSError("partial_crop_update", "FFS crop result did not match this plot; refetch before retrying")
            self.note("crop_mapping_saved", land_ref=item["land_ref"])
            land["crops"] = [data]
            s.pop("uncertain_plot", None)
            await self.save()
        recommendations = await self.call("POST", FERT, "/fertilizerRecommendation/getRecommendationsByGFRV4", {
            "lands": [{"farmlandPlotRegistryId": lands[x["land_ref"]]["farmlandPlotRegistryId"],
                       "areaHa": lands[x["land_ref"]]["landAreaHectares"], "cropCode": x["crop_code"],
                       "stateLgdCode": int(s["state_code"]), "districtLgdCode": lands[x["land_ref"]]["districtLgdCode"]} for x in selected],
            "isProceedWithGfr": True})
        if not isinstance(recommendations, list) or len(recommendations) != 1 or not isinstance(recommendations[0], dict):
            raise FFSError("unavailable", "FFS returned unsupported GFR alternatives; do not combine them")
        gfr = recommendations[0]
        groups = []
        for r in gfr.get("recommendations", []):
            if r.get("productGroupId") not in PRODUCTS:
                continue
            bags = r.get("offeredBags")
            weight = r.get("kgPerBag")
            if type(bags) is not int or bags < 0:
                raise FFSError("unavailable", "FFS did not return usable offeredBags; do not infer an allowance from required bags or weight")
            self.note("entitlement_quantity", group=r["productGroupId"], bags=bags,
                      quantity_field="offeredBags")
            groups.append({"group": r["productGroupId"], "name": "urea" if r["productGroupId"] == 11 else "DAP",
                           "bags": bags, "kg_per_bag": weight})
        if len({g["group"] for g in groups}) != len(groups):
            raise FFSError("unavailable", "FFS did not return supported fertilizer groups")
        s.update(gfr=gfr, stage="entitlement_ready")
        s["entitlement"] = result("ok" if any(type(r.get("offeredBags")) is int and r["offeredBags"] > 0 for r in gfr.get("recommendations", [])) else "zero_entitlement",
            "Name the selected village and explain that this allowance covers only the returned plots in that village. If replaces_village is present, explain that this selection replaces the previous village, not adds to it. Never add allowances from separate calls. Report bags exactly as the FFS offered allowance; do not subtract purchases or recalculate it. Collect the requested eligible products and bag counts, reusing earlier requests; never default to entitlement. Confirm the order before shop search. If it exceeds GFR, explain the returned limit and ask for a smaller choice",
            village_scope=deepcopy(scope),
            products=groups, plots=[x["land_ref"] for x in selected],
            crops=[{"land_ref": x["land_ref"], "crop": next(c.get("cropName") for c in s["crops"] if c["cropCode"] == x["crop_code"])} for x in selected])
        await self.save()
        return deepcopy(s["entitlement"])

    async def _load_products(self, groups, geo):
        """Location-specific mappings, reused only for the same selected lands."""
        groups = sorted(set(groups) | set(self.state.get("product_catalog_groups", [])))
        if groups == self.state.get("product_catalog_groups"):
            return
        data = await self.call("POST", MASTER, "/product/getProductsByAvailability", {
            **geo, "productGroupIds": ",".join(map(str, groups))})
        if not isinstance(data, list) or not all(isinstance(p, dict) for p in data):
            raise FFSError("unavailable", "FFS returned an invalid product catalogue")
        self.state["products"] = data
        self.state["product_catalog_groups"] = groups
        await self.save()

    async def _set_order(self, order, geo):
        s = self.state
        # Invalidate the old order immediately: an incomplete correction must not
        # leave a previously valid order available to the booking tool.
        previous = s.pop("order", None)
        previous_search = s.pop("shops", None)
        await self.save()
        if not order:
            return result("needs_order", "Ask which products and how many bags the farmer wants; never use eligibility as the order")
        common_groups = {pid: group for group, pid in PRODUCTS.items()}
        # Discovery also needs urea/DAP metadata for the agreed shop ranking.
        # Loading those products does not add them to the farmer's order.
        groups = set(PRODUCTS)
        if any(item.product_id not in common_groups for item in order):
            groups.update(r["productGroupId"] for r in s["gfr"]["recommendations"])
        await self._load_products(groups, geo)
        catalogue = [p for p in s["products"] if type(p.get("productId")) is int
                     and type(p.get("productGroupId")) is int and p.get("productName")
                     and type(p.get("bagWeightKg")) in (int, float) and math.isfinite(p["bagWeightKg"])
                     and p["bagWeightKg"] > 0]
        ids = {p["productId"] for p in catalogue}
        allowed_ids = set(common_groups) | set(s.get("product_candidates", [])) | set(s.get("resolved_products", {}).values())
        if any(item.product_id is not None and item.product_id not in allowed_ids for item in order):
            return result("needs_clarification", "Use 1089/1005 for generic urea/DAP, a returned candidate ID, or omit the ID to resolve the farmer's named product")
        resolved = s.setdefault("resolved_products", {})
        missing = list(dict.fromkeys(item.product for item in order if item.product_id is None and resolved.get(item.product) not in ids))
        if missing:
            try:
                matches = await resolve_products(missing, catalogue, session_id=self.session_id, question_id=self.question_id)
            except Exception as error:
                self.note("product_resolution_failed", error_type=type(error).__name__)
                return result("unavailable", "Product matching failed; retry without restarting login. No order or booking was submitted")
            unresolved = []
            for name, match in zip(missing, matches):
                if match.status == "matched":
                    resolved[name] = match.product_id
                else:
                    candidates = [{"product_id": p["productId"], "name": p["productName"],
                                   "group": p["productGroupId"], "kg_per_bag": p["bagWeightKg"]}
                                  for p in catalogue if p["productId"] in match.candidates]
                    s["product_candidates"] = sorted(set(s.get("product_candidates", [])) | set(match.candidates))
                    unresolved.append({"product": name, "status": match.status, "candidates": candidates})
            await self.save()
            if unresolved:
                return result("needs_clarification", "Clarify ambiguous products using the candidates. If not found, explain that FFS did not return a match for this location and eligible groups. Never substitute a named variant", products=unresolved)
        chosen = []
        for item in order:
            pid = item.product_id if item.product_id is not None else resolved[item.product]
            rows = [p for p in catalogue if p["productId"] == pid]
            mappings = {(p.get("plantId"), p["productGroupId"], p["bagWeightKg"]) for p in rows}
            if not rows:
                group = common_groups.get(pid)
                alternatives = [{"product_id": p["productId"], "name": p["productName"], "kg_per_bag": p["bagWeightKg"]}
                                for p in catalogue if p["productGroupId"] == group]
                s["product_candidates"] = sorted(set(s.get("product_candidates", [])) | {p["product_id"] for p in alternatives})
                await self.save()
                return result("product_unavailable", "FFS did not return the requested product locally. Offer named alternatives only with the farmer's choice; never substitute", product=item.product, alternatives=alternatives)
            product = rows[0]
            if len(mappings) != 1 or type(product.get("plantId")) is not int or product["plantId"] <= 0:
                return result("unavailable", "FFS returned missing or ambiguous product/plant mappings; do not invent a plant ID")
            chosen.append({"product_id": pid, "name": product["productName"], "group": product["productGroupId"],
                           "bags": item.bags, "kg_per_bag": product["bagWeightKg"], "plant_id": product["plantId"]})
        if len({p["product_id"] for p in chosen}) != len(chosen):
            return result("needs_order", "Specify each requested product once with its total requested bags")
        error = self._check_order_allowance(chosen)
        if error:
            return error
        chosen.sort(key=lambda p: p["product_id"])
        if chosen == previous and previous_search is not None:
            s["shops"] = previous_search
        s["order"] = chosen
        s["order_turn"] = self.question_id
        await self.save()
        return None

    def _check_order_allowance(self, order):
        recommendations = self.state["gfr"]["recommendations"]
        for group in {p["group"] for p in order}:
            rows = [r for r in recommendations if r.get("productGroupId") == group]
            if not rows:
                return result("not_eligible", "FFS returned no allowance for this product group; ask for a different eligible product", group=group)
            if len(rows) != 1 or type(rows[0].get("offeredBags")) is not int or rows[0]["offeredBags"] < 0:
                return result("unavailable", "FFS did not return an unambiguous offeredBags allowance for this group")
            allowed = rows[0]["offeredBags"]
            requested = sum(p["bags"] for p in order if p["group"] == group)
            if requested > allowed:
                return result("needs_order", "We need to follow the GFR recommendation; explain the returned allowed bags and ask for a lower number. Never change the farmer's quantity yourself",
                              group=group, allowed_bags=allowed, requested_bags=requested)
        return None

    async def find_shops(self, action="initial", order=None, shop_ref=None):
        self.ready()
        s = self.state
        if not s.get("entitlement"):
            raise FFSError("needs_entitlement", "Collect village/crops and call ffs_get_entitlement first")
        first = self.land_map()[s["selection"][0]["land_ref"]]
        geo = {k: first[k] for k in ("stateLgdCode", "districtLgdCode", "subDistrictLgdCode", "villageLgdCode")}
        if shop_ref is not None:
            shop = self._find_shop(shop_ref)
            if shop is None:
                return result("needs_shops", "Use the chosen shop's returned shop_ref, never its display number")
            s["selected_shop"] = deepcopy(shop)
        if order is not None:
            previous_order = deepcopy(s.get("order"))
            error = await self._set_order(order, geo)
            if error:
                return error
            if s["order"] != previous_order:
                action = "initial"
        if not s.get("order"):
            return result("needs_order", "Ask for the products and bag counts the farmer wants; never default to entitlement")
        selected = s.get("selected_shop")
        if order is not None and action == "initial" and selected:
            answer = result("ok", "Order updated; keep the chosen shop. Submit the farmer's agreed correction using this shop_ref. FFS checks current stock at booking; never silently switch retailer",
                            selected_shop=self._shop_view(selected))
            s["stage"] = "shops_ready"
            await self.save()
            return answer
        # This is an availability probe, not a proposed booking. Asking for one
        # bag keeps smaller-stock shops discoverable. Include common products
        # for ranking, but count shops against the separately stored order below.
        discovery = {p["productId"]: {"productGroupId": p["productGroupId"],
                     "productId": p["productId"], "quantityOfBagKg": p["bagWeightKg"],
                     "noBagsRequested": 1} for p in s.get("products", [])
                     if p.get("productId") in PRODUCTS.values()
                     and type(p.get("bagWeightKg")) in (int, float)
                     and math.isfinite(p["bagWeightKg"]) and p["bagWeightKg"] > 0}
        discovery.update({p["product_id"]: {"productGroupId": p["group"],
                          "productId": p["product_id"], "quantityOfBagKg": p["kg_per_bag"],
                          "noBagsRequested": 1} for p in s["order"]})
        chosen = list(discovery.values())
        search = s.setdefault("shops", {"level": 0, "page": 1, "pool": {}, "shown": [], "complete_levels": [], "turn_results": {}})
        turn_key = self.question_id + ":" + action
        if turn_key in search["turn_results"]:
            return deepcopy(search["turn_results"][turn_key])
        if action == "initial" and search["shown"]:
            return deepcopy(search["last_result"])
        if action == "widen":
            if search["level"] >= len(LEVELS) - 1:
                return result("no_more_shops", "The search is already at state level")
            search["level"] += 1
            search["page"] = 1
            search["minimum_level"] = search["level"]
        await self._collect_shop_pages(search, geo, chosen)
        answer = self._shop_result(search)
        search["last_result"] = answer
        search["turn_results"][turn_key] = answer
        s["stage"] = "shops_ready"
        await self.save()
        return deepcopy(answer)

    @staticmethod
    def _pending_shops(search):
        return [shop for shop in search["pool"].values() if shop["shop_ref"] not in search["shown"]
                and shop["coverage"]
                and shop["rank"][0] >= search.get("minimum_level", 0)]

    async def _collect_shop_pages(self, search, geo, chosen):
        # Count distinct shops carrying an ordered product, not raw retailer rows.
        # Keep paging this territory before widening, stopping as soon as we have
        # three choices. The run timeout bounds calls; every page saves its cursor.
        while len(self._pending_shops(search)) < 3:
            while LEVELS[search["level"]] in search["complete_levels"] and search["level"] < len(LEVELS) - 1:
                search["level"] += 1
                search["page"] = 1
            if LEVELS[search["level"]] in search["complete_levels"]:
                break
            self.note("shop_page_requested", territory=LEVELS[search["level"]], page=search["page"])
            data = await self.call("POST", MASTER, "/retailer/getRetailersByProductAvailabilityV4", {
                **geo, "selectedProducts": chosen, "territoryLevel": LEVELS[search["level"]],
                "farmerLatitude": None, "farmerLongitude": None, "pageNo": search["page"], "pageSize": 3})
            pages = self.last_envelope.get("totalPages")
            if not isinstance(data, list) or type(pages) is not int or pages < 0:
                raise FFSError("unavailable", "FFS returned invalid retailer pagination")
            for row in data:
                self._add_retailer(search, row)
            search["page"] += 1
            if search["page"] > pages:
                search["complete_levels"].append(LEVELS[search["level"]])
            await self.save()

    def _add_retailer(self, search, row):
        if not isinstance(row, dict) or type(row.get("retailerId")) is not int:
            raise FFSError("unavailable", "FFS returned an invalid retailer")
        rid = str(row["retailerId"])
        if rid in search["pool"]:
            return
        stocks, groups_with_stock = {}, set()
        known_groups = {p["productId"]: p["productGroupId"] for p in self.state.get("products", [])}
        known_groups.update({pid: group for group, pid in PRODUCTS.items()})
        for group in row.get("availableProducts", []):
            for product in group.get("products", []):
                pid, stock = product.get("productId"), product.get("noBagsAvailable")
                if type(stock) in (int, float) and math.isfinite(stock) and stock >= 0:
                    stocks[pid] = stock
                    if stock > 0:
                        groups_with_stock.add(group.get("productGroupId", known_groups.get(pid)))
        order = self.state["order"]
        complete = all(stocks.get(p["product_id"], -1) >= p["bags"] for p in order)
        unknown = any(p["product_id"] not in stocks for p in order)
        supply = "full" if complete else "unknown" if unknown else "partial"
        # Within a supply class: both urea/DAP, then urea, then DAP,
        # then other shops; descending reported volumes within each bucket.
        bucket = 0 if {11, 12} <= groups_with_stock else 1 if 11 in groups_with_stock else 2 if 12 in groups_with_stock else 3
        search["pool"][rid] = {"shop_ref": "retailer-" + rid,
            "name": row.get("retailerName", ""), "territory": LEVELS[search["level"]],
            "coverage": [{"product_id": p["product_id"], "name": p["name"]} for p in order if stocks.get(p["product_id"], 0) > 0],
            "supply": supply, "retailer_id": row["retailerId"], "stock": {str(k): v for k, v in stocks.items()},
            "rank": [search["level"], bucket, -sum(stocks.values()), row["retailerId"]]}
        selected = self.state.get("selected_shop")
        if selected and selected["retailer_id"] == row["retailerId"]:
            search["pool"][rid]["shop_ref"] = selected["shop_ref"]
            self.state["selected_shop"] = deepcopy(search["pool"][rid])

    @staticmethod
    def _shop_view(shop):
        # This is the selected retailer's identity, not a fresh availability check.
        return {k: deepcopy(shop[k]) for k in ("shop_ref", "name", "territory")}

    def _find_shop(self, shop_ref):
        selected = self.state.get("selected_shop")
        if selected and selected["shop_ref"] == shop_ref:
            return selected
        return next((shop for shop in self.state.get("shops", {}).get("pool", {}).values()
                     if shop["shop_ref"] == shop_ref), None)

    def _shop_result(self, search):
        # Apply full-order supply before product buckets, including cached v8 pools.
        # Territory remains first; do not renumber shops already shown.
        batch = sorted(self._pending_shops(search),
                       key=lambda x: (x["rank"][0], x["supply"] != "full", *x["rank"][1:]))[:3]
        search["shown"].extend(shop["shop_ref"] for shop in batch)
        exhausted = LEVELS[-1] in search["complete_levels"]
        shops = [{"number": i + 1, **{k: shop[k] for k in ("shop_ref", "name", "territory", "coverage", "supply")}}
                 for i, shop in enumerate(batch)]
        return result("ok" if batch else "no_more_shops" if exhausted else "search_incomplete",
                      "Present these numbered shops with their territory and ordered product availability. Explain partial or unknown supply without stock volumes. Never change the order. Offer more only when has_more is true",
                      shops=shops, has_more=bool(self._pending_shops(search)) or not exhausted)

    async def create_booking(self, shop_ref, confirmed=False):
        s = self.state
        self.ready()
        if not s.get("order"):
            return result("needs_order", "Ask for products and bags, then pass the farmer's order to ffs_find_shops before booking")
        shop = self._find_shop(shop_ref)
        if not shop:
            return result("needs_shops", "Search for the stored order and ask the farmer to choose a returned shop")
        s["selected_shop"] = deepcopy(shop)
        await self.save()
        self.note("booking_consent", confirmed=bool(confirmed))
        if not confirmed:
            self.note("booking_confirmation_requested")
            return result("needs_confirmation", "Ask once whether to book these products and quantities at this shop. If the farmer has already confirmed, call with confirmed=true now; no token or extra turn is required",
                          summary={"shop_ref": shop_ref, "shop_name": shop.get("name", ""),
                                   "products": [{k: p[k] for k in ("name", "bags", "kg_per_bag")} for p in s["order"]]},
                          booking_enabled=True)
        return await self._submit_booking(shop, s["order"], self._booking_lands())

    def _booking_lands(self):
        selected_lands = []
        for item in self.state.get("selection", []):
            land = self.land_map().get(item["land_ref"])
            if not land:
                raise FFSError("unavailable", "Selected plot details are missing; refresh entitlement")
            selected_lands.append({"land_ref": item["land_ref"], "survey": str(land.get("surveyNumber", "")),
                                   "farmLandPlotRegistryId": str(land["farmlandPlotRegistryId"]),
                                   "farmLandOwnershipId": str(land["farmlandOwnershipId"]),
                                   "cropCode": item["crop_code"]})
        if not selected_lands:
            raise FFSError("needs_entitlement", "Select plots and get entitlement before booking")
        return selected_lands

    async def _submit_booking(self, shop, chosen, selected_lands):
        s = self.state
        mobile = s["user"].get("userMobileNumber")
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        lines = [{"productId": x["product_id"], "plantId": int(x["plant_id"]),
                  "productGroupId": x["group"], "noBagsRequested": x["bags"],
                  "quantityOfBagKg": x["kg_per_bag"], "isLooseBuy": False,
                  "totalQuantityKgRequested": x["bags"] * x["kg_per_bag"]} for x in chosen]
        first = self.land_map()[s["selection"][0]["land_ref"]]
        payload = {
            "seasonId": s["season"]["season"]["seasonId"], "year": now.year, "month": now.month,
            "requestedDatetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "applicantUserId": s["user"]["userId"], "contactNoForCommunication": mobile,
            "isReceiverApplicantSame": True, "receiverUserId": s["user"]["userId"],
            "receiverName": s["user"].get("userFullName"), "receiverMobileNo": mobile,
            "receiverAadharHash": s["user"]["userAadhaarHash"],
            "stateLgdCode": int(s["state_code"]),
            **{k: first[k] for k in ("districtLgdCode", "subDistrictLgdCode", "villageLgdCode")},
            "selectedLandIds": [{k: x[k] for k in ("farmLandOwnershipId", "cropCode", "farmLandPlotRegistryId")} for x in selected_lands],
            "bookingRequest": [{"retailerId": shop["retailer_id"], "productLines": lines}],
            "GFRRecommendation": s["gfr"]["recommendations"], "gfrType": s["gfr"]["gfrType"],
            "isProceedWithGfr": True,
        }
        self.note("booking_submitted", products_count=len(lines), plots_count=len(selected_lands))
        self.diagnostics["booking_dispatched"] = True
        envelope = await self.call("POST", FERT, "/bharatVistar/createMultipleBookingV2", payload)
        return await self.finish_booking(envelope, shop)

    async def finish_booking(self, envelope, shop):
        feedback = {"http_status": self.last_http_status}
        if isinstance(envelope, dict):
            feedback.update({k: deepcopy(envelope[k]) for k in ("code", "status", "message", "data") if k in envelope})
        def uncertain(reason):
            return FFSError("booking_uncertain", reason + ". The same farmer-approved request may be retried; FFS rejects existing or parallel bookings. Do not claim success without a conclusive response", feedback)

        if not isinstance(envelope, dict):
            raise uncertain("FFS returned an unreadable booking response")
        data = envelope.get("data")
        failed = envelope.get("status") == "FAILED"
        # Confirmed provider contract: FAILED with explicit null data means no
        # write, including code/HTTP 500. Never infer this from English wording.
        rejected = failed and "data" in envelope and data is None
        row = None
        success = False
        if isinstance(data, dict):
            rows = data.get("bookingResults", [])
            if (not isinstance(rows, list) or len(rows) > 1
                    or any(not isinstance(r, dict) or r.get("retailerId") != shop["retailer_id"] for r in rows)):
                raise uncertain("FFS booking result did not match the selected retailer")
            row = rows[0] if rows else None
            success = data.get("isValid") is True and row is not None and row.get("isValid") is True
            rejected = (data.get("isValid") is False
                        and type(data.get("totalBookingsSuccessful")) is int
                        and data["totalBookingsSuccessful"] == 0)
            if rejected and any(r.get("isValid") is not False or r.get("procurementId")
                                or r.get("procurementApplicationCode") or r.get("isWaitlisted") is True for r in rows):
                raise uncertain("FFS rejection contradicts its booking results")
            if rejected and (data.get("procurementId") or data.get("procurementApplicationCode")):
                raise uncertain("FFS rejection also contains a booking reference")
            if rejected and (data.get("totalBookingsFailed", 1) != 1 or data.get("totalBookingsRequested", 1) != 1):
                raise uncertain("FFS rejection contains contradictory booking counts")
            if success and (failed or not 200 <= self.last_http_status < 300 or envelope.get("code") != 200
                    or not row.get("procurementId") or not row.get("procurementApplicationCode")
                    or type(row.get("isWaitlisted")) is not bool
                    or data.get("totalBookingsSuccessful") != 1 or data.get("totalBookingsFailed") != 0):
                raise uncertain("FFS success response is incomplete or inconsistent")
        if not success and not rejected:
            raise uncertain("FFS did not establish whether the booking was created")
        # Even the null-data failure shape cannot contradict a top-level success.
        if rejected and (envelope.get("isValid") is True or envelope.get("totalBookingsSuccessful", 0)
                         or envelope.get("procurementId") or envelope.get("procurementApplicationCode")):
            raise uncertain("FFS failure response also reports a booking")
        public = {"is_valid": success, "bookings": [{
            "procurement_id": row.get("procurementId"), "booking_code": row.get("procurementApplicationCode"),
            "is_waitlisted": row.get("isWaitlisted") is True,
            "expires_at": row.get("waitlistExpiresOn") if row.get("isWaitlisted") else row.get("expiryDatetime"),
            "total_bags_requested": row.get("totalBagsRequested"),
        }] if success else []}
        s = self.state
        s["stage"] = "booking_done" if success else "shops_ready" if s.get("token") else "login_expired"
        answer = result("ok" if success else "booking_failed",
                        "Share the booking reference and expiry; distinguish waitlisting from confirmed allocation. Mention SMS confirmation or a registered-mobile update for a waitlist. Close only with a neutral Can I help with anything else? in the farmer's language; do not offer another fertilizer, extra bags, another shop or another booking" if success else
                        "FFS rejected this attempt; it did not create a new booking. Explain its message without shop stock volumes. If an existing booking prevents this attempt, explain that and stop; do not infer its products or reference. Ask for a correction only when the provider response supports changing the choice; use the farmer's instruction without another confirmation",
                        booking=public, order=[{k: p[k] for k in ("product_id", "name", "bags", "kg_per_bag")} for p in s.get("order", [])], booking_enabled=True,
                        **({"provider_error": feedback} if rejected else {}))
        self.note("booking_outcome", outcome="success" if success else "rejected")
        await self.save()
        return deepcopy(answer)

    async def run(self, operation, **kwargs):
        if not settings.ffs_base_url or not self.session_id:
            self.diagnostics["reason"] = "missing_configuration_or_session"
            return result("unavailable", "FFS is not configured or the conversation has no session ID")
        try:
            lock_name = settings.redis_key_prefix + "ffs-lock:" + session_key(self.session_id)
            lock_started = time.monotonic()
            async with cache.client.lock(lock_name, timeout=50, blocking_timeout=1):
                self.diagnostics["lock_wait_ms"] = round((time.monotonic() - lock_started) * 1000, 2)
                async with asyncio.timeout(40):
                    self.state = await get_session(self.session_id)
                    # Old local booking guards are no longer authoritative. FFS
                    # owns existing/parallel booking enforcement.
                    for key in ("uncertain_booking", "last_booking_result", "booking_tool_exchanges", "pending_booking", "quota"):
                        self.state.pop(key, None)
                    self.diagnostics["stage_before"] = self.state.get("stage", "not_started")
                    async with httpx.AsyncClient(timeout=get_default_httpx_timeout(), follow_redirects=False) as client:
                        self.client = client
                        try:
                            answer = await getattr(self, operation)(**kwargs)
                        except FFSError as error:
                            if operation not in {"find_shops", "create_booking"}:
                                raise
                            self.diagnostics.update(error_type=type(error).__name__, workflow_status=error.status)
                            if error.status == "needs_otp":
                                self.state.pop("token", None)
                                self.state["stage"] = "login_expired"
                            answer = result(error.status, error.next_action, **({"provider_error": error.provider_error} if error.provider_error else {}))
                        if operation in {"find_shops", "create_booking"}:
                            answer = compact_ffs_result(answer)
                        return answer
        except FFSError as error:
            detail = {"provider_error": error.provider_error} if error.provider_error else {}
            self.diagnostics.update(error_type=type(error).__name__, workflow_status=error.status)
            if error.status == "needs_otp" and self.state:
                self.state.pop("token", None)
                self.state["stage"] = "login_expired"
                await self.save()
            if self.state.get("token") and self.state.get("stage") == "loading":
                return result("setup_incomplete", "Explain the FFS response. If retrying can help, call ffs_verify_otp without another OTP to resume setup", **detail)
            if self.state.get("uncertain_plot"):
                ref = self.state["uncertain_plot"]
                land = self.land_map().get(ref, {})
                return result("partial_crop_update", "Crop mapping is incomplete. Explain the FFS response for the affected plot. Ask the farmer which plots to proceed with before changing the selection; do not silently exclude plots. No new OTP is needed",
                              affected_plot={"land_ref": ref, "survey": str(land.get("surveyNumber", "")),
                                             "sub_survey": str(land.get("subSurveyNumber", ""))}, **detail)
            return result(error.status, error.next_action, **detail)
        except (httpx.HTTPError, TimeoutError) as error:
            self.diagnostics["error_type"] = type(error).__name__
            if self.diagnostics.get("booking_dispatched"):
                return result("booking_uncertain", "Could not confirm this booking response. The same farmer-approved request may be retried; FFS rejects existing or parallel bookings. Do not claim that nothing was booked")
            status = "partial_crop_update" if self.state.get("uncertain_plot") else "setup_incomplete" if self.state.get("token") and self.state.get("stage") == "loading" else "unavailable"
            return result(status, "FFS did not complete the request. Resume setup/crop reconciliation without resending OTP or blindly repeating writes")
        except (RedisError, ValueError, KeyError, TypeError, IndexError) as error:
            self.diagnostics["error_type"] = type(error).__name__
            if self.diagnostics.get("booking_dispatched"):
                return result("booking_uncertain", "Could not retain or interpret this booking outcome. The same farmer-approved request may be retried; FFS rejects existing or parallel bookings. Do not claim that nothing was booked")
            return result("unavailable", "FFS state or response is unavailable; no booking was attempted")
