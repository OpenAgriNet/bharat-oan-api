from app.services.chat import _collapse_adjacent_duplicate_text


def test_collapse_adjacent_duplicate_sentence():
    text = (
        "Which state is the grievance from?"
        "Which state is the grievance from?"
    )
    assert _collapse_adjacent_duplicate_text(text) == "Which state is the grievance from?"


def test_collapse_adjacent_duplicate_sentence_with_minor_punctuation_variation():
    text = (
        "Please confirm one of these sub-categories for your complaint: Bank refused loan under AIF."
        "Please confirm one of these sub-categories for your complaint: Bank refused loan under A.I.F."
    )
    assert _collapse_adjacent_duplicate_text(text) == (
        "Please confirm one of these sub-categories for your complaint: Bank refused loan under AIF."
    )


def test_keep_non_duplicate_sentence():
    text = "Which state is the grievance from? Please share district too."
    assert _collapse_adjacent_duplicate_text(text) == text
