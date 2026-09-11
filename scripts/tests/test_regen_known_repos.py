"""Tests for `scripts/regen-known-repos.py` — the mention-mapping generator.

🔴 THE FILE THIS GENERATES MUST NEVER BE TRACKED. `gh api user/repos` returns
private repos, and an earlier version of this generator wrote its output into
`scripts/collector/known_repos.py`, which was committed to this PUBLIC repo:
232 private repositories, 217 of them named nowhere else in the tree. The first
test below is the guard against that ever recurring; the rest pin the filters
that keep a wrong answer out of the mapping.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
GENERATOR = ROOT / "scripts" / "regen-known-repos.py"

_spec = importlib.util.spec_from_file_location("regen_known_repos_under_test", GENERATOR)
RG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RG)


# --------------------------------------------------------------------------- #
# 🔴 The disclosure guard
# --------------------------------------------------------------------------- #
# What an `owner/repo` looks like. Shared by BOTH detectors below — the mapping
# one asks it of a dict's VALUES, the universe one of a list's ELEMENTS — so the
# two arms cannot drift on what a repository name is.
_FULL_NAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\Z")

# A repo mapping is many DISTINCT names pointing at `owner/repo`. Counting
# occurrences instead flagged a dl-router fixture that repeats ONE key 45 times
# (`dupRelPath: "john-smith/75936.mov"`); the leaked file had 420 distinct keys.
#
# 🔴 THE NUMBER IS NOT THE GUARD — THE SHAPE IS, AND THAT IS WHY THIS ONE IS
# SAFE TO LEAVE ALONE. `looks_like_a_repo_mapping` counts keys inside ONE dict
# literal, so reaching 20 takes a single object mapping 20 distinct names at
# `owner/repo` — which is a repo mapping, not a coincidence. MEASURED across all
# 562 published `.json`/`.py` files on 2026-09-09: largest score **9**
# (`scripts/check-clickup-addressed/check-completion.py`, a hand-curated
# vocabulary), i.e. 11 keys of headroom in ONE literal. Under the text sweep this
# replaced, the largest was **19** — one added fixture from reddening a
# DISCLOSURE guard, because unrelated pairs anywhere in a 700-line file were
# summed together.
#
# ⚠ NO RATCHET IS ASSERTED ON THAT HEADROOM, DELIBERATELY. A "largest ordinary
# file must stay under N" test is a second fixed threshold, and the curated
# vocabulary above is a legitimate mapping that may grow — the guard would go
# red on an honest edit, which is the failure mode this change removed. The
# measurement is recorded here; the enforcement is the shape.
MAPPING_KEY_THRESHOLD = 20


def _json_dicts(text: str):
    """Every `dict` in `text` read as a JSON document, at any depth."""
    try:
        doc = json.loads(text.strip())
    except ValueError:
        return
    stack = [doc]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            yield node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def looks_like_a_repo_mapping(text: str) -> int:
    """How many DISTINCT `name -> "owner/repo"` keys the BIGGEST single MAPPING
    LITERAL in `text` carries.

    🔴 ONE LITERAL, NOT THE WHOLE FILE — AND THAT IS THE FIX, NOT A RELAXATION.
    This used to `re.findall` a `"key": "owner/repo"` pattern across the entire
    text and count the distinct keys, which sums pairs that have nothing to do
    with each other: unrelated fixtures, several small dicts, a docstring and a
    parametrize list all added into one number. MEASURED 2026-09-09, this very
    file scored **19** against a threshold of 20 — one added fixture from
    reddening a DISCLOSURE guard, and the fixes that come to hand under pressure
    (raise the number, exclude the file) are the ones that gut it. The number was
    never the guard; the SHAPE is. `looks_like_a_repo_universe` had already
    reached the same conclusion for the list shape and used `ast` for exactly
    this reason — this is that argument applied to the dict shape.

    🔴 BOTH INCIDENT SPELLINGS ARE STILL CAUGHT, and they are the whole point:
      * a `.py` module holding `KNOWN_REPOS = {...}` — the ORIGINAL incident, a
        generator writing its output into a module that was then committed. It
        is an `ast.Dict` literal, counted here.
      * a `.json` file of the same mapping — what the generator emits today. It
        is a JSON object, counted here, at ANY depth, so wrapping it in
        `{"repos": {...}}` does not hide it.
    A dict-literal count on the artefact is if anything TIGHTER than the old
    text sweep: 420 keys in one literal is 420 either way, and no ordinary file
    reaches 20 by accident (measurement beside `MAPPING_KEY_THRESHOLD`).

    ⚠ WHAT IT CANNOT SEE, stated rather than papered over:
      * a mapping SPLIT across several smaller literals, or built by a
        comprehension, a `dict(...)` call, or key-by-key assignment. That is the
        same residual `looks_like_a_repo_universe` already documents, and it is
        deliberate: this detects the DUMP, which is how this has gone wrong.
      * a mapping embedded as an escaped JSON string inside another document.
      * anything outside `.json`/`.py` — the sweep's file filter, unchanged.
    """
    best = 0
    for node in _json_dicts(text):
        best = max(best, len({k for k, v in node.items()
                              if isinstance(v, str) and _FULL_NAME_RE.match(v)}))
    # The Python-literal spelling. `ast` rather than a regex, so a dict is
    # recognised as a dict rather than as "some quoted strings near a colon" —
    # and a non-constant key (`'X'.lower()`, a variable) is skipped instead of
    # being scraped out of the source text.
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return best
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = set()
        for k, v in zip(node.keys, node.values):
            if (isinstance(k, ast.Constant) and isinstance(k.value, str)
                    and isinstance(v, ast.Constant) and isinstance(v.value, str)
                    and _FULL_NAME_RE.match(v.value)):
                keys.add(k.value)
        best = max(best, len(keys))
    return best


# 🔴 A SECOND SHAPE, BECAUSE THE GENERATOR NOW EMITS A SECOND FILE — AND THE
# DETECTOR ABOVE IS STRUCTURALLY BLIND TO IT. `known_universe.json` is a JSON
# LIST of `owner/repo` strings: it carries no `key: value` pairs at all, so
# `_PAIR_RE` finds nothing and `looks_like_a_repo_mapping` returns 0 for a file
# that is a WIDER disclosure than the mapping (it is deliberately unfiltered, so
# it names MORE private repositories). Committing one would have sailed past the
# guard that exists precisely to stop that — the same structural blindness that
# let the original incident through, in a new shape. (`_FULL_NAME_RE` is defined
# above `looks_like_a_repo_mapping`, which now shares it.)


def _owner_repo_count(items) -> int:
    return len({s for s in items
                if isinstance(s, str) and _FULL_NAME_RE.match(s)})


def looks_like_a_repo_universe(text: str) -> int:
    """How many DISTINCT `owner/repo` strings sit in a LIST LITERAL in `text`.

    🔴 STRUCTURAL, NOT TEXTUAL, AND THE FIRST VERSION WAS TEXTUAL AND USELESS.
    It counted every quoted `a/b` anywhere in the file and produced NINE false
    accusations on the first run — `claude/skills/clickup/package-lock.json` (46,
    every one an npm `node_modules/...` path) plus eight test files whose
    synthetic fixtures merely mention repos in passing. A guard that fires on
    ordinary files is a guard everyone learns to override.

    So the question asked here is the one that actually matters: is this file a
    LIST OF REPOSITORIES? That is the artefact `write_universe` emits, and it is
    a shape no lockfile and no docstring has. Both spellings are covered — a
    JSON document (the file itself, committed by accident) and a Python list
    literal (the shape the ORIGINAL incident took, in which a generator wrote
    its output into a `.py` module that was then committed).

    ⚠ RESIDUAL, STATED RATHER THAN PAPERED OVER: a universe split across several
    smaller literals, or built by a comprehension, is NOT counted. This detects
    the dump, which is the way this has actually gone wrong twice; it is not a
    general private-name scanner, and `claude/RULES.md` already records that no
    gate in this repo covers repo names in prose.
    """
    best = 0
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            doc = json.loads(stripped)
        except ValueError:
            doc = None
        if isinstance(doc, list):
            best = max(best, _owner_repo_count(doc))
    # The Python-literal spelling. `ast.parse` rather than a regex, so a list is
    # recognised as a list rather than as "some quoted strings near brackets".
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return best
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            best = max(best, _owner_repo_count(
                [e.value for e in node.elts if isinstance(e, ast.Constant)]))
    return best


# 🔴 A THIRD AND FOURTH SHAPE, AND THE TWO DETECTORS ABOVE ARE BLIND TO BOTH —
# WHICH IS THE WHOLE FINDING, NOT A FOOTNOTE. The generator now emits
# `known_ranges.json` and `mention-open.py` now writes `picks.jsonl`, and:
#
#   * `known_ranges.json` is `{"owner/repo": 1291}` — the private names are the
#     KEYS and the values are INTEGERS. `looks_like_a_repo_mapping` counts keys
#     whose VALUE matches `_FULL_NAME_RE`, so it scores this file **0**; it is
#     the exact MIRROR IMAGE of the shape that guard was built for.
#   * `picks.jsonl` is one JSON OBJECT PER LINE. It is not a JSON document at
#     all — `json.loads` of the whole file raises — so both detectors return 0,
#     AND `.jsonl` was not even in the sweep's file filter, so no detector would
#     ever have been handed its text.
#
# Both are 0600 files under `~/.config/mention-open/`, outside every checkout,
# for the same reason as their two siblings. These detectors exist so that a
# COPY of either, committed under any name, fails the suite — the same
# content-keyed claim the incident guard has always made, extended to the shapes
# that now exist. Proved able to fire by
# `test_the_incident_guard_CAN_GO_RED_on_the_TWO_NEWER_shapes`.


def looks_like_a_repo_range_table(text: str) -> int:
    """How many DISTINCT `"owner/repo" -> <number>` KEYS the biggest single dict
    literal in `text` carries.

    🔴 KEYS, NOT VALUES — AND THAT ONE WORD IS THE ENTIRE REASON THIS FUNCTION
    EXISTS. `looks_like_a_repo_mapping` asks "does a key point AT a repo name?",
    which is the resolution mapping's shape. A range table points FROM one, so
    that detector returns 0 on a file that names every private repository the
    operator has. Two detectors, two directions; neither subsumes the other.

    The value must be a NUMBER. That is what separates a range table from an
    arbitrary dict keyed by path-like strings — an npm lockfile's
    `"node_modules/x/y": {...}` maps to an OBJECT, and a `{"a/b": "c/d"}`
    mapping is the other detector's business.

    ⚠ SAME RESIDUALS AS ITS TWO SIBLINGS, and they are not restated here: split
    literals, comprehensions, and anything outside the sweep's file filter. This
    detects the DUMP.
    """
    best = 0

    def count(pairs) -> int:
        return len({k for k, v in pairs
                    if isinstance(k, str) and _FULL_NAME_RE.match(k)
                    and isinstance(v, (int, float)) and not isinstance(v, bool)})

    for node in _json_dicts(text):
        best = max(best, count(node.items()))
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return best
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        best = max(best, count(
            (k.value, v.value) for k, v in zip(node.keys, node.values)
            if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)))
    return best


def looks_like_a_pick_log(text: str) -> int:
    """How many DISTINCT `owner/repo` names a JSON-LINES document carries.

    🔴 A DIFFERENT PARSE, NOT A DIFFERENT PATTERN. `picks.jsonl` is one JSON
    object per LINE: `json.loads` of the whole text raises `ValueError`, so
    `_json_dicts` yields nothing and every detector above returns 0 however many
    private names the file holds. The fix is to read it the way it is written.

    A line that is not a JSON object is skipped rather than failing the file —
    the real artefact is append-only and can carry one torn last line.

    ⚠ IT COUNTS NAMES AT ANY DEPTH IN EACH ROW, not just a `repo` field. A guard
    keyed on today's field name would be walked past by tomorrow's rename, and
    the hazard is the NAMES being in the tree, not which key holds them.
    """
    names: set[str] = set()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # Two or more object lines, or it is not a JSON-LINES document — a single
    # JSON object spread over one line is the other detectors' business, and a
    # one-line file cannot be a log.
    rows = 0
    for line in lines:
        try:
            doc = json.loads(line.strip())
        except ValueError:
            continue
        if not isinstance(doc, dict):
            continue
        rows += 1
        stack = [doc]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, str) and _FULL_NAME_RE.match(node):
                names.add(node)
    return len(names) if rows >= 2 else 0


def test_the_mapping_detector_FIRES_on_a_realistic_mapping():
    """🔴 THE NEGATIVE CONTROL — without it, the sweep below is a zero that may
    be indistinguishable from a detector wired to nothing. Built from realistic
    data in both spellings the generator emits, not a textbook fixture."""
    synthetic = "KNOWN_REPOS = {\n" + "".join(
        f"    '{n}{i}': 'gardenersguild/{n}{i}',\n    '{n}{i}'.lower(): "
        f"'gardenersguild/{n}{i}',\n"
        for i, n in enumerate(["Trowelcast", "SledgeHorn", "PloughShare"] * 9)
    ) + "}\n"
    assert looks_like_a_repo_mapping(synthetic) >= MAPPING_KEY_THRESHOLD
    # …and it does NOT fire on an ordinary small dict of the same shape.
    assert looks_like_a_repo_mapping(
        "{'a': 'o/a', 'b': 'o/b', 'c': 'o/c'}") < MAPPING_KEY_THRESHOLD


def test_the_mapping_detector_FIRES_on_the_JSON_the_generator_ACTUALLY_WRITES():
    """🔴 THE SECOND POSITIVE CONTROL, AND IT IS THE ONE THAT MATTERS TODAY.

    The `.py` control above is the ORIGINAL incident's shape — a generator's
    output pasted into a module. What the generator emits NOW is a JSON object,
    and a detector that only understood Python would score it 0 while the file
    sat in the tree. Built by `write_mapping` itself rather than by hand, so
    this cannot drift from the artefact it is about.

    ⚠ AT DEPTH TOO. A committed copy is as likely to arrive wrapped
    (`{"generated": …, "repos": {…}}`) as bare, and a top-level-only reader
    would score that 0.
    """
    mapping = {f"{n}{i}": f"gardenersguild/{n}{i}"
               for i, n in enumerate(["Trowelcast", "SledgeHorn",
                                      "PloughShare"] * 9)}
    bare = json.dumps(mapping, indent=1)
    assert looks_like_a_repo_mapping(bare) >= MAPPING_KEY_THRESHOLD
    wrapped = json.dumps({"generated": "2026-09-09T00:00:00Z", "repos": mapping})
    assert looks_like_a_repo_mapping(wrapped) >= MAPPING_KEY_THRESHOLD, (
        "a mapping nested one level down scored below the threshold — the JSON "
        "reader is looking at the top level only")


def test_the_mapping_detector_counts_ONE_LITERAL_not_the_whole_file():
    """🔴 THE STRUCTURAL PROPERTY, ASSERTED — this is what replaced a threshold
    the next fixture was going to trip.

    Pairs scattered across many unrelated small dicts are not a repo mapping,
    however many of them a long file happens to contain. Summing them is what
    put THIS file at 19 of 20 on 2026-09-09; the arithmetic said "one fixture
    from red" while nothing in the tree was anywhere near being a dump.

    ⚠ THIS IS A DELIBERATE NARROWING AND IT IS NAMED IN
    `looks_like_a_repo_mapping`'s docstring. What it gives up — a mapping split
    across several literals — has never been how this went wrong; what it buys
    is a guard that fires on the artefact and on nothing else, which is the only
    kind anyone leaves switched on.
    """
    scattered = "\n".join(
        f"CASE_{i} = {{'name{i}': 'gardenersguild/thing{i}'}}"
        for i in range(3 * MAPPING_KEY_THRESHOLD)
    )
    # The old text sweep counted 60 distinct keys here.
    assert len(re.findall(r"'name\d+':", scattered)) == 3 * MAPPING_KEY_THRESHOLD
    assert looks_like_a_repo_mapping(scattered) == 1, (
        "unrelated one-entry dicts are being summed into one score again")


def test_the_UNIVERSE_detector_FIRES_and_the_mapping_one_is_BLIND_to_it():
    """🔴 THE NEGATIVE CONTROL FOR THE SECOND SHAPE — and it asserts the BLIND
    SPOT explicitly, because that is the finding rather than a side note.

    Built from a REALISTIC artefact: the exact JSON `write_universe` emits, not
    a textbook fixture. `looks_like_a_repo_mapping` returns 0 on it — a list has
    no `key: value` pairs — so before this detector existed, committing the
    picker universe to this public repo would have passed the incident guard
    that exists to prevent exactly that.
    """
    universe = json.dumps(sorted(
        f"gardenersguild/{n}{i}"
        for i, n in enumerate(["Trowelcast", "SledgeHorn", "PloughShare"] * 9)
    ), indent=1)
    assert looks_like_a_repo_universe(universe) >= MAPPING_KEY_THRESHOLD
    assert looks_like_a_repo_mapping(universe) == 0, (
        "the pair matcher must be shown BLIND to this shape — if it ever fires "
        "here, the second detector's justification has changed and this test "
        "is the one that should say so")
    # …and it does NOT fire on a file that merely mentions a few repos, which is
    # every docstring and comment in this tree.
    assert looks_like_a_repo_universe(
        "see 'civitai/talos-infra' and 'acme/widget'") < MAPPING_KEY_THRESHOLD


# Directories that exist only in a working checkout and are gitignored there.
# Only consulted on the WALK path — `git ls-files` already excludes them.
_WALK_SKIP = {".git", "node_modules", "__pycache__", ".venv", ".direnv",
              "result", ".mypy_cache", ".pytest_cache"}

# 🔴 `.jsonl` IS IN THE FILTER BECAUSE A DETECTOR THAT IS NEVER HANDED THE TEXT
# IS NOT A DETECTOR. `picks.jsonl` is the fourth artefact in this family and the
# first with an extension this sweep did not look at, so `looks_like_a_pick_log`
# would have scored a committed copy exactly as often as it was called: never.
# Pinned by `test_the_sweep_actually_LOOKS_at_jsonl_files`.
_SWEPT_SUFFIXES = (".json", ".py", ".jsonl")


def candidate_files(root: Path | None = None) -> tuple[str, list[Path]]:
    """(how, files) — every `.json`/`.py` this repo would publish.

    🔴 TWO TIERS, TWO VIEWS, AND THE GUARD MUST WORK IN BOTH. `git ls-files` is
    the view the other content gates use, but the sandbox check derivation
    builds from a `cp -r` store copy with NO `.git`, so git fails there. A skip
    would make a DISCLOSURE guard invisible in the very tier the merge gates on
    — so the fallback WALKS the tree instead, which is if anything a wider view
    (it is exactly what was copied in). `how` is returned so the assertion
    message can say which view produced the verdict.
    """
    root = root or ROOT
    tracked = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                             capture_output=True, text=True, timeout=60)
    if tracked.returncode == 0 and tracked.stdout.strip():
        return "git ls-files", [
            root / r for r in tracked.stdout.split("\0")
            if r.endswith(_SWEPT_SUFFIXES)]

    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _WALK_SKIP]
        for fn in filenames:
            if fn.endswith(_SWEPT_SUFFIXES):
                found.append(Path(dirpath) / fn)
    return "filesystem walk (no .git — sandbox tier)", found


def disclosure_offenders(files, root: Path) -> list[str]:
    """Every file in `files` that IS a repo mapping or a picker universe.

    Lifted out of the guard below so the guard's own negative control can drive
    the SAME loop over a planted tree. It used to be inline, which left the only
    evidence that the sweep can go red as a synthetic call to
    `looks_like_a_repo_mapping` — a claim about the detector, never about the
    loop that reads files and assembles the verdict.
    """
    offenders = []
    for path in files:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        keys = looks_like_a_repo_mapping(text)
        if keys >= MAPPING_KEY_THRESHOLD:
            offenders.append(f"{path.relative_to(root)} ({keys} distinct repo keys)")
            continue
        # 🔴 THE UNIVERSE SHAPE, CHECKED ON THE SAME SWEEP RATHER THAN IN A
        # SECOND TEST — one walk, one `len(files) > 100` floor, one place for a
        # future third shape. A separate test would need its own floor and would
        # be the one nobody notices has stopped walking anything.
        fulls = looks_like_a_repo_universe(text)
        if fulls >= MAPPING_KEY_THRESHOLD:
            offenders.append(
                f"{path.relative_to(root)} ({fulls} distinct owner/repo names)")
            continue
        # …and the third and fourth shapes, on the SAME sweep, as that comment
        # promised. `known_ranges.json` names its repositories in the KEYS and
        # `picks.jsonl` in a per-line object — both score 0 under the two
        # detectors above.
        ranged = looks_like_a_repo_range_table(text)
        if ranged >= MAPPING_KEY_THRESHOLD:
            offenders.append(
                f"{path.relative_to(root)} ({ranged} repo keys in a range table)")
            continue
        picked = looks_like_a_pick_log(text)
        if picked >= MAPPING_KEY_THRESHOLD:
            offenders.append(
                f"{path.relative_to(root)} ({picked} repo names in a pick log)")
    return offenders


def test_the_incident_guard_CAN_GO_RED_on_both_incident_spellings(tmp_path):
    """🔴 THE NEGATIVE CONTROL FOR THE GUARD BELOW, on a PLANTED tree.

    The guard's passing verdict is an empty list, which is indistinguishable
    from a loop wired to nothing — so this feeds it the two artefacts it exists
    to catch and watches the number move. Both spellings, because the detector
    was rewritten to be structural and "still catches the incident" is the claim
    that rewrite has to keep:

      * `.py` — a module holding `KNOWN_REPOS = {...}`. THE ORIGINAL INCIDENT: a
        generator's output pasted into a committed module.
      * `.json` — the mapping file the generator writes today, verbatim.

    Both are built by `RG.write_mapping`/`json.dumps` from a realistic 27-entry
    mapping rather than by hand, and an ordinary file sits beside them so the
    result is a SELECTION and not "everything is an offender".
    """
    mapping = {f"{n}{i}": f"gardenersguild/{n}{i}"
               for i, n in enumerate(["Trowelcast", "SledgeHorn",
                                      "PloughShare"] * 9)}
    (tmp_path / "leaked.json").write_text(json.dumps(mapping, indent=1))
    (tmp_path / "pasted.py").write_text(
        "KNOWN_REPOS = " + repr(mapping) + "\n")
    (tmp_path / "ordinary.py").write_text(
        "# mentions gardenersguild/trowelcast in passing\nX = {'a': 'o/a'}\n")

    how, files = candidate_files(tmp_path)
    assert len(files) == 3, sorted(f.name for f in files)
    named = sorted(o.split(" ")[0] for o in disclosure_offenders(files, tmp_path))
    assert named == ["leaked.json", "pasted.py"], (
        f"the guard did not flag both incident spellings, and only them: "
        f"{disclosure_offenders(files, tmp_path)}")


def _synthetic_repos(n: int = 27) -> list[str]:
    """`n` realistic-looking `owner/repo` names. Synthetic on purpose — this repo
    is PUBLIC and the real universe is private (see the module docstring)."""
    return [f"gardenersguild/{n_}{i}" for i, n_ in
            enumerate(["Trowelcast", "SledgeHorn", "PloughShare"] * ((n + 2) // 3))][:n]


def test_the_RANGE_TABLE_detector_FIRES_and_the_OTHER_TWO_are_BLIND_to_it():
    """🔴 THE NEGATIVE CONTROL FOR THE THIRD SHAPE — and it asserts the BLIND
    SPOT explicitly, because the blind spot IS the finding.

    Built from the exact JSON `RG.write_ranges` emits, not a hand-typed fixture.
    `known_ranges.json` names its private repositories in the KEYS and maps them
    to INTEGERS, which is the mirror image of the mapping's shape — so the
    mapping detector, which asks whether a VALUE is a repo name, returns 0, and
    the universe detector, which wants a list, returns 0 too. Before this
    detector existed, committing the range table to this public repo would have
    sailed past both guards that exist to stop exactly that.
    """
    table = json.dumps({r.lower(): 1291 - i * 7
                        for i, r in enumerate(_synthetic_repos())},
                       indent=1, sort_keys=True)
    assert looks_like_a_repo_range_table(table) >= MAPPING_KEY_THRESHOLD
    assert looks_like_a_repo_mapping(table) == 0, (
        "the mapping detector must be shown BLIND to a KEYED-BY-REPO table — "
        "if it ever fires here, this detector's justification has changed and "
        "this test is the one that should say so")
    assert looks_like_a_repo_universe(table) == 0, (
        "the universe detector must be shown BLIND to a dict shape")
    # …and it does NOT fire on an ordinary small dict, nor on a lockfile-shaped
    # dict whose path-like keys map to OBJECTS rather than numbers.
    assert looks_like_a_repo_range_table('{"a/b": 1, "c/d": 2}') < MAPPING_KEY_THRESHOLD
    lockish = json.dumps({f"node_modules/pkg{i}": {"version": "1.0.0"}
                          for i in range(60)})
    assert looks_like_a_repo_range_table(lockish) == 0, (
        "a lockfile whose path-like keys map to OBJECTS is not a range table")


def test_the_PICK_LOG_detector_FIRES_and_EVERY_OTHER_DETECTOR_is_BLIND_to_it():
    """🔴 THE NEGATIVE CONTROL FOR THE FOURTH SHAPE, and the one with TWO blind
    spots stacked: the detectors could not read it, and the sweep would never
    have opened it (see `_SWEPT_SUFFIXES`).

    JSON-LINES is not a JSON document — `json.loads` of the whole text raises —
    so every detector that starts by parsing the file is blind to it however
    many private repository names it holds. Built the way `record_pick` actually
    writes, one object per line.

    ⚠ THE MAPPING DETECTOR SCORES **1**, NOT 0, AND THE MECHANISM IS WORTH
    KNOWING. Its `ast` half parses the text as PYTHON, where each JSONL line is
    a separate one-key dict literal expression — and it takes the biggest SINGLE
    literal, so its score is capped at 1 for a log of ANY length. That is the
    blindness exactly: not that it cannot see the names, but that it structurally
    cannot count them. The claim asserted is therefore "would not flag this
    file", which is the operative one.
    """
    log = "\n".join(json.dumps({"n": 1200 + i, "repo": r, "t": 1757000000 + i},
                               sort_keys=True)
                    for i, r in enumerate(_synthetic_repos())) + "\n"
    assert looks_like_a_pick_log(log) >= MAPPING_KEY_THRESHOLD
    for blind, why in ((looks_like_a_repo_mapping, "mapping"),
                       (looks_like_a_repo_universe, "universe"),
                       (looks_like_a_repo_range_table, "range table")):
        assert blind(log) < MAPPING_KEY_THRESHOLD, (
            f"the {why} detector would now FLAG a JSON-LINES pick log "
            f"(scored {blind(log)}) — its justification has changed and this "
            f"test is the one that should say so")
    # …and the mapping detector's cap is asserted rather than described: a log
    # ten times longer must still score 1, or the sentence above is wrong.
    longer = "\n".join(json.dumps({"n": 1200 + i, "repo": f"o/r{i}"})
                       for i in range(300)) + "\n"
    assert looks_like_a_repo_mapping(longer) == 1, (
        "the mapping detector is no longer capped at one JSONL line — re-read "
        "this test's docstring before changing it")
    # …and it does NOT fire on an ordinary JSONL transcript that merely mentions
    # a couple of repos, which is the shape every fixture in this tree has.
    chatty = "\n".join(json.dumps({"text": "see acme/widget and o/b"})
                       for _ in range(50))
    assert looks_like_a_pick_log(chatty) < MAPPING_KEY_THRESHOLD


def test_the_sweep_actually_LOOKS_at_jsonl_files(tmp_path):
    """🔴 THE POSITIVE CONTROL ON THE FILE FILTER, WHICH IS A SEPARATE CLAIM
    FROM THE DETECTOR WORKING.

    `looks_like_a_pick_log` going red on a string proves nothing about whether
    the sweep ever hands it one: `.jsonl` was absent from the suffix tuple, so a
    committed `picks.jsonl` would have been invisible to a perfectly good
    detector. This plants one in a tree and asserts the WHOLE LOOP names it.
    """
    log = "\n".join(json.dumps({"n": 1200 + i, "repo": r, "t": 1757000000 + i})
                    for i, r in enumerate(_synthetic_repos())) + "\n"
    (tmp_path / "picks.jsonl").write_text(log)
    (tmp_path / "ordinary.jsonl").write_text(
        '{"text": "mentions acme/widget"}\n{"text": "and o/b"}\n')
    how, files = candidate_files(tmp_path)
    assert sorted(f.name for f in files) == ["ordinary.jsonl", "picks.jsonl"], (
        f"the sweep did not collect .jsonl via {how}: "
        f"{sorted(f.name for f in files)}")
    named = [o.split(" ")[0] for o in disclosure_offenders(files, tmp_path)]
    assert named == ["picks.jsonl"], (
        f"the sweep flagged {named}, not the planted pick log alone")


def test_the_incident_guard_CAN_GO_RED_on_the_TWO_NEWER_shapes(tmp_path):
    """🔴 THE NEGATIVE CONTROL FOR THE GUARD ITSELF on the third and fourth
    artefacts, driving the SAME `disclosure_offenders` loop over a planted tree
    — the sibling of `test_the_incident_guard_CAN_GO_RED_on_both_incident_
    spellings`, and written separately rather than folded into it because a
    control built only from shapes the old detectors already caught proves
    nothing about the detectors that were added for the new ones.

    Both artefacts are produced by the real writers (`RG.write_ranges`, and the
    line format `record_pick` emits), and an ordinary file of each extension
    sits beside them so the result is a SELECTION rather than "everything is an
    offender".
    """
    repos = _synthetic_repos()
    RG.write_ranges({r.lower(): 1291 - i * 7 for i, r in enumerate(repos)},
                    tmp_path / "ranges.json")
    (tmp_path / "picks.jsonl").write_text("\n".join(
        json.dumps({"n": 1200 + i, "repo": r, "t": 1757000000 + i},
                   sort_keys=True) for i, r in enumerate(repos)) + "\n")
    (tmp_path / "ordinary.json").write_text(json.dumps({"a/b": 1, "c/d": 2}))
    (tmp_path / "ordinary.jsonl").write_text(
        '{"text": "mentions acme/widget"}\n{"text": "and o/b"}\n')

    how, files = candidate_files(tmp_path)
    assert len(files) == 4, sorted(f.name for f in files)
    named = sorted(o.split(" ")[0] for o in disclosure_offenders(files, tmp_path))
    assert named == ["picks.jsonl", "ranges.json"], (
        f"the guard did not flag both NEW artefacts, and only them: "
        f"{disclosure_offenders(files, tmp_path)}")


def test_the_generated_mapping_is_NOT_published_anywhere_in_this_repo():
    """🔴 THE INCIDENT GUARD. This repo is public and the mapping names private
    repositories. Keyed on CONTENT rather than on a filename, so a copy under
    any name or in any directory fails it: the claim is 'no published file IS
    this mapping', not 'the old path is absent'.

    ⚠ Its passing verdict is an EMPTY LIST, so the proof that it can go red is
    `test_the_incident_guard_CAN_GO_RED_on_both_incident_spellings`, which
    drives the same `disclosure_offenders` loop over a planted tree.
    """
    how, files = candidate_files()
    assert len(files) > 100, (
        f"the sweep examined only {len(files)} file(s) via {how} — a zero from "
        f"a sweep that walked nothing is the failure, not the all-clear")
    offenders = disclosure_offenders(files, ROOT)
    assert offenders == [], (
        f"file(s) look like a repo mapping or picker universe (via {how}): "
        f"{offenders}. Both name PRIVATE repositories and this repo is PUBLIC — "
        f"they belong at {RG.DEFAULT_PATH} and {RG.DEFAULT_UNIVERSE_PATH}, "
        f"outside every checkout.")


def test_the_default_output_path_is_outside_every_checkout():
    """The path is the whole mitigation, so it is pinned. Under the operator's
    config dir, not under any repo, not under /tmp."""
    p = RG.DEFAULT_PATH
    assert p.is_absolute()
    assert p.name == "known_repos.json"
    assert "workspace" not in p.parts, p
    assert p.parent.name == "mention-open"


def test_the_written_file_is_not_world_readable(tmp_path):
    """It names private repositories, on a machine with other accounts possible."""
    out = tmp_path / "sub" / "known_repos.json"
    RG.write_mapping({"a": "o/a"}, out)
    assert json.loads(out.read_text()) == {"a": "o/a"}
    assert oct(os.stat(out).st_mode)[-3:] == "600"


# --------------------------------------------------------------------------- #
# build_mapping — the filters
# --------------------------------------------------------------------------- #
def _row(full, has_issues=True):
    return {"full_name": full, "has_issues": has_issues}


def test_a_repo_is_mapped_under_both_its_own_case_and_lowercase():
    """`mention_scan._resolve_repo` does an EXACT dict lookup with no case
    folding anywhere, and GitHub names are case-insensitive — so a
    canonical-case key alone cannot match `sledgehorn#12`."""
    out = RG.build_mapping([_row("gardenersguild/SledgeHorn")], {})
    assert out["SledgeHorn"] == "gardenersguild/SledgeHorn"
    assert out["sledgehorn"] == "gardenersguild/SledgeHorn"


def test_a_repo_with_issues_DISABLED_is_dropped_entirely():
    """🔴 `repo#N` builds an ISSUES url. A repo with issues disabled 404s for
    every N, so mapping it produces a confident wrong page rather than a
    refusal. Measured on the first version: 44 of 383, including the fork whose
    `#100` was the example used to justify the mapping."""
    out = RG.build_mapping(
        [_row("gardenersguild/trowelcast", has_issues=False),
         _row("gardenersguild/sledgehorn", has_issues=True)], {})
    assert "trowelcast" not in out and "sledgehorn" in out


def test_a_name_owned_by_TWO_owners_is_dropped_rather_than_arbitrated():
    """🔴 Last-write-wins silently picked one. Measured on the first version:
    7 collisions, and `bitdex` resolved to a third party rather than the
    client. An ambiguous name must resolve to NOTHING — the operator writes
    `owner/repo#N`, which is source 1."""
    out = RG.build_mapping(
        [_row("gardenersguild/trowelcast"), _row("hobbyist/trowelcast")], {})
    assert "trowelcast" not in out
    assert "Trowelcast" not in out
    # …and the collision does not take an unrelated repo down with it.
    out2 = RG.build_mapping(
        [_row("gardenersguild/trowelcast"), _row("hobbyist/trowelcast"),
         _row("gardenersguild/sledgehorn")], {})
    assert out2["sledgehorn"] == "gardenersguild/sledgehorn"


def test_a_local_checkout_settles_a_name_the_api_had_to_drop():
    """A checkout is a MEASUREMENT of this disk, so it is the authority on its
    own owner and outranks the ambiguity."""
    out = RG.build_mapping(
        [_row("gardenersguild/trowelcast"), _row("hobbyist/trowelcast")],
        {"trowelcast": "gardenersguild/trowelcast"})
    assert out["trowelcast"] == "gardenersguild/trowelcast"


def test_a_checkout_DISAGREEING_with_the_api_row_resolves_to_nothing():
    """🔴 THE GENERATOR IS NARROWER THAN THE HANDLER, ON PURPOSE — and these two
    tests used to contradict each other, which is how the contradiction was
    found. At click time `mention-open.py` has just MEASURED the remote, so a
    checkout wins there. This file is a SNAPSHOT that can be months old, so a
    checkout disagreeing with an API row is two claims about one name with no
    way to tell which is current: the module's answer to that is NOTHING, and
    the operator writes `owner/repo#N`."""
    out = RG.build_mapping([_row("hobbyist/sledgehorn")],
                           {"sledgehorn": "gardenersguild/sledgehorn"})
    assert "sledgehorn" not in out, out.get("sledgehorn")


def test_a_checkout_directory_named_differently_maps_BOTH_spellings():
    """A checkout whose DIRECTORY name differs from the repo name — the shape
    that occurs when a clone was named after the project rather than the repo.
    Both spellings must resolve, because either is what the operator types."""
    out = RG.build_mapping([], {"toolshed-cluster": "gardenersguild/toolshed-infra"})
    assert out["toolshed-cluster"] == "gardenersguild/toolshed-infra"
    assert out["toolshed-infra"] == "gardenersguild/toolshed-infra"


def test_a_malformed_row_is_skipped_not_crashed_on():
    out = RG.build_mapping(
        [{"full_name": None, "has_issues": True},
         {"has_issues": True},
         _row("no-slash-here"),
         _row("gardenersguild/sledgehorn")], {})
    assert out == {"sledgehorn": "gardenersguild/sledgehorn",
                   "sledgehorn".capitalize().lower(): "gardenersguild/sledgehorn"}


# --------------------------------------------------------------------------- #
# read_api_repos — the losslessness floor
# --------------------------------------------------------------------------- #
def test_a_gh_failure_RAISES_rather_than_yielding_a_smaller_mapping(monkeypatch):
    """🔴 The previous generator caught every exception, carried on, and wrote a
    file holding only the local overlay — then printed `wrote …` and exited 0.
    That is the same lossy-regeneration class this change exists to fix."""
    import types
    monkeypatch.setattr(RG.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=1, stdout="", stderr="gh: not authenticated"))
    with pytest.raises(RuntimeError, match="gh exited 1"):
        RG.read_api_repos()


def test_a_SHORT_result_is_refused_even_though_gh_exited_zero(monkeypatch):
    """A truncated page reads as success. The floor is what makes it loud."""
    import types
    rows = "".join(json.dumps({"full_name": f"o/r{i}", "has_issues": True}) + "\n"
                   for i in range(RG.MIN_API_REPOS - 1))
    monkeypatch.setattr(RG.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout=rows, stderr=""))
    with pytest.raises(RuntimeError, match="below the floor"):
        RG.read_api_repos()
    # One more row clears it — the floor is the boundary, not a blanket refusal.
    rows += json.dumps({"full_name": "o/last", "has_issues": True}) + "\n"
    monkeypatch.setattr(RG.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout=rows, stderr=""))
    assert len(RG.read_api_repos()) == RG.MIN_API_REPOS


def test_main_exits_nonzero_and_writes_NOTHING_when_gh_fails(monkeypatch, tmp_path, capsys):
    """🔴 THE READINESS CHECK IS STUBBED, AND WITHOUT THAT LINE THIS TEST READ
    THE HOST INSTEAD OF THE CODE — measured, in the tier that gates the merge.

    `main()` asks `require_gh_ready()` before it reaches `read_api_repos`, so
    stubbing only the latter makes the outcome depend on whether the MACHINE has
    `gh`: the dev host does (readiness passes, the stub throws, exit 3 — green),
    the nix sandbox does not (readiness fails first, exit 4 — red). It passed on
    the dev host and failed in the sandbox for that reason alone, which is the
    two-tier blindness `CLAUDE.md` names: each tier's environment silently
    decides what executes, so greening one while the other is unobservable moves
    the bug rather than removing it.

    The arm under test is the AUTHENTICATED-BUT-BROKEN one — gh is present and
    logged in, and the API call still failed. That is exit 3 and it must toast.
    Pinning readiness explicitly is what makes the assertion about the code.
    """
    out = tmp_path / "known_repos.json"
    monkeypatch.setattr(RG, "require_gh_ready", lambda: None)
    monkeypatch.setattr(RG, "read_api_repos",
                        lambda: (_ for _ in ()).throw(RuntimeError("gh exited 4: no auth")))
    assert RG.main(["--path", str(out)]) == RG.EXIT_FAILED == 3
    assert not out.exists()
    assert "no auth" in capsys.readouterr().err


def test_this_suite_never_asks_the_HOST_whether_gh_is_installed(monkeypatch,
                                                                tmp_path):
    """🔴 THE GUARD ON THE GUARD ABOVE, because the defect it fixes is invisible
    on the machine anyone develops on.

    Every `main()` path now runs `require_gh_ready()`, which shells out to
    `gh auth status`. A test that neither stubs `subprocess.run` nor patches
    `require_gh_ready` therefore reads the DEVELOPER'S MACHINE, and will keep
    passing here while failing in the sandbox — a whole class, not one test.

    This asserts the class is closed by construction: with the real readiness
    check in force and `gh` made unreachable, `main()` returns NOT_CONFIGURED.
    Any future test that forgets to stub gets that 4 rather than a plausible
    wrong answer, and this test says why."""
    def no_gh(cmd, *a, **k):
        if cmd[:3] == ["gh", "auth", "status"]:
            raise FileNotFoundError("gh")
        raise AssertionError(f"nothing may run after readiness fails: {cmd}")
    monkeypatch.setattr(RG.subprocess, "run", no_gh)
    assert RG.main(["--path", str(tmp_path / "m.json"),
                    "--universe-path", str(tmp_path / "u.json")]) == 4


# --------------------------------------------------------------------------- #
# read_local_repos — worktrees
# --------------------------------------------------------------------------- #
def test_a_linked_worktree_is_skipped(tmp_path):
    """A linked worktree's `.git` is a FILE. It shares the base clone's remote,
    so it adds no owner — and its transient name (`devrc-integ-1261`) churned
    the generated file on every run."""
    real = tmp_path / "realclone"
    (real / ".git").mkdir(parents=True)
    wt = tmp_path / "somebranch-wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /elsewhere/.git/worktrees/somebranch-wt\n")
    seen = []

    def fake_run(cmd, cwd=None, **kw):
        import types
        seen.append(Path(cwd).name)
        return types.SimpleNamespace(
            returncode=0, stdout="git@github.com:gardenersguild/realclone.git\n", stderr="")

    RG.subprocess.run, saved = fake_run, RG.subprocess.run
    try:
        out = RG.read_local_repos(tmp_path)
    finally:
        RG.subprocess.run = saved
    assert out == {"realclone": "gardenersguild/realclone"}
    assert seen == ["realclone"], "the worktree must not even be probed"


def test_an_absent_workspace_is_empty_not_an_error(tmp_path):
    assert RG.read_local_repos(tmp_path / "nope") == {}


def _synthetic_mapping(n: int = 30) -> str:
    return "KNOWN_REPOS = {\n" + "".join(
        f"    'plot{i}': 'gardenersguild/plot{i}',\n" for i in range(n)) + "}\n"


def test_the_sweep_falls_back_to_a_WALK_where_there_is_no_git(tmp_path):
    """🔴 THE SANDBOX TIER HAS NO `.git` — it builds from a `cp -r` store copy.
    Measured: the first version of this guard shelled out to `git ls-files`,
    passed on the dev host, and FAILED in the sandbox with `fatal: not a git
    repository`. Had it been written to skip instead of fail, a disclosure
    guard would have been silently inert in the tier that gates the merge.

    Proven on a tree with no `.git` at all: the walk finds the file, and it
    still skips the directories git would have excluded."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "anything.py").write_text(_synthetic_mapping())
    (tmp_path / "node_modules" / "dep").mkdir(parents=True)
    (tmp_path / "node_modules" / "dep" / "vendored.json").write_text(_synthetic_mapping())

    how, files = candidate_files(tmp_path)
    assert how.startswith("filesystem walk"), how
    rels = sorted(str(f.relative_to(tmp_path)) for f in files)
    assert rels == ["pkg/anything.py"], rels
    assert looks_like_a_repo_mapping(files[0].read_text()) >= MAPPING_KEY_THRESHOLD


def test_the_sweep_prefers_git_ls_files_where_there_IS_a_git(tmp_path):
    """The other branch, so neither is asserted only by absence. A real init +
    commit, because `ls-files` reports nothing for an unstaged file — which is
    exactly the empty-output case the fallback must not be fooled by."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    (tmp_path / "kept.py").write_text("x = 1\n")
    (tmp_path / "untracked.py").write_text(_synthetic_mapping())
    subprocess.run(["git", "-C", str(tmp_path), "add", "kept.py"], check=True, timeout=60)
    how, files = candidate_files(tmp_path)
    assert how == "git ls-files", how
    assert [f.name for f in files] == ["kept.py"], "an UNTRACKED file is not published"


# --------------------------------------------------------------------------- #
# 🔴 The local overlay obeys the SAME filters as the API path
#
# It did not, at first: local entries were written in unconditionally, which
# put an issues-disabled repo back into the mapping (measured: 1 such key in
# the file generated that day) and restored last-write-wins between two
# checkouts sharing a bare name — the two wrong answers this function exists to
# prevent, reintroduced through the back door.
# --------------------------------------------------------------------------- #
def test_a_local_checkout_of_an_ISSUES_DISABLED_repo_is_still_dropped():
    """A checkout is authoritative about its OWNER. It is not evidence that the
    repo accepts issues, and `repo#N` builds an issues URL."""
    out = RG.build_mapping(
        [_row("gardenersguild/trowelcast", has_issues=False)],
        {"trowelcast": "gardenersguild/trowelcast"})
    assert "trowelcast" not in out, out


def test_TWO_checkouts_sharing_a_bare_name_are_dropped_not_arbitrated():
    """The same ambiguity the API path drops. `~/workspace/plot-a` and
    `~/workspace/plot-b` both cloning a repo named `plotwidget` from different
    owners must resolve to NOTHING, not to whichever `iterdir()` returned last."""
    out = RG.build_mapping([], {"plot-a": "gardenersguild/plotwidget",
                                "plot-b": "rivalorg/plotwidget"})
    assert "plotwidget" not in out
    # …and each directory name, being unambiguous on its own, still resolves.
    assert out["plot-a"] == "gardenersguild/plotwidget"
    assert out["plot-b"] == "rivalorg/plotwidget"


def test_a_local_checkout_does_not_silently_replace_an_unambiguous_api_row():
    """A checkout whose BARE name collides with a different API repo must not
    overwrite it without the collision being noticed."""
    out = RG.build_mapping(
        [_row("gardenersguild/plotwidget")],
        {"vendored-plotwidget": "rivalorg/plotwidget"})
    # Two different repos now claim the bare name `plotwidget` — ambiguous, so
    # NEITHER gets it. Silently taking the checkout's is last-write-wins across
    # sources, and it is the API row that would vanish.
    assert "plotwidget" not in out, out.get("plotwidget")
    assert out["vendored-plotwidget"] == "rivalorg/plotwidget"


def test_a_checkout_AGREEING_with_the_api_row_is_not_a_collision():
    """The other side of the boundary: a checkout of the same repo the API
    already mapped is source 3 CONFIRMING the owner, not a second claimant. A
    rule that dropped this would empty the mapping of everything checked out."""
    out = RG.build_mapping([_row("gardenersguild/plotwidget")],
                           {"plotwidget": "gardenersguild/plotwidget"})
    assert out["plotwidget"] == "gardenersguild/plotwidget"


def test_THREE_checkouts_two_of_one_repo_and_a_namesake_still_refuse():
    """Three checkouts, two of one repo plus a namesake — the shape that
    exposed a redundant second clause: whichever entry writes the spelling
    first, the next disagreeing one must drop it, and the DROP MUST BE FINAL so
    the third entry cannot write it back. (The operator really does keep
    duplicate checkouts of one repo.)"""
    out = RG.build_mapping([], {"a-copy": "gardenersguild/plotwidget",
                                "b-copy": "rivalorg/plotwidget",
                                "c-copy": "gardenersguild/plotwidget"})
    assert "plotwidget" not in out, out.get("plotwidget")


def test_a_checkout_cloned_via_a_LOWERCASE_url_is_still_matched_to_its_api_row():
    """🔴 CASE-FOLDED COMPARISON. GitHub URLs are case-insensitive, so
    `git clone .../acme/plotwidget` yields a remote spelled differently from the
    API's `acme/PlotWidget`. An exact-case comparison treated them as unrelated
    and let an ISSUES-DISABLED repo back into the mapping."""
    out = RG.build_mapping([_row("acme/PlotWidget", has_issues=False)],
                           {"plotwidget": "acme/plotwidget"})
    assert "plotwidget" not in out, out.get("plotwidget")
    # The MIRROR spelling: the API row lowercase, the checkout's remote mixed.
    # Without this case a mutant that drops the `.lower()` on the LEFT of the
    # membership test survives, because `issues_off` is lowercased on the right
    # and the fixture above happens to be lowercase on both sides.
    out = RG.build_mapping([_row("acme/plotwidget", has_issues=False)],
                           {"plotwidget": "acme/PlotWidget"})
    assert "plotwidget" not in out, out.get("plotwidget")


def test_a_case_differing_checkout_does_not_silently_displace_an_api_row():
    out = RG.build_mapping([_row("gardenersguild/widget")],
                           {"Widget": "rivalorg/Widget"})
    assert out.get("widget") != "rivalorg/Widget", out.get("widget")
    assert out.get("Widget") != "rivalorg/Widget", out.get("Widget")


def test_read_local_repos_resolves_its_workspace_at_CALL_time(tmp_path, monkeypatch):
    """🔴 THE OTHER HALF OF THE SWEEP, WHICH HAD NO GUARD AT ALL. Two mutants
    survived the whole suite here: restoring the import-bound default, and
    deleting the resolution outright — the latter makes the ONLY production
    caller (`main()`, which passes no argument) die with
    `AttributeError: 'NoneType' object has no attribute 'iterdir'`, green suite
    and all. Every existing test passed `tmp_path` explicitly, so none of them
    ever exercised the default."""
    ws = tmp_path / "elsewhere"
    (ws / "plotwidget" / ".git").mkdir(parents=True)
    monkeypatch.setattr(RG, "WORKSPACE", ws)
    monkeypatch.setattr(RG.subprocess, "run", lambda *a, **k: __import__("types").SimpleNamespace(
        returncode=0, stdout="git@github.com:gardenersguild/plotwidget.git\n", stderr=""))
    assert RG.read_local_repos() == {"plotwidget": "gardenersguild/plotwidget"}


def test_a_case_differing_checkout_of_the_SAME_repo_still_resolves():
    """🔴 PINS THE FOLD ON THE COLLISION COMPARISON — un-folding it survived the
    suite, and it breaks the very example the fold was added for: a repo cloned
    via a lowercase URL is the SAME repo as the API's mixed-case row, so it must
    confirm the owner, not read as a second claimant."""
    out = RG.build_mapping([_row("acme/PlotWidget")], {"plotwidget": "acme/plotwidget"})
    assert out.get("plotwidget") == "acme/plotwidget", out
    assert out.get("PlotWidget") == "acme/PlotWidget", out


def test_a_DROPPED_name_does_not_survive_under_a_different_casing():
    """🔴 A DROP MUST REMOVE EVERY CASING. The API pass writes both `Name` and
    `name`; a drop that popped only the spellings it happened to hold left the
    other one resolving a name the code had just called ambiguous — so
    `plotwidget#12` refused while `PlotWidget#12` opened a page."""
    out = RG.build_mapping([_row("acme/PlotWidget")], {"plotwidget": "rival/plotwidget"})
    assert not [k for k in out if k.lower() == "plotwidget"], out


def test_two_checkouts_colliding_only_by_CASE_are_still_ambiguous():
    """⚠ INVARIANT GUARD, NOT REGRESSION COVERAGE FOR THE CLAUSE IT MENTIONS —
    and this label is the finding, not a formality. It passes with EITHER
    disjunct of that `if` removed, because it pins the disjunction, so a
    maintainer deleting the `local_owners` clause again gets a green suite.

    🔴 NO TEST CAN PIN THAT CLAUSE, because on the SHIPPED code it changes no
    output. Independently checked over 135,035 cases — exhaustive mixed-case
    plus randomized fuzz — with a positive control confirming the harness could
    see a difference: zero. The structural reason is that any spelling with two
    distinct local owners is necessarily visited by both claimants, so the
    second one's `existing != full` drop fires with or without it.

    The measurement that motivated restoring the clause was taken against the
    code BEFORE the case-folded lookup existed, where it genuinely was
    load-bearing. Three harnesses have since agreed on that direction and
    disagreed on the magnitude, and none of them is committed here — so this
    docstring quotes no count, and `regen-known-repos.py` says the same.

    What this test DOES pin is the outcome: two checkouts colliding only by
    case stay ambiguous."""
    out = RG.build_mapping([], {"mirror": "rivalorg/WIDGET", "Widget": "acme/Widget"})
    assert not [k for k in out if k.lower() == "widget"], out


def test_an_ISSUES_DISABLED_checkout_does_not_make_its_NAMESAKE_unresolvable():
    """🔴 `local_owners` is built from the FILTERED checkouts, and that choice
    was unpinned: building it from the raw `local_repos` survived the suite and
    is NOT equivalent — a repo excluded for having issues disabled would still
    count as a second claimant and take the resolvable one down with it.

    The real shape: a fork with issues disabled checked out beside the repo the
    operator actually files issues against."""
    out = RG.build_mapping(
        [_row("acme/plotwidget", has_issues=False)],
        # TWO checkouts sharing the bare name — one filtered out, one not. With
        # only one, the excluded checkout never reaches the clause and the
        # mutant is equivalent: the fixture has to make the FILTERED entry a
        # potential second claimant for the choice of source to matter.
        {"plotwidget-fork": "acme/plotwidget",
         "plotwidget": "upstream/plotwidget"})
    assert out.get("plotwidget") == "upstream/plotwidget", out


def test_a_checkout_with_NO_api_row_is_written_in_BOTH_spellings():
    """🔴 The local pass's dual-spelling write was unguarded — dropping the
    lowercase half survived the suite while changing the majority of mixed-case
    inputs. `mention_scan._resolve_repo` does an EXACT dict lookup, so a
    canonical-case key alone means `plotwidget#12` silently stops resolving for
    a checkout the operator has on disk. (The identical write in the API pass
    was already guarded; this one was not.)"""
    out = RG.build_mapping([], {"PlotWidget": "acme/PlotWidget"})
    assert out.get("PlotWidget") == "acme/PlotWidget", out
    assert out.get("plotwidget") == "acme/PlotWidget", out


# --------------------------------------------------------------------------- #
# 🔴 THE PICKER UNIVERSE — WIDER THAN THE MAPPING, ON PURPOSE
#
# `build_mapping` answers "what does the bare name `foo` mean?" and must drop an
# issues-disabled repo and a bare name two owners share. `build_universe`
# answers "which repos might be worth offering?", its rows are fully-qualified,
# and nothing opens without a selection — so it applies NEITHER filter.
#
# MEASURED on the operator's host 2026-09-07: 388 repos from `gh api
# user/repos`, 339 in the mapping, so 53 were unreachable from the picker.
# --------------------------------------------------------------------------- #
def _api(*rows) -> list[dict]:
    """`{full_name, has_issues}` rows in the shape `gh api user/repos` returns."""
    return [{"full_name": f, "has_issues": h} for f, h in rows]


def test_the_universe_KEEPS_a_repo_whose_issues_are_disabled():
    """🔴 THE 48-REPO HALF, and the assumption that hid it was in a comment.

    The mapping drops these, and the ORIGINAL justification said a bare `repo#N`
    "404s for EVERY N". That is false for PULL REQUESTS: measured 2026-09-07
    against the API with a positive control (2 issues-ENABLED repos first, both
    `PULL`), **6 of 6** issues-disabled repos resolved `/issues/<pr>` to the pull
    request. An earlier probe that saw 3 of 4 return 404 was confounded — an
    unauthenticated request 404s on a PRIVATE repo whatever the redirect does.

    The mapping still drops them, on the narrower true reason (no ISSUES exist,
    so a bare `#N` naming an issue is a wrong answer). The universe must not."""
    api = _api(("acme/widget", True), ("acme/noissues", False))
    assert RG.build_universe(api, {}) == ["acme/noissues", "acme/widget"]
    # THE CONTRAST IS THE POINT — pinned here so the two functions can never
    # quietly converge on one filter policy.
    assert "acme/noissues" not in RG.build_mapping(api, {}).values()


def test_the_universe_KEEPS_BOTH_SIDES_of_an_ambiguous_bare_name():
    """🔴 THE 7-COLLISION HALF. `build_mapping` drops `bitdex` entirely, because
    resolving it would mean guessing between two owners — measured once picking
    a third party's fork over the client's repo.

    A picker row is `owner/repo`, so there is no ambiguity left to arbitrate:
    offering both and letting the operator choose is exactly the question they
    are being asked. Dropping them makes a repo unreachable to protect against
    a guess nobody is making."""
    api = _api(("alice/bitdex", True), ("bob/bitdex", True))
    assert RG.build_universe(api, {}) == ["alice/bitdex", "bob/bitdex"]
    # The mapping's silence on the shared bare name is UNCHANGED.
    assert "bitdex" not in RG.build_mapping(api, {})


def test_the_universe_unions_local_checkouts_the_API_never_returned():
    """A clone of somebody else's repository is on this disk and not in
    `user/repos`. It cannot CONTRADICT an API row — this is a list, not a
    lookup — so it is simply added."""
    api = _api(("acme/widget", True))
    out = RG.build_universe(api, {"mirror": "elsewhere/stranger"})
    assert out == ["acme/widget", "elsewhere/stranger"]


def test_the_universe_dedupes_case_insensitively_keeping_the_API_spelling():
    """GitHub repo names are case-insensitive, so these are ONE repository. The
    API row wins because it is canonical; a remote URL preserves whatever the
    person who cloned happened to type, which is why the mapping has to write
    two spellings of every key in the first place."""
    api = _api(("civitai/ComfyUI", True))
    out = RG.build_universe(api, {"mirror": "civitai/comfyui"})
    assert out == ["civitai/ComfyUI"], out


def test_the_universe_drops_rows_that_are_not_owner_slash_repo():
    """Same predicate as the mapping's, and IMPORTED from `mention_scan` rather
    than re-spelled here — a row that is not exactly `owner/repo` builds a URL
    that 404s while looking authoritative."""
    api = _api(("acme/widget/", True), ("acme//widget", True), ("noslash", True),
               ("", True), ("acme/widget", True))
    assert RG.build_universe(api, {"mirror": "also/bad/three"}) == ["acme/widget"]


def test_the_universe_is_SORTED_case_insensitively():
    """The picker shows this list in order; sorting by raw codepoint puts every
    capitalised name in a block ahead of the lowercase ones, which reads as
    random to someone scanning for a name."""
    api = _api(("acme/zebra", True), ("acme/Apple", True), ("acme/mango", True))
    assert RG.build_universe(api, {}) == ["acme/Apple", "acme/mango", "acme/zebra"]


def test_write_universe_is_0600_because_it_names_private_repositories(tmp_path):
    """Identical posture to `write_mapping`. This file holds MORE private names
    than the mapping does — it is deliberately unfiltered — so a weaker mode
    here would be a wider disclosure than the one that started all of this."""
    out = tmp_path / "sub" / "known_universe.json"
    RG.write_universe(["acme/widget"], out)
    assert json.loads(out.read_text()) == ["acme/widget"]
    assert oct(out.stat().st_mode)[-3:] == "600", oct(out.stat().st_mode)
    assert oct(out.parent.stat().st_mode)[-3:] == "700"
    assert not list(out.parent.glob("*.tmp")), "the temp file must not survive"


# --------------------------------------------------------------------------- #
# 🔴 TWO KINDS OF FAILURE — THE SPLIT THAT MAKES THE TIMER POSSIBLE
#
# `mention-open.py` once argued AGAINST a scheduled run: with a single failure
# code, a host without `gh auth` would take a failing unit and a failure toast
# on every fire, and a permanently-red timer is worse than no timer. That was
# correct. It is answered by exit 4 ("not configured here" — which the unit
# declares a success) versus exit 3 ("configured and broken" — which toasts).
# --------------------------------------------------------------------------- #
def _stub_run(monkeypatch, *, auth_rc=0, auth_raises=None, api_rc=0, api_out=""):
    """Replace `subprocess.run` INSIDE the generator only, keyed on argv."""
    def fake(cmd, *a, **k):
        if cmd[:3] == ["gh", "auth", "status"]:
            if auth_raises is not None:
                raise auth_raises
            return subprocess.CompletedProcess(cmd, auth_rc, "", "")
        if cmd[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(cmd, api_rc, api_out, "boom")
        return subprocess.CompletedProcess(cmd, 1, "", "")
    monkeypatch.setattr(RG.subprocess, "run", fake)


def _enough_repos() -> str:
    return "".join(
        json.dumps({"full_name": f"acme/plot{i}", "has_issues": True}) + "\n"
        for i in range(RG.MIN_API_REPOS + 5))


def test_a_host_with_no_gh_exits_NOT_CONFIGURED_not_FAILED(monkeypatch, tmp_path):
    """🔴 THE WHOLE REASON THE TIMER CAN EXIST. `gh` absent is a legitimate host
    state — the click handler degrades to `owner/repo#N` plus local checkouts —
    so it must not be the code that toasts daily forever."""
    _stub_run(monkeypatch, auth_raises=FileNotFoundError("no gh"))
    rc = RG.main(["--path", str(tmp_path / "m.json"),
                  "--universe-path", str(tmp_path / "u.json")])
    assert rc == RG.EXIT_NOT_CONFIGURED == 4, rc
    assert not (tmp_path / "m.json").exists(), "nothing may be written"
    assert not (tmp_path / "u.json").exists()


def test_gh_present_but_LOGGED_OUT_is_also_NOT_CONFIGURED(monkeypatch, tmp_path):
    """The second spelling of the same host state, and the one a real host
    reaches by having a token expire rather than by lacking the binary."""
    _stub_run(monkeypatch, auth_rc=1)
    assert RG.main(["--path", str(tmp_path / "m.json"),
                    "--universe-path", str(tmp_path / "u.json")]) == 4


def test_an_AUTHENTICATED_host_whose_API_call_fails_is_a_REAL_failure(
        monkeypatch, tmp_path):
    """🔴 THE OTHER ARM, AND WITHOUT IT THE SPLIT IS WORTHLESS. If everything
    returned 4 the unit would be permanently green and a genuinely broken
    refresh would be silent — which is the same failure as a permanently-red
    gate, wearing the other colour."""
    _stub_run(monkeypatch, auth_rc=0, api_rc=1)
    rc = RG.main(["--path", str(tmp_path / "m.json"),
                  "--universe-path", str(tmp_path / "u.json")])
    assert rc == RG.EXIT_FAILED == 3, rc


def test_an_AUTHENTICATED_host_returning_TOO_FEW_repos_is_a_REAL_failure(
        monkeypatch, tmp_path):
    """The floor is a truncation detector: a short list is indistinguishable
    from a fine one once written. Authenticated + short is broken, not
    unconfigured — so it must toast."""
    _stub_run(monkeypatch, auth_rc=0, api_out=json.dumps(
        {"full_name": "acme/widget", "has_issues": True}) + "\n")
    assert RG.main(["--path", str(tmp_path / "m.json"),
                    "--universe-path", str(tmp_path / "u.json")]) == 3


def test_NotConfigured_is_caught_BEFORE_the_generic_RuntimeError(monkeypatch,
                                                                 tmp_path):
    """🔴 AN ORDERING GUARD, AND THE BUG IT PINS IS INVISIBLE BY INSPECTION.
    `NotConfigured` SUBCLASSES `RuntimeError`. Swap the two `except` clauses in
    `main()` and an unconfigured host reports exit 3 — a red unit and a daily
    toast, the exact outcome the split exists to prevent — while every other
    test in this file still passes, because they never exercise the ordering.

    So this asserts the CODE, not just a message: 4, and nothing written."""
    _stub_run(monkeypatch, auth_rc=1)
    assert RG.main(["--path", str(tmp_path / "m.json"),
                    "--universe-path", str(tmp_path / "u.json")]) == 4
    assert RG.EXIT_NOT_CONFIGURED != RG.EXIT_FAILED, (
        "the two codes must stay distinct or the unit cannot tell them apart")


def test_a_HEALTHY_run_writes_BOTH_files(monkeypatch, tmp_path):
    """🔴 THE POSITIVE CONTROL FOR EVERY REFUSAL ABOVE. Five tests assert that
    nothing is written; a generator that never wrote anything would pass all
    five. This is the one that proves the happy path still lands — and that the
    universe is written at all, which a `write_mapping`-only regression would
    otherwise leave to the reader's graceful `[]` fallback and hide completely."""
    _stub_run(monkeypatch, auth_rc=0, api_out=_enough_repos())
    m, u = tmp_path / "m.json", tmp_path / "u.json"
    assert RG.main(["--path", str(m), "--universe-path", str(u)]) == 0
    assert json.loads(m.read_text()), "the mapping must be written"
    universe = json.loads(u.read_text())
    assert isinstance(universe, list) and len(universe) == RG.MIN_API_REPOS + 5
    assert oct(u.stat().st_mode)[-3:] == "600"


def test_print_mode_writes_NOTHING_and_reports_the_universe(monkeypatch,
                                                            tmp_path, capsys):
    """`--print` must stay a read. It also reports how many repos are reachable
    ONLY through the picker — the number this whole change is about, and one
    nobody would check if it were never printed."""
    _stub_run(monkeypatch, auth_rc=0, api_out=_enough_repos())
    m, u = tmp_path / "m.json", tmp_path / "u.json"
    assert RG.main(["--print", "--path", str(m), "--universe-path", str(u)]) == 0
    assert not m.exists() and not u.exists()
    assert "picker universe" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# THE RANGE TABLE — batched GraphQL
#
# 🔴 EVERY TEST HERE STUBS THE RUNNER. `read_api_ranges` takes its subprocess
# runner as an argument precisely so this file never has to reach the network to
# test the parsing, which is where all the hazards are.
# --------------------------------------------------------------------------- #
class _Reply:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _graphql(answers: dict, *, returncode=0, errors=None):
    """A runner that answers `{alias_index: highest_number_or_None}`."""
    def run(argv, **kwargs):
        assert argv[:3] == ["gh", "api", "graphql"], argv
        data = {}
        for i, value in answers.items():
            data[f"r{i}"] = None if value is None else {
                "issues": {"nodes": []},
                "pullRequests": {"nodes": [{"number": value}]},
            }
        doc = {"data": data}
        if errors:
            doc["errors"] = errors
        return _Reply(json.dumps(doc), returncode)
    return run


def test_the_ranges_query_asks_for_BOTH_issues_AND_pull_requests():
    """🔴 ASKING ONLY `issues` WOULD MANUFACTURE FALSE `IMPOSSIBLE` VERDICTS.
    GitHub numbers issues and pull requests out of ONE sequence per repository,
    and 48 of this host's repos have issues DISABLED — every one of them would
    report 0 from the issues list while its PR numbers run into the thousands,
    and the reader ranks 0 dead last."""
    q = RG.ranges_query(["gardenersguild/trowelcast", "hobbyist/PlotWidget"])
    assert "issues(" in q and "pullRequests(" in q
    # One aliased selection per repo, positional — the caller re-pairs by index.
    assert "r0: repository(" in q and "r1: repository(" in q
    assert 'owner: "hobbyist", name: "PlotWidget"' in q, q
    # Newest-CREATED, not a count: `totalCount` is how MANY, which is a
    # different number from the highest.
    assert "orderBy: {field: CREATED_AT, direction: DESC}" in q
    assert "totalCount" not in q


def test_a_NONZERO_exit_still_yields_every_answer_in_the_batch():
    """🔴 THE MEASURED TRAP, AND IT IS THE REASON THIS FUNCTION DOES NOT LOOK AT
    `returncode` AT ALL. MEASURED 2026-09-11: a 21-alias request containing ONE
    deleted repository exited **1**, printed `errors: [{type: NOT_FOUND, path:
    ["r20"]}]`, and carried all **20** other answers in `data`. Branching on the
    exit code would have thrown the whole batch away on one bad row.

    `claude/RULES.md` names this shape: an exit code is a claim about the TOOL,
    never about your data."""
    repos = ["o/a", "o/b", "o/c"]
    runner = _graphql({0: 40, 1: None, 2: 1291}, returncode=1,
                      errors=[{"type": "NOT_FOUND", "path": ["r1"]}])
    got = RG.read_api_ranges(repos, runner=runner)
    assert got == {"o/a": 40, "o/c": 1291}, got


def test_an_UNANSWERABLE_repo_is_ABSENT_rather_than_ZERO():
    """🔴 THE DISTINCTION THE WHOLE ORDERING RESTS ON. `0` is the strongest
    signal in the scheme — the only value that rules a repository out — so
    writing it for "I could not ask" would rank a perfectly good repository last
    on no evidence. Absence is what the reader maps to UNKNOWN."""
    got = RG.read_api_ranges(["o/a", "o/gone"], runner=_graphql({0: 7, 1: None}))
    assert "o/gone" not in got, got
    assert got["o/a"] == 7
    # …and a repo with genuinely no references IS recorded, as 0. Measured on
    # `torvalds/linux`, where both totalCounts are really 0.
    got = RG.read_api_ranges(["o/empty"], runner=_graphql({0: 0}))
    assert got == {"o/empty": 0}, got


def test_the_HIGHEST_of_the_two_lists_wins():
    assert RG._highest_ref({"issues": {"nodes": [{"number": 12}]},
                            "pullRequests": {"nodes": [{"number": 99}]}}) == 99
    assert RG._highest_ref({"issues": {"nodes": [{"number": 99}]},
                            "pullRequests": {"nodes": [{"number": 12}]}}) == 99
    assert RG._highest_ref({"issues": {"nodes": []},
                            "pullRequests": {"nodes": []}}) == 0
    assert RG._highest_ref({}) == 0


def test_a_batch_that_FAILS_OUTRIGHT_costs_only_ITS_OWN_repos():
    """One transient `gh` failure must not abandon the leg — the repos in that
    batch are UNKNOWN and the rest of the run continues. Driven at a batch size
    of 1 so the partition is visible."""
    calls = {"n": 0}

    def flaky(argv, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("gh died")
        return _Reply(json.dumps({"data": {"r0": {
            "issues": {"nodes": []},
            "pullRequests": {"nodes": [{"number": calls["n"] * 10}]}}}}))

    import unittest.mock
    with unittest.mock.patch.object(RG, "RANGES_BATCH", 1):
        got = RG.read_api_ranges(["o/a", "o/b", "o/c"], runner=flaky)
    assert sorted(got) == ["o/a", "o/c"], got


@pytest.mark.parametrize("body,why", [
    ("", "empty stdout"),
    ("not json", "an unparseable body"),
    ('{"errors": [{"message": "x"}]}', "errors with no data at all"),
    ('{"data": null}', "a null data object"),
    ('[1, 2]', "a JSON array where an object belongs"),
])
def test_an_UNREADABLE_graphql_reply_leaves_every_repo_UNKNOWN(body, why):
    got = RG.read_api_ranges(["o/a"], runner=lambda *a, **k: _Reply(body))
    assert got == {}, why


def test_build_ranges_is_keyed_off_the_UNIVERSE_and_LOWERCASED():
    """🔴 KEYED OFF THE UNIVERSE: a range for a repo the picker can never offer
    is dead weight in a 0600 file that names private repositories.

    🔴 LOWERCASED: the reader looks up a universe row by `.lower()`, and the row
    keeps whatever casing the API returned — a table keyed on the canonical
    spelling would miss every row cloned through a lowercase URL.

    🔴 THE API KEYS HERE ARE MIXED-CASE ON PURPOSE. The first version of this
    test fed already-lowercase keys, which makes the fold a no-op — so a mutant
    that dropped `.lower()` entirely produced an IDENTICAL table and SURVIVED a
    green run (measured in this change's own sweep, M18). The keys come from
    `build_universe`, which preserves the API's canonical spelling, so mixed
    case is the REALISTIC input and the lowercase one was the artificial one."""
    universe = ["Acme/Widget", "acme/other"]
    api = {"Acme/Widget": 40, "acme/other": 0, "Nobody/Asked": 999}
    assert RG.build_ranges(universe, api) == {"acme/widget": 40,
                                              "acme/other": 0}
    # …and a universe row whose casing differs from the API's still resolves,
    # in BOTH directions — the fold has two sides and only one was exercised.
    assert RG.build_ranges(["acme/widget"], {"Acme/Widget": 7}) == {
        "acme/widget": 7}
    assert RG.build_ranges(["Acme/Widget"], {"acme/widget": 7}) == {
        "acme/widget": 7}


def test_build_ranges_OMITS_a_repo_the_api_could_not_answer_for():
    """The write side of the UNKNOWN contract: no entry, never a 0."""
    assert RG.build_ranges(["o/a", "o/unanswered"], {"o/a": 5}) == {"o/a": 5}


def test_write_ranges_is_0600_because_its_KEYS_name_private_repositories(tmp_path):
    """The mirror of the mapping's disclosure: here the private names are the
    KEYS. Same mode, same reason."""
    out = tmp_path / "sub" / "known_ranges.json"
    RG.write_ranges({"acme/widget": 1291}, out)
    assert json.loads(out.read_text()) == {"acme/widget": 1291}
    assert oct(os.stat(out).st_mode)[-3:] == "600"
    assert oct(os.stat(out.parent).st_mode)[-3:] == "700"


def test_the_ranges_output_path_is_outside_every_checkout():
    p = RG.DEFAULT_RANGES_PATH
    assert p.is_absolute()
    assert p.name == "known_ranges.json"
    assert "workspace" not in p.parts, p
    assert p.parent.name == "mention-open"


def test_a_FAILED_ranges_leg_does_NOT_fail_the_run_or_clobber_the_table(
        monkeypatch, tmp_path):
    """🔴 NOT EXIT 3, AND NOT AN EMPTY FILE EITHER. The mapping and the universe
    are what make a click RESOLVE; the range table only ORDERS rows offered
    either way, and the handler degrades to the old order without it. Failing
    the unit daily for an ordering accelerator — and firing the DND-defeating
    toast — is the objection that kept this timer unwritten for months.

    The existing table is LEFT ALONE rather than replaced with an empty one, so
    it ages, and that staleness is this leg's deadman."""
    _stub_run(monkeypatch, api_out=_enough_repos())
    monkeypatch.setattr(RG, "read_api_ranges", lambda *a, **k: {})
    monkeypatch.setattr(RG, "read_local_repos", lambda *a, **k: {})
    ranges = tmp_path / "known_ranges.json"
    ranges.write_text(json.dumps({"o/previous": 12}))
    rc = RG.main(["--path", str(tmp_path / "m.json"),
                  "--universe-path", str(tmp_path / "u.json"),
                  "--ranges-path", str(ranges)])
    assert rc == 0, "a failed ranges leg must not fail the run"
    assert json.loads(ranges.read_text()) == {"o/previous": 12}, (
        "the previous table was clobbered by an empty one — it must be left to "
        "AGE instead, which is what the handler's staleness note reads")


def test_a_HEALTHY_run_writes_the_RANGE_TABLE_too(monkeypatch, tmp_path):
    _stub_run(monkeypatch, api_out=_enough_repos())
    monkeypatch.setattr(RG, "read_local_repos", lambda *a, **k: {})
    monkeypatch.setattr(
        RG, "read_api_ranges",
        lambda names, **k: {n: 100 + i for i, n in enumerate(names)})
    ranges = tmp_path / "known_ranges.json"
    rc = RG.main(["--path", str(tmp_path / "m.json"),
                  "--universe-path", str(tmp_path / "u.json"),
                  "--ranges-path", str(ranges)])
    assert rc == 0
    table = json.loads(ranges.read_text())
    assert table, "no range table was written by a healthy run"
    assert all(isinstance(v, int) for v in table.values()), table
    assert all(k == k.lower() for k in table), table


def test_no_ranges_SKIPS_the_leg_entirely(monkeypatch, tmp_path):
    """The opt-out must not merely write an empty table — it must not ask."""
    _stub_run(monkeypatch, api_out=_enough_repos())
    monkeypatch.setattr(RG, "read_local_repos", lambda *a, **k: {})
    asked = []
    monkeypatch.setattr(RG, "read_api_ranges",
                        lambda names, **k: asked.append(names) or {})
    ranges = tmp_path / "known_ranges.json"
    rc = RG.main(["--no-ranges", "--path", str(tmp_path / "m.json"),
                  "--universe-path", str(tmp_path / "u.json"),
                  "--ranges-path", str(ranges)])
    assert rc == 0
    assert asked == [], asked
    assert not ranges.exists()


# --------------------------------------------------------------------------- #
# 🔴 THE FILE-WIDE SPAWN PIN — required by this script's entry in
# `test_no_real_launchers.py::ACKNOWLEDGED_UNSTUBBED` under `home-manager`.
#
# This file names `home-manager` in ONE comment (why the picker universe is a
# second FILE rather than a new shape inside `known_repos.json`).
# `launcher_scan.hazard_hits` is a TEXTUAL scan, so that mention registers the
# script as "reaching home-manager" and had to be acknowledged.
#
# 🔴 AN ACKNOWLEDGEMENT BLINDS THE GUARD IT IS FILED UNDER — MEASURED on this
# very table, where `tmux-reply-agent`'s entry rested on a grep and an injected
# real call site left the whole suite green. So the entry gets a pin, and the
# pin is this.
# --------------------------------------------------------------------------- #
_SPAWN_FUNCS = {"run", "Popen", "call", "check_output", "check_call", "system",
                "execv", "execvp", "execve", "spawnv", "spawnvp"}

# gh  — `gh auth status` (readiness) and `gh api user/repos` (the rows)
# git — `git remote get-url origin`, per local checkout
EXPECTED_ARGV0 = {"gh", "git"}


def _spawn_argv0_literals(path: Path) -> set[str]:
    """Every literal argv[0] in a spawn-shaped call, from the SYNTAX TREE.

    `<computed>` rather than a skip for a non-literal argv[0]: a command built
    from a variable is how a literal-keyed ledger gets walked past, so it must
    fail loudly instead of leaving the set."""
    tree = ast.parse(path.read_text())
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if name not in _SPAWN_FUNCS:
            continue
        first = node.args[0]
        if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
            head = first.elts[0]
            found.add(head.value if isinstance(head, ast.Constant)
                      else "<computed>")
        else:
            found.add("<not-a-list>")
    return found


def test_regen_SPAWNS_these_argv0_AND_NOTHING_ELSE():
    """GROWS-OR-SHRINKS. A new binary is the hazard the acknowledgement would
    otherwise hide; a vanished one means the justification has stopped
    describing the file."""
    assert _spawn_argv0_literals(GENERATOR) == EXPECTED_ARGV0


def test_home_manager_is_MENTIONED_but_never_SPAWNED():
    """Both halves of the acknowledgement's claim, asserted rather than left to
    a reader of prose: the mention must still EXIST (or the table entry has
    outlived the sentence it describes), and it must remain a mention."""
    text = GENERATOR.read_text()
    assert re.search(r"(?<![\w-])home-manager(?![\w-])", text), (
        "the ACKNOWLEDGED_UNSTUBBED entry for this file exists BECAUSE it names "
        "home-manager; if that is gone, remove the acknowledgement too")
    assert "home-manager" not in _spawn_argv0_literals(GENERATOR), (
        "regen-known-repos.py now SPAWNS home-manager — the acknowledgement "
        "covering it is an unreachability claim and is now FALSE")
