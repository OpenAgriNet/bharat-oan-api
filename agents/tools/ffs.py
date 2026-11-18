"""Farmer decision tools for FFS fertilizer booking.

The workflow, session state and FFS credentials live in bharat-provider-backend
(provider `ffs-agri`), reached through the BAP like the other provider tools. This
module only validates input, calls the provider and runs the LLM crop/product matching
the provider asks for (`needs_resolution`).
"""
import copy
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from langfuse import observe
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai import RunContext
from redis.exceptions import RedisError

from agents.deps import FarmerContext
from agents.ffs_crop_resolver import resolve_crops
from agents.ffs_product_resolver import resolve_products
from agents.tools.aif import _descriptor, _endpoint, _outcome_tag, _values
from app.core.cache import cache
from helpers.langfuse_tracing import lf_update_current_observation
from helpers.utils import get_logger, to_ascii_digits

logger = get_logger(__name__)

PROVIDER_ID = "ffs-agri"
DOMAIN = "schemes:vistaar"
ITEM_ID = "ffs-booking"
ACTIVE_NAMESPACE = "ffs-active"
ACTIVE_TTL = 1200  # matches the provider's session lifetime
# The provider bounds a step to 40s; leave room for the BAP hop.
TIMEOUT = httpx.Timeout(10, read=60)
# State/UT master snapshot from FFS getStateLgdMaster, verified 2026-11-11. Used for the
# tool prompt; the provider validates the code independently.
STATE_CODES = json.loads((Path(__file__).resolve().parents[2] / "assets/data/ffs_states.json").read_text())
FAILED_RESOLUTION = {
    "crops": "Crop resolution failed; no crops were saved. Retry without restarting OTP login",
    "products": "Product matching failed; retry without restarting login. No order or booking was submitted",
}


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


def _tag_value(value):
    """Beckn tag values are strings: scalars as text, lists and objects as JSON."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, default=lambda m: m.model_dump())


def _loggable(payload):
    """Copy of the payload with the OTP masked — OTP values are never written to logs."""
    redacted = copy.deepcopy(payload)
    for tag in redacted["message"]["order"]["fulfillments"][0]["customer"]["person"]["tags"]:
        if tag["descriptor"]["code"] == "otp":
            tag["value"] = "****"
    return redacted


def _build_payload(transaction_id, session_id, question_id, **tags):
    """Beckn envelope for /init. Tags carry request_type and the FFS inputs."""
    return {
        "context": {
            "domain": DOMAIN,
            "action": "init",
            "version": "1.1.0",
            "bap_id": os.getenv("BAP_ID"),
            "bap_uri": os.getenv("BAP_URI"),
            "bpp_id": os.getenv("BPP_ID"),
            "bpp_uri": os.getenv("BPP_URI"),
            "transaction_id": transaction_id,
            "message_id": str(uuid.uuid4()),
            "timestamp": str(int(datetime.now(timezone.utc).timestamp())),
            "ttl": "PT10M",
            "location": {
                "country": {"code": "IND"},
                "city": {"code": "*"},
            },
            "tags": {
                "session_id": session_id or "",
                "question_id": question_id or "",
            },
        },
        "message": {
            "order": {
                "provider": {"id": PROVIDER_ID},
                "items": [{"id": ITEM_ID}],
                "fulfillments": [
                    {
                        "customer": {
                            "person": {
                                "tags": [
                                    {"descriptor": {"code": code}, "value": value}
                                    for code, value in tags.items()
                                    if value
                                ]
                            }
                        }
                    }
                ],
            }
        },
    }


async def _provider(session_id, question_id, operation, args, diagnostics):
    """One provider step through the BAP; returns its decoded result."""
    transaction_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "ffs:" + session_id))
    tags = {key: _tag_value(value) for key, value in args.items()}
    payload = _build_payload(transaction_id, session_id, question_id, request_type=operation, **tags)
    endpoint = _endpoint("init")
    logger.info(f"[FFS INIT] Request URL: {endpoint}")
    logger.info(f"[FFS INIT] Request Payload: {json.dumps(_loggable(payload), ensure_ascii=False)}")
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
        response = await client.post(endpoint, json=payload)
    logger.info(f"[FFS INIT] Response Status: {response.status_code}")
    logger.info(f"[FFS INIT] Response Payload: {response.text}")
    response.raise_for_status()
    tag = _outcome_tag(response.json(), "init")
    code, message = _descriptor(tag)
    if code != "ffs_result":
        raise ValueError(message or "FFS provider returned no outcome")
    fields = _values(tag)
    diagnostics.update(json.loads(fields.get("diagnostics", "{}")))
    return json.loads(fields["result"])


async def _resolve(session_id, question_id, request):
    """LLM interpretation of farmer wording against the provider's catalogue."""
    names, catalogue = request["names"], request["catalogue"]
    if request["kind"] == "crops":
        matches = await resolve_crops(names, catalogue, state_code=request["state_code"],
                                      session_id=session_id, question_id=question_id)
        return [{"name": name, "status": m.status, "crop_code": m.crop_code, "candidates": m.candidates}
                for name, m in zip(names, matches)]
    matches = await resolve_products(names, catalogue, session_id=session_id, question_id=question_id)
    return [{"name": name, "status": m.status, "product_id": m.product_id, "candidates": m.candidates}
            for name, m in zip(names, matches)]


async def _call_provider(session_id, question_id, operation, kwargs, diagnostics):
    if not session_id or not os.getenv("BAP_ENDPOINT"):
        diagnostics["reason"] = "missing_configuration_or_session"
        return result("unavailable", "FFS is not configured or the conversation has no session ID")
    # Digits in any script reach the provider as ASCII; its validation is strict.
    for field in ("farmer_id", "state_lgd_code", "otp"):
        if kwargs.get(field) is not None:
            kwargs[field] = to_ascii_digits(kwargs[field])
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    try:
        await cache.set("ffs:" + session_id, True, ttl=ACTIVE_TTL, namespace=ACTIVE_NAMESPACE)
    except RedisError:
        logger.warning("ffs.active_marker_failed")
    try:
        answer = await _provider(session_id, question_id, operation, kwargs, diagnostics)
        if answer.get("status") == "needs_resolution":
            kind = answer["data"]["kind"]
            try:
                kwargs["resolution"] = await _resolve(session_id, question_id, answer["data"])
            except Exception as error:
                diagnostics[kind + "_resolution_failed"] = type(error).__name__
                return result("unavailable", FAILED_RESOLUTION[kind])
            answer = await _provider(session_id, question_id, operation, kwargs, diagnostics)
            if answer.get("status") == "needs_resolution":
                return result("unavailable", FAILED_RESOLUTION[kind])
        return answer
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        diagnostics["error_type"] = type(error).__name__
        if operation == "create_booking":
            return result("booking_uncertain", "Could not confirm this booking response. The same farmer-approved request may be retried; FFS rejects existing or parallel bookings. Do not claim that nothing was booked")
        return result("unavailable", "FFS did not complete the request. Resume setup/crop reconciliation without resending OTP or blindly repeating writes")


async def prepare_chat_turn(session_id):
    """Restore FFS workflow context for the model prompt; empty unless this session used FFS."""
    if not session_id or not os.getenv("BAP_ENDPOINT"):
        return {}
    try:
        if not await cache.get("ffs:" + session_id, namespace=ACTIVE_NAMESPACE):
            return {}
        return await _provider(session_id, "", "context", {}, {})
    except (RedisError, httpx.HTTPError, ValueError, KeyError, TypeError):
        logger.warning("ffs.context_unavailable")
        return {}


async def _run(ctx, operation, **kwargs):
    metadata = {"tool": "ffs." + operation, "session_id": ctx.deps.session_id,
                "question_id": ctx.deps.question_id, "channel": "chat", "operation": operation}
    diagnostics = {}
    answer = None
    try:
        if "ffs.booking" not in ctx.deps.skills.active:
            answer = result("unavailable", "Load the enabled ffs.booking skill before using FFS tools")
            diagnostics["reason"] = "skill_not_loaded"
        else:
            answer = await _call_provider(ctx.deps.session_id, ctx.deps.question_id, operation, kwargs, diagnostics)
        return answer
    finally:
        # @observe records input/output; add the provider diagnostics.
        lf_update_current_observation(metadata={**metadata, **diagnostics})


@observe(name="tool:ffs_send_otp", as_type="tool")
async def ffs_send_otp(ctx: RunContext[FarmerContext], farmer_id: str, state_lgd_code: str, resend: bool = False) -> dict:
    """After FFS opt-in, request one SMS OTP using the 11-digit Farmer ID and state LGD code.

    For any fertilizer purchase intent, first ask whether they want to buy through
    FFS and wait for agreement. Explain that this integration offers urea/DAP and
    requires a Farmer ID. Ask for missing login details one question at a time.
    Call this identifier Farmer ID, never FFS ID.
    Interpret state names, local languages, clear spelling/ASR variants or places
    using the state/LGD list below; pass the code as a string, never a state name.
    Ask for the state before the Farmer ID. If the state is clearly not in the list,
    do not call this tool; tell the farmer the FFS service is not available in their state.
    If the intended state or Farmer ID is unclear, ask the farmer; do not guess.
    The list is the provider's geographic master, not proof of farmer eligibility.
    FFS login is separate from AgriStack. Do not invent an ID. Use resend only
    after the farmer requests another code, including paraphrases in any language;
    a negation such as "don't resend" is not a resend request. After login expiry
    or an FFS OTP rejection, a new send call works without resend=True. Otherwise,
    use resend=True for a requested replacement of an already-sent code. Never
    send another code automatically on rejection; follow the farmer's request.
    """
    return await _run(ctx, "send_otp", farmer_id=farmer_id, state_lgd_code=state_lgd_code, resend=resend)


# One provider snapshot supplies both model guidance and code membership validation.
ffs_send_otp.__doc__ += "\nState LGD codes:\n" + "\n".join(
    f"{name}: {code}" for code, name in STATE_CODES.items()
)


@observe(name="tool:ffs_verify_otp", as_type="tool")
async def ffs_verify_otp(ctx: RunContext[FarmerContext], otp: str | None = None) -> dict:
    """Verify the FFS OTP supplied in this turn, then load villages, plots and crops.

    Read the OTP from the farmer's current message and pass six digits as a string,
    preserving leading zeros. Interpret clear spoken numbers, e.g. "double zero
    one two three four" as "001234". Ask for clarification if incomplete or ambiguous;
    never invent digits or try several guesses. FFS validates whether it is correct.
    Omit otp only to resume incomplete setup with an already-valid login; do not
    ask for another OTP for setup_incomplete. Do not echo the OTP in your reply.
    """
    return await _run(ctx, "verify_otp", otp=otp)


@observe(name="tool:ffs_get_entitlement", as_type="tool")
async def ffs_get_entitlement(ctx: RunContext[FarmerContext], village_ref: str,
                              crop_selections: list[CropSelection] | None = None,
                              crop_name: str | None = None, crop_code: str | None = None,
                              selected_plots_only: bool = False) -> dict:
    """Save farmer-selected crops on FFS plots and return urea/DAP bag entitlement.

    This booking flow supports one village at a time; tell the farmer this.
    If they ask to add another village, ask which village to use before calling.
    An explicit switch replaces the previous selection; it never combines them.
    Do not split a multi-village request into separate calls or add their allowances.
    Report the returned village scope and its allowance only.

    Once village and planned crops are known, call automatically; no permission
    question is needed. This WRITES crop mappings. For one crop, pass village_ref,
    crop_name and optional crop_code: the provider expands all plots in that village.
    Do not enumerate or ask the farmer to confirm every plot. For multiple crops,
    supply crop_selections with one mapping per plot. Only when the farmer explicitly
    asks for fewer plots, set selected_plots_only=true and supply those mappings.
    Prefer either village-wide crop fields or per-plot mappings. FFS decides eligibility;
    do not invent exclusions from land-status labels. If FFS rejects a plot,
    explain the affected plot/reason and ask the farmer what to do. Change to a
    subset only on their instruction; do not silently omit or retry a rejected plot.

    For wheat, maize or paddy (including local-language names), pass crop_code from
    common_crops, either at village level or inside each per-plot mapping. This
    bypasses the specialist. For other crops omit the code and preserve the farmer's
    wording/descriptors. Ambiguity returns candidates before any write. Partial
    updates can resume without new OTP login. Eligibility is not an order: explain
    returned allowances, collect requested products/bags and confirm before shops.
    """
    return await _run(ctx, "entitlement", village_ref=village_ref, crop_selections=crop_selections,
                      crop_name=crop_name, crop_code=crop_code, selected_plots_only=selected_plots_only)


@observe(name="tool:ffs_find_shops", as_type="tool")
async def ffs_find_shops(ctx: RunContext[FarmerContext], action: Literal["initial", "more", "widen"] = "initial",
                         order: list[OrderItem] | None = None, shop_ref: str | None = None) -> dict:
    """Store the farmer's order and find up to three shops for that order.

    Eligibility is per product group; an order is specific products and bags.
    Collect products/counts ONLY from the farmer's words, reusing earlier requests.
    Never copy allowance into an order or invent quantities from a shop-choice yes.
    Confirm their product/count choice before searching unless already confirmed.
    Pass order on initial search or an explicit correction; more/widen reuse it.
    order is a JSON ARRAY, for example:
    [{"product": "urea", "product_id": 1089, "bags": 1}].
    Each item uses product, not name; do not wrap the array in a products object.
    Wait for this tool's result before calling ffs_create_booking. Never call both
    tools in one response: booking must consume the successfully updated order.
    On correction pass the chosen shop_ref if available. Stable shop_ref identifies
    the retailer; numbers 1–3 only index the displayed list. Keep that shop and
    book an agreed correction directly. FFS checks stock when booking; cached
    shop stock must not block the correction. Search alternatives if requested.
    Never silently switch to the first retailer in a new list.

    Common product IDs (all states): generic urea = Neem Coated Urea, 1089, 45 kg;
    generic DAP = DAP, 1005, 50 kg. Pass these IDs directly with product and bags.
    Named variants (sulphur-coated urea, zinc DAP, MAP, TSP) are different products:
    preserve the farmer's words and omit product_id to invoke product resolution.
    For ambiguity use a returned candidate ID; never invent an ID or substitute.
    Product lookup still supplies actual plant IDs, weights and local availability.
    Discovery probes one-bag availability for urea, DAP and ordered products;
    these search probes are not order quantities and never change the order.
    Bags across products in one group must fit its offeredBags allowance. If over,
    explain the returned limit and ask for a lower choice; never cap it yourself.

    Keep the returned ranking within village, tehsil, district, then state:
    full-order supply first; within each supply class, both urea/DAP shops,
    then single-product shops, then stock volumes internally. Discovery pages until three distinct shops carry an ordered
    product, or every territory is exhausted. Lower-stock shops still count;
    unrelated, zero-stock or unknown-stock rows do not fill the three choices.
    More continues from the saved cursor, finishing a territory before widening.
    Never reveal stock counts. Present only numbered returned shops,
    their level, ordered-product availability and full/partial/unknown supply.
    More gets the next options; widen moves further. A shop choice reserves nothing.
    After a clear booking rejection, use the farmer's corrected order here, then
    book directly when their instruction supplies agreement; no repeated approval.
    """
    return await _run(ctx, "find_shops", action=action, order=order, shop_ref=shop_ref)


@observe(name="tool:ffs_create_booking", as_type="tool")
async def ffs_create_booking(ctx: RunContext[FarmerContext], shop_ref: str,
                             confirmed: bool = False) -> dict:
    """Submit the one stored order at the chosen shop after one confirmation.

    There is no quantity argument: shop search and booking read the same order.
    If the farmer changes products or bags, update that order with ffs_find_shops.
    Wait for that tool's successful result before invoking this tool. Never send
    an order update and a booking together in the same model response.
    Ask once whether to book the order at this shop. An explicit instruction such
    as 'book it from the first shop' already supplies agreement: confirmed=True.
    No local confirmation token, preview prerequisite, expiry or extra turn.
    confirmed=False can return a summary if agreement is missing.
    Do not append another-product offers to the confirmation question.
    Follow the compact provider outcome. Never silently change quantities, repeat
    an identical rejection or reveal shop stock volumes. Clear rejections allow
    a farmer-selected correction. FFS enforces existing and parallel bookings;
    every confirmed attempt reaches FFS, including a retry after an unknown outcome.
    After confirmed or waitlisted success, replace general follow-up suggestions
    with a neutral "Can I help with anything else?" in the farmer's language.
    Never suggest another fertilizer, extra bags, another shop or another booking.
    If the farmer asks
    for another product/order, submit their actual choice and explain FFS's fresh
    response. Never describe an earlier receipt as a new booking. A timeout does
    not prove failure; a retry uses the same approved order, without another
    confirmation round. Do not keep retrying an identical provider rejection.
    """
    return await _run(ctx, "create_booking", shop_ref=shop_ref, confirmed=confirmed)
