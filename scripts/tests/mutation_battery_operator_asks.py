#!/usr/bin/env python3
"""Mutation battery for `scripts/lib/operator_asks.py` — round 0's attribution
input for `/audit-pr`, its classifier, and its no-quiet-empty-block contract.

    python3 scripts/tests/mutation_battery_operator_asks.py
    python3 scripts/tests/mutation_battery_operator_asks.py --only M06

🔴 NOT COLLECTED BY THE GATE, ON PURPOSE — the filename is the mechanism:
`scripts/run-tests.sh` collects `test_*.py` only. A MANUAL instrument, run when
`operator_asks.py`, its suite, or `extract_user_msgs.py`'s emit contract changes.
It rewrites tracked source in place once per mutant, so two sessions cannot run
it concurrently.

WHY IT IS COMMITTED — the reason `mutation_battery_extract_user_msgs.py` gives,
arrived at the same way and one PR later. devrc#1887's body and commit messages
quoted `17 mutants, 17 KILLED` from a script in a session scratchpad, which makes
the number a CLAIM; round 1 of its own ladder said so, noting that
`claude/RULES.md`'s "re-verify an auditor's or subagent's self-reported mutation
results" cannot be satisfied from a tree that does not hold the instrument. This
makes them evidence.

WHAT THE ROWS ARE FOR. Two classes, and the second is the one this module keeps
getting wrong:

  * the no-quiet-empty-block contract (`M01`, `M09`) — the block exists so round
    0 cannot read "nobody asked for this" off a partial or failed read, and both
    mutants restore exactly that.
  * PREDICATES THAT ANSWER A NEARBY QUESTION (`M06`, `M07`, `M16`, `M17`). This
    module's operator-identity predicate has been wrong TWICE — a bot denylist
    that filtered nothing, then a repo-membership set that re-admitted the bot it
    replaced and ten teammates besides. Both were unit-tested and green.

🔴 AND THE SHAPE THAT DEFEATED THIS BATTERY'S PREDECESSOR: eight classifier
families were mutated here, all eight KILLED, and all eight fired ZERO times in
production because the upstream producer already removed them. A kill proves a
test watches a branch; it says NOTHING about whether the branch is REACHABLE.
That is `TestTheSeamWithTheRealProducer`'s job in the suite, not this file's —
do not add a row for a pattern without a seam test proving the input can arrive.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

#: The file this battery mutates, resolved from `__file__` so a copy placed
#: anywhere else cannot silently mutate a different tree.
SCRIPT = ROOT / "scripts" / "lib" / "operator_asks.py"

#: The suite that must kill each mutant.
SUITE = ROOT / "scripts" / "tests" / "test_operator_asks.py"

#: `(id, why it matters, killer, old, new)` — `old` is the 4th field, which
#: `test_mutation_battery_anchors.py` reads to check each anchor is unique.
MUTANTS = (
    (
        "P1",
        "POSITIVE CONTROL — a rename nothing can survive. If this is not KILLED "
        "the harness is wired to nothing and every other row is meaningless.",
        "",
        "def render(",
        "def render_RENAMED(",
    ),
    (
        "M01",
        "a PARTIAL read drops the UNKNOWN directive — round 0 then reads a "
        "half-resolved session set as complete and proposes a deletion",
        "test_the_directive_ships_even_when_SOME_asks_were_read",
        "    if unmeasured or not asks:\n        lines += [\"\", UNKNOWN_DIRECTIVE]",
        "    if not asks:\n        lines += [\"\", UNKNOWN_DIRECTIVE]",
    ),
    (
        "M02",
        "an id holding a glob metacharacter matches OTHER sessions' transcripts, "
        "so their asks are attributed to this PR",
        "test_a_glob_metacharacter_in_an_id_cannot_widen_the_transcript_match",
        'root.glob(f"*/{_glob.escape(sid)}.jsonl")',
        'root.glob(f"*/{sid}.jsonl")',
    ),
    (
        "M03",
        "an escape sequence from a commit body reaches the operator's terminal",
        "test_an_unsafe_id_never_reaches_the_rendered_block",
        '    if not session_trailer.valid_id(sid):\n        return "<unsafe-id>"\n    return sid[:width]',
        "    return sid[:width]",
    ),
    (
        "M04",
        "a bare `/slash` invocation counts as an operator ask",
        "test_both_operator_kinds_become_asks_and_command_does_not",
        '        if row.get("kind") not in OPERATOR_KINDS:',
        "        if False:",
    ),
    (
        "M05",
        "the operator's ANSWERS vanish again — the gap round 0 found, where the "
        "requirements statement authorising a design decision was invisible",
        "test_both_operator_kinds_become_asks_and_command_does_not",
        'OPERATOR_KINDS = ("typed", "answer")',
        'OPERATOR_KINDS = ("typed",)',
    ),
    (
        "M06",
        "a MISSING authorship field is guessed instead of reported UNKNOWN — the "
        "shape both earlier wrong predicates had",
        "test_a_MISSING_viewerDidAuthor_is_UNKNOWN_not_a_silent_no",
        "    if v is True:\n        return True, \"\"",
        "    if v is not False:\n        return True, \"\"",
    ),
    (
        "M07",
        "a teammate's or a bot's comment is inlined under a heading saying it is "
        "the operator's — measured: devrc has 10 other collaborators, and "
        "`civitai-deploy` posts 74% of comments on `civitai/civitai`",
        "test_the_operator_is_viewerDidAuthor_and_NOT_repo_membership",
        "    if v is False:\n        login =",
        "    if False:\n        login =",
    ),
    (
        "M08",
        "the extractor renumbers an exit and this module prints a confident "
        "wrong reason for it",
        "test_the_extractor_exit_vocabulary_is_pinned_to_what_the_script_documents",
        '    6: "the transcripts were read and held no operator-typed message",\n',
        "",
    ),
    (
        "M09",
        "the empty block stops announcing itself, so 'we looked nowhere' and "
        "'the operator asked for nothing' become indistinguishable",
        "test_no_asks_and_no_sources_says_WE_LOOKED_NOWHERE_not_nothing_was_asked",
        '            "🔴 **NO OPERATOR ASK COULD BE READ FOR THIS PR.** Every source is "\n            "listed below with the reason it did not answer.",',
        '            "",',
    ),
    (
        "M10",
        "98.39% of the bytes this module receives — subagent results — read as "
        "the operator",
        "test_a_subagent_notification_is_not_an_operator_ask",
        "    if t.startswith(TASK_NOTIFICATION_TAG):",
        "    if False:",
    ),
    (
        "M11",
        "harness-authored notes are attributed to the operator",
        "test_a_harness_authored_note_is_not_an_operator_ask",
        "    for m in HARNESS_NOTE_PATTERNS:\n        if m in head:",
        "    for m in ():\n        if m in head:",
    ),
    (
        "M12",
        "the marker window WIDENS and the filter starts eating asks ABOUT a "
        "stopped agent — the direction this module calls worse than no filter",
        "test_an_ask_that_MENTIONS_a_harness_note_is_still_an_ask",
        "    head = t[:_MARKER_WINDOW]",
        "    head = t",
    ),
    (
        "M13",
        "the filter deciding 98% of the bytes stops reporting what it dropped",
        "test_the_rendered_block_names_what_the_classifier_dropped",
        '        lines.append(f"  {SOURCE_SESSION}: dropped {n} record(s) — {reason}")',
        "        pass",
    ),
    (
        "M14",
        "a picked option reads as a freely-typed sentence",
        "test_an_answer_row_is_LABELLED_as_a_decision_in_the_render",
        '                lines.append("_(an answer to a question this session asked)_")',
        "                pass",
    ),
    (
        "M15",
        "the printed re-read command loses `--include-answers`, reproducing the "
        "gap round 0 found",
        "test_the_reread_command_carries_include_answers",
        '                f"session-analysis/extract_user_msgs.py --include-answers {cmd}"',
        '                f"session-analysis/extract_user_msgs.py {cmd}"',
    ),
    (
        "M16",
        "the deleted PR-description source comes back without a measurement",
        "test_the_PR_DESCRIPTION_is_not_a_source_at_all",
        'SOURCE_PR_COMMENT = "PR comment"',
        'SOURCE_PR_COMMENT = "PR comment"\nSOURCE_PR_BODY = "PR description"',
    ),
    (
        "M17",
        "a measured-dead size mechanism is re-added on a hunch",
        "test_no_cap_or_ceiling_constant_survives",
        'SOURCE_SESSION = "session transcript"',
        'SOURCE_SESSION = "session transcript"\nPER_MESSAGE_CAP = 4000',
    ),
    (
        "M18",
        "an rc-0 PARTIAL read is treated as full coverage, because the "
        "extractor's own `!` notes are not read",
        "test_a_rc0_coverage_note_becomes_an_UNKNOWN",
        '        if not (t.startswith(_COVERAGE_NOTE_PREFIX) and len(t) > 1):',
        "        if True:",
    ),
    (
        "M21",
        "the dedup note is read as a coverage GAP, so a COMPLETE read carries a "
        "false UNKNOWN and the directive — a permanent UNKNOWN teaches the reader "
        "to skip the line that matters",
        "test_the_dedup_note_is_NOT_read_as_a_coverage_gap",
        "        if any(m in note for m in NOT_A_GAP_MARKERS):",
        "        if False:",
    ),
    (
        "M22",
        "an UNRECOGNISED `!` note stops counting as a gap, so a note family the "
        "extractor adds later is silently hidden instead of over-reported",
        "test_an_UNRECOGNISED_note_IS_still_read_as_a_gap",
        "        out.append(note)",
        "        pass",
    ),
    (
        "M23",
        "the captured-text warning narrows back to files only, permitting the "
        "exposure its own heading forbids",
        "test_the_captured_text_warning_covers_every_surface_not_just_files",
        '        "🔴 **DO NOT PUBLISH A QUOTED ASK — ON ANY SURFACE THAT LEAVES THIS "',
        '        "🔴 **DO NOT COMMIT A QUOTED ASK into a file. "',
    ),
    (
        "M24",
        "a non-operator reason asserts an identity the field cannot establish — "
        "untrue under a CI or shared `gh` credential",
        "test_a_non_operator_reason_does_not_assert_an_identity_it_cannot_know",
        # ⚠ MUTATES THE SECOND LINE OF THE CONCATENATION, NOT THE FIRST. An
        # earlier row rewrote the f-string and produced a SYNTAX ERROR, so the
        # module failed to import, the suite errored with no FAILED line, and the
        # row scored WRONG-REASON — a mutant that dies of its own malformation
        # tests nothing. Keep every mutation syntactically valid.
        '            "authenticated as"',
        '            ""',
    ),
    (
        "M19",
        "the agent's own `selected preview:` plan is inlined as the operator's "
        "ask — the inversion round 0 exists to catch",
        "test_the_agents_own_preview_block_is_stripped_from_an_answer",
        "    cut = out.find(ANSWER_PREVIEW_MARKER)\n    if cut != -1:",
        "    cut = -1\n    if cut != -1:",
    ),
    (
        "M20",
        "the comment source stops declaring that REVIEW comments are invisible, "
        "so an ask left in a review reads as absent rather than UNKNOWN",
        "test_the_comment_source_declares_that_REVIEW_comments_are_invisible",
        '            "ISSUE comments ONLY — an ask left as a REVIEW comment, inside a "',
        '            "— an ask left in a review is "',
    ),
)


def selected(argv):
    """-> (rows, banner). `--only ID` re-checks one row, ALWAYS with P1."""
    ap = argparse.ArgumentParser(prog=Path(__file__).name, add_help=True)
    ap.add_argument("--only", action="append", default=[],
                    help="mutant id to run; repeatable. P1 is always included.")
    a = ap.parse_args(argv)
    if not a.only:
        return list(MUTANTS), ""
    ids = {x.strip() for x in a.only}
    if not all(ids):
        ap.error("--only needs a non-empty id")
    known = {m[0] for m in MUTANTS}
    unknown = sorted(ids - known)
    if unknown:
        ap.error(f"unknown mutant id(s): {unknown}; known: {sorted(known)}")
    # 🔴 P1 IS NEVER FILTERED OUT. A filtered run without it would report
    # `1/1 killed` from an instrument nothing had shown could observe anything.
    keep = ids | {"P1"}
    rows = [m for m in MUTANTS if m[0] in keep]
    return rows, f"FILTERED to {sorted(ids)} (plus the P1 positive control)"


def _run_suite():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", str(SUITE), "-q", "--no-header",
         "-p", "no:cacheprovider"],
        capture_output=True, text=True, env=env, cwd=str(ROOT),
    )
    return r.returncode, r.stdout + r.stderr


def _failing(output):
    return set(re.findall(r"^(?:FAILED|ERROR) [^:]+::(?:\w+::)?(\w+)", output, re.M))


def main(argv=None):
    rows, banner = selected(argv if argv is not None else sys.argv[1:])
    if banner:
        print(banner)
    original = SCRIPT.read_text()
    digest = hashlib.sha256(original.encode()).hexdigest()

    def restore():
        SCRIPT.write_text(original)
        got = hashlib.sha256(SCRIPT.read_text().encode()).hexdigest()
        if got != digest:
            sys.exit(f"🔴 RESTORE FAILED — {SCRIPT} is not byte-identical. STOP.")

    # 🔴 C0 — the unmutated control. Without it every KILLED below could be a
    # suite that was already red.
    rc, out = _run_suite()
    print(f"C0  unmutated -> rc={rc}  {'GREEN' if rc == 0 else '🔴 RED'}")
    if rc != 0:
        print(out[-2000:])
        return 1

    killed = survived = skipped = 0
    try:
        killed, survived, skipped = _sweep(rows, original, restore)
    finally:
        # 🔴 IN A `finally`, AS BOTH PREDECESSORS DO. Without it a Ctrl-C or any
        # exception mid-sweep leaves `scripts/lib/operator_asks.py` MUTATED in a
        # shared, tracked checkout — e.g. `if False:` in the operator-identity
        # predicate — and the next `/audit-pr --round 0` reads that module live.
        # The digest check only runs on paths that reach it. Round 2 of the
        # devrc#1887 ladder found this missing.
        restore()

    print(f"\n{killed} KILLED · {survived} SURVIVED/WRONG-REASON · "
          f"{skipped} SKIPPED · of {len(rows)}")
    print(f"restored by digest: "
          f"{hashlib.sha256(SCRIPT.read_text().encode()).hexdigest() == digest}")
    return 1 if (survived or skipped) else 0


def _sweep(rows, original, restore):
    """One mutant at a time. -> (killed, survived, skipped)."""
    killed = survived = skipped = 0
    for mid, why, killer, old, new in rows:
        n = original.count(old)
        if n != 1:
            skipped += 1
            print(f"⚠ SKIPPED {mid}: anchor matches {n}x, not 1 — the mutation "
                  f"was NEVER APPLIED, which is not a pass")
            continue
        SCRIPT.write_text(original.replace(old, new, 1))
        rc, out = _run_suite()
        restore()
        fails = _failing(out)
        if rc == 0:
            survived += 1
            print(f"🔴 SURVIVED {mid} — nothing caught: {why}")
        elif mid == "P1":
            killed += 1
            print(f"✅ KILLED   {mid} (positive control — the harness can go red)")
        elif killer in fails:
            killed += 1
            print(f"✅ KILLED   {mid} by {killer}")
        else:
            survived += 1
            print(f"🔴 WRONG-REASON {mid}: red, but {killer} not among "
                  f"{sorted(fails)} — it died for a different reason")

    return killed, survived, skipped


if __name__ == "__main__":
    sys.exit(main())
