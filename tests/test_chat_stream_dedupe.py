from app.services.chat import _collapse_adjacent_duplicate_text


def test_collapse_adjacent_duplicate_sentence():
    text = (
        "Which state is the grievance from?"
        "Which state is the grievance from?"
    )
    assert _collapse_adjacent_duplicate_text(text) == "Which state is the grievance from?"


def test_keep_non_duplicate_sentence():
    text = "Which state is the grievance from? Please share district too."
    assert _collapse_adjacent_duplicate_text(text) == text
