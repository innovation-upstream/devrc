#!/usr/bin/env python3
"""Mutation battery for `claudedocs/archive/` resolution AND the handoff-doc
byte ceiling.

    python3 scripts/tests/mutation_battery_handoff_archive_and_cap.py

🔴 NOT COLLECTED BY THE GATE, ON PURPOSE — and the filename is the mechanism:
`scripts/run-tests.sh` collects `test_*.py` only. This is a MANUAL instrument,
run when either subject changes. It rewrites tracked source in place once per
mutant and runs the suite each time, so it is not something two sessions can run
concurrently.

WHY IT IS COMMITTED AT ALL — the reason `mutation_battery_resume_state.py`
gives, which this file was nearly a fresh instance of. Two consecutive audits of
#690 could not re-run the battery whose results were being quoted, because it
lived in /tmp; both rebuilt one from scratch and the second still found three
mutants the first had not imagined. The results in this PR's body were produced
from a scratchpad script, which makes them a claim. This makes them evidence.

READ BEFORE TRUSTING A VERDICT
------------------------------
  * THE CONTROL RUNS FIRST and aborts on a red baseline OR a run that printed no
    summary line. A zero is indistinguishable from a probe wired to nothing
    until something makes the number move.
  * EVERY ROW NAMES THE TEST WHOSE OWN ASSERTION MUST GO RED. A mutant killed by
    a neighbouring guard is `KILLED-WRONG-REASON` and is counted with the
    survivors — green for the wrong reason is not a kill.
  * A mutant whose pattern is not found EXACTLY ONCE is `NOT-APPLIED` and counts
    as a survivor. Silent non-application is how a battery reports a clean sweep
    of mutations it never made. (Measured here: row A4's first spelling missed a
    comment between the two lines it swapped and scored 0x.)
  * `PYTHONDONTWRITEBYTECODE=1` IS FORCED ON THE CHILD, and it is not hygiene.
    CPython validates a cached module on whole-second mtime + size, so a
    same-length edit landing in the same second as the last import is invisible:
    the test imports the ORIGINAL bytecode and the mutant is scored SURVIVED
    without ever executing. Half the rows below edit a Python module.
  * Sources are restored in a `finally`. Check `git status` anyway if it dies
    hard.

WHAT THE FIRST RUN FOUND, kept because it is the reusable part
--------------------------------------------------------------
  * A2/A3 SURVIVED. Every fixture named a path that EXISTS, and `resolve()`
    takes `[ -f "$tok" ]` before anything reads `$base` — so the line under test
    was breakable but NOT REACHABLE. Fixed by adding the two routes that do read
    it (the linked-worktree search and the `$root` re-anchor).
  * C10 SURVIVED. Recompiling the shared name predicate with `re.IGNORECASE`
    changes `.flags`, never `.pattern`, so a string-only seam pin read a
    case-folding widening as no change at all. Fixed with a behavioural table;
    C12/C13 were added to prove that table reachable.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SH = ROOT / "scripts/resume-state.sh"
CAP = ROOT / "scripts/tests/test_handoff_doc_size.py"
IDX = ROOT / "scripts/lib/handoff_index.py"

RESOLUTION_SUITE = "scripts/tests/test_resume_state_handoff_resolution.py"
CAP_SUITE = "scripts/tests/test_handoff_doc_size.py"

# --- section 1: claudedocs/archive/ resolution --------------------------------
NORMALISE = (
    '    case "$dir" in\n'
    '      */claudedocs/archive|claudedocs/archive) archsub="archive/"; '
    "dir=${dir%/archive} ;;\n"
    "    esac\n"
)
ANCHOR = '    case "$dir" in */claudedocs|claudedocs) ;; *) continue ;; esac\n'
FAMILY = '    case "$base" in handoff-*.md|*HANDOFF*.md) ;; *) continue ;; esac\n'
PREFIX = (
    "    # After the family test, never before: the test is about the BASENAME, and\n"
    "    # `archive/handoff-x.md` matches neither glob.\n"
    '    base="$archsub$base"\n'
)
FALLBACK = (
    '        HANDOFF=$(ls -t "$REPO"/claudedocs/handoff-*.md 2>/dev/null | head -1)\n'
)

# (id, suite, file, description, old, new, the test that must go red)
ROWS = [
    ("A1", RESOLUTION_SUITE, SH, "delete the archive normalisation entirely",
     NORMALISE, "",
     "test_an_archived_doc_named_in_PROSE_resolves"),
    ("A2", RESOLUTION_SUITE, SH, "normalisation runs but drops archive/ from $base",
     'archsub="archive/"; dir=${dir%/archive}',
     'archsub=""; dir=${dir%/archive}',
     "test_an_archived_doc_in_a_LINKED_WORKTREE_resolves_out_of_the_RIGHT_one"),
    ("A3", RESOLUTION_SUITE, SH, "never apply the prefix to $base",
     PREFIX, "",
     "test_an_archived_doc_in_a_LINKED_WORKTREE_resolves_out_of_the_RIGHT_one"),
    ("A4", RESOLUTION_SUITE, SH, "apply the prefix BEFORE the basename family test",
     FAMILY + PREFIX, PREFIX + FAMILY,
     "test_an_archived_doc_named_in_PROSE_resolves"),
    ("A5", RESOLUTION_SUITE, SH, "respell the enumerated directory gate as a PATTERN",
     ANCHOR,
     '    case "$dir" in */claudedocs|claudedocs|*/claudedocs/*) ;; *) continue ;; esac\n',
     "test_an_UNRECOGNISED_claudedocs_subdirectory_is_not_a_handoff_location"),
    ("A6", RESOLUTION_SUITE, SH, "make the newest-of-N fallback reach the archive",
     FALLBACK,
     '        HANDOFF=$(ls -t "$REPO"/claudedocs/handoff-*.md '
     '"$REPO"/claudedocs/archive/handoff-*.md 2>/dev/null | head -1)\n',
     "test_the_newest_of_N_fallback_NEVER_reaches_the_archive"),
    ("A7", RESOLUTION_SUITE, SH, "add a THIRD directory without updating the ledger",
     NORMALISE,
     '    case "$dir" in\n'
     '      */claudedocs/archive|claudedocs/archive|*/claudedocs/drafts) '
     'archsub="archive/"; dir=${dir%/archive} ;;\n'
     "    esac\n",
     "test_the_accepted_handoff_DIRECTORIES_are_an_enumerated_ledger"),
    ("A8", RESOLUTION_SUITE, SH, "$base prefix dropped — the RE-ANCHOR route",
     'archsub="archive/"; dir=${dir%/archive}',
     'archsub=""; dir=${dir%/archive}',
     "test_an_archived_doc_reached_by_the_RELATIVE_re_anchor_resolves"),

    # --- section 2: the byte ceiling ------------------------------------------
    ("C1", CAP_SUITE, CAP, "raise the ceiling until nothing hits it (decoration)",
     "MAX_BYTES = 65_536", "MAX_BYTES = 1_000_000",
     "test_every_grandfathered_entry_is_a_correctly_stepped_allowance"),
    ("C2", CAP_SUITE, CAP, "the ceiling check never reports",
     "            if size > MAX_BYTES:\n", "            if False:\n",
     "test_control_the_checker_reports_every_kind_of_breach"),
    ("C3", CAP_SUITE, CAP, "the walk stops being recursive (archive ungated)",
     "    return {p: (root / p).stat().st_size for p in scan.paths}, scan",
     "    return {p: (root / p).stat().st_size for p in scan.paths "
     "if p.count('/') == 1}, scan",
     "test_the_scan_reaches_the_ARCHIVE_subdirectory"),
    ("C4", CAP_SUITE, CAP, "silently drop a document from the ledger",
     '    "claudedocs/handoff-tmux-webapp.md": 327_680,               # 314,233 B\n',
     "",
     "test_no_handoff_doc_exceeds_its_budget"),
    ("C5", CAP_SUITE, CAP, "loosen one allowance by two steps (slack entry)",
     '"claudedocs/handoff-handoff-search-index.md": 81_920,',
     '"claudedocs/handoff-handoff-search-index.md": 114_688,',
     "test_no_handoff_doc_exceeds_its_budget"),
    ("C6", CAP_SUITE, CAP, "leave a stale entry naming a nonexistent document",
     "GRANDFATHERED: dict[str, int] = {\n",
     "GRANDFATHERED: dict[str, int] = {\n"
     '    "claudedocs/handoff-was-renamed-away.md": 98_304,\n',
     "test_no_handoff_doc_exceeds_its_budget"),
    ("C7", CAP_SUITE, CAP, "never ask a shrunken document to drop its entry",
     "        if size <= MAX_BYTES:\n            now_fits.append(",
     "        if False:\n            now_fits.append(",
     "test_control_the_checker_reports_every_kind_of_breach"),
    ("C8", CAP_SUITE, CAP, "tightest_allowance stops quantising",
     "    return max(step, math.ceil(size / step) * step)",
     "    return max(step, size)",
     "test_tightest_allowance_quantises_up_and_never_returns_zero"),
    ("C9", CAP_SUITE, CAP, "🔴 THE VACUOUS GREEN: the walk returns nothing",
     "    scan = handoff_index.handoff_paths_on_disk(root)",
     "    scan = handoff_index.handoff_paths_on_disk(root)\n"
     "    scan = handoff_index.DiskScan(paths=(), scanned=True, absent=False, errors=())",
     "test_the_scan_sees_a_real_corpus"),
    ("C10", CAP_SUITE, IDX, "case-fold the shared name predicate (the SEAM)",
     r'_HANDOFF_NAME = re.compile(r"\Ahandoff-.+\.md\Z")',
     r'_HANDOFF_NAME = re.compile(r"\Ahandoff-.+\.md\Z", re.IGNORECASE)',
     "test_the_size_predicate_is_the_INDEX_MODULES_and_not_a_second_spelling"),
    ("C11", CAP_SUITE, CAP, "POSITIVE CONTROL — a malformed ledger MUST die",
     '"claudedocs/handoff-cairn-phase3.md": 163_840,',
     '"claudedocs/handoff-cairn-phase3.md": 163_841,',
     "test_every_grandfathered_entry_is_a_correctly_stepped_allowance"),
    ("C12", CAP_SUITE, IDX, "drop the start anchor from the shared predicate (SEAM)",
     r'_HANDOFF_NAME = re.compile(r"\Ahandoff-.+\.md\Z")',
     r'_HANDOFF_NAME = re.compile(r"handoff-.+\.md\Z")',
     "test_the_size_predicate_is_the_INDEX_MODULES_and_not_a_second_spelling"),
    ("C13", CAP_SUITE, IDX, "widen the shared predicate to .markdown (SEAM)",
     r'_HANDOFF_NAME = re.compile(r"\Ahandoff-.+\.md\Z")',
     r'_HANDOFF_NAME = re.compile(r"\Ahandoff-.+\.mark?down?\Z|\Ahandoff-.+\.md\Z")',
     "test_the_size_predicate_is_the_INDEX_MODULES_and_not_a_second_spelling"),
]


def run_suite(suite: str) -> tuple[bool, str]:
    p = subprocess.run(
        ["nix", "develop", str(ROOT), "-c",
         "python3", "-m", "pytest", suite, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return p.returncode == 0, p.stdout + p.stderr


def main() -> int:
    only = {a for a in sys.argv[1:] if not a.startswith("-")}
    rows = [r for r in ROWS if not only or r[0] in only]
    suites = sorted({r[1] for r in rows})
    saved = {p: p.read_text(encoding="utf-8") for p in {SH, CAP, IDX}}

    for suite in suites:
        ok, out = run_suite(suite)
        summary = [ln for ln in out.splitlines() if " passed" in ln]
        if not ok or not summary:
            print(f"CONTROL NOT GREEN / NO SUMMARY for {suite} — aborting.")
            print(out[-3000:])
            return 1
        print(f"CONTROL {suite}: {summary[-1].strip()}")
    print()

    results = []
    try:
        for mid, suite, path, desc, old, new, want in rows:
            src = path.read_text(encoding="utf-8")
            n = src.count(old)
            if n != 1:
                print(f"{mid:4s} NOT-APPLIED ({n}x) — {desc}")
                results.append((mid, "NOT-APPLIED", desc))
                continue
            path.write_text(src.replace(old, new, 1), encoding="utf-8")
            try:
                ok, out = run_suite(suite)
            finally:
                path.write_text(src, encoding="utf-8")
            if ok:
                print(f"{mid:4s} SURVIVED — {desc}")
                results.append((mid, "SURVIVED", desc))
                continue
            verdict = "KILLED(attributed)" if want in out else "KILLED-WRONG-REASON"
            print(f"{mid:4s} {verdict} — {desc}")
            results.append((mid, verdict, desc))
    finally:
        for p, text in saved.items():
            p.write_text(text, encoding="utf-8")

    print("\n=== SUMMARY ===")
    for mid, verdict, desc in results:
        print(f"  {mid:5s} {verdict:20s} {desc}")
    bad = [r for r in results if not r[1].startswith("KILLED(")]
    print(f"\n{len(results) - len(bad)}/{len(results)} killed for the named reason")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
