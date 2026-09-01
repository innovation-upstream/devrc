#!/usr/bin/env python3
"""Mutation battery for `scripts/resume-state.sh`'s handoff RESOLUTION.

    python3 scripts/tests/mutation_battery_resume_state.py

🔴 NOT COLLECTED BY THE GATE, ON PURPOSE — and the filename is the mechanism:
`scripts/run-tests.sh` collects `test_*.py` only. This is a MANUAL instrument,
run when the resolution logic changes, not on every push: it rewrites
`resume-state.sh` in place once per mutant and runs the whole resolution suite
each time (~30 mutants x ~6 s). A gate that edits tracked source is a gate
nobody can run concurrently.

WHY IT IS COMMITTED AT ALL. Two consecutive audits of #690 could not re-run the
battery this file's results were quoted from, because it lived in /tmp — so both
rebuilt one from scratch, and the second still found three mutants the first had
not imagined. A mutation result quoted from an instrument the reader cannot run
is a claim, not evidence. This makes it evidence.

READ BEFORE TRUSTING A VERDICT:

  * The CONTROL runs first and aborts on a red OR EMPTY baseline. That guard is
    not decoration: the first version of this file passed `-q -q`, which
    suppresses the summary line the parser reads, and it duly scored all 22
    mutants SURVIVED while printing non-empty killer lists on the same lines,
    over a control reporting "0 passed, 0 failed". A zero is indistinguishable
    from a probe wired to nothing until something makes the number move.
  * A mutant whose pattern is NOT FOUND is reported as such and counted as a
    survivor. Silent non-application is how a battery reports a clean sweep of
    mutations it never made.
  * SURVIVED does not mean "the code is wrong". It means "no test can see this
    change" — usually a missing test, occasionally genuinely-equivalent code.
    Every survivor is a question to answer, not a defect to fix.
  * The script is restored in a `finally`, so an abort mid-run does not leave a
    mutated `resume-state.sh` behind. Check `git diff` anyway if it dies hard.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/resume-state.sh"
SUITE = "scripts/tests/test_resume_state_handoff_resolution.py"

# (id, shape, description, old, new) — `old` must occur EXACTLY once.
MUTANTS: list[tuple[str, str, str, str, str]] = [
    # ---- deletion --------------------------------------------------------
    ("M1", "deletion", "drop the parent-directory (claudedocs) test",
     '    case "$dir" in */claudedocs|claudedocs) ;; *) continue ;; esac\n', ""),
    ("M2", "deletion", "drop the handoff-shaped basename test",
     '    case "$base" in handoff-*.md|*HANDOFF*.md) ;; *) continue ;; esac\n', ""),
    ("M3", "deletion", "drop the regular-file test (-f)",
     '    [ -f "$tok" ] && { hit="$tok"; break; }\n', '    hit="$tok"; break\n'),
    ("M4", "deletion", "drop set -f (glob the prose)",
     "  case $- in *f*) ;; *) noglob=1; set -f ;; esac\n",
     "  case $- in *f*) ;; *) noglob=1 ;; esac\n"),
    ("M5", "deletion", "the slug-miss class never reaches the gap (the New-2 bug)",
     '    if [ -n "$named_missing" ] || [ -n "$unresolved" ]; then',
     '    if [ -n "$named_missing" ]; then'),
    ("M6", "deletion", "drop the named-but-missing gap",
     '      elif [ -n "$named_missing" ]; then', '      elif false; then'),
    ("M7", "deletion", "never record a named-but-missing token",
     '    [ -n "$miss" ] || { miss="$tok"; ambig="$amb"; }\n', "    :\n"),
    # ---- comment-out -----------------------------------------------------
    ("M8", "comment-out", "comment out the unresolved flag",
     '      [ -z "$HANDOFF" ] && unresolved=1\n',
     '      : # [ -z "$HANDOFF" ] && unresolved=1\n'),
    ("M9", "comment-out", "comment out the prose-scan call",
     '      path=$(embedded_md_path "$arg"); rc=$?\n',
     '      path=""; rc=1  # embedded_md_path disabled\n'),
    # ---- operand swap ----------------------------------------------------
    ("M10", "operand swap", "swap base/dir extraction",
     "    base=${tok##*/}\n    dir=${tok%/*}\n",
     "    base=${tok%/*}\n    dir=${tok##*/}\n"),
    ("M11", "operand swap", "swap the two case subjects",
     '    case "$dir" in */claudedocs|claudedocs) ;; *) continue ;; esac\n'
     '    case "$base" in handoff-*.md|*HANDOFF*.md) ;; *) continue ;; esac\n',
     '    case "$base" in */claudedocs|claudedocs) ;; *) continue ;; esac\n'
     '    case "$dir" in handoff-*.md|*HANDOFF*.md) ;; *) continue ;; esac\n'),
    ("M12", "operand swap", "gap names $path instead of the resolved $HANDOFF",
     'rest=" The digest FELL BACK to $(basename "$HANDOFF")',
     'rest=" The digest FELL BACK to $(basename "$path")'),
    ("M13", "operand swap", "$REPO from $PWD instead of the doc's own directory",
     '    REPO=$(git -C "$(dirname "$HANDOFF")" rev-parse --show-toplevel 2>/dev/null) \\\n'
     '      || REPO=$(dirname "$HANDOFF")\n',
     '    REPO=$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null) || REPO="$PWD"\n'),
    # ---- branch inversion ------------------------------------------------
    ("M14", "branch inversion", "invert the parent-directory arms",
     '    case "$dir" in */claudedocs|claudedocs) ;; *) continue ;; esac\n',
     '    case "$dir" in */claudedocs|claudedocs) continue ;; *) ;; esac\n'),
    ("M15", "branch inversion", "invert the basename arms",
     '    case "$base" in handoff-*.md|*HANDOFF*.md) ;; *) continue ;; esac\n',
     '    case "$base" in handoff-*.md|*HANDOFF*.md) continue ;; *) ;; esac\n'),
    # ⚠ Carries the FOLLOWING line as context. Since #1164 wrapped the fallback
    # chain in `if [ -z "$named_missing" ]`, the bare `      if [ -z "$HANDOFF" ]`
    # spelling occurs THREE times and the mutant would report NOT APPLIED — a
    # silent survivor, which is the failure mode this file's docstring warns
    # about. The `rest=` line is what makes it the GAP branch specifically.
    ("M16", "branch inversion", "invert the empty-HANDOFF gap branch",
     '      if [ -z "$HANDOFF" ]; then\n        rest=" NOTHING was reconciled',
     '      if [ -n "$HANDOFF" ]; then\n        rest=" NOTHING was reconciled'),
    ("M17", "branch inversion", "explicit-path branch inverted (-f -> ! -f)",
     '    if [ -f "$arg" ]; then path="$arg"',
     '    if [ ! -f "$arg" ]; then path="$arg"'),
    # ---- off-by-one ------------------------------------------------------
    ("M18", "off-by-one", "MOVES clause threshold >1 -> >2 (clause missing at 2)",
     '        [ "$n_cand" -gt 1 ] && moves=',
     '        [ "$n_cand" -gt 2 ] && moves='),
    ("M19", "stale rebind", "n_cand never computed, so the MOVES clause never fires",
     '      [ -n "$fam" ] && n_cand=$(ls -t "$REPO"/claudedocs/$fam 2>/dev/null | grep -c .)\n',
     '      :\n'),
    ("M20", "off-by-one", "the dot before md (handoff-*.md -> handoff-*md)",
     'case "$base" in handoff-*.md|*HANDOFF*.md)',
     'case "$base" in handoff-*md|*HANDOFF*.md)'),
    ("M21", "off-by-one", "break -> continue (last prose path wins)",
     '    [ -f "$tok" ] && { hit="$tok"; break; }\n',
     '    [ -f "$tok" ] && { hit="$tok"; continue; }\n'),
    ("M22", "off-by-one", "record the LAST named-missing token, not the first",
     '    [ -n "$miss" ] || { miss="$tok"; ambig="$amb"; }\n',
     '    miss="$tok"; ambig="$amb"\n'),
    # ---- widening (the shape two audits both reached for) -----------------
    ("M23", "widening", "case-fold the caps glob in the prose scan",
     'case "$base" in handoff-*.md|*HANDOFF*.md)',
     'case "$base" in handoff-*.md|*[Hh][Aa][Nn][Dd][Oo][Ff][Ff]*.md)'),
    ("M24", "widening", "case-fold the caps glob in the fallback chain",
     'ls -t "$REPO"/claudedocs/*HANDOFF*.md',
     'ls -t "$REPO"/claudedocs/*[Hh][Aa][Nn][Dd][Oo][Ff][Ff]*.md'),
    ("M25", "widening", "claudedocs as a SUFFIX rather than a whole component",
     '    case "$dir" in */claudedocs|claudedocs) ;; *) continue ;; esac\n',
     '    case "$dir" in *claudedocs) ;; *) continue ;; esac\n'),
    # ---- stale rebind ----------------------------------------------------
    ("M26", "stale rebind", "count the lowercase family whatever resolved",
     '      [ -n "$fam" ] && n_cand=$(ls -t "$REPO"/claudedocs/$fam 2>/dev/null | grep -c .)\n',
     '      n_cand=$(ls -t "$REPO"/claudedocs/handoff-*.md 2>/dev/null | grep -c .)\n'),
    ("M27", "stale rebind", "count the UNION of both families (the pre-fix bug)",
     '      [ -n "$fam" ] && n_cand=$(ls -t "$REPO"/claudedocs/$fam 2>/dev/null | grep -c .)\n',
     '      n_cand=$(ls -t "$REPO"/claudedocs/handoff-*.md '
     '"$REPO"/claudedocs/*HANDOFF*.md 2>/dev/null | sort -u | grep -c .)\n'),
    ("M28", "stale rebind", "lowercase branch records the WRONG family",
     "      [ -n \"$HANDOFF\" ] && fam='handoff-*.md'\n",
     "      [ -n \"$HANDOFF\" ] && fam='*HANDOFF*.md'\n"),
    ("M29", "stale rebind", "a named-missing token reports rc 1, not rc 2",
     "  printf '%s\\n' \"$miss\"\n  return 2\n",
     "  return 1\n"),
    # ---- count-vs-body (New-1) -------------------------------------------
    ("M32", "count/body skew", "the GAPS header counts something other than the body",
     '  echo "  !! GAPS (${#UNRECONCILED[@]}) — SOURCES THAT DID NOT ANSWER."\n',
     '  echo "  !! GAPS (1) — SOURCES THAT DID NOT ANSWER."\n'),
    ("M33", "count/body skew", "one cause appends TWO lines again (the de-dup regressed)",
     '      UNRECONCILED+=("$lead$rest")\n',
     '      UNRECONCILED+=("$lead$rest")\n      UNRECONCILED+=("$lead$rest")\n'),
    # ---- the conditional clause (New-2) ----------------------------------
    ("M34", "clause hoist", "the MOVES clause is emitted unconditionally",
     '        [ "$n_cand" -gt 1 ] && moves=',
     '        [ "$n_cand" -gt 0 ] && moves='),
    ("M35", "guard hoist", "the whole warning keys on the count again (the New-2 bug)",
     '    if [ -n "$named_missing" ] || [ -n "$unresolved" ]; then',
     '    if [ -n "$named_missing" ] || { [ -n "$unresolved" ] && [ -n "$fam" ] && '
     '[ "$(ls -t "$REPO"/claudedocs/$fam 2>/dev/null | grep -c .)" -gt 1 ]; }; then'),
    ("M36", "guard hoist", "a no-argument run warns too (the contract inverted)",
     '    if [ -n "$named_missing" ] || [ -n "$unresolved" ]; then',
     '    if true; then'),
    # ---- prose fidelity (F1/F2/F3) --------------------------------------
    # Every one of these rewrites a SENTENCE rather than a branch. They exist
    # because the six whole-string pins are the only thing that can see them:
    # Z16 was the auditor's own mutant and it passed 138/138 before those pins.
    ("Z16", "false sentence", "the no-fallback branch claims a fallback happened",
     'rest=" NOTHING was reconciled; the DRIFT section below is about no document at all."',
     'rest=" The digest FELL BACK to nothing at all."'),
    ("Z17", "false sentence", "the identity claim retired by F1 creeps back in",
     'rest=" The digest FELL BACK to $(basename "$HANDOFF").$moves',
     'rest=" The digest FELL BACK to $(basename "$HANDOFF"), a DIFFERENT document from the one you asked for.$moves'),
    ("Z18", "reword", "the named-missing lead is reworded",
     'lead="requested handoff \\"$named_missing\\" — NO SUCH FILE (renamed, moved, or in another checkout?)."',
     'lead="requested handoff \\"$named_missing\\" — not found."'),
    ("Z19", "reword", "the slug-miss lead is reworded",
     'lead="requested \\"$arg\\" — nothing in it resolved to a handoff doc under $REPO/claudedocs."',
     'lead="requested \\"$arg\\" — no match under $REPO/claudedocs."'),
    ("Z20", "reword", "the MOVES clause is reworded",
     'moves=" It is the newest of $n_cand, and which one that is depends on commit times, so it MOVES between runs."',
     'moves=" It is the newest of $n_cand."'),
    ("Z21", "reword", "the re-run advice is dropped from the fallback branch",
     " Re-run naming the doc's path, or with no argument to take newest deliberately.\"",
     '"'),
    ("M31", "early exit", "a miss short-circuits the scan before a later hit",
     '    # Shaped like a handoff reference, but not on disk. Remember the FIRST such\n'
     '    # token: it is the caller\'s stated intent, and the run is about to ignore it.\n'
     '    # Its candidate set (empty unless the search above found SEVERAL) travels\n'
     '    # with it, so the gap can say what it saw rather than only that it failed.\n'
     '    [ -n "$miss" ] || { miss="$tok"; ambig="$amb"; }\n',
     '    miss="$tok"; ambig="$amb"; break\n'),
    ("M30", "stale rebind", "the strip loop drops its trailing-punctuation strip",
     '    tok=${tok%[\\`\\\'\\"\\)\\]\\>,\\;]}\n', "    :\n"),
    # ---- #1164: linked-worktree resolution, scoped to the NAMED clone ------
    #
    # The two halves are mutated independently because they fail differently:
    # W1/W4/W6 put the wrong-initiative digest back by finding nothing (or
    # everything); W2 widens the search out of the clone that was NAMED; W3 and
    # W7/W8/W11/W12 break the refusal on an ambiguous set or lie about it;
    # W9/W10 are part 2's no-fallback guard, one side each.
    ("W1", "deletion", "never search the NAMED clone's worktrees",
     '        wt=$(worktrees_holding "${dir%/claudedocs}" "$base"); wrc=$?\n',
     '        wrc=1\n'),
    ("W2", "widening", "search $PWD's clone instead of the one the token NAMED "
                       "(the wrong-initiative bug, one level down)",
     'wt=$(worktrees_holding "${dir%/claudedocs}" "$base"); wrc=$?',
     'wt=$(worktrees_holding "$root" "$base"); wrc=$?'),
    ("W3", "branch inversion", "an AMBIGUOUS set is resolved to its first member",
     '  [ "$n" -eq 1 ] && return 0\n  return 2\n', '  return 0\n'),
    ("W4", "deletion", "drop the regular-file test inside the worktree scan",
     '        [ -f "$w/claudedocs/$base" ] && printf \'%s\\n\' "$w/claudedocs/$base"\n',
     '        printf \'%s\\n\' "$w/claudedocs/$base"\n'),
    # ⚠ NO MUTANT FOR THE `rev-parse --git-dir` GUARD IN `worktrees_holding`,
    # and saying so beats shipping a permanent survivor. It is EQUIVALENT: a
    # directory that exists but is not in a checkout makes `git worktree list`
    # fail, whose (suppressed) output is empty, which returns 1 either way; a
    # directory that IS inside a checkout resolves identically with or without
    # it. The guard is kept because it states the precondition, not because
    # anything can observe it. `[ -d "$dir" ]` above it is what handles absence,
    # and W2 covers the scoping claim people actually care about.
    ("W6", "deletion", "never search the RELATIVE anchor's worktrees",
     '           wt=$(worktrees_holding "$root" "$base"); wrc=$?\n',
     '           wrc=1\n'),
    ("W7", "operand swap", "the ambiguity count is the token, not the candidates",
     '        lead="requested handoff \\"$named_missing\\" — NO SUCH FILE, and '
     '$(basename "$named_missing") exists in $n_amb worktrees',
     '        lead="requested handoff \\"$named_missing\\" — NO SUCH FILE, and '
     '$(basename "$named_missing") exists in 1 worktrees'),
    ("W8", "reword", "the ambiguity lead is reworded",
     'exists in $n_amb worktrees of that clone ($list_amb), so NONE was chosen."',
     'is ambiguous."'),
    # ---- #1164 part 2: a named-missing handoff must not fall back ----------
    ("W9", "guard hoist", "the fallback chain runs for a NAMED path again "
                          "(the #1164 wrong-initiative digest)",
     '    if [ -z "$named_missing" ]; then\n', '    if true; then\n'),
    ("W10", "widening", "the no-fallback guard also blocks any SUPPLIED "
                        "argument (the `unresolved` class), killing the "
                        "MEASURED bare-basename and civitai-slug cases",
     '    if [ -z "$named_missing" ]; then\n',
     '    if [ -z "$named_missing" ] && [ -z "$arg" ]; then\n'),
    ("W13", "off-by-one", "the enumeration cap slides by one (5 shown, not 4)",
     '          if [ "$n_shown" -lt 4 ]; then\n',
     '          if [ "$n_shown" -lt 5 ]; then\n'),
    ("W14", "deletion", "the capped list drops its `and N more` clause, so the "
                        "sentence silently understates what was found",
     '        [ "$n_amb" -gt "$n_shown" ] \\\n'
     '          && list_amb="$list_amb, and $((n_amb - n_shown)) more"\n',
     "        :\n"),
    ("W15", "stale rebind", "the COUNT shrinks with the capped list",
     'exists in $n_amb worktrees', 'exists in $n_shown worktrees'),
    ("W11", "operand swap", "rc 3 is read as rc 2, so the candidate set is lost",
     '      [ "$rc" -eq 3 ] && {\n', '      [ "$rc" -eq 9 ] && {\n'),
    ("W12", "operand swap", "the ambiguous token and its candidate list are "
                            "swapped",
     '        named_missing=${path%%$\'\\n\'*}; named_ambig=${path#*$\'\\n\'}; path=""; }',
     '        named_missing=${path#*$\'\\n\'}; named_ambig=${path%%$\'\\n\'*}; path=""; }'),
]


def run_suite() -> tuple[int, int, list[str]]:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "scripts")}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", SUITE, "-p", "no:cacheprovider",
         "-p", "testlib.nolaunch_plugin", "-p", "testlib.spool_plugin",
         "--tb=no", "-q"],
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=1800,
    )
    out = r.stdout
    killers = sorted({ln.split("::")[1].split("[")[0]
                      for ln in out.splitlines() if ln.startswith("FAILED")})
    nfail = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
    npass = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    return nfail, npass, killers


def main() -> int:
    orig = SCRIPT.read_text(encoding="utf-8")
    try:
        nf, np_, _ = run_suite()
        print(f"CONTROL (pristine): {np_} passed, {nf} failed")
        # 🔴 BOTH halves — see this module's docstring. A green-looking zero is
        # what a battery wired to nothing reports.
        if nf or np_ < 100:
            print("ABORT — baseline is red or collected nothing; no verdict below "
                  "would mean anything")
            return 1

        survived: list[str] = []
        for mid, shape, desc, old, new in MUTANTS:
            n = orig.count(old)
            if n != 1:
                print(f"{mid:4} {shape:16} !! PATTERN OCCURS {n}x — NOT APPLIED — {desc}")
                survived.append(mid)
                continue
            SCRIPT.write_text(orig.replace(old, new), encoding="utf-8")
            nf, _np, killers = run_suite()
            if not nf:
                survived.append(mid)
            shown = ", ".join(k[:56] for k in killers[:3])
            extra = f" (+{len(killers) - 3} more)" if len(killers) > 3 else ""
            print(f"{mid:4} {shape:16} {'KILLED ' if nf else 'SURVIVED'} "
                  f"f={nf:<3} {desc}")
            if killers:
                print(f"     killers: {shown}{extra}")
        print(f"\n{len(MUTANTS) - len(survived)}/{len(MUTANTS)} killed; "
              f"survived: {survived or 'none'}")
        return 1 if survived else 0
    finally:
        SCRIPT.write_text(orig, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
