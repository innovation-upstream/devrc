#!/usr/bin/env python3
"""Mutation battery for `scripts/round0-attribution-rate.py` — the round-0 rate.

    nix develop ~/workspace/devrc -c python3 scripts/tests/mutants-round0-attribution-rate.py

Not run by CI. An author/reviewer instrument, kept IN THE TREE so
"mutation-verified" can be RE-DERIVED instead of believed — the same reason
`scripts/tests/mutants-audit-dispatch.py` and `mutants-audit-ladder.sh` exist,
and it follows their conventions. 🔴 THIS FILE WAS ITSELF A FINDING: `#1901
round 3` observed that two claims in that PR — that kill quality is classified,
and that the sweep prints its own coverage gaps — rested on a script living in a
session scratchpad, so nobody could check either. A sweep you cannot re-run is a
sentence, not evidence.

🔴 EACH MUTANT NAMES THE EXACT SET OF TESTS THAT MUST KILL IT, because "a test
failed" is not coverage. These pins overlap by design, so a mutant can die to a
DIFFERENT test's error while its own guard is unreachable — the defect this arc
hit twice (rounds 2 and 3, the `none-consulted` and `selected` branches). Two
failures are reported separately because the responses differ:
  * an expected killer ABSENT  -> 🔴 WRONG-KILLER: that pin is DEAD.
  * an UNEXPECTED extra killer -> ⚠ EXTRA-KILLER: the row no longer isolates
                                  what it names (often fine when the RUNTIME pin
                                  refuses, which fails every test that calls
                                  `main()` — those are listed as `RUNTIME`).

🔴 A WRONG-KILLER HAS TWO CAUSES AND THEY DEMAND OPPOSITE FIXES — establish WHICH
before touching anything, because an expectation edited to match what was
observed is how a guard's claim becomes unfalsifiable:
  (a) the row names a test that CANNOT see this mutation, and some OTHER guard
      genuinely covers it (it failed, on its own assertion) -> the named pin is
      dead weight: DELETE it from the set, and say why in the row;
  (b) the named test is the one that SHOULD cover the mutation and does not ->
      that is a GUARD GAP: widen the guard until it fails, and leave the
      expectation alone.
Both were live here on the first committed sweep. (a): three rows named
`test_the_pin_reach_the_ANCHORS_header_CLAIMS_is_the_reach_it_HAS` for breaking
an ANCHOR's `text`, which is not a claim that test makes. (b): the same test was
named by `grows-only-session-lines` and could not see it, because the test
exercised `match_anchors` while the mutation moved the CALL SITE inside
`check_pins` — `claude/RULES.md` → "isolation-seam". 🔴 The tell for the serious
shape is `own=[] quality=none`: 19 tests failed and not ONE was the named killer,
i.e. a mutant scored KILLED while nothing showed its guard had run.

🔴 AND EACH KILL CARRIES ITS QUALITY, per failing test, because a mutant that
dies on an unrelated exception proves nothing about the guard:
  * `assert` — the failing test's LAST `E` line is an assertion. The guard spoke.
  * `raise`  — it ended on an exception (KeyError/TypeError/...). TREAT AS
               UNPROVEN and fix the test to assert instead. MEASURED: mutant
               `block-scope-deleted-from-anchor` first died with `KeyError:
               'scope'` from the test's own subscript — scored KILLED while
               proving nothing, caught only by reading the message.
  ⚠ WHAT THIS CANNOT DO: it reads pytest's own per-failure sections, so it is
  per-TEST rather than per-run — but it is still text. A test whose assertion
  message quotes the word `KeyError` would be misread, and a `pytest.raises`
  assertion legitimately ends on an exception line. Read the printed message
  when a row says `raise`; do not treat the label as a proof.

SELF-CHECKS, all three refuse to print a clean sweep:
  * C0 control — the UNMUTATED copy must be green, or no row means anything.
  * C3 restore — every mutant is reverted and the file re-hashed to the pristine
    digest, so a later row cannot inherit an earlier mutation.
  * C4 coverage — the ANCHOR ids and BRANCH names are derived FROM THE MODULE and
    every one must be named by some mutant. A hand-maintained list is
    structurally blind to code added in the same round as the sweep, which is
    exactly how rounds 2 and 3 each found survivors among the previous round's
    own additions.

⚠ `PYTHONDONTWRITEBYTECODE=1` is set for every child: a same-length edit landing
in the same second as the last import is invisible to CPython's
mtime-in-seconds bytecode cache, and the mutant would score SURVIVED without
ever executing.

Exit codes: 0 every mutant killed by exactly its named tests · 1 a control
failed, a mutant survived, or a killer set disagreed.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from functools import partial
from pathlib import Path

#: Redirected to a log (which is how anyone reads a 6-minute sweep), an
#: unflushed print shows NOTHING until exit — indistinguishable from a hang,
#: and `wc -c` then reports a near-empty log for a run that is fine.
say = partial(print, flush=True)

REPO = Path(__file__).resolve().parents[2]
REL_SCRIPT = "scripts/round0-attribution-rate.py"
REL_TEST = "scripts/tests/test_round0_attribution_rate.py"

#: The file this battery mutates. 🔴 READ BY `test_mutation_battery_anchors.py`,
#: which pins every `old` below to occur EXACTLY ONCE in it — the collected guard
#: that catches a row gone 0x after somebody reformats the anchored line without
#: ever running this sweep. That module's ledger names this file, so the two-way
#: pin covers it; the name, the `SCRIPT` attribute and the row SHAPE below are all
#: part of satisfying it.
SCRIPT = REPO / REL_SCRIPT

#: 🔴 (name, why it matters, {tests that MUST kill it}, old, new)
#:
#: `old` is the FOURTH field and `new` the FIFTH, which is the shape
#: `test_mutation_battery_anchors.py` reads — the same convention
#: `mutation_battery_operator_asks.py` follows, and not a free choice.
#: A multi-site mutation makes BOTH a TUPLE of equal length (applied in order),
#: because some defects are inherently multi-site — the fabricated-share one was
#: a missing guard AND an `or 0`. ⚠ A tuple against a non-tuple, or two tuples of
#: different length, is a row that applies FEWER edits than it names and is
#: scored under this row's id; that is pinned there too.
#:
#: `RUNTIME` in a killer set means "the runtime pin refuses, so every test that
#: calls `main()` fails"; those rows name their own guard plus that marker.
RUNTIME = "RUNTIME:the-pin-refuses-so-main-callers-fail"

MUTANTS: list[tuple[str, str, set[str], str | tuple[str, ...],
                    str | tuple[str, ...]]] = [
    # ---- the floor and its unit ------------------------------------------
    ("floor-value",
     "the standing 'do not re-tune the feature off n<10' instruction is "
     "weakened to n<1, so one observation licenses a comparison",
     {"test_MIN_SESSIONS_is_ten_and_that_is_a_standing_instruction",
      "test_below_MIN_SESSIONS_it_prints_NOT_MEASURABLE_and_exits_6",
      "test_the_floor_counts_DISTINCT_SESSIONS_not_ledger_lines",
      "test_the_floor_counts_REAL_SESSIONS_not_transcript_FILES"},
     "MIN_SESSIONS = 10", "MIN_SESSIONS = 1"),
    ("floor-refusal-branch",
     "the refusal branch becomes unreachable, so a sub-floor corpus prints a "
     "verdict instead of NOT MEASURABLE",
     {"test_below_MIN_SESSIONS_it_prints_NOT_MEASURABLE_and_exits_6",
      "test_the_floor_counts_DISTINCT_SESSIONS_not_ledger_lines",
      "test_the_floor_counts_REAL_SESSIONS_not_transcript_FILES"},
     "    if n_sessions < MIN_SESSIONS:", "    if n_sessions < 0:"),
    ("floor-counts-files-not-sessions",
     "the floor counts transcript FILES again, which one subagent-heavy session "
     "can satisfy on its own",
     {"test_the_floor_counts_REAL_SESSIONS_not_transcript_FILES"},
     "    n_sessions = len({r.session_id for r in post_in})",
     "    n_sessions = len({r.file for r in post_in})"),
    ("session-id-strip-removed",
     "`.jsonl` is left on the first path segment, so a session with both a "
     "top-level transcript and a `subagents/` dir is counted TWICE — the "
     "mechanism that put a wrong 1,563 in the docstring",
     {"test_the_floor_counts_REAL_SESSIONS_not_transcript_FILES",
      "test_the_naive_session_derivation_double_counts_and_ours_does_not",
      "test_the_printed_files_per_session_ratio_is_not_INVERTED"},
     '    return head[:-len(".jsonl")] if head.endswith(".jsonl") else head',
     "    return head"),
    ("session-id-is-the-file-stem",
     "the session id becomes the FILE stem, so every transcript is its own "
     "session and the floor is met by one busy session",
     {"test_the_floor_counts_REAL_SESSIONS_not_transcript_FILES",
      "test_the_naive_session_derivation_double_counts_and_ours_does_not",
      "test_the_printed_files_per_session_ratio_is_not_INVERTED"},
     "    head = parts[1]", "    head = parts[-1]"),
    # ---- precedence: the defect of rounds 0, 2 and 3 ---------------------
    ("precedence-selected-is-in-population",
     "`selected` re-enters the in-population set — `#1901 round 0`'s defect "
     "verbatim: trailers NAMING a session scored as the words arriving",
     # ⚠ `…NAMED_a_session_but_could_read_NOTHING…` is deliberately NOT expected:
     # MEASURED, it still passes under this mutation, because that block also
     # carries `unmeasured`, which PRECEDES `selected` — which is the very
     # unreachability round 3 found. The battery caught this false expectation of
     # mine on its first run.
     {"test_only_the_answered_role_can_make_a_report_in_population",
      "test_a_block_that_NAMED_a_session_and_read_it_but_matched_NO_ask_is_out",
      RUNTIME},
     '    "selected": ("out", "trailers NAMED a session but no ask from it was "',
     '    "selected": ("in", "trailers NAMED a session but no ask from it was "'),
    ("precedence-selection-anchor-scores-answered",
     "the `selected` anchor carries `answered` again, which is the first "
     "version's bug at the ledger rather than at the precedence",
     {"test_a_block_that_NAMED_a_session_but_could_read_NOTHING_is_NOT_in_population",
      "test_a_block_that_NAMED_a_session_and_read_it_but_matched_NO_ask_is_out",
      RUNTIME},
     '    dict(id="selected", where="sources", role="selected",',
     '    dict(id="selected", where="sources", role="answered",'),
    ("selected-branch-deleted",
     "the `selected` branch vanishes, so a selection-only block falls through "
     "to UNKNOWN instead of out-of-population",
     {"test_a_block_that_NAMED_a_session_and_read_it_but_matched_NO_ask_is_out",
      "test_every_ANCHOR_and_ROLE_is_NAMED_by_this_module",
      "test_every_live_pole_classifies_correctly_today", RUNTIME},
     '    "selected": ("out", "trailers NAMED a session but no ask from it was "\n'
     '                        "rendered — selection is not arrival"),\n', ""),
    ("none-consulted-branch-deleted",
     "the `none-consulted` branch vanishes, so a block stating plainly that "
     "nothing was consulted reads as THIS instrument's blindness",
     {"test_a_block_that_says_NO_SOURCE_WAS_CONSULTED_is_out_not_UNKNOWN",
      "test_every_ANCHOR_and_ROLE_is_NAMED_by_this_module",
      "test_every_live_pole_classifies_correctly_today", RUNTIME},
     '    "none-consulted": ("out", "the asks block says no source was consulted at "\n'
     '                              "all — a fact about that assembly, and no ask "\n'
     '                              "arrived either way"),\n', ""),
    ("block-scope-deleted-from-anchor",
     "`scope=\"block\"` is dropped, so the no-source-consulted line is only "
     "read off a SESSION source line — where it never appears",
     {"test_a_block_that_says_NO_SOURCE_WAS_CONSULTED_is_out_not_UNKNOWN",
      RUNTIME},
     '    dict(id="no-source-consulted", where="sources", role="none-consulted",\n'
     '         scope="block", text="no source was consulted at all",',
     '    dict(id="no-source-consulted", where="sources", role="none-consulted",\n'
     '         text="no source was consulted at all",'),
    ("unmeasured-anchor-made-block-scoped",
     "`unmeasured` becomes block-scoped, so a PR-comment UNKNOWN line marks the "
     "SESSION source unreadable and a report whose asks arrived scores out",
     {"test_a_SESSION_scoped_anchor_is_not_read_off_another_sources_line"},
     '    dict(id="unmeasured", where="sources", role="unmeasured",',
     '    dict(id="unmeasured", where="sources", role="unmeasured", scope="block",'),
    ("other-source-branch-deleted",
     "the `other-source` branch vanishes, so PR-comment-only asks fall through "
     "to UNKNOWN instead of out-of-population",
     {"test_asks_that_arrived_only_from_a_PR_COMMENT_are_out_of_population",
      "test_every_ANCHOR_and_ROLE_is_NAMED_by_this_module",
      "test_every_live_pole_classifies_correctly_today", RUNTIME},
     '    "other-source": ("out", "the only asks came from another source (a PR "\n'
     '                            "comment), not from a session transcript"),\n', ""),
    ("unknown-folded-into-in",
     "UNKNOWN is folded into the in-population bucket, so this instrument's own "
     "blindness inflates the denominator the closing condition is computed over",
     {"test_UNKNOWN_is_excluded_from_the_rates_DENOMINATOR",
      "test_an_asks_block_with_an_unparseable_Sources_block_is_UNKNOWN"},
     '    return "UNKNOWN", (\n        "an asks block is present but this instrument could not read it: "',
     '    return "in", (\n        "an asks block is present but this instrument could not read it: "'),
    # ---- the two-way renderer pin ---------------------------------------
    ("pin-shrinks-half",
     "the SHRINKS half stops computing, so an anchor that matches nothing a "
     "live render emits passes and the denominator silently empties",
     {"test_the_pin_goes_RED_when_THIS_modules_own_ledger_is_mutated",
      "test_the_pin_goes_RED_when_the_renderer_REWORDS_its_ASK_HEADING",
      "test_the_pin_goes_RED_when_the_renderer_REWORDS_its_selection_line"},
     '    missing = sorted({a["id"] for a in ANCHORS} - seen_ids)',
     "    missing = []"),
    ("pin-grows-half",
     "the GROWS half stops accumulating, so a rendered line no anchor covers "
     "passes and nobody decides what it means for the in-population test",
     {"test_the_pin_goes_RED_when_THIS_modules_own_ledger_is_mutated",
      "test_the_pin_goes_RED_when_the_renderer_GAINS_an_uncovered_source_line",
      "test_the_pin_goes_RED_when_the_renderer_REWORDS_its_selection_line",
      # 🔴 ADDED with the guard-gap fix below: the reach guard now drives
      # `check_pins` itself with an uncovered NON-session source line, so a GROWS
      # half wired to `[]` fails it too. A genuine second killer, not a relabel.
      "test_the_pin_reach_the_ANCHORS_header_CLAIMS_is_the_reach_it_HAS"},
     "        unmatched += ask_bad + src_bad", "        unmatched += []"),
    ("grows-only-session-lines",
     "the GROWS reader is filtered back to session-transcript lines — the exact "
     "inertness `#1901 round 1` measured, where an uncovered `PR comment:` line "
     "gave rc 4 with zero GROWS",
     {"test_the_pin_reach_the_ANCHORS_header_CLAIMS_is_the_reach_it_HAS",
      RUNTIME},
     '        _src_roles, src_ids, src_bad = match_anchors(sources, "sources")',
     '        _src_roles, src_ids, src_bad = match_anchors(\n'
     '            session_source_lines(oa.SOURCE_SESSION, sources), "sources")'),
    ("probes-not-derived-from-source-order",
     "the every-source probe is hand-pinned to the session source, so a NEW "
     "source added to `operator_asks` reaches no probe and trips no pin",
     {"test_the_pin_reach_the_ANCHORS_header_CLAIMS_is_the_reach_it_HAS"},
     '    every_source = [oa.Ask(source=s, text="an ask",',
     '    every_source = [oa.Ask(source=oa.SOURCE_SESSION, text="an ask",'),
    ("comment-skipped-anchor-removed",
     "the `comment-skipped` anchor matches nothing, so `render()`'s skip line "
     "is uncovered by the ledger",
     {"test_every_anchor_is_MATCHED_by_a_live_probe_line_and_carries_its_role",
      "test_every_render_KWARG_is_exercised_by_a_probe", RUNTIME},
     '    dict(id="comment-skipped", where="sources", role="informational-other",\n'
     '         text=" skipped — ",',
     '    dict(id="comment-skipped", where="sources", role="informational-other",\n'
     '         text="a shape no render emits",'),
    ("probe-stops-driving-comment-skips",
     "no probe drives `comment_skips`, so the pin is blind to the line that "
     "keyword emits — round 2's 🟡-4 restored",
     {"test_every_anchor_is_MATCHED_by_a_live_probe_line_and_carries_its_role",
      "test_every_render_KWARG_is_exercised_by_a_probe", RUNTIME},
     '            comment_skips={"written by someone else": 2},\n', ""),
    # ---- the delivery form (round 1) ------------------------------------
    ("lineno-blind-block-anchor",
     "the block anchor stops tolerating a `cat -n` prefix — round 1's 🔴, where "
     "13 of 20 post-cut reports read as pre-fix on the DOMINANT delivery path",
     {"test_EVERY_line_anchored_matcher_survives_the_numbered_form",
      "test_a_brief_delivered_as_a_LINE_NUMBERED_read_is_still_found",
      # The numbered region test locates the block through this very pattern, so
      # a lineno-blind anchor collects ZERO source lines there. A real killer,
      # observed rather than assumed — it was an EXTRA-KILLER on the first sweep.
      "test_the_numbered_sources_region_STOPS_where_the_unnumbered_one_does"},
     'ANCHOR_BLOCK_RE = re.compile("^" + _LINENO_PREFIX + re.escape(ANCHOR_BLOCK), re.M)',
     'ANCHOR_BLOCK_RE = re.compile("^" + re.escape(ANCHOR_BLOCK), re.M)'),
    ("lineno-blind-sources-anchor",
     "the Sources anchor goes lineno-blind, so a numbered brief has no "
     "locatable Sources block at all",
     {"test_EVERY_line_anchored_matcher_survives_the_numbered_form",
      "test_a_brief_delivered_as_a_LINE_NUMBERED_read_is_still_found",
      "test_the_numbered_sources_region_STOPS_where_the_unnumbered_one_does"},
     'ANCHOR_SOURCES_RE = re.compile("^" + _LINENO_PREFIX + re.escape(ANCHOR_SOURCES),',
     'ANCHOR_SOURCES_RE = re.compile("^" + re.escape(ANCHOR_SOURCES),'),
    ("lineno-blind-ask-heading",
     "the ask-heading pattern goes lineno-blind, so `answered` — the only role "
     "that proves reading — is unreachable on the dominant delivery path",
     {"test_EVERY_line_anchored_matcher_survives_the_numbered_form",
      "test_a_brief_delivered_as_a_LINE_NUMBERED_read_is_still_found"},
     'ASK_HEADING_RE = re.compile("^" + _LINENO_PREFIX + r"### from the .*$", re.M)',
     'ASK_HEADING_RE = re.compile("^" + r"### from the .*$", re.M)'),
    ("lineno-not-stripped-per-line",
     "the per-line number is left on, so the source region never ends and "
     "collects the `**Ledger:**` line and everything after it",
     {"test_the_numbered_sources_region_STOPS_where_the_unnumbered_one_does"},
     "        line = strip_lineno(raw)", "        line = raw"),
    ("blocklike-net-removed",
     "the safety net is gone, so a block delivered in a form this instrument "
     "cannot parse reads as a pre-fix report rather than as UNKNOWN",
     {"test_a_block_in_a_form_I_cannot_parse_is_UNKNOWN_not_a_pre_fix_report"},
     "            if _BLOCKLIKE_BLOCK_RE.search(txt):", "            if False:"),
    ("blocklike-net-too-wide",
     "the safety net becomes a bare substring, so a source READ of "
     "`operator_asks.py` routes to UNKNOWN — the false-positive class",
     {"test_EVERY_line_anchored_matcher_survives_the_numbered_form",
      "test_a_LINE_NUMBERED_source_read_is_still_NOT_a_block",
      "test_a_source_READ_of_operator_asks_is_not_an_asks_block"},
     '_BLOCKLIKE_BLOCK_RE = re.compile(\n    "^" + _LINENO_PREFIX + r"[ \\t>*+-]*" + re.escape(ANCHOR_BLOCK), re.M)',
     "_BLOCKLIKE_BLOCK_RE = re.compile(re.escape(ANCHOR_BLOCK))"),
    ("line-anchored-heading",
     "block detection becomes a substring test, re-admitting the very "
     "false-positive class line-anchoring was added for",
     {"test_a_LINE_NUMBERED_source_read_is_still_NOT_a_block",
      "test_a_block_in_a_form_I_cannot_parse_is_UNKNOWN_not_a_pre_fix_report",
      "test_a_source_READ_of_operator_asks_is_not_an_asks_block"},
     "        if not ANCHOR_BLOCK_RE.search(txt):",
     "        if ANCHOR_BLOCK not in txt:"),
    # ---- one mutation per remaining ANCHOR, so C4 is satisfied by real
    # mutations rather than by mentioning an id in a comment ----------------
    #
    # ⚠ NONE OF THESE FIVE ROWS NAMES `test_the_pin_reach_…`, and TWO of them did
    # — `anchor-asks-read-session-text-broken` and `-pr-comment-` below; the other
    # three never named it. MEASURED on those two, at `9e866492`: that test PASSES
    # under both, because breaking an anchor's `text` is neither of the claims it
    # makes (a probe per declared SOURCE; GROWS reaching every source line) — it is
    # the ledger disagreeing with the renderer, which is
    # `test_the_anchor_ledger_matches_what_operator_asks_emits_today`'s and
    # `test_every_anchor_is_MATCHED_by_a_live_probe_line…`'s subject. Both of those
    # DO fail on each, and the second on its OWN assertion (the first ends on the
    # run's `SystemExit: 5`, which is why the row needs the pair). A killer set
    # naming a test that cannot see the mutation is a dead pin, so it is removed
    # rather than the observation being relabelled.
    ("anchor-asks-read-session-text-broken",
     "the `answered` anchor's text matches nothing a live render emits, so the "
     "only line that proves the operator's words arrived stops being read",
     {"test_the_anchor_ledger_matches_what_operator_asks_emits_today",
      "test_every_anchor_is_MATCHED_by_a_live_probe_line_and_carries_its_role",
      RUNTIME},
     '    dict(id="asks-read-session", where="asks", role="answered",\n'
     '         text="### from the session transcript",',
     '    dict(id="asks-read-session", where="asks", role="answered",\n'
     '         text="### from a source no render names",'),
    ("anchor-asks-read-pr-comment-text-broken",
     "the PR-comment ask heading's anchor text matches nothing a live render "
     "emits, so adding a source could stop failing the GROWS direction",
     {"test_the_anchor_ledger_matches_what_operator_asks_emits_today",
      "test_every_anchor_is_MATCHED_by_a_live_probe_line_and_carries_its_role",
      RUNTIME},
     '    dict(id="asks-read-pr-comment", where="asks", role="other-source",\n'
     '         text="### from the PR comment",',
     '    dict(id="asks-read-pr-comment", where="asks", role="other-source",\n'
     '         text="### from a source no render names",'),
    ("anchor-dropped-text-broken",
     "the `dropped` anchor matches nothing, so the informational session line "
     "is uncovered and GROWS stops being meaningful",
     {"test_the_anchor_ledger_matches_what_operator_asks_emits_today",
      "test_every_anchor_is_MATCHED_by_a_live_probe_line_and_carries_its_role",
      "test_every_render_KWARG_is_exercised_by_a_probe", RUNTIME},
     '    dict(id="dropped", where="sources", role="informational",\n'
     '         text=": dropped ",',
     '    dict(id="dropped", where="sources", role="informational",\n'
     '         text=": a line no render emits ",'),
    ("anchor-comments-examined-text-broken",
     "the `comments-examined` anchor matches nothing, so the line that "
     "distinguishes 'consulted and empty' from 'not consulted' is uncovered",
     {"test_the_anchor_ledger_matches_what_operator_asks_emits_today",
      "test_every_anchor_is_MATCHED_by_a_live_probe_line_and_carries_its_role",
      "test_every_render_KWARG_is_exercised_by_a_probe", RUNTIME},
     '    dict(id="comments-examined", where="sources", role="informational-other",\n'
     '         text="comment(s) examined",',
     '    dict(id="comments-examined", where="sources", role="informational-other",\n'
     '         text="a line no render emits",'),
    ("anchor-review-comment-caveat-text-broken",
     "the review-comment caveat anchor matches nothing, so `render()`'s "
     "ISSUE-comments-only line is uncovered",
     {"test_the_anchor_ledger_matches_what_operator_asks_emits_today",
      "test_every_anchor_is_MATCHED_by_a_live_probe_line_and_carries_its_role",
      RUNTIME},
     '    dict(id="review-comment-caveat", where="sources", role="informational-other",\n'
     '         text="ISSUE comments ONLY",',
     '    dict(id="review-comment-caveat", where="sources", role="informational-other",\n'
     '         text="a line no render emits",'),
    # ---- provenance, clock, share, rows ---------------------------------
    ("provenance-separation",
     "the provenance filter is gone, so a skill body, a brief or a thinking "
     "block counts as a report — the ~2.1x over-count the unit exists to avoid",
     {"test_a_thinking_block_is_not_a_report",
      "test_the_same_ledger_line_in_an_assistant_block_and_a_tool_result_counts_ONCE"},
     '            if prov != "assistant":', "            if False:"),
    ("cut-clock-author-date",
     "the cut is read off the AUTHOR date rather than the committer date, so "
     "reports near a rebased landing instant are mis-bucketed",
     {"test_the_cut_is_read_off_the_COMMITTER_date_not_the_author_date",
      "test_the_run_NAMES_the_clock_its_cut_came_from"},
     'CUT_CLOCK = "%cI"', 'CUT_CLOCK = "%aI"'),
    ("undefined-share-fabricated-as-zero",
     "MULTI-SITE: the guard goes AND the `or 0` arrives, so an UNDEFINED share "
     "is fabricated as zero and a delta is printed over a value nobody has",
     {"test_an_UNDEFINED_share_refuses_instead_of_being_read_as_zero"},
     ('    if now["unattributed_share"] is None or base["unattributed_share"] is None:',
      '    d_share = now["unattributed_share"] - base["unattributed_share"]'),
     ("    if False:",
      '    d_share = ((now["unattributed_share"] or 0)\n'
      '               - (base["unattributed_share"] or 0))')),
    ("drop-contemporaneous-control-row",
     "the contemporaneous control row stops printing, so the comparator has no "
     "same-period control beside it",
     {"test_the_CONTEMPORANEOUS_control_row_is_printed_beside_the_comparator"},
     '        ("POST-cut out-of-population [control]", post_out, ""),\n', ""),
    ("static-pre-in-note",
     "the PRE in-population note becomes static, so it asserts the structural "
     "zero blind spot 7 records as FALSIFIED",
     {"test_the_PRE_in_population_note_describes_WHICHEVER_case_HOLDS"},
     '    pre_in_note = (\n        "0 as expected: the asks block did not exist before the cut"\n        if not pre_in else',
     '    pre_in_note = (\n        "0 as expected: the asks block did not exist before the cut"\n        if True else'),
    ("files-per-session-ratio-inverted",
     "the printed files-per-session ratio is inverted, so the figure that "
     "catches a wrong session derivation reads below 1 and looks plausible",
     {"test_the_printed_files_per_session_ratio_is_not_INVERTED"},
     "                 f\"{lbl}={(stats(rows)['files'] / stats(rows)['sessions']):.2f}x\"",
     "                 f\"{lbl}={(stats(rows)['sessions'] / stats(rows)['files']):.2f}x\""),
    ("legend-says-rc2-for-a-bad-corpus",
     "the exit-code legend stops naming the not-a-directory case, so rc 4's "
     "documented meaning no longer covers what the code does",
     {"test_an_unreadable_corpus_exits_4_AS_THE_LEGEND_SAYS"},
     "failed · 4 nothing walked: a corpus path that is not a directory, or no",
     "failed · 4 nothing walked: no"),
]


# --------------------------------------------------------------------------- #
def pytest_run(tree: Path) -> subprocess.CompletedProcess:
    for cache in (tree / "scripts").rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(tree / REL_TEST), "-q",
         "--no-header", "-p", "no:cacheprovider"],
        capture_output=True, text=True,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))


_SECTION = re.compile(r"^_+ (\S+?) _+$", re.M)
#: 🔴 `AssertionError` IS EXCLUDED, and its absence was a bug in this very
#: classifier: the first run scored `floor-counts-files-not-sessions` as
#: KILLED(raise) because pytest prints a real assertion as
#: `E AssertionError: assert ...` and "AssertionError" ends in "Error". A
#: quality label that mislabels the good case is worse than none.
_RAISED = re.compile(
    r"^E\s+(?!AssertionError)(\w*(?:Error|Exit|Exception|Interrupt))\b")

#: pytest names a parametrized case `test_x[9]`; the ledger names the test.
_PARAM = re.compile(r"\[.*\]$")


def per_failure(out: str) -> dict:
    """{test name: ('assert'|'raise', last E line)} from pytest's own sections.

    Per-TEST, not per-run: the whole-run version cannot tell "this guard's
    assertion fired" from "some other test asserted while mine raised".
    """
    parts = _SECTION.split(out)
    result = {}
    # 🔴 THE EXPLICIT `range` IS DELIBERATE — DO NOT "SIMPLIFY" IT INTO A PAIR OF
    # STEPPED SLICES. `re.split` with one capture group gives
    # `[pre, name1, body1, name2, body2, …]`, so zipping the odd-index slice
    # against the even-index one is the obvious spelling — and a stepped slice of
    # the form `<digit>::<digit>` is ALSO a valid compressed IPv6 literal that
    # clears the hextet floor in `scripts/tests/test_no_public_ips.py`, which then
    # fails this file for publishing a routable address. That gate is genuinely
    # defective (no other tracked `.py` in the repo slices that way, so it has
    # been green by luck) and is being fixed in a SEPARATE PR; this spelling
    # exists only so this battery does not trip it in the meantime.
    for i in range(1, len(parts) - 1, 2):
        raw_name, body = parts[i], parts[i + 1]
        name = _PARAM.sub("", raw_name)
        elines = [l for l in body.splitlines() if l.startswith("E ")]
        if not elines:
            continue
        last = elines[-1].strip()
        kind = "raise" if _RAISED.match(elines[-1]) else "assert"
        result[name] = (kind, last[:150])
    return result


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="mutants-round0-rate-"))
    tree = work / "tree"
    (tree / "scripts").mkdir(parents=True)
    shutil.copytree(REPO / "scripts", tree / "scripts", dirs_exist_ok=True)
    script = tree / REL_SCRIPT
    pristine = script.read_text()
    digest = hashlib.sha256(pristine.encode()).hexdigest()
    rc = 0
    try:
        # ---- C4: the list's own coverage, derived from the module -------
        anchor_ids = re.findall(r'dict\(id="([\w-]+)"', pristine)
        branches = re.findall(r'^    "([\w-]+)": \("(?:in|out)"', pristine, re.M)
        named = " ".join(f"{n} {old} {new}"
                         for n, _why, _k, old, new in MUTANTS)
        gaps = sorted({x for x in anchor_ids + branches if x not in named})
        say(f"C4 COVERAGE: {len(anchor_ids)} anchors + {len(branches)} branches "
              f"derived from the module; unnamed by any mutant: {gaps or 'none'}")
        if gaps:
            say("🔴 C4 FAILED — a hand-maintained list is blind to code added "
                  "in the same round; name these or the sweep is not a claim "
                  "about the file.")
            rc = 1

        # ---- C0: control ------------------------------------------------
        ctl = pytest_run(tree)
        say(f"C0 CONTROL: {ctl.stdout.strip().splitlines()[-1]}")
        if ctl.returncode != 0:
            say("🔴 C0 FAILED — the control is red, so no row below means "
                  "anything.")
            return 1

        say(f"\n{'verdict':22} {'mutant':40} killers / quality")
        killed = by_assert = 0
        for name, _why, expected, old_f, new_f in MUTANTS:
            # A multi-site row carries TUPLES of equal length; a single-site row
            # carries two strings. `zip` is safe here only because
            # `test_mutation_battery_anchors.py` refuses a row whose tuples
            # disagree in length — otherwise it would silently drop a site and
            # score the half-mutant under this row's name.
            pairs = (list(zip(old_f, new_f)) if isinstance(old_f, tuple)
                     else [(old_f, new_f)])
            counts = [pristine.count(old) for old, _new in pairs]
            if any(c != 1 for c in counts):
                say(f"{'🔴 INERT':22} {name:40} target occurs {counts}x")
                rc = 1
                continue
            mutated = pristine
            for old, new in pairs:
                mutated = mutated.replace(old, new)
            script.write_text(mutated)
            compiled = subprocess.run(
                [sys.executable, "-m", "py_compile", str(script)],
                capture_output=True, text=True)
            if compiled.returncode != 0:
                say(f"{'🔴 SYNTAX':22} {name:40} not a valid mutant")
                rc = 1
                script.write_text(pristine)
                continue
            r = pytest_run(tree)
            fails = per_failure(r.stdout)
            got = set(fails)
            if r.returncode == 0:
                say(f"{'🔴 SURVIVED':22} {name:40} nothing failed")
                rc = 1
            else:
                killed += 1
                want = {e for e in expected if e != RUNTIME}
                absent = sorted(want - got)
                extra = sorted(got - want)
                own = {t: fails[t] for t in want & got}
                quality = ("assert" if any(k == "assert" for k, _m in own.values())
                           else ("raise" if own else "none"))
                by_assert += 1 if quality == "assert" else 0
                tag = "KILLED"
                if absent:
                    tag = "🔴 WRONG-KILLER"
                    rc = 1
                elif quality != "assert":
                    tag = "🔴 KILLED(raise)"
                    rc = 1
                elif extra and RUNTIME not in expected:
                    tag = "⚠ EXTRA-KILLER"
                say(f"{tag:22} {name:40} "
                      f"{len(got)} failing; own={sorted(want & got)} "
                      f"quality={quality}")
                for t, (k, msg) in sorted(own.items()):
                    say(f"{'':22}   {t} [{k}] {msg}")
                if absent:
                    say(f"{'':22}   🔴 expected killer(s) ABSENT: {absent}")
                if extra and RUNTIME not in expected:
                    say(f"{'':22}   ⚠ extra: {extra[:6]}")
            script.write_text(pristine)
            after = hashlib.sha256(script.read_text().encode()).hexdigest()
            if after != digest:
                say(f"🔴 C3 RESTORE FAILED after {name}")
                return 1

        say(f"\n{killed} of {len(MUTANTS)} killed; {by_assert} died on their "
              "own guard's ASSERTION")
        say("C3 restore: byte-identical to pristine after every mutant")
        say("RESULT: " + ("PASS" if rc == 0 else "FAIL"))
        return rc
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
