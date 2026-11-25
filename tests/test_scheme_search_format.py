"""Scheme search results are labelled [scheme | document | page]; no scheme-list gating."""
from helpers.scheme_qdrant_search import NO_RESULTS_MESSAGE, _format_page, format_search_results


def test_format_page():
    assert _format_page(None, None) == ""
    assert _format_page(4, 4) == "4"
    assert _format_page(4, None) == "4"
    assert _format_page(4, 6) == "4-6"


def test_format_search_results_header_and_empty():
    results = [
        {
            "score": 0.5,
            "scheme_code": "pkvy",
            "scheme_name": "PKVY",
            "doc_title": "PKVY Guidelines",
            "page": "3",
            "text": "Some text",
            "section": "eligibility",
        }
    ]
    out = format_search_results(results, "pkvy eligibility")
    assert "[PKVY | PKVY Guidelines | p.3]" in out
    assert "section=Eligibility" in out
    assert format_search_results([], "anything") == NO_RESULTS_MESSAGE
