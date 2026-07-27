from datetime import datetime, timedelta

from agents.tools.mandi import (
    MAX_DATE_RANGE_DAYS,
    Catalog,
    Context,
    Descriptor,
    MandiItem,
    MandiRequest,
    MandiResponse,
    Message,
    Provider,
    ResponseItem,
    Tag,
    TagItem,
    _IST,
    _arrival_date_sort_key,
    _closest_available_date,
    _item_matches_requested_date,
    _parse_mandi_date,
    _resolve_date_range,
)


def _today_ist_str() -> str:
    return datetime.now(_IST).date().strftime("%d-%m-%Y")


def _days_ago_str(days: int) -> str:
    return (datetime.now(_IST).date() - timedelta(days=days)).strftime("%d-%m-%Y")


def _make_context() -> Context:
    return Context(
        action="search",
        timestamp="2026-07-26T00:00:00.000Z",
        message_id="m1",
        transaction_id="t1",
        domain="schemes:vistaar",
        version="1.1.0",
    )


def _make_item(item_id: str, arrival_date: str | None = None, commodity: str = "Onion", modal_price: str = "2000") -> MandiItem:
    tag_list = [
        TagItem(descriptor=Descriptor(code="Commodity"), value=commodity),
        TagItem(descriptor=Descriptor(code="Market"), value="Lasalgaon"),
        TagItem(descriptor=Descriptor(code="Modal Price"), value=modal_price),
    ]
    if arrival_date:
        tag_list.append(TagItem(descriptor=Descriptor(code="Arrival Date"), value=arrival_date))
    return MandiItem(
        id=item_id,
        descriptor=Descriptor(name=commodity),
        tags=[Tag(descriptor=Descriptor(code="attributes"), list=tag_list)],
    )


class TestParseMandiDate:
    def test_parses_valid_ddmmyyyy(self):
        assert _parse_mandi_date("25-07-2026") == datetime(2026, 7, 25).date()

    def test_returns_none_for_empty_or_blank(self):
        assert _parse_mandi_date(None) is None
        assert _parse_mandi_date("") is None
        assert _parse_mandi_date("   ") is None

    def test_returns_none_for_unparseable_string(self):
        assert _parse_mandi_date("not-a-date") is None


class TestResolveDateRange:
    def test_to_date_is_always_today(self):
        _, to_date = _resolve_date_range("25-07-2026")
        assert to_date == _today_ist_str()

    def test_no_price_date_defaults_from_date_to_max_range_ago(self):
        from_date, to_date = _resolve_date_range(None)
        assert from_date == _days_ago_str(MAX_DATE_RANGE_DAYS)
        assert to_date == _today_ist_str()

    def test_recent_price_date_is_used_as_is(self):
        recent = _days_ago_str(5)
        from_date, to_date = _resolve_date_range(recent)
        assert from_date == recent
        assert to_date == _today_ist_str()

    def test_price_date_beyond_max_range_is_clamped(self):
        too_old = _days_ago_str(MAX_DATE_RANGE_DAYS + 100)
        from_date, to_date = _resolve_date_range(too_old)
        assert from_date == _days_ago_str(MAX_DATE_RANGE_DAYS)
        assert to_date == _today_ist_str()

    def test_price_date_exactly_at_boundary_is_not_clamped(self):
        boundary = _days_ago_str(MAX_DATE_RANGE_DAYS)
        from_date, _ = _resolve_date_range(boundary)
        assert from_date == boundary

    def test_invalid_price_date_string_falls_back_to_latest_available(self):
        from_date, to_date = _resolve_date_range("not-a-real-date")
        assert from_date == _days_ago_str(MAX_DATE_RANGE_DAYS)
        assert to_date == _today_ist_str()


class TestMandiRequestPayload:
    def _make_request(self, **overrides):
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
        return MandiRequest(**defaults)

    def _set_bap_env(self, monkeypatch):
        monkeypatch.setenv("BAP_ID", "bap-test")
        monkeypatch.setenv("BAP_URI", "https://bap.test")
        monkeypatch.setenv("BPP_ID", "bpp-test")
        monkeypatch.setenv("BPP_URI", "https://bpp.test")

    def test_payload_uses_from_date_to_date_tags(self, monkeypatch):
        self._set_bap_env(monkeypatch)

        payload = self._make_request(price_date="25-07-2026").get_payload()

        tags = payload["message"]["intent"]["tags"]
        assert tags == [
            {"code": "from_date", "value": "25-07-2026"},
            {"code": "to_date", "value": _today_ist_str()},
        ]

    def test_payload_context_fields(self, monkeypatch):
        self._set_bap_env(monkeypatch)

        payload = self._make_request().get_payload()
        context = payload["context"]

        assert context["domain"] == "schemes:vistaar"
        assert context["action"] == "search"
        assert context["bap_id"] == "bap-test"
        assert context["bpp_id"] == "bpp-test"
        assert context["tags"] == {"session_id": "sess-1", "question_id": "q-1"}

    def test_payload_intent_item_and_fulfillment(self, monkeypatch):
        self._set_bap_env(monkeypatch)

        payload = self._make_request(location_name="Pune", commodity_name="Wheat").get_payload()
        intent = payload["message"]["intent"]

        assert intent["category"]["descriptor"]["code"] == "price-discovery"
        assert intent["item"]["descriptor"]["name"] == "Wheat"
        assert intent["fulfillment"]["end"]["location"]["descriptor"]["name"] == "Pune"
        assert intent["fulfillment"]["end"]["location"]["gps"] == "20.011,73.79"

    def test_blank_price_date_treated_as_latest_available(self, monkeypatch):
        self._set_bap_env(monkeypatch)

        payload = self._make_request(price_date="   ").get_payload()

        tags = payload["message"]["intent"]["tags"]
        assert tags == [
            {"code": "from_date", "value": _days_ago_str(MAX_DATE_RANGE_DAYS)},
            {"code": "to_date", "value": _today_ist_str()},
        ]


class TestItemMatchesRequestedDate:
    def test_no_requested_date_matches_everything(self):
        item = _make_item("1", arrival_date="20-07-2026")
        assert _item_matches_requested_date(item, None) is True
        assert _item_matches_requested_date(item, "  ") is True

    def test_matches_exact_arrival_date(self):
        item = _make_item("1", arrival_date="20-07-2026")
        assert _item_matches_requested_date(item, "20-07-2026") is True

    def test_does_not_match_different_arrival_date(self):
        item = _make_item("1", arrival_date="19-07-2026")
        assert _item_matches_requested_date(item, "20-07-2026") is False

    def test_item_without_arrival_date_does_not_match_specific_request(self):
        item = _make_item("1", arrival_date=None)
        assert _item_matches_requested_date(item, "20-07-2026") is False


class TestClosestAvailableDate:
    def test_returns_none_when_no_items_have_a_parseable_date(self):
        items = [_make_item("1", arrival_date=None)]
        assert _closest_available_date(items, datetime(2026, 7, 20).date()) is None

    def test_returns_the_only_available_date(self):
        items = [_make_item("1", arrival_date="18-07-2026")]
        assert _closest_available_date(items, datetime(2026, 7, 20).date()) == datetime(2026, 7, 18).date()

    def test_picks_the_nearest_of_several_dates(self):
        items = [
            _make_item("1", arrival_date="15-07-2026"),
            _make_item("2", arrival_date="19-07-2026"),
            _make_item("3", arrival_date="10-07-2026"),
        ]
        assert _closest_available_date(items, datetime(2026, 7, 20).date()) == datetime(2026, 7, 19).date()

    def test_ties_prefer_the_more_recent_date(self):
        items = [
            _make_item("1", arrival_date="18-07-2026"),
            _make_item("2", arrival_date="22-07-2026"),
        ]
        assert _closest_available_date(items, datetime(2026, 7, 20).date()) == datetime(2026, 7, 22).date()


class TestArrivalDateSortKey:
    def test_undated_items_sort_last(self):
        # Production call sites (e.g. Provider.__str__) sort with reverse=True,
        # so undated items must use the minimum datetime sentinel to land last.
        dated = _make_item("1", arrival_date="20-07-2026")
        undated = _make_item("2", arrival_date=None)

        items = sorted([undated, dated], key=_arrival_date_sort_key, reverse=True)

        assert [item.id for item in items] == ["1", "2"]

    def test_more_recent_date_sorts_first(self):
        older = _make_item("1", arrival_date="19-07-2026")
        newer = _make_item("2", arrival_date="20-07-2026")

        items = sorted([older, newer], key=_arrival_date_sort_key, reverse=True)

        assert [item.id for item in items] == ["2", "1"]


class TestMandiResponseFormatOutput:
    def _make_response(self, providers):
        return MandiResponse(
            context=_make_context(),
            responses=[
                ResponseItem(
                    context=_make_context(),
                    message=Message(catalog=Catalog(descriptor=Descriptor(), providers=providers)),
                )
            ],
        )

    def test_no_providers_reports_no_data(self):
        response = self._make_response(providers=[])

        assert "No mandi price data found" in response.format_output()

    def test_filters_items_by_requested_date(self):
        matching = _make_item("1", arrival_date="20-07-2026")
        other = _make_item("2", arrival_date="19-07-2026")
        provider = Provider(id="p1", descriptor=Descriptor(name="Provider"), items=[matching, other])
        response = self._make_response(providers=[provider])

        output = response.format_output(requested_price_date="20-07-2026")

        assert output.count("Commodity: Onion") == 1
        assert "Lasalgaon" in output

    def test_no_exact_match_falls_back_to_closest_available_date(self):
        item = _make_item("1", arrival_date="19-07-2026")
        provider = Provider(id="p1", descriptor=Descriptor(name="Provider"), items=[item])
        response = self._make_response(providers=[provider])

        output = response.format_output(requested_price_date="25-12-2026")

        assert "not available" in output
        assert "closest available date" in output
        assert "Lasalgaon" in output
        assert "No mandi price data found" not in output

    def test_fallback_prefers_the_more_recent_of_two_equidistant_dates(self):
        earlier = _make_item("1", arrival_date="18-07-2026")
        later = _make_item("2", arrival_date="22-07-2026")
        provider = Provider(id="p1", descriptor=Descriptor(name="Provider"), items=[earlier, later])
        response = self._make_response(providers=[provider])

        # Requested date (20-07-2026) is exactly 2 days from both candidates.
        output = response.format_output(requested_price_date="20-07-2026")

        assert output.count("Commodity: Onion") == 1
        assert "22-07-2026" not in output  # display uses the formatted date, not raw
        assert "Wednesday, 22 July 2026" in output

    def test_items_without_any_parseable_date_report_no_data_when_no_exact_match(self):
        item = _make_item("1", arrival_date=None)
        provider = Provider(id="p1", descriptor=Descriptor(name="Provider"), items=[item])
        response = self._make_response(providers=[provider])

        output = response.format_output(requested_price_date="20-07-2026")

        assert "No mandi price data found" in output

    def test_no_requested_date_returns_only_the_latest_available_date(self):
        newer = _make_item("1", arrival_date="20-07-2026")
        older = _make_item("2", arrival_date="19-07-2026")
        provider = Provider(id="p1", descriptor=Descriptor(name="Provider"), items=[newer, older])
        response = self._make_response(providers=[provider])

        output = response.format_output()

        assert output.count("Commodity: Onion") == 1
        assert "Monday, 20 July 2026" in output
