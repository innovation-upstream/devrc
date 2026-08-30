"""Two-way ratchet on POSITIONAL spool reads in `scripts/browser-bridge/tests`.

WHY THIS EXISTS
---------------
`scripts/browser-bridge/tests` reads a shared activity spool. `ACTIVITY_SPOOL_DIR`
is a **process-global** env var that `conftest._isolate_activity_spool` re-points
per test, so a thread still alive from an EARLIER test emits into the CURRENT
test's spool. A reader that indexes by POSITION -- `_wait_events(spool_dir, 1)[0]`
-- therefore returns whichever row landed first, not whichever row this test
caused. Seen in CI as `assert 'getHtml' == 'frames'` (#773, a change to
`scripts/run-tests.sh`) and `assert 'getHtml' == 'type'` (#770, a change to one
`.md` file) -- two diffs that cannot reach browser-bridge at all.

The emitter-side leak is a **DECIDED no-fix** (#783; see
`claudedocs/handoff-ci-flakes-and-misattribution.md`). Joining or quiescing
server threads at teardown was tried and REJECTED on the merits -- it fights the
deliberate off-critical-path design and would have to be repeated at every call
site. There are ELEVEN unjoined-daemon-thread tests, not one, so "just fix the
leaking test" is not available either. **DO NOT re-open the leak as a global
fix, and do not treat this module as a step toward one.**

The containment is entirely on the READER side: a reader that DISCRIMINATES
cannot be fooled by a foreign row. Three safe forms exist --
`_wait_events(..., until=<predicate>)`, `_wait_ops(..., where=<predicate>)`
(added in #891), and op-selection via `_wait_ops` / `_wait_payload`. The unsafe
form is `_wait_events(spool_dir, N)` indexed by position.

WHAT THIS MODULE ADDS AND WHY A RATCHET RATHER THAN A BAN
----------------------------------------------------------
The agreed posture is INCREMENTAL: convert a reader to `until=` / `where=` when
a site is actually bitten, paid per-site and independently verifiable. A ban
would demand 48 conversions nobody asked for; a one-way "no new sites" gate
would let a win evaporate silently.

But the migration was running BACKWARDS and nothing detected it. Measured by AST:

    2d4b2980     39 positional `_wait_events` sites (of which 39 n=1, 0 n>=2 --
                 the contemporaneous note recorded `53 total = 39 n=1 + 5 until=
                 + 9 n>=2`, i.e. 48 positional under this module's rule)
    6068ac51     48 positional
    20beb3c4     48 positional   <- the sha this module's constants were pinned at

So: a two-way ratchet. It fails when the number GROWS (someone wrote a new
positional site) **and** when it SHRINKS below the pin (so a win is locked in and
the constant is updated in the same commit, instead of quietly regrowing later).

WHERE THIS LIVES AND WHY
------------------------
`scripts/tests`, not `scripts/browser-bridge/tests`, even though it measures the
latter. Both directories are in `HERMETIC_TARGETS` in `scripts/run-tests.sh`, so
either placement runs in `nix build .#checks.x86_64-linux.pytests` -- the real
pre-merge gate. The choice is about COST and BLAST RADIUS, not coverage:

  * This guard is a pure `ast.parse` over one directory: milliseconds, no
    network, no subprocess, no server. The browser-bridge target costs ~234s and
    needs `curl`/`setsid`/a live loopback server.
  * `scripts/browser-bridge/tests/test_browser_agent.py` has a KNOWN intermittent
    hang at its shared subprocess net (a different test each run; see the handoff
    doc's "SECOND flake family"). A ratchet living in that directory would share
    that directory's run budget, so its verdict could be lost to a flake it has
    nothing to do with. Here it cannot be.
  * The repo's other ratchets -- `test_rules_size.py`, `test_skill_tiers.py`,
    `test_skill_descriptions.py` -- all live in `scripts/tests` and all measure
    files elsewhere in the repo. This follows that convention.

It reads the source with `ast.parse` and resolves paths from `__file__`. It
touches **git not at all**, so it behaves identically on the dev host and in the
`nix build` sandbox, which is a `cp -r` store copy with NO `.git`.

HOW THE COUNT IS DERIVED -- AST, NEVER GREP
--------------------------------------------
🔴 Grep is WRONG here for a documented reason, and this is not a style
preference: a line-oriented regex for the count reads the POSITIONAL form
(`_wait_events(d, 2)`) and MISSES the keyword one (`_wait_events(d, n=2)`), so
sites land in the wrong bucket while the total still adds up. Counting methods
have already disagreed on this corpus -- `4` by AST against `7` by the earlier
hand count, for `_wait_ops`.

So `classify_wait_calls()` below walks the AST of every `*.py` under the target
directory and buckets each `ast.Call`. The bucketing rule, stated because it is a
judgement call and was got wrong once already: **bucket by what the call ASKS
FOR**, and **no self-test exemption** -- `_wait_events`' own control tests (e.g.
`test_wait_events_reports_a_timeout_instead_of_returning_short`) are classified
like every other site. Exempting one of a pair and not the other is what produced
the wrong number last time.
"""
from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET_DIR = REPO_ROOT / "scripts" / "browser-bridge" / "tests"

#: The helper names this module recognises. A call whose callee spells none of
#: these is invisible to the counter -- see BLIND SPOTS in
#: `classify_wait_calls`'s docstring.
HELPERS = ("_wait_events", "_wait_ops", "_wait_payload")

#: The bucket whose size is ratcheted: a `_wait_events` call with NO `until=`
#: keyword, i.e. one that asks for a COUNT and then reads rows by position.
POSITIONAL_BUCKET = "_wait_events positional (no until=)"


# --------------------------------------------------------------------------
# THE PIN
# --------------------------------------------------------------------------
# 🔴 Measured by THIS module's own `classify_wait_calls()` -- never by grep, and
# never by hand -- over `scripts/browser-bridge/tests` at:
#
#     BASE SHA   this branch (PR #1074), the commit that lowers the pin to 47.
#                Deliberately not a hex sha: the measurement is OF the commit
#                doing the lowering, so any sha written here would be the one
#                before the change it describes.
#     DATE       2026-08-30
#
# Full breakdown at that commit, re-measured by `classify_wait_calls()` (never by
# hand), all six buckets, 62 call sites total:
#
#     47  _wait_events positional (no until=)   <- RATCHETED
#              38  of which n literal 1 / defaulted
#               9  of which n >= 2
#               0  of which n dynamic
#      5  _wait_events until=                        SAFE
#      4  _wait_ops op-only                          discriminated by op
#      3  _wait_ops where=                           discriminated by op + row
#      3  _wait_payload op-only                      discriminated by op
#
# ⚠ The dynamic sub-bucket went 1 -> 0 and `_wait_ops where=` 2 -> 3: the SAME
# site moved between them. `_wait_events(spool_dir, len(ORIGIN_TOKENS))` in
# `test_the_two_origin_tokens_are_distinct_and_recorded_verbatim` became
# `_wait_ops(spool_dir, "tabs", …, where=_routed_to(inst))` after it flaked in
# the sandbox tier (2026-08-29, both origin tokens verbatim but REVERSED).
#
# 🔴 AND THE SUB-SPLIT EARNED ITS KEEP -- read this before calling it
# informational. The `_wait_events` docstring audited its own n>=2 sites and
# concluded "All 8 remaining real waits are order-safe". That audit could not
# see the site that actually flaked, because its own counting (`39 n=1 + 5
# until= + 9 n>=2`) folded the DYNAMIC site into n=1 -- exactly the first-pass
# error recorded here, where a NON-LITERAL `n` reads as the default 1. A site
# whose `n` is `len(...)` is an n>=2 site in every way that matters to ordering,
# and bucketing it as n=1 is what let an order-dependent assertion sit outside
# an order-safety audit for four days. The two methods still agree on the
# RATCHETED total; they disagreed about which bucket, and the bucket was the
# part that mattered.
#
# ⚠ The `_wait_ops op-only` figure of 4 includes ONE call that is not a test
# site: `_wait_payload`'s own body calls `_wait_ops(spool_dir, op, 1, **kw)`.
# That is deliberate -- the counter walks every call in every file with no
# exemption for helper bodies, and the handoff doc's AST figure counts it the
# same way. No positional `_wait_events` call lives inside a helper body, so the
# RATCHETED number is unaffected either way.
#
# The two constants below are pinned SEPARATELY and cross-checked against each
# other by `test_the_ledger_and_the_headline_total_agree`. That is not
# redundancy: the ledger is what names WHICH site moved, the headline is what the
# handoff doc and PR bodies quote, and a guard that they agree is what stops the
# two drifting into a state where the failure message names the wrong thing.

#: How many positional `_wait_events` call sites the corpus is allowed to have.
#: Two-way: growing this is a REGRESSION, shrinking it is a WIN that must be
#: banked by editing this number and the ledger in the same commit.
PINNED_POSITIONAL_TOTAL = 47

#: Per-enclosing-function ledger of the same 48 sites, keyed
#: `<file>::<dotted function path>`. Line numbers are deliberately NOT pinned --
#: they churn on every unrelated edit above them, which would make this a
#: permanently-red gate. Function names are stable and are what a failure needs
#: to name. `<module>` is used for a call at module scope.
PINNED_POSITIONAL_SITES = {
    "test_server.py::test_a_diagnostic_get_from_a_nested_run_is_not_credited": 1,
    "test_server.py::test_a_joinable_parent_is_still_recorded": 1,
    "test_server.py::test_a_neighbours_late_row_does_not_become_this_tests_event": 1,
    "test_server.py::test_a_nested_run_records_the_forwarded_id_as_origin_not_as_session": 1,
    "test_server.py::test_a_tag_outside_the_vocabulary_is_normalised_not_recorded": 1,
    "test_server.py::test_activate_i3_telemetry_stays_metadata_only": 1,
    "test_server.py::test_activate_telemetry_is_metadata_only": 1,
    "test_server.py::test_activate_telemetry_records_the_consent_decision": 2,
    "test_server.py::test_an_absent_or_empty_id_fails_closed": 1,
    "test_server.py::test_an_ordinary_request_sets_session_and_declares_no_origin": 1,
    "test_server.py::test_an_origin_wins_over_the_joinable_tier_on_every_emit_site": 2,
    "test_server.py::test_an_oversized_id_is_dropped_whole_never_truncated": 2,
    "test_server.py::test_an_unreadable_origin_still_suppresses_the_session": 1,
    "test_server.py::test_an_untagged_id_is_never_promoted_to_a_join_key": 1,
    "test_server.py::test_both_join_sites_answer_the_same_way_for_every_joinable_tier": 2,
    "test_server.py::test_cmd_ambiguous_emits_ambiguous_outcome": 1,
    "test_server.py::test_cmd_emits_routing_key_from_target": 1,
    "test_server.py::test_cmd_no_extension_emits_error_outcome": 1,
    "test_server.py::test_cmd_ok_emits_one_metadata_event": 1,
    "test_server.py::test_cmd_ok_no_url_uses_op_as_text_and_omits_domain": 1,
    "test_server.py::test_cmd_rate_limited_returns_429_and_emits_throttle": 1,
    "test_server.py::test_cmd_timeout_emits_timeout_outcome": 1,
    "test_server.py::test_emit_heartbeat_survives_a_broken_registry": 1,
    "test_server.py::test_emitted_event_is_metadata_only_no_page_content": 1,
    "test_server.py::test_emulate_telemetry_is_metadata_only": 2,
    "test_server.py::test_every_origin_token_suppresses_the_session_key": 1,
    "test_server.py::test_heartbeat_emits_a_metadata_only_liveness_event": 1,
    "test_server.py::test_heartbeat_reports_a_disconnected_extension": 1,
    "test_server.py::test_heartbeat_tracks_registry_liveness_across_the_stale_boundary": 2,
    "test_server.py::test_only_the_first_colon_splits_a_multi_colon_id": 1,
    "test_server.py::test_orientation_ops_emit_exactly_one_metadata_only_event": 1,
    "test_server.py::test_origin_session_passes_the_same_tier_gate_as_session": 1,
    "test_server.py::test_session_column_is_filled_for_the_joinable_tiers_only": 1,
    "test_server.py::test_text_telemetry_is_metadata_only": 1,
    "test_server.py::test_the_diagnostic_gets_are_attributed_like_any_operator_command": 1,
    "test_server.py::test_the_release_short_circuit_attributes_an_ordinary_session": 1,
    "test_server.py::test_the_same_id_fills_the_session_when_no_origin_is_declared": 1,
    "test_server.py::test_the_session_column_carries_the_bare_id_not_the_wire_tag": 1,
    "test_server.py::test_upload_dispatches_and_audit_event_has_op_domain_path": 1,
    "test_server.py::test_wait_events_reports_a_timeout_instead_of_returning_short": 1,
    "test_server.py::test_wake_telemetry_is_metadata_only": 1,
}

#: Quoted verbatim in every failure message. The remedy is per-site and
#: incremental -- see the "WHY A RATCHET RATHER THAN A BAN" section above.
REMEDY = """\
REMEDY -- convert the site, do not raise the pin:

  * Waiting for a SPECIFIC event?
        _wait_events(spool_dir, until=lambda evs: <predicate over evs>)
  * Want rows of a specific OP?
        _wait_ops(spool_dir, "<op>", n)
  * Unpacking N rows of a COMMON op positionally (`a, b = ...`)?
        _wait_ops(spool_dir, "<op>", n, where=_routed_to(<instance id>))
    Bare `op` is NOT enough there: a neighbour emitting the SAME op still
    lands between your rows and swaps your assertions onto the wrong ones.

Read `_wait_ops`' docstring in scripts/browser-bridge/tests/test_server.py for
the idiom, for why the extra discriminator is the ROUTING KEY, and for the
requirement that a `where=` predicate be TOTAL (walk past a malformed row,
never raise -- build it out of `_payload_field`).

🔴 Do NOT "fix" this by joining or quiescing server threads at teardown. That is
the design REJECTED in #783; see claudedocs/handoff-ci-flakes-and-misattribution.md.
"""


class _WaitCallVisitor(ast.NodeVisitor):
    """Walks one module, recording every call to a name in `HELPERS`."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.stack: list[str] = []
        self.calls: list[dict] = []

    def visit_FunctionDef(self, node):  # noqa: N802
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_Call(self, node):  # noqa: N802
        name = _callee_name(node.func)
        if name in HELPERS:
            self.calls.append({
                "helper": name,
                "file": self.filename,
                "lineno": node.lineno,
                "where": "%s::%s" % (
                    self.filename, ".".join(self.stack) or "<module>"),
                "keywords": {k.arg for k in node.keywords if k.arg is not None},
                "n": _literal_n(node),
            })
        # Keep descending: a helper call can appear inside another call's args.
        self.generic_visit(node)


def _callee_name(func: ast.expr) -> str | None:
    """The spelled name of a callee: `f(...)` -> "f", `m.f(...)` -> "f"."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _literal_n(node: ast.Call):
    """The literal value of `_wait_events`' `n`, or None if it is not a literal.

    `n` is the SECOND positional parameter (`_wait_events(spool_dir, n=1, ...)`),
    so it is `args[1]` positionally or the `n=` keyword. Absent entirely means
    the default, 1. A non-literal (a variable, an expression) yields None, which
    the classifier reports as `n dynamic` -- still POSITIONAL, because what
    matters for the ratchet is the absence of `until=`, not the value of `n`.

    ⚠ SCOPE OF THIS FUNCTION, narrower than where it is called: it is evaluated
    for EVERY helper call, but only `_wait_events` has an `n` in that position.
    For `_wait_ops(d, "tabs", 1)` the second positional is the OP, a string, so
    this returns None -- harmless, because `n` is only ever READ for calls in the
    positional `_wait_events` bucket (see `_breakdown`). Nothing gates on it
    either way; the ratchet gates on the bucket, and the sub-split is printed.
    """
    if len(node.args) >= 2:
        value = node.args[1]
    else:
        value = next(
            (k.value for k in node.keywords if k.arg == "n"), None)
    if value is None:
        return 1  # defaulted
    if isinstance(value, ast.Constant) and isinstance(value.value, int):
        return value.value
    return None


def classify_wait_calls(directory: Path) -> list[dict]:
    """Every call to `_wait_events` / `_wait_ops` / `_wait_payload` under `directory`.

    WHAT THIS COUNTS, EXACTLY -- the sentence and the implementation are meant to
    be the same width, so read this as the specification:

    It `ast.parse`s every `*.py` file found by `directory.rglob("*.py")` and
    returns one record per `ast.Call` node whose callee is SPELLED `_wait_events`,
    `_wait_ops` or `_wait_payload` -- either as a bare `Name` or as the final
    `.attr` of an `Attribute`. Each record carries a `bucket`:

      `_wait_events positional (no until=)`  -- no `until=` keyword present
      `_wait_events until=`                 -- an `until=` keyword IS present
      `_wait_ops where=` / `_wait_ops op-only`
      `_wait_payload where=` / `_wait_payload op-only`

    Calls inside helper bodies are counted like any other, and `_wait_events`'
    own control tests get no exemption.

    🔴 BLIND SPOTS -- things this CANNOT see, stated so nobody reads a green run
    as wider than it is:

      1. ALIASED CALLS. Matching is on the SPELLED name at the call site, with no
         name resolution. `w = _wait_events; w(spool_dir, 1)` is invisible, and so
         is `from ... import _wait_events as we`.
      2. INDIRECTION THROUGH A WRAPPER. A local helper that itself calls
         `_wait_events(d, 1)` is counted ONCE, at the wrapper's own line -- not
         once per test that calls the wrapper. The exposure is per-execution; this
         number is per-source-site.
      3. SCOPE. `*.py` under this one directory only. A positional read written in
         a sibling `.test.mjs`, or anywhere else in the repo, is not seen.
      4. `until=` / `where=` PRESENCE, NOT MEANING. Detection is the presence of
         the KEYWORD ARGUMENT NAME. `until=lambda evs: True` is classified SAFE
         even though it discriminates nothing. Conversely a `**kwargs` splat that
         happens to carry `until` is classified POSITIONAL, since the name is not
         visible at the call site -- deliberately the conservative direction.
      5. DYNAMIC DISPATCH. `getattr(mod, "_wait_events")(...)`, `eval`, or a name
         built at runtime.
      6. A FILE THAT DOES NOT PARSE raises `SyntaxError` out of here rather than
         being skipped. A corpus this cannot parse is a corpus it cannot vouch
         for, and silently returning a smaller number is exactly the failure the
         shrink arm exists to catch.
    """
    records: list[dict] = []
    for path in sorted(directory.rglob("*.py")):
        visitor = _WaitCallVisitor(path.name)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), str(path)))
        for call in visitor.calls:
            # Repo-relative when the file is in the repo (readable in a failure
            # message), absolute otherwise -- the synthetic-source controls below
            # parse a tmp_path that is not under REPO_ROOT.
            call["path"] = str(
                path.relative_to(REPO_ROOT)
                if path.is_relative_to(REPO_ROOT) else path)
            call["bucket"] = _bucket(call)
            records.append(call)
    records.sort(key=lambda c: (c["path"], c["lineno"]))
    return records


def _bucket(call: dict) -> str:
    helper, kws = call["helper"], call["keywords"]
    if helper == "_wait_events":
        return "_wait_events until=" if "until" in kws else POSITIONAL_BUCKET
    return "%s %s" % (helper, "where=" if "where" in kws else "op-only")


def _positional(records: list[dict]) -> list[dict]:
    return [r for r in records if r["bucket"] == POSITIONAL_BUCKET]


def _breakdown(records: list[dict]) -> str:
    counts = Counter(r["bucket"] for r in records)
    sub = Counter(
        ("n literal 1 / defaulted" if r["n"] == 1
         else "n dynamic" if r["n"] is None else "n >= 2")
        for r in _positional(records))
    lines = ["CURRENT BREAKDOWN (AST over %s, %d call sites):"
             % (TARGET_DIR.relative_to(REPO_ROOT), len(records))]
    for bucket, count in sorted(counts.items()):
        lines.append("    %3d  %s" % (count, bucket))
        if bucket == POSITIONAL_BUCKET:
            for label, n in sorted(sub.items()):
                lines.append("             %3d  of which %s" % (n, label))
    return "\n".join(lines)


def _replacement_block(records: list[dict]) -> str:
    """The exact text to paste over the pinned constants. Modelled on
    `run-tests.sh`'s `TARGET_FLOORS`, which prints its own replacement number
    rather than making the reader do arithmetic across a conflict."""
    ledger = Counter(r["where"] for r in _positional(records))
    lines = ["PASTE THIS OVER THE CONSTANTS IN %s:" % Path(__file__).name,
             "",
             "    PINNED_POSITIONAL_TOTAL = %d" % len(_positional(records)),
             "",
             "    PINNED_POSITIONAL_SITES = {"]
    for key in sorted(ledger):
        lines.append('        "%s": %d,' % (key, ledger[key]))
    lines.append("    }")
    lines.append("")
    lines.append("...and update the BASE SHA in the pin comment to the sha you "
                 "measured at.")
    return "\n".join(lines)


def _site_lines(records: list[dict], where: str) -> str:
    return ", ".join(
        "%s:%d" % (r["path"], r["lineno"])
        for r in _positional(records) if r["where"] == where) or "(none)"


def _delta(records: list[dict]) -> tuple[list[str], list[str]]:
    """(added, removed) as human lines, comparing the ledger to the pin."""
    current = Counter(r["where"] for r in _positional(records))
    added, removed = [], []
    for key in sorted(set(current) | set(PINNED_POSITIONAL_SITES)):
        now = current.get(key, 0)
        pinned = PINNED_POSITIONAL_SITES.get(key, 0)
        if now > pinned:
            added.append("    +%d  %s   at %s"
                         % (now - pinned, key, _site_lines(records, key)))
        elif now < pinned:
            removed.append("    -%d  %s   (pinned %d, now %d)"
                           % (pinned - now, key, pinned, now))
    return added, removed


def test_the_positional_reader_count_has_not_grown():
    """GROW ARM: no NEW `_wait_events(spool_dir, N)` positional site.

    Fails when the corpus contains more positional sites than `PINNED_POSITIONAL_
    TOTAL`, and names the enclosing test functions that gained one, with file:line.
    """
    records = classify_wait_calls(TARGET_DIR)
    current = len(_positional(records))
    if current <= PINNED_POSITIONAL_TOTAL:
        return
    added, _ = _delta(records)
    raise AssertionError("\n".join([
        "",
        "POSITIONAL SPOOL READERS GREW: %d  (pinned %d, +%d)"
        % (current, PINNED_POSITIONAL_TOTAL, current - PINNED_POSITIONAL_TOTAL),
        "",
        "A `_wait_events(spool_dir, N)` read indexed by POSITION assumes every row",
        "in the spool is yours. It is not: ACTIVITY_SPOOL_DIR is process-global and",
        "re-pointed per test, so a thread alive from an EARLIER test emits into",
        "yours. This is the shape that reddened #770 and #773 -- two PRs that could",
        "not reach browser-bridge at all.",
        "",
        "NEW SITES (enclosing test function, then file:line):",
        *(added or ["    (count grew but no function gained -- see the breakdown)"]),
        "",
        _breakdown(records),
        "",
        REMEDY,
        "If the new site is genuinely order-safe and you are RAISING the pin",
        "deliberately, say in the commit message WHY position is safe there.",
        "",
        _replacement_block(records),
    ]))


def test_the_positional_reader_count_has_not_shrunk_below_the_pin():
    """SHRINK ARM: a win must be BANKED in the same commit, not left to regrow.

    Fails when the corpus contains FEWER positional sites than the pin. This is
    the good direction -- the fix is to lower the constant, not to undo the work.

    It doubles as the counter's own positive control in the gate: a classifier
    that silently stopped matching (a renamed helper, a moved directory, an
    `ast` change) returns 0 and trips this arm loudly, instead of reporting a
    reassuring "no growth".
    """
    records = classify_wait_calls(TARGET_DIR)
    current = len(_positional(records))
    if current >= PINNED_POSITIONAL_TOTAL:
        return
    _, removed = _delta(records)
    raise AssertionError("\n".join([
        "",
        "POSITIONAL SPOOL READERS SHRANK: %d  (pinned %d, -%d)"
        % (current, PINNED_POSITIONAL_TOTAL, PINNED_POSITIONAL_TOTAL - current),
        "",
        "This is a WIN and the ratchet is asking you to BANK it -- lower the pin",
        "in this file, in the SAME commit, so the ground cannot be lost again.",
        "Leaving the pin high means the next positional site added is invisible.",
        "",
        "SITES THAT WENT AWAY (enclosing test function):",
        *(removed or ["    (count fell but no function lost one -- see below)"]),
        "",
        "⚠ IF YOU DID NOT TOUCH THESE TESTS, DO NOT LOWER THE PIN. A drop you did",
        "not cause means the COUNTER stopped seeing sites -- a renamed helper, a",
        "moved directory, an aliased import (blind spot 1 in `classify_wait_calls`)",
        "-- and lowering the pin would bank a measurement error as progress.",
        "Check the breakdown adds up before believing the number:",
        "",
        _breakdown(records),
        "",
        _replacement_block(records),
    ]))


def test_the_ledger_and_the_headline_total_agree():
    """The two pinned constants must describe the same corpus.

    `PINNED_POSITIONAL_TOTAL` is what the handoff doc and PR bodies quote;
    `PINNED_POSITIONAL_SITES` is what a failure message uses to name WHICH site
    moved. If they drift, both ratchet arms still fire on the right condition but
    report the wrong sites -- a guard that reads as coverage while misdirecting.
    """
    ledger_total = sum(PINNED_POSITIONAL_SITES.values())
    assert ledger_total == PINNED_POSITIONAL_TOTAL, (
        "\nPINNED_POSITIONAL_SITES sums to %d but PINNED_POSITIONAL_TOTAL is %d."
        "\nThey are pinned separately on purpose; update BOTH from the block a"
        "\nfailing ratchet arm prints, never one by hand."
        % (ledger_total, PINNED_POSITIONAL_TOTAL))


def test_the_pinned_ledger_names_only_functions_that_exist():
    """A ledger entry naming no function is as stale as a missing one.

    Two-way, like `test_skill_tiers.py`'s: the grow/shrink arms compare totals, so
    a RENAMED test would net to zero (one key gone, one key new) and slip past
    both. This is what catches that.
    """
    records = classify_wait_calls(TARGET_DIR)
    current = set(Counter(r["where"] for r in _positional(records)))
    pinned = set(PINNED_POSITIONAL_SITES)
    unknown = sorted(pinned - current)
    unpinned = sorted(current - pinned)
    if not unknown and not unpinned:
        return
    raise AssertionError("\n".join([
        "",
        "THE PINNED LEDGER AND THE CORPUS NAME DIFFERENT FUNCTIONS.",
        "",
        "PINNED but holding no positional site now (renamed, deleted, or"
        " converted):",
        *(["    %s" % k for k in unknown] or ["    (none)"]),
        "",
        "HOLDING a positional site but not in the ledger (new or renamed):",
        *(["    %s   at %s" % (k, _site_lines(records, k)) for k in unpinned]
          or ["    (none)"]),
        "",
        "A pure RENAME nets to zero on the count, so the two arms above cannot"
        " see it.",
        "That is why this test exists. If it is a rename, re-paste the ledger.",
        "",
        REMEDY,
        "",
        _replacement_block(records),
    ]))


# --------------------------------------------------------------------------
# CONTROLS ON THE INSTRUMENT ITSELF
#
# 🔴 A zero from this classifier is indistinguishable from a classifier wired to
# nothing. The shrink arm is one positive control (it fires loudly on 0 against
# the real corpus), but it only proves the count moved -- not that each BUCKET is
# reachable. These feed synthetic source through the same code path and watch
# each bucket produce a non-zero count.
# --------------------------------------------------------------------------

_SYNTHETIC = '''
def test_positional_default():
    _wait_events(d)

def test_positional_two_positional_args():
    _wait_events(d, 2)

def test_positional_keyword_n():
    _wait_events(d, n=2)

def test_positional_dynamic_n():
    _wait_events(d, how_many)

def test_safe_until():
    _wait_events(d, until=lambda evs: len(evs) > 0)

def test_safe_until_with_n():
    _wait_events(d, n=3, until=lambda evs: evs)

def test_op_only():
    _wait_ops(d, "tabs", 1)

def test_op_where():
    _wait_ops(d, "tabs", 2, where=_routed_to(inst))

def test_payload():
    _wait_payload(d, "type")

def test_payload_where():
    _wait_payload(d, "type", where=_routed_to(inst))

def test_attribute_form():
    mod._wait_events(d, 1)

def test_not_a_helper():
    _wait_forever(d, 1)
'''


def _classify_source(tmp_path, source: str) -> Counter:
    (tmp_path / "test_synthetic.py").write_text(source, encoding="utf-8")
    return Counter(r["bucket"] for r in classify_wait_calls(tmp_path))


def _n_split(tmp_path, source: str) -> Counter:
    (tmp_path / "test_synthetic.py").write_text(source, encoding="utf-8")
    return Counter(r["n"] for r in _positional(classify_wait_calls(tmp_path)))


def test_every_bucket_is_reachable_on_synthetic_source(tmp_path):
    """POSITIVE CONTROL: each bucket produces a non-zero count on source that
    must land in it. Without this, a classifier that matched nothing would report
    a plausible-looking breakdown of zeroes."""
    counts = _classify_source(tmp_path, _SYNTHETIC)
    # 5 positional: default n, positional 2, keyword n=2, dynamic n, and the
    # `mod._wait_events(d, 1)` attribute form.
    assert counts[POSITIONAL_BUCKET] == 5, counts
    assert counts["_wait_events until="] == 2, counts
    assert counts["_wait_ops op-only"] == 1, counts
    assert counts["_wait_ops where="] == 1, counts
    assert counts["_wait_payload op-only"] == 1, counts
    assert counts["_wait_payload where="] == 1, counts
    assert sum(counts.values()) == 11, counts  # `_wait_forever` is not a helper


def test_the_n_sub_split_reads_the_n_argument_and_not_the_spool_dir(tmp_path):
    """The informational sub-split must read the RIGHT argument.

    `n` is the second parameter. A mutant reading `args[0]` instead gets the
    spool_dir -- a `Name`, so non-literal, so every site files as `n dynamic` and
    the printed breakdown becomes a confident lie while every bucket TOTAL stays
    correct. The ratchet does not gate on the sub-split, but the failure message
    quotes it, and a guard that misdescribes what it counted is the defect this
    module is written against.
    """
    split = _n_split(tmp_path, "\n".join([
        "_wait_events(d)",            # defaulted   -> 1
        "_wait_events(d, 1)",         # positional  -> 1
        "_wait_events(d, n=1)",       # keyword     -> 1
        "_wait_events(d, 4)",         # positional  -> 4
        "_wait_events(d, n=7)",       # keyword     -> 7
        "_wait_events(d, len(xs))",   # dynamic     -> None
        "",
    ]))
    # 4 and 7 are deliberately not 1, not each other, and not a multiple of the
    # default -- a fixture whose values collide cannot see a mutant that hardcodes
    # one of them.
    assert split == Counter({1: 3, 4: 1, 7: 1, None: 1}), split


def test_a_file_in_a_SUBDIRECTORY_is_counted(tmp_path):
    """The walk is recursive, and nothing in the real corpus proves it.

    Every `.py` in `scripts/browser-bridge/tests` is at the top level today, so a
    mutant swapping `rglob` for `glob` changes NOTHING against the live corpus and
    scores as survived. It would matter the day someone adds a subpackage there --
    the count would silently drop and the SHRINK arm would read as a win to bank.
    """
    nested = tmp_path / "sub" / "deeper"
    nested.mkdir(parents=True)
    (nested / "test_nested_file.py").write_text(
        "_wait_events(d, 1)\n", encoding="utf-8")
    records = classify_wait_calls(tmp_path)
    assert len(_positional(records)) == 1, records


def test_the_keyword_form_is_not_read_as_the_positional_one(tmp_path):
    """NEGATIVE CONTROL for the documented grep failure.

    A line-oriented regex for the count reads `_wait_events(d, 2)` and misses
    `_wait_events(d, n=2)`. Both must classify identically here, and both must
    be POSITIONAL.
    """
    positional = _classify_source(tmp_path, "_wait_events(d, 2)\n")
    keyword = _classify_source(tmp_path, "_wait_events(d, n=2)\n")
    assert positional == keyword == Counter({POSITIONAL_BUCKET: 1}), (
        positional, keyword)


def test_until_flips_a_site_out_of_the_ratcheted_bucket(tmp_path):
    """The remedy the failure message prescribes must actually move the number.

    If adding `until=` did not shrink the positional bucket, every conversion
    would leave the ratchet red and the guard would be unactionable.
    """
    before = _classify_source(tmp_path, "_wait_events(d, 1)\n")
    after = _classify_source(tmp_path, "_wait_events(d, 1, until=p)\n")
    assert before[POSITIONAL_BUCKET] == 1 and after[POSITIONAL_BUCKET] == 0
    assert after["_wait_events until="] == 1, after


def test_a_call_nested_inside_another_call_is_still_counted(tmp_path):
    """`json.loads(_wait_events(d, 1)[0]["payload"])` is a real shape in the
    corpus. A visitor that stopped descending at the outer call would miss it."""
    counts = _classify_source(
        tmp_path, 'x = json.loads(_wait_events(d, 1)[0]["payload"])\n')
    assert counts[POSITIONAL_BUCKET] == 1, counts


def test_the_enclosing_function_is_the_innermost_one(tmp_path):
    """The ledger keys on the enclosing function path, so nesting must resolve to
    the innermost definition -- otherwise two sites in different nested helpers
    collapse onto one ledger key and a growth inside one hides."""
    (tmp_path / "test_nested.py").write_text(
        "def outer():\n"
        "    _wait_events(d, 1)\n"
        "    def inner():\n"
        "        _wait_events(d, 1)\n",
        encoding="utf-8")
    keys = Counter(r["where"] for r in _positional(classify_wait_calls(tmp_path)))
    assert keys == Counter({
        "test_nested.py::outer": 1,
        "test_nested.py::outer.inner": 1,
    }), keys


def _this_module():
    return sys.modules[__name__]


def test_the_grow_arm_ACTUALLY_FIRES_when_the_corpus_exceeds_the_pin(monkeypatch):
    """REACHABILITY of the grow arm, decoupled from the corpus.

    🔴 Watching an arm go red by editing `test_server.py` proves the arm can fail;
    it does NOT prove which comparison did it -- on a corpus mutation that also
    adds a test FUNCTION, `test_the_pinned_ledger_names_only_functions_that_exist`
    reddens too, and the run is green-for-the-wrong-reason if the arm is broken.
    So drive the arm's own decision directly: move the PIN under the real count
    and require THIS arm's own message.

    Mutating the comparison to `>=` makes the arm inert on every corpus and is
    invisible to a corpus-side probe. This test is what kills that mutant.
    """
    records = classify_wait_calls(TARGET_DIR)
    current = len(_positional(records))
    monkeypatch.setattr(_this_module(), "PINNED_POSITIONAL_TOTAL", current - 1)
    with pytest.raises(AssertionError, match=r"POSITIONAL SPOOL READERS GREW"):
        test_the_positional_reader_count_has_not_grown()


def test_the_shrink_arm_ACTUALLY_FIRES_when_the_corpus_falls_under_the_pin(
        monkeypatch):
    """REACHABILITY of the shrink arm, same argument as the grow arm above.

    Without this, an "only-grows" mutant -- the shrink arm made inert, which is
    the exact regression that would let a banked win silently regrow -- survives
    every run against the real corpus.
    """
    records = classify_wait_calls(TARGET_DIR)
    current = len(_positional(records))
    monkeypatch.setattr(_this_module(), "PINNED_POSITIONAL_TOTAL", current + 1)
    with pytest.raises(AssertionError, match=r"POSITIONAL SPOOL READERS SHRANK"):
        test_the_positional_reader_count_has_not_shrunk_below_the_pin()


def test_the_ledger_guard_ACTUALLY_FIRES_on_a_pure_RENAME(monkeypatch):
    """REACHABILITY of the ledger guard, on the exact case it claims to cover.

    A renamed test function nets to ZERO on the total -- one key gone, one key new
    -- so both ratchet arms stay green and only this guard can see it. Against the
    real corpus the ledger matches by construction, so neutering the guard changes
    nothing and scores as survived. Simulating the rename on the PIN side is what
    makes it reachable.
    """
    renamed = dict(PINNED_POSITIONAL_SITES)
    victim = sorted(renamed)[0]
    renamed["test_server.py::test_a_name_that_is_not_in_the_corpus"] = \
        renamed.pop(victim)
    monkeypatch.setattr(_this_module(), "PINNED_POSITIONAL_SITES", renamed)
    with pytest.raises(AssertionError,
                       match=r"NAME DIFFERENT FUNCTIONS") as excinfo:
        test_the_pinned_ledger_names_only_functions_that_exist()
    message = str(excinfo.value)
    # Both halves of the diff must be reported, or the message names only half
    # the rename and the reader cannot act on it.
    assert "test_a_name_that_is_not_in_the_corpus" in message, message
    assert victim.split("::", 1)[1] in message, message
    # ...and the totals genuinely did NOT move, which is the whole premise.
    assert sum(renamed.values()) == PINNED_POSITIONAL_TOTAL


def test_neither_arm_fires_when_the_corpus_matches_the_pin():
    """The boundary in the other direction: at EQUALITY both arms must be silent.

    A permanently-red gate trains everyone to click through, so the arms have to
    be exact at `current == pinned`, not merely loud somewhere nearby. This also
    pins the live corpus against the live constant -- it is the test that goes
    red on an ordinary day when someone adds or converts a site.
    """
    test_the_positional_reader_count_has_not_grown()
    test_the_positional_reader_count_has_not_shrunk_below_the_pin()


def test_the_target_directory_is_where_the_helpers_are_defined():
    """A moved corpus must not read as a clean one.

    If `TARGET_DIR` ever stops containing the helper DEFINITIONS, every count
    this module reports is about the wrong tree -- and the shrink arm's message
    would read as "a win to bank" rather than "you are measuring nothing".
    """
    assert TARGET_DIR.is_dir(), TARGET_DIR
    defined = {
        node.name
        for path in TARGET_DIR.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and node.name in HELPERS
    }
    assert defined == set(HELPERS), (
        "Expected %s to define all of %s; found %s. The helpers moved, so the"
        " pinned counts describe a corpus that no longer exists here."
        % (TARGET_DIR, sorted(HELPERS), sorted(defined)))
