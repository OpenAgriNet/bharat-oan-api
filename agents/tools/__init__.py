"""
Tools for the BharatVistaar AI Agent.
"""

# from agents.tools.search_beckn import search_documents

import os
from dataclasses import replace


def _beckn_v2(capability: str) -> bool:
    """True when <CAPABILITY>_BECKN_VERSION asks for v2. Read at import time.

    Each capability flips on its own and stays on v1 until its v2 path is
    signed off. For every one migrated so far the OUTPUT changed and not just
    the transport, so the two versions cannot be diffed blind; what each one
    changed is written up in migration-plan/.
    """
    return os.getenv(f"{capability}_BECKN_VERSION", "v1").strip().lower() == "v2"

from pydantic_ai import Tool
from helpers.gemma_schema import gemma_compatible
from agents.tools.ffs import ffs_send_otp, ffs_verify_otp, ffs_get_entitlement, ffs_find_shops, ffs_create_booking

from agents.tools.amul_vistaar import call_amul_vistaar_network
from agents.tools.maha_vistaar import call_maha_vistaar_network
# PM-KISAN's two capabilities move together on one flag: they share an OTP, so
# a farmer who asked for one on v1 cannot spend it on v2. One signature differs
# -- pmkisan_grievance_status takes a filing date on v2.
if _beckn_v2("PMKISAN"):
    from agents.tools.v2.pmkisan_scheme_status import (
        initiate_pm_kisan_status_check,
        check_pm_kisan_status_with_otp,
    )
else:
    from agents.tools.pmkisan_scheme_status import (
        initiate_pm_kisan_status_check,
        check_pm_kisan_status_with_otp,
    )
# The same flag as PMFBY's grievance tools: the policy and claim reads share
# the OTP the grievance tools do not, so a farmer mid-journey on one version
# cannot finish on the other.
if _beckn_v2("PMFBY"):
    from agents.tools.v2.pmfby_scheme_status import (
        initiate_pmfby_status_check,
        check_pmfby_status_with_otp,
    )
else:
    from agents.tools.pmfby_scheme_status import (
        initiate_pmfby_status_check,
        check_pmfby_status_with_otp,
    )
if _beckn_v2("SHC"):
    from agents.tools.v2.shc_scheme_status import check_shc_status
else:
    from agents.tools.shc_scheme_status import check_shc_status
if _beckn_v2("PMKISAN"):
    from agents.tools.v2.pmkisan_grievance import (
        pmkisan_grievance_send_otp,
        pmkisan_submit_grievance,
        pmkisan_grievance_status,
    )
else:
    from agents.tools.pmkisan_grievance import (
        pmkisan_grievance_send_otp,
        pmkisan_submit_grievance,
        pmkisan_grievance_status,
    )
from agents.tools.smam_scheme_status import check_smam_scheme_status
# Only two of PMFBY's four grievance tools have a v2 target. The two OTP tools
# stay on v1 whatever the flag says, because the Grievance pack has no
# PMFBYInit for them to become -- and the v2 submit calls the same v1 OTP check
# the v1 submit does, so the gate survives the switch.
from agents.tools.pmfby_grievance import (
    initiate_pmfby_grievance_otp,
    check_pmfby_grievance_otp,
)
if _beckn_v2("PMFBY"):
    from agents.tools.v2.pmfby_grievance import (
        pmfby_grievance_status,
        pmfby_submit_grievance,
    )
else:
    from agents.tools.pmfby_grievance import (
        pmfby_grievance_status,
        pmfby_submit_grievance,
    )
# AIF's four tools move together on one flag: all four share one OTP and one
# session, so a farmer who verified on v1 cannot spend that session on v2.
if _beckn_v2("AIF"):
    from agents.tools.v2.aif import (
        initiate_aif_otp,
        verify_aif_otp,
        check_aif_loan_status,
        check_aif_grievance_status,
    )
else:
    from agents.tools.aif import (
        initiate_aif_otp,
        verify_aif_otp,
        check_aif_loan_status,
        check_aif_grievance_status,
    )
# KCC's three tools move together on one flag: they share the OTP the first
# one sends, and the third reads a list the second fetched, so a farmer who
# started on v1 cannot finish on v2. select_kcc_application sends nothing on
# v2 -- it picks out of what status already returned.
if _beckn_v2("KCC"):
    from agents.tools.v2.kcc import (
        initiate_kcc_otp,
        check_kcc_application_status,
        select_kcc_application,
    )
else:
    from agents.tools.kcc import (
        initiate_kcc_otp,
        check_kcc_application_status,
        select_kcc_application,
    )
# KHC's seven tools move together on one flag: the filing spends the session
# that initiate/verify opened, and the three lookups resolve the ids the filing
# carries, so a farmer who started on v1 cannot finish on v2. On v2 the lookups
# call KHC directly rather than riding the network -- see agents/tools/v2/khc.py
# for why -- and the status read is the gated pair, check-status then verify-otp,
# with the grievance number on the query string (open-issues.md item 20, closed).
if _beckn_v2("KHC"):
    from agents.tools.v2.khc import (
        initiate_khc_otp,
        verify_khc_otp,
        lookup_khc_reference,
        find_khc_location,
        get_khc_grievance_options,
        check_khc_grievance_status,
        register_khc_grievance,
    )
else:
    from agents.tools.khc import (
        initiate_khc_otp,
        verify_khc_otp,
        lookup_khc_reference,
        find_khc_location,
        get_khc_grievance_options,
        check_khc_grievance_status,
        register_khc_grievance,
    )
from agents.tools.terms import search_terms
from agents.tools.search import (
    search_documents,
    search_video,
    search_pests_diseases,
    search_schemes,
)
from agents.tools.advisory import search_advisory
# Unlike mandi, weather's v2 output differs: the provider mapping drops fields
# the v0.1 pack cannot hold. See migration-plan/weather/plan.md.
if _beckn_v2("WEATHER"):
    from agents.tools.v2.weather import weather_forecast
else:
    from agents.tools.weather import weather_forecast
from agents.tools.imd_advisory import imd_agromet_advisory
# The one capability whose v2 output is byte-identical to v1.
if _beckn_v2("MANDI"):
    from agents.tools.v2.mandi import get_mandi_prices
else:
    from agents.tools.mandi import get_mandi_prices
from agents.tools.commodity import search_commodity
from agents.tools.maps import reverse_geocode, forward_geocode
if _beckn_v2("GFR"):
    from agents.tools.v2.gfr import (
        gfr_get_crop_registries,
        gfr_get_recommendations,
    )
else:
    from agents.tools.gfr import (
        gfr_get_crop_registries,
        gfr_get_recommendations,
    )
# v2 reads crops from the network catalog, so SATHI's master-API tools are v1 only.
if _beckn_v2("SATHI"):
    from agents.tools.v2.sathi_seed import list_sathi_crops, search_sathi_seed_availability
else:
    from agents.tools.sathi_seed import (
        get_sathi_crop_groups,
        list_sathi_crops_in_group,
        search_sathi_seed_availability,
    )
from agents.tools.npss import analyze_crop_image
from agents.tools.agristack import (
    get_agristack_farmer_location,
    get_agristack_farmer_crops,
    get_agristack_farmer_profile,
    only_with_agristack_consent,
)

async def _gemma_schema(ctx, tool_def):
    return replace(tool_def, parameters_json_schema=gemma_compatible(tool_def.parameters_json_schema))


if _beckn_v2("SATHI"):
    _SATHI_CROP_TOOLS = [
        Tool(
            list_sathi_crops,
            takes_ctx=True,
            strict=False,
        ),
    ]
else:
    _SATHI_CROP_TOOLS = [
        Tool(
            get_sathi_crop_groups,
            takes_ctx=False,
            strict=False,
        ),
        Tool(
            list_sathi_crops_in_group,
            takes_ctx=False,
            strict=False,
        ),
    ]


TOOLS = [
    Tool(ffs_send_otp, takes_ctx=True, sequential=True, strict=False, prepare=_gemma_schema),
    Tool(ffs_verify_otp, takes_ctx=True, sequential=True, strict=False, prepare=_gemma_schema),
    Tool(ffs_get_entitlement, takes_ctx=True, sequential=True, strict=False, prepare=_gemma_schema),
    Tool(ffs_find_shops, takes_ctx=True, sequential=True, strict=False, prepare=_gemma_schema),
    Tool(ffs_create_booking, takes_ctx=True, sequential=True, strict=False, prepare=_gemma_schema),
    Tool(
        call_amul_vistaar_network,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        call_maha_vistaar_network,
        takes_ctx=True,
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
        pmkisan_grievance_send_otp,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        check_smam_scheme_status,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        pmkisan_submit_grievance,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        pmkisan_grievance_status,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        initiate_pmfby_grievance_otp,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        check_pmfby_grievance_otp,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        pmfby_grievance_status,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        pmfby_submit_grievance,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        initiate_aif_otp,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        verify_aif_otp,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        check_aif_loan_status,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        check_aif_grievance_status,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        initiate_kcc_otp,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        check_kcc_application_status,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        select_kcc_application,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        check_khc_grievance_status,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        initiate_khc_otp,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        verify_khc_otp,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        lookup_khc_reference,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        find_khc_location,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        get_khc_grievance_options,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        register_khc_grievance,
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
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        search_schemes,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        search_advisory,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        search_video,
        takes_ctx=False,
        strict=False,
    ),
    Tool(
        search_pests_diseases,
        takes_ctx=True,
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
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        reverse_geocode,
        takes_ctx=False,
        strict=False,
    ),
    Tool(
        gfr_get_crop_registries,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        gfr_get_recommendations,
        takes_ctx=True,
        strict=False,
    ),
    *_SATHI_CROP_TOOLS,
    Tool(
        search_sathi_seed_availability,
        takes_ctx=True,
        strict=False,
    ),
    Tool(
        analyze_crop_image,
        takes_ctx=True,
        strict=False,
    ),
    # Hidden from the model unless the farmer logged in with AgriStack and gave consent.
    Tool(
        get_agristack_farmer_location,
        takes_ctx=True,
        strict=False,
        prepare=only_with_agristack_consent,
    ),
    Tool(
        get_agristack_farmer_crops,
        takes_ctx=True,
        strict=False,
        prepare=only_with_agristack_consent,
    ),
    Tool(
        get_agristack_farmer_profile,
        takes_ctx=True,
        strict=False,
        prepare=only_with_agristack_consent,
    ),
    Tool(
        imd_agromet_advisory,
        takes_ctx=True,
        strict=False,
    ),
]
