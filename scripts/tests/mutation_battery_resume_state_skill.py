#!/usr/bin/env python3
"""Mutation battery for the SKILL-freshness block in `scripts/resume-state.sh`.

    python3 scripts/tests/mutation_battery_resume_state_skill.py

🔴 NOT COLLECTED BY THE GATE, ON PURPOSE — the filename is the mechanism, the
same as its sibling `mutation_battery_resume_state.py`: `scripts/run-tests.sh`
collects `test_*.py` only. This rewrites a TRACKED file in place once per
mutant, so it is a manual instrument, not something two agents can run at once.
It is committed because a mutation result quoted from an instrument nobody else
can run is a claim, not evidence.

🔴 WHAT THIS ONE ADDS OVER THE SIBLING — AND EXACTLY WHAT IT PROVES. It does not
accept "a test failed" as a kill. Each mutant names a phrase from the ASSERTION
MESSAGE of the test that owns that guard's behaviour, and counts as killed only
when the phrase appears in pytest's rendered `E ` lines.

Be precise about the strength of that, because the sentence here used to
overstate it: this proves **the test carrying that phrase is the one that went
red** — NOT "the guard emitted its own text". The phrase is the TEST's wording,
and several of these mutants make the guard emit nothing at all, which is the
whole defect. Test-level attribution is still the property worth buying, because
what it rules out is live in this file: an earlier check in a long chain of
early returns always winning, a DIFFERENT guard's message killing the test, or
the happy path resolving anyway. It does NOT rule out two tests sharing wording.
Where a guard does emit text the messages carry the real line (`got: …`), so the
guard's output is in the `E ` lines too — but the MATCH is on the test's phrase,
and the claim above is all that match licenses. A mutant that goes red for the
wrong reason is reported as KILLED-WRONG-REASON, a FAILURE of this battery.

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
#
# 🔴 `old`/`new` MAY BE TUPLES OF EQUAL LENGTH, applied in order, each required
# to occur exactly once. That exists because a faithful mutation is not always
# one edit: restoring a pre-fix shape can mean REMOVING code in one place and
# ADDING it in another, dozens of lines apart. Expressing only half of it and
# calling it "the pre-fix shape" is a false label on the one instrument whose
# entire purpose is producing quotable evidence — see R1.
MUTANTS: list[tuple[str, str, str, str | tuple, str | tuple, str]] = [
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
    # ⚠ S15 was rewritten when the capped branch was split out of the
    # matchless one. The ORIGINAL pattern no longer exists, and the battery said
    # so — `!! PATTERN OCCURS 0x — NOT APPLIED`, counted as a survivor rather
    # than passed over. That report is the only reason this row was not silently
    # measuring nothing; keep the not-applied check.
    ("S15", "cap", "the capped flag is ignored, so a budget-limited walk is "
     "reported as if the content matched no commit",
     '  elif [ -n "$capped" ]; then',
     '  elif false; then',
     "expected the capped sentence"),

    # ---- the live/store fork (readlink -f is the arbiter) -----------------
    ("S16", "arbiter", "the working tree is compared instead of the DEPLOYED copy",
     '  dep_hash=$(git -C "$d" hash-object -- "$real" 2>/dev/null)',
     '  dep_hash=$(git -C "$d" hash-object -- "$d/$rel" 2>/dev/null)',
     "BEHIND origin/main"),
    ("S17", "arbiter", "every deployed copy is labelled a live working-tree copy",
     '  if [ "$live" -eq 1 ]; then\n    prov="the live working-tree copy at $real"',
     '  if true; then\n    prov="the live working-tree copy at $real"',
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

    # ---- THE REVIEW ROUND'S FIXES ----------------------------------------
    # 🔴 AN AUDIT FIX RESETS THE VERIFICATION GATE. Every one of these is a
    # change made in response to a review, so each gets a mutant of its own and
    # the WHOLE battery is re-run — not just the row for what moved.
    # ⚠ R1 RESTORES THE ORIGINAL DEFECT, IN BOTH HALVES — and it took two
    # attempts to make that true. The pre-fix shape (still readable at
    # `origin/main:scripts/resume-state.sh`, the DRIFT block of `main`) is: NO
    # unconditional print, AND the `echo` living inside the
    # `elif [ -z "$HANDOFF" ]` arm, where any finding shadows it. Two earlier
    # revisions did only the first half — a pure deletion, so the mutant never
    # printed the notice at all, which is STRICTLY STRONGER than the bug — while
    # the description claimed the pre-fix shape had been restored. The result on
    # the sheet was right both times; the LABEL was false, on the one instrument
    # whose stated purpose is making mutation results quotable as evidence.
    ("R1", "no-handoff", "the notice goes back under the findings branch, so any "
     "finding suppresses it — the exact pre-fix shape, both halves",
     ('  if [ -z "$HANDOFF" ]; then\n'
      '    echo "  (no handoff loaded — nothing to reconcile; this is NOT a clean bill of health)"\n'
      '  fi\n',
      "    # still print the gaps.\n    print_gaps"),
     ('',
      "    # still print the gaps.\n"
      '    echo "  (no handoff loaded — nothing to reconcile; this is NOT a clean bill of health)"\n'
      "    print_gaps"),
     "a finding suppressed the notice"),
    ("R1b", "no-handoff", "the notice is ALSO printed from the elif arm, so a "
     "no-handoff run with no finding prints it TWICE",
     "    # still print the gaps.\n    print_gaps",
     "    # still print the gaps.\n"
     '    echo "  (no handoff loaded — nothing to reconcile; this is NOT a clean bill of health)"\n'
     "    print_gaps",
     "the notice must appear exactly once"),
    ("R2", "no-handoff", "the notice prints even when a handoff WAS reconciled "
     "— the mirror-image lie",
     '  if [ -z "$HANDOFF" ]; then\n'
     '    echo "  (no handoff loaded — nothing to reconcile; this is NOT a clean bill of health)"\n'
     '  fi\n',
     '  if true; then\n'
     '    echo "  (no handoff loaded — nothing to reconcile; this is NOT a clean bill of health)"\n'
     '  fi\n',
     "claimed nothing was reconciled"),
    ("R3", "cap boundary", "the scan cap is off by one (`-gt` -> `-ge`), stopping "
     "the walk one commit early",
     '    if [ "$scanned" -gt "$cap" ]; then capped=1; break; fi',
     '    if [ "$scanned" -ge "$cap" ]; then capped=1; break; fi',
     "the match sits exactly ON the cap"),
    ("R4", "capped cause", "the capped branch asserts the cause it never measured",
     '    DRIFT+=("the /$name skill THIS SESSION IS EXECUTING $how — the deployed copy at $dep is NOT current,',
     '    DRIFT+=("the /$name skill THIS SESSION IS EXECUTING $how — it was built from a '
     'tree that was never pushed; the deployed copy at $dep is NOT current,',
     "never pushed"),
    ("R5", "hedge", "the matchless branch drops the hedge and re-asserts one cause",
     "it may be uncommitted, on a branch that has not merged, or older than a rename of this path (the walk has no --follow)",
     "it was built from a tree that was never pushed",
     "never pushed"),
    ("R6", "timeout", "the fetch is no longer bounded — a hung remote hangs the resume",
     '      timeout 25 git -C "$d" fetch --quiet origin >/dev/null 2>&1 || rc=1',
     '      git -C "$d" fetch --quiet origin >/dev/null 2>&1 || rc=1',
     "an unbounded `git fetch`"),
    ("R7", "memo", "the memo stores SUCCESS for a fetch that failed",
     "  FETCH_RC[$d]=$rc\n",
     "  FETCH_RC[$d]=0\n",
     "the memo answered 0 for a fetch that FAILED"),
    ("R8", "order", "the SKILL block no longer leads the digest",
     "  skill_block\n  git_pr_block\n",
     "  git_pr_block\n  skill_block\n",
     "must LEAD the digest"),
    ("R9", "provenance", "the three-way label collapses back to two, telling a "
     "foreign file that a switch will replace it",
     '        prov="an UNMANAGED file at $real"\n'
     '        prov_note="neither a checkout nor /nix/store, so home-manager will NOT replace it — home.file.force does not clobber a foreign file, so remove it and re-switch" ;;',
     '        prov="a store copy at $real"\n'
     '        prov_note="only a home-manager switch replaces it" ;;',
     "expected UNMANAGED provenance"),
    ("R10", "cap input", "a non-integer cap is no longer validated, so `[` writes "
     "to stderr once per commit and the cap silently stops applying",
     '  case "$cap" in\n'
     "    ''|*[!0-9]*|0*)\n",
     '  case "$cap" in\n'
     "    '__never_matches__')\n",
     "integer expected"),
    ("R12", "cap input", "the leading-zero arm narrows back to a bare `0`, so `00` "
     "is accepted and caps the walk at the first commit",
     "    ''|*[!0-9]*|0*)\n",
     "    ''|*[!0-9]*|0)\n",
     "a leading zero must be rejected"),
    ("R13", "hedge", "the matchless sentence re-asserts absence from $ref, which "
     "is false for content that predates a rename of the path",
     "this walk could not place the deployed copy at $dep in that path's history",
     "the deployed copy at $dep is not on $ref",
     "is not on origin/main"),
    ("R14", "prov splice", "the provenance remedy is fused back into the noun "
     "phrase, so it lands inside a COULD NOT MEASURE parenthetical",
     '  local prov_full="$prov${prov_note:+ — $prov_note}"',
     '  local prov_full="$prov"; prov="$prov${prov_note:+ — $prov_note}"',
     "must not carry its remedy"),
    ("R11", "vocabulary", "a new COULD NOT MEASURE reason appears with no entry "
     "in SKILL.md — the drift the scraper exists to catch",
     '  local d="" live=0 cand\n',
     '  local d="" live=0 cand\n'
     '  if [ -n "${RESUME_STATE_FAKE_REASON:-}" ]; then\n'
     "    printf '  skill-read: %s — COULD NOT MEASURE (the moon was in the wrong phase)\\n' \"$name\"\n"
     "    return\n"
     "  fi\n",
     # keyed on a marker BOTH arms of the doc guard carry and nothing else in
     # the suite does. The guard has two arms (the spelled count, and the
     # per-reason sweep) and which fires first depends on how many reasons the
     # scrape found — but neither can be confused with another test now.
     # Keyed on the filename it matched half the module's messages instead.
     "SKILL.md VOCABULARY DRIFT"),

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
    # 🔴 ERRORS COUNT AS KILLS. A mutant that removes the fetch timeout makes a
    # test hang until `subprocess.run(timeout=…)` raises, and pytest reports
    # that as `1 error`, not `1 failed` — so a battery reading only "failed"
    # scores the most dangerous mutant in the set as SURVIVED.
    nerr = int(m.group(1)) if (m := re.search(r"(\d+) error", out)) else 0
    nfail += nerr
    npass = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    # 🔴 ONLY THE `E ` LINES COUNT AS "THE MESSAGE". Under `--tb=short` pytest
    # also echoes the SOURCE of the failing statement — which, for an assert
    # carrying an f-string message, contains that message's literal text. So
    # `expected in <whole output>` matched the test's own source and reported
    # KILLED-for-the-right-reason for a guard that had produced nothing. Exactly
    # the source-line-echo trap this file's docstring says it already hit once,
    # in a second place. pytest prefixes every rendered assertion line with
    # `E `, and prefixes source echo with nothing.
    msgs = "\n".join(ln for ln in out.splitlines() if ln.startswith("E "))
    return nfail, npass, msgs


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
            # One edit or several: a tuple means every pair is applied in order,
            # and EACH is still required to occur exactly once. A multi-site
            # mutant whose second half silently did not apply would be scored on
            # the first half alone, which is the failure this loop reports.
            pairs = list(zip(old, new)) if isinstance(old, tuple) else [(old, new)]
            counts = [orig.count(o) for o, _ in pairs]
            if any(c != 1 for c in counts):
                print(f"{mid:4} {guard:14} !! PATTERN OCCURS {counts} — NOT APPLIED — {desc}")
                bad.append(f"{mid}(not-applied)")
                continue
            mutated = orig
            for o, nw in pairs:
                mutated = mutated.replace(o, nw)
            SCRIPT.write_text(mutated, encoding="utf-8")
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
