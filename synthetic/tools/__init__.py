"""
Tools for the synthetic agrinet agent.
Mock tools for dynamic data, real tools for static data.
"""
from pydantic_ai import Tool

# Real tools (copied from agents/tools/ — self-contained to avoid agents/ import chain)
from synthetic.tools.commodity import search_commodity
from synthetic.tools.terms import search_terms
from synthetic.tools.search import search_documents, search_videos, search_pests_diseases
from synthetic.tools.maps import reverse_geocode, forward_geocode

# Mock tools (randomized data for synthetic conversations)
from synthetic.tools.scheme_info import get_scheme_info
from synthetic.tools.pmkisan_scheme_status import initiate_pm_kisan_status_check, check_pm_kisan_status_with_otp
from synthetic.tools.pmfby_scheme_status import initiate_pmfby_status_check, check_pmfby_status_with_otp
from synthetic.tools.shc_scheme_status import check_shc_status
from synthetic.tools.grievance import submit_pmkisan_grievance, check_pmkisan_grievance_status
from synthetic.tools.weather import weather_forecast
from synthetic.tools.mandi import get_mandi_prices

TOOLS = [
    Tool(
        get_scheme_info,
        takes_ctx=False,
        strict=False,
    ),
    Tool(
        initiate_pm_kisan_status_check,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        check_pm_kisan_status_with_otp,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        initiate_pmfby_status_check,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        check_pmfby_status_with_otp,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        check_shc_status,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        submit_pmkisan_grievance,
        takes_ctx=False,
        strict=False,
    ),
    Tool(
        check_pmkisan_grievance_status,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        search_terms,
        takes_ctx=False,
        strict=False,
    ),
    Tool(
        search_documents,
        takes_ctx=False,
        strict=False,
    ),
    Tool(
        search_videos,
        takes_ctx=False,
        strict=False,
    ),
    Tool(
        search_pests_diseases,
        takes_ctx=False,
        strict=False,
    ),
    Tool(
        weather_forecast,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        get_mandi_prices,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        search_commodity,
        takes_ctx=False,
        strict=False,
    ),
    Tool(
        forward_geocode,
        takes_ctx=False,
        strict=False,
    ),
    Tool(
        reverse_geocode,
        takes_ctx=False,
        strict=False,
    ),
]
