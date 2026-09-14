"""Every mutation row in `mutants-handoff-cap.sh` still MATCHES its target.

WHY THIS EXISTS
---------------
A row whose `sed` no longer matches the source reports `MUTATION DID NOT APPLY`,
and the harness correctly scores that as a FAILURE — *when the row runs*. Since
`MUTANT_FILTER` landed, most sweeps are filtered, so a row can rot for rounds
without ever being selected.

🔴 MEASURED, and it is why this file is not hypothetical. `deleting-the-field-is-
not-noticed` was repointed once, went dead again when a later round rewrote the
same line, and **two successive adversarial audits did not catch it** — both ran
filtered sweeps that never selected it. It was found only by sweeping every row
mechanically, which is what this does.

🔴 THE CHECK MUST NOT BE SUBJECT TO THE THING IT CHECKS. It reads the rows out of
the script and applies each expression to the live source directly; it never
invokes the harness, so `MUTANT_FILTER` cannot hide a row from it. A row that
changes nothing is a guard that reads as coverage and provides none — the
`claude/RULES.md` shape where a green is believed because nobody asked what it
measured.

⚠ THIS PROVES THE MUTATION APPLIES, NOT THAT IT IS KILLED. Whether a mutant dies,
and to which test, is the harness's job and cannot be answered without running
the suite. This is the cheap half, and it is the half that rots silently.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BATTERY = REPO_ROOT / "scripts/tests/mutants-handoff-cap.sh"
MODULE = REPO_ROOT / "scripts/lib/handoff_doc.py"
SKILL = REPO_ROOT / "claude/skills/handoff/SKILL.md"

#: Rows already dead when this guard landed, so it binds NEW rot without going
#: red on inherited debt — `claude/RULES.md`: a permanently-red gate is worse
#: than no gate, and this one would have been red on `origin/main` the day it
#: shipped.
#:
#: 🔴 THIS LIST MAY ONLY SHRINK. Each entry is a row that currently measures
#: NOTHING; removing one means repointing its expression, never deleting the
#: row. Verified dead at `origin/main` before being written here, so no entry is
#: a defect this branch introduced.
KNOWN_DEAD: dict[str, str] = {
    "elision-omits-the-MAINLINE-source": (
        "the expression embeds a shell-quoted apostrophe sequence that no "
        "longer matches the rendered source; dead at origin/main too, so it "
        "predates this branch and is not ours to repoint mid-arc"
    ),
}

_ROW = re.compile(
    r"^(run|run_skill) '?([\w-]+)'?[^\n]*\n(?:[^\n]*\n)*?\s+'(s[|@].*?)'\n", re.M
)


def _rows() -> list[tuple[str, str, str]]:
    return _ROW.findall(BATTERY.read_text(encoding="utf-8"))


def test_the_row_scraper_finds_a_realistic_number_of_rows():
    """🔴 POSITIVE CONTROL ON THE INSTRUMENT. A regex that matched nothing would
    make every assertion below vacuously true — the reassuring zero this repo
    refuses. The floor is deliberately far under the real count so ordinary
    growth never trips it; it catches COLLAPSE."""
    rows = _rows()
    assert len(rows) >= 60, (
        f"the row scraper found only {len(rows)} row(s) in {BATTERY.name}; the "
        f"pattern has stopped matching the file's shape, and a sweep that "
        f"matches nothing reports every row healthy"
    )
    names = [n for _k, n, _e in rows]
    assert len(names) == len(set(names)), (
        f"duplicate row names: {[n for n in names if names.count(n) > 1]} — the "
        f"harness keys its filter and its reports on the name"
    )


def test_every_mutation_row_still_changes_its_target():
    rows = _rows()
    dead = []
    for kind, name, expr in rows:
        target = SKILL if kind == "run_skill" else MODULE
        text = target.read_text(encoding="utf-8")
        out = subprocess.run(
            ["sed", expr], input=text, capture_output=True, text=True
        ).stdout
        if out == text:
            dead.append(name)

    unexpected = [n for n in dead if n not in KNOWN_DEAD]
    assert not unexpected, (
        "\n\nThese mutation row(s) no longer match the source, so they measure "
        "NOTHING:\n"
        + "".join(f"    {n}\n" for n in unexpected)
        + "\nA row whose `sed` misses reports MUTATION DID NOT APPLY when it "
        "runs — but most sweeps are filtered, so it can rot unseen for rounds.\n"
        "Repoint the expression at the line as it reads NOW, and check the "
        "replacement changes EXACTLY ONE line before writing it down.\n"
        "Do NOT add it to KNOWN_DEAD: that list is for rot inherited from "
        "before this guard, and it may only shrink.\n"
    )

    revived = [n for n in KNOWN_DEAD if n not in dead]
    assert not revived, (
        f"\n\n{revived} is in KNOWN_DEAD but its mutation now APPLIES — someone "
        f"repointed it. Delete the entry; the list may only shrink.\n"
    )
