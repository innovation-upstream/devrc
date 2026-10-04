#!/usr/bin/env python3
"""Mutation battery for the task-CLI corpus scan in
`scripts/tests/test_task_cli_resolver.py` — the shell-suffix ledger, the shebang
discriminator and the bare-invocation pattern.

    python3 scripts/tests/mutation_battery_task_cli_scan.py
    python3 scripts/tests/mutation_battery_task_cli_scan.py --only S1

🔴 NOT COLLECTED BY THE GATE, ON PURPOSE — the filename is the mechanism:
`scripts/run-tests.sh` collects `test_*.py` only. A MANUAL instrument, run when the
scan's ledgers, its file filter or its regex change. It rewrites tracked source in
place once per mutant, so two sessions cannot run it concurrently.

🔴 `SCRIPT` AND `SUITE` ARE THE SAME FILE, AND THAT IS NOT A MISTAKE. The scan's
helpers (`SHELL_SUFFIXES`, `_is_shell_file`, `_BARE_SH_INVOKE_RX`, `_bare_sh_hits`)
live in the test module that drives them, because they ARE the guard — there is no
production module to point at. So each mutant edits the same file the killing test
is read from, which is why `C0` and the `P1` positive control below matter more than
usual rather than less.

WHY IT IS COMMITTED. #2005's own round-0 audit recorded that its mutation sweep was
never committed while this area already holds committed batteries, which makes a
quoted `N KILLED` a CLAIM rather than evidence — `claude/RULES.md`'s "re-verify an
auditor's or subagent's self-reported mutation results" cannot be satisfied from a
tree that does not hold the instrument.

WHAT THE ROWS ARE FOR. The scan was NARROWER THAN ITS OWN DOCSTRING: it filtered
`p.suffix != ".sh"` while `_scan_files()` deliberately yields extensionless files,
so 17 extensionless `#!/usr/bin/env bash` scripts under `scripts/`+`claude/` were
skipped in silence. `S1` IS THAT DEFECT, restored verbatim — the one row here that
is a measured historical state and not an invention.

🔴 AND THE LIMIT THIS BATTERY DOES NOT CLOSE. Every row proves a test WATCHES a
branch of the scan. None proves the scan would ever FIRE on this repo: measured
2026-10-03, a deliberately wide probe over those 17 files (any mention of `muster`,
`clawgatectl`, `$cli`, `${cli}`, `$task_cli` on a non-comment line) returned ZERO.
So the widening closed a claim-wider-than-code gap, NOT a live escape, and a reader
must not take `N KILLED` here as evidence that anything was being missed.
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
SCRIPT = ROOT / "scripts" / "tests" / "test_task_cli_resolver.py"

#: The suite that must kill each mutant — the SAME file; see the header.
SUITE = SCRIPT

#: `(id, why it matters, killer, old, new)` — `old` is the 4th field, which
#: `test_mutation_battery_anchors.py` reads to check each anchor is unique.
MUTANTS = (
    (
        "P1",
        "POSITIVE CONTROL — a rename nothing can survive. If this is not KILLED "
        "the harness is wired to nothing and every other row is meaningless.",
        "",
        "def _bare_sh_hits(files):",
        "def _bare_sh_hits_RENAMED(files):",
    ),
    (
        "S1",
        "THE MEASURED DEFECT, RESTORED: the file filter narrows back to `.sh` only, "
        "so the 17 extensionless bash scripts `_scan_files()` yields are skipped in "
        "silence and the scan's docstring claims coverage it does not have",
        "test_the_scan_really_READS_the_extensionless_bash_scripts",
        "    if p.suffix not in SHELL_SUFFIXES:\n        return False\n",
        '    if p.suffix != ".sh":\n        return False\n',
    ),
    (
        "S2",
        "the extensionless half drops out of the shell ledger — the same narrowing "
        "as S1 but written at the LEDGER instead of the filter, which is the edit a "
        "reader is likelier to make and the one a filter-site test could miss",
        "test_the_shell_suffix_ledger_is_a_SUBSET_of_what_the_walk_yields",
        'SHELL_SUFFIXES = {".sh", ""}',
        'SHELL_SUFFIXES = {".sh"}',
    ),
    (
        "S3",
        "the shebang discriminator is dropped, so 48 `#!/usr/bin/env python3` "
        "scripts and 5 with no shebang are handed to a SHELL regex — a false "
        "positive here is a permanently-red gate, which trains everyone to click "
        "through and is worse than the narrow scan it replaced",
        "test_the_scan_really_READS_the_extensionless_bash_scripts",
        '    return first.startswith("#!") and ("bash" in first'
        ' or "sh" in first.split("/")[-1])',
        "    return True",
    ),
    (
        # 🔴 THIS ROW WAS WRONG ONCE AND THE CORRECTION IS THE INTERESTING PART. It
        # first mutated the ASSERTION (`<=` to `>= … or True`) and SURVIVED — of
        # course it did: disabling a test is not a mutation OF THE SUBJECT, and a
        # row shaped that way scores the suite's ability to notice its own
        # sabotage. Re-aimed at the WALK's ledger, which is what the seam guard is
        # actually a claim about.
        "S4",
        "the ledger that keeps the filter reachable is broken at the OTHER end: the "
        "WALK stops yielding extensionless files while the shell filter still asks "
        "for them, so the scan can never see those 17 files no matter what the "
        "filter says. The SEAM between two ledgers, which is where the original "
        "defect lived",
        "test_the_shell_suffix_ledger_is_a_SUBSET_of_what_the_walk_yields",
        'SCAN_SUFFIXES = {".py", ".sh", ".md", ".json", ".nix", ""}',
        'SCAN_SUFFIXES = {".py", ".sh", ".md", ".json", ".nix"}',
    ),
    (
        "S5",
        "the `_exec ` lookbehind goes, so the WRAPPED form matches too — the "
        "pattern's whole content is that one distinction, and a scan that flags the "
        "fix as the defect is permanently red",
        "test_the_CONTROLS_for_that_bare_invocation_scan",
        "    r'(?<!_exec )'\n",
        "    r''\n",
    ),
    (
        "S6",
        "the regex narrows back to the single quoted spelling, so `${cli}` and a "
        "bare unquoted `$cli` — the same one-character-wide hazard written "
        "differently — walk straight past it again",
        "test_the_CONTROLS_for_that_bare_invocation_scan",
        "    r'(?:\"\\$(?:%(v)s)\"|\\$\\{(?:%(v)s)\\}|\\$(?:%(v)s))'\n",
        "    r'\"\\$(?:%(v)s)\"'\n",
    ),
    # 🔴 `S7` IS WITHDRAWN, NOT RENUMBERED, AND THE GAP IN THE IDS IS THE RECORD.
    # It mutated away a right-hand `(?![A-Za-z0-9_])` on the unquoted alternative
    # and SURVIVED. That was not a missing test: the lookahead was REDUNDANT, because
    # the `\s+` after it already requires whitespace and whitespace is non-alnum, so
    # it could never reject an input `\s+` accepts. Measured over 12 spellings
    # (`$cli_other`, `$clix`, `$cli2`, `$cli-x`, `$cli.`, a tab, the wrapped forms):
    # the two patterns agreed on ALL of them. The remedy for an unkillable guard is
    # to DELETE it and state the limit — `claude/RULES.md`, "for the first two the
    # remedy is to DELETE it" — so the lookahead is gone from
    # `_BARE_SH_INVOKE_RX` and this row has nothing left to mutate. Do not
    # "close the gap" by re-adding it.
    (
        "S8",
        "the comment skip becomes unconditional, so the scan reads NOTHING and "
        "every corpus run reports a confident clean zero",
        "test_the_POSITIVE_CONTROL_for_that_scan",
        '            if line.lstrip().startswith("#"):\n                continue',
        "            if True:\n                continue",
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
    # 🔴 PYTHONDONTWRITEBYTECODE — CPython validates a cached module on
    # mtime-in-whole-SECONDS plus size, so a SAME-LENGTH edit landing in the same
    # second as the last import is invisible and the mutant is scored SURVIVED
    # without ever having executed. S2 and S7 are same-second, near-same-length
    # edits; this flag is what makes their results mean anything.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", str(SUITE), "-q", "--no-header",
         "-p", "no:cacheprovider", "-p", "no:randomly"],
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
        # 🔴 IN A `finally`. Without it a Ctrl-C or any exception mid-sweep leaves
        # a TRACKED, COLLECTED test module mutated in a shared checkout — and this
        # one is collected by the gate, so the next push reads the mutant as the
        # guard. The digest check only runs on paths that reach it.
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
