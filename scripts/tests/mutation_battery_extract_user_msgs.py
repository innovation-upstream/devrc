#!/usr/bin/env python3
"""Mutation battery for `scripts/session-analysis/extract_user_msgs.py` —
the arc-scoped extractor, its four-way exit contract, and the prose that routes
to it.

    python3 scripts/tests/mutation_battery_extract_user_msgs.py

🔴 NOT COLLECTED BY THE GATE, ON PURPOSE — the filename is the mechanism:
`scripts/run-tests.sh` collects `test_*.py` only. This is a MANUAL instrument,
run when the extractor, its suite, its reference doc or `/resume`'s routing row
changes. It rewrites tracked source in place once per mutant, so two sessions
cannot run it concurrently.

WHY IT IS COMMITTED — the reason `mutation_battery_handoff_archive_and_cap.py`
gives, arrived at the same way. The results quoted in #1870's body and commit
message were produced by a script in a session scratchpad, which makes them a
CLAIM; the round-0 audit of that PR said so explicitly, having had to rebuild
the battery from scratch to check them. This makes them evidence.

🔴 WHAT THE FIRST RUNS FOUND — kept because it is the reusable part, and because
two of the three are defects in the BATTERY, not in the code:

  * A `replace` whose pattern was not in the file changed nothing, and a no-op
    mutant is SURVIVED by construction. Every row is now checksummed and a
    pattern that does not occur EXACTLY ONCE is `NOT-APPLIED`, counted with the
    survivors.
  * A syntax-broken mutant made pytest emit `ERROR` at collection, and
    `grep -c '^FAILED '` counts zero of those — so a mutant that broke the
    module scored SURVIVED. A run that collected nothing is now `INVALID`.
  * The first battery restored two of the three files it could touch and left a
    mutated `SKILL.md` in the tree. Restore covers every target, in a `finally`.

🔴 AND ONE FINDING ABOUT MUTATION RESULTS THEMSELVES — READ THIS BEFORE
MEASURING `P2` AGAIN. Deleting `os.dup2` was scored, in order: SURVIVED — 0 red
of 1 draw, and that GREEN draw is the whole reason this history exists; then
20/20 red; then an independent audit's 22/40; then 10/40. Four measurements, no
two agreeing. Same mutant, same test, controls clean every time. It is LOAD-DEPENDENT — whether the shutdown
flush still holds data depends on TextIOWrapper buffer state when the pipe
closes, which depends on how far `head` got. **There is no rate to find. If you
are about to write a FIFTH number, that is the mistake**; two of the four
already shipped into source comments as properties of the guard, one of them
telling maintainers a load-bearing line was uncovered.

`P2` therefore no longer targets the flaky observable. It deletes the `dup2` and
is killed by `test_the_shutdown_flush_is_silenced_by_redirecting_fd_1`, which
asserts the redirect HAPPENS — deterministic. That is a weaker claim than "the
stderr noise is gone" and is deliberately not dressed up as the stronger one.

The general rule the episode is worth remembering for: a mutation result is a
fact about the test AS IT STOOD. Re-run after touching a fixture; never carry a
verdict across that change.

READ BEFORE TRUSTING A VERDICT
------------------------------
  * THE CONTROL RUNS FIRST (row `C0`) and aborts on a red baseline or a run that
    printed no summary. A sweep of green verdicts is indistinguishable from a
    harness wired to nothing until something makes the number move.
  * EVERY ROW NAMES THE TEST WHOSE OWN ASSERTION MUST GO RED. A mutant killed by
    a neighbouring guard is `KILLED-WRONG-REASON` and counts with the survivors.
    Rows `E1`/`E2` name the DISTINCTNESS guards rather than the behavioural
    ones deliberately: collapsing an exit constant moves the behavioural test's
    expectation with it, so that test correctly stays green and the guard that
    owns "these are two different facts" is the pair of `_are_DIFFERENT_CODES`
    tests.
  * `PYTHONDONTWRITEBYTECODE=1` IS FORCED ON THE CHILD. CPython validates a
    cached module on whole-second mtime + size, so a same-length edit landing in
    the same second as the last import is invisible: the test imports the
    ORIGINAL bytecode and the mutant is scored SURVIVED without ever executing.
  * Sources are restored in a `finally`. Check `git status` anyway if it dies
    hard.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

#: Sentinels for the deletion rows that must REMOVE an except arm without
#: breaking the module: replacing the caught type with a class nothing raises
#: disables the arm while keeping the file importable. Injected into the source
#: by `_PRELUDE` so the mutant is a real deletion, not a NameError.
_PRELUDE = ("class _NeverRaisedW1(Exception):\n    pass\n\n\n"
            "class _NeverRaisedW2(Exception):\n    pass\n\n\n")

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "scripts" / "session-analysis" / "extract_user_msgs.py"
TESTS = REPO / "scripts" / "session-analysis" / "tests" / "test_extract_user_msgs.py"
REF = REPO / "claude" / "skills" / "handoff" / "reference" / "user-messages.md"
SKILL = REPO / "claude" / "skills" / "resume" / "SKILL.md"

SCRIPT = SRC
SUITE = TESTS

#: The dedup key, quoted once so the three key-component rows cannot drift apart.
DEDUP_KEY = ('"\\x1f".join((rec["project"], rec["kind"],\n'
             '                                 rec["text"])).encode()')

#: `(id, kind, description, old, new, expected_test)`
MUTANTS = [
    ("C0", "control", "message_of never returns typed prose — the POSITIVE "
     "CONTROL; if this does not go red the battery is wired to nothing",
     '    return ("typed", txt)', "    return None",
     "test_plain_prose_is_typed"),

    # --- the four zeros -------------------------------------------------------
    ("E1", "collapse", "EXIT_ARC_EMPTY becomes EXIT_ARC_UNMEASURED — a wrong "
     "name and a measured-empty arc become one fact",
     "EXIT_ARC_EMPTY = 4", "EXIT_ARC_EMPTY = 3",
     "test_4_and_3_are_DIFFERENT_CODES"),
    ("E2", "collapse", "EXIT_NO_MESSAGES becomes EXIT_NO_TRANSCRIPTS",
     "EXIT_NO_MESSAGES = 6", "EXIT_NO_MESSAGES = 5",
     "test_5_and_6_are_DIFFERENT_CODES"),
    ("E3", "replacement", "a measured-empty arc exits OK",
     "            return EXIT_ARC_EMPTY", "            return EXIT_OK",
     "test_4_a_doc_that_resolves_with_no_members"),
    ("E4", "replacement", "an unresolvable seed exits OK",
     "            return EXIT_ARC_UNMEASURED", "            return EXIT_OK",
     "test_3_a_seed_naming_no_doc_is_UNMEASURED"),
    # 🔴 ANCHORED ON THE ENCLOSING GUARD. `return EXIT_NO_TRANSCRIPTS` occurs
    # TWICE since the exit-6 fix added an opened-nothing branch, and a 2x anchor
    # mutates a site this row does not name — caught by the anchors ledger.
    ("E5", "replacement", "ids resolving to no transcript exit OK",
     '"was read — this is not \'the sessions are empty\' (that is "\n'
     '                  f"exit {EXIT_NO_MESSAGES}).", file=err)\n'
     "            return EXIT_NO_TRANSCRIPTS",
     '"was read — this is not \'the sessions are empty\' (that is "\n'
     '                  f"exit {EXIT_NO_MESSAGES}).", file=err)\n'
     "            return EXIT_OK",
     "test_5_ids_that_resolve_to_nothing_read_NOTHING"),
    ("E6", "replacement", "transcripts holding nothing typed exit OK",
     "        return EXIT_NO_MESSAGES", "        return EXIT_OK",
     "test_6_transcripts_that_ARE_read"),

    # --- silent narrowing -----------------------------------------------------
    ("N1", "deletion", "resolve_sessions DROPS an unresolvable id instead of "
     "returning it — a partial extraction then reads as a whole one",
     "            missing.append(sid)", "            pass",
     "test_an_unresolvable_id_is_RETURNED_not_dropped"),
    ("N2", "disable", "the partial-selection warning never prints",
     "        if missing:", "        if False:",
     "test_a_partially_resolvable_selection_SAYS_SO"),
    ("N3", "replacement", "the dedup count is hardcoded to zero",
     'f"deduped={suppressed} out=', 'f"deduped=0 out=',
     "test_dedup_suppresses_a_repeat_and_REPORTS"),
    ("N4", "disable", "dedup never suppresses anything",
     "                if key in seen:", "                if False:",
     "test_dedup_suppresses_a_repeat_and_REPORTS"),
    ("N5", "replacement", "the UNSCOPED banner stops saying it is unscoped",
     'notes.append(f"UNSCOPED: every session transcript',
     'notes.append(f"every session transcript',
     "test_with_no_selector_it_walks_the_corpus"),

    # --- the dedup key, one row per component --------------------------------
    ("D1", "narrowing", "kind DROPPED from the dedup key — a slash command and "
     "identical typed prose collide",
     DEDUP_KEY, '(rec["project"] + rec["text"]).encode()',
     "test_dedup_does_NOT_collide_a_command"),
    ("D2", "narrowing", "project DROPPED — the pre-2026-09-25 bug, where a "
     "/handoff in repo A suppressed the one in repo B",
     DEDUP_KEY, '"\\x1f".join((rec["kind"], rec["text"])).encode()',
     "test_dedup_is_scoped_PER_PROJECT_not_global"),
    ("D3", "narrowing", "text DROPPED — a project collapses to one row per kind",
     DEDUP_KEY, '"\\x1f".join((rec["project"], rec["kind"])).encode()',
     "test_dedup_keeps_two_DIFFERENT_texts_in_one_project"),

    # --- the arc chain --------------------------------------------------------
    ("A1", "deletion", "the coverage line is dropped from the arc notes",
     "             fs.handoff_arc.coverage_line(report)]", "             ]",
     "test_every_coverage_note_the_resolver_produced"),
    ("A2", "deletion", "the resolver's unmeasured_notes are dropped",
     "    notes.extend(report.unmeasured_notes)", "    pass",
     "test_every_coverage_note_the_resolver_produced"),
    ("A3", "replacement", "arc_role is never set on a row",
     'rec["arc_role"] = roles.get(sid)', 'rec["arc_role"] = None',
     "test_arc_role_lands_in_the_jsonl"),
    ("A4", "disable", "the member role is dropped from the markdown heading",
     "        if roles.get(sid):", "        if False:",
     "test_the_member_ROLE_reaches_the_markdown_heading"),
    ("A5", "replacement", "arc members never reach the id list — the imported "
     "resolver is called and its answer discarded",
     "    return list(report.members), notes", "    return [], notes",
     "test_the_resolver_is_IMPORTED_not_re_implemented"),

    # --- ordering and scope ---------------------------------------------------
    ("O1", "inversion", "undated rows sort FIRST, heading the chain with the "
     "one message nothing could place in it",
     '(r["ts"] == "", r["ts"])', '(r["ts"] != "", r["ts"])',
     "test_rows_are_chronological_and_undated_rows_sort_LAST"),
    ("O2", "widening", "a SCOPED run walks the whole corpus anyway",
     "        sources = resolved",
     "        sources = [(p.stem, p) for p in corpus_paths(root=root)]",
     "test_a_selected_session_extracts_only_that_session"),
    ("O3", "widening", "the walk stops being the shared enumerator and "
     "re-grows a private glob that keeps subagent transcripts",
     "    return list(iter_transcripts(root=root if root is not None else ROOT))",
     '    base = Path(root if root is not None else ROOT)\n'
     '    return sorted(base.glob("**/*.jsonl"))',
     "test_a_REAL_shaped_subagent_transcript_is_excluded"),

    # --- the broken-pipe guard ------------------------------------------------
    # 🔴 THIS ROW CAUGHT A MASKED REGRESSION, which is the argument for naming
    # a test per row. When the write-failure guard (`except OSError` -> exit 2)
    # landed beside the pipe guard, this mutant stopped producing a traceback —
    # BrokenPipeError SUBCLASSES OSError, so it fell through to the sibling and
    # exited 2 quietly. The named test's assertions (no traceback, head got a
    # row) all still held, so the row scored KILLED-WRONG-REASON instead of
    # silently passing. The test now asserts the EXIT CODE.
    ("P1", "replacement", "the except arm no longer catches BrokenPipeError, so "
     "the pipe falls through to the write-failure guard and `| head` exits 2",
     "    except BrokenPipeError:", "    except KeyboardInterrupt:",
     "test_a_closed_stdout_exits_quietly"),
    # 🔴 NAMES THE DETERMINISTIC GUARD, NOT THE FLAKY ONE. Pointed at
    # `test_a_closed_stdout_exits_quietly` this row was a coin flip across four
    # measurements (see the header); the structural pin kills it every time.
    ("P2", "deletion", "the dup2 silencer removed — CPython then retries the "
     "flush at shutdown and prints 'Exception ignored … BrokenPipeError'",
     "        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())\n",
     "", "test_the_shutdown_flush_is_silenced_by_redirecting_fd_1"),

    # --- the --help contract --------------------------------------------------
    ("H1", "deletion", "an exit code disappears from --help",
     "  4  --arc: the arc WAS measured and has zero member sessions\n", "",
     "test_every_exit_code_is_listed_in_the_help"),
    ("H2", "addition", "--help gains a code the tool never returns",
     "  6  transcripts WERE read",
     "  7  a code the tool never returns\n  6  transcripts WERE read",
     "test_the_help_lists_no_code_the_contract_does_not_define"),
    # 🔴 REMOVES BOTH EXAMPLES, and the first spelling removed one of two and
    # SURVIVED. That was the BATTERY being wrong, not a coverage gap: the test
    # asserts "at least one example per selector", which two examples minus one
    # still satisfies. A mutant must violate the contract the named test states,
    # not merely edit near it.
    ("H3", "deletion", "--session loses EVERY example in --help",
     "      extract_user_msgs.py --session 00000000-1111-4222-8333-444444444444\n"
     "      extract_user_msgs.py --session <id-a> --session <id-b>\n", "",
     "test_every_selector_has_at_least_one_example"),

    # --- the WRITE path, added round 3 ---------------------------------------
    # 🔴 THESE FOUR ROWS EXIST BECAUSE THE BATTERY GAINED NONE WHEN THE CODE
    # THEY COVER LANDED. An independent round-3 sweep found three reachable
    # mutants over that code surviving a fully green 93-test suite AND a 33/33
    # battery — the battery was evidence about the 33 rows it held, and the new
    # guards were not among them. W2 is the one that matters: without the close
    # guard a TOTAL write failure reported `rc 0 … out=<path>`.
    ("W1", "deletion", "the mid-write OSError arm is gone, so a failed write "
     "tracebacks at rc 1 again — the defect round 2 says it fixed",
     "    except OSError as exc:\n"
     "        # 🔴 THE WRITE, NOT JUST THE OPEN.",
     "    except _NeverRaisedW1 as exc:\n"
     "        # 🔴 THE WRITE, NOT JUST THE OPEN.",
     "test_the_mid_write_arm_reports_rather_than_tracebacks"),
    ("W2", "deletion", "the success-path close guard is gone, so a write that "
     "wrote NOTHING reports success",
     "    if a.out:\n        try:\n            out.close()\n"
     "        except OSError as exc:",
     "    if a.out:\n        try:\n            out.close()\n"
     "        except _NeverRaisedW2 as exc:",
     "test_a_write_that_fails_ENTIRELY_does_not_report_success"),
    ("W3", "replacement", "sessions= reports the SELECTION size again, not what "
     "was read",
     'print(f"sessions={opened} msgs={len(rows)} "',
     'print(f"sessions={len(sources)} msgs={len(rows)} "',
     "test_sessions_counts_what_was_READ"),
    ("W4", "deletion", "the arc-empty path stops flushing its coverage notes",
     "        if not members:\n            flush_notes()",
     "        if not members:\n            pass",
     "test_notes_reach_the_ARC_EMPTY_path"),

    # --- the routing prose (payload for this PR, not scaffolding) -------------
    ("R1", "deletion", "an exit row vanishes from the reference's table",
     "| 4 | `--arc` only: the doc resolved",
     "| 4x | `--arc` only: the doc resolved",
     "test_the_reference_documents_EVERY_exit_code"),
    ("R2", "deletion", "the reference loses its MEASURED anchor",
     "MEASURED 2026-09-25", "measured recently",
     "test_the_reference_carries_a_MEASURED_cost"),
    # 🔴 TWO BATTERY DEFECTS FIXED IN ONE ROW, both caught by running it.
    # (1) The first spelling anchored on the bare flag `--ids-file`, which
    #     occurs 3x in the reference (two runnable commands and the exit-2 table
    #     row); `test_every_mutation_anchor_occurs_exactly_once_in_its_target`
    #     refused it, because a 3x anchor mutates sites the row does not name.
    # (2) The second anchored on ONE of the two commands and SURVIVED — the
    #     named test asks for at least one runnable command per selector, and
    #     one of two still satisfies it. Both go.
    ("R3", "deletion", "--ids-file loses EVERY runnable command in the reference",
     "  | python3 $E --ids-file -\n"
     "\n"
     "# a file of ids; `#` comments and blank lines are skipped\n"
     "python3 $E --ids-file ./ids.txt\n",
     "  | python3 $E -\n",
     "test_the_reference_gives_a_RUNNABLE_command_for_every_selector"),
    ("R4", "deletion", "/resume's routing row loses the tool name",
     "extract_user_msgs.py --arc/--session/--ids-file", "the extractor",
     "test_resume_ROUTES_to_it"),
    # 🔴 THE WHOLE LINE, and the first spelling replaced only its FIRST CELL —
    # leaving the tool name and the reference path still on the row, so nothing
    # was actually removed and it SURVIVED. A "delete the row" mutant that
    # leaves the row is not a deletion.
    ("R5", "deletion", "/resume's routing row is removed entirely",
     "| You need what the OPERATOR actually typed across this arc — their "
     "words, not the doc's summary of them. `extract_user_msgs.py "
     "--arc/--session/--ids-file`, its exit codes, and why a 0 cannot mean "
     '"empty arc" | `~/.claude/skills/handoff/reference/user-messages.md` |\n',
     "", "test_resume_ROUTES_to_it"),
]

#: Which file each row rewrites. Declared per row, never per section: `R1`–`R3`
#: are prose in the reference and `R4`–`R5` prose in a different skill's body,
#: and a section-level mapping would put them in one bucket.
TARGETS = {
    **{mid: SRC for mid, *_ in MUTANTS},
    "R1": REF, "R2": REF, "R3": REF,
    "R4": SKILL, "R5": SKILL,
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_suite(tmp: Path) -> tuple[int, int, int, str]:
    """`(failed, errors, collected, output)` for one pytest run.

    🔴 `collected` is counted from the runner's own per-test lines, not from an
    exit code: a mutant that breaks the module emits `ERROR` at collection and
    ZERO `FAILED` lines, which reads exactly like a clean sweep.
    """
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    res = subprocess.run(
        [sys.executable, "-m", "pytest", str(SUITE), "-q", "--no-header",
         "-p", "no:cacheprovider", "-o", f"cache_dir={tmp}"],
        capture_output=True, text=True, env=env, cwd=REPO)
    out = res.stdout + res.stderr
    failed = sum(1 for ln in out.splitlines() if ln.startswith("FAILED "))
    errors = sum(1 for ln in out.splitlines()
                 if ln.startswith("ERROR ") or ln.startswith("E   "))
    collected = sum(1 for ln in out.splitlines()
                    if ln.startswith("FAILED ") or
                    (ln and ln[0] in ".FEsx" and "[" in ln and "%" in ln))
    return failed, errors, collected, out


def main() -> int:
    targets = sorted({TARGETS[mid] for mid, *_ in MUTANTS})
    backup = Path(tempfile.mkdtemp(prefix="mutbat-eum-"))
    pristine = {}
    for t in targets:
        shutil.copy2(t, backup / t.name)
        pristine[t] = _digest(t)

    def restore() -> None:
        for t in targets:
            shutil.copy2(backup / t.name, t)
            # 🔴 BY DIGEST. A restore that silently failed makes the NEXT
            # mutant land on the previous one's damage, and the test that dies
            # to the leftover is recorded as killing a mutant it never saw.
            assert _digest(t) == pristine[t], f"restore FAILED for {t}"

    rows, tmp = [], Path(tempfile.mkdtemp(prefix="mutbat-cache-"))
    try:
        for mid, kind, desc, old, new, expected in MUTANTS:
            restore()
            target = TARGETS[mid]
            text = target.read_text()
            n = text.count(old)
            if n != 1:
                rows.append((mid, "NOT-APPLIED",
                             f"pattern occurs {n}x in {target.name}, want 1"))
                continue
            mutated = text.replace(old, new, 1)
            if "_NeverRaisedW" in new:
                mutated = mutated.replace("import argparse",
                                          _PRELUDE + "import argparse", 1)
            target.write_text(mutated)
            assert _digest(target) != pristine[target], f"{mid}: no-op write"

            failed, errors, collected, out = _run_suite(tmp)
            if mid == "C0":
                # The control also validates the BASELINE: run it clean first.
                restore()
                b_failed, _, b_collected, _ = _run_suite(tmp)
                if b_collected == 0:
                    print("ABORT: the baseline run collected NOTHING")
                    return 2
                if b_failed:
                    print(f"ABORT: the baseline is RED ({b_failed} failing)")
                    return 2
                target.write_text(text.replace(old, new, 1))
                failed, errors, collected, out = _run_suite(tmp)

            if collected == 0 and errors:
                rows.append((mid, "INVALID", "the suite never collected"))
            elif failed == 0:
                rows.append((mid, "SURVIVED", "suite collected and stayed green"))
            elif any(ln.startswith("FAILED ") and expected in ln
                     for ln in out.splitlines()):
                rows.append((mid, "KILLED", f"by {expected} ({failed} failing)"))
            else:
                got = [ln.split("::")[-1] for ln in out.splitlines()
                       if ln.startswith("FAILED ")][:3]
                rows.append((mid, "KILLED-WRONG-REASON",
                             f"want {expected}, got {' '.join(got)}"))
    finally:
        restore()
        shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)

    width = max(len(m) for m, *_ in MUTANTS)
    for mid, verdict, note in rows:
        print(f"{mid:<{width}}  {verdict:<20}  {note}")
    bad = [r for r in rows if r[1] != "KILLED"]
    print(f"\n{len(rows) - len(bad)} of {len(rows)} KILLED by the named test")
    if bad:
        print("🔴 NOT A CLEAN SWEEP: " + ", ".join(f"{m} {v}" for m, v, _ in bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
