#!/usr/bin/env python3
"""Mutation battery for the RENAME-SURVIVAL of an investigation block's age.

    python3 scripts/tests/mutation_battery_investigation_rename.py [R1 R2 …]

🔴 NOT COLLECTED BY THE GATE, ON PURPOSE — and the filename is the mechanism:
`scripts/run-tests.sh` collects `test_*.py` only. This is a MANUAL instrument,
run when `investigations_block`'s clock ladder changes. It rewrites tracked
source in place once per mutant and runs the suite each time, so two sessions
cannot run it concurrently.

WHY IT EXISTS. #1618 dated a block with a PATH-SCOPED pickaxe; #1627 archived 35
handoff docs by RENAMING them. Each was green alone, and together they made
every relocated doc's blocks report `0d` — the freshest possible reading on the
oldest documents in the corpus, which is this feature inverted. The fix scopes
the pickaxe to the doc's whole NAME HISTORY. This battery is what makes the
claim "the new tests can see that going wrong" evidence rather than an
assertion, in the terms `mutation_battery_resume_state.py` sets out: a result
quoted from an instrument the reader cannot re-run is a claim, not evidence.

READ BEFORE TRUSTING A VERDICT
------------------------------
  * THE CONTROL RUNS FIRST and aborts on a red baseline OR on a run that printed
    no summary line. A zero is indistinguishable from a probe wired to nothing
    until something makes the number move.
  * EVERY ROW NAMES THE TEST WHOSE OWN ASSERTION MUST GO RED. A mutant killed by
    a neighbouring guard is `KILLED-WRONG-REASON` and counts with the survivors.
  * A mutant whose pattern is not found EXACTLY ONCE is `NOT-APPLIED` and counts
    as a survivor. Silent non-application is how a battery reports a clean sweep
    of mutations it never made. That anchor rule is enforced on every push by
    `test_mutation_battery_anchors.py`, which reads the `MUTANTS` table below.
  * REACHABILITY IS NOT BREAKABILITY. R6 is the positive control for the fast
    path — the un-renamed doc that 84 of this repo's 119 handoff docs are — so a
    sweep where only the rename rows move would be one that never proved the
    ordinary case is exercised at all.
  * `PYTHONDONTWRITEBYTECODE=1` is forced on the child. Every row here edits a
    shell script rather than a Python module, so the `.pyc` mtime hazard cannot
    bite this file — it is set anyway because the suite imports
    `scripts/lib/handoff_doc.py`, and a stale import there would score rows
    against code that never ran.
  * The script is restored in a `finally`. Check `git status` anyway if it dies
    hard.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/resume-state.sh"
SUITE = "scripts/tests/test_resume_state_investigations.py"

RESOLVE = 'done < <(doc_prior_paths "$ref" "$rel")\n'
PICKAXE_PATHS = '${ref:+"$ref"} -- "${paths[@]}" 2>/dev/null | head -1)'
AWK = """awk -F'\\t' '$1 ~ /^R[0-9]*$/ && $2 != "" { print $2 }'"""
FOLLOW = '      --follow -- "$rel" 2>/dev/null |\n'
NEEDLE = '--reverse -S"### $heading" \\\n'

#: (id, shape, description, old, new, the test whose OWN assertion must go red)
#:
#: 🔴 THE TABLE IS NAMED `MUTANTS` AND `old` IS ITS 4TH FIELD BECAUSE
#: `test_mutation_battery_anchors.py` READS IT — that is a contract, not a
#: style. That module is COLLECTED (it mutates nothing), so on every push it
#: re-checks that each `old` below still occurs EXACTLY ONCE in `SCRIPT`. An
#: anchor reformatted to 0x makes its row print `NOT-APPLIED` and score as a
#: SURVIVOR while testing nothing, and only someone re-running this battery by
#: hand would ever see it — which has happened twice in this repo.
MUTANTS: list[tuple[str, str, str, str, str, str]] = [
    ("R1", "deletion", "🔴 THE REGRESSION ITSELF — never resolve prior names, "
     "so the pickaxe sees only the doc's CURRENT path",
     RESOLVE, "done < <(:)\n",
     "test_a_RENAMED_docs_block_keeps_the_age_of_the_commit_that_WROTE_it"),

    ("R2", "widening", "drop the pathspec — the tempting repo-wide fix, which "
     "lets an older document sharing a heading win",
     PICKAXE_PATHS, '${ref:+"$ref"} 2>/dev/null | head -1)',
     "test_the_pickaxe_stays_SCOPED_and_never_dates_a_block_from_ANOTHER_doc"),

    ("R3", "narrowing", "resolve only the MOST RECENT rename, not the chain",
     AWK,
     """awk -F'\\t' '$1 ~ /^R[0-9]*$/ && $2 != "" && !seen++ { print $2 }'""",
     "test_a_CHAIN_of_renames_is_walked_all_the_way_back"),

    ("R4", "replacement", "read the rename's NEW name (field 3) instead of the "
     "OLD one (field 2) — a path we already have",
     AWK,
     """awk -F'\\t' '$1 ~ /^R[0-9]*$/ && $2 != "" { print $3 }'""",
     "test_a_RENAMED_docs_block_keeps_the_age_of_the_commit_that_WROTE_it"),

    ("R5", "deletion", "drop `--follow`, so rename detection stops at the "
     "commits that touch the current path",
     FOLLOW, '      -- "$rel" 2>/dev/null |\n',
     "test_a_RENAMED_docs_block_keeps_the_age_of_the_commit_that_WROTE_it"),

    ("R6", "short-circuit", "POSITIVE CONTROL for the UN-RENAMED fast path — "
     "make the pickaxe needle unmatchable and the common case must go red too",
     NEEDLE, '--reverse -S"### $heading — no such text" \\\n',
     "test_CONTROL_the_same_fixture_WITHOUT_the_rename_is_EXPIRED_too"),
]


def run_suite() -> tuple[bool, str]:
    p = subprocess.run(
        ["nix", "develop", str(ROOT), "-c",
         "python3", "-m", "pytest", SUITE, "-q", "--no-header",
         "-k", "RenameDoesNotReset or Reachability",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return p.returncode == 0, p.stdout + p.stderr


def main() -> int:
    only = {a for a in sys.argv[1:] if not a.startswith("-")}
    unknown = sorted(only - {r[0] for r in MUTANTS})
    if unknown:
        raise SystemExit(f"no such mutant id(s): {unknown}")
    rows = [r for r in MUTANTS if not only or r[0] in only]

    ok, out = run_suite()
    summary = [ln for ln in out.splitlines() if " passed" in ln]
    if not ok or not summary:
        print("CONTROL NOT GREEN / NO SUMMARY — aborting.")
        print(out[-3000:])
        return 1
    print(f"CONTROL {SUITE}: {summary[-1].strip()}\n")

    saved = SCRIPT.read_text(encoding="utf-8")
    results = []
    try:
        for mid, _shape, desc, old, new, want in rows:
            src = SCRIPT.read_text(encoding="utf-8")
            n = src.count(old)
            if n != 1:
                print(f"{mid:4s} NOT-APPLIED ({n}x) — {desc}")
                results.append((mid, "NOT-APPLIED", desc))
                continue
            SCRIPT.write_text(src.replace(old, new, 1), encoding="utf-8")
            try:
                ok, out = run_suite()
            finally:
                SCRIPT.write_text(src, encoding="utf-8")
            if ok:
                print(f"{mid:4s} SURVIVED — {desc}")
                results.append((mid, "SURVIVED", desc))
                continue
            verdict = "KILLED(attributed)" if want in out else "KILLED-WRONG-REASON"
            print(f"{mid:4s} {verdict} — {desc}")
            results.append((mid, verdict, desc))
    finally:
        SCRIPT.write_text(saved, encoding="utf-8")

    print("\n=== SUMMARY ===")
    for mid, verdict, desc in results:
        print(f"  {mid:5s} {verdict:20s} {desc}")
    bad = [r for r in results if not r[1].startswith("KILLED(")]
    print(f"\n{len(results) - len(bad)}/{len(results)} killed for the named reason")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
