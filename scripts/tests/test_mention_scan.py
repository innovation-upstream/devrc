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


def test_the_clawgate_candidate_points_at_the_task_details_page():
    """clawgate serves a real, server-rendered page per task at `GET /tasks/{id}`
    (internal/api/server.go, `s.handleTaskDetail`), so the candidate is a PATH,
    not a fragment on the board."""
    (clawgate, _github) = MS.scan_mentions("#370")
    assert clawgate["url"] == "https://clawgate.zacx.dev/tasks/370"


def test_the_clawgate_url_carries_NO_fragment():
    """🔴 The regression guard for a partial revert. `#task-<id>` was the old
    pattern and it resolved only for a card the board had already rendered; the
    details page has no such precondition. A `#` anywhere in the URL means some
    caller is back on the fragment, so pin its ABSENCE rather than only pinning
    the new string — a half-reverted `…/tasks/370#task-370` passes an `endswith`
    check and fails this one."""
    (clawgate, _github) = MS.scan_mentions("#370")
    assert "#" not in clawgate["url"], clawgate["url"]
    assert "#" not in MS.clawgate_url("370")
    assert not MS.CLAWGATE_TASKS_URL.endswith("/")


def test_the_clawgate_url_round_trips_a_multi_digit_id():
    """The id is interpolated whole, never truncated or re-parsed. Distinct
    digit-counts, and no digit repeated across the cases, so a mutation that
    slices or reformats the id cannot land on the expected string by accident."""
    for ident, expected in (
        ("7", "https://clawgate.zacx.dev/tasks/7"),
        ("42", "https://clawgate.zacx.dev/tasks/42"),
        ("370", "https://clawgate.zacx.dev/tasks/370"),
        ("10593", "https://clawgate.zacx.dev/tasks/10593"),
    ):
        assert MS.clawgate_url(ident) == expected
        (clawgate, _github) = MS.scan_mentions(f"#{ident}")
        assert clawgate["url"] == expected, ident
        assert clawgate["id"] == ident


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


# --------------------------------------------------------------------------- #
# 🔴 THE PROFILE SPLIT
#
# The module used to run ONE set of regexes so the terminal and the telemetry
# could never disagree. That invariant is deliberately relaxed along exactly one
# axis, so the relaxation has to be EXPLICIT: a ledger, pinned two-way, and a
# default that keeps the click surface where it was.
# --------------------------------------------------------------------------- #
def _compiled_pattern_names():
    """Every module-level compiled regex, read from the module itself rather
    than restated here — so a pattern added tomorrow is covered without editing
    this file. Private helpers (leading underscore) are excluded: the ledger is
    about what the SCAN consults, and `_` marks an implementation detail."""
    import re as _re
    return {name for name, val in vars(MS).items()
            if isinstance(val, _re.Pattern) and not name.startswith("_")}


def test_the_pattern_ledger_is_pinned_TWO_WAY():
    """🔴 BOTH DIRECTIONS, because each one fails differently and silently.

    A pattern with NO ledger entry is never consulted in any profile — it
    compiles, it looks live, and `scan_mentions` skips it forever.
    A ledger entry naming NO pattern is a row asserting coverage that does not
    exist, which is worse than no row because it stops anyone looking."""
    declared = set(MS.PATTERN_LEDGER)
    compiled = _compiled_pattern_names()
    assert declared - compiled == set(), (
        "ledger entries that name no compiled pattern: "
        f"{sorted(declared - compiled)}")
    # `NON_SCAN_PATTERNS` is the ONLY other home a compiled pattern may have,
    # and it is itself pinned below — so "in neither" is a failure, not a gap.
    unclassified = compiled - declared - MS.NON_SCAN_PATTERNS
    assert unclassified == set(), (
        "compiled patterns in NEITHER the ledger nor NON_SCAN_PATTERNS — they "
        f"are consulted in NO profile and are dead: {sorted(unclassified)}")


def test_the_non_scan_escape_hatch_is_pinned_TOO():
    """An escape hatch nobody has to justify is how the next pattern lands in no
    ledger at all. Both directions again: a name here must be a real compiled
    pattern, and it must not ALSO claim a profile in the ledger."""
    compiled = _compiled_pattern_names()
    assert MS.NON_SCAN_PATTERNS <= compiled, (
        f"names no compiled pattern: {sorted(MS.NON_SCAN_PATTERNS - compiled)}")
    assert not (MS.NON_SCAN_PATTERNS & set(MS.PATTERN_LEDGER)), (
        "a pattern cannot be both scanned and not-scanned")
    assert MS.NON_SCAN_PATTERNS == {"OWNER_REPO_VALUE_RE"}, (
        "the hatch grew — every addition needs its own reason in the module")


def test_every_ledger_entry_names_real_profiles_and_a_real_role():
    for name, entry in MS.PATTERN_LEDGER.items():
        assert entry.profiles, f"{name} is enabled in no profile at all"
        assert set(entry.profiles) <= set(MS.PROFILES), name
        assert entry.role in ("detect", "attribute"), f"{name}: {entry.role}"


def test_every_ledger_SAMPLE_actually_matches_its_pattern():
    """A sample that does not match makes the hint check below vacuous — it
    would prove a property of a string nothing ever matched."""
    for name, entry in MS.PATTERN_LEDGER.items():
        pattern = getattr(MS, name)
        assert pattern.search(entry.sample), (
            f"{name}'s ledger sample {entry.sample!r} does not match it")


def test_every_detecting_patterns_HINTS_are_honest_about_its_sample():
    """🔴 THE HINT IS A CLAIM: "a text without one of these cannot match". A hint
    that is not present in a text the pattern DOES match is a false claim, and it
    shows up in production as a shape that is silently never scanned."""
    for name, entry in MS.PATTERN_LEDGER.items():
        if entry.role != "detect":
            continue
        assert entry.hints, f"{name} declares no hints"
        assert any(h in entry.sample for h in entry.hints), (
            f"{name}'s hints {entry.hints} appear in none of its own sample "
            f"{entry.sample!r} — the pre-filter would skip it")


def test_attribution_patterns_contribute_NO_hints():
    """They cannot produce a mention on their own, so a text carrying only one
    of them has nothing to emit; letting them widen the pre-filter would cost
    the short-circuit and buy nothing."""
    for name, entry in MS.PATTERN_LEDGER.items():
        if entry.role == "attribute":
            assert entry.hints == (), f"{name} widened the filter for nothing"


def test_the_terminal_profiles_hints_are_still_the_original_two():
    """🔴 THE CLICK SURFACE DID NOT MOVE. This is the value the tailer used to
    hardcode, and it is the value `scripts/mention-open.py` still implies."""
    assert MS.mention_hints() == ("#", "868")
    assert MS.mention_hints(MS.PROFILE_TERMINAL) == ("#", "868")


def test_the_telemetry_profiles_hints_cover_every_new_shape():
    """🔴 THE INERT-PREFILTER GUARD, at the module end of the seam. Each of these
    shapes contains NEITHER '#' NOR '868', so under the terminal hints the
    tailer's short-circuit would skip the block and the regex would never run."""
    hints = MS.mention_hints(MS.PROFILE_TELEMETRY)
    for text in ("https://github.com/gardenersguild/trowelcast/pull/7",
                 "/audit-pr 1291",
                 "audit-pr 1291",
                 "gh pr view 1291",
                 "gh issue close 42",
                 "clawgate task 370"):
        assert any(h in text for h in hints), f"{text!r} would be pre-filtered away"
        assert not any(h in text for h in MS.mention_hints(MS.PROFILE_TERMINAL)), (
            f"{text!r} was expected to be invisible to the OLD hints — if it is "
            "not, this test proves nothing about the widening")


def test_an_unknown_profile_falls_back_to_the_NARROW_one():
    """🔴 A typo must never widen the click surface. Falling back to `telemetry`
    would be the same defect in the direction nobody notices."""
    assert MS.patterns_in("teleemtry") == MS.patterns_in(MS.PROFILE_TERMINAL)
    assert MS.scan_mentions("gh pr view 1291", profile="teleemtry") == []
    assert MS.mention_hints("nonsense") == ("#", "868")


def test_the_terminal_profile_is_the_DEFAULT():
    """Pinned as a behaviour, not only as a default argument: this is what keeps
    `scripts/mention-open.py` unchanged without it having to say anything."""
    for text in ("https://github.com/gardenersguild/trowelcast/pull/7",
                 "/audit-pr 1291", "gh pr view 1291", "clawgate task 370",
                 "https://clawgate.zacx.dev/tasks#task-370"):
        assert MS.scan_mentions(text) == [], f"the DEFAULT profile matched {text!r}"
        assert MS.scan_mention_spans(text) == []


# --------------------------------------------------------------------------- #
# The enumerated widening — one positive per new shape (telemetry profile)
# --------------------------------------------------------------------------- #
TELEMETRY = {"profile": MS.PROFILE_TELEMETRY}


def test_a_github_pull_url_is_a_mention_and_carries_its_own_owner():
    (span,) = MS.scan_mention_spans(
        "landed https://github.com/gardenersguild/trowelcast/pull/7 today", **TELEMETRY)
    assert span["platform"] == "github"
    assert span["id"] == "7"
    assert span["repo"] == "gardenersguild/trowelcast"
    assert span["repo_source"] == MS.SOURCE_URL
    assert span["url"] == "https://github.com/gardenersguild/trowelcast/issues/7"


def test_a_github_issues_url_too_and_a_scheme_is_optional():
    for text in ("github.com/hobbyist/plotwidget/issues/4213",
                 "https://github.com/hobbyist/plotwidget/issues/4213",
                 "http://github.com/hobbyist/plotwidget/issues/4213"):
        (span,) = MS.scan_mention_spans(text, **TELEMETRY)
        assert span["repo"] == "hobbyist/plotwidget", text
        assert span["id"] == "4213", text


def test_a_url_with_trailing_path_segments_still_names_the_same_pr():
    """`/pull/7/files` is PR 7. `_NUM_END` permits a following `/` on purpose."""
    (span,) = MS.scan_mention_spans(
        "https://github.com/gardenersguild/trowelcast/pull/7/files", **TELEMETRY)
    assert span["id"] == "7"


def test_a_dotted_repo_name_IS_detected_in_the_URL_form():
    """The no-dots rule exists to keep `index.html#12` out of the `repo#N` form.
    A URL delimits `owner/repo` with slashes, so the hazard is absent and the
    cost the module documents for `repo#N` is not paid here."""
    (span,) = MS.scan_mention_spans(
        "https://github.com/hobbyist/plot.widget.js/pull/9", **TELEMETRY)
    assert span["repo"] == "hobbyist/plot.widget.js"


@pytest.mark.parametrize("text,num", [
    ("/audit-pr 1291", "1291"),
    ("audit-pr 1291", "1291"),
    ("run /audit-pr 1291 next", "1291"),
])
def test_the_audit_pr_command_is_a_github_reference(text, num):
    (span,) = MS.scan_mention_spans(text, **TELEMETRY)
    assert span["platform"] == "github"
    assert span["id"] == num


@pytest.mark.parametrize("text,num", [
    ("gh pr view 1291", "1291"),
    ("gh pr merge 887 --squash", "887"),
    ("gh issue close 42", "42"),
    ("gh issue comment 3", "3"),
])
def test_the_gh_cli_forms_are_github_references(text, num):
    spans = MS.scan_mention_spans(text, **TELEMETRY)
    assert [s["id"] for s in spans] == [num], text
    assert spans[0]["platform"] == "github"


def test_a_gh_subcommand_that_is_NOT_on_the_enumerated_list_is_not_detected():
    """🔴 THE ENUMERATION IS THE GUARD. `\\w+` there would make `gh pr 12345`,
    `gh repo clone 3` and any future subcommand a reference."""
    assert MS.scan_mentions("gh pr rebase 1291", **TELEMETRY) == []
    assert MS.scan_mentions("gh repo view 1291", **TELEMETRY) == []
    assert "rebase" not in MS.GH_CLI_SUBCOMMANDS


@pytest.mark.parametrize("text", ["clawgate task 370", "Clawgate task 370",
                                  "the clawgate Task 370 board"])
def test_clawgate_task_N_is_a_clawgate_reference(text):
    (span,) = MS.scan_mention_spans(text, **TELEMETRY)
    assert span["platform"] == "clawgate"
    assert span["id"] == "370"
    assert span["url"] == "https://clawgate.zacx.dev/tasks/370"


def test_a_BARE_task_N_is_deliberately_NOT_detected():
    """🔴 179 occurrences in one measured 24h window, overwhelmingly prose. The
    literal `clawgate` is the entire anchor; without it there is no pattern here
    worth having."""
    for text in ("task 5 of 9", "the task 3 lines down", "task 370"):
        assert MS.scan_mentions(text, **TELEMETRY) == [], text


def test_the_legacy_task_anchor_resolves_to_the_task_page():
    (span,) = MS.scan_mention_spans(
        "https://clawgate.zacx.dev/tasks#task-370", **TELEMETRY)
    assert span["platform"] == "clawgate"
    assert span["url"] == "https://clawgate.zacx.dev/tasks/370"
    assert "#" not in span["url"], "the constructor must not mint a fragment"


def test_the_legacy_anchor_is_reachable_AFTER_A_WORD_CHARACTER():
    """🔴 THE INERT-GUARD CASE FOR THIS PATTERN. Every real occurrence of the
    legacy form sits at the end of `.../tasks#task-N`, where the character before
    `#` is a LETTER — so the module's standard left guard rejects all of them and
    a pattern carrying it would be dead on arrival. Deleting the relaxed
    lookbehind turns this red while every other case above stays green."""
    (span,) = MS.scan_mention_spans("tasks#task-370", **TELEMETRY)
    assert span["id"] == "370"


def test_git_shas_are_NOT_detected_in_either_profile():
    """🔴 A `[0-9a-f]{7,12}` probe over one 24h window returned ~520,000 hits.
    Pinned because it is the widening somebody will reach for next."""
    for text in ("fixed in 099771da", "squash fd68d48c", "at a1adf740 today"):
        assert MS.scan_mentions(text, **TELEMETRY) == [], text
        assert MS.scan_mentions(text) == [], text


def test_no_two_spans_ever_OVERLAP_in_the_wider_profile():
    """🔴 The widening's structural hazard: two patterns claiming overlapping
    text emit the SAME reference twice. `clawgate task #370` is the case that
    forced `CLAWGATE_TASK_RE` to refuse a `#`."""
    text = ("clawgate task 370 and clawgate task #371, "
            "https://github.com/gardenersguild/trowelcast/pull/7 "
            "plus gh pr view 12 and /audit-pr 13 and tasks#task-14 "
            "and 868abc123 and hobbyist/plotwidget#15 and #16")
    spans = MS.scan_mention_spans(text, **TELEMETRY)
    assert len(spans) >= 9, spans
    ends = [(s["start"], s["end"]) for s in spans]
    for (_a_start, a_end), (b_start, _b_end) in zip(ends, ends[1:]):
        assert a_end <= b_start, f"spans overlap: {ends}"


# --------------------------------------------------------------------------- #
# ATTRIBUTION — each source proved SEPARATELY
#
# 🔴 THE FIXTURE VALUES ARE PAIRWISE DISTINCT AND DISTINCT FROM EVERY CONSTANT
# THE ASSERTIONS NAME, so a mutant that hardcodes any one owner/repo literal
# cannot survive by landing on the expected value.
# --------------------------------------------------------------------------- #
REPOS = {
    "trowelcast": "gardenersguild/trowelcast",
    "plotwidget": "hobbyist/plotwidget",
    "spadeworks": "rivalorg/spadeworks",
}


def test_A2_a_repo_token_immediately_before_the_ref_attributes_it():
    (span,) = MS.scan_mention_spans("trowelcast PR #1291", repos=REPOS, **TELEMETRY)
    assert span["repo"] == "gardenersguild/trowelcast"
    assert span["repo_source"] == MS.SOURCE_ADJACENT
    assert span["candidates"][1]["url"] == (
        "https://github.com/gardenersguild/trowelcast/issues/1291")


def test_A2_works_without_a_connector_word_too():
    (span,) = MS.scan_mention_spans("plotwidget #42", repos=REPOS, **TELEMETRY)
    assert span["repo"] == "hobbyist/plotwidget"


def test_A2_picks_the_repo_ACTUALLY_written_not_a_fixed_one():
    """Three distinct owners in the fixture, three distinct expectations — a
    mutant returning any single literal dies on two of the three."""
    for token, expected in (("trowelcast", "gardenersguild/trowelcast"),
                            ("plotwidget", "hobbyist/plotwidget"),
                            ("spadeworks", "rivalorg/spadeworks")):
        (span,) = MS.scan_mention_spans(f"{token} PR #7", repos=REPOS, **TELEMETRY)
        assert span["repo"] == expected, token


def test_A2_STAYS_AMBIGUOUS_when_the_token_is_not_in_the_measured_mapping():
    """🔴 THE NO-GUESSING RULE, at the new site. `zzzunknown` is a word, not
    evidence: without a measured mapping entry there is no owner and none is
    synthesised."""
    (span,) = MS.scan_mention_spans("zzzunknown PR #1291", repos=REPOS, **TELEMETRY)
    assert span["repo"] == ""
    assert span["repo_source"] == ""
    assert span["url"] == ""
    assert all(c["url"] == "" or c["platform"] == "clawgate"
               for c in span["candidates"])


def test_A2_does_not_fire_without_a_repos_mapping_at_all():
    (span,) = MS.scan_mention_spans("trowelcast PR #1291", **TELEMETRY)
    assert span["repo"] == ""


def test_A2_does_not_attribute_across_a_LINE_BREAK():
    """`\\Z` not `$`: a repo token ending the previous line is not adjacent.

    🔴 THE SECOND CASE IS THE ONE THAT DISCRIMINATES, and the first alone does
    not. With no trailing space, `[ \\t]+$` cannot match either — so a `$`
    mutant SURVIVED the first case, measured. `$` matches immediately BEFORE a
    final newline, so a line ending in "<repo> " is exactly where the two spell
    different behaviour."""
    (span,) = MS.scan_mention_spans("trowelcast\n#1291", repos=REPOS, **TELEMETRY)
    assert span["repo"] == ""
    (span,) = MS.scan_mention_spans("trowelcast \n#1291", repos=REPOS, **TELEMETRY)
    assert span["repo"] == "", "`$` matched before the trailing newline"


def test_A2_a_common_english_word_before_a_ref_attributes_NOTHING():
    for text in ("fixed in #370", "PR #370", "see #370"):
        (span,) = MS.scan_mention_spans(text, repos=REPOS, **TELEMETRY)
        assert span["repo"] == "", text


def test_A2_a_NON_connector_word_between_the_repo_and_the_ref_breaks_adjacency():
    """🔴 WHAT THE ENUMERATED CONNECTOR LIST IS FOR, and the case that
    discriminates it from `\\w+`. Under a generic word `trowelcast thing #370`
    attributes to trowelcast — a repository the operator was not referring to.
    Measured: without this case a `\\w+` mutant SURVIVED, because every other
    negative here uses a token that is absent from the mapping anyway, so the
    mapping killed the mutant rather than the enumeration."""
    for text in ("trowelcast thing #370", "trowelcast unrelated #370",
                 "trowelcast, #370"):
        (span,) = MS.scan_mention_spans(text, repos=REPOS, **TELEMETRY)
        assert span["repo"] == "", text
    # ...while the enumerated connectors still work, so this is a rule and not
    # simply a narrower pattern.
    (span,) = MS.scan_mention_spans("trowelcast issue #370", repos=REPOS, **TELEMETRY)
    assert span["repo"] == "gardenersguild/trowelcast"


def test_A3_a_github_url_elsewhere_in_the_block_attributes_a_bare_ref():
    text = ("reviewed https://github.com/rivalorg/spadeworks/pull/8 "
            "and then fixed #1291")
    spans = MS.scan_mention_spans(text, **TELEMETRY)
    bare = [s for s in spans if s["id"] == "1291"]
    assert len(bare) == 1
    assert bare[0]["repo"] == "rivalorg/spadeworks"
    assert bare[0]["repo_source"] == MS.SOURCE_URL


def test_A4_a_repo_flag_elsewhere_in_the_block_attributes_a_bare_ref():
    text = "ran gh pr list --repo hobbyist/plotwidget, then closed #1291"
    bare = [s for s in MS.scan_mention_spans(text, **TELEMETRY) if s["id"] == "1291"]
    assert len(bare) == 1
    assert bare[0]["repo"] == "hobbyist/plotwidget"
    assert bare[0]["repo_source"] == MS.SOURCE_FLAG


def test_A4_attributes_the_gh_cli_reference_beside_it():
    """The measured 14/14 case: `gh pr <sub> N --repo owner/repo`."""
    (span,) = MS.scan_mention_spans(
        "gh pr view 1291 --repo rivalorg/spadeworks", **TELEMETRY)
    assert span["repo"] == "rivalorg/spadeworks"
    assert span["url"] == "https://github.com/rivalorg/spadeworks/issues/1291"


def test_the_ladder_ranks_ADJACENT_above_URL_above_FLAG_above_DEFAULT():
    """🔴 ONE ASSERTION PER RUNG, each removing the rung above it. Testing only
    the top of a priority list proves nothing about the order below it."""
    url = "https://github.com/rivalorg/spadeworks/pull/8"
    flag = "--repo hobbyist/plotwidget"
    default = "gardenersguild/trowelcast"

    def repo_of(text, **kw):
        return [s for s in MS.scan_mention_spans(text, **kw, **TELEMETRY)
                if s["id"] == "1291"][0]["repo"]

    assert repo_of(f"{url} {flag} trowelcast PR #1291",
                   repos=REPOS, default_repo=default) == "gardenersguild/trowelcast"
    assert repo_of(f"{url} {flag} fixed #1291",
                   repos=REPOS, default_repo=default) == "rivalorg/spadeworks"
    assert repo_of(f"{flag} fixed #1291",
                   repos=REPOS, default_repo=default) == "hobbyist/plotwidget"
    assert repo_of("fixed #1291", repos=REPOS,
                   default_repo=default) == "gardenersguild/trowelcast"


def test_TWO_different_repos_in_one_block_attribute_NOTHING():
    """🔴 SEVERAL IS AN ABSENCE OF AN ANSWER, NOT A TIE TO BREAK. Nearest-wins or
    first-wins would both be a guess wearing a heuristic's clothes."""
    text = ("https://github.com/rivalorg/spadeworks/pull/8 and "
            "https://github.com/hobbyist/plotwidget/pull/9 then #1291")
    bare = [s for s in MS.scan_mention_spans(text, **TELEMETRY) if s["id"] == "1291"]
    assert bare[0]["repo"] == ""
    text2 = ("gh pr list --repo rivalorg/spadeworks; "
             "gh pr list --repo hobbyist/plotwidget; #1291")
    bare2 = [s for s in MS.scan_mention_spans(text2, **TELEMETRY) if s["id"] == "1291"]
    assert bare2[0]["repo"] == ""


def test_the_SAME_repo_named_twice_still_attributes():
    """The other side of `_sole`: a boundary that also swallowed the legitimate
    case would be a refusal dressed as a rule."""
    text = ("https://github.com/rivalorg/spadeworks/pull/8 and "
            "https://github.com/rivalorg/spadeworks/pull/9 then #1291")
    bare = [s for s in MS.scan_mention_spans(text, **TELEMETRY) if s["id"] == "1291"]
    assert bare[0]["repo"] == "rivalorg/spadeworks"


def test_a_ref_that_NAMES_a_repo_is_never_re_attributed_from_the_block():
    """🔴 Substituting a different repository for the one the operator wrote is
    strictly worse than admitting the owner is unknown."""
    text = ("https://github.com/rivalorg/spadeworks/pull/8 "
            "and separately zzzunknown#1291")
    named = [s for s in MS.scan_mention_spans(text, **TELEMETRY) if s["id"] == "1291"]
    assert named[0]["repo"] == ""
    assert named[0]["url"] == ""


def test_attribution_does_not_SETTLE_the_platform():
    """Which repository and which platform are two questions. A `#N` next to a
    repo name is still possibly a clawgate task."""
    (span,) = MS.scan_mention_spans("trowelcast PR #1291", repos=REPOS, **TELEMETRY)
    assert span["ambiguous"] is True
    assert span["platform"] == "ambiguous"
    assert span["url"] == ""
    assert [c["platform"] for c in span["candidates"]] == ["clawgate", "github"]


def test_repo_source_is_EMPTY_exactly_when_repo_is():
    """🔴 BOTH LAYERS. A span builds its `repo_source` from a candidate that has
    a `repo`, so the span layer cannot observe a candidate that carries a source
    with no repo — the `zzzunknown#1` row is invisible to a span-only sweep and
    is the case the guard exists for."""
    for text, kw in (("fixed #1", {}),
                     ("trowelcast PR #1", {"repos": REPOS}),
                     ("gardenersguild/trowelcast#1", {}),
                     ("zzzunknown#1", {"repos": REPOS}),
                     ("#1", {"default_repo": "rivalorg/spadeworks"})):
        for span in MS.scan_mention_spans(text, **kw, **TELEMETRY):
            assert bool(span["repo"]) == bool(span["repo_source"]), (text, span)
        for cand in MS.scan_mentions(text, **kw, **TELEMETRY):
            assert bool(cand["repo"]) == bool(cand["repo_source"]), (text, cand)


def test_the_explicit_source_is_reported_for_an_owner_written_out():
    (span,) = MS.scan_mention_spans("gardenersguild/trowelcast#1065", **TELEMETRY)
    assert span["repo_source"] == MS.SOURCE_EXPLICIT
    (span,) = MS.scan_mention_spans("#1065", default_repo="hobbyist/plotwidget",
                                    **TELEMETRY)
    assert span["repo_source"] == MS.SOURCE_DEFAULT


def test_a_bare_repo_hash_N_resolved_through_the_MAPPING_is_not_called_explicit():
    """🔴 `explicit` MUST MEAN THE TEXT WROTE THE OWNER OUT.

    `trowelcast#591` and `gardenersguild/trowelcast#591` used to report the SAME
    `repo_source`, because `_resolve_repo` falls through to the caller's mapping
    when no owner was written and the caller labelled the result `explicit`
    regardless. The field's whole stated purpose is telling "the text said so"
    apart from "our mapping said so"; reporting both as `explicit` is one
    measurement pretending to be two.

    Both halves are asserted TOGETHER, and against the SAME repository, so the
    only thing that can differ is the source — a mutant that collapses the two
    dies here rather than on an unrelated fixture difference."""
    (mapped,) = MS.scan_mention_spans("see trowelcast#591", repos=REPOS, **TELEMETRY)
    (written,) = MS.scan_mention_spans("see gardenersguild/trowelcast#591",
                                       repos=REPOS, **TELEMETRY)
    assert mapped["repo"] == written["repo"] == "gardenersguild/trowelcast"
    assert mapped["repo_source"] == MS.SOURCE_MAPPED, (
        "a MAPPING-resolved repo is reported as if the text stated it")
    assert written["repo_source"] == MS.SOURCE_EXPLICIT
    assert MS.SOURCE_MAPPED != MS.SOURCE_EXPLICIT, (
        "a MAPPING-resolved repo is reported as if the text stated it")


def test_the_TERMINAL_profile_reports_the_mapped_source_too():
    """The `repo#N` -> mapping route is in BOTH profiles (it is `GITHUB_RE`), so
    the new value is not a telemetry-only vocabulary the click surface never
    emits."""
    (span,) = MS.scan_mention_spans("see plotwidget#42", repos=REPOS)
    assert (span["repo"], span["repo_source"]) == ("hobbyist/plotwidget",
                                                   MS.SOURCE_MAPPED)


def test_a_repo_hash_N_the_MAPPING_CANNOT_resolve_reports_NO_source():
    """🔴 THE `source if repo else SOURCE_NONE` GUARD, AT THE LAYER THAT CAN SEE
    IT. `zzzunknown#12` takes the `mapped` branch — the text named a repo — but
    the mapping does not hold it, so there is no owner. Without the guard the
    CANDIDATE reads `repo="" repo_source="mapped"`: a claim that a resolution
    happened, beside the evidence that it did not.

    ⚠ ASSERTED ON `scan_mentions`, NOT ON A SPAN, and that is the whole point.
    `scan_mention_spans` takes its `repo_source` from whichever candidate carries
    a `repo`, so a span with none reports `""` no matter what its candidates say
    — a span-level assertion is satisfied by the span builder and never
    evaluates this guard at all. Measured: a mutant deleting the ternary
    SURVIVED a span-level version of this test. The candidate layer is public,
    documented API (`scan_mentions`' docstring pins `""` exactly when `repo` is
    `""`), so the contract is real even though this repo's two consumers both
    read spans."""
    cands = MS.scan_mentions("see zzzunknown#12", repos=REPOS, **TELEMETRY)
    assert [c["platform"] for c in cands] == ["github"], cands
    assert cands[0]["repo"] == ""
    assert cands[0]["repo_source"] == MS.SOURCE_NONE == "", (
        "repo_source claims a resolution that did not happen")
    assert cands[0]["url"] == ""
    # And the span the consumers actually read agrees.
    (span,) = MS.scan_mention_spans("see zzzunknown#12", repos=REPOS, **TELEMETRY)
    assert (span["repo"], span["repo_source"], span["url"]) == ("", "", "")


# --------------------------------------------------------------------------- #
# 🔴 THE PROFILE SPLIT, ENFORCED AT THE THREE ATTRIBUTION SITES
#
# The PR that introduced `profile` relaxed this module's "one set of regexes,
# they can never drift apart" invariant on the argument that the DEFAULT stays
# `terminal`. That default was pinned at the module (`patterns_in`) and nowhere
# at the three places that CONSULT it, so an independent mutation sweep deleted
# each `if "<NAME>" in on` in turn with the whole suite green. These are the
# pins. Each case names ONE route and would still pass with the other two guards
# deleted, so the file cannot lose coverage of a route by a fixture edit.
# --------------------------------------------------------------------------- #
def test_no_TELEMETRY_only_attribution_route_answers_in_the_terminal_profile():
    cases = {
        # `REPO_BEFORE_RE` — the adjacent-token route.
        "adjacent": "trowelcast PR #1291",
        # `GITHUB_URL_RE` as an ATTRIBUTION source (its DETECT role is guarded
        # separately). The bare `#1291` is what must stay unattributed.
        "url": "https://github.com/hobbyist/plotwidget/pull/8 and also #1291",
        # `REPO_FLAG_RE` — the `--repo owner/repo` route.
        "flag": "--repo rivalorg/spadeworks then #1291",
    }
    for route, text in cases.items():
        terminal = MS.scan_mention_spans(text, repos=REPOS)
        bare = [s for s in terminal if s["raw"] == "#1291"]
        assert len(bare) == 1, (route, terminal)
        assert bare[0]["repo"] == "", (
            f"the {route} attribution route answered in the TERMINAL profile")
        assert bare[0]["repo_source"] == "", (route, bare[0])
        # POSITIVE CONTROL — the same text DOES attribute at the telemetry
        # profile. Without this the assertions above would be satisfied by a
        # fixture that attributes nowhere, which proves nothing about the guard.
        telemetry = MS.scan_mention_spans(text, repos=REPOS, **TELEMETRY)
        attributed = [s for s in telemetry if s["raw"] == "#1291"]
        assert len(attributed) == 1 and attributed[0]["repo"], (route, telemetry)


# --------------------------------------------------------------------------- #
# TASK_ANCHOR_RE's relaxed-but-not-absent left guard
# --------------------------------------------------------------------------- #
def test_the_legacy_anchor_left_guard_still_rejects_an_entity_and_a_heading_run():
    """🔴 `(?<![&#])` IS LIVE, and it was untested — a sweep deleting it survived.

    The guard is deliberately LOOSER than `_BARE_BEFORE` (the real
    `…/tasks#task-370` case is preceded by a letter), but it is not absent: `&`
    excludes an HTML entity and `#` excludes a `##` run. The positive control is
    the third assertion — without it a mutant that broke the pattern entirely
    would also pass the two negatives."""
    assert MS.scan_mentions("&#task-1", **TELEMETRY) == [], (
        "the legacy-anchor left guard is gone")
    assert MS.scan_mentions("##task-1", **TELEMETRY) == [], (
        "the legacy-anchor left guard is gone")
    assert [(m["platform"], m["id"]) for m in
            MS.scan_mentions("https://clawgate.zacx.dev/tasks#task-370",
                             **TELEMETRY)] == [("clawgate", "370")]


# --------------------------------------------------------------------------- #
# The wider profile's own documented residuals
# --------------------------------------------------------------------------- #
def test_the_telemetry_residual_false_positives_are_pinned_as_a_set():
    """Same discipline as `_KNOWN_FALSE_POSITIVES`: the acceptance stays a
    DECISION rather than becoming a surprise. Each of these DOES match, and each
    is documented in the module."""
    assert set(MS._KNOWN_FALSE_POSITIVES_TELEMETRY) == {
        "gh pr view 12", "/audit-pr 12", "[the old anchor](#task-1)"}
    for shape in MS._KNOWN_FALSE_POSITIVES_TELEMETRY:
        assert MS.scan_mentions(shape, **TELEMETRY), (
            f"{shape!r} was expected to still match in the wider profile")


def test_the_original_residual_set_is_UNCHANGED():
    """The click surface's accepted noise did not move — pinned separately so a
    telemetry-only addition cannot quietly land on it."""
    assert set(MS._KNOWN_FALSE_POSITIVES) == {"#123"}


# --------------------------------------------------------------------------- #
# clean_repo_map — ONE definition of the mapping-value rule
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [
    "acme/widget/", "acme//widget", "acme/widget ", "acme/wid\nget",
    "acme/widget\n", "/acme/widget", "acme", 12, None,
])
def test_clean_repo_map_drops_anything_that_is_not_exactly_owner_slash_repo(value):
    assert MS.clean_repo_map({"widget": value}) == {}


def test_clean_repo_map_keeps_a_good_entry_and_is_total_on_junk():
    assert MS.clean_repo_map({"a": "gardenersguild/trowelcast", "b": "nope"}) == {
        "a": "gardenersguild/trowelcast"}
    for junk in (None, [], "", 3, {1: "a/b"}):
        assert MS.clean_repo_map(junk) == {}
