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
#: 🔴 THE CEILING'S CONSTANTS LIVE IN THE LIBRARY, NOT THE SUITE. `#1648`
#: (`a2c84a1c`) moved `MAX_BYTES`, `GRANDFATHERED` and `tightest_allowance` out
#: of `CAP` and into this module so a WRITER could be warned before a write.
#: Six rows below still anchored on `CAP`, where they then occurred 0x — every
#: one of them scoring as a SURVIVOR while testing nothing, which is precisely
#: the failure the note above `SCRIPT` describes. The battery's targets are a
#: THIRD thing an extraction has to carry with it; moving a constant is not a
#: refactor of one file.
BUD = ROOT / "scripts/lib/handoff_budget.py"

RESOLUTION_SUITE = "scripts/tests/test_resume_state_handoff_resolution.py"
CAP_SUITE = "scripts/tests/test_handoff_doc_size.py"

#: 🔴 THE TABLE IS NAMED `MUTANTS` AND `old` IS ITS 4TH FIELD BECAUSE
#: `test_mutation_battery_anchors.py` READS IT — that is a contract, not a
#: style. That module is COLLECTED (it mutates nothing), so on every push it
#: re-checks that each `old` below still occurs EXACTLY ONCE in its target. An
#: anchor reformatted to 0x makes its row print `NOT-APPLIED` and score as a
#: SURVIVOR while testing nothing, and only someone re-running this battery by
#: hand would ever see it. That has happened twice in this repo, once on
#: `resume-state.sh` itself (`60c893b7`, row X1).
#:
#: ⚠ THE FIRST VERSION OF THIS FILE CALLED IT `ROWS` WITH `old` FIFTH, AND WAS
#: THEREFORE UNPINNED — caught by that module's own two-way ledger, which is a
#: THIRD enumeration this change had to register with. Reshaped rather than
#: exempted: `NOT_TABLE_DRIVEN` would have passed on the technicality that a
#: table named `ROWS` has no `^MUTANTS` line, which is exactly the
#: technically-true-but-misleading reason `test_the_EXEMPTION_list_is_not_a_
#: hiding_place` exists to refuse.
#:
#: A BATTERY SPANNING SEVERAL FILES declares `TARGETS`; `SCRIPT` is the default
#: for any row without an entry. Both are declared below, and the anchors module
#: requires every row to appear in `TARGETS` once `TARGETS` is non-empty.
SCRIPT = SH

#: Which suite's verdict each target's mutants are scored against. Derived from
#: the target rather than carried per row, so a row cannot name a file and a
#: suite that disagree.
SUITE_OF_TARGET = {
    SH: RESOLUTION_SUITE,
    CAP: CAP_SUITE,
    IDX: CAP_SUITE,
    BUD: CAP_SUITE,
}

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

# (id, shape, description, old, new, the test whose OWN assertion must go red)
#
# 🔴 `old` IS THE 4TH FIELD. See the contract note above `SCRIPT`.
MUTANTS = [
    ("A1", "deletion", "delete the archive normalisation entirely",
     NORMALISE, "",
     "test_an_archived_doc_named_in_PROSE_resolves"),
    ("A2", "replacement", "normalisation runs but drops archive/ from $base",
     'archsub="archive/"; dir=${dir%/archive}',
     'archsub=""; dir=${dir%/archive}',
     "test_an_archived_doc_in_a_LINKED_WORKTREE_resolves_out_of_the_RIGHT_one"),
    ("A3", "deletion", "never apply the prefix to $base",
     PREFIX, "",
     "test_an_archived_doc_in_a_LINKED_WORKTREE_resolves_out_of_the_RIGHT_one"),
    ("A4", "reorder", "apply the prefix BEFORE the basename family test",
     FAMILY + PREFIX, PREFIX + FAMILY,
     "test_an_archived_doc_named_in_PROSE_resolves"),
    ("A5", "widening", "respell the enumerated directory gate as a PATTERN",
     ANCHOR,
     '    case "$dir" in */claudedocs|claudedocs|*/claudedocs/*) ;; *) continue ;; esac\n',
     "test_an_UNRECOGNISED_claudedocs_subdirectory_is_not_a_handoff_location"),
    ("A6", "widening", "make the newest-of-N fallback reach the archive",
     FALLBACK,
     '        HANDOFF=$(ls -t "$REPO"/claudedocs/handoff-*.md '
     '"$REPO"/claudedocs/archive/handoff-*.md 2>/dev/null | head -1)\n',
     "test_the_newest_of_N_fallback_NEVER_reaches_the_archive"),
    ("A7", "widening", "add a THIRD directory without updating the ledger",
     NORMALISE,
     '    case "$dir" in\n'
     '      */claudedocs/archive|claudedocs/archive|*/claudedocs/drafts) '
     'archsub="archive/"; dir=${dir%/archive} ;;\n'
     "    esac\n",
     "test_the_accepted_handoff_DIRECTORIES_are_an_enumerated_ledger"),
    ("A8", "replacement", "$base prefix dropped — the RE-ANCHOR route",
     'archsub="archive/"; dir=${dir%/archive}',
     'archsub=""; dir=${dir%/archive}',
     "test_an_archived_doc_reached_by_the_RELATIVE_re_anchor_resolves"),

    # --- section 2: the byte ceiling ------------------------------------------
    ("C1", "replacement", "raise the ceiling until nothing hits it (decoration)",
     "MAX_BYTES = 65_536", "MAX_BYTES = 1_000_000",
     "test_every_grandfathered_entry_is_a_correctly_stepped_allowance"),
    ("C2", "short-circuit", "the ceiling check never reports",
     "            if size > MAX_BYTES:\n", "            if False:\n",
     "test_control_the_checker_reports_every_kind_of_breach"),
    ("C3", "narrowing", "the walk stops being recursive (archive ungated)",
     "    return {p: (root / p).stat().st_size for p in scan.paths}, scan",
     "    return {p: (root / p).stat().st_size for p in scan.paths "
     "if p.count('/') == 1}, scan",
     "test_the_scan_reaches_the_ARCHIVE_subdirectory"),
    # ⚠ A ROW ANCHORED ON A WHOLE LEDGER LINE BINDS TO EVERYTHING ON THAT LINE,
    # and the REASON generalises to C5 and C11 below. This used to include a
    # trailing measured size comment, which was re-written on every allowance
    # bump and so silently took this anchor to 0x — SURVIVED while testing
    # nothing, the exact defect this file's own re-anchors keep fixing. Those
    # comments were DELETED from the ledger, so a line here now moves only when
    # the ALLOWANCE moves, which is always a deliberate edit to that line.
    # 🔴 Re-check these three rows whenever the ledger is edited;
    # `handoff_budget.GRANDFATHERED` is the source.
    #
    # 🔴 AND SINCE #1871 THE LEDGER HOLDS ENTRIES FOR DOCUMENTS IN OTHER REPOS,
    # WHICH MAKES THE CHOICE OF ANCHOR LOAD-BEARING IN A NEW WAY: C4 and C5 must
    # anchor on an entry whose document is in devrc. Point either at a foreign
    # entry and it SURVIVES while looking correct — `over_allowance` and
    # `over_ceiling` both need the doc to be IN the scanned corpus, and a foreign
    # one never is. C11 is immune (it is a pure ledger check) but is listed with
    # them because the re-check is one habit, not three.
    # ⚠ THAT TRAP IS NOW HARD TO FALL INTO RATHER THAN CLOSED, and the difference
    # matters. A foreign entry's key is `digest_key(path)` (devrc is public), so
    # the anchors below are visibly the only PATH-shaped keys in the dict and a
    # foreign one cannot be picked by pasting a familiar name. Nothing ASSERTS the
    # anchor is a devrc entry, so the habit stands.
    ("C4", "deletion", "silently drop a document from the ledger",
     '    "claudedocs/handoff-nix-disk-cleanup.md": 114_688,\n',
     "",
     "test_no_handoff_doc_exceeds_its_budget"),
    # ⚠ RE-ANCHORED: this row used to loosen `handoff-handoff-search-index.md`,
    # whose entry `#1650` DELETED once the doc was pruned back under MAX_BYTES.
    # A row anchored on a ledger entry is only as durable as that entry, and the
    # ledger is designed to shrink — so the anchor moved to a live entry rather
    # than the row being dropped. Same shape: 76,743 B quantises to 81,920, so
    # 114,688 is exactly two steps of slack.
    ("C5", "replacement", "loosen one allowance by two steps (slack entry)",
     '"claudedocs/handoff-cairn-task-linkage.md": 81_920,',
     '"claudedocs/handoff-cairn-task-linkage.md": 114_688,',
     "test_no_handoff_doc_exceeds_its_budget"),
    ("C6", "insertion", "leave a stale entry naming a nonexistent document",
     "GRANDFATHERED: dict[str, int] = {\n",
     "GRANDFATHERED: dict[str, int] = {\n"
     '    "claudedocs/handoff-was-renamed-away.md": 98_304,\n',
     "test_no_handoff_doc_exceeds_its_budget"),
    ("C7", "short-circuit", "never ask a shrunken document to drop its entry",
     "        if size <= MAX_BYTES:\n            now_fits.append(",
     "        if False:\n            now_fits.append(",
     "test_control_the_checker_reports_every_kind_of_breach"),
    ("C8", "replacement", "tightest_allowance stops quantising",
     "    return max(step, math.ceil(size / step) * step)",
     "    return max(step, size)",
     "test_tightest_allowance_quantises_up_and_never_returns_zero"),
    ("C9", "insertion", "🔴 THE VACUOUS GREEN: the walk returns nothing",
     "    scan = handoff_index.handoff_paths_on_disk(root)",
     "    scan = handoff_index.handoff_paths_on_disk(root)\n"
     "    scan = handoff_index.DiskScan(paths=(), scanned=True, absent=False, errors=())",
     "test_the_scan_sees_a_real_corpus"),
    ("C10", "widening", "case-fold the shared name predicate (the SEAM)",
     r'_HANDOFF_NAME = re.compile(r"\Ahandoff-.+\.md\Z")',
     r'_HANDOFF_NAME = re.compile(r"\Ahandoff-.+\.md\Z", re.IGNORECASE)',
     "test_the_size_predicate_is_the_INDEX_MODULES_and_not_a_second_spelling"),
    ("C11", "replacement", "POSITIVE CONTROL — a malformed ledger MUST die",
     '"claudedocs/handoff-cairn-phase3.md": 163_840,',
     '"claudedocs/handoff-cairn-phase3.md": 163_841,',
     "test_every_grandfathered_entry_is_a_correctly_stepped_allowance"),
    ("C12", "widening", "drop the start anchor from the shared predicate (SEAM)",
     r'_HANDOFF_NAME = re.compile(r"\Ahandoff-.+\.md\Z")',
     r'_HANDOFF_NAME = re.compile(r"handoff-.+\.md\Z")',
     "test_the_size_predicate_is_the_INDEX_MODULES_and_not_a_second_spelling"),
    ("C13", "widening", "widen the shared predicate to .markdown (SEAM)",
     r'_HANDOFF_NAME = re.compile(r"\Ahandoff-.+\.md\Z")',
     r'_HANDOFF_NAME = re.compile(r"\Ahandoff-.+\.mark?down?\Z|\Ahandoff-.+\.md\Z")',
     "test_the_size_predicate_is_the_INDEX_MODULES_and_not_a_second_spelling"),
    # 🔴 THE CROSS-REPO LEDGER'S OWN HAZARD (#1871). `LIVES_ELSEWHERE` turns
    # check (d) OFF for the entries it names, so the way it goes wrong is not a
    # stale entry — C6 above still covers that, and still kills, because the path
    # it plants is undeclared — but a COLLISION: the ledger key carries no repo,
    # so declaring a path that ALSO exists in devrc hands two different documents
    # one allowance and silences the only check that could see it.
    # The planted path is in `GRANDFATHERED` and IS a devrc doc, which isolates
    # the collision arm from the "declared but not grandfathered" arm beside it.
    # MEASURED by hand at this tree: killed by that one test, nothing else.
    ("C14", "insertion", "declare a path that is ALSO a devrc document",
     "LIVES_ELSEWHERE: dict[str, str] = {\n",
     "LIVES_ELSEWHERE: dict[str, str] = {\n"
     '    "claudedocs/handoff-tmux-webapp.md": "some-other-repo",\n',
     "test_every_FOREIGN_entry_is_declared_and_is_NOT_a_devrc_document"),
    # 🔴 THE PUBLIC-REPO HAZARD (#1871, the re-key). A foreign entry is keyed by
    # `digest_key(path)` because devrc is PUBLIC, and the way that decays is not a
    # stale entry or a collision — both covered above — but the NEXT PERSON adding
    # an entry in PLAINTEXT, because a plaintext path is what the failure message
    # they are pasting hands them. The planted key is a synthetic name and is
    # deliberately NOT in `GRANDFATHERED`, which is the shape a real re-add takes
    # (nobody adds to one dict on purpose); the named test's claim 1 is what fires
    # on the readable key, and it is the one this row is scored against.
    ("C15", "insertion", "re-add a FOREIGN entry spelled as a readable path",
     "LIVES_ELSEWHERE: dict[str, str] = {\n",
     "LIVES_ELSEWHERE: dict[str, str] = {\n"
     '    "claudedocs/handoff-a-plaintext-foreign-key.md": "some-other-repo",\n',
     "test_every_ledger_KEY_is_a_devrc_path_or_a_WELL_FORMED_digest"),
    # 🔴 THE RESOLVER, WHICH NOTHING ELSE IN THIS SUITE REACHES. `lookup` tries the
    # plaintext key first, so on devrc's own 11 entries the digest arm never
    # executes — every other row here, and every fixture in the ceiling module,
    # would pass with it deleted while all 71 foreign documents silently fell back
    # to the bare ceiling.
    ("C16", "deletion", "drop the DIGEST arm of the one ledger resolver",
     "        hit = ledger.get(digest_key(relpath))\n",
     "        hit = None\n",
     "test_the_ledger_RESOLVER_reads_a_digest_key_and_prefers_plaintext"),
]

#: 🔴 EVERY ROW, BECAUSE THIS BATTERY SPANS THREE FILES. The anchors module
#: requires a COMPLETE map once `TARGETS` is non-empty: a row with no entry
#: silently falls back to `SCRIPT` and has its anchor counted against the WRONG
#: file, where it occurs 0x — which reads as a battery bug rather than as the
#: mapping bug it is. An entry naming no row fails there too.
TARGETS = {
    "A1": SH,
    "A2": SH,
    "A3": SH,
    "A4": SH,
    "A5": SH,
    "A6": SH,
    "A7": SH,
    "A8": SH,
    # The ceiling's DATA and its arithmetic live in `BUD`; the checker that
    # reads them, and the scan wiring, stay in `CAP`. Split per row, not per
    # section, because the section spans both files.
    "C1": BUD,
    "C2": CAP,
    "C3": CAP,
    "C4": BUD,
    "C5": BUD,
    "C6": BUD,
    "C7": CAP,
    "C8": BUD,
    "C9": CAP,
    "C10": IDX,
    "C11": BUD,
    "C12": IDX,
    "C13": IDX,
    "C14": BUD,
    "C15": BUD,
    "C16": BUD,
}


def run_suite(suite: str) -> tuple[bool, str]:
    p = subprocess.run(
        ["nix", "develop", str(ROOT), "-c",
         "python3", "-m", "pytest", suite, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return p.returncode == 0, p.stdout + p.stderr


def target_of(mid: str) -> pathlib.Path:
    """The file a row mutates. Same fallback rule the anchors module applies, so
    the battery and the checker cannot disagree about where a row's anchor is."""
    return TARGETS.get(mid, SCRIPT)


def suite_of(mid: str) -> str:
    """The suite a row is scored against, DERIVED from its target rather than
    carried as a field — a row cannot then name a file and a suite that
    disagree, and there is one less thing for a reshape to desynchronise."""
    return SUITE_OF_TARGET[target_of(mid)]


def main() -> int:
    only = {a for a in sys.argv[1:] if not a.startswith("-")}
    unknown = sorted(only - {r[0] for r in MUTANTS})
    if unknown:
        raise SystemExit(f"no such mutant id(s): {unknown}")
    rows = [r for r in MUTANTS if not only or r[0] in only]
    suites = sorted({suite_of(r[0]) for r in rows})
    # 🔴 `BUD` BELONGS HERE — this is the OUTER net, and it is the one that
    # matters. The per-row `finally` restores after each mutant, but a run killed
    # between rows (a timeout, a ^C) skips it, and then only this snapshot can
    # put the tree back. MEASURED while writing this change: a foreground run hit
    # a 10-minute cap and the SIGTERM left `scripts/resume-state.sh` carrying row
    # A6's mutant — tracked source, silently modified. Adding a target without
    # adding it here leaves that target the one file the net cannot restore.
    saved = {p: p.read_text(encoding="utf-8") for p in {SH, CAP, IDX, BUD}}

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
        for mid, _shape, desc, old, new, want in rows:
            path, suite = target_of(mid), suite_of(mid)
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
