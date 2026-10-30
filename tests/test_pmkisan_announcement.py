"""Lock the approved launch wording while moving it off the network tool."""
import ast
from datetime import date
import importlib.util
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import pytest

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("as", "bn", "en", "gu", "hi", "kn", "mai", "ml", "mr", "or", "pa", "ta", "te")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("day,future", [(date(2026,10,29), True), (date(2026,10,30), True), (date(2026,10,31), False)])
def test_approved_answer_and_date_boundaries(day, future):
    helper = load("helpers/pmkisan_installment_release.py", "release_helper")
    en, hi = helper.get_pm_kisan_24th_installment_release_messages(day)
    assert en == ("The 24th instalment of PM-KISAN will be released on 30th October 2026." if future else
                  "The 24th instalment of PM-KISAN was released on 30th October 2026.")
    assert hi == ("पीएम-किसान योजना की 24वीं किस्त दिनांक 30 अक्टूबर 2026 को जारी की जाएगी।" if future else
                  "पीएम-किसान योजना की 24वीं किस्त दिनांक 30 अक्टूबर 2026 को जारी की गई।")


def test_announcement_helper_needs_no_network(monkeypatch):
    helper = load("helpers/pmkisan_installment_release.py", "release_helper")
    monkeypatch.setattr(helper, "_today_ist", lambda: date(2026,10,30))
    def fail(*args, **kwargs):
        pytest.fail("Announcement attempted network access")
    import socket
    monkeypatch.setattr(socket.socket, "connect", fail)
    assert "will be released on 30th October 2026" in helper.get_pm_kisan_24th_installment_release_section()


@pytest.mark.parametrize("language", LANGUAGES)
def test_all_prompts_render_with_live_catalog_and_without_retired_tool(language):
    env = Environment(loader=FileSystemLoader(ROOT / "assets/prompts"), undefined=StrictUndefined)
    text = env.get_template(f"agrinet_{language}.md").render(
        today_date="Thursday, 29 October 2026", crop_season="Kharif", last_weekday_table="",
        vector_scheme_count=1, vector_schemes_bullets="- PM Kisan (pk)",
        vector_schemes_identifiers="- pk / PM Kisan / PMKISAN",
        pm_kisan_installment_release=load("helpers/pmkisan_installment_release.py", "release_helper").get_pm_kisan_24th_installment_release_section(date(2026,10,30)))
    assert "get_scheme_info" not in text
    assert "PMKISAN" in text
    assert "get_pm_kisan_installment_release" not in text
    assert text.count("The 24th instalment of PM-KISAN will be released on 30th October 2026.") == 1
    assert "पीएम-किसान योजना की 24वीं किस्त दिनांक 30 अक्टूबर 2026 को जारी की जाएगी।" in text


def test_service_tools_remain_registered():
    tree = ast.parse((ROOT / "agents/tools/__init__.py").read_text())
    registered = {node.args[0].id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Tool"}
    assert "get_scheme_info" not in registered
    assert "get_pm_kisan_installment_release" not in registered
    assert {"search_schemes", "initiate_pm_kisan_status_check",
        "check_pm_kisan_status_with_otp", "pmkisan_grievance_send_otp", "pmkisan_submit_grievance",
        "pmkisan_grievance_status", "initiate_pmfby_status_check", "check_pmfby_status_with_otp",
        "initiate_pmfby_grievance_otp", "check_pmfby_grievance_otp", "pmfby_grievance_status",
        "pmfby_submit_grievance", "initiate_aif_otp", "verify_aif_otp", "check_aif_loan_status",
        "check_aif_grievance_status", "initiate_kcc_otp", "check_kcc_application_status",
        "select_kcc_application", "check_shc_status", "check_smam_scheme_status",
        "get_sathi_crop_groups", "list_sathi_crops_in_group", "search_sathi_seed_availability",
        "call_maha_vistaar_network", "call_amul_vistaar_network"} <= registered
