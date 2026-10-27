from __future__ import annotations

from datetime import datetime, timedelta, timezone

import dotenv
import pytest

from tests.test_production_reconciliation import load_module


@pytest.fixture
def mandi(load_module, monkeypatch):
    monkeypatch.setattr(dotenv, "load_dotenv", lambda: None)
    module = load_module("agents/tools/mandi.py")

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 10, 27, 12, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", FixedDatetime)
    return module


def _today_ist_str(mandi) -> str:
    return mandi.datetime.now(mandi._IST).date().strftime("%d-%m-%Y")


def _days_ago_str(mandi, days: int) -> str:
    return (mandi.datetime.now(mandi._IST).date() - timedelta(days=days)).strftime("%d-%m-%Y")


def _make_context(mandi) -> mandi.Context:
    return mandi.Context(
        action="search",
        timestamp="2026-07-26T00:00:00.000Z",
        message_id="m1",
        transaction_id="t1",
        domain="schemes:vistaar",
        version="1.1.0",
    )


def _make_item(mandi, item_id: str, arrival_date: str | None = None, commodity: str = "Onion", modal_price: str = "2000") -> mandi.MandiItem:
    tag_list = [
        mandi.TagItem(descriptor=mandi.Descriptor(code="Commodity"), value=commodity),
        mandi.TagItem(descriptor=mandi.Descriptor(code="Market"), value="Lasalgaon"),
        mandi.TagItem(descriptor=mandi.Descriptor(code="Modal Price"), value=modal_price),
    ]
    if arrival_date:
        tag_list.append(mandi.TagItem(descriptor=mandi.Descriptor(code="Arrival Date"), value=arrival_date))
    return mandi.MandiItem(
        id=item_id,
        descriptor=mandi.Descriptor(name=commodity),
        tags=[mandi.Tag(descriptor=mandi.Descriptor(code="attributes"), list=tag_list)],
    )


class TestParseMandiDate:
    def test_parses_valid_ddmmyyyy(self, mandi):
        assert mandi._parse_mandi_date("25-07-2026") == datetime(2026, 7, 25).date()

    def test_returns_none_for_empty_or_blank(self, mandi):
        assert mandi._parse_mandi_date(None) is None
        assert mandi._parse_mandi_date("") is None
        assert mandi._parse_mandi_date("   ") is None

    def test_returns_none_for_unparseable_string(self, mandi):
        assert mandi._parse_mandi_date("not-a-date") is None


class TestResolveDateRange:
    def test_single_date_asks_for_exactly_that_day(self, mandi):
        assert mandi._resolve_date_range("25-07-2026") == ("25-07-2026", "25-07-2026")

    def test_no_price_date_defaults_from_date_to_max_range_ago(self, mandi):
        from_date, to_date = mandi._resolve_date_range(None)
        assert from_date == _days_ago_str(mandi, mandi.MAX_DATE_RANGE_DAYS)
        assert to_date == _today_ist_str(mandi)

    def test_recent_price_date_is_used_as_is(self, mandi):
        recent = _days_ago_str(mandi, 5)
        from_date, to_date = mandi._resolve_date_range(recent)
        assert (from_date, to_date) == (recent, recent)

    def test_old_single_date_is_not_moved(self, mandi):
        too_old = _days_ago_str(mandi, mandi.MAX_DATE_RANGE_DAYS + 100)
        assert mandi._resolve_date_range(too_old) == (too_old, too_old)

    def test_future_single_date_is_capped_at_today(self, mandi):
        assert mandi._resolve_date_range(_days_ago_str(mandi, -3)) == (_today_ist_str(mandi), _today_ist_str(mandi))

    def test_price_date_exactly_at_boundary_is_not_clamped(self, mandi):
        boundary = _days_ago_str(mandi, mandi.MAX_DATE_RANGE_DAYS)
        from_date, _ = mandi._resolve_date_range(boundary)
        assert from_date == boundary

    def test_invalid_price_date_string_falls_back_to_latest_available(self, mandi):
        from_date, to_date = mandi._resolve_date_range("not-a-real-date")
        assert from_date == _days_ago_str(mandi, mandi.MAX_DATE_RANGE_DAYS)
        assert to_date == _today_ist_str(mandi)

    def test_explicit_range_uses_both_ends(self, mandi):
        start, end = _days_ago_str(mandi, 20), _days_ago_str(mandi, 10)
        from_date, to_date = mandi._resolve_date_range(start, end)
        assert (from_date, to_date) == (start, end)

    def test_reversed_range_is_swapped(self, mandi):
        start, end = _days_ago_str(mandi, 10), _days_ago_str(mandi, 20)
        from_date, to_date = mandi._resolve_date_range(start, end)
        assert (from_date, to_date) == (end, start)

    def test_range_end_in_the_future_is_capped_at_today(self, mandi):
        start = _days_ago_str(mandi, 5)
        from_date, to_date = mandi._resolve_date_range(start, _days_ago_str(mandi, -10))
        assert (from_date, to_date) == (start, _today_ist_str(mandi))

    def test_range_wider_than_max_is_clamped_from_the_end(self, mandi):
        start, end = _days_ago_str(mandi, mandi.MAX_DATE_RANGE_DAYS + 40), _days_ago_str(mandi, 5)
        from_date, to_date = mandi._resolve_date_range(start, end)
        assert from_date == _days_ago_str(mandi, mandi.MAX_DATE_RANGE_DAYS + 5)
        assert to_date == end

    def test_range_of_exactly_max_days_is_allowed(self, mandi):
        start, end = _days_ago_str(mandi, mandi.MAX_DATE_RANGE_DAYS + 5), _days_ago_str(mandi, 5)
        assert mandi._range_too_long(start, end) is None
        assert mandi._resolve_date_range(start, end) == (start, end)

    def test_range_end_without_start_searches_back_from_the_end(self, mandi):
        end = _days_ago_str(mandi, 10)
        from_date, to_date = mandi._resolve_date_range(None, end)
        assert from_date == _days_ago_str(mandi, mandi.MAX_DATE_RANGE_DAYS + 10)
        assert to_date == end


class TestMandiRequestPayload:
    def _make_request(self, mandi, **overrides):
        defaults = dict(
            latitude=20.011,
            longitude=73.79,
            location_name="Nashik",
            commodity_name="Onion",
            price_date=None,
            session_id="sess-1",
            question_id="q-1",
        )
        defaults.update(overrides)
        return mandi.MandiRequest(**defaults)

    def _set_bap_env(self, mandi, monkeypatch):
        monkeypatch.setenv("BAP_ID", "bap-test")
        monkeypatch.setenv("BAP_URI", "https://bap.test")
        monkeypatch.setenv("BPP_ID", "bpp-test")
        monkeypatch.setenv("BPP_URI", "https://bpp.test")

    def test_payload_uses_from_date_to_date_tags(self, mandi, monkeypatch):
        self._set_bap_env(mandi, monkeypatch)

        payload = self._make_request(mandi, price_date="25-07-2026").get_payload()

        tags = payload["message"]["intent"]["tags"]
        assert tags == [
            {"code": "from_date", "value": "25-07-2026"},
            {"code": "to_date", "value": "25-07-2026"},
        ]

    def test_payload_uses_the_requested_range_ends(self, mandi, monkeypatch):
        self._set_bap_env(mandi, monkeypatch)
        start, end = _days_ago_str(mandi, 20), _days_ago_str(mandi, 10)

        payload = self._make_request(mandi, price_date=start, price_date_to=end).get_payload()

        tags = payload["message"]["intent"]["tags"]
        assert tags == [
            {"code": "from_date", "value": start},
            {"code": "to_date", "value": end},
        ]

    def test_blank_price_date_to_is_treated_as_no_range(self, mandi, monkeypatch):
        self._set_bap_env(mandi, monkeypatch)
        start = _days_ago_str(mandi, 5)

        payload = self._make_request(mandi, price_date=start, price_date_to="   ").get_payload()

        tags = payload["message"]["intent"]["tags"]
        assert tags == [
            {"code": "from_date", "value": start},
            {"code": "to_date", "value": start},
        ]

    def test_payload_context_fields(self, mandi, monkeypatch):
        self._set_bap_env(mandi, monkeypatch)

        payload = self._make_request(mandi).get_payload()
        context = payload["context"]

        assert context["domain"] == "schemes:vistaar"
        assert context["action"] == "search"
        assert context["bap_id"] == "bap-test"
        assert context["bpp_id"] == "bpp-test"
        assert context["tags"] == {"session_id": "sess-1", "question_id": "q-1"}

    def test_payload_intent_item_and_fulfillment(self, mandi, monkeypatch):
        self._set_bap_env(mandi, monkeypatch)

        payload = self._make_request(mandi, location_name="Pune", commodity_name="Wheat").get_payload()
        intent = payload["message"]["intent"]

        assert intent["category"]["descriptor"]["code"] == "price-discovery"
        assert intent["item"]["descriptor"]["name"] == "Wheat"
        assert intent["fulfillment"]["end"]["location"]["descriptor"]["name"] == "Pune"
        assert intent["fulfillment"]["end"]["location"]["gps"] == "20.011,73.79"

    def test_blank_price_date_treated_as_latest_available(self, mandi, monkeypatch):
        self._set_bap_env(mandi, monkeypatch)

        payload = self._make_request(mandi, price_date="   ").get_payload()

        tags = payload["message"]["intent"]["tags"]
        assert tags == [
            {"code": "from_date", "value": _days_ago_str(mandi, mandi.MAX_DATE_RANGE_DAYS)},
            {"code": "to_date", "value": _today_ist_str(mandi)},
        ]


class TestItemMatchesRequestedDate:
    def test_no_requested_date_matches_everything(self, mandi):
        item = _make_item(mandi, "1", arrival_date="20-07-2026")
        assert mandi._item_matches_requested_date(item, None) is True
        assert mandi._item_matches_requested_date(item, "  ") is True

    def test_matches_exact_arrival_date(self, mandi):
        item = _make_item(mandi, "1", arrival_date="20-07-2026")
        assert mandi._item_matches_requested_date(item, "20-07-2026") is True

    def test_does_not_match_different_arrival_date(self, mandi):
        item = _make_item(mandi, "1", arrival_date="19-07-2026")
        assert mandi._item_matches_requested_date(item, "20-07-2026") is False

    def test_item_without_arrival_date_does_not_match_specific_request(self, mandi):
        item = _make_item(mandi, "1", arrival_date=None)
        assert mandi._item_matches_requested_date(item, "20-07-2026") is False


class TestClosestAvailableDate:
    def test_returns_none_when_no_items_have_a_parseable_date(self, mandi):
        items = [_make_item(mandi, "1", arrival_date=None)]
        assert mandi._closest_available_date(items, datetime(2026, 7, 20).date()) is None

    def test_returns_the_only_available_date(self, mandi):
        items = [_make_item(mandi, "1", arrival_date="18-07-2026")]
        assert mandi._closest_available_date(items, datetime(2026, 7, 20).date()) == datetime(2026, 7, 18).date()

    def test_picks_the_nearest_of_several_dates(self, mandi):
        items = [
            _make_item(mandi, "1", arrival_date="15-07-2026"),
            _make_item(mandi, "2", arrival_date="19-07-2026"),
            _make_item(mandi, "3", arrival_date="10-07-2026"),
        ]
        assert mandi._closest_available_date(items, datetime(2026, 7, 20).date()) == datetime(2026, 7, 19).date()

    def test_ties_prefer_the_more_recent_date(self, mandi):
        items = [
            _make_item(mandi, "1", arrival_date="18-07-2026"),
            _make_item(mandi, "2", arrival_date="22-07-2026"),
        ]
        assert mandi._closest_available_date(items, datetime(2026, 7, 20).date()) == datetime(2026, 7, 22).date()


class TestArrivalDateSortKey:
    def test_undated_items_sort_last(self, mandi):
        # Production call sites (e.g. Provider.__str__) sort with reverse=True,
        # so undated items must use the minimum datetime sentinel to land last.
        dated = _make_item(mandi, "1", arrival_date="20-07-2026")
        undated = _make_item(mandi, "2", arrival_date=None)

        items = sorted([undated, dated], key=mandi._arrival_date_sort_key, reverse=True)

        assert [item.id for item in items] == ["1", "2"]

    def test_more_recent_date_sorts_first(self, mandi):
        older = _make_item(mandi, "1", arrival_date="19-07-2026")
        newer = _make_item(mandi, "2", arrival_date="20-07-2026")

        items = sorted([older, newer], key=mandi._arrival_date_sort_key, reverse=True)

        assert [item.id for item in items] == ["2", "1"]


class TestMandiResponseFormatOutput:
    def _make_response(self, mandi, providers):
        return mandi.MandiResponse(
            context=_make_context(mandi),
            responses=[
                mandi.ResponseItem(
                    context=_make_context(mandi),
                    message=mandi.Message(catalog=mandi.Catalog(descriptor=mandi.Descriptor(), providers=providers)),
                )
            ],
        )

    def test_no_providers_reports_no_data(self, mandi):
        response = self._make_response(mandi, providers=[])

        assert "No mandi price data found" in response.format_output()

    def test_filters_items_by_requested_date(self, mandi):
        matching = _make_item(mandi, "1", arrival_date="20-07-2026")
        other = _make_item(mandi, "2", arrival_date="19-07-2026")
        provider = mandi.Provider(id="p1", descriptor=mandi.Descriptor(name="Provider"), items=[matching, other])
        response = self._make_response(mandi, providers=[provider])

        output = response.format_output(requested_price_date="20-07-2026")

        assert output.count("Commodity: Onion") == 1
        assert "Lasalgaon" in output

    def test_no_exact_match_reports_no_data_instead_of_another_date(self, mandi):
        item = _make_item(mandi, "1", arrival_date="19-07-2026")
        provider = mandi.Provider(id="p1", descriptor=mandi.Descriptor(name="Provider"), items=[item])
        response = self._make_response(mandi, providers=[provider])

        output = response.format_output(requested_price_date="25-12-2026")

        assert "[Status: NO_DATA]" in output
        assert "Lasalgaon" not in output

    def test_items_without_any_parseable_date_report_no_data_when_no_exact_match(self, mandi):
        item = _make_item(mandi, "1", arrival_date=None)
        provider = mandi.Provider(id="p1", descriptor=mandi.Descriptor(name="Provider"), items=[item])
        response = self._make_response(mandi, providers=[provider])

        output = response.format_output(requested_price_date="20-07-2026")

        assert "No mandi price data found" in output

    def test_date_range_keeps_every_date_inside_the_window(self, mandi):
        inside_start = _make_item(mandi, "1", arrival_date="01-07-2026")
        inside_middle = _make_item(mandi, "2", arrival_date="05-07-2026")
        inside_end = _make_item(mandi, "3", arrival_date="10-07-2026")
        outside = _make_item(mandi, "4", arrival_date="11-07-2026")
        provider = mandi.Provider(
            id="p1",
            descriptor=mandi.Descriptor(name="Provider"),
            items=[inside_start, inside_middle, inside_end, outside],
        )
        response = self._make_response(mandi, providers=[provider])

        output = response.format_output(
            requested_price_date="01-07-2026",
            requested_price_date_to="10-07-2026",
        )

        assert "Price Date Range: Wednesday, 01 July 2026 to Friday, 10 July 2026" in output
        assert output.count("Commodity: Onion") == 3
        assert "Saturday, 11 July 2026" not in output
        # Newest arrival first (skip the header, which names both ends of the range).
        body = output.split("\n---\n", 1)[1]
        assert body.index("Friday, 10 July 2026") < body.index("Sunday, 05 July 2026") < body.index("Wednesday, 01 July 2026")

    def test_date_range_with_no_data_inside_reports_no_data(self, mandi):
        item = _make_item(mandi, "1", arrival_date="20-07-2026")
        provider = mandi.Provider(id="p1", descriptor=mandi.Descriptor(name="Provider"), items=[item])
        response = self._make_response(mandi, providers=[provider])

        output = response.format_output(
            requested_price_date="01-07-2026",
            requested_price_date_to="10-07-2026",
        )

        assert "[Status: NO_DATA]" in output
        assert "Wednesday, 01 July 2026 to Friday, 10 July 2026" in output
        assert "Lasalgaon" not in output

    def test_date_range_with_no_providers_reports_the_range(self, mandi):
        response = self._make_response(mandi, providers=[])

        output = response.format_output(
            requested_price_date="01-07-2026",
            requested_price_date_to="10-07-2026",
        )

        assert "Wednesday, 01 July 2026 to Friday, 10 July 2026" in output
        assert "No mandi price data found" in output

    def test_range_with_identical_ends_behaves_like_a_single_date(self, mandi):
        matching = _make_item(mandi, "1", arrival_date="20-07-2026")
        other = _make_item(mandi, "2", arrival_date="19-07-2026")
        provider = mandi.Provider(id="p1", descriptor=mandi.Descriptor(name="Provider"), items=[matching, other])
        response = self._make_response(mandi, providers=[provider])

        output = response.format_output(
            requested_price_date="20-07-2026",
            requested_price_date_to="20-07-2026",
        )

        assert "Price Date: Monday, 20 July 2026" in output
        assert output.count("Commodity: Onion") == 1

    def test_no_requested_date_returns_only_the_latest_available_date(self, mandi):
        newer = _make_item(mandi, "1", arrival_date="20-07-2026")
        older = _make_item(mandi, "2", arrival_date="19-07-2026")
        provider = mandi.Provider(id="p1", descriptor=mandi.Descriptor(name="Provider"), items=[newer, older])
        response = self._make_response(mandi, providers=[provider])

        output = response.format_output()

        assert output.count("Commodity: Onion") == 1
        assert "Monday, 20 July 2026" in output


class TestSearchContextStatus:
    NEARBY = {
        "status": "nearby_only",
        "commodity": "Onion",
        "requested_location": "Manchar",
        "market": "Junnar(Narayangaon)",
        "district": "Pune",
        "distance_km": "23.5",
    }

    def _response(self, mandi, tags=None):
        item = _make_item(mandi, "1", arrival_date="28-09-2026")
        provider = mandi.Provider(id="p1", descriptor=mandi.Descriptor(name="Provider"), items=[item])
        catalog = mandi.Catalog(descriptor=mandi.Descriptor(), providers=[provider], tags=tags)
        return mandi.MandiResponse(
            context=_make_context(mandi),
            responses=[mandi.ResponseItem(context=_make_context(mandi), message=mandi.Message(catalog=catalog))],
        )

    def test_catalog_reads_the_provider_search_context_tag(self, mandi):
        tag = mandi.Tag(
            descriptor=mandi.Descriptor(code="search-context"),
            list=[
                mandi.TagItem(descriptor=mandi.Descriptor(code="status"), value="nearby_only"),
                mandi.TagItem(descriptor=mandi.Descriptor(code="market"), value="Junnar(Narayangaon)"),
            ],
        )
        catalog = self._response(mandi, tags=[tag]).responses[0].message.catalog
        assert catalog.search_context() == {"status": "nearby_only", "market": "Junnar(Narayangaon)"}

    def test_catalog_without_tags_has_empty_context(self, mandi):
        assert self._response(mandi).responses[0].message.catalog.search_context() == {}

    def test_data_found_shows_prices(self, mandi):
        output = self._response(mandi).format_output(
            requested_price_date="28-09-2026",
            search_context={"status": "data_found", "commodity": "Onion", "requested_location": "Nashik"},
        )
        assert "Status:" not in output
        assert "Lasalgaon" in output

    def test_nearby_only_withholds_prices_and_names_the_nearest_mandi(self, mandi):
        output = self._response(mandi).format_output(
            requested_price_date="28-09-2026", search_context=self.NEARBY
        )
        assert "[Status: NO_DATA_AT_REQUESTED_MANDI]" in output
        assert "No mandi price data found for Onion at Manchar" in output
        assert "Junnar(Narayangaon), Pune (about 23.5 km away)" in output
        assert "include_nearby_mandis=true" in output
        assert "Commodity: Onion" not in output

    def test_nearby_only_shows_prices_once_the_farmer_chooses_them(self, mandi):
        output = self._response(mandi).format_output(
            requested_price_date="28-09-2026", search_context=self.NEARBY, include_nearby_mandis=True
        )
        assert "Nearest mandi with data — none at Manchar: Junnar(Narayangaon), Pune" in output
        assert "Commodity: Onion" in output

    def test_no_data_names_commodity_and_place(self, mandi):
        response = mandi.MandiResponse(context=_make_context(mandi), responses=[])
        output = response.format_output(
            requested_price_date="28-09-2026",
            search_context={"status": "no_data", "commodity": "Onion", "requested_location": "Manchar"},
        )
        assert "[Status: NO_DATA]" in output
        assert "No mandi price data found for Onion at Manchar, or at any other mandi within 50 km" in output


class TestRangeTooLong:
    def test_range_over_max_days_is_rejected_with_a_suggested_window(self, mandi):
        start, end = _days_ago_str(mandi, mandi.MAX_DATE_RANGE_DAYS + 40), _days_ago_str(mandi, 5)
        output = mandi._range_too_long(start, end)
        assert output is not None
        assert "[Status: RANGE_TOO_LONG]" in output
        assert f"at most {mandi.MAX_DATE_RANGE_DAYS} days" in output
        assert f"price_date={_days_ago_str(mandi, mandi.MAX_DATE_RANGE_DAYS + 5)}, price_date_to={end}" in output

    def test_future_end_is_capped_before_measuring(self, mandi):
        # 20 days ago to 20 days ahead is only 20 days once capped at today.
        assert mandi._range_too_long(_days_ago_str(mandi, 20), _days_ago_str(mandi, -20)) is None

    def test_single_date_and_latest_are_never_too_long(self, mandi):
        assert mandi._range_too_long(_days_ago_str(mandi, 200), None) is None
        assert mandi._range_too_long(None, None) is None
