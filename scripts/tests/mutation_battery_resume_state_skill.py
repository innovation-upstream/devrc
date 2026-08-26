#!/usr/bin/env python3
"""Mutation battery for the SKILL-freshness block in `scripts/resume-state.sh`.

    python3 scripts/tests/mutation_battery_resume_state_skill.py

🔴 NOT COLLECTED BY THE GATE, ON PURPOSE — the filename is the mechanism, the
same as its sibling `mutation_battery_resume_state.py`: `scripts/run-tests.sh`
collects `test_*.py` only. This rewrites a TRACKED file in place once per
mutant, so it is a manual instrument, not something two agents can run at once.
It is committed because a mutation result quoted from an instrument nobody else
can run is a claim, not evidence.

🔴 WHAT THIS ONE ADDS OVER THE SIBLING: it does not accept "a test failed" as a
kill. Each mutant names the SPECIFIC text its own guard produces, and the mutant
counts as killed only when that text appears in the failure output. "I broke it
and something went red" stays green for the wrong reason when an earlier check
always wins, when a DIFFERENT guard's message kills the test, or when the happy
path resolves anyway — all three are live shapes in a block that is one long
chain of early returns. A mutant that goes red for the wrong reason is reported
as KILLED-WRONG-REASON, which is a FAILURE of this battery, not a pass.

READ BEFORE TRUSTING A VERDICT:
  * The CONTROL runs first and aborts on a red OR EMPTY baseline. A zero is
    indistinguishable from a probe wired to nothing until something makes the
    number move.
  * A mutant whose pattern is NOT FOUND is reported as such and counted as a
    survivor — silent non-application is how a battery reports a clean sweep of
    mutations it never made.
  * The subject is BASH, so there is no bytecode cache to invalidate; the
    suite is still run with PYTHONDONTWRITEBYTECODE=1 so the harness cannot
    import a stale copy of its own helpers.
  * The script is restored in a `finally`. Check `git diff` anyway if it dies
    hard.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/resume-state.sh"
SUITE = "scripts/tests/test_resume_state_skill_freshness.py"

# (id, guard, description, old, new, expected-text-in-the-failure)
#
# `expected` is the phrase THIS guard is responsible for. It must show up in the
# failure output — pytest prints the asserted line and its message with
# --tb=short — or the kill does not count.
MUTANTS: list[tuple[str, str, str, str, str, str]] = [
    # ---- the block exists and is wired into main -------------------------
    ("S1", "wiring", "the SKILL block is never called",
     "  skill_block\n", "  :\n",
     "no skill-read: line at all"),
    # 🔴 ONE SITE. An earlier revision spelled the expansion twice and the
    # SECOND copy's mutant SURVIVED — the guard above always returned first, so
    # nothing could observe it. The fix was in the SUBJECT (a single `want`
    # local), not in the battery: an unobservable predicate is a defect.
    ("S2", "default", "the default becomes 'check nothing' (`:-` instead of `-`)",
     '  local want="${RESUME_STATE_SKILL-resume}"',
     '  local want="${RESUME_STATE_SKILL:-}"',
     "NO skill was checked"),
    ("S3", "default", "an explicitly-empty override silently checks /resume anyway",
     '  local want="${RESUME_STATE_SKILL-resume}"',
     '  local want="${RESUME_STATE_SKILL:-resume}"',
     "NO skill was checked"),

    # ---- COULD-NOT-MEASURE guards: each must stay its OWN message ---------
    # ⚠ The expectation is this guard's OWN wording, not the shared
    # "COULD NOT MEASURE" banner: with the guard removed, the readlink branch
    # below fires and prints a DIFFERENT could-not-measure line, which the
    # banner would happily match. That looser expectation was measured passing
    # for the wrong reason.
    ("S4", "absent deploy", "a missing deployed copy is passed over silently",
     '  if [ ! -e "$dep" ] && [ ! -L "$dep" ]; then\n',
     '  if false; then\n',
     "no deployed copy at"),
    ("S5", "dangling link", "`-e` alone again: a dangling link reads as never-deployed",
     '  if [ ! -e "$dep" ] && [ ! -L "$dep" ]; then',
     '  if [ ! -e "$dep" ]; then',
     "resolves nowhere"),
    ("S6", "dangling link", "the readlink resolution test is dropped",
     '  if [ -z "$real" ] || [ ! -f "$real" ]; then\n',
     '  if false; then\n',
     "resolves nowhere"),
    ("S7", "no checkout", "no source checkout is treated as a pass",
     '  if [ -z "$d" ]; then\n    printf \'  skill-read: %s — COULD NOT MEASURE (no git checkout of the skill source found; %s)\\n\' "$name" "$prov"\n',
     '  if false; then\n    printf \'  skill-read: %s — COULD NOT MEASURE (no git checkout of the skill source found; %s)\\n\' "$name" "$prov"\n',
     "no git checkout of the skill source found"),
    ("S8", "no origin", "a checkout with no origin remote is treated as a pass",
     '  if ! git -C "$d" remote get-url origin >/dev/null 2>&1; then\n    printf \'  skill-read: %s — COULD NOT MEASURE (%s has no origin remote; %s)\\n\'',
     '  if false; then\n    printf \'  skill-read: %s — COULD NOT MEASURE (%s has no origin remote; %s)\\n\'',
     "has no origin remote"),
    ("S9", "absent operand", "the cat-file existence probe is dropped — the "
     "`diff --quiet` trap: absent on BOTH sides reports SAME",
     '  if ! git -C "$d" cat-file -e "$(printf \'%s:%s\' "$ref" "$rel")" 2>/dev/null; then\n'
     '    printf \'  skill-read: %s%s — COULD NOT MEASURE (%s is not on %s; %s)\\n\'',
     '  if false; then\n'
     '    printf \'  skill-read: %s%s — COULD NOT MEASURE (%s is not on %s; %s)\\n\'',
     "is not on origin/main"),

    # ---- the measurement itself ------------------------------------------
    ("S10", "verdict", "everything is reported CURRENT (the hardcoded all-clear)",
     '  if [ "$dep_hash" = "$tip_hash" ]; then',
     '  if true; then',
     "BEHIND origin/main"),
    ("S11", "verdict", "nothing is ever CURRENT (the hardcoded alarm)",
     '  if [ "$dep_hash" = "$tip_hash" ]; then',
     '  if false; then',
     "CURRENT with origin/main"),
    ("S12", "size", "the distance is not counted — every stale copy reads as 1 behind",
     "    behind=$((behind+1))\n",
     "    behind=1\n",
     "2 commit(s) BEHIND origin/main"),
    ("S13", "size", "the walk stops at the first commit, so nothing is ever found",
     '    if [ "$h" = "$dep_hash" ]; then found="$c"; break; fi\n',
     '    if false; then found="$c"; break; fi\n',
     "matches NO commit"),
    ("S14", "direction", "the newest-lacked commit is dropped from the line",
     "  newest=$(git -C \"$d\" log -1 --format='%h %s' \"$ref\" -- \"$rel\" 2>/dev/null)",
     '  newest=""',
     "newest it lacks"),
    ("S15", "cap", "the capped walk claims a clean answer",
     '    [ -n "$capped" ] && how="is older than the newest $cap commit(s) touching $rel on $ref (scan capped)"',
     "    :",
     "scan capped"),

    # ---- the live/store fork (readlink -f is the arbiter) -----------------
    ("S16", "arbiter", "the working tree is compared instead of the DEPLOYED copy",
     '  dep_hash=$(git -C "$d" hash-object -- "$real" 2>/dev/null)',
     '  dep_hash=$(git -C "$d" hash-object -- "$d/$rel" 2>/dev/null)',
     "BEHIND origin/main"),
    ("S17", "arbiter", "every deployed copy is labelled a live working-tree copy",
     '  if [ "$live" -eq 1 ]; then\n    prov="live working-tree copy at $real"',
     '  if true; then\n    prov="live working-tree copy at $real"',
     "store copy at"),
    ("S18", "wip fork", "uncommitted local edits are called STALE",
     '  if [ "$live" -eq 1 ] && ! git -C "$d" diff --quiet -- "$rel" 2>/dev/null; then\n',
     '  if false; then\n',
     "UNCOMMITTED"),
    ("S19", "wip fork", "a store copy is swept into the uncommitted-edits branch",
     '  if [ "$live" -eq 1 ] && ! git -C "$d" diff --quiet -- "$rel" 2>/dev/null; then',
     '  if ! git -C "$d" diff --quiet -- "$rel" 2>/dev/null; then',
     "expected the store copy to read BEHIND"),

    # ---- the DRIFT/GAP contract -----------------------------------------
    #
    # 🔴 THE MUTATION MUST STAY VALID BASH. `: ("…")` is a PARSE ERROR — `(`
    # opens a subshell — so it broke the whole script, every test failed, and
    # the failure output quoted the source line, which contains the very phrase
    # this battery was looking for. Both mutants scored a confident KILLED
    # having tested nothing. Appending to an unused array removes the effect
    # while leaving the file parseable.
    ("S20", "drift", "the stale finding never reaches DRIFT (a report with no finding)",
     '    DRIFT+=("the /$name skill THIS SESSION IS EXECUTING is STALE:',
     '    _DROPPED+=("the /$name skill THIS SESSION IS EXECUTING is STALE:',
     "THIS SESSION IS EXECUTING is STALE"),
    ("S21", "gap", "a could-not-measure prints its line but records NO gap, so the "
     "digest still issues a clean bill of health",
     '    UNRECONCILED+=("the /$name skill has no deployed copy at $dep,',
     '    _DROPPED+=("the /$name skill has no deployed copy at $dep,',
     "/resume skill …' GAP"),

    # ---- the fetch (staleness is invisible without it) --------------------
    # Only observable when the skill repo is NOT the repo being resumed —
    # otherwise `handoff_freshness` has already fetched it and the memo answers.
    # That fixture had to be written before this mutant could be scored.
    ("S22", "fetch", "the fetch is skipped, so the stale local ref answers instead",
     "  local pre=\"\"\n  bounded_fetch \"$d\"\n",
     "  local pre=\"\"\n  RESUME_STATE_SKIP_FETCH=1 bounded_fetch \"$d\"\n",
     "the skill repo was not fetched"),

    # ---- POSITIVE CONTROL -------------------------------------------------
    # A mutant nobody doubts, kept in the batch so a run that scores everything
    # KILLED still has one result whose correctness is obvious at a glance. If
    # THIS one ever survives, the battery is measuring nothing and no other row
    # on the sheet means anything.
    ("PC", "control", "the whole SKILL block prints a fixed all-clear",
     '  echo "SKILL"\n',
     '  echo "SKILL"\n  echo "  skill-read: resume — deployed copy is CURRENT with origin/main (nonsense)"; return\n',
     "BEHIND origin/main"),
]


def run_suite() -> tuple[int, int, str]:
    # 🔴 PREPEND to $PYTHONPATH, never REPLACE it. A nix-shell hands pytest to
    # the interpreter THROUGH $PYTHONPATH, so overwriting it makes every run
    # die with "No module named pytest" — which this battery's own regexes read
    # as "0 passed, 0 failed", i.e. a control that aborts (caught) or, without
    # the abort guard, a clean sweep of mutations that never executed.
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            p for p in (str(ROOT / "scripts"), os.environ.get("PYTHONPATH", "")) if p
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    r = subprocess.run(
        [sys.executable, "-m", "pytest", SUITE, "-p", "no:cacheprovider",
         "--tb=short", "-q"],
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=1800,
    )
    out = r.stdout + r.stderr
    nfail = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
    npass = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    return nfail, npass, out


def main() -> int:
    orig = SCRIPT.read_text(encoding="utf-8")
    bad: list[str] = []
    try:
        nf, np_, _ = run_suite()
        print(f"CONTROL (pristine): {np_} passed, {nf} failed")
        if nf or np_ < 15:
            print("ABORT — baseline is red or collected almost nothing; no verdict "
                  "below would mean anything")
            return 1

        for mid, guard, desc, old, new, expected in MUTANTS:
            n = orig.count(old)
            if n != 1:
                print(f"{mid:4} {guard:14} !! PATTERN OCCURS {n}x — NOT APPLIED — {desc}")
                bad.append(f"{mid}(not-applied)")
                continue
            SCRIPT.write_text(orig.replace(old, new), encoding="utf-8")
            nf, _np, out = run_suite()
            if not nf:
                verdict = "SURVIVED"
                bad.append(f"{mid}(survived)")
            elif expected in out:
                verdict = "KILLED"
            else:
                verdict = "KILLED-WRONG-REASON"
                bad.append(f"{mid}(wrong-reason)")
            print(f"{mid:4} {guard:14} {verdict:20} f={nf:<3} {desc}")
            if verdict == "KILLED-WRONG-REASON":
                print(f"     expected {expected!r} in the failure output; not found")
        print(f"\n{len(MUTANTS) - len(bad)}/{len(MUTANTS)} killed for the RIGHT "
              f"reason; problems: {bad or 'none'}")
        return 1 if bad else 0
    finally:
        SCRIPT.write_text(orig, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
