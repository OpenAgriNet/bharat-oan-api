from app.services.stream_dedupe import ToolBoundaryDeduper


def run(parts):
    """parts: strings are chunks; None marks a tool boundary."""
    deduper, out = ToolBoundaryDeduper(), []
    for part in parts:
        if part is None:
            deduper.tool_boundary()
        else:
            out.append(deduper.feed(part))
    return "".join(out)


def test_repeat_after_tool_is_dropped():
    q = "Which state is the grievance from?"
    assert run([q[:10], q[10:], None, q[:5], q[5:20], q[20:]]) == q


def test_repeat_with_punctuation_variation_is_dropped():
    assert run(["Choose: bank refused loan under AIF.", None, "Choose: bank refused loan under A.I.F."]) == (
        "Choose: bank refused loan under AIF."
    )


def test_different_text_after_tool_is_kept_in_full():
    assert run(["Checking.", None, "Chosen state: Uttar Pradesh."]) == "Checking.Chosen state: Uttar Pradesh."


def test_new_text_after_repeated_prefix_is_kept():
    assert run(["Which state?", None, "Which state? Please reply."]) == "Which state? Please reply."


def test_no_tool_means_no_change():
    assert run(["same", "same"]) == "samesame"
