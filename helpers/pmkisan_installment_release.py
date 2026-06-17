from datetime import date, datetime

import pytz

PM_KISAN_23RD_INSTALLMENT_RELEASE_DATE = date(2026, 6, 20)


def _today_ist() -> date:
    ist = pytz.timezone("Asia/Kolkata")
    return datetime.now(ist).date()


def _is_before_or_on_release_day(today: date) -> bool:
    return today <= PM_KISAN_23RD_INSTALLMENT_RELEASE_DATE


def get_pm_kisan_23rd_installment_release_messages(
    today: date | None = None,
) -> tuple[str, str]:
    """Return English and Hindi answers for the 23rd PM-KISAN instalment release date."""
    if today is None:
        today = _today_ist()

    if _is_before_or_on_release_day(today):
        en = "The 23rd instalment of PM-KISAN is set to be released on 20th June 2026."
        hi = "पीएम-किसान योजना की 23वीं किस्त दिनांक 20 जून 2026 को जारी की जाएगी।"
    else:
        en = "The 23rd instalment of PM-KISAN was released on 20th June 2026."
        hi = "पीएम-किसान योजना की 23वीं किस्त दिनांक 20 जून 2026 को जारी की गई।"

    return en, hi


def get_pm_kisan_23rd_installment_release_section(
    today: date | None = None,
) -> str:
    """Formatted tool section for ingestion into scheme info responses."""
    if today is None:
        today = _today_ist()

    en, hi = get_pm_kisan_23rd_installment_release_messages(today=today)
    tense = "upcoming" if _is_before_or_on_release_day(today) else "released"

    return (
        "## PM-KISAN 23rd Instalment Release\n\n"
        f"**Instalment number:** 23\n"
        f"**Official release date:** 20 June 2026\n"
        f"**Status as of today:** {tense}\n\n"
        f"**Answer (English):** {en}\n\n"
        f"**Answer (Hindi):** {hi}\n"
    )
