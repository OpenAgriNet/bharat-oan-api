"""FFS authentication, resumable crop mapping and fertilizer shop discovery."""
import asyncio
import base64
from copy import deepcopy
from difflib import SequenceMatcher
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Literal
import secrets
import hashlib
import json
import math
import re
import time
import unicodedata

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from redis.exceptions import RedisError

from app.config import get_default_httpx_timeout, settings
from app.core.cache import cache
from helpers.utils import to_ascii_digits
from helpers.ffs_tracing import observation, response_summary

USER = "/ufa-user-service/ufa-user/v1/api"
MASTER = "/ufa-master-service/ufa-master/v1/api"
APPROVAL = "/ufa-approval-service/ufa-approval-service/v1/api"
FERT = "/ufa-fertilizer-service/ufa-fertilizer/v1/api"
APPLICANT_FETCH = "/bharatVistar/applicantFetch"
NAMESPACE = "ffs-session"
LEVELS = ("V", "T", "D", "S")
PRODUCTS = {11: 1089, 12: 1005}
STATE_CODES = {
    "Jammu and Kashmir": "1", "Himachal Pradesh": "2", "Punjab": "3", "Chandigarh": "4",
    "Uttarakhand": "5", "Haryana": "6", "Delhi": "7", "Rajasthan": "8", "Uttar Pradesh": "9",
    "Bihar": "10", "Sikkim": "11", "Arunachal Pradesh": "12", "Nagaland": "13", "Manipur": "14",
    "Mizoram": "15", "Tripura": "16", "Meghalaya": "17", "Assam": "18", "West Bengal": "19",
    "Jharkhand": "20", "Odisha": "21", "Chhattisgarh": "22", "Madhya Pradesh": "23", "Gujarat": "24",
    "Maharashtra": "27", "Andhra Pradesh": "28", "Karnataka": "29", "Goa": "30", "Lakshadweep": "31",
    "Kerala": "32", "Tamil Nadu": "33", "Puducherry": "34", "Andaman and Nicobar Islands": "35",
    "Telangana": "36", "Ladakh": "37", "Dadra and Nagar Haveli and Daman and Diu": "38",
}
MAIZE = "0101000"
ALIASES = {
    MAIZE: "maize corn makka makai makki makkai maka makaa मक्का मका मकई मक्की मकै মকাই ভুট্টা મકાઈ મકાઇ મક્કા مکئی ਮੱਕੀ ಮೆಕ್ಕೆಜೋಳ மக்காச்சோளம் మొక్కజొన్న ചോളം ମକା".split(),
    "0101900": "wheat gehun gehu gehhoo gahu गहू गेहूं गेहूँ গম ঘেঁহু ઘઉં ਕਣਕ ਗੰਦਮ ಗೋಧಿ கோதுமை గోధుమ గోధుమలు ഗോതമ്പ് ଗହମ".split(),
    "0801200": "soybean soyabean soya सोयाबीन सोयाबिन સોયાબીન".split(),
    "0300200": "sugarcane ऊस गन्ना શેરડી".split(),
    "0101700": "rice paddy dhan dhaan धान भात तांदूळ चावल चावल्स ચોખા ધાન நெல் அரிசி వరి బియ్యం നെല്ല് ଧାନ ধান ಅಕ್ಕಿ ಭತ್ತ ਚੌਲ".split(),
    "0101800": "sorghum jowar jowari ज्वारी ज्वार சோளம்".split(),
    "0101300": ["pearl millet", "bajra", "बाजरी", "बाजरा"],
    "0201100": ["green gram", "mung bean", "moong", "mung", "मूग", "मूंग"],
    "0800400": "groundnut peanut peanuts भुईमूग मूंगफली".split(),
    "0702000": "onion कांदा प्याज".split(),
    "0702300": "potato बटाटा आलू".split(),
}


class CropSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    land_ref: str
    crop_name: str = Field(min_length=1, max_length=100)


class BookingQuantity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product: Literal["urea", "DAP"]
    bags: int = Field(strict=True, ge=1)


class BookingLimitError(Exception):
    def __init__(self, product, requested, allowed, purchased):
        self.details = dict(product=product, requested_bags=requested,
                            allowed_bags=allowed, already_purchased_bags=purchased)


def result(status, next_action, **data):
    return {"status": status, "data": data, "next_action": next_action, "source": "FFS"}


def owner_id(user):
    identity = user.get("sub") or user.get("unique_id") or user.get("mobile")
    token_id = user.get("auth_token_id")
    if not identity or not token_id:
        return ""
    return hashlib.sha256(json.dumps([user.get("channel"), str(identity), token_id]).encode()).hexdigest()


def session_key(session_id):
    return hashlib.sha256(session_id.encode()).hexdigest()


def normalize(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if not unicodedata.category(c).startswith(("P", "Z")))


def resolve_crop(name, crops):
    code_names = {str(c["cropCode"]): c.get("cropName", "") for c in crops}
    names = {}
    for code, label in code_names.items():
        if label.strip():
            names.setdefault(normalize(label), set()).add(code)
    for code, aliases in ALIASES.items():
        if code in code_names:
            for alias in aliases:
                names.setdefault(normalize(alias), set()).add(code)
    query = normalize(name)
    if query in names:
        codes = names[query]
        return MAIZE if MAIZE in codes else next(iter(codes)) if len(codes) == 1 else None
    if len(query) < 3:
        return None
    scores = {}
    for candidate, codes in names.items():
        score = SequenceMatcher(None, query, candidate).ratio()
        for code in codes:
            scores[code] = max(scores.get(code, 0), score)
    ranked = sorted(scores, key=lambda c: scores[c], reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    # Prefer maize only among credible near matches, never for an unrelated word.
    if scores.get(MAIZE, 0) >= 0.78 and scores[MAIZE] >= scores[best] - 0.06:
        return MAIZE
    if scores[best] >= 0.88 and (len(ranked) == 1 or scores[best] - scores[ranked[1]] >= 0.08):
        return best
    return None


class FFSError(Exception):
    def __init__(self, status, next_action):
        self.status, self.next_action = status, next_action


async def get_session(session_id):
    return await cache.get(session_key(session_id), namespace=NAMESPACE) or {}


async def prepare_chat_turn(query, session_id, user):
    """Run before query logging/history loading. The OTP never enters model input."""
    owner = owner_id(user)
    bound_owner = await cache.get(session_key(session_id), namespace="ffs-owner")
    if not settings.ffs_base_url and not bound_owner:
        return query, None, "", {}
    state = await get_session(session_id)
    if (state and state.get("owner") != owner) or (bound_owner and bound_owner != owner):
        from fastapi import HTTPException
        raise HTTPException(403, "This FFS conversation belongs to another authenticated caller")
    if bound_owner:
        await cache.set(session_key(session_id), bound_owner, ttl=7200, namespace="ffs-owner")
    private_otp = None
    if state.get("stage") in {"otp_pending", "otp_outcome_unknown", "loading", "login_expired"}:
        digits = to_ascii_digits(query)
        match = re.search(r"(?<!\d)\d{6}(?!\d)", digits)
        if match:
            private_otp = SecretStr(match[0])
            # Digit normalization does not change string length, including native-script digits.
            query = query[:match.start()] + "[FFS OTP supplied]" + query[match.end():]
    context = {"stage": state.get("stage", "not_started"),
               "authenticated": bool(state.get("token") and state.get("token_expires", 0) > time.time()),
               "otp_supplied": private_otp is not None} if state else {}
    if all(k in state for k in ("profile", "crops", "season")):
        view = FFSService(session_id, owner, "")
        view.state = state
        context["villages"] = view.farmer_view()["data"]["villages"]
        if state.get("entitlement"):
            context["entitlement"] = state["entitlement"]["data"]
        if state.get("shops", {}).get("last_result"):
            context["last_shops"] = state["shops"]["last_result"]["data"]
    return query, private_otp, owner, context


class FFSService:
    def __init__(self, session_id, owner, question_id):
        self.session_id, self.owner, self.question_id = session_id, owner, question_id
        self.state = {}
        self.client = None
        self.diagnostics = {"api_calls": 0, "decisions": []}

    async def save(self):
        await cache.set(session_key(self.session_id), self.owner, ttl=7200, namespace="ffs-owner")
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
        response_data = None
        try:
            response_data = response.json()
        except ValueError:
            pass
        diagnostic.update(response_summary(response.status_code, response_data))
        if (path == "/bharatVistar/createMultipleBookingV2" and isinstance(response_data, dict)
                and response_data.get("code") == 500 and response_data.get("status") == "FAILED"
                and response_data.get("data") is None):
            limit = re.fullmatch(
                r"Error: Requested quantity exceeds the allowed limit for (Urea|DAP)\. "
                r"Requested: ([0-9]{1,9}) bags \| Allowed now: ([0-9]{1,9}) bags \| "
                r"Already purchased this month: ([0-9]{1,9}) bags\.",
                str(response_data.get("message", "")))
            if limit:
                product, requested, allowed, purchased = limit.groups()
                raise BookingLimitError("urea" if product == "Urea" else "DAP",
                                        int(requested), int(allowed), int(purchased))
        if response.status_code == 401:
            self.state.pop("token", None)
            self.state["stage"] = "login_expired"
            await self.save()
            raise FFSError("needs_otp", "Login expired. Ask whether the farmer wants a new OTP")
        if encrypted and "validate" in path and response.status_code in {400, 429}:
            raise FFSError("invalid_otp" if response.status_code == 400 else "needs_otp",
                           "Ask for the correct OTP, or offer a resend after three failures")
        if not response.is_success:
            raise FFSError("unavailable", "The FFS request failed; do not repeat a write automatically")
        if not isinstance(response_data, dict):
            raise FFSError("unavailable", "FFS returned an invalid response")
        data = response_data
        if not isinstance(data, dict) or data.get("code") != 200:
            raise FFSError("invalid_otp" if encrypted and "validate" in path else "unavailable",
                           "Ask for the correct OTP" if encrypted and "validate" in path else "FFS could not complete this request")
        self.last_envelope = data
        return data.get("data")

    def authenticated(self):
        if not self.state.get("token") or self.state.get("token_expires", 0) <= time.time():
            raise FFSError("needs_otp", "Ask whether the farmer wants to log in to FFS")

    def ready(self):
        self.authenticated()
        if not all(k in self.state for k in ("config", "season", "profile", "crops")):
            raise FFSError("setup_incomplete", "Call ffs_verify_otp with no arguments to resume loading")

    async def send_otp(self, farmer_id, state, resend=False, *, query=""):
        farmer_id = re.sub(r"[\s-]", "", to_ascii_digits(farmer_id))
        states = {normalize(name): code for name, code in STATE_CODES.items()}
        states.update({"maharashtra": "27", "महाराष्ट्र": "27", "mh": "27", "महाराष्ट": "27",
                  "uttarpradesh": "9", "उत्तरप्रदेश": "9", "up": "9", "gujarat": "24", "ગુજરાત": "24", "गुजरात": "24"}
        )
        state_code = states.get(normalize(state), to_ascii_digits(state).strip())
        if not re.fullmatch(r"\d{11}", farmer_id) or state_code not in STATE_CODES.values():
            return result("needs_clarification", "Ask for an 11-digit farmer ID and a valid state name or LGD code")
        same = self.state.get("farmer_id") == farmer_id and self.state.get("state_code") == state_code
        if self.state.get("otp_turn") == self.question_id:
            self.note("otp_send_suppressed", reason="same_turn")
            return result("already_started", "An OTP request was already attempted this turn")
        if same and self.state.get("stage") != "not_started":
            if not resend:
                self.note("otp_send_suppressed", reason="already_started")
                return result("already_started", "OTP already requested; ask for it, or offer an explicit resend if expired",
                              expires_in=max(0, int(self.state.get("otp_expires", 0) - time.time())))
            if not re.search(r"resend|again|new otp|re-send|फिर|दोबारा|पुनः|पुन्हा|फेर|ફરી|আবার|পুনৰ|دوبارہ|ಮತ್ತೆ|மீண்டும்|మళ్లీ|വീണ്ടും|ପୁଣି|ਦੁਬਾਰਾ", query, re.I):
                return result("needs_clarification", "Ask the farmer to explicitly request another OTP")
        self.note("otp_requested", state_code=state_code, resend=bool(resend))
        booking_state = {k: self.state[k] for k in ("uncertain_booking", "last_booking_result")
                         if same and k in self.state}
        self.state = {**booking_state, "owner": self.owner, "farmer_id": farmer_id, "state_code": state_code,
                      "stage": "otp_outcome_unknown", "otp_expires": time.time() + 180,
                      "otp_turn": self.question_id, "otp_attempts": 0}
        await self.save()
        await self.call("POST", USER, "/authenticate/user/loginByFarmerId", {
            "userStateLgdCode": state_code, "userFarmerId": farmer_id}, encrypted=True)
        self.note("otp_send_accepted")
        self.state["stage"] = "otp_pending"
        await self.save()
        return result("otp_sent", "Ask for the six-digit SMS OTP", expires_in=180)

    async def verify_otp(self, otp=None):
        if not self.state.get("token"):
            if self.state.get("stage") not in {"otp_pending", "otp_outcome_unknown"} or self.state.get("otp_expires", 0) <= time.time():
                return result("needs_otp", "Ask whether to send a new OTP")
            if self.state.get("otp_attempts", 0) >= 3:
                return result("needs_otp", "Three OTP attempts failed. Offer a resend; do not send automatically")
            if not otp or not re.fullmatch(r"\d{6}", to_ascii_digits(otp)):
                return result("needs_otp", "Ask the farmer for the six-digit SMS OTP")
            if self.state.get("verify_turn") == self.question_id:
                return result("invalid_otp", "Ask for a new OTP entry; this attempt was already processed")
            self.state["verify_turn"] = self.question_id
            self.state["otp_attempts"] += 1
            self.note("otp_verification_attempted", attempt=self.state["otp_attempts"])
            await self.save()
            data = await self.call("POST", USER, "/authenticate/user/validateLoginByFarmerId", {
                "userStateLgdCode": self.state["state_code"], "userFarmerId": self.state["farmer_id"],
                "otp": to_ascii_digits(otp)}, encrypted=True)
            if not isinstance(data, dict) or not data.get("userToken") or not data.get("userId") or not data.get("userAadhaarHash"):
                raise FFSError("unavailable", "FFS login returned an incomplete profile")
            try:
                claims = json.loads(base64.urlsafe_b64decode(data["userToken"].split(".")[1] + "==="))
                expires = min(float(claims["exp"]), time.time() + 900)
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
            ("config", MASTER, "/stateConfiguration/getConfigurationByState", "GET", None, {"stateLgdCode": s["state_code"]}),
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
            data = await self.call(method, prefix, path, body, params=params)
            if name == "profile":
                message = data.get("message", "") if isinstance(data, dict) else self.last_envelope.get("message", "")
                if (data is None or (isinstance(data, dict) and data.get("landData") is None)) and "no data found" in str(message).lower():
                    data = {"landData": []}
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
            if not valid or (name == "config" and not data):
                raise FFSError("setup_incomplete", "Call ffs_verify_otp without another OTP to resume setup")
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

    @staticmethod
    def restriction(land):
        dispute = normalize(str(land.get("disputeStatus") or ""))
        approval = normalize(str(land.get("claimApprovalStatus") or ""))
        if dispute and dispute not in {"nodispute", "false", "0"}:
            return "disputed"
        if approval and approval not in {"approved", "accepted", "1"}:
            return "approval_not_confirmed"
        return None

    def farmer_view(self):
        s, villages = self.state, []
        for ref, code in self.villages().items():
            plots = []
            for land_ref, land in self.land_map().items():
                if str(land["villageLgdCode"]) != code:
                    continue
                plots.append({"land_ref": land_ref, "survey": str(land.get("surveyNumber", "")),
                              "sub_survey": str(land.get("subSurveyNumber", "")), "area_ha": land["landAreaHectares"],
                              "restriction": self.restriction(land), "booking_status": "unknown",
                              "current_crops": [next((r.get("cropName") for r in s["crops"] if
                                  r.get("cropCode") == c.get("cropCode")), "unknown") for c in land.get("crops", []) if
                                  c.get("seasonId") == s["season"]["season"]["seasonId"] and
                                  c.get("yearId") == s["season"]["year"]["yearId"]]})
            villages.append({"village_ref": ref, "name": next(l.get("villageName", "") for l in self.land_map().values()
                             if str(l["villageLgdCode"]) == code), "plots": plots})
        return result("ok" if villages else "no_lands", "Ask only for missing village and crops" if villages else
                      "Explain that FFS returned no land records", farmer_name=s["user"].get("userFullName"), villages=villages)

    async def entitlement(self, village_ref, crop_selections):
        self.ready()
        s, lands = self.state, self.land_map()
        village = self.villages().get(village_ref)
        if not village or not crop_selections or len({x.land_ref for x in crop_selections}) != len(crop_selections):
            return result("needs_clarification", "Choose a returned village and one crop per selected plot")
        selected = []
        for item in crop_selections:
            land = lands.get(item.land_ref)
            if not land or str(land["villageLgdCode"]) != village or self.restriction(land):
                return result("needs_clarification", "Choose only unrestricted plots from the selected village")
            area = land.get("landAreaHectares")
            if type(area) not in (int, float) or not math.isfinite(area) or area <= 0:
                return result("unavailable", "FFS returned an invalid plot area; do not save crops or estimate quantities")
            code = resolve_crop(item.crop_name, s["crops"])
            if not code:
                return result("needs_clarification", "Ask which crop this is; if unclear, ask whether they mean maize",
                              land_ref=item.land_ref, suggested_crop="maize" if any(c["cropCode"] == MAIZE for c in s["crops"]) else None)
            selected.append({"land_ref": item.land_ref, "crop_code": code})
        signature = json.dumps([village_ref, selected], sort_keys=True)
        if s.get("selection_signature") == signature and "entitlement" in s:
            self.note("entitlement_reused")
            return deepcopy(s["entitlement"])
        s.pop("entitlement", None)
        s.pop("shops", None)
        s.pop("products", None)
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
            for land in lands.values():
                if str(land["farmlandPlotRegistryId"]) not in current:
                    raise FFSError("partial_crop_update", "Land records changed. Restart farmer setup before proceeding")
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
        first = lands[selected[0]["land_ref"]]
        quota = await self.call("POST", FERT, "/applicant/getApplicantDashboardQuota", {
            "applicantUserId": s["user"]["userId"], "stateLgdCode": int(s["state_code"]), "districtLgdCode": first["districtLgdCode"]})
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
            bags = r.get("offeredBags", r.get("noOfBagsRequired"))
            weight = r.get("kgPerBag")
            if type(bags) is not int or bags < 0 or type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0:
                raise FFSError("unavailable", "FFS returned invalid fertilizer quantities")
            self.note("entitlement_quantity", group=r["productGroupId"], bags=bags,
                      quantity_field="offeredBags" if "offeredBags" in r else "noOfBagsRequired")
            groups.append({"group": r["productGroupId"], "name": "urea" if r["productGroupId"] == 11 else "DAP",
                           "bags": bags, "kg_per_bag": weight, "already_purchased": r.get("alreadyPurchasedBags", 0)})
        if len({g["group"] for g in groups}) != len(groups) or not groups:
            raise FFSError("unavailable", "FFS did not return supported fertilizer groups")
        s.update(gfr=gfr, quota=quota, stage="entitlement_ready")
        s["entitlement"] = result("ok" if any(g["bags"] for g in groups) else "zero_entitlement",
            "Read the bag counts and ask whether to find shops; only urea and DAP are covered",
            products=groups, plots=[x["land_ref"] for x in selected],
            crops=[{"land_ref": x["land_ref"], "crop": next(c.get("cropName") for c in s["crops"] if c["cropCode"] == x["crop_code"])} for x in selected])
        await self.save()
        return deepcopy(s["entitlement"])

    async def find_shops(self, action="initial"):
        self.ready()
        s = self.state
        if not s.get("entitlement"):
            raise FFSError("needs_entitlement", "Collect village/crops and call ffs_get_entitlement first")
        wanted = [p for p in s["entitlement"]["data"]["products"] if p["bags"] > 0]
        if not wanted:
            return result("zero_entitlement", "No urea/DAP bags are offered; do not search shops")
        first = self.land_map()[s["selection"][0]["land_ref"]]
        geo = {k: first[k] for k in ("stateLgdCode", "districtLgdCode", "subDistrictLgdCode", "villageLgdCode")}
        if "products" not in s:
            s["products"] = await self.call("POST", MASTER, "/product/getProductsByAvailability", {**geo, "productGroupIds": ",".join(str(p["group"]) for p in wanted)})
            await self.save()
        chosen = self._shop_products(wanted)
        search = self._shop_search()
        turn_key = self.question_id + ":" + action
        if turn_key in search["turn_results"]:
            self.note("shop_results_reused", reason="same_turn")
            return deepcopy(search["turn_results"][turn_key])
        if action == "initial" and search["shown"]:
            self.note("shop_results_reused", reason="already_shown")
            return deepcopy(search["last_result"])
        if action == "widen":
            if search["level"] >= len(LEVELS) - 1:
                return result("no_more_shops", "The search is already at state level")
            search["level"] += 1
            search["page"] = 1
            search["minimum_level"] = search["level"]
        await self._collect_shop_pages(search, geo, chosen)
        answer = self._shop_result(search, wanted)
        search["last_result"] = answer
        search["turn_results"][turn_key] = answer
        s["stage"] = "shops_ready"
        await self.save()
        return deepcopy(answer)

    def _shop_products(self, wanted):
        chosen = []
        for group in wanted:
            product = next((p for p in self.state["products"] if p.get("productId") == PRODUCTS[group["group"]]), None)
            if not product or product.get("bagWeightKg") != group["kg_per_bag"] or not product.get("plantId"):
                raise FFSError("unavailable", "The required fertilizer product/bag size is unavailable; do not substitute")
            chosen.append({"productGroupId": group["group"], "productId": product["productId"],
                           "quantityOfBagKg": group["kg_per_bag"], "noBagsRequested": group["bags"]})
        return chosen

    def _shop_search(self):
        s = self.state
        # Old searches discarded low-stock shops, so their cached pages cannot be reused.
        if "shops" in s and (s["shops"].get("stock_visibility") != "all"
                             or any("stock" not in shop for shop in s["shops"]["pool"].values())):
            s.pop("shops")
            self.note("shop_results_refreshed", reason="stock_visibility_changed")
        return s.setdefault("shops", {"level": 0, "page": 1, "pool": {}, "shown": [], "complete_levels": [], "turn_results": {}, "stock_visibility": "all"})

    @staticmethod
    def _pending_shops(search):
        return [shop for shop in search["pool"].values() if shop["shop_ref"] not in search["shown"] and
                shop["rank"][0] >= search.get("minimum_level", 0)]

    async def _collect_shop_pages(self, search, geo, chosen):
        quantities = {p["productId"]: p["noBagsRequested"] for p in chosen}
        # Seek three stocked choices; empty shops must not stop territory expansion.
        # Bound actual API calls, while preserving each territory's pagination.
        for _ in range(4):
            if sum(bool(shop["coverage"]) for shop in self._pending_shops(search)) >= 3:
                break
            while LEVELS[search["level"]] in search["complete_levels"] and search["level"] < len(LEVELS) - 1:
                search["level"] += 1
                search["page"] = 1
            level = search["level"]
            if LEVELS[level] in search["complete_levels"]:
                break
            self.note("shop_page_requested", territory=LEVELS[level], page=search["page"])
            data = await self.call("POST", MASTER, "/retailer/getRetailersByProductAvailabilityV4", {
                **geo, "selectedProducts": chosen, "territoryLevel": LEVELS[level],
                "farmerLatitude": None, "farmerLongitude": None, "pageNo": search["page"], "pageSize": 100})
            # This API's envelope owns pagination, captured by call() below.
            pages = self.last_envelope.get("totalPages")
            if not isinstance(data, list) or type(pages) is not int or pages < 0:
                raise FFSError("unavailable", "FFS returned invalid retailer pagination")
            for row in data:
                self._add_retailer(search, row, quantities)
            self.note("shop_page_processed", territory=LEVELS[level], page=search["page"],
                      rows=len(data), eligible_pending=len(self._pending_shops(search)))
            search["page"] += 1
            if search["page"] > pages:
                search["complete_levels"].append(LEVELS[level])
            await self.save()

    def _add_retailer(self, search, row, quantities):
        if not isinstance(row, dict) or type(row.get("retailerId")) is not int:
            raise FFSError("unavailable", "FFS returned an invalid retailer")
        stocks = {}
        for group in row.get("availableProducts", []):
            for product in group.get("products", []):
                pid, available = product.get("productId"), product.get("noBagsAvailable")
                if pid in quantities and type(available) in (int, float) and math.isfinite(available) and available >= 0:
                    stocks[pid] = available
        rid = str(row["retailerId"])
        if rid in search["pool"]:
            return
        sufficient = {pid for pid, bags in quantities.items() if stocks.get(pid, 0) >= bags}
        available = {pid for pid, bags in stocks.items() if bags > 0}
        complete = sufficient == set(quantities)
        supply_rank = self._shop_supply_rank(complete, sufficient, available)
        level = search["level"]
        search["pool"][rid] = {"shop_ref": "shop-" + str(len(search["pool"]) + 1), "name": row.get("retailerName", ""),
            "address": row.get("address1", ""), "coverage": ["urea" if pid == 1089 else "DAP" for pid in quantities if pid in available],
            "complete_supply": complete, "stock": {str(k): v for k, v in stocks.items()},
            "territory": LEVELS[level], "retailer_id": row["retailerId"],
            "rank": [level, supply_rank, -sum(stocks.values()), row["retailerId"]]}

    @staticmethod
    def _shop_supply_rank(complete, sufficient, available):
        # Full entitlement coverage ranks first; smaller stocks remain selectable.
        if complete:
            return 0
        if 1089 in sufficient:
            return 1
        if 1005 in sufficient:
            return 2
        if 1089 in available:
            return 3
        if 1005 in available:
            return 4
        return 5

    def _shop_result(self, search, wanted):
        batch = sorted(self._pending_shops(search), key=lambda x: (not bool(x["coverage"]), *x["rank"]))[:3]
        search["shown"].extend(shop["shop_ref"] for shop in batch)
        exhausted = search["level"] == 3 and "S" in search["complete_levels"]
        public = [{**{k: v for k, v in shop.items() if k not in {"rank", "retailer_id", "stock"}},
                   "stock": {p["name"]: shop["stock"].get(str(PRODUCTS[p["group"]])) for p in wanted}}
                  for shop in batch]
        status = "search_incomplete"
        if batch:
            status = "ok"
        elif exhausted:
            status = "no_stock"
        return result(status,
                        "Present at most three shop names and urea/DAP availability with stock counts; no booking or reservation has been made",
                        shops=public, has_more=bool(self._pending_shops(search)) or not exhausted,
                        search_complete=LEVELS[search["level"]] in search["complete_levels"],
                        complete_supply_only=False,
                        complete_supply_further=None, booking_enabled=True)

    def booking_choices(self, shop):
        limits = self.state.get("booking_limits", {})
        return [{**p, "bags": limits.get(p["name"], p["bags"])}
                for p in self.state["entitlement"]["data"]["products"]
                if p["name"] in shop["coverage"]]

    async def create_booking(self, shop_ref, confirmed=False, confirmation_token="", quantities=None):
        s = self.state
        if s.get("uncertain_booking"):
            return result("booking_uncertain", "Previous booking outcome is unknown. Verify with FFS before another submission; do not retry automatically")
        if s.get("last_booking_result", {}).get("status") == "ok":
            self.note("booking_result_reused")
            return deepcopy(s["last_booking_result"])
        self.ready()
        if not s.get("entitlement"):
            raise FFSError("needs_entitlement", "Collect village/crops and call ffs_get_entitlement first")
        if not s.get("shops", {}).get("pool"):
            raise FFSError("needs_shops", "Call ffs_find_shops first and ask the farmer to choose a shop")
        if s.get("booking_turn") == self.question_id:
            return result("already_started", "Booking was already attempted this turn. Wait for the farmer's next response")
        shop = next((row for row in s["shops"]["pool"].values() if row["shop_ref"] == shop_ref), None)
        if not shop:
            return result("needs_clarification", "Choose a shop_ref from the latest shop list")
        choices = self.booking_choices(shop)
        if not choices:
            return result("needs_shops", "No available urea/DAP stock was reported for this shop. Ask the farmer to choose another shop or refresh stock")
        pending = s.get("pending_booking", {})
        if quantities is None and confirmed and pending.get("summary", {}).get("shop_ref") == shop_ref:
            quantities = [BookingQuantity(**q) for q in pending["quantities"]]
        chosen, error = self._booking_products(shop, choices, quantities)
        if error:
            return error
        return await self._confirm_booking(shop, chosen, quantities, confirmed, confirmation_token)

    def _booking_products(self, shop, choices, quantities):
        """Return validated product lines or the existing farmer-facing correction."""
        if not quantities:
            return None, result("needs_quantities", "Ask how many bags of each available product the farmer wants; reuse quantities they explicitly supplied", products=choices)
        names = [q.product for q in quantities]
        if len(names) != len(set(names)):
            return None, result("needs_quantities", "Specify each requested product once", products=choices)
        chosen = []
        for q in quantities:
            group = next((p for p in choices if p["name"] == q.product), None)
            if not group or q.bags > group["bags"]:
                return None, result("needs_quantities", "Use only products this shop covers and ask for quantities within the latest provider limit; never reduce silently", products=choices)
            product = next((p for p in self.state.get("products", []) if p.get("productId") == PRODUCTS[group["group"]]), None)
            if not product or product.get("bagWeightKg") != group["kg_per_bag"] or not product.get("plantId"):
                raise FFSError("unavailable", "Required fertilizer product/plant mapping is unavailable; refresh shop search")
            stock = shop.get("stock", {}).get(str(product["productId"]))
            if stock is None:
                return None, result("needs_shops", "Refresh shop search: selected product stock is missing")
            if q.bags > stock:
                return None, result("insufficient_stock", "Explain this shop's available stock and ask for a smaller quantity or another shop; never reduce the quantity automatically",
                              product=q.product, requested_bags=q.bags, available_bags=stock)
            chosen.append({"group": group["group"], "name": group["name"], "bags": q.bags,
                           "kg_per_bag": group["kg_per_bag"], "product_id": product["productId"],
                           "plant_id": product["plantId"]})
        chosen.sort(key=lambda p: p["group"])
        return chosen, None

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

    async def _confirm_booking(self, shop, chosen, quantities, confirmed, confirmation_token):
        s = self.state
        shop_ref = shop["shop_ref"]
        pending = s.get("pending_booking", {})
        mobile = re.sub(r"\D", "", str(s["user"].get("userMobileNumber") or ""))
        if mobile.startswith("91") and len(mobile) == 12:
            mobile = mobile[2:]
        if not re.fullmatch(r"[6-9]\d{9}", mobile) or not s["user"].get("userFullName"):
            return result("needs_clarification", "FFS login did not return a valid contact number or receiver name for booking")
        selected_lands = self._booking_lands()
        summary = {
            "shop_ref": shop_ref, "shop_name": shop.get("name", ""),
            "products": [{"name": x["name"], "bags": x["bags"], "kg_per_bag": x["kg_per_bag"]} for x in chosen],
            "plots": [{"land_ref": x["land_ref"], "survey": x["survey"]} for x in selected_lands],
        }
        # Bind hidden provider identities as well as the user-visible summary.
        signature = hashlib.sha256(json.dumps([summary, selected_lands, chosen, shop["retailer_id"],
                                               s["user"], s["season"], s["gfr"]], sort_keys=True).encode()).hexdigest()
        valid_preview = (pending.get("signature") == signature and
                         pending.get("confirmation_token") == confirmation_token and
                         pending.get("expires", 0) > time.time())
        if not confirmed or not valid_preview:
            pending = {"signature": signature, "confirmation_token": secrets.token_urlsafe(18),
                       "summary": summary, "turn": self.question_id, "expires": time.time() + 300,
                       "quantities": [q.model_dump() for q in quantities]}
            s["pending_booking"] = pending
            await self.save()
            self.note("booking_confirmation_requested")
            return result("needs_confirmation", "Read the exact summary and wait for explicit confirmation in a later user turn",
                          summary=summary, confirmation_token=pending["confirmation_token"], booking_enabled=True)
        if pending["turn"] == self.question_id:
            return result("needs_confirmation", "Wait for a subsequent user message explicitly confirming this summary; do not submit in this turn",
                          summary=summary, confirmation_token=pending["confirmation_token"], booking_enabled=True)
        return await self._submit_booking(shop, summary, chosen, selected_lands, mobile)

    async def _submit_booking(self, shop, summary, chosen, selected_lands, mobile):
        s = self.state
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
            "receiverName": s["user"]["userFullName"], "receiverMobileNo": mobile,
            "receiverAadharHash": s["user"]["userAadhaarHash"],
            "stateLgdCode": int(s["state_code"]),
            **{k: first[k] for k in ("districtLgdCode", "subDistrictLgdCode", "villageLgdCode")},
            "selectedLandIds": [{k: x[k] for k in ("farmLandOwnershipId", "cropCode", "farmLandPlotRegistryId")} for x in selected_lands],
            "bookingRequest": [{"retailerId": shop["retailer_id"], "productLines": lines}],
            "GFRRecommendation": s["gfr"]["recommendations"], "gfrType": s["gfr"]["gfrType"],
            "isProceedWithGfr": True,
        }
        s["booking_turn"] = self.question_id
        s["uncertain_booking"] = {"shop_ref": shop["shop_ref"], "summary": summary}
        await self.save()
        self.note("booking_submitted", products_count=len(lines), plots_count=len(selected_lands))
        self.diagnostics["booking_dispatched"] = True
        try:
            data = await self.call("POST", FERT, "/bharatVistar/createMultipleBookingV2", payload)
        except BookingLimitError as error:
            detail = error.details
            if not any(x["name"] == detail["product"] and x["bags"] == detail["requested_bags"] for x in chosen):
                raise FFSError("booking_uncertain", "Provider rejection did not match this request; check the outcome before retrying")
            s.setdefault("booking_limits", {})[detail["product"]] = detail["allowed_bags"]
            s.pop("uncertain_booking", None)
            s.pop("pending_booking", None)
            await self.save()
            self.note("booking_limit_changed", **detail)
            return result("quantity_limit_changed", "Provider rejected this booking. Explain the updated allowed quantity and ask how many bags the farmer wants, then create a new preview and obtain confirmation", **detail)
        return await self.finish_booking(data, shop)

    async def finish_booking(self, data, shop):
        if not isinstance(data, dict) or type(data.get("isValid")) is not bool:
            raise FFSError("booking_uncertain", "FFS returned an invalid booking result")
        rows = data.get("bookingResults")
        if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
                or rows[0].get("retailerId") != shop["retailer_id"]):
            raise FFSError("booking_uncertain", "FFS booking result did not match the selected retailer")
        row = rows[0]
        success = data["isValid"] is True and row.get("isValid") is True
        rejected = (data["isValid"] is False and row.get("isValid") is False
                    and data.get("totalBookingsSuccessful") == 0 and not row.get("procurementId"))
        if success:
            if (not row.get("procurementId") or not row.get("procurementApplicationCode")
                    or type(row.get("isWaitlisted")) is not bool
                    or data.get("totalBookingsSuccessful") != 1 or data.get("totalBookingsFailed") != 0):
                raise FFSError("booking_uncertain", "FFS success response is incomplete; check status before retrying")
        elif not rejected:
            raise FFSError("booking_uncertain", "FFS booking results are inconsistent; check status before retrying")
        public = {"is_valid": success, "bookings": [{
            "procurement_id": row.get("procurementId"), "booking_code": row.get("procurementApplicationCode"),
            "is_waitlisted": row.get("isWaitlisted") is True, "expires_at": row.get("expiryDatetime"),
        }] if success else []}
        s = self.state
        s.pop("pending_booking", None)
        s.pop("uncertain_booking", None)
        s["stage"] = "booking_done" if success else "shops_ready"
        answer = result("ok" if success else "booking_failed",
                        "Share the booking reference and expiry; distinguish waitlisting from confirmed allocation" if success else
                        "FFS rejected the booking. Explain the failure; ask before preparing another request",
                        booking=public, booking_enabled=True)
        if success:
            s["last_booking_result"] = answer
        await self.save()
        return deepcopy(answer)

    async def run(self, operation, **kwargs):
        if not settings.ffs_base_url or not self.owner or not self.session_id:
            self.diagnostics["reason"] = "missing_configuration_or_caller_identity"
            return result("unavailable", "FFS is not configured or the caller has no authenticated identity")
        try:
            lock_name = settings.redis_key_prefix + "ffs-lock:" + session_key(self.session_id)
            lock_started = time.monotonic()
            async with cache.client.lock(lock_name, timeout=50, blocking_timeout=1):
                self.diagnostics["lock_wait_ms"] = round((time.monotonic() - lock_started) * 1000, 2)
                async with asyncio.timeout(40):
                    self.state = await get_session(self.session_id)
                    self.diagnostics["stage_before"] = self.state.get("stage", "not_started")
                    if self.state and self.state.get("owner") != self.owner:
                        self.diagnostics["reason"] = "session_owner_mismatch"
                        return result("unavailable", "This FFS session belongs to another authenticated caller")
                    async with httpx.AsyncClient(timeout=get_default_httpx_timeout(), follow_redirects=False) as client:
                        self.client = client
                        return await getattr(self, operation)(**kwargs)
        except FFSError as error:
            self.diagnostics.update(error_type=type(error).__name__, workflow_status=error.status)
            if error.status == "needs_otp" and self.state:
                self.state.pop("token", None)
                self.state["stage"] = "login_expired"
                await self.save()
            if self.state.get("token") and self.state.get("stage") == "loading":
                return result("setup_incomplete", "Call ffs_verify_otp without another OTP to resume setup")
            if self.state.get("uncertain_booking"):
                return result("booking_uncertain", "Booking outcome is uncertain. Check booking status before retrying another booking")
            if self.state.get("uncertain_plot"):
                return result("partial_crop_update", "Crop mapping is incomplete. Retry entitlement to reconcile saved plots; no new OTP is needed")
            return result(error.status, error.next_action)
        except (httpx.HTTPError, TimeoutError) as error:
            self.diagnostics["error_type"] = type(error).__name__
            status = "booking_uncertain" if self.state.get("uncertain_booking") else "partial_crop_update" if self.state.get("uncertain_plot") else "setup_incomplete" if self.state.get("token") and self.state.get("stage") == "loading" else "unavailable"
            return result(status, "FFS did not complete the request. Resume setup/crop reconciliation without resending OTP or blindly repeating writes")
        except (RedisError, ValueError, KeyError, TypeError, IndexError) as error:
            self.diagnostics["error_type"] = type(error).__name__
            if self.state.get("uncertain_booking") or self.diagnostics.get("booking_dispatched"):
                return result("booking_uncertain", "Booking outcome is unknown; verify with FFS before another submission")
            return result("unavailable", "FFS state or response is unavailable; no booking was attempted")
