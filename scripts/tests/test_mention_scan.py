"""Tests for `scripts/collector/mention_scan.py`.

The scanner's whole value proposition is that it is QUIET. `#` is one of the
most common characters in terminal output — markdown headings, shell comments,
colour literals, CSS, URL fragments, HTML entities — and a scanner that fires on
those is worse than no scanner at all: it puts junk in activity.events and it
underlines half the screen. So the NEGATIVE cases below carry as much weight as
the positive ones, and they are the reason each pattern is anchored on BOTH
sides rather than just prefixed with `\\b`.

The other invariant pinned here: NOTHING IS GUESSED. A GitHub reference whose
owner is unknown comes back with `url == ""`, never with a plausible-looking URL
built from a default org. `scripts/check-clickup-addressed/check-completion.py`
already paid for that lesson — a guessed owner points confidently at a real but
unrelated issue.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
COLLECTOR = ROOT / "scripts" / "collector"
sys.path.insert(0, str(COLLECTOR))

_spec = importlib.util.spec_from_file_location(
    "mention_scan_under_test", COLLECTOR / "mention_scan.py")
MS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MS)


def platforms(text, **kw):
    return [(m["platform"], m["id"]) for m in MS.scan_mentions(text, **kw)]


def span_platforms(text, **kw):
    return [(s["platform"], s["id"]) for s in MS.scan_mention_spans(text, **kw)]


# --------------------------------------------------------------------------- #
# Positive control — the scan CAN find something.
# --------------------------------------------------------------------------- #
def test_positive_control_the_scanner_finds_every_platform():
    """A suite of negative assertions is indistinguishable from a scanner wired
    to nothing. This is the case that must produce a NON-ZERO count before any
    zero below is worth reading."""
    text = "closed civitai/talos-infra#1065, opened devrc#591, ticket 868abc123, plus #370"
    found = MS.scan_mentions(text)
    assert found, "the scanner found NOTHING on a text full of mentions"
    kinds = {m["platform"] for m in found}
    assert kinds == {"github", "clickup", "clawgate"}


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #
def test_owner_repo_hash_number_is_unambiguously_github():
    (m,) = MS.scan_mentions("landed in civitai/talos-infra#1065 yesterday")
    assert m["platform"] == "github"
    assert m["id"] == "1065"
    assert m["raw"] == "civitai/talos-infra#1065"
    assert m["ambiguous"] is False
    assert m["url"] == "https://github.com/civitai/talos-infra/issues/1065"


def test_the_issues_url_is_used_for_prs_too():
    """`/issues/<n>` is deliberate: GitHub redirects it to `/pull/<n>` when the
    number is a PR, so one template covers both and the scanner never has to
    know which it is."""
    (m,) = MS.scan_mentions("innovation-upstream/devrc#992")
    assert m["url"].endswith("/issues/992")


def test_bare_repo_hash_number_is_github_but_has_no_url_without_an_owner():
    """🔴 THE NO-GUESSING RULE. `devrc#591` names a platform but not an owner,
    and a default org is exactly the guess that points at the wrong issue."""
    (m,) = MS.scan_mentions("see devrc#591")
    assert m["platform"] == "github"
    assert m["url"] == ""


def test_a_measured_repo_mapping_supplies_the_owner():
    (m,) = MS.scan_mentions("see devrc#591",
                            repos={"devrc": "innovation-upstream/devrc"})
    assert m["url"] == "https://github.com/innovation-upstream/devrc/issues/591"


def test_an_explicit_owner_beats_the_mapping():
    (m,) = MS.scan_mentions("civitai/devrc#1", repos={"devrc": "innovation-upstream/devrc"})
    assert m["url"] == "https://github.com/civitai/devrc/issues/1"


def test_a_malformed_mapping_entry_yields_no_url_rather_than_a_broken_one():
    (m,) = MS.scan_mentions("see devrc#591", repos={"devrc": "devrc"})
    assert m["url"] == ""


# --------------------------------------------------------------------------- #
# ClickUp
# --------------------------------------------------------------------------- #
def test_clickup_task_id():
    (m,) = MS.scan_mentions("please look at 868abc123 today")
    assert m["platform"] == "clickup"
    assert m["id"] == "868abc123"
    assert m["url"] == "https://app.clickup.com/t/868abc123"
    assert m["ambiguous"] is False


@pytest.mark.parametrize("text", [
    "868abc12",           # too short
    "868abc1234",         # too long
    "x868abc123",         # inside a longer token
    "868ABC123",          # uppercase — ClickUp renders these lowercase
    "868abc12_",          # trailing word char
    "/868abc123",         # a URL path segment, not a mention
])
def test_clickup_negatives(text):
    assert [m for m in MS.scan_mentions(text) if m["platform"] == "clickup"] == []


def test_the_DEV_prefix_form_is_deliberately_not_supported():
    """The `DEV-123` custom-prefix idea from the proposal is NOT implemented —
    it is indistinguishable from a Jira key, a branch name and a version string,
    and no ClickUp workspace here has been measured to use one. Pinned so the
    decision is visible rather than looking like an oversight."""
    assert MS.scan_mentions("DEV-123 is assigned to me") == []
    assert MS.scan_mentions("on branch DEV-123-fix-the-thing") == []


# --------------------------------------------------------------------------- #
# The ambiguous bare `#N`
# --------------------------------------------------------------------------- #
def test_bare_hash_number_returns_a_candidate_for_both_platforms():
    cands = MS.scan_mentions("fixed in #370")
    assert [(c["platform"], c["ambiguous"]) for c in cands] == [
        ("clawgate", True), ("github", True)]
    assert {c["start"] for c in cands} == {len("fixed in ")}
    assert {c["end"] for c in cands} == {len("fixed in #370")}


def test_the_clawgate_candidate_anchors_on_the_task_cards_own_dom_id():
    """clawgate DOES have a browser UI (GET /tasks -> handleIndex) and each card
    carries `id="task-<n>"` — internal/ui/notes.go, `ID("task-"+ids)`, pinned by
    its own notes_test.go as a stable card id."""
    (clawgate, _github) = MS.scan_mentions("#370")
    assert clawgate["url"] == "https://clawgate.zacx.dev/tasks#task-370"


def test_a_bare_number_gets_a_github_url_only_from_a_supplied_repo():
    (_clawgate, github) = MS.scan_mentions("#370")
    assert github["url"] == ""
    (_clawgate, github) = MS.scan_mentions("#370", default_repo="civitai/talos-infra")
    assert github["url"] == "https://github.com/civitai/talos-infra/issues/370"


def test_a_span_reports_ambiguous_rather_than_picking_a_winner():
    (span,) = MS.scan_mention_spans("fixed in #370")
    assert span["platform"] == "ambiguous"
    assert span["url"] == "", "an ambiguous span must not carry a resolved URL"
    assert [c["platform"] for c in span["candidates"]] == ["clawgate", "github"]


def test_an_unambiguous_span_carries_its_single_url():
    (span,) = MS.scan_mention_spans("civitai/talos-infra#1065")
    assert span["platform"] == "github"
    assert span["ambiguous"] is False
    assert span["url"] == "https://github.com/civitai/talos-infra/issues/1065"


def test_the_two_hash_patterns_never_claim_the_same_span():
    """`repo#N` belongs to the GitHub pattern and `#N` to the bare one. If both
    could match one span, every `devrc#591` would be double-counted in the
    telemetry and would open a picker instead of the issue."""
    spans = MS.scan_mention_spans("see devrc#591 and also #370")
    assert [(s["platform"], s["id"]) for s in spans] == [
        ("github", "591"), ("ambiguous", "370")]


# --------------------------------------------------------------------------- #
# 🔴 NEGATIVE CASES — the shapes a noisy scanner would fire on
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [
    # Hex colour literals. Six digits is the shape that matters: this repo's own
    # gruvbox palette is full of them.
    'background = "#282828";',
    "foreground = \"#ebdbb2\";",
    "#000000",
    "#ff00ff",
    "#fff",
    "color: #1a2b3c;",
    "#123456",
    # Markdown headings — CommonMark requires a space, so digits never follow.
    "# Heading",
    "## Section two",
    "### 3 things to do",
    # Shell comments. The shebang case is spelled with `/bin/sh` rather than
    # `/usr/bin/env …` on purpose: the shape under test is `#` followed by a
    # non-digit, which either spelling exercises identically, and the repo-wide
    # `test_runtime_shebangs.py` guard flags a literal `/usr/bin/env` shebang in
    # any test file. Satisfying it here costs nothing and needs no allowlist pin.
    "#!/bin/sh",
    "# comment about 5 files",
    "grep -c foo  # 12 matches",
    # HTML numeric entities.
    "a &#8212; b",
    "&#123;",
    # URL fragments — Alacritty's URL hint already owns whole URLs.
    "https://example.com/page#123",
    "https://example.com/docs/index.html#12",
    "https://github.com/innovation-upstream/devrc/pull/992",
    # Markdown links into a file with an anchor.
    "[see](docs/notes.md#12)",
    # Not a reference at all.
    "the C# language",
    "issue number 370 without a hash",
])
def test_negative_no_mention_is_detected(text):
    assert MS.scan_mentions(text) == [], f"false positive on {text!r}"


def test_six_digit_hex_cannot_match_by_backtracking_to_five_digits():
    """🔴 THE GUARD THAT MAKES THE DIGIT BOUND REAL. `\\d{1,5}` alone would match
    `#28282` inside `#282828`. The trailing lookahead is what rejects every
    backtrack, and this test is what catches its removal — deleting `_NUM_END`
    turns this red while most of the suite stays green."""
    assert MS.scan_mentions("#282828") == []
    assert MS.scan_mentions("#2828281") == []
    # ... while a five-digit reference still resolves.
    assert [m["id"] for m in MS.scan_mentions("#28282")] == ["28282", "28282"]


def test_the_documented_residual_false_positive_is_still_the_only_one():
    """A three-digit numeric CSS colour is character-for-character an issue
    number and no rule separates them. It is accepted, documented in the module,
    and pinned here so the acceptance stays a DECISION rather than becoming a
    surprise."""
    for shape in MS._KNOWN_FALSE_POSITIVES:
        spans = MS.scan_mention_spans(shape)
        assert len(spans) == 1 and spans[0]["platform"] == "ambiguous", (
            f"{shape!r} was expected to still match as an ambiguous span")


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [None, 0, [], {}, b"#370"])
def test_a_non_string_is_not_a_crash(value):
    assert MS.scan_mentions(value) == []


def test_empty_text():
    assert MS.scan_mentions("") == []
    assert MS.scan_mention_spans("") == []


def test_context_is_bounded_and_single_line():
    text = "x" * 500 + " #370 " + "y" * 500
    (span,) = MS.scan_mention_spans(text)
    assert "#370" in span["context"]
    assert len(span["context"]) <= MS.CONTEXT_CHARS + len("#370")
    assert "\n" not in span["context"]


def test_context_collapses_whitespace():
    (span,) = MS.scan_mention_spans("see\n\n   #370\t\tnow")
    assert span["context"] == "see #370 now"


def test_results_are_ordered_by_position():
    text = "868abc123 then devrc#1 then #2"
    assert [m["start"] for m in MS.scan_mentions(text)] == sorted(
        m["start"] for m in MS.scan_mentions(text))
