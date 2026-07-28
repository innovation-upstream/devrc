"""Unit tests for the PURE grounded next-step recommender (scripts/initiatives/nextstep.py).

Hermetic: nextstep.py is stdlib-only + pure, so these tests just feed view dicts and assert
the derivation priority + the ANTI-CONFABULATION property (every recommendation text is
derived from a real fed field, never invented) + the trim cap + the None-when-nothing case.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nextstep  # noqa: E402


def _view(**over):
    """A minimal view with every field the recommender reads set to empty/inert, so each test
    turns exactly one field on and asserts which basis wins."""
    v = {
        "slug": "x", "repo": "/repo/devrc", "repo_name": "devrc",
        "next_step": "", "open_prs": [], "open_investigations": [],
        "face_message": None, "status": "", "momentum": "active", "age": "",
    }
    v.update(over)
    return v


# --- priority 1: documented handoff next_step ------------------------------- #
def test_next_step_is_handoff_basis_and_verbatim():
    rec = nextstep.recommend_next_step(_view(next_step="wire the systemd unit"))
    assert rec == {"text": "wire the systemd unit", "basis": "handoff"}


def test_next_step_wins_over_everything_else():
    rec = nextstep.recommend_next_step(_view(
        next_step="the real step", open_prs=[{"number": 5, "title": "x"}],
        open_investigations=["q"], face_message={"text": "m"}, status="s",
        momentum="stalled", age="9d"))
    assert rec["basis"] == "handoff" and rec["text"] == "the real step"


# --- priority 2: open PR ---------------------------------------------------- #
def test_open_pr_basis_with_number_and_title():
    rec = nextstep.recommend_next_step(_view(open_prs=[{"number": 138, "title": "feat: viewer"}]))
    assert rec["basis"] == "open-pr"
    assert rec["text"] == "Review/land open PR #138 feat: viewer"


def test_open_pr_without_title():
    rec = nextstep.recommend_next_step(_view(open_prs=[{"number": 7}]))
    assert rec == {"text": "Review/land open PR #7", "basis": "open-pr"}


def test_open_pr_without_number():
    rec = nextstep.recommend_next_step(_view(open_prs=[{"title": "some pr"}]))
    assert rec["basis"] == "open-pr"
    assert rec["text"] == "Review/land the open PR some pr"


def test_open_pr_ranks_above_investigation_and_focus():
    rec = nextstep.recommend_next_step(_view(
        open_prs=[{"number": 1, "title": "a"}], open_investigations=["q"],
        face_message={"text": "m"}, status="s"))
    assert rec["basis"] == "open-pr"


# --- priority 3: open investigation ----------------------------------------- #
def test_investigation_basis_uses_first():
    rec = nextstep.recommend_next_step(_view(
        open_investigations=["does the overlay hold under churn?", "second one"]))
    assert rec["basis"] == "investigation"
    assert rec["text"] == "Resolve: does the overlay hold under churn?"


def test_investigation_ranks_above_focus_and_status():
    rec = nextstep.recommend_next_step(_view(
        open_investigations=["q"], face_message={"text": "m"}, status="s"))
    assert rec["basis"] == "investigation"


# --- priority 4: face_message (last prompt) --------------------------------- #
def test_focus_basis_from_face_message():
    # Change B: the recommendation leads with the BARE prompt — the repeated "Continue where you
    # left off:" filler lead-in was dropped (it prefixed ~40 cards, killing scannability). The
    # `basis` ("from your last prompt") + the dispatch header frame WHERE it came from.
    rec = nextstep.recommend_next_step(_view(face_message={"text": "explore the router"}))
    assert rec["basis"] == "focus"
    assert rec["text"] == "explore the router"
    assert "Continue where you left off" not in rec["text"]


def test_focus_ranks_above_status():
    rec = nextstep.recommend_next_step(_view(face_message={"text": "m"}, status="s"))
    assert rec["basis"] == "focus"


# --- priority 5: status ----------------------------------------------------- #
def test_status_basis():
    # Change B: bare status text — the "Follow up on:" filler lead-in was dropped (same
    # repeated-noise reason as the focus basis); the `basis` supplies the framing.
    rec = nextstep.recommend_next_step(_view(status="exploring age-gating"))
    assert rec == {"text": "exploring age-gating", "basis": "status"}
    assert "Follow up on" not in rec["text"]


# --- priority 6: stalled ---------------------------------------------------- #
def test_stalled_basis_needs_age():
    rec = nextstep.recommend_next_step(_view(momentum="stalled", age="9d"))
    assert rec["basis"] == "stalled"
    assert "stalled — last touched 9d ago" in rec["text"]


def test_stalled_without_age_is_none():
    # No age → nothing to ground "last touched N ago" on → no recommendation.
    assert nextstep.recommend_next_step(_view(momentum="stalled", age="")) is None


# --- priority 7: nothing ---------------------------------------------------- #
def test_empty_view_is_none():
    assert nextstep.recommend_next_step(_view()) is None


def test_non_dict_is_none():
    assert nextstep.recommend_next_step(None) is None
    assert nextstep.recommend_next_step("nope") is None


# --- trimming --------------------------------------------------------------- #
def test_trims_long_text_to_cap_with_ellipsis():
    long = "x" * 500
    rec = nextstep.recommend_next_step(_view(next_step=long))
    assert len(rec["text"]) <= nextstep._TEXT_MAX
    assert rec["text"].endswith("…")


def test_whitespace_only_fields_skipped():
    # A blank/whitespace next_step must NOT win — it's not a real step.
    rec = nextstep.recommend_next_step(_view(next_step="   ", status="real status"))
    assert rec["basis"] == "status"


# --- grounding: text is derived from the fed field -------------------------- #
def test_grounded_text_contains_the_source_field():
    src = "resume the sentinel failover soak"
    rec = nextstep.recommend_next_step(_view(next_step=src))
    assert src in rec["text"]  # verbatim for handoff basis
    rec2 = nextstep.recommend_next_step(_view(status=src))
    assert src in rec2["text"]  # embedded for status basis


# --- basis_label ------------------------------------------------------------ #
def test_basis_label_covers_every_basis():
    for basis in ("handoff", "open-pr", "investigation", "focus", "status", "stalled"):
        assert nextstep.basis_label(basis)
    # unknown basis degrades to the string itself; never raises.
    assert nextstep.basis_label("wat") == "wat"
    assert nextstep.basis_label("") == ""
