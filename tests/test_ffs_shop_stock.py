"""Stock discovery keeps partial shops and limits each response to three choices."""
import asyncio

from app.services import ffs
from agents.tools.ffs import ffs_get_entitlement, ffs_find_shops, ffs_create_booking
from tests.test_ffs import setup, context, login, selection


async def prepare():
    village, crops = selection(await login())
    await ffs_get_entitlement(context("stock-entitlement"), village, crops)


def test_below_entitlement_stock_stays_visible_and_can_be_booked(setup):
    setup.backend.stock = {i: (2, 0) for i in range(8)}

    async def run():
        await prepare()
        result = await ffs_find_shops(context("stock-search"))
        shops = result["data"]["shops"]
        assert result["status"] == "ok" and len(shops) == 3
        assert all(s["coverage"] == ["urea"] and not s["complete_supply"] for s in shops)
        assert all(s["stock"] == {"urea": 2, "DAP": 0} for s in shops)
        shop = shops[0]["shop_ref"]
        failed = await ffs_create_booking(context("stock-too-many"), shop,
            quantities=[ffs.BookingQuantity(product="urea", bags=3)])
        assert failed["status"] == "needs_confirmation"
        assert not setup.backend.booking_payloads
        preview = await ffs_create_booking(context("stock-preview"), shop,
            quantities=[ffs.BookingQuantity(product="urea", bags=1)])
        assert preview["status"] == "needs_confirmation"
        booked = await ffs_create_booking(context("stock-confirm"), shop, True)
        assert booked["status"] == "ok"
        assert setup.backend.booking_payloads[0]["bookingRequest"][0]["productLines"][0]["noBagsRequested"] == 1
    asyncio.run(run())


def test_search_expands_past_empty_village_and_ranks_stocked_shops_first(setup):
    setup.backend.stock = {0: (0, 0), 1: (0, 0), 2: (2, 0), 3: (0, 1),
                           4: (0, 0), 5: (0, 0), 6: (0, 4), 7: (3, 0)}

    async def run():
        await prepare()
        result = await ffs_find_shops(context("stock-search"))
        shops = result["data"]["shops"]
        assert [s["name"] for s in shops] == ["Shop 2", "Shop 3", "Shop 6"]
        assert [s["territory"] for s in shops] == ["T", "T", "D"]
        searches = [body for path, body in setup.backend.calls if path.endswith("getRetailersByProductAvailabilityV4")]
        assert [body["territoryLevel"] for body in searches] == ["V", "T", "D"]
    asyncio.run(run())


def test_zero_stock_shops_remain_fallbacks_after_all_territories_are_searched(setup):
    setup.backend.stock = {i: (0, 0) for i in range(8)}

    async def run():
        await prepare()
        first = await ffs_find_shops(context("stock-search"))
        assert len(first["data"]["shops"]) == 3
        assert all(not s["coverage"] for s in first["data"]["shops"])
        searches = [body for path, body in setup.backend.calls if path.endswith("getRetailersByProductAvailabilityV4")]
        assert [body["territoryLevel"] for body in searches] == ["V", "T", "D", "S"]
        assert await ffs_find_shops(context("stock-search")) == first
        second = await ffs_find_shops(context("stock-more"), "more")
        assert len(second["data"]["shops"]) == 3
        assert not ({s["shop_ref"] for s in first["data"]["shops"]} & {s["shop_ref"] for s in second["data"]["shops"]})
        blocked = await ffs_create_booking(context("stock-book"), first["data"]["shops"][0]["shop_ref"],
            quantities=[ffs.BookingQuantity(product="urea", bags=1)])
        assert blocked["status"] == "needs_confirmation" and not setup.backend.booking_payloads
    asyncio.run(run())


def test_widen_keeps_partial_stock_and_more_preserves_territory(setup):
    setup.backend.stock = {i: (2, 0) for i in range(8)}

    async def run():
        await prepare()
        initial = await ffs_find_shops(context("stock-initial"))
        wider = await ffs_find_shops(context("stock-widen"), "widen")
        assert [s["name"] for s in wider["data"]["shops"]] == ["Shop 6", "Shop 7"]
        assert all(s["territory"] == "D" and not s["complete_supply"] for s in wider["data"]["shops"])
        assert wider["data"]["complete_supply_only"] is False
        more = await ffs_find_shops(context("stock-more"), "more")
        assert more["data"]["shops"] == []
        assert len(initial["data"]["shops"]) == 3
    asyncio.run(run())


def test_pre_change_search_cache_is_rebuilt(setup):
    async def run():
        await prepare()
        await ffs_find_shops(context("stock-old"))
        state = await ffs.get_session("session-1")
        state["shops"].pop("stock_visibility")
        await setup.memory.set(ffs.session_key("session-1"), state, namespace=ffs.NAMESPACE)
        setup.backend.stock = {i: (2, 0) for i in range(8)}
        result = await ffs_find_shops(context("stock-new"))
        assert all(s["coverage"] == ["urea"] for s in result["data"]["shops"])
        state = await ffs.get_session("session-1")
        assert state["shops"]["stock_visibility"] == "all"
    asyncio.run(run())


def test_village_pages_are_exhausted_before_expanding_to_tehsil(setup, monkeypatch):
    import httpx
    import json

    setup.backend.stock = {0: (0, 0), 1: (0, 0)}
    original = setup.backend.respond

    async def respond(request):
        response = await original(request)
        if request.url.path.endswith("getRetailersByProductAvailabilityV4"):
            body = json.loads(request.content)
            if body["territoryLevel"] == "V":
                envelope = response.json()
                envelope["totalPages"] = 2
                if body["pageNo"] == 2:
                    row = envelope["data"][0]
                    row["retailerId"] = 100
                    row["retailerName"] = "Village second-page shop"
                    row["availableProducts"][0]["products"][0]["noBagsAvailable"] = 2
                    envelope["data"] = [row]
                return httpx.Response(200, json=envelope)
        return response

    class Client(setup.client_class):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", Client)

    async def run():
        await prepare()
        result = await ffs_find_shops(context("stock-search"))
        assert result["data"]["shops"][0]["name"] == "Village second-page shop"
        assert len(result["data"]["shops"]) == 3
        searches = [body for path, body in setup.backend.calls if path.endswith("getRetailersByProductAvailabilityV4")]
        assert [(b["territoryLevel"], b["pageNo"]) for b in searches] == [("V", 1), ("V", 2), ("T", 1)]
    asyncio.run(run())
