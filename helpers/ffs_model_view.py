"""Compact FFS model view, including cleanup of legacy saved tool results."""
from copy import deepcopy


def compact_ffs_result(value):
    if not isinstance(value, dict):
        return value
    clean = deepcopy(value)
    data = clean.get("data", {})
    if not isinstance(data, dict):
        return clean
    data.pop("backend_exchanges", None)
    data.pop("booking_tool_exchanges", None)
    data.pop("shop_retained", None)
    if isinstance(data.get("selected_shop"), dict):
        data["selected_shop"] = {k: data["selected_shop"][k] for k in
                                 ("shop_ref", "name", "territory") if k in data["selected_shop"]}
    if "shops" in data:
        data["shops"] = [{"number": i + 1, **{k: row[k] for k in
            ("shop_ref", "name", "territory", "coverage", "supply") if k in row}}
            for i, row in enumerate(data["shops"]) if isinstance(row, dict)]
        clean["next_action"] = "Present the numbered shops, territory and ordered product availability. Never reveal stock volumes or change the order; offer more if available"
    # Provider rejection messages and structured details intentionally reach the
    # model. The skill controls how they are explained; do not parse/filter NLP.
    return clean
