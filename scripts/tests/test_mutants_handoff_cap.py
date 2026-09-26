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

🔴 AND FOR ONE ROUND IT WAS ITSELF THE ROT, WHICH IS THE LESSON WORTH KEEPING:
the ROW SCRAPER decided what this file could see, and it was a regex requiring
each expression to start `s|` or `s@`. Four other shapes in this battery do not,
and a row it could not match did not drop out — the pattern skipped FORWARD to a
later row's expression, pairing that row's NAME with the wrong `sed` and deleting
every row in between from the ledger. MEASURED at `f4b98ce7`: 109 declared, 102
scraped, and `rule-p-crashes-instead-of-degrading` rot-checked against
`rule-p-comment-reword-control`'s expression. Nothing went red: the only
instrument check was a floor of 60, which 102 clears.

So the shape to distrust is a SCRAPER, not a row — a guard whose own input is
parsed, and whose parse can fail SILENTLY into a smaller-but-plausible set.
`test_the_scraper_sees_EVERY_declared_row` is the answer: compare against the
file's own declarations, never against a constant that a partial parse clears.
"""
from __future__ import annotations

import re
import shlex
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
#: row.
#:
#: 🔴 IT IS NOW EMPTY, AND ITS ONE ENTRY WAS NEVER A DEAD ROW. It read
#: `elision-omits-the-MAINLINE-source` — "the expression embeds a shell-quoted
#: apostrophe sequence that no longer matches the rendered source". The first
#: clause was true and the second was the SCRAPER's defect, not the row's: that
#: expression closes and reopens its single quotes (`…{ref}'"'"'s"…`) so the
#: shell can pass one apostrophe through, and the regex this file used to scrape
#: with took the RAW text between the outer quotes — `'"'"'` included — which of
#: course matches no source. `mutants-handoff-cap.sh` itself never saw that:
#: bash had already joined the fragments. Parsing the row the way the shell does
#: (`shlex`) shows the mutation APPLIES, so the entry is removed as a false
#: positive rather than repointed. MEASURED at `f4b98ce7`: dead under the old
#: regex, applying under the parser below, with the row's own text unchanged.
KNOWN_DEAD: dict[str, str] = {}

#: A row's first line. The NAME is all this reads — the expression is NOT scraped
#: by regex, for the reason in `_scrape`.
_ROW_HEAD = re.compile(r"^(run|run_skill) '?([\w-]+)'?", re.M)

#: The expression the old scraper required, kept ONLY so
#: `test_the_scraper_no_longer_SKIPS_FORWARD_to_a_later_rows_expression` can show
#: its fixture is one that regex actually gets wrong. Never used to scrape.
_LEGACY_ROW = re.compile(
    r"^(run|run_skill) '?([\w-]+)'?[^\n]*\n(?:[^\n]*\n)*?\s+'(s[|@].*?)'\n", re.M
)


def _logical_lines(text: str) -> list[tuple[str, str]]:
    """`(name, joined-arguments)` for every `run`/`run_skill` invocation.

    A row is its first line plus every line a trailing `\\` continues it onto —
    the same rule bash applies — so the walk CANNOT run past the row it is on.
    """
    lines = text.splitlines()
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        head = _ROW_HEAD.match(lines[i])
        if head:
            chunk = [lines[i]]
            while chunk[-1].rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                chunk.append(lines[i])
            out.append((head.group(2),
                        " ".join(c.rstrip().rstrip("\\") for c in chunk)))
        i += 1
    return out


def _scrape(text: str) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """`(rows, uncaptured)` — `rows` as `(kind, name, sed-expression)`.

    🔴 A ROW CAN ONLY PAIR WITH ITS OWN EXPRESSION, AND THAT IS THE WHOLE FIX.
    The regex this replaced required the expression to be SINGLE-quoted and to
    start `s|` or `s@`. Four shapes this battery actually uses fail that: a
    `s/`-delimited one, a DOUBLE-quoted one, a bare `/addr/d`, and a
    `/from/,/to/ s|…|…|` ADDRESS RANGE. A failing row did not drop
    out — `(?:[^\\n]*\\n)*?` kept SKIPPING FORWARD until some LATER row's
    expression matched, so that row's NAME was scored against a DIFFERENT row's
    `sed`, and every row in between vanished from the ledger silently.
    MEASURED at `f4b98ce7`: 109 rows declared, 102 scraped, 7 invisible, and
    `rule-p-crashes-instead-of-degrading` scored while carrying
    `rule-p-comment-reword-control`'s expression — so its own `sed` was never
    rot-checked by the guard that exists to rot-check it.

    🔴 AND IT REPORTS WHAT IT CANNOT PARSE INSTEAD OF DROPPING IT. A silent skip
    is how the defect above stayed invisible for rounds: the count looked
    healthy because the floor was 60. `uncaptured` is asserted EMPTY by
    `test_the_scraper_sees_EVERY_declared_row`, so an unparseable row is a
    failure naming itself rather than a row nobody checks.

    ⚠ `shlex` IS THE SHELL'S OWN QUOTING RULE, WHICH IS WHY IT IS RIGHT HERE —
    the harness passes these words to `sed` after bash has processed the quotes,
    so scraping the RAW text is scraping a string that never reaches `sed` (see
    `KNOWN_DEAD`). It is NOT a bash emulator: it does no parameter or command
    substitution, so a DOUBLE-quoted expression containing `$` or a backtick
    would diverge from what the harness runs. That case does not exist in this
    file and is refused rather than guessed at.
    """
    rows: list[tuple[str, str, str]] = []
    uncaptured: list[tuple[str, str]] = []
    for name, logical in _logical_lines(text):
        try:
            words = shlex.split(logical)
        except ValueError as exc:                       # unbalanced quotes
            uncaptured.append((name, f"shell-unquoting failed: {exc}"))
            continue
        if len(words) != 4:
            uncaptured.append(
                (name, f"expected `run <name> <want> <expr>`, got {len(words)} "
                       f"word(s) — a row shape this scraper does not know"))
            continue
        kind, _name, _want, expr = words
        if logical.rstrip().endswith('"') and re.search(r"[$`]", expr):
            uncaptured.append(
                (name, "a DOUBLE-quoted expression containing `$` or a "
                       "backtick: bash would expand it and `shlex` does not, so "
                       "this scraper would rot-check a different string than "
                       "the harness runs. Single-quote it."))
            continue
        rows.append((kind, name, expr))
    return rows, uncaptured


def _rows() -> list[tuple[str, str, str]]:
    return _scrape(BATTERY.read_text(encoding="utf-8"))[0]


def test_the_row_scraper_finds_a_realistic_number_of_rows():
    """🔴 POSITIVE CONTROL ON THE INSTRUMENT. A scraper that matched nothing
    would make every assertion below vacuously true — the reassuring zero this
    repo refuses. The floor is deliberately far under the real count so ordinary
    growth never trips it; it catches COLLAPSE.

    ⚠ AND A FLOOR IS ALL IT IS, which is exactly why it did not notice 7 rows
    going missing: 102 clears 60 comfortably. The assertion that catches THAT is
    `test_the_scraper_sees_EVERY_declared_row`, which compares against the
    file's own declarations instead of a constant."""
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


def test_the_scraper_sees_EVERY_declared_row():
    """🔴 THE LEDGER ASSERTION, AND THE ONE THE OLD FLOOR COULD NOT BE. Every
    `run`/`run_skill` line in the file must reach the sweep with an expression of
    its own — a row the scraper cannot pair is a row nothing rot-checks, and it
    reads as coverage. Declared is counted from the file, not from a constant,
    so the two numbers move together or the test goes red."""
    text = BATTERY.read_text(encoding="utf-8")
    declared = [m.group(2) for m in _ROW_HEAD.finditer(text)]
    rows, uncaptured = _scrape(text)
    assert uncaptured == [], (
        "\n\nrow(s) the scraper could not pair with an expression:\n"
        + "".join(f"    {n}: {why}\n" for n, why in uncaptured)
    )
    scraped = [n for _k, n, _e in rows]
    assert scraped == declared, (
        f"\n\n{len(declared)} row(s) declared, {len(scraped)} scraped.\n"
        f"missing: {[n for n in declared if n not in scraped]}\n"
        f"A scraped count BELOW the declared one means rows are invisible to "
        f"the sweep; they report healthy because they are never looked at.\n"
    )


def test_the_scraper_no_longer_SKIPS_FORWARD_to_a_later_rows_expression():
    """🔴 THE POSITIVE CONTROL ON THE FIX, over a fixture built to be exactly
    the shape that broke: a ranged row, an address-delete row and a
    double-quoted row, each followed by a plain `s|…|…|` row the old regex WOULD
    match. It asserts two things, and the second is what makes the first
    meaningful — that the old regex really does get this fixture wrong, so a
    green here is not a green about a case nothing was ever wrong with."""
    fixture = (
        "run 'plain-row' test_a \\\n"
        "  's|alpha|beta|'\n"
        "# an explanatory comment between two rows\n"
        "run 'ranged-row' test_b \\\n"
        "  '/^def one/,/^def two/ s|gamma|delta|'\n"
        "run 'address-delete-row' test_c \\\n"
        "  '/^drop me/d'\n"
        "run 'slash-delimited-row' test_d \\\n"
        "  's/epsilon/zeta/'\n"
        "run_skill 'double-quoted-row' test_e \\\n"
        '  "s@eta\\"theta@iota\\"kappa@"\n'
        "run 'trailing-plain-row' test_f \\\n"
        "  's|mu|nu|'\n"
    )
    rows, uncaptured = _scrape(fixture)
    assert uncaptured == [], uncaptured
    assert rows == [
        ("run", "plain-row", "s|alpha|beta|"),
        ("run", "ranged-row", "/^def one/,/^def two/ s|gamma|delta|"),
        ("run", "address-delete-row", "/^drop me/d"),
        ("run", "slash-delimited-row", "s/epsilon/zeta/"),
        ("run_skill", "double-quoted-row", 's@eta"theta@iota"kappa@'),
        ("run", "trailing-plain-row", "s|mu|nu|"),
    ], rows

    # 🔴 THE CONTROL ON THE CONTROL. Without this, the fixture above could be
    # one the old regex handled fine and the test would prove nothing about the
    # defect it is named for.
    legacy = _LEGACY_ROW.findall(fixture)
    legacy_names = [n for _k, n, _e in legacy]
    assert len(legacy) < len(rows), (
        f"the old regex scraped {len(legacy)} of {len(rows)} fixture rows; this "
        f"fixture no longer reproduces the forward-skip it exists to pin"
    )
    assert "ranged-row" not in legacy_names or dict(
        (n, e) for _k, n, e in legacy
    )["ranged-row"] != "/^def one/,/^def two/ s|gamma|delta|", (
        "the old regex paired `ranged-row` with its own expression, so this "
        "fixture does not demonstrate the mis-pairing"
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

    # ⚠ VACUOUS WHILE `KNOWN_DEAD` IS EMPTY, AND SAID SO RATHER THAN LEFT TO
    # READ AS COVERAGE. It is kept because the list may gain an entry again and
    # this is what stops one outliving its reason. MEASURED that it still bites:
    # re-adding `elision-omits-the-MAINLINE-source` on the fixed scraper reddens
    # exactly this assertion, which is how that entry was shown to be a false
    # positive of the old regex rather than a dead row.
    revived = [n for n in KNOWN_DEAD if n not in dead]
    assert not revived, (
        f"\n\n{revived} is in KNOWN_DEAD but its mutation now APPLIES — someone "
        f"repointed it. Delete the entry; the list may only shrink.\n"
    )
