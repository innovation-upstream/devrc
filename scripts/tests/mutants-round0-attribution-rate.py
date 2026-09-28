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

#: 🔴 (name, [(old, new), ...], {tests that MUST kill it})
#: A mutation is a LIST of substitutions because some defects are inherently
#: multi-site — the fabricated-share one was a missing guard AND an `or 0`.
#: `RUNTIME` in a killer set means "the runtime pin refuses, so every test that
#: calls `main()` fails"; those rows name their own guard plus that marker.
RUNTIME = "RUNTIME:the-pin-refuses-so-main-callers-fail"

MUTANTS: list[tuple[str, list[tuple[str, str]], set[str]]] = [
    # ---- the floor and its unit ------------------------------------------
    ("floor-value", [("MIN_SESSIONS = 10", "MIN_SESSIONS = 1")],
     {"test_MIN_SESSIONS_is_ten_and_that_is_a_standing_instruction",
      "test_below_MIN_SESSIONS_it_prints_NOT_MEASURABLE_and_exits_6",
      "test_the_floor_counts_DISTINCT_SESSIONS_not_ledger_lines",
      "test_the_floor_counts_REAL_SESSIONS_not_transcript_FILES"}),
    ("floor-refusal-branch",
     [("    if n_sessions < MIN_SESSIONS:", "    if n_sessions < 0:")],
     {"test_below_MIN_SESSIONS_it_prints_NOT_MEASURABLE_and_exits_6",
      "test_the_floor_counts_DISTINCT_SESSIONS_not_ledger_lines",
      "test_the_floor_counts_REAL_SESSIONS_not_transcript_FILES"}),
    ("floor-counts-files-not-sessions",
     [("    n_sessions = len({r.session_id for r in post_in})",
       "    n_sessions = len({r.file for r in post_in})")],
     {"test_the_floor_counts_REAL_SESSIONS_not_transcript_FILES"}),
    ("session-id-strip-removed",
     [('    return head[:-len(".jsonl")] if head.endswith(".jsonl") else head',
       "    return head")],
     {"test_the_floor_counts_REAL_SESSIONS_not_transcript_FILES",
      "test_the_naive_session_derivation_double_counts_and_ours_does_not",
      "test_the_printed_files_per_session_ratio_is_not_INVERTED"}),
    ("session-id-is-the-file-stem",
     [("    head = parts[1]", "    head = parts[-1]")],
     {"test_the_floor_counts_REAL_SESSIONS_not_transcript_FILES",
      "test_the_naive_session_derivation_double_counts_and_ours_does_not",
      "test_the_printed_files_per_session_ratio_is_not_INVERTED"}),
    # ---- precedence: the defect of rounds 0, 2 and 3 ---------------------
    ("precedence-selected-is-in-population",
     [('    "selected": ("out", "trailers NAMED a session but no ask from it was "',
       '    "selected": ("in", "trailers NAMED a session but no ask from it was "')],
     # ⚠ `…NAMED_a_session_but_could_read_NOTHING…` is deliberately NOT expected:
     # MEASURED, it still passes under this mutation, because that block also
     # carries `unmeasured`, which PRECEDES `selected` — which is the very
     # unreachability round 3 found. The battery caught this false expectation of
     # mine on its first run.
     {"test_only_the_answered_role_can_make_a_report_in_population",
      "test_a_block_that_NAMED_a_session_and_read_it_but_matched_NO_ask_is_out",
      RUNTIME}),
    ("precedence-selection-anchor-scores-answered",
     [('    dict(id="selected", where="sources", role="selected",',
       '    dict(id="selected", where="sources", role="answered",')],
     {"test_a_block_that_NAMED_a_session_but_could_read_NOTHING_is_NOT_in_population",
      "test_a_block_that_NAMED_a_session_and_read_it_but_matched_NO_ask_is_out",
      RUNTIME}),
    ("selected-branch-deleted",
     [('    "selected": ("out", "trailers NAMED a session but no ask from it was "\n'
       '                        "rendered — selection is not arrival"),\n', "")],
     {"test_a_block_that_NAMED_a_session_and_read_it_but_matched_NO_ask_is_out",
      "test_every_ANCHOR_and_ROLE_is_NAMED_by_this_module",
      "test_every_live_pole_classifies_correctly_today", RUNTIME}),
    ("none-consulted-branch-deleted",
     [('    "none-consulted": ("out", "the asks block says no source was consulted at "\n'
       '                              "all — a fact about that assembly, and no ask "\n'
       '                              "arrived either way"),\n', "")],
     {"test_a_block_that_says_NO_SOURCE_WAS_CONSULTED_is_out_not_UNKNOWN",
      "test_every_ANCHOR_and_ROLE_is_NAMED_by_this_module",
      "test_every_live_pole_classifies_correctly_today", RUNTIME}),
    ("block-scope-deleted-from-anchor",
     [('    dict(id="no-source-consulted", where="sources", role="none-consulted",\n'
       '         scope="block", text="no source was consulted at all",',
       '    dict(id="no-source-consulted", where="sources", role="none-consulted",\n'
       '         text="no source was consulted at all",')],
     {"test_a_block_that_says_NO_SOURCE_WAS_CONSULTED_is_out_not_UNKNOWN",
      RUNTIME}),
    ("unmeasured-anchor-made-block-scoped",
     [('    dict(id="unmeasured", where="sources", role="unmeasured",',
       '    dict(id="unmeasured", where="sources", role="unmeasured", scope="block",')],
     {"test_a_SESSION_scoped_anchor_is_not_read_off_another_sources_line"}),
    ("other-source-branch-deleted",
     [('    "other-source": ("out", "the only asks came from another source (a PR "\n'
       '                            "comment), not from a session transcript"),\n', "")],
     {"test_asks_that_arrived_only_from_a_PR_COMMENT_are_out_of_population",
      "test_every_ANCHOR_and_ROLE_is_NAMED_by_this_module",
      "test_every_live_pole_classifies_correctly_today", RUNTIME}),
    ("unknown-folded-into-in",
     [('    return "UNKNOWN", (\n        "an asks block is present but this instrument could not read it: "',
       '    return "in", (\n        "an asks block is present but this instrument could not read it: "')],
     {"test_UNKNOWN_is_excluded_from_the_rates_DENOMINATOR",
      "test_an_asks_block_with_an_unparseable_Sources_block_is_UNKNOWN"}),
    # ---- the two-way renderer pin ---------------------------------------
    ("pin-shrinks-half",
     [('    missing = sorted({a["id"] for a in ANCHORS} - seen_ids)',
       "    missing = []")],
     {"test_the_pin_goes_RED_when_THIS_modules_own_ledger_is_mutated",
      "test_the_pin_goes_RED_when_the_renderer_REWORDS_its_ASK_HEADING",
      "test_the_pin_goes_RED_when_the_renderer_REWORDS_its_selection_line"}),
    ("pin-grows-half",
     [("        unmatched += ask_bad + src_bad", "        unmatched += []")],
     {"test_the_pin_goes_RED_when_THIS_modules_own_ledger_is_mutated",
      "test_the_pin_goes_RED_when_the_renderer_GAINS_an_uncovered_source_line",
      "test_the_pin_goes_RED_when_the_renderer_REWORDS_its_selection_line"}),
    ("grows-only-session-lines",
     [('        _src_roles, src_ids, src_bad = match_anchors(sources, "sources")',
       '        _src_roles, src_ids, src_bad = match_anchors(\n'
       '            session_source_lines(oa.SOURCE_SESSION, sources), "sources")')],
     {"test_the_pin_reach_the_ANCHORS_header_CLAIMS_is_the_reach_it_HAS",
      RUNTIME}),
    ("probes-not-derived-from-source-order",
     [('    every_source = [oa.Ask(source=s, text="an ask",',
       '    every_source = [oa.Ask(source=oa.SOURCE_SESSION, text="an ask",')],
     {"test_the_pin_reach_the_ANCHORS_header_CLAIMS_is_the_reach_it_HAS"}),
    ("comment-skipped-anchor-removed",
     [('    dict(id="comment-skipped", where="sources", role="informational-other",\n'
       '         text=" skipped — ",',
       '    dict(id="comment-skipped", where="sources", role="informational-other",\n'
       '         text="a shape no render emits",')],
     {"test_every_anchor_is_MATCHED_by_a_live_probe_line_and_carries_its_role",
      "test_every_render_KWARG_is_exercised_by_a_probe", RUNTIME}),
    ("probe-stops-driving-comment-skips",
     [('            comment_skips={"written by someone else": 2},\n', "")],
     {"test_every_anchor_is_MATCHED_by_a_live_probe_line_and_carries_its_role",
      "test_every_render_KWARG_is_exercised_by_a_probe", RUNTIME}),
    # ---- the delivery form (round 1) ------------------------------------
    ("lineno-blind-block-anchor",
     [('ANCHOR_BLOCK_RE = re.compile("^" + _LINENO_PREFIX + re.escape(ANCHOR_BLOCK), re.M)',
       'ANCHOR_BLOCK_RE = re.compile("^" + re.escape(ANCHOR_BLOCK), re.M)')],
     {"test_EVERY_line_anchored_matcher_survives_the_numbered_form",
      "test_a_brief_delivered_as_a_LINE_NUMBERED_read_is_still_found"}),
    ("lineno-blind-sources-anchor",
     [('ANCHOR_SOURCES_RE = re.compile("^" + _LINENO_PREFIX + re.escape(ANCHOR_SOURCES),',
       'ANCHOR_SOURCES_RE = re.compile("^" + re.escape(ANCHOR_SOURCES),')],
     {"test_EVERY_line_anchored_matcher_survives_the_numbered_form",
      "test_a_brief_delivered_as_a_LINE_NUMBERED_read_is_still_found",
      "test_the_numbered_sources_region_STOPS_where_the_unnumbered_one_does"}),
    ("lineno-blind-ask-heading",
     [('ASK_HEADING_RE = re.compile("^" + _LINENO_PREFIX + r"### from the .*$", re.M)',
       'ASK_HEADING_RE = re.compile("^" + r"### from the .*$", re.M)')],
     {"test_EVERY_line_anchored_matcher_survives_the_numbered_form",
      "test_a_brief_delivered_as_a_LINE_NUMBERED_read_is_still_found"}),
    ("lineno-not-stripped-per-line",
     [("        line = strip_lineno(raw)", "        line = raw")],
     {"test_the_numbered_sources_region_STOPS_where_the_unnumbered_one_does"}),
    ("blocklike-net-removed",
     [("            if _BLOCKLIKE_BLOCK_RE.search(txt):", "            if False:")],
     {"test_a_block_in_a_form_I_cannot_parse_is_UNKNOWN_not_a_pre_fix_report"}),
    ("blocklike-net-too-wide",
     [('_BLOCKLIKE_BLOCK_RE = re.compile(\n    "^" + _LINENO_PREFIX + r"[ \\t>*+-]*" + re.escape(ANCHOR_BLOCK), re.M)',
       "_BLOCKLIKE_BLOCK_RE = re.compile(re.escape(ANCHOR_BLOCK))")],
     {"test_EVERY_line_anchored_matcher_survives_the_numbered_form",
      "test_a_LINE_NUMBERED_source_read_is_still_NOT_a_block",
      "test_a_source_READ_of_operator_asks_is_not_an_asks_block"}),
    ("line-anchored-heading",
     [("        if not ANCHOR_BLOCK_RE.search(txt):",
       "        if ANCHOR_BLOCK not in txt:")],
     {"test_a_LINE_NUMBERED_source_read_is_still_NOT_a_block",
      "test_a_block_in_a_form_I_cannot_parse_is_UNKNOWN_not_a_pre_fix_report",
      "test_a_source_READ_of_operator_asks_is_not_an_asks_block"}),
    # ---- one mutation per remaining ANCHOR, so C4 is satisfied by real
    # mutations rather than by mentioning an id in a comment ----------------
    ("anchor-asks-read-session-text-broken",
     [('    dict(id="asks-read-session", where="asks", role="answered",\n'
       '         text="### from the session transcript",',
       '    dict(id="asks-read-session", where="asks", role="answered",\n'
       '         text="### from a source no render names",')],
     {"test_the_anchor_ledger_matches_what_operator_asks_emits_today",
      "test_every_anchor_is_MATCHED_by_a_live_probe_line_and_carries_its_role",
      "test_the_pin_reach_the_ANCHORS_header_CLAIMS_is_the_reach_it_HAS",
      RUNTIME}),
    ("anchor-asks-read-pr-comment-text-broken",
     [('    dict(id="asks-read-pr-comment", where="asks", role="other-source",\n'
       '         text="### from the PR comment",',
       '    dict(id="asks-read-pr-comment", where="asks", role="other-source",\n'
       '         text="### from a source no render names",')],
     {"test_the_anchor_ledger_matches_what_operator_asks_emits_today",
      "test_every_anchor_is_MATCHED_by_a_live_probe_line_and_carries_its_role",
      "test_the_pin_reach_the_ANCHORS_header_CLAIMS_is_the_reach_it_HAS",
      RUNTIME}),
    ("anchor-dropped-text-broken",
     [('    dict(id="dropped", where="sources", role="informational",\n'
       '         text=": dropped ",',
       '    dict(id="dropped", where="sources", role="informational",\n'
       '         text=": a line no render emits ",')],
     {"test_the_anchor_ledger_matches_what_operator_asks_emits_today",
      "test_every_anchor_is_MATCHED_by_a_live_probe_line_and_carries_its_role",
      "test_every_render_KWARG_is_exercised_by_a_probe", RUNTIME}),
    ("anchor-comments-examined-text-broken",
     [('    dict(id="comments-examined", where="sources", role="informational-other",\n'
       '         text="comment(s) examined",',
       '    dict(id="comments-examined", where="sources", role="informational-other",\n'
       '         text="a line no render emits",')],
     {"test_the_anchor_ledger_matches_what_operator_asks_emits_today",
      "test_every_anchor_is_MATCHED_by_a_live_probe_line_and_carries_its_role",
      "test_every_render_KWARG_is_exercised_by_a_probe", RUNTIME}),
    ("anchor-review-comment-caveat-text-broken",
     [('    dict(id="review-comment-caveat", where="sources", role="informational-other",\n'
       '         text="ISSUE comments ONLY",',
       '    dict(id="review-comment-caveat", where="sources", role="informational-other",\n'
       '         text="a line no render emits",')],
     {"test_the_anchor_ledger_matches_what_operator_asks_emits_today",
      "test_every_anchor_is_MATCHED_by_a_live_probe_line_and_carries_its_role",
      RUNTIME}),
    # ---- provenance, clock, share, rows ---------------------------------
    ("provenance-separation",
     [('            if prov != "assistant":', "            if False:")],
     {"test_a_thinking_block_is_not_a_report",
      "test_the_same_ledger_line_in_an_assistant_block_and_a_tool_result_counts_ONCE"}),
    ("cut-clock-author-date", [('CUT_CLOCK = "%cI"', 'CUT_CLOCK = "%aI"')],
     {"test_the_cut_is_read_off_the_COMMITTER_date_not_the_author_date",
      "test_the_run_NAMES_the_clock_its_cut_came_from"}),
    ("undefined-share-fabricated-as-zero",
     [('    if now["unattributed_share"] is None or base["unattributed_share"] is None:',
       "    if False:"),
      ('    d_share = now["unattributed_share"] - base["unattributed_share"]',
       '    d_share = ((now["unattributed_share"] or 0)\n'
       '               - (base["unattributed_share"] or 0))')],
     {"test_an_UNDEFINED_share_refuses_instead_of_being_read_as_zero"}),
    ("drop-contemporaneous-control-row",
     [('        ("POST-cut out-of-population [control]", post_out, ""),\n', "")],
     {"test_the_CONTEMPORANEOUS_control_row_is_printed_beside_the_comparator"}),
    ("static-pre-in-note",
     [('    pre_in_note = (\n        "0 as expected: the asks block did not exist before the cut"\n        if not pre_in else',
       '    pre_in_note = (\n        "0 as expected: the asks block did not exist before the cut"\n        if True else')],
     {"test_the_PRE_in_population_note_describes_WHICHEVER_case_HOLDS"}),
    ("files-per-session-ratio-inverted",
     [("                 f\"{lbl}={(stats(rows)['files'] / stats(rows)['sessions']):.2f}x\"",
       "                 f\"{lbl}={(stats(rows)['sessions'] / stats(rows)['files']):.2f}x\"")],
     {"test_the_printed_files_per_session_ratio_is_not_INVERTED"}),
    ("legend-says-rc2-for-a-bad-corpus",
     [("failed · 4 nothing walked: a corpus path that is not a directory, or no",
       "failed · 4 nothing walked: no")],
     {"test_an_unreadable_corpus_exits_4_AS_THE_LEGEND_SAYS"}),
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
    for raw_name, body in zip(parts[1::2], parts[2::2]):
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
        named = " ".join(n + " " + str(p) for n, p, _k in MUTANTS)
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
        for name, pairs, expected in MUTANTS:
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
