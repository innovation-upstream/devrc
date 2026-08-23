#!/usr/bin/env python3
"""🔴 THIS REPO IS PUBLIC. Nothing under this skill may carry a real ClickUp task ID or a
real person's name.

WHY IT EXISTS. This skill was migrated in from a PRIVATE repo, where none of this
mattered, and its fixtures are ClickUp-shaped by nature: task IDs, comment authors, comment
bodies. devrc's existing content gates (`scripts/tests/test_no_captured_text.py`,
`scripts/tests/test_no_captured_markup.py`) cover JSON/JSONL/JSONC and `.html`/`.txt`, so a
`.py`/`.md` arrival is watched by nothing else and a migration merges clean either way.

WHAT IT PINS, and why a LEDGER rather than a pattern. "Reject ids that look real" is not
expressible: a synthetic ID and a real one are the same nine characters by construction
(`TASK_ID_RE` is `\\b868[a-z0-9]{6}\\b`, and the fixtures must match it or they test
nothing). So the gate inverts it — every ID-shaped token under this skill must be on the
list below, and every comment AUTHOR must be on the other one. A new ID or a new name is
red by default, and going green means writing it down and saying it is invented. That is
the review this gate exists to force; it is not an inconvenience to route around.

SHAPE, NOT WORDS. When you regenerate a fixture, keep what the case was chosen for —
negator position, clause boundaries, closure vocabulary, prefix overlap between two IDs —
and change only the words. `test_corpus.py` scores the veto against these shapes; a case
flattened into a simpler sentence keeps the score and tests nothing.

WHAT IT DOES NOT COVER, stated so a green run is not over-read:
  * Free prose. It has no view on whether a paraphrase is really a paraphrase.
  * Repo, org and PR references. `KNOWN_REPOS` in `check-completion.py` is production
    vocabulary and must name real repositories for the tool to resolve anything.
  * GIT HISTORY. Like all four sibling content gates this reads the working tree only.
    Scrubbing HEAD does not remove anything from a public repo's history.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# Both halves of the skill: the code + suite, and the docs that ship through nix.
SCAN_ROOTS = (
    REPO_ROOT / "scripts" / "check-clickup-addressed",
    REPO_ROOT / "claude" / "skills" / "check-clickup-addressed",
)
SCAN_SUFFIXES = (".py", ".md")

# The production ID shape, kept in step with `check-completion.py`'s TASK_ID_RE.
TASK_ID_RE = re.compile(r"\b868[a-z0-9]{6}\b")

# Fixture IDs that a `-` or `_` splits (test_special_characters_in_task_id), which the
# plain shape above cannot see.
SEPARATED_ID_RE = re.compile(r"\b868[a-z0-9]{2}[-_][a-z0-9]{4}\b")

# ── LEDGER 1: every task ID permitted under this skill. All invented. ───────────────────
# The shared-PREFIX structure is deliberate and load-bearing:
#   868gx0aaa / 868gx0bbb / 868gx0zzz  share six characters — the fixture pair behind
#                                      `test_prefix_matching_does_not_invent_windows`
#   868gx1ccc                          shares five with that group
#   868gy0ddd / 868gy0eee / 868gy0fff              a second family
#   868gz0hhh / 868gw0zzz / 868aaa111 / 868zzz111 / 868test01   isolated fixtures
ALLOWED_TASK_IDS = frozenset({
    "868gx0aaa", "868gx0bbb", "868gx0zzz", "868gx1ccc",
    "868gy0ddd", "868gy0eee", "868gy0fff",
    "868gz0hhh", "868gw0zzz",
    "868aaa111", "868zzz111", "868test01",
})
ALLOWED_SEPARATED_IDS = frozenset({"868gy-0ddd", "868gy_0ddd"})

# ── LEDGER 2: every comment author / ClickUp username appearing in a fixture. ───────────
# "Robin Example" and "Jordan Sample" are invented people. The rest are placeholders.
AUTHOR_FIELD_RE = re.compile(r'"(?:author|username)"\s*:\s*"([^"]*)"')
AUTHOR_KWARG_RE = re.compile(r'\bauthor\s*=\s*"([^"]*)"')
ALLOWED_AUTHORS = frozenset({
    "Robin Example", "Jordan Sample",
    "a", "x", "me", "someone", "colleague",
})


# 🔴 THIS FILE EXCLUDES ITSELF, and that is not tidiness — it is what makes the ledgers
# mean anything. The ledgers are written here, in a scanned directory, so a self-including
# scan finds every allowed value present by construction: `test_the_ledgers_have_no_dead_
# entries` can then never fail, and an unregistered id planted here would be "found" by its
# own ledger comment. Measured: with self-inclusion, the dead-entry mutant SURVIVED and the
# dropped-ledger-entry mutant died on this file's own text rather than on a fixture.
SELF = Path(__file__).resolve()


def _files():
    out = []
    for root in SCAN_ROOTS:
        assert root.is_dir(), (
            f"scan root missing: {root}. A scope this gate cannot walk is not a clean scope — "
            "fix the path rather than letting the run report zero findings over zero files.")
        out.extend(sorted(p for p in root.rglob("*")
                          if p.is_file() and p.suffix in SCAN_SUFFIXES
                          and p.resolve() != SELF))
    return out


def _hits(pattern, group=0):
    """Every match of `pattern` under the scan roots, as (value, relative path)."""
    found = []
    for p in _files():
        for m in pattern.finditer(p.read_text(errors="replace")):
            found.append((m.group(group), str(p.relative_to(REPO_ROOT))))
    return found


def test_the_scan_actually_walks_files_and_can_see_an_id():
    """POSITIVE CONTROL. A zero from a scan wired to nothing is indistinguishable from a
    zero from a clean tree, so prove both halves move before believing any verdict below.

    Reports files EXAMINED beside the count, for the same reason `drift-check.sh` does.
    """
    files = _files()
    assert len(files) >= 15, f"expected the whole skill, examined only {len(files)} file(s)"
    assert SELF not in {p.resolve() for p in files}, "this file must exclude itself"

    ids = _hits(TASK_ID_RE)
    assert len(ids) >= 100, (
        f"examined {len(files)} files and found only {len(ids)} task-ID-shaped tokens — the "
        "fixtures are full of them, so a low count means the scan is not reading them")

    # NEGATIVE CONTROL, built by concatenation on purpose: a literal ID-shaped string in
    # this file would itself be scanned, so the control cannot be written out.
    fake_real = "868" + "k" + "r07fu"
    assert TASK_ID_RE.fullmatch(fake_real), "the ID pattern cannot match an ID — it tests nothing"
    assert fake_real not in ALLOWED_TASK_IDS, "the ledger would admit an unregistered ID"


def test_every_task_id_under_this_skill_is_on_the_synthetic_ledger():
    """🔴 The gate. An unregistered ID is red whether or not it is real, because nothing can
    tell those apart by looking. Regenerate the fixture synthetic, then add it here."""
    bad = sorted({(v, f) for v, f in _hits(TASK_ID_RE) if v not in ALLOWED_TASK_IDS})
    assert not bad, (
        "task ID(s) under this skill are not on the synthetic ledger in this file.\n"
        "  If real: this repo is PUBLIC — regenerate the fixture with an invented ID of the\n"
        "  same shape (868 + 6 of [a-z0-9]), preserving any shared prefix the test depends on.\n"
        "  If invented: add it to ALLOWED_TASK_IDS and say so.\n"
        f"  unregistered: {bad}")

    bad_sep = sorted({(v, f) for v, f in _hits(SEPARATED_ID_RE)
                      if v not in ALLOWED_SEPARATED_IDS})
    assert not bad_sep, f"separator-split ID(s) not on the ledger: {bad_sep}"


def test_every_fixture_author_is_on_the_invented_name_ledger():
    """🔴 The other half. Real colleagues' names arrived in `author` / `username` fixture
    fields and in the assertions that read them back; both are covered here.

    REACHABILITY, since a clean tree cannot demonstrate it: replacing the membership test
    with `if False` SURVIVES here — on a tree with nothing to find, the guard is its own
    identity. What proves it runs is planting an unregistered author in a fixture and
    watching THIS assertion name it (mutant M2 of the battery in the PR that added this
    file). Same reasoning for the ID ledger above, whose reachability mutant is M1.
    """
    bad = sorted({(v, f) for v, f in _hits(AUTHOR_FIELD_RE, 1) + _hits(AUTHOR_KWARG_RE, 1)
                  if v not in ALLOWED_AUTHORS})
    assert not bad, (
        "comment author(s) under this skill are not on the invented-name ledger in this file.\n"
        "  If a real person: this repo is PUBLIC — rename in the FIXTURE and in every\n"
        "  assertion that reads it back, or the suite goes red for the wrong reason.\n"
        "  If invented: add it to ALLOWED_AUTHORS and say so.\n"
        f"  unregistered: {bad}")


def test_the_ledgers_have_no_dead_entries():
    """A ledger that only ever grows stops being a review. An entry nobody uses is either a
    fixture that was deleted (drop it) or a typo that is silently permitting nothing."""
    live_ids = {v for v, _ in _hits(TASK_ID_RE)}
    dead = sorted(ALLOWED_TASK_IDS - live_ids)
    assert not dead, f"ALLOWED_TASK_IDS entries no longer used anywhere — remove them: {dead}"

    live_authors = {v for v, _ in _hits(AUTHOR_FIELD_RE, 1) + _hits(AUTHOR_KWARG_RE, 1)}
    dead_a = sorted(ALLOWED_AUTHORS - live_authors)
    assert not dead_a, f"ALLOWED_AUTHORS entries no longer used anywhere — remove them: {dead_a}"
