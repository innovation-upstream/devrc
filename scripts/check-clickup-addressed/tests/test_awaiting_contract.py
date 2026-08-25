#!/usr/bin/env python3
"""The PYTHON half of the cross-language contract for
"the newest comment on this task is NOT the token owner's".

🔴 WHY THIS FILE EXISTS. That predicate is implemented TWICE in this repo, in two
languages, and nothing made the two agree until this file and its JS sibling existed:

  * `claude/skills/clickup/lib/awaiting.mjs::isAwaiting` — the `awaiting` command,
    whose own header calls this "the ONLY predicate this API can answer";
  * HERE, decomposed across the ccua seam: `recent-comments.py::_collect` reports the
    owner's newest reply (`latest_reply_ts_by`, with the `UNIDENTIFIED` sentinel), and
    `check-addressed.py::_reply_answers_the_comment` derives the same predicate from it.

ccua ALREADY shells out to that CLI — `recent-comments.py` runs `node query.mjs
my-tasks|comments|me` — so the obvious consolidation is to call `query.mjs awaiting` and
stop re-deriving. **It cannot**, and the reasons are enumerated in the fixture's
`blockers` array and in `claude/skills/clickup/reference/awaiting-vs-ccua.md`. The
decisive one is asserted below rather than argued: `awaiting` emits a row only when the
newest comment is NOT the owner's, so every task the owner answered last is structurally
absent from it — and that is exactly the population `_waiting_verdict`'s ANSWERED branch
is built from, the branch that carries `BOT_IDENTITY_CAVEAT`.

So the single definition available is a shared TABLE, not shared code:
`claude/skills/clickup/test/awaiting-contract.fixtures.json`. Each side MEASURES its own
column rather than copying the other's, and BOTH recompute the divergence ledger from the
two columns and pin it — so a divergence appearing or disappearing is red on both sides.

🔴 THESE ARE SEAM / INVARIANT GUARDS, NOT REGRESSION COVERAGE. No defect is fixed here;
nothing below was red before this file existed because nothing below existed. Their
evidence is the MUTATION matrix in the PR body: break either implementation's predicate
and a named test here (or in the JS half) goes red with its own message.

Every value in the fixture is SYNTHETIC. This repo is public.
"""
import importlib.util
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = (REPO_ROOT / "claude" / "skills" / "clickup" / "test"
                / "awaiting-contract.fixtures.json")

sys.path.insert(0, str(SCRIPT_DIR))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_addressed = _load("check_addressed", "check-addressed.py")
recent_comments = _load("recent_comments", "recent-comments.py")

# 🔴 Read, never regenerated from this side. A table a test writes is a table that agrees
# with any drift; the point of the file is that the OTHER language reads the same bytes.
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
OWNER = FIXTURE["owner_id"]
CASES = FIXTURE["cases"]
NOW = datetime.fromtimestamp(FIXTURE["now_ms"] / 1000, tz=timezone.utc)

UNVERIFIED = "unverified"


def _case(name):
    for c in CASES:
        if c["name"] == name:
            return c
    raise AssertionError(f"fixture has no case named {name!r} — the table and this file disagree")


def _collect_rows(comments, my_id=OWNER):
    """Run the REAL producer end to end over a stubbed ClickUp, returning its JSON rows.

    `_collect` is driven rather than its parts called directly: the D12 fix lives in the
    JOIN (the reply is computed over the UNFILTERED comment list, before the loop that
    drops the owner's own comments), and calling `latest_reply_ts_by` by hand would test
    both ends of that join while leaving the join itself unwitnessed.
    """
    orig = (recent_comments.get_my_user_id, recent_comments.get_my_tasks,
            recent_comments.get_comments)
    try:
        recent_comments.get_my_user_id = lambda: my_id
        recent_comments.get_my_tasks = lambda: [{
            "id": "T-SYNTH-1", "name": "synthetic task",
            "status": {"status": "to do"}, "priority": {"priority": "normal"},
        }]
        recent_comments.get_comments = lambda _tid: comments
        buf, err = io.StringIO(), io.StringIO()
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = buf, err
        try:
            recent_comments._collect(10, False, True)
        finally:
            sys.stdout, sys.stderr = real_out, real_err
    finally:
        (recent_comments.get_my_user_id, recent_comments.get_my_tasks,
         recent_comments.get_comments) = orig
    return json.loads(buf.getvalue())


def measure_py(comments):
    """What the ccua PIPELINE answers, in the fixture's vocabulary.

    Producer -> consumer, both real. Four branches, and the ORDER of the middle two is the
    whole point of the seam:

      * no row at all              -> False. Every comment was the owner's (or there were
                                      none), so nobody is waiting. `_waiting_verdict`
                                      returns at its `snippet` check.
      * `my_latest_reply` ABSENT   -> UNVERIFIED. The producer withheld it: the UNIDENTIFIED
                                      sentinel, i.e. the owner's own comments cannot be
                                      ranked or the owner could not be identified at all.
                                      `_waiting_verdict` surfaces the ticket WITH a note
                                      saying the check did not run.
      * `my_latest_reply` is None  -> True. PRESENT-and-null is the positive claim "I looked
                                      at every comment and none is mine". That is a DECIDED
                                      fact, not an absence — reading it as unverified is
                                      exactly the conflation the sentinel was built to
                                      prevent, in the opposite direction.
                                      🔴 MEASURED, not assumed: `_reply_answers_the_comment`
                                      returns None here (there is no date to compare), and
                                      an earlier draft of this helper therefore scored the
                                      case UNVERIFIED. `_waiting_verdict` gets it right by
                                      falling through `is True` to the flag; this helper has
                                      to make the same distinction explicitly.
      * otherwise                  -> not answered.
    """
    rows = _collect_rows(comments)
    if not rows:
        return False
    # `_collect` sorts newest-first and there is one task, so row 0 is the newest comment
    # that is not the owner's — the record `attach_clickup_meta` would hand the consumer.
    nc = check_addressed.build_newest_comment(rows[0])
    if "my_latest_reply" not in nc:
        return UNVERIFIED
    if nc["my_latest_reply"] is None:
        return True
    answered = check_addressed._reply_answers_the_comment(nc, NOW)
    if answered is None:
        return UNVERIFIED
    return not answered


# --------------------------------------------------------------------------- the column

def test_the_python_column_is_measured_case_by_case():
    """Every row of the shared table, against the real producer+consumer pipeline."""
    wrong = []
    for c in CASES:
        got = measure_py(c["comments"])
        if got != c["py"]:
            wrong.append(f"{c['name']}: fixture says py={c['py']!r}, pipeline yields {got!r}")
    assert wrong == [], (
        "the shared contract table no longer describes this side.\n  " + "\n  ".join(wrong)
        + "\nThe implementation is the authority — re-read it before editing the table, and "
        "say in the commit which behaviour moved.")


def test_the_divergence_ledger_is_exactly_what_the_two_columns_produce():
    """🔴 Recomputed HERE too, from the same two columns the JS half reads.

    Asserted as a SET that fails when it GROWS *or* SHRINKS. A ledger that only catches
    growth blesses a silent narrowing, and a divergence disappearing is a behaviour change
    on a live command that nobody asked for.
    """
    derived = sorted(c["name"] for c in CASES if c["js"] != c["py"])
    pinned = sorted(FIXTURE["divergences"])
    assert derived == pinned, (
        f"pinned divergences {pinned} != derived {derived}. Update BOTH the case row and "
        "the ledger, in the same commit.")


def test_the_two_implementations_agree_wherever_both_answer_a_boolean():
    """The AGREEMENT half. Without it the table only records disagreement."""
    checked = []
    for c in CASES:
        if not isinstance(c["js"], bool) or not isinstance(c["py"], bool):
            continue
        assert isinstance(c["awaiting"], bool), (
            f"{c['name']}: both implementations answer a boolean, so the contract must too")
        assert c["js"] == c["awaiting"], f"{c['name']}: JS disagrees with the contract"
        assert c["py"] == c["awaiting"], f"{c['name']}: Python disagrees with the contract"
        checked.append(c["name"])
    assert len(checked) >= 5, (
        f"only {len(checked)} case(s) exercised the agreement claim — a table whose cases are "
        "nearly all divergent proves the two are equivalent nowhere")


# ------------------------------------------------------- the UNIDENTIFIED distinction

def test_UNIDENTIFIED_survives_the_contract_and_the_JS_side_cannot_express_it():
    """🔴 THE FIELD THAT BLOCKS A CONSOLIDATION ONTO awaiting.mjs.

    One of the owner's comments cannot be dated, so which of the owner's comments is
    newest is UNKNOWABLE. This side says so — `latest_reply_ts_by` returns UNIDENTIFIED,
    `build_record` omits the key, and `_waiting_verdict` fires the flag WITH a note saying
    the check did not run. `isAwaiting()` has no third state and returns a confident
    `true` (pinned on the JS side).

    Driven end to end, not asserted about `latest_reply_ts_by` alone: the fact that
    matters is the key being ABSENT from the record the consumer receives, and that is a
    property of the join, not of the ranking function.
    """
    c = _case("the_owners_own_comment_has_an_unreadable_date")
    rows = _collect_rows(c["comments"])
    assert len(rows) == 1, f"the producer stopped reporting the colleague's comment: {rows}"
    assert "my_latest_reply" not in rows[0], (
        "the producer emitted `my_latest_reply` over a thread it cannot rank. Present-and-null "
        "is the POSITIVE claim 'I looked; you never replied' — the exact false negative the "
        f"UNIDENTIFIED sentinel exists to prevent: {rows[0]}")
    assert "my_latest_reply_ms" not in rows[0], (
        "the ms refinement appeared without the display date it refines")
    assert measure_py(c["comments"]) == UNVERIFIED
    assert c["js"] is True, (
        "the fixture records that isAwaiting() answers a confident boolean here; if that "
        "changed, the JS half is where it is measured and this claim moves with it")

    # 🔴 UNVERIFIED must be OBSERVABLE in the report, not merely a value this helper
    # computes. A state nobody can watch reach the output is indistinguishable from a state
    # wired to nothing — and the whole claim being made about awaiting.mjs is that IT has no
    # way to say this. Drive the real consumer and read what it prints.
    record = {
        "task_id": "T-SYNTH-1", "status": "no_mentions_found", "sessions_searched": 1,
        "mentions_found": 0, "completion": [], "open": [],
    }
    check_addressed.attach_clickup_meta(record, rows[0])
    kind, line = check_addressed._waiting_verdict(record, NOW)
    assert kind == check_addressed.WAITING, (
        f"the ticket was not surfaced at all when the owner's replies could not be ranked: "
        f"{kind!r} / {line!r}")
    assert "could not be checked" in line, (
        "the flag fired but claimed nothing about the check that did not run — that is the "
        f"confident false negative the sentinel exists to prevent: {line!r}")


def test_a_failed_user_lookup_is_UNVERIFIED_and_not_a_verdict():
    """The other arm of the same sentinel: the owner could not be identified AT ALL.

    `query.mjs awaiting` exits 1 here and prints nothing (fixture blocker `failure-mode`).
    ccua continues, warns on stderr, and withholds the key — so the pipeline declines to
    claim rather than reporting an answered ticket as unanswered.
    """
    c = _case("colleague_commented_last")
    rows = _collect_rows(c["comments"], my_id=None)
    assert rows, "the producer reported nothing at all when the user id was unresolvable"
    for r in rows:
        assert "my_latest_reply" not in r, (
            f"a failed user lookup produced a positive 'you never replied' claim: {r}")


# ----------------------------------------------------- the structural blocker, asserted

def test_the_population_awaiting_DROPS_is_the_one_the_suppression_note_needs():
    """🔴 THE BLOCKER, from this side.

    `awaiting` emits no row for a task the owner answered last (asserted in the JS half).
    ccua DOES — and the record it emits carries `my_latest_reply_ms`, which is the only
    input `_waiting_verdict`'s ANSWERED branch has. Feeding ccua from `awaiting` would
    delete that whole block, and nothing in either suite would have failed.
    """
    c = _case("owner_commented_last")
    rows = _collect_rows(c["comments"])
    assert len(rows) == 1, (
        "ccua must still report the colleague's comment on a task the owner answered last — "
        f"that record is what the suppression note is built from: {rows}")
    row = rows[0]
    assert isinstance(row.get("my_latest_reply_ms"), int), (
        f"the record carries no raw reply ms, so the ANSWERED branch cannot decide: {row}")
    assert isinstance(row.get("date_ms"), int), (
        f"the record carries no raw comment ms to compare against: {row}")

    # And the consumer actually reaches ANSWERED on it — a structural check would
    # type-check past a wrong argument, so the behaviour is exercised too.
    nc = check_addressed.build_newest_comment(row)
    assert check_addressed._reply_answers_the_comment(nc, NOW) is True, (
        "the owner's reply is not older than the colleague's comment, so the consumer must "
        "read this as answered")


def test_the_fields_awaiting_cannot_carry_are_the_fields_this_producer_emits():
    """The `field` blockers, measured rather than listed.

    The JS half pins the exact key set an `awaiting` row carries. Here we pin that the
    ccua record genuinely carries each blocked field, so the two ledgers describe one real
    gap. A blocker naming a field nobody emits would be a sentence, not a gap.
    """
    c = _case("colleague_commented_last")
    row = _collect_rows(c["comments"])[0]
    for key in ("text", "task_priority", "my_latest_reply", "my_latest_reply_ms"):
        assert key in row, (
            f"the ccua record no longer carries {key!r}, which the fixture lists as a blocker. "
            "Either the producer regressed or a blocker was genuinely closed — say which.")
    awaiting_keys = set(FIXTURE["awaiting_row_keys"])
    for key in ("text", "snippet", "task_priority", "my_latest_reply", "my_latest_reply_ms"):
        assert key not in awaiting_keys, (
            f"an `awaiting` row now carries {key!r} — that retires a blocker, so re-read "
            "reference/awaiting-vs-ccua.md and the fixture before assuming it is still true.")


def test_every_blocker_carries_a_kind_and_a_reason():
    """The same claim the JS half makes over the same array, so neither reader drifts alone."""
    kinds = {"population", "field", "failure-mode"}
    assert len(FIXTURE["blockers"]) >= 5, (
        "the blocker ledger shrank — if a blocker was genuinely closed, say which in the commit")
    for b in FIXTURE["blockers"]:
        assert b["kind"] in kinds, f"unknown blocker kind {b['kind']!r}"
        assert b["field"], f"blocker {b['kind']} names no field"
        assert len(b["why"]) > 40, f"blocker {b['kind']}/{b['field']} carries no usable reason"


def test_the_fixture_is_reachable_from_this_suite_at_all():
    """A POSITIVE CONTROL on the path, not a formality.

    Every assertion above is vacuous if the fixture resolves to nothing — and the two
    suites reach it from different roots, so a move that keeps the JS half green can leave
    this one reading an empty table. The counts are the witness that it was read.
    """
    assert FIXTURE_PATH.is_file(), f"shared contract fixture missing at {FIXTURE_PATH}"
    assert len(CASES) >= 9, f"the shared table collapsed to {len(CASES)} case(s)"
    assert OWNER and FIXTURE["colleague_id"] != OWNER
