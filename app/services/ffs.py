"""FFS authentication, resumable crop mapping and fertilizer shop discovery."""
import asyncio
import base64
from copy import deepcopy
from difflib import SequenceMatcher
from datetime import datetime
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

    async def save(self):
        await cache.set(session_key(self.session_id), self.owner, ttl=7200, namespace="ffs-owner")
        await cache.set(session_key(self.session_id), self.state, ttl=1200, namespace=NAMESPACE)

    async def call(self, method, prefix, path, body=None, *, encrypted=False, params=None):
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
        response = await self.client.request(method, settings.ffs_base_url.rstrip("/") + prefix + path,
                                             headers=headers, **kwargs)
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
        try:
            data = response.json()
        except ValueError:
            raise FFSError("unavailable", "FFS returned an invalid response")
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
            return result("already_started", "An OTP request was already attempted this turn")
        if same and self.state.get("stage") != "not_started":
            if not resend:
                return result("already_started", "OTP already requested; ask for it, or offer an explicit resend if expired",
                              expires_in=max(0, int(self.state.get("otp_expires", 0) - time.time())))
            if not re.search(r"resend|again|new otp|re-send|फिर|दोबारा|पुनः|पुन्हा|फेर|ફરી|আবার|পুনৰ|دوبارہ|ಮತ್ತೆ|மீண்டும்|మళ్లీ|വീണ്ടും|ପୁଣି|ਦੁਬਾਰਾ", query, re.I):
                return result("needs_clarification", "Ask the farmer to explicitly request another OTP")
        self.state = {"owner": self.owner, "farmer_id": farmer_id, "state_code": state_code,
                      "stage": "otp_outcome_unknown", "otp_expires": time.time() + 180,
                      "otp_turn": self.question_id, "otp_attempts": 0}
        await self.save()
        await self.call("POST", USER, "/authenticate/user/loginByFarmerId", {
            "userStateLgdCode": state_code, "userFarmerId": farmer_id}, encrypted=True)
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
                              user={k: data.get(k) for k in ("userId", "userAadhaarHash", "userFullName")}, stage="loading")
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
                continue
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
            return deepcopy(s["entitlement"])
        s.pop("entitlement", None)
        s.pop("shops", None)
        s.pop("products", None)
        s["selection_signature"], s["selection"] = signature, selected
        s["stage"] = "mapping_crops"
        await self.save()
        if s.get("uncertain_plot"):
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
                continue
            s["uncertain_plot"] = item["land_ref"]
            await self.save()
            data = await self.call("POST", APPROVAL, "/applicant/land/crop/add", {
                "applicantUserId": s["user"]["userId"], "farmlandPlotRegistryId": land["farmlandPlotRegistryId"],
                "farmlandOwnershipRegistryId": land["farmlandOwnershipId"], "cropCode": item["crop_code"],
                "seasonId": season, "yearId": year, "stateLgdCode": int(s["state_code"])})
            if not isinstance(data, dict) or data.get("cropCode") != item["crop_code"] or data.get("farmlandPlotRegistryId") != land["farmlandPlotRegistryId"]:
                raise FFSError("partial_crop_update", "FFS crop result did not match this plot; refetch before retrying")
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
        chosen = []
        for group in wanted:
            product = next((p for p in s["products"] if p.get("productId") == PRODUCTS[group["group"]]), None)
            if not product or product.get("bagWeightKg") != group["kg_per_bag"] or not product.get("plantId"):
                raise FFSError("unavailable", "The required fertilizer product/bag size is unavailable; do not substitute")
            chosen.append({"productGroupId": group["group"], "productId": product["productId"],
                           "quantityOfBagKg": group["kg_per_bag"], "noBagsRequested": group["bags"]})
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
            search["complete_only"] = True
        quantities = {p["productId"]: p["noBagsRequested"] for p in chosen}
        pending = lambda: [shop for shop in search["pool"].values() if shop["shop_ref"] not in search["shown"] and
                          (not search.get("complete_only") or
                           (shop["complete_supply"] and shop["rank"][0] >= search.get("minimum_level", 0)))]
        # Bound a tool call; completeness is reported instead of pretending a partial scan is exhaustive.
        for _ in range(4):
            level = search["level"]
            if LEVELS[level] in search["complete_levels"]:
                if len(pending()) >= 3 or level == len(LEVELS) - 1:
                    break
                search["level"] += 1
                search["page"] = 1
                continue
            data = await self.call("POST", MASTER, "/retailer/getRetailersByProductAvailabilityV4", {
                **geo, "selectedProducts": chosen, "territoryLevel": LEVELS[level],
                "farmerLatitude": None, "farmerLongitude": None, "pageNo": search["page"], "pageSize": 100})
            # This API's envelope owns pagination, captured by call() below.
            pages = self.last_envelope.get("totalPages")
            if not isinstance(data, list) or type(pages) is not int or pages < 0:
                raise FFSError("unavailable", "FFS returned invalid retailer pagination")
            for row in data:
                if not isinstance(row, dict) or type(row.get("retailerId")) is not int:
                    raise FFSError("unavailable", "FFS returned an invalid retailer")
                stocks = {}
                for group in row.get("availableProducts", []):
                    for product in group.get("products", []):
                        pid, available = product.get("productId"), product.get("noBagsAvailable")
                        if pid in quantities and type(available) in (int, float) and math.isfinite(available) and available >= quantities[pid]:
                            stocks[pid] = available
                if not stocks:
                    continue
                rid = str(row["retailerId"])
                if rid in search["pool"]:
                    continue
                complete = set(stocks) == set(quantities)
                search["pool"][rid] = {"shop_ref": "shop-" + str(len(search["pool"]) + 1), "name": row.get("retailerName", ""),
                    "address": row.get("address1", ""), "coverage": ["urea" if pid == 1089 else "DAP" for pid in stocks],
                    "complete_supply": complete, "territory": LEVELS[level], "retailer_id": row["retailerId"],
                    "rank": [level, 0 if complete else 1 if 1089 in stocks else 2, -sum(stocks.values()), row["retailerId"]]}
            search["page"] += 1
            if search["page"] > pages:
                search["complete_levels"].append(LEVELS[level])
            await self.save()
            if len(pending()) >= 3:
                break
        batch = sorted(pending(), key=lambda x: x["rank"])[:3]
        search["shown"].extend(shop["shop_ref"] for shop in batch)
        exhausted = search["level"] == 3 and "S" in search["complete_levels"]
        public = [{k: v for k, v in shop.items() if k not in {"rank", "retailer_id"}} for shop in batch]
        answer = result("ok" if batch else "no_stock" if exhausted else "search_incomplete",
                        "Present shops and coverage; no booking or reservation has been made",
                        shops=public, has_more=bool(pending()) or not exhausted,
                        search_complete=LEVELS[search["level"]] in search["complete_levels"],
                        complete_supply_only=search.get("complete_only", False),
                        complete_supply_further=None, booking_enabled=True)
        search["last_result"] = answer
        search["turn_results"][turn_key] = answer
        s["stage"] = "shops_ready"
        await self.save()
        return deepcopy(answer)

    async def create_booking(self, shop_ref, confirmed=False, confirmation_token=""):
        self.ready()
        s = self.state
        if not s.get("entitlement"):
            raise FFSError("needs_entitlement", "Collect village/crops and call ffs_get_entitlement first")
        if not s.get("shops", {}).get("pool"):
            raise FFSError("needs_shops", "Call ffs_find_shops first and ask the farmer to choose a shop")
        if s.get("booking_turn") == self.question_id:
            if s.get("last_booking_result"):
                return deepcopy(s["last_booking_result"])
            return result("already_started", "Booking has already been attempted this turn")
        wanted = [p for p in s["entitlement"]["data"]["products"] if p["bags"] > 0]
        if not wanted:
            return result("zero_entitlement", "No urea/DAP bags are offered; do not place a booking")
        shop = next((row for row in s["shops"]["pool"].values() if row["shop_ref"] == shop_ref), None)
        if not shop:
            return result("needs_clarification", "Choose a shop_ref from the latest shop list")

        mobile = re.sub(r"\D", "", str(s["user"].get("userMobileNumber") or ""))
        if mobile.startswith("91") and len(mobile) == 12:
            mobile = mobile[2:]
        if not re.fullmatch(r"[6-9]\d{9}", mobile):
            return result("needs_clarification", "FFS login did not return a valid 10-digit contact number for booking")

        chosen = []
        for group in wanted:
            product = next((p for p in s.get("products", []) if p.get("productId") == PRODUCTS[group["group"]]), None)
            if not product or product.get("bagWeightKg") != group["kg_per_bag"] or not product.get("plantId"):
                raise FFSError("unavailable", "The required fertilizer product/plant mapping is unavailable for booking")
            chosen.append({"group": group["group"], "name": group["name"], "bags": group["bags"],
                           "kg_per_bag": group["kg_per_bag"], "product_id": product["productId"],
                           "plant_id": product["plantId"]})

        selected_lands = []
        for item in s.get("selection", []):
            land = self.land_map().get(item["land_ref"])
            if not land:
                raise FFSError("unavailable", "Selected plot details are missing; refresh entitlement before booking")
            selected_lands.append({"land_ref": item["land_ref"], "survey": str(land.get("surveyNumber", "")),
                                   "farmlandPlotRegistryId": str(land["farmlandPlotRegistryId"]),
                                   "farmLandOwnershipId": str(land["farmlandOwnershipId"]),
                                   "cropCode": item["crop_code"]})
        first_plot_id = int(selected_lands[0]["farmlandPlotRegistryId"])
        first_crop_code = selected_lands[0]["cropCode"]

        summary = {
            "shop_ref": shop["shop_ref"],
            "shop_name": shop.get("name", ""),
            "products": [{"name": x["name"], "bags": x["bags"], "kg_per_bag": x["kg_per_bag"]} for x in chosen],
            "plots": [{"land_ref": x["land_ref"], "survey": x["survey"]} for x in selected_lands],
        }
        expected_token = hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()[:12]
        if not confirmed:
            s["pending_booking"] = {"shop_ref": shop_ref, "confirmation_token": expected_token, "summary": summary}
            await self.save()
            return result("needs_confirmation",
                          "Read the summary and ask for explicit confirmation, then call again with confirmed=true",
                          summary=summary, confirmation_token=expected_token, booking_enabled=True)
        pending = s.get("pending_booking", {})
        token = (confirmation_token or "").strip()
        if pending.get("shop_ref") != shop_ref or pending.get("confirmation_token") != token or token != expected_token:
            s["pending_booking"] = {"shop_ref": shop_ref, "confirmation_token": expected_token, "summary": summary}
            await self.save()
            return result("needs_confirmation",
                          "Confirmation token did not match this booking summary. Read back and confirm again.",
                          summary=summary, confirmation_token=expected_token, booking_enabled=True)

        now = datetime.now()
        lines = [{
            "productId": x["product_id"],
            "plantId": int(x["plant_id"]),
            "productGroupId": x["group"],
            "noBagsRequested": int(x["bags"]),
            "quantityOfBagKg": x["kg_per_bag"],
            "farmlandPlotRegistryId": first_plot_id,
            "cropCode": first_crop_code,
        } for x in chosen]
        payload = {
            "seasonId": s["season"]["season"]["seasonId"],
            "year": now.year,
            "month": now.month,
            "requestedDatetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "applicantUserId": s["user"]["userId"],
            "contactNoForCommunication": mobile,
            "stateLgdCode": int(self.state["state_code"]),
            "districtLgdCode": self.land_map()[s["selection"][0]["land_ref"]]["districtLgdCode"],
            "subDistrictLgdCode": self.land_map()[s["selection"][0]["land_ref"]]["subDistrictLgdCode"],
            "villageLgdCode": self.land_map()[s["selection"][0]["land_ref"]]["villageLgdCode"],
            "selectedLandIds": [{
                "farmLandOwnershipId": x["farmLandOwnershipId"],
                "cropCode": x["cropCode"],
                "farmLandPlotRegistryId": x["farmlandPlotRegistryId"],
            } for x in selected_lands],
            "bookingRequest": [{"retailerId": shop["retailer_id"], "productLines": lines}],
            "GFRRecommendation": s.get("gfr", {}).get("recommendations", []),
            "gfrType": s.get("gfr", {}).get("gfrType"),
            "isProceedWithGfr": False,
        }

        s["booking_turn"] = self.question_id
        s["uncertain_booking"] = {"shop_ref": shop_ref, "summary": summary}
        await self.save()
        data = await self.call("POST", FERT, "/bharatVistar/createMultipleBookingV2", payload)
        if not isinstance(data, dict):
            raise FFSError("unavailable", "FFS booking returned an invalid response")
        results = data.get("bookingResults") or []
        public = {
            "is_valid": bool(data.get("isValid")),
            "message": data.get("message", ""),
            "bookings": [{
                "procurement_id": row.get("procurementId"),
                "booking_code": row.get("procurementApplicationCode"),
                "is_waitlisted": bool(row.get("isWaitlisted")),
            } for row in results if isinstance(row, dict)],
        }
        s.pop("pending_booking", None)
        s.pop("uncertain_booking", None)
        s["stage"] = "booking_done"
        s["last_booking_result"] = result("ok" if public["is_valid"] else "booking_failed",
                                          "Share booking result and reference code", booking=public, booking_enabled=True)
        await self.save()
        return deepcopy(s["last_booking_result"])

    async def run(self, operation, **kwargs):
        if not settings.ffs_base_url or not self.owner or not self.session_id:
            return result("unavailable", "FFS is not configured or the caller has no authenticated identity")
        try:
            lock_name = settings.redis_key_prefix + "ffs-lock:" + session_key(self.session_id)
            async with cache.client.lock(lock_name, timeout=50, blocking_timeout=1):
                async with asyncio.timeout(40):
                    self.state = await get_session(self.session_id)
                    if self.state and self.state.get("owner") != self.owner:
                        return result("unavailable", "This FFS session belongs to another authenticated caller")
                    async with httpx.AsyncClient(timeout=get_default_httpx_timeout(), follow_redirects=False) as client:
                        self.client = client
                        return await getattr(self, operation)(**kwargs)
        except FFSError as error:
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
        except (httpx.HTTPError, TimeoutError):
            status = "booking_uncertain" if self.state.get("uncertain_booking") else "partial_crop_update" if self.state.get("uncertain_plot") else "setup_incomplete" if self.state.get("token") and self.state.get("stage") == "loading" else "unavailable"
            return result(status, "FFS did not complete the request. Resume setup/crop reconciliation without resending OTP or blindly repeating writes")
        except (RedisError, ValueError, KeyError, TypeError, IndexError):
            return result("unavailable", "FFS state or response is unavailable; no booking was attempted")
