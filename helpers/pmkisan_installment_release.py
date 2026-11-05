from datetime import date, datetime

import pytz

PM_KISAN_24TH_INSTALLMENT_RELEASE_DATE = date(2026, 10, 30)


def _today_ist() -> date:
    ist = pytz.timezone("Asia/Kolkata")
    return datetime.now(ist).date()


def _is_before_or_on_release_day(today: date) -> bool:
    return today <= PM_KISAN_24TH_INSTALLMENT_RELEASE_DATE


def get_pm_kisan_24th_installment_release_messages(
    today: date | None = None,
) -> tuple[str, str]:
    """Return English and Hindi answers for the 24th PM-KISAN instalment release date."""
    if today is None:
        today = _today_ist()

    if _is_before_or_on_release_day(today):
        en = "The 24th instalment of PM-KISAN will be released on 30th October 2026."
        hi = "पीएम-किसान योजना की 24वीं किस्त दिनांक 30 अक्टूबर 2026 को जारी की जाएगी।"
    else:
        en = "The 24th instalment of PM-KISAN was released on 30th October 2026."
        hi = "पीएम-किसान योजना की 24वीं किस्त दिनांक 30 अक्टूबर 2026 को जारी की गई।"

    return en, hi


def get_pm_kisan_24th_installment_release_section(
    today: date | None = None,
) -> str:
    """Formatted tool section for ingestion into scheme info responses."""
    if today is None:
        today = _today_ist()

    en, hi = get_pm_kisan_24th_installment_release_messages(today=today)
    tense = "upcoming" if _is_before_or_on_release_day(today) else "released"

    return (
        "## PM-KISAN 24th Instalment Release\n\n"
        f"**Instalment number:** 24\n"
        f"**Official release date:** 30 October 2026\n"
        f"**Status as of today:** {tense}\n\n"
        f"**Answer (English):** {en}\n\n"
        f"**Answer (Hindi):** {hi}\n"
    )
