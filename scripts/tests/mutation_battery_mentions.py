#!/usr/bin/env python3
"""Mutation battery for the MENTION pipeline — scanner, tailer and hint handler.

    python3 scripts/tests/mutation_battery_mentions.py

🔴 NOT COLLECTED BY THE GATE, ON PURPOSE — and the filename is the mechanism:
`scripts/run-tests.sh` collects `test_*.py` only. This is a MANUAL instrument,
run when the mention pipeline changes: it rewrites tracked source in place, one
mutant at a time, and runs the three mention suites after each. A gate that edits
tracked source is a gate nobody can run concurrently.

WHY IT IS COMMITTED. PR #1313's round-1 sweep lived in /tmp, so the auditor could
not re-run it and built their own — which found four guards the first sweep had
not imagined, including a `profile="telemetry"` mutant at the click surface that
the whole 313-test suite tolerated. A mutation result quoted from an instrument
the reader cannot run is a claim, not evidence. This makes it evidence.

🔴 IT SPANS THREE FILES, which is what this battery adds over its two siblings.
The defect class it exists for is a SEAM: `mention_scan.py` decides what may be
detected, `session-tailer.py` decides what is recorded, and `mention-open.py`
decides what is clicked — and every round-1 survivor lived at one of those joins,
not inside one file. So `TARGETS` names a file per mutant and
`scripts/tests/test_mutation_battery_anchors.py` (which IS collected) reads it,
so a row whose anchor stops occurring exactly once fails the push rather than
scoring a silent SURVIVED for whoever next runs this by hand.

READ BEFORE TRUSTING A VERDICT:

  * The CONTROL runs first and aborts on a red OR EMPTY baseline. A zero is
    indistinguishable from a probe wired to nothing until something makes the
    number move.
  * `P1` is the POSITIVE CONTROL — a mutant that MUST die, breaking a URL every
    suite reads. A run in which P1 survives is a broken battery, not a clean
    sweep, and the final line says so.
  * Every run sets `PYTHONDONTWRITEBYTECODE=1`. CPython validates a cached module
    on mtime-in-whole-SECONDS plus size, so a same-length edit landing inside one
    second of the last import is invisible: the suite would import the ORIGINAL
    bytecode and the mutant would be scored SURVIVED without ever executing.
    Several rows here ARE same-length-ish edits.
  * A mutant whose pattern is NOT FOUND is reported as such and counted as a
    problem. Silent non-application is how a battery reports a clean sweep of
    mutations it never made.
  * SURVIVED does not mean "the code is wrong". It means "no test can see this
    change" — usually a missing test, occasionally genuinely-equivalent code.
  * THREE KILL VERDICTS. `KILLED` is "the suite went red". A row carrying an
    `expected` phrase reports `KILLED(attributed)` only when that phrase appears
    in pytest's `E ` lines, and `KILLED-WRONG-REASON` otherwise — the latter is a
    FAILURE of this battery, counted with the survivors, because the row's named
    assertion is not the one that went red. Nearly every row here carries one:
    these mutants redden broad swathes of the suite, so a bare kill would not
    show that the guard the row is about is the guard that fired.
  * Sources are restored in a `finally` AND the restore is verified by hash, so
    an abort mid-run cannot leave a mutated tree behind unreported.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

SCAN = ROOT / "scripts/collector/mention_scan.py"
TAILER = ROOT / "scripts/collector/claude/session-tailer.py"
OPEN_ = ROOT / "scripts/mention-open.py"

# The primary target, for the shared anchor checker's single-file affordances.
SCRIPT = SCAN

SUITES = (
    "scripts/tests/test_mention_scan.py",
    "scripts/tests/test_mention_open.py",
    "scripts/collector/claude/tests/test_session_tailer.py",
)

# (id, shape, description, old, new[, expected]) — `old` must occur EXACTLY once
# in that row's TARGETS file. Same contract as the two sibling batteries; the
# only addition is that the file is per-row rather than module-wide.
MUTANTS: list[tuple] = [
    # ---- POSITIVE CONTROL --------------------------------------------------
    ("P1", "control", "break a URL every suite reads — MUST be killed",
     'CLAWGATE_TASKS_URL = "https://clawgate.zacx.dev/tasks"',
     'CLAWGATE_TASKS_URL = "https://example.invalid/tasks"'),

    # ---- F1: the dedupe identity discards the attribution -------------------
    ("K1", "deletion", "the key drops the repo again (the round-1 defect): two "
                       "repositories referencing one number collapse to one row",
     '    return f"{m[\'platform\']}:{m[\'raw\']}@{repo}" if repo else f"{m[\'platform\']}:{m[\'raw\']}"\n',
     '    return f"{m[\'platform\']}:{m[\'raw\']}"\n',
     "dropped a second repository's reference"),
    ("K2", "widening", "the suffix becomes UNCONDITIONAL, re-keying every "
                       "already-emitted mention on both hosts",
     '    return f"{m[\'platform\']}:{m[\'raw\']}@{repo}" if repo else f"{m[\'platform\']}:{m[\'raw\']}"\n',
     '    return f"{m[\'platform\']}:{m[\'raw\']}@{repo}"\n',
     "the unattributed key format MOVED"),
    ("K3", "operand swap", "the key uses the bare id instead of the raw text",
     '    return f"{m[\'platform\']}:{m[\'raw\']}@{repo}" if repo else f"{m[\'platform\']}:{m[\'raw\']}"\n',
     '    return f"{m[\'platform\']}:{m[\'id\']}@{repo}" if repo else f"{m[\'platform\']}:{m[\'id\']}"\n',
     "keyed on the id, not the raw text"),

    # ---- F2: `explicit` reported for an owner nobody wrote ------------------
    ("K4", "deletion", "the mapping-resolved path is labelled `explicit` again",
     "            source = SOURCE_EXPLICIT if owner else SOURCE_MAPPED\n",
     "            source = SOURCE_EXPLICIT\n",
     "a MAPPING-resolved repo is reported as if the text stated it"),
    ("K5", "widening", "`mapped` is spelled `explicit`, so the two collapse "
                       "again one level down",
     'SOURCE_MAPPED = "mapped"\n', 'SOURCE_MAPPED = "explicit"\n',
     "a MAPPING-resolved repo is reported as if the text stated it"),
    ("K6", "deletion", "drop the `source if repo else SOURCE_NONE` guard, so an "
                       "UNRESOLVED `repo#N` claims `repo_source=mapped`",
     '        "repo_source": source if repo else SOURCE_NONE,\n',
     '        "repo_source": source,\n',
     "claims a resolution that did not happen"),

    # ---- F3: the profile split, at all four enforcement sites ---------------
    ("K7", "deletion", "the ADJACENT attribution route runs in the terminal "
                       "profile too",
     '        adjacent = _adjacent_repo(text, start, repos) if "REPO_BEFORE_RE" in on else ""\n',
     "        adjacent = _adjacent_repo(text, start, repos)\n",
     "the adjacent attribution route answered in the TERMINAL profile"),
    ("K8", "deletion", "the URL attribution route runs in the terminal profile",
     '    url_repo = _sole_repo_named_by(GITHUB_URL_RE, text) if "GITHUB_URL_RE" in on else ""\n',
     "    url_repo = _sole_repo_named_by(GITHUB_URL_RE, text)\n",
     "the url attribution route answered in the TERMINAL profile"),
    ("K9", "deletion", "the --repo FLAG attribution route runs in the terminal "
                       "profile",
     '    flag_repo = _sole_repo_named_by(REPO_FLAG_RE, text) if "REPO_FLAG_RE" in on else ""\n',
     "    flag_repo = _sole_repo_named_by(REPO_FLAG_RE, text)\n",
     "the flag attribution route answered in the TERMINAL profile"),
    ("K10", "widening", "the CLICK handler scans at the telemetry profile — the "
                        "wide surface becomes clickable",
     "    spans = scan_mention_spans(text, repos=repos, default_repo=default_repo)\n",
     "    spans = scan_mention_spans(text, repos=repos, default_repo=default_repo,\n"
     '                               profile="telemetry")\n',
     "reached the CLICK surface"),

    # ---- F4: the disclosure guard's two blind paths -------------------------
    ("K11", "disclosure", "the REFUSAL notify offers the universe as a hint — "
                          "the path `--print` reaches and PASS 4 does not",
     "        notify(f\"cannot resolve {span['raw']}\", detail)\n",
     "        notify(f\"cannot resolve {span['raw']}\",\n"
     '               detail + " known: " + ", ".join(repo_universe(discovered)))\n',
     "REFUSAL-PATH DISCLOSURE"),
    ("K12", "disclosure", "the picker path logs the universe to stdout",
     "            candidates = universe\n",
     '            print("universe:", repo_universe(discovered))\n'
     "            candidates = universe\n",
     "PICKER-PATH DISCLOSURE"),
    ("K13", "disclosure", "the emit line ships the WHOLE mapping beside the one "
                          "repo the mention was attributed to",
     "        f\"b64:repo={m.get('repo', '')}\",\n",
     "        f\"b64:repo={m.get('repo', '')}\",\n"
     '        f"b64:known_repos={sorted(load_mention_repos().values())}",\n',
     "SPOOL DISCLOSURE (attributed run)"),
    ("K14", "disclosure", "the mapping rides along in `context`, on a mention "
                          "with NO attribution at all",
     "        f\"b64:context={m['context']}\",\n",
     "        f\"b64:context={m['context']} {sorted(load_mention_repos())}\",\n",
     "SPOOL DISCLOSURE (unattributed run)"),

    # ---- the nit list: live, reachable, previously untested ------------------
    ("K15", "deletion", "TASK_ANCHOR_RE loses its `(?<![&#])` left guard, so an "
                        "HTML entity and a `##` run both parse as the anchor",
     'TASK_ANCHOR_RE = re.compile(rf"(?<![&#])#task-(?P<num>{_NUM})" + _NUM_END)\n',
     'TASK_ANCHOR_RE = re.compile(rf"#task-(?P<num>{_NUM})" + _NUM_END)\n',
     "the legacy-anchor left guard is gone"),
]

TARGETS: dict[str, pathlib.Path] = {
    "P1": SCAN,
    "K1": TAILER, "K2": TAILER, "K3": TAILER,
    "K4": SCAN, "K5": SCAN, "K6": SCAN,
    "K7": SCAN, "K8": SCAN, "K9": SCAN, "K10": OPEN_,
    "K11": OPEN_, "K12": OPEN_, "K13": TAILER, "K14": TAILER,
    "K15": SCAN,
}


def _digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def run_suite(messages: bool = False) -> tuple[int, int, list[str], str]:
    """Run the three mention suites once. Returns (failed, passed, killers, msgs).

    🔴 ONLY THE `E ` LINES COUNT AS "THE MESSAGE". Under `--tb=short` pytest also
    echoes the SOURCE of the failing statement, which for an assert carrying an
    f-string message contains that message's literal text — so matching the whole
    output would report a right-reason kill for a test that never evaluated the
    assertion. pytest prefixes rendered assertion lines with `E `.
    """
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *SUITES, "-p", "no:cacheprovider",
         "--tb=short" if messages else "--tb=no", "-q"],
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=1800,
    )
    out = r.stdout
    killers = sorted({ln.split("::")[1].split("[")[0]
                      for ln in out.splitlines()
                      if ln.startswith("FAILED") and "::" in ln})
    nfail = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
    npass = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    msgs = "\n".join(ln for ln in out.splitlines() if ln.startswith("E "))
    return nfail, npass, killers, msgs


def main() -> int:
    files = sorted({p for p in TARGETS.values()}, key=str)
    orig = {p: p.read_text(encoding="utf-8") for p in files}
    before = {p: _digest(p) for p in files}
    try:
        nf, np_, _, _ = run_suite()
        print(f"CONTROL (pristine): {np_} passed, {nf} failed")
        # 🔴 BOTH halves. A green-looking zero is what a battery wired to
        # nothing reports.
        if nf or np_ < 200:
            print("ABORT — baseline is red or collected nothing; no verdict "
                  "below would mean anything")
            return 1

        problems: list[str] = []
        for row in MUTANTS:
            mid, shape, desc, old, new = row[:5]
            expected = row[5] if len(row) > 5 else None
            target = TARGETS[mid]
            text = orig[target]
            n = text.count(old)
            if n != 1:
                print(f"{mid:4} {shape:12} !! PATTERN OCCURS {n}x in "
                      f"{target.name} — NOT APPLIED — {desc}")
                problems.append(mid)
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
            try:
                nf, _np, killers, msgs = run_suite(messages=expected is not None)
            finally:
                target.write_text(text, encoding="utf-8")
            if not nf:
                verdict = "SURVIVED"
                problems.append(mid)
            elif expected is None:
                verdict = "KILLED"
            elif expected in msgs:
                verdict = "KILLED(attributed)"
            else:
                verdict = "KILLED-WRONG-REASON"
                problems.append(mid)
            shown = ", ".join(k[:52] for k in killers[:3])
            extra = f" (+{len(killers) - 3} more)" if len(killers) > 3 else ""
            print(f"{mid:4} {shape:12} {verdict:19} f={nf:<3} "
                  f"[{target.name}] {desc}")
            if verdict == "KILLED-WRONG-REASON":
                print(f"     expected {expected!r} in the `E ` lines; not found")
            if killers:
                print(f"     killers: {shown}{extra}")

        # 🔴 THE POSITIVE CONTROL IS READ AS A VERDICT ON THE BATTERY. A run in
        # which P1 survives observed nothing, whatever the other rows say.
        pc_ok = "P1" not in problems
        print(f"\npositive control P1: {'KILLED — the battery can observe' if pc_ok else 'SURVIVED — THIS BATTERY IS BROKEN'}")
        print(f"{len(MUTANTS) - len(problems)}/{len(MUTANTS)} killed for the "
              f"stated reason; problems: {problems or 'none'}")
        return 0 if (pc_ok and not problems) else 1
    finally:
        for p in files:
            p.write_text(orig[p], encoding="utf-8")
        after = {p: _digest(p) for p in files}
        drifted = [p.name for p in files if before[p] != after[p]]
        print("restore: " + ("OK — " + " ".join(f"{p.name}={before[p]}" for p in files)
                             if not drifted else f"🔴 DRIFTED: {drifted}"))


if __name__ == "__main__":
    sys.exit(main())
