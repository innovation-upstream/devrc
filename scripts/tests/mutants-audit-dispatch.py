#!/usr/bin/env python3
"""Mutation battery for `scripts/audit-dispatch.py` — the audit-brief assembler.

    nix develop ~/workspace/devrc -c python3 scripts/tests/mutants-audit-dispatch.py

Not run by CI. An author/reviewer instrument, kept IN THE TREE so
"mutation-verified" can be RE-DERIVED instead of believed — the same reason
`scripts/tests/mutants-audit-ladder.sh` exists, and it records the incident:
devrc #900 ran ten rounds and recorded ~30 mutants in a module docstring, every
one of them inside a session scratchpad that no longer exists, so not a single
row could be re-checked by anyone else. This follows its conventions.

🔴 EACH MUTANT NAMES THE EXACT SET OF TESTS THAT MUST KILL IT — not "a test
failed". These pins overlap by design (a clause deletion trips the id ledger,
the fragment ledger and the rendered-section comparison), so a mutant can die to
a DIFFERENT test's error and be scored as covered while its own assertion is
unreachable. Two distinct failures are reported separately because the responses
differ:
  * an expected killer ABSENT   -> 🔴 WRONG-KILLER: that pin is DEAD.
  * an UNEXPECTED extra killer  -> 🔴 EXTRA-KILLER: something else now fires;
                                   the row no longer isolates what it names.
Collapsing them into one comparison routes the operator away from the first,
which is the failure this battery exists to catch.

🔴 EACH MUTATION IS ASSERTED TO APPLY BEFORE IT RUNS. A target string that no
longer matches — a line re-wrapped, a word changed — leaves the file UNMUTATED
and reports "the guard held", the most flattering possible wrong answer.

🔴 IT NEVER TOUCHES YOUR WORKING TREE. Everything happens in a `mktemp` copy
built by naming TWO INDIVIDUAL FILES. That selective copy, not the assertion
below it, is what keeps a `.git` out; the assertion is an INVARIANT GUARD
against the future refactor that replaces the file list with `cp -a`, which
would carry a worktree's `.git` POINTER FILE and let a git command inside the
copy act on the real repository.

🔴 PYTHONDONTWRITEBYTECODE=1, and `-p no:cacheprovider`. A stale `.pyc` keyed on
mtime-in-whole-seconds plus size is how a same-length edit gets scored SURVIVED
without ever executing.

🔴 THE BASELINE IS THE POSITIVE CONTROL. If the unmutated copy is not green,
every row below is meaningless and this exits 2 without running any of them —
and it distinguishes "the tree is red" from "pytest is not on PATH", because the
second one blames the tree for the shell you are in.

COVERAGE IS DELIBERATELY PARTIAL. What is here: one deletion per invariant
clause, an addition, one REWORD that leaves the clause present (the reachability
control for the fragment ledger), one renderer bypass, and one inversion per
generated decision. What is NOT here: the `gh`/`git` boundary itself (the suite
injects a runner and says so), and whether a human classifies the ledger's files
correctly (nothing mechanical can grade that).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT_REL = "scripts/audit-dispatch.py"
TEST_REL = "scripts/tests/test_audit_dispatch.py"

# 🔴 A COLLAPSE floor, not a growth floor — same rule as
# `mutants-audit-ladder.sh`. A suite that never ran yields zero FAILED lines,
# i.e. "clean", so a harness wired to nothing would score every mutant SURVIVED.
MIN_TESTS = 30


# --------------------------------------------------------------------------- #
# Mutations. Each takes the script source and returns it changed, or raises.
# --------------------------------------------------------------------------- #

def drop_clause(cid):
    def f(t):
        pat = re.compile(
            r"    Clause\(\n        \"" + re.escape(cid) + r"\",\n(?:.*?\n)*?    \),\n"
        )
        new, n = pat.subn("", t, count=1)
        if n != 1:
            raise AssertionError(f"clause {cid!r} not found in its expected shape")
        return new
    return f


def _swap(t, old, new):
    if old not in t:
        raise AssertionError(f"target absent: {old[:70]!r}…")
    return t.replace(old, new, 1)


def add_unledgered_clause(t):
    marker = ")\n\nINVARIANTS_HEADING"
    return _swap(t, marker,
                 '    Clause("unledgered", "**An eighth instruction nobody '
                 'reviewed.**"),\n' + marker)


def reword_stop_rule(t):
    return _swap(
        t,
        '"**A clean round ENDS the ladder — ending it is the CORRECT outcome, "\n'
        '        "not a failure.** ',
        '"**A clean round ends the ladder.** ',
    )


def hardcode_bullets(t):
    return _swap(
        t,
        '    out += [f"- {c.text}" for c in INVARIANT_CLAUSES]',
        '    out += ["- **READ-ONLY.**", "- **Do not merge.**"]',
    )


def remove_the_refusal(t):
    return _swap(t,
                 "    if args.round_no >= 2 and newest is None:",
                 "    if False:")


def claims_from_the_whole_comment(t):
    return _swap(
        t,
        "    claims = list(newest.items) if newest else []",
        '    claims = ([ln for ln in (comment_texts[0] if comment_texts else "")'
        ".splitlines() if ln.strip()] if newest else [])",
    )


def invert_cross_repo(t):
    return _swap(t,
                 "cross_repo=bool(cwd_slug and repo and cwd_slug != repo),",
                 "cross_repo=bool(cwd_slug and repo and cwd_slug == repo),")


def numstat_failure_reads_zero(t):
    return _swap(
        t,
        '    if rc != 0:\n'
        '        return fail(f"the numstat command exited {rc}: "\n'
        '                    f"{(err or out).strip() or \'no output\'}")\n',
        '    if rc != 0:\n        out = ""\n',
    )


def classify_by_pathspec(t):
    return _swap(
        t,
        '        f"round {facts.round_no} · payload lines changed THIS round: X "\n',
        '        f"round {facts.round_no} · payload lines changed THIS round: "\n'
        "        f\"{sum(a + d for p, (a, d) in led.files.items() "
        "if 'test' not in p)} \"\n",
    )


def the_warning_blocks(t):
    old = ('              "emitted. Re-add them by hand, or re-run without --out edits.",\n'
           "            file=err_stream,\n"
           "        )\n")
    return _swap(t, old, old + "        return 4\n")


LEDGER_TRIO = {
    "test_the_invariant_clause_ledger_is_pinned_two_way",
    "test_each_clause_carries_the_instruction_its_ledger_entry_names",
    "test_the_rendered_section_holds_exactly_the_ledgered_clauses",
}
DELETION_CONTROL = "test_control_a_clause_deleted_from_the_constant_is_detected"

# (label, expected killer set, mutation)
ROWS = [
    ("D1  delete clause read-only",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("read-only")),
    # 🔴 D2 is the one asymmetric row, and it is recorded rather than tidied
    # away: `test_missing_clause_check_warns_and_never_blocks` looks the
    # `no-fetch` clause up BY ID, so deleting that particular clause makes it
    # error. A real coupling, and the reason the deleted-clause CONTROL is a
    # separate test from the warn test.
    ("D2  delete clause no-fetch",
     LEDGER_TRIO | {"test_missing_clause_check_warns_and_never_blocks"},
     drop_clause("no-fetch")),
    ("D3  delete clause stop-rule",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("stop-rule")),
    ("D4  delete clause nit-is-not-a-finding",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("nit-is-not-a-finding")),
    ("D5  delete clause reverify-self-reported",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("reverify-self-reported")),
    ("D6  delete clause finding-format",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("finding-format")),
    ("D7  delete clause do-not-merge",
     LEDGER_TRIO | {DELETION_CONTROL}, drop_clause("do-not-merge")),
    ("A1  add an unledgered eighth clause",
     {"test_the_invariant_clause_ledger_is_pinned_two_way",
      "test_the_rendered_section_holds_exactly_the_ledgered_clauses",
      DELETION_CONTROL},
     add_unledgered_clause),
    # 🔴 THE REACHABILITY CONTROL. The clause survives and is still emitted, so
    # every presence pin stays GREEN; only the FRAGMENT ledger can see it. A
    # WRONG-KILLER here means that ledger is dead code.
    ("R1  reword stop-rule (clause still present)",
     {"test_each_clause_carries_the_instruction_its_ledger_entry_names"},
     reword_stop_rule),
    ("F1  render bullets from a hardcoded copy",
     {"test_the_rendered_section_holds_exactly_the_ledgered_clauses",
      "test_every_clause_is_emitted_verbatim_in_both_kinds_of_brief",
      DELETION_CONTROL},
     hardcode_bullets),
    ("C1  the delta refusal removed",
     {"test_the_delta_refusal_exits_non_zero_and_emits_no_brief",
      "test_the_refusal_names_what_it_looked_for_and_where",
      "test_the_refusal_fires_for_a_malformed_block_and_says_which_way"},
     remove_the_refusal),
    ("C2  claims read from the whole comment",
     {"test_the_brief_carries_the_claims_and_not_the_reasoning_around_them",
      "test_the_newest_claims_block_wins",
      "test_emit_claims_prints_a_block_this_scripts_own_parser_accepts"},
     claims_from_the_whole_comment),
    ("C3  cross-repo decision inverted",
     {"test_cross_repo_tells_the_agent_to_worktree_the_prs_repo_itself",
      "test_same_repo_recommends_the_isolation_flag_and_does_not_hand_roll",
      "test_the_cross_repo_decision_comes_from_the_repos_not_from_prose"},
     invert_cross_repo),
    ("C4  numstat failure reads as a clean zero",
     {"test_the_ledger_refuses_a_failed_command_rather_than_printing_zero"},
     numstat_failure_reads_zero),
    ("C5  the ledger classifies by pathspec",
     {"test_the_ledger_shows_the_files_and_refuses_to_classify_them"},
     classify_by_pathspec),
    ("C6  the missing-clause warning BLOCKS",
     {"test_missing_clause_check_warns_and_never_blocks"},
     the_warning_blocks),
]


def failing(root: Path):
    """-> (killer test names, raw output). Reads the CONTENT, never an exit code."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(root / TEST_REL),
         "-q", "--no-header", "--tb=no", "-p", "no:cacheprovider"],
        capture_output=True, text=True, env=env, cwd=str(root), check=False,
    )
    # stderr is CAPTURED, not discarded: the commonest way to get "0 tests ran"
    # on this host is running outside `nix develop` (`.envrc` is `use opencode`,
    # so a loaded direnv has no pytest), and discarding it turns that one-line
    # diagnosis into a headline that blames the TREE.
    out = p.stdout + p.stderr
    if "No module named pytest" in out:
        return None, ("pytest is not on PATH — run this as `nix develop "
                      "~/workspace/devrc -c python3 scripts/tests/"
                      "mutants-audit-dispatch.py`, not in a bare shell")
    passed = sum(int(n) for n in re.findall(r"(\d+) passed", out))
    failed = sum(int(n) for n in re.findall(r"(\d+) failed", out))
    if passed + failed < MIN_TESTS:
        return None, (f"only {passed + failed} test(s) ran (floor {MIN_TESTS}) — "
                      f"the harness, not the tree:\n{out[-800:]}")
    return set(re.findall(r"^FAILED [^:]*::([A-Za-z0-9_]+)", out, re.M)), out


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="audit-dispatch-mut-"))
    try:
        root = tmp / "tree"
        (root / "scripts" / "tests").mkdir(parents=True)
        shutil.copy(REPO / SCRIPT_REL, root / SCRIPT_REL)
        shutil.copy(REPO / TEST_REL, root / TEST_REL)
        if (root / ".git").exists():
            print("🔴 the copy carries a .git — refusing to run")
            return 2
        original = (root / SCRIPT_REL).read_text(encoding="utf-8")

        killers, raw = failing(root)
        if killers is None:
            print(f"🔴 THE HARNESS could not run: {raw}")
            print("   Nothing was measured. This says nothing about the tree.")
            return 2
        if killers:
            print("🔴 the UNMUTATED tree is already failing: "
                  + ", ".join(sorted(killers)))
            print("   every row below would be meaningless. Fix that first.")
            return 2
        n = sum(int(x) for x in re.findall(r"(\d+) passed", raw))
        print(f"POS  unmutated copy .......................... {n} passed")
        print()

        bad = 0
        for label, want, mutate in ROWS:
            try:
                mutated = mutate(original)
            except AssertionError as e:
                print(f"  🔴 {label:44s} MUTATION DID NOT APPLY — {e}")
                bad += 1
                continue
            if mutated == original:
                print(f"  🔴 {label:44s} MUTATION DID NOT APPLY (no change)")
                bad += 1
                continue
            (root / SCRIPT_REL).write_text(mutated, encoding="utf-8")
            got, raw2 = failing(root)
            (root / SCRIPT_REL).write_text(original, encoding="utf-8")

            if got is None:
                print(f"  🔴 {label:44s} HARNESS BROKE — {raw2}")
                bad += 1
                continue
            if not got:
                print(f"  🔴 {label:44s} SURVIVED — no test failed")
                bad += 1
                continue
            missing = want - got
            extra = got - want
            if missing:
                print(f"  🔴 {label:44s} WRONG-KILLER: {sorted(missing)} did NOT "
                      f"fire (got {sorted(got)})")
                bad += 1
                continue
            if extra:
                print(f"  🔴 {label:44s} EXTRA-KILLER: {sorted(extra)} also fired "
                      "— the row no longer isolates what it names")
                bad += 1
                continue
            print(f"  ok {label:44s} killed by exactly {len(got)}: "
                  + ", ".join(sorted(got)))

        print()
        if bad:
            print(f"🔴 {bad} of {len(ROWS)} row(s) not as expected")
            return 1
        print(f"✅ {len(ROWS)} row(s), all as expected")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
