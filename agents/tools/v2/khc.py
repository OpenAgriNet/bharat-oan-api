"""KHC (Kisan Help Center) on Beckn v2.

Four of KHC's operations ride the network and seven do not. The split is not an
optimisation -- it is what the protocol leaves room for.

    Beckn v2        send OTP            -> init
                    verify OTP          -> confirm
                    file the grievance  -> support
                    read the status     -> status   (unbound, see below)

    Direct HTTPS    states, districts, sub-districts, villages,
                    schemes, sub-categories

    Catalog         the scheme's complaint categories, already published on
                    the resource, so no call is made to list them

A v1 registry row holds one ActionPlan per action name (pkg/model/model.go:114)
and BuildEndpoint has no templating (internal/common/util/endpoint.go:16-40), so
the eleven KHC operations cannot be eleven rows. v1 got around that by riding
two actions with a `request_type` discriminator in the payload; v2 has one named
endpoint per action and no discriminator, so the reference lists have no slot.
Publishing them is not an option either: the village tree is about 1.06 million
rows nationally against 64 per scheme for the complaint options.

So the lists are fetched straight from KHC, the way imd_advisory.py reaches
IMD_DB_URL, maps.py reaches PHOTON_HOST and npss.py reaches NPSS_BASE_URL with
its own credentials. The cost is that this app now holds KHC_BASE_URL and
KHC_AUTH_HEADER itself rather than only the registry doing so.

find_khc_location and get_khc_grievance_options were never KHC operations -- the
v1 provider backend composed them out of the flat lists. That composition moves
here, which is why this module walks the geography tree itself.

Status is published unbound. KHC's check-status is a POST whose only input is a
query parameter, which no registry row can express; migration-plan/open-issues.md
item 20 carries the question to KHC.
"""

import asyncio
import difflib
import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx
from langfuse import observe
from pydantic_ai.tools import RunContext

from agents.deps import FarmerContext
from agents.tools.khc import (
    SOURCE,
    _LANGUAGE_CODES,
    _loose,
    _render_location,
    _render_tree,
    _resolve_concern,
    _STATUS_ID_PROMPT,
)
from agents.tools.kcc import _mobile
from agents.tools.v2.aif import (
    GRIEVANCE_TYPE,
    ACTIVE,
    DRAFT,
    adapter_error,
    build_context,
    commitment_attributes,
    commitment_envelope,
    support_channel,
    support_envelope,
    grievance_context,
    post_action,
    _first_resource,
    _unusable,
)
from app.config import DEFAULT_HTTP_TIMEOUT
from helpers.utils import get_logger, to_ascii_digits

logger = get_logger(__name__)

_CODE = "khc-grievance"
_OFFER = "off:khc:grievance"
_WHO = "KHC"

_UNAVAILABLE = "KHC service is unavailable. Please try again later."
_UNCONFIRMED = (
    "The grievance submission could not be confirmed. Do not retry automatically: "
    "a second attempt can file the same grievance twice. Tell the farmer the "
    "submission is unconfirmed and that they can check the status shortly."
)


# --------------------------------------------------------------------------------------
# Direct reference lookups -- not Beckn
# --------------------------------------------------------------------------------------

# path, parent query parameter, id field, name field. The field names are
# lowercase and unseparated because that is what KHC returns -- verified
# against UAT on 2026-11-24, not transcribed from the provider backend, whose
# local snapshot is older than what is deployed.
_REFERENCE: Dict[str, Tuple[str, Optional[str], str, str]] = {
    "states": ("/v1/state/reference/states", None, "stateid", "statename"),
    "districts": ("/v1/state/reference/districts", "stateId", "districtid", "districtname"),
    "sub-districts": (
        "/v1/state/reference/sub-districts", "districtId", "subdistrictid", "subdistrictname",
    ),
    "villages": ("/v1/state/reference/villages", "subDistrictId", "villageid", "villagename"),
    "schemes": ("/v1/state/reference/schemes", "stateId", "scheme", "schemename"),
    "categories": ("/v1/state/reference/categories", "scheme", "problemcategory", "categoryname"),
    "sub-categories": (
        "/v1/state/reference/sub-categories", "problemCategory", "problemsubcategory",
        "subcategoryname",
    ),
}

PARENTS = {name: spec[1] for name, spec in _REFERENCE.items()}

_PAGE_SIZE = 50

# KHC's reference lists are administrative data that changes on the order of
# months, and one location walk reads a state's districts plus every
# sub-district's villages. Without this, finding a village costs ten round
# trips every time the farmer corrects a spelling.
_CACHE_TTL_SECONDS = 3600
_cache: Dict[Tuple[str, Optional[int]], Tuple[float, List[Dict[str, Any]]]] = {}


def khc_base() -> str:
    return os.getenv("KHC_BASE_URL", "").rstrip("/")


def _khc_header() -> str:
    """The whole Authorization value, as KHC wants it: "ApiKey <key>".

    One variable, not a key the caller dresses, because the adapter's static
    header scheme sends its variable verbatim and this is the same credential.
    """
    return os.getenv("KHC_AUTH_HEADER", "")


async def _reference(
    list_name: str, parent_id: Optional[int] = None
) -> Optional[List[Dict[str, Any]]]:
    """One KHC reference list, normalised to {id, name} rows.

    None means the call failed, which is not the same as an empty list: a state
    with no matching villages and a KHC that did not answer have to be told
    apart before anything is said to the farmer.
    """
    key = (list_name, parent_id)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    base, header = khc_base(), _khc_header()
    if not base or not header:
        logger.error("khc v2 reference lookup not configured: KHC_BASE_URL / KHC_AUTH_HEADER")
        return None

    path, param, id_field, name_field = _REFERENCE[list_name]
    params = {param: parent_id} if param and parent_id is not None else {}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = await client.get(
                base + path, params=params, headers={"Authorization": header}
            )
    except httpx.RequestError:
        logger.error(f"khc v2 {list_name} lookup failed to reach the provider")
        return None

    if response.status_code != 200:
        logger.error(f"khc v2 {list_name} lookup <- HTTP {response.status_code}")
        return None
    try:
        envelope = response.json()
    except ValueError:
        logger.error(f"khc v2 {list_name} lookup returned a body that is not JSON")
        return None

    # KHC answers with a status string -- "200 OK", not the number 200 -- in a
    # 200 response, so the envelope is checked as well as the HTTP code.
    if not str(envelope.get("status", "")).startswith("200"):
        logger.error(f"khc v2 {list_name} lookup refused: {str(envelope.get('status'))[:60]}")
        return None

    rows: List[Dict[str, Any]] = []
    for row in envelope.get("data") or []:
        if not isinstance(row, dict) or row.get(id_field) is None:
            continue
        entry = {"id": row[id_field], "name": str(row.get(name_field) or "").strip()}
        if list_name == "schemes":
            # The division belongs to the scheme row and is sent on the filing.
            # Reading it off a different row files against the wrong division.
            entry["division"] = row.get("division")
            entry["divisionName"] = row.get("divisionname")
        if entry["name"]:
            rows.append(entry)

    _cache[(list_name, parent_id)] = (time.monotonic(), rows)
    return rows


def _match(rows: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
    """Rows whose name matches what the farmer said, best tier only.

    Exact beats contained beats close spelling, so "Patna" does not come back
    alongside the six villages with Patna in the name.
    """
    wanted = _loose(text)
    if not wanted:
        return []
    exact = [row for row in rows if _loose(row["name"]) == wanted]
    if exact:
        return exact
    contained = [row for row in rows if wanted in _loose(row["name"])]
    if contained:
        return contained
    close = set(difflib.get_close_matches(wanted, [_loose(row["name"]) for row in rows], n=5, cutoff=0.8))
    return [row for row in rows if _loose(row["name"]) in close]


# --------------------------------------------------------------------------------------
# Composition -- what the v1 provider backend used to do server side
# --------------------------------------------------------------------------------------

async def _walk_to_district(
    state: str, district: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
    """(state row, district row, fields-JSON for an unfinished walk).

    The third value is a rendered reply when the walk cannot continue: no state
    given, a name that matched nothing or matched several. It is the same
    `fields` dict shape the v1 renderers read, so the wording the agent sees
    does not change with the transport.
    """
    states = await _reference("states")
    if states is None:
        return None, None, _UNAVAILABLE
    state_matches = _match(states, state) if state else []
    if len(state_matches) != 1:
        fields = {
            "needs": "state",
            "matches": json.dumps(state_matches or (states if state else [])),
        }
        return None, None, json.dumps(fields)

    state_row = state_matches[0]
    districts = await _reference("districts", state_row["id"])
    if districts is None:
        return state_row, None, _UNAVAILABLE
    district_matches = _match(districts, district) if district else []
    if len(district_matches) != 1:
        fields = {"needs": "district", "matches": json.dumps(district_matches)}
        return state_row, None, json.dumps(fields)

    return state_row, district_matches[0], None


# --------------------------------------------------------------------------------------
# Beckn transport
# --------------------------------------------------------------------------------------

async def _discover(action: str) -> Optional[Dict[str, Any]]:
    """The published KHC grievance resource, with its catalog provider."""
    body = {
        "context": build_context("discover", str(uuid.uuid4())),
        "message": {
            "intent": {
                "filters": {
                    "type": "jsonpath",
                    "expression": (
                        f'$.catalogs[*].resources[*] ? (@.descriptor.code == "{_CODE}")'
                    ),
                }
            }
        },
    }
    response = await post_action("discover", body, who=_WHO)
    if response.status_code != 200:
        logger.error(
            f"KHC v2 discover failed: HTTP {response.status_code} {response.text[:300]}"
        )
        return None
    return _first_resource(
        response.json(), usable=lambda candidate: _unusable(candidate, action), who=_WHO
    )


def published_categories(found: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The scheme's complaint categories as the catalog advertises them.

    This is the third leg of the split: the categories are small enough and
    stable enough to publish, so discover already carries them and no call is
    made to list them.
    """
    attributes = (found["resource"].get("resourceAttributes") or {})
    options = attributes.get("grievanceOptions") or {}
    return [
        {"id": int(entry["code"]), "name": entry.get("name") or ""}
        for entry in options.get("categories") or []
        if str(entry.get("code") or "").isdigit()
    ]


def published_scheme(found: Dict[str, Any]) -> Dict[str, Any]:
    return (found["resource"].get("resourceAttributes") or {}).get("scheme") or {}


def _transaction(ctx: RunContext[FarmerContext], mobile: str) -> str:
    """One transaction id across init, confirm and support for this mobile.

    The adapter keys the session it captures on confirm and sends on support by
    transaction id, so a fresh uuid on each call would file the grievance with
    no token.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"khc:{ctx.deps.session_id}:{mobile}"))


def _attributes(found: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    scheme = published_scheme(found)
    return {
        "@context": grievance_context(),
        "@type": GRIEVANCE_TYPE,
        "informationMode": "OnDemand",
        "scheme": {"code": scheme.get("code", ""), "name": scheme.get("name", "")},
        **extra,
    }


def _explain(response: httpx.Response, what: str) -> str:
    """What to tell the farmer when the adapter refused a KHC action."""
    code, message = adapter_error(response)
    logger.error(
        f"KHC v2 {what} refused: HTTP {response.status_code} "
        f"{code or 'no code'} {response.text[:500]}"
    )
    if code.startswith("BIZ_") and message:
        return message
    return _UNAVAILABLE


# --------------------------------------------------------------------------------------
# Tools -- same names and signatures as the v1 module, so no prompt changes
# --------------------------------------------------------------------------------------

@observe(name="tool:initiate_khc_otp", as_type="tool")
async def initiate_khc_otp(ctx: RunContext[FarmerContext], mobile_number: str = "") -> str:
    """Send a KHC OTP before grievance registration, to a mobile supplied by the farmer.

    Ask for the farmer's mobile if absent. Do not send OTPs for a status-only lookup.
    Call this only after scheme, location, concern, mapped sub-category, and farmer
    confirmation are complete for the current grievance.
    """
    mobile = _mobile(mobile_number)
    if not mobile:
        return "Ask the farmer for their 10-digit mobile number."
    if not ctx.deps.session_id:
        return "KHC registration requires an active conversation."

    found = await _discover("init")
    if not found:
        return _UNAVAILABLE

    body = commitment_envelope(
        "init",
        _transaction(ctx, mobile),
        DRAFT,
        _attributes(found, farmerMobileNumber=mobile),
        _OFFER,
        found,
    )
    response = await post_action("init", body, who=_WHO)
    if response.status_code != 200:
        return _explain(response, "OTP send")
    if not commitment_attributes(response).get("challenge", {}).get("issued"):
        logger.error("KHC v2 on_init carried no issued challenge")
        return _UNAVAILABLE
    return (
        "OTP sent successfully. Ask the farmer for the OTP received on their mobile. "
        "When they supply six digits, call verify_khc_otp with the same mobile and the supplied OTP. "
        "Do not reject a six-digit reply without a verification tool result."
    )


@observe(name="tool:verify_khc_otp", as_type="tool", capture_input=False, capture_output=False)
async def verify_khc_otp(
    ctx: RunContext[FarmerContext], mobile_number: str = "", otp: str = "",
) -> str:
    """Verify the KHC OTP received by the farmer. Sending must happen first.

    Use the same mobile number as initiate_khc_otp. The result identifies new versus
    existing farmers. Never invent OTPs or report verification without a tool result.
    While waiting for OTP, call this tool for a six-digit reply; do not ask the farmer
    to repeat a correctly formatted OTP without attempting verification. Preserve leading
    zeros. Only the tool result can establish acceptance, rejection or service failure.
    Grievance details and the mapped sub-category must already be confirmed. Only after successful
    verification, use isRegistered to decide: existing farmers submit directly; new farmers supply
    any missing name and gender before submission. Do not collect those fields before verification.
    """
    mobile = _mobile(mobile_number)
    if not mobile:
        return "Ask the farmer for their 10-digit mobile number."
    code = to_ascii_digits(str(otp or "")).strip()
    if not re.fullmatch(r"[0-9]{6}", code):
        return "Ask the farmer for the 6-digit OTP received on their mobile."
    if not ctx.deps.session_id:
        return "KHC registration requires an active conversation."

    found = await _discover("confirm")
    if not found:
        return _UNAVAILABLE

    body = commitment_envelope(
        "confirm",
        _transaction(ctx, mobile),
        ACTIVE,
        _attributes(
            found,
            farmerMobileNumber=mobile,
            challenge={"response": {"method": "OTP", "value": code}},
        ),
        _OFFER,
        found,
    )
    response = await post_action("confirm", body, who=_WHO)
    if response.status_code != 200:
        return _explain(response, "OTP verification")

    registered = commitment_attributes(response).get("applicant", {}).get("registered")
    if not isinstance(registered, bool):
        logger.error("KHC v2 on_confirm carried no registration state")
        return _UNAVAILABLE
    return (
        "Mobile verified successfully. Farmer is "
        + ("already registered" if registered else "not yet registered")
        + " with KHC. This is the last step: the scheme, location, complaint and sub-category were "
        "already confirmed, so do not ask for them again. "
        + (
            "isRegistered=true. Use farmer_type='existing'. Do not ask for name or gender. "
            "Call register_khc_grievance now with the confirmed details and the IDs already found."
            if registered else
            "isRegistered=false. Use farmer_type='new'. Ask only for the missing name, then gender "
            "(Male, Female or Other), one question at a time; reuse either if already supplied. "
            "Do not submit until both are known. Then call register_khc_grievance with those profile "
            "details, the confirmed grievance and the IDs already found."
        )
    )


@observe(name="tool:find_khc_location", as_type="tool")
async def find_khc_location(
    ctx: RunContext[FarmerContext], place: str, state: str = "", district: str = "",
    sub_district: str = "",
) -> str:
    """Find where a place is: its state, district, sub-district and village IDs, in one call.

    Use this first for the KHC grievance location, with the village or place name the
    farmer gave (ask state, then district, then village). Pass the state and district. When the farmer has
    picked a sub-district from a list, call again with the same village as place and that sub-district
    as sub_district; it then returns exactly one place. Names
    may have spacing, case or small spelling differences. A place that is a district also
    matches. If nothing matches, the result says what to ask next. Do not use for status.
    """
    place, state, district = place.strip(), state.strip(), district.strip()
    sub_district = sub_district.strip()
    if not place or len(place) > 200 or max(len(state), len(district), len(sub_district)) > 100:
        return "Ask the farmer for their village or place name (up to 200 characters)."
    if state and _loose(place) == _loose(state):
        return (
            "That is the state, not a village. Do not search with it. Ask 'Can you please share your district name' "
            "and then 'Can you please share the name of your village', and call this tool with the village as place."
        )
    if district and _loose(place) == _loose(district):
        return (
            "That is the district, not a village. Do not search with it. Ask 'Can you please share the name of your village' "
            "and call this tool with the village as place, this state and this district."
        )

    state_row, district_row, unfinished = await _walk_to_district(state, district)
    if unfinished == _UNAVAILABLE:
        return _UNAVAILABLE
    if unfinished:
        return f"{_render_location(json.loads(unfinished), place)}\n{SOURCE}"

    sub_districts = await _reference("sub-districts", district_row["id"])
    if sub_districts is None:
        return _UNAVAILABLE
    if sub_district:
        picked = _match(sub_districts, sub_district)
        sub_districts = picked or sub_districts

    state_ref = {"id": state_row["id"], "name": state_row["name"]}
    district_ref = {"id": district_row["id"], "name": district_row["name"]}
    matches: List[Dict[str, Any]] = []

    # A village is what was asked for, but the farmer may have named the
    # district or a sub-district instead. Those are reported at their own level
    # so the renderer can ask for the village rather than treat one as a match.
    if _loose(place) == _loose(district_row["name"]):
        matches.append({"level": "district", **district_ref, "state": state_ref})
    for row in _match(sub_districts, place):
        matches.append(
            {"level": "sub-district", "id": row["id"], "name": row["name"],
             "state": state_ref, "district": district_ref}
        )

    # One call per sub-district, concurrently: KHC has no village search, only
    # a list per sub-district, and a district holds about nine of them.
    village_lists = await asyncio.gather(
        *[_reference("villages", row["id"]) for row in sub_districts]
    )
    if any(rows is None for rows in village_lists):
        return _UNAVAILABLE
    for row, villages in zip(sub_districts, village_lists):
        for village in _match(villages, place):
            matches.append(
                {
                    "level": "village", "id": village["id"], "name": village["name"],
                    "state": state_ref, "district": district_ref,
                    "subDistrict": {"id": row["id"], "name": row["name"]},
                }
            )

    fields = {
        "matches": json.dumps(matches),
        "district": json.dumps(district_ref),
        "total": str(len(matches)),
        "needs": "" if matches else "geocode",
    }
    return f"{_render_location(fields, place)}\n{SOURCE}"


@observe(name="tool:get_khc_grievance_options", as_type="tool")
async def get_khc_grievance_options(
    ctx: RunContext[FarmerContext], state: str, scheme: str = "", concern: str = "", category: str = "",
) -> str:
    """Get a state's KHC grievance options in one call.

    Without a scheme it lists the state's schemes. With the scheme the farmer named
    (AIF, A.I.F. and the full name all work) it needs concern: the farmer's original complaint
    (up to 500 characters), translated to English only if needed. Do not shorten it or drop
    category-identifying words. Call it only after the scheme, state, district and
    village are done and the farmer has described the problem. Without concern it returns no
    categories and no next step. With concern it returns the API-backed category/sub-category
    catalogue for you to match semantically. Use a clear match without showing a list. Ask only
    when the meaning is ambiguous, showing categories if none fits or just the matched category's
    sub-categories if only the sub-category is unclear. After a category pick, call again with the
    same concern and category=<their pick> to narrow the catalogue. Use only returned IDs and
    preserve parent-child relationships. Show the issue and mapped sub-category in the confirmation summary.
    Prefer this over three separate lookup_khc_reference calls.
    """
    state, scheme, concern = state.strip(), scheme.strip(), " ".join(concern.split())
    category = category.strip()
    if not state:
        return (
            "Nothing to look up yet: no state has been given. Do not ask anything because of this message. "
            "Go back to the KHC step order and ask the first step that is not done yet; the scheme comes "
            "before the state."
        )
    if max(len(state), len(scheme), len(category)) > 100 or len(concern) > 500:
        return "Those details are too long. Ask the farmer to say it again in a few words."

    states = await _reference("states")
    if states is None:
        return _UNAVAILABLE
    state_matches = _match(states, state)
    if len(state_matches) != 1:
        fields = {"needs": "state", "matches": json.dumps(state_matches or states)}
        return f"{_render_tree(fields, scheme, concern)}\n{SOURCE}"
    state_row = state_matches[0]
    state_field = json.dumps({"id": state_row["id"], "name": state_row["name"]})

    schemes = await _reference("schemes", state_row["id"])
    if schemes is None:
        return _UNAVAILABLE
    if not scheme:
        fields = {
            "needs": "scheme", "state": state_field, "match": "unnamed",
            "schemes": json.dumps(schemes),
        }
        return f"{_render_tree(fields, scheme, concern)}\n{SOURCE}"

    scheme_matches = _match(schemes, scheme)
    if len(scheme_matches) != 1:
        fields = {
            "needs": "scheme", "state": state_field,
            "match": "none" if not scheme_matches else "several",
            "schemes": json.dumps(scheme_matches or schemes),
        }
        return f"{_render_tree(fields, scheme, concern)}\n{SOURCE}"
    scheme_row = scheme_matches[0]
    scheme_field = json.dumps(
        {"id": scheme_row["id"], "name": scheme_row["name"], "division": scheme_row.get("division")}
    )

    if not concern:
        fields = {"state": state_field, "scheme": scheme_field}
        return f"{_render_tree(fields, scheme, '')}\n{SOURCE}"

    resolved = _resolve_concern(ctx, concern, scheme)
    if resolved is None:
        logger.info(f"khc v2 concern refused session={ctx.deps.session_id} len={len(concern)}")
        return (
            "Nothing to show yet: the farmer has not described their problem in their own words in this "
            "chat, so that concern is not theirs. Do not show any list. Go back to the KHC step order and "
            "ask the next step that is not done yet. If the state, district and village are done, that step "
            "is 'Please tell me about your problem in your own words. I am here to help.' If the farmer "
            "already described the problem, do not ask again: call this tool again right away with their "
            "exact words (translated to English if needed) as concern."
        )
    concern = resolved

    # The catalog leg of the split: the published resource already carries this
    # scheme's categories, so discover answers instead of a reference call. Any
    # other scheme is not published and is read from KHC.
    categories: Optional[List[Dict[str, Any]]] = None
    found = await _discover("support")
    if found and str(published_scheme(found).get("code") or "") == str(scheme_row["id"]):
        categories = published_categories(found) or None
    if categories is None:
        categories = await _reference("categories", scheme_row["id"])
    if categories is None:
        return _UNAVAILABLE
    if category:
        categories = _match(categories, category) or categories

    sub_lists = await asyncio.gather(
        *[_reference("sub-categories", row["id"]) for row in categories]
    )
    if any(rows is None for rows in sub_lists):
        return _UNAVAILABLE
    tree = [
        {"id": row["id"], "name": row["name"], "subCategories": subs}
        for row, subs in zip(categories, sub_lists)
        if subs
    ]
    if not tree:
        return _UNAVAILABLE

    fields = {"state": state_field, "scheme": scheme_field, "categories": json.dumps(tree)}
    return f"{_render_tree(fields, scheme, concern)}\n{SOURCE}"


@observe(name="tool:lookup_khc_reference", as_type="tool")
async def lookup_khc_reference(
    ctx: RunContext[FarmerContext], reference_type: str,
    parent_id: Optional[int] = None, name: str = "", page: int = 1,
) -> str:
    """Fallback lookup of KHC reference lists and IDs by name. Prefer find_khc_location (places) and
    get_khc_grievance_options (scheme, category, sub-category); use this only if they are unavailable
    or fail, or the farmer explicitly asks to see alternatives to a matched category/sub-category.

    Lists and parents: states (none), districts (stateId), sub-districts (districtId),
    villages (subDistrictId), schemes (stateId), categories (scheme),
    sub-categories (problemCategory). Take the scheme and division from the SAME row.
    Keep the grievance flow's order: ask the farmer, match what they said, and show a list of
    names only when nothing matched or several matched. Never guess an ID or a scheme.

    name is a case- and whitespace-insensitive substring filter on the name (uttarpradesh matches
    Uttar Pradesh), not a search over a complaint: never pass the complaint as name. Use name=""
    to see the options when a filter finds nothing, and read the remaining pages (50 per page)
    before saying nothing matches. Do not ask the farmer for IDs.
    """
    if reference_type not in PARENTS or type(page) is not int or page <= 0:
        return "Choose a valid KHC reference list and positive page number."
    if PARENTS[reference_type] and (type(parent_id) is not int or not 0 < parent_id <= 9007199254740991):
        return f"Look up and confirm the parent {PARENTS[reference_type]} before requesting {reference_type}."
    if len(name) > 200:
        return "Use a location, scheme or category name of up to 200 characters."

    rows = await _reference(reference_type, parent_id)
    if rows is None:
        return _UNAVAILABLE

    # KHC serves these lists whole. The paging is the tool's, kept because the
    # agent prompt counts on 50 per page and a 1,000-row village list in one
    # reply crowds out everything else in the window.
    if name:
        wanted = _loose(name)
        rows = [row for row in rows if wanted in _loose(row["name"])]
    total = len(rows)
    options = rows[(page - 1) * _PAGE_SIZE: page * _PAGE_SIZE]

    hold_back = (
        "Do not show these names to the farmer unless they have described their problem in their own "
        "words and none of them fits it, or they explicitly requested alternatives. "
        "If they have not described it yet, ask for it first.\n"
        if reference_type in ("categories", "sub-categories") else ""
    )
    return (
        f"KHC {reference_type} options: {json.dumps(options, ensure_ascii=False)}\n{hold_back}"
        f"Total matches: {total}; page: {page} (50 per page).\n"
        "Keep the grievance flow's order. Match what the farmer said to these names, ignoring case, "
        "spacing and small spelling differences (uttarpradesh is Uttar Pradesh). If one matches, use it "
        "and do not ask again. Show names to choose from only when none or several match, only the names "
        "that fit the question being asked, and offer the next pages if there are more. If a filter finds "
        "nothing, look at the unfiltered list and remaining pages before saying nothing matches. "
        "If the farmer explicitly asks to change the sub-category, show the requested options even "
        "when one already matched; fetch all categories of the scheme and their sub-categories, "
        "including remaining pages. Update both the category and sub-category IDs from their pick "
        "and ask for confirmation again before submitting. "
        "Take the scheme and division from the same row, use only returned IDs, and never show IDs or ask "
        "the farmer for them. Do not list sub-districts as a step: ask for the village first. A failed "
        "lookup does not mean a scheme is unsupported; say that the lookup failed. "
        "Match the complaint semantically against these API-backed names before asking for a selection. "
        "Never invent a category, sub-category or ID. Keep the chosen sub-category under its returned "
        f"parent category, and ask only when the meaning is unclear.\n{SOURCE}"
    )


@observe(name="tool:check_khc_grievance_status", as_type="tool", capture_input=False, capture_output=False)
async def check_khc_grievance_status(
    ctx: RunContext[FarmerContext], grievance_number: str = "", otp: str = "",
) -> str:
    """Read a KHC grievance status. Not available on this network version yet.

    Still validates the grievance number so the farmer is not asked for an OTP or a
    resend for a number that was never usable. Ask for the full ID received by SMS
    if missing or invalid; keep slashes and leading zeros, e.g. UP/231026/5.
    Do not interpret the middle section as a calendar date, and never truncate, pad
    or replace characters to make an invalid number pass.
    """
    number = to_ascii_digits(str(grievance_number or "")).strip()
    if len(number) > 100 or not re.fullmatch(r"[A-Za-z]{2}/[0-9]{6}/0*[1-9][0-9]*", number):
        reply = _STATUS_ID_PROMPT
        if number:
            parts = number.split("/")
            reason = (
                f"The middle section has {len(parts[1])} digits; exactly six digits are required."
                if len(parts) == 3 and re.fullmatch(r"[0-9]+", parts[1]) and len(parts[1]) != 6
                else "Use two letters, exactly six digits in the middle and a positive numeric sequence, separated by two slashes."
            )
            reply = (
                f"The KHC grievance ID format is incorrect. {reason} "
                "Please check your SMS and share the complete grievance ID again. "
                "Explain this validation error in the farmer's language before asking for the correction. "
                "Do not repeat the initial missing-ID prompt or its example."
            )
        return (
            f"{reply} Ask for the grievance ID only once in this reply. "
            "Never truncate, pad or replace characters to make it valid. "
            "Do not restart scheme/service selection. "
            "Keep both slashes and any leading zeros. "
        )

    # KHC's check-status is a POST whose only input is a query parameter, which
    # no registry row can express -- verified against the provider on
    # 2026-11-24 and carried to KHC as open-issues.md item 20. Published
    # unbound rather than routed around, because the alternative read needs no
    # OTP at all and a grievance number alone must not open a farmer's record.
    logger.info("khc v2 status requested while the journey is unbound")
    return (
        "KHC grievance status cannot be checked through this service right now. "
        "Tell the farmer the status check is temporarily unavailable, that their "
        "grievance number is still valid, and that they can call the Kisan Call "
        "Centre on 1800-180-1551 to hear the status. Do not send or ask for an OTP, "
        "do not ask for the number again, and do not retry this tool in this conversation."
    )


@observe(name="tool:register_khc_grievance", as_type="tool")
async def register_khc_grievance(
    ctx: RunContext[FarmerContext],
    mobile_number: str = "",
    farmer_type: str = "",
    grievance_text: str = "",
    farmer_language: str = "",
    state_id: Optional[int] = None,
    village_id: Optional[int] = None,
    scheme_id: Optional[int] = None,
    division_id: Optional[int] = None,
    problem_category_id: Optional[int] = None,
    problem_subcategory_id: Optional[int] = None,
    farmer_name: str = "",
    gender: str = "",
    confirmed: bool = False,
) -> str:
    """Register a KHC grievance for a new or existing farmer, ONLY after explicit confirmation.

    First send and verify OTP with initiate_khc_otp / verify_khc_otp. Use the verified
    registration state for farmer_type (new/existing). Collect mobile, preferred
    language, location and grievance details. Resolve scheme/category/subcategory from
    the problem using the reference APIs; reuse scheme already provided and ask for
    scheme name only when missing. Never ask for internal IDs/codes from the farmer.
    Only after OTP verification reports isRegistered=false, collect any missing name and
    gender code for the new farmer. Existing farmers do not need these fields.
    All six numeric IDs MUST come from verified KHC master data, not LGD
    assumptions, memory, example requests or guesses. Resolve IDs using lookup_khc_reference.
    Reuse permitted browser location via reverse_geocode and KHC reference lookups
    when the farmer did not state a grievance location; otherwise ask for state/village.
    Summarize the details and obtain explicit confirmation before setting confirmed=True.
    farmer_language must use a supported KHC code such as hi or en; English language
    names such as Hindi or English are also normalized to codes before sending.
    gender uses M/F/O; Male/Female/Other are normalized for new farmers.
    The provider rejects creation without a verified mobile. Never retry automatically
    after an unconfirmed submission.
    """
    mobile = _mobile(mobile_number)
    if not mobile:
        return "Ask the farmer for their 10-digit mobile number."
    if farmer_type not in ("new", "existing"):
        return "Send and verify the farmer's mobile OTP first, then use the registration state returned by KHC."
    if not grievance_text.strip() or len(grievance_text) > 5000:
        return "Ask the farmer to describe their grievance in up to 5000 characters."
    farmer_language = farmer_language.strip().lower()
    farmer_language = _LANGUAGE_CODES.get(farmer_language, farmer_language)
    if farmer_language not in _LANGUAGE_CODES.values():
        return (
            "Ask the farmer for a supported KHC language from the OAN UI: Hindi, English, "
            "Marathi, Bengali, Gujarati, Kannada, Tamil, Telugu, Assamese or Malayalam. "
            "Maithili is available in the UI but is not currently accepted by the KHC provider; "
            "ask the farmer to choose a supported language without silently substituting one."
        )
    gender = gender.strip().upper()
    gender = {"MALE": "M", "FEMALE": "F", "OTHER": "O"}.get(gender, gender)
    if farmer_type == "new":
        if not farmer_name.strip():
            return "Ask the new farmer only for their missing name before registering."
        if gender not in ("M", "F", "O"):
            return "Ask the new farmer only for their gender (Male, Female or Other) before registering."
    ids = (state_id, village_id, scheme_id, division_id, problem_category_id, problem_subcategory_id)
    if any(type(value) is not int or not 0 < value <= 9007199254740991 for value in ids):
        return (
            "Verified KHC state, village, scheme, division, problem category and subcategory "
            "mappings are required. Call lookup_khc_reference to resolve them from the farmer's "
            "location and problem description: state -> schemes (take scheme and division from the same row) "
            "-> categories -> sub-categories, and state -> districts -> sub-districts -> villages. "
            "If the district is unknown, ask for it. Prefer find_khc_location, which finds the village and its "
            "sub-district from the village name; fetch sub-districts yourself only as a fallback when the "
            "village was not found, and then show them for selection by name. "
            "Fetch villages under the selected sub-district and match the village already supplied. "
            "Resolve all mappings before requesting final submission confirmation. "
            "Match returned names to the problem; ask a focused clarification only if ambiguous. "
            "Do not guess IDs. Reuse scheme already provided and ask for scheme only when missing. "
            "Never ask the farmer for internal IDs/codes."
        )
    if not confirmed:
        return "Summarize the farmer's grievance and registration details, then ask for explicit confirmation to submit."
    if not ctx.deps.session_id:
        logger.error("khc v2 registration missing session")
        return "KHC registration requires an active conversation. Please start a new conversation."

    found = await _discover("support")
    if not found:
        return _UNAVAILABLE

    # Ours, not KHC's. Derived from the session and the filing so a repeat of
    # the same confirmed submission carries the same reference and KHC can see
    # it is the same one.
    reference = uuid.uuid5(
        uuid.NAMESPACE_URL,
        ctx.deps.session_id + ":" + json.dumps(
            {"mobile": mobile, "text": grievance_text.strip(), "ids": list(ids)}, sort_keys=True
        ),
    )
    grievance = {
        "description": grievance_text.strip(),
        "language": farmer_language,
        "division": division_id,
        "category": {"code": str(problem_category_id)},
        "subCategory": {"code": str(problem_subcategory_id)},
    }
    extra: Dict[str, Any] = {
        "referenceNo": f"BV-{reference}",
        "farmerMobileNumber": mobile,
        "grievance": grievance,
        "location": {"stateId": state_id, "villageId": village_id},
    }
    if farmer_type == "new":
        extra["applicantName"] = farmer_name.strip()
        extra["applicantGender"] = gender

    attributes = _attributes(found, **extra)
    # The scheme on the filing is the one resolved from KHC's own list, not the
    # catalog's, because a farmer may be filing against a scheme this resource
    # advertises nothing about. The catalog's name is only carried when it is
    # that same scheme -- otherwise it would label one scheme's code with
    # another scheme's name.
    advertised = published_scheme(found)
    attributes["scheme"] = {"code": str(scheme_id)}
    if str(advertised.get("code", "")) == str(scheme_id) and advertised.get("name"):
        attributes["scheme"]["name"] = advertised["name"]

    # support carries its payload on channels, not on a contract: SupportAction
    # admits a support object and nothing else.
    body = support_envelope(_transaction(ctx, mobile), attributes, found)
    response = await post_action("support", body, who=_WHO)
    if response.status_code != 200:
        code, message = adapter_error(response)
        logger.error(
            f"KHC v2 filing refused: HTTP {response.status_code} {code or 'no code'} "
            f"{response.text[:500]}"
        )
        if code.startswith("BIZ_") and message:
            return message
        return _UNCONFIRMED

    grievance_no = support_channel(response).get("grievanceNo")
    if not grievance_no:
        logger.error("KHC v2 on_support carried no grievance number")
        return _UNCONFIRMED
    return (
        "Grievance registered with KHC.\n"
        f"Grievance number: {grievance_no}\n"
        "Give the farmer this number and ask them to keep it. It is KHC's number, not "
        f"our reference. Do not submit again.\n{SOURCE}"
    )
