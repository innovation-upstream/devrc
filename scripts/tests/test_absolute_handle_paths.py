"""Handle gate: a doc may not spell out a checkout path that a handle already names.

WHY THIS EXISTS
---------------
`claude/skills/<name>/SKILL.md` is loaded VERBATIM into an agent's context as
authoritative operating instructions, and `nix/agent-handles.nix` already exports
`$DEVRC`, `$HOMELAB`, `$DATAPACKET`, `$CIVITAI`, `$CIVITAI_CLI` and the `$KC_*`
kubeconfigs into EVERY agent shell -- Claude Code's `zsh -c` (via
`nix/programs/zsh/default.nix`) and opencode's bash tool (via the generated
`~/.config/opencode/plugin/env.js`), both derived from that ONE file and both
existence-guarded.

Writing `python3 /home/zach/workspace/devrc/scripts/memory-audit.py` into a skill
therefore duplicates a value the runtime already holds, at a site nothing checks.
The duplicate is wrong the moment the user, the home directory or the checkout
location differs -- and it is wrong SILENTLY: the command does not warn, it just
names a file that is not there, or (worse, for a kubeconfig) a cluster that is
not the one meant. Measured at the commit that introduced this module: 21
occurrences across 9 files, all of them inside runnable commands or file
pointers.

🔴 THIS IS A SPELLING GATE, NOT A RESOLUTION GATE -- and that is deliberate
------------------------------------------------------------------------------
`test_doc_path_rot.py` -- the sibling gate over the SAME corpus -- explicitly
declines to look at absolute paths:

    no `$HOME`-EXPANDED spelling is mapped. A row keyed on `Path.home()` would
    make whether a token is even COUNTED depend on which user runs the gate [...]
    Absolute paths are never claims this repo can settle

That reasoning is correct and is exactly why these 21 sites survived a gate
running over the very same files. So this module never resolves, stats or
expands anything: it matches a TEXTUAL PATTERN, which is identical on every host,
in every clone, for every user. `/home/zach/workspace/devrc/x`,
`/home/alice/workspace/devrc/x` and `/root/workspace/devrc/x` are all flagged, and
the verdict cannot depend on the machine because nothing about the machine is
read.

THE HANDLE TABLE IS PARSED FROM `nix/agent-handles.nix`, NEVER RESTATED
-----------------------------------------------------------------------
`agent-handles.nix` is the single source of truth and derives every value from
`home`, so the only host-independent part of a handle is its path SUFFIX
(`workspace/devrc`). This module parses those suffixes out of the nix file and
turns each into `<any absolute prefix>/<suffix>`. Two consequences worth stating:

  * adding a handle there arms this gate for it with no edit here;
  * the remedy printed on failure is the handle's OWN name, so the failure line
    is the fix (`-> use `$DEVRC/scripts/memory-audit.py``).

⚠ A SECOND COPY OF THIS TABLE EXISTS and is deliberately NOT consulted:
`scripts/claude-hooks/shell-env-nudge.py` keeps its own `REPO_VARS`/`KC_VARS`
dicts, whose header says they must match. Reading the nix file is what makes this
gate DRY against the SOURCE rather than against another copy; unifying the hook
with it is separate work.

LONGEST MATCH WINS
------------------
`/home/zach/workspace/homelab-talos/workbench-kubeconfig` is matched by BOTH
`$HOMELAB` and `$KC_WORKBENCH`. The more specific handle is the better remedy --
`$KC_WORKBENCH` is guarded on the FILE existing, `$HOMELAB` only on the directory
-- so matches are resolved longest-suffix-first. Pinned by
`test_the_longest_handle_wins`.

BOUNDARY, NOT SUBSTRING
-----------------------
A match must end at a path boundary, or `workspace/civit/civitai` would claim
`workspace/civit/civitai-cli` (a DIFFERENT repo with its OWN handle) and
`workspace/devrc` would claim a sibling `workspace/devrc-scratch`. Pinned by
`test_a_sibling_directory_is_not_a_match`.

TWO TIERS -- THIS MODULE MUST RUN WHERE IT ACTUALLY GATES
----------------------------------------------------------
`scripts/tests` is in `scripts/run-tests.sh`'s `HERMETIC_TARGETS`, and
`flake.nix` `checks.pytests` runs that set over a COPY OF THE FLAKE SOURCE --
tracked files only, and NO `.git`. An unguarded `git ls-files` exits 128 there,
so a gate that reads the index without a fallback is dev-host-only in the tier
that actually blocks a merge. The corpus builders are IMPORTED from
`test_doc_path_rot`, which already solves this by falling back to a filesystem
walk -- one definition, so the two gates cannot disagree about what they cover.
`test_the_corpus_is_the_doc_rot_gates_corpus` pins that seam.

A RUN THAT CHECKS ZERO FILES IS AN ERROR
-----------------------------------------
A reassuring zero is indistinguishable from a gate wired to nothing, so the
corpus size is floored (`test_corpus_is_not_empty`) and the matcher is driven
against a planted violation on every run (`test_planted_absolute_path_is_caught`).
Both halves are needed: a floor proves the corpus is there, the plant proves the
matcher can still see.

THE ESCAPE HATCH
----------------
`IGNORE_FILE`, keyed on `(doc, literal)` -- NOT on the literal alone, so an
exemption cannot silently license the same spelling in a doc that copies it. 🔴
EVERY entry must carry a `#` reason on its own line, enforced by
`test_every_ignore_entry_carries_a_reason`: an ignore list of unexplained lines
stops being an exception and becomes the coverage mechanism.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# ONE corpus definition, shared with the sibling gate over the same files. See
# the TWO TIERS note above: these builders already carry the no-index fallback.
from test_doc_path_rot import (  # noqa: E402
    CORPUS_DIRS,
    REPO_ROOT,
    _corpus_docs,
    _corpus_from_filesystem,
    _corpus_from_index,
    _has_index,
)

HERE = Path(__file__).resolve().parent
HANDLES_NIX = REPO_ROOT / "nix/agent-handles.nix"
# 🔴 NOT a `.txt`: `test_no_captured_markup.py` gates every tracked `.txt` for
# pasted prose, and a header explaining an exception is exactly what trips it.
# The suffix says what the file is -- a list of entries.
IGNORE_FILE = HERE / "absolute-handle-path-ignore.list"

# `NAME = "${home}/workspace/devrc";` -- the only shape `agent-handles.nix` uses,
# and the only one that is host-independent. A future entry built any other way
# would go unparsed, which `test_the_handle_table_is_parsed_from_the_nix_source`
# catches via the floor and the named-handle assertions.
_NIX_ENTRY = re.compile(r'^\s*([A-Z][A-Z0-9_]*)\s*=\s*"\$\{home\}/([^"]+)"\s*;', re.M)

# The `${home}` half, as a pattern: ANY absolute prefix of one or more segments.
# This is what makes the gate host-independent -- `/home/zach`, `/home/alice` and
# `/root` are all matched, so the verdict never depends on who runs it.
_ABS_PREFIX = r"/(?:[A-Za-z0-9._+-]+/)*"
# A match may not START mid-path and may not END mid-segment (see BOUNDARY, NOT
# SUBSTRING above).
#
# 🔴 `/` IS IN THE LEFT-HAND EXCLUSION SET, and it is what keeps URLs out.
# `https://example.dev/home/zach/workspace/devrc/scripts/x.py` offers the engine
# a start at `/example.dev/…` whose preceding character is the second `/` of the
# scheme; every other candidate start inside it is preceded by a word character.
# Without this the gate reports a finding on any documentation LINK whose path
# happens to contain a handle suffix, and prints a remedy that would corrupt the
# URL. Pinned in `test_a_correct_spelling_stays_green`.
_LEFT_BOUND = r"(?<![A-Za-z0-9._+~/-])"
_RIGHT_BOUND = r"(?![A-Za-z0-9._+-])"
# The TAIL: the rest of the path after the handle's own suffix. Captured so the
# failure line can print the WHOLE literal and the remedy can keep it verbatim --
# `$DEVRC/scripts/memory-audit.py`, not a bare `$DEVRC` the reader must re-derive
# the rest of. `@` and `%` are in the class because real paths in this corpus
# carry them; a trailing `/` is allowed, punctuation that ends a sentence is not.
_TAIL = r"(?:/[A-Za-z0-9._+@%-]+)*/?"


def _handle_table() -> list[tuple[str, str]]:
    """`[(HANDLE_NAME, home-relative-suffix), ...]`, LONGEST SUFFIX FIRST.

    Parsed from `nix/agent-handles.nix` rather than restated, so the gate is DRY
    against the single source of truth and a new handle arms it automatically.
    The ordering is the longest-match rule: see the module docstring.
    """
    text = HANDLES_NIX.read_text(encoding="utf-8")
    entries = [(name, rel.rstrip("/")) for name, rel in _NIX_ENTRY.findall(text)]
    return sorted(entries, key=lambda e: (-len(e[1]), e[0]))


def _patterns() -> list[tuple[str, re.Pattern[str]]]:
    return [
        (name, re.compile(_LEFT_BOUND + _ABS_PREFIX + re.escape(rel) + _TAIL + _RIGHT_BOUND))
        for name, rel in _handle_table()
    ]


def _violations(docs: dict[str, str]) -> list[tuple[str, int, str, str]]:
    """`[(doc, lineno, matched-literal, HANDLE_NAME), ...]` for `{path: text}`.

    Longest-suffix-first with a per-offset claim, so one literal yields exactly
    one finding and it names the MOST SPECIFIC handle. Grading by equality in the
    probes below then catches a mutation that ADDS a finding as well as one that
    drops it.
    """
    pats = _patterns()
    out: list[tuple[str, int, str, str]] = []
    for doc in sorted(docs):
        for lineno, line in enumerate(docs[doc].splitlines(), start=1):
            claimed: dict[int, tuple[str, str]] = {}
            for name, pat in pats:
                for m in pat.finditer(line):
                    # First writer wins: `pats` is longest-suffix-first, so the
                    # most specific handle claims the offset.
                    claimed.setdefault(m.start(), (m.group(0), name))
            for start in sorted(claimed):
                literal, name = claimed[start]
                out.append((doc, lineno, literal, name))
    return out


def _remedy(literal: str, name: str) -> str:
    """`$HANDLE/<tail>` -- the literal with its handle-named prefix replaced.

    🔴 `find`, not `rfind`. The regex's `${home}` half is GREEDY, so on a path
    that repeats the suffix (`…/workspace/devrc/x/workspace/devrc/y.py`) the
    engine's own split lands on the LAST occurrence -- and a remedy built from
    that silently deletes the middle of the path while still reading as a correct
    instruction. The checkout root is the FIRST occurrence. Pinned by
    `test_the_remedy_names_the_handle_and_keeps_the_tail`.
    """
    rel = dict((n, r) for n, r in _handle_table())[name]
    idx = literal.find("/" + rel)
    tail = literal[idx + len(rel) + 1:]
    return f"${name}{tail}"


# --- the ignore list ---------------------------------------------------------


def _read_ignore() -> tuple[set[tuple[str, str]], list[str]]:
    """`({(doc, literal), ...}, [raw non-comment lines])`.

    LOUD on a missing file, never silent: without it every exempt site is
    reclassified as a violation, which is a false red, and a false red is how a
    gate gets bypassed.
    """
    if not IGNORE_FILE.is_file():
        raise AssertionError(
            f"{IGNORE_FILE} is missing. This gate's verdict is meaningless "
            "without it; restore it from git rather than letting the run "
            "continue."
        )
    pairs: set[tuple[str, str]] = set()
    raws: list[str] = []
    for line in IGNORE_FILE.read_text(encoding="utf-8").splitlines():
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        raws.append(line)
        doc, sep, literal = body.partition("\t")
        assert sep and literal.strip(), (
            f"malformed ignore line (want '<doc>\\t<literal>  # why'): {line!r}"
        )
        pairs.add((doc.strip(), literal.strip()))
    return pairs, raws


def _unexplained(raws: list[str]) -> list[str]:
    """Ignore-file lines carrying no `#` reason.

    Extracted so it can be driven against a SYNTHETIC list. The real file's one
    entry is explained, so a `return []` mutant here is invisible when graded
    only against the tree -- it SURVIVED a full battery before this probe
    existed. The guard is worth nothing unless it has been watched to fire.
    """
    return [r for r in raws if "#" not in r or not r.split("#", 1)[1].strip()]


def _corpus() -> list[str]:
    return _corpus_docs(REPO_ROOT)


def _corpus_text() -> dict[str, str]:
    return {d: (REPO_ROOT / d).read_text(encoding="utf-8") for d in _corpus()}


def _filter(violations, ignored) -> list[tuple[str, int, str, str]]:
    """Violations the ignore list does not license.

    🔴 ONE implementation, called by THE GATE and by
    `test_an_ignore_entry_licenses_only_the_doc_it_names` alike. Open-coding the
    filter in the probe would leave the gate's own copy ungraded: the corpus is
    clean, so `return []` here reports a perfect PASS and every planted-violation
    probe stays green, because those grade `_violations` and never reach this.
    Routing both through one function is what makes that mutant die.

    🔴 The key is `(doc, literal)`, never the literal alone -- see the probe.
    """
    return [v for v in violations if (v[0], v[2]) not in ignored]


def _new_violations() -> list[tuple[str, int, str, str]]:
    ignored, _ = _read_ignore()
    return _filter(_violations(_corpus_text()), ignored)


# --- positive controls -------------------------------------------------------

# HALF the measured corpus, by the same rule `test_doc_path_rot.py` states for
# its own floors: these are COLLAPSE detectors, not a census. This repo's
# headline workflow is PRUNING documentation, so a floor sitting just under the
# measurement reds on ordinary work and sends the reader hunting a bug that is
# not there.
CORPUS_DOC_FLOOR = 40  # measured 88


def test_corpus_is_not_empty():
    """🔴 A RUN THAT CHECKS ZERO FILES IS AN ERROR, NOT A PASS.

    Every other assertion here is "no violations found". That sentence is true of
    a gate wired to nothing, and true in exactly the same words, so the corpus
    size has to be asserted or the green means nothing.
    """
    docs = _corpus()
    assert len(docs) >= CORPUS_DOC_FLOOR, (
        f"only {len(docs)} corpus doc(s) found under {CORPUS_DIRS} -- 88 were "
        "measured. The corpus builder has broken, and every PASS from this "
        "module is a claim about the builder rather than about the docs."
    )


def test_the_corpus_is_the_doc_rot_gates_corpus():
    """SEAM GUARD. Two gates over 'the same corpus' is a claim, and a claim that
    nobody owns drifts: if one of them narrows, the other's green stops covering
    what its docstring says it covers. Asserted in BOTH tiers -- the temp-repo
    arms live in `test_doc_path_rot`; here it is enough that both modules compute
    the corpus from the SAME functions and that the no-index fallback agrees."""
    assert _corpus() == _corpus_docs(REPO_ROOT), (
        "this gate's corpus is no longer the doc-rot gate's corpus. A narrowed "
        "corpus is the silent failure: every doc it dropped is UNCHECKED and "
        "UNCOUNTED, and the gate still reports PASS."
    )
    if _has_index(REPO_ROOT):
        assert set(_corpus_from_index(REPO_ROOT)) == set(
            _corpus_from_filesystem(REPO_ROOT)
        ), (
            "the index and the filesystem disagree about the corpus, so the dev "
            "host and the nix-sandbox tier check different sets of docs."
        )


# Every handle name this module is known to arm. Named individually rather than
# counted, because a COUNT survives a rename: the parser could pick up five
# entries with one of them silently spelled differently and the floor would not
# move. The `$KC_*` rows matter most -- a wrong kubeconfig is the failure that
# looks like success.
KNOWN_HANDLES = frozenset(
    {
        "DEVRC", "HOMELAB", "DATAPACKET", "CIVITAI", "CIVITAI_CLI",
        "KC_HOMELAB", "KC_WORKBENCH", "KC_PROD", "KC_DPPROD", "KC_NEBULA",
    }
)


def test_the_handle_table_is_parsed_from_the_nix_source():
    """POSITIVE CONTROL on the parser, and a LEDGER over `agent-handles.nix`.

    The table is the whole gate: an empty or half-read table makes every
    assertion below vacuously true while the module still reports PASS. Graded as
    a ledger that fails when the set SHRINKS (a handle stopped being armed) --
    growth is allowed, because adding a handle upstream is meant to arm this gate
    with no edit here.
    """
    table = _handle_table()
    names = {n for n, _ in table}
    assert not (KNOWN_HANDLES - names), (
        f"handles no longer parsed out of nix/agent-handles.nix: "
        f"{sorted(KNOWN_HANDLES - names)}\nEither they were removed upstream (in "
        "which case delete them from KNOWN_HANDLES and say why) or `_NIX_ENTRY` "
        "no longer matches the shape they are written in -- and in that second "
        "case this gate is silently not checking them."
    )
    # The suffixes are what the patterns are built from; an empty one would match
    # every absolute path in the corpus.
    for name, rel in table:
        assert rel and not rel.startswith("/") and "${" not in rel, (
            f"{name} parsed to a suffix this gate cannot use: {rel!r}"
        )


def test_the_table_is_longest_suffix_first():
    """The ordering IS the longest-match rule -- it is not cosmetic. Sorted
    wrongly, `$HOMELAB` claims every `$KC_*` path under it and the printed remedy
    silently degrades to the weaker handle."""
    lens = [len(rel) for _, rel in _handle_table()]
    assert lens == sorted(lens, reverse=True)


# --- THE GATE ----------------------------------------------------------------


def test_no_absolute_checkout_paths_in_agent_docs():
    """THE GATE."""
    new = _new_violations()
    assert not new, (
        f"{len(new)} doc site(s) spell out a checkout path that a handle already "
        "names:\n"
        + "\n".join(
            f"  {d}:{n}  {lit}  ->  use `{_remedy(lit, h)}`"
            for d, n, lit, h in new
        )
        + "\n\nThese files are loaded VERBATIM as authoritative agent "
        "instructions, and `nix/agent-handles.nix` already exports the handle "
        "into every agent shell (zsh + opencode), existence-guarded. A spelled-"
        "out path is a second copy of that value which nothing reconciles.\n"
        "  * in a shell command   -> use the handle: `$DEVRC/scripts/x.py`\n"
        "  * in PYTHON            -> `os.environ[\"DEVRC\"]` ($VAR does not "
        "expand there)\n"
        "  * a doc POINTER to another devrc skill -> the deployed spelling, "
        "`~/.claude/skills/<name>/reference/<topic>.md`\n"
        "  * a path being CREATED, or one with no handle -> derive it from a "
        "handle (`$(dirname $HOMELAB)/homelab-trunk`)\n"
        f"Genuinely correct as a literal (a sample API payload, a per-host "
        f"probe) -> scripts/tests/{IGNORE_FILE.name}, WITH A REASON."
    )


def test_every_ignore_entry_carries_a_reason():
    """🔴 An ignore list of unexplained lines stops being an exception list and
    becomes the coverage mechanism -- silently, because it reads as maintained.

    So the reason is STRUCTURAL, not a convention: an entry without a `#` comment
    on its own line fails here. Reviewing a one-line justification is the entire
    cost of the hatch, and it is what keeps the list small."""
    _, raws = _read_ignore()
    unexplained = _unexplained(raws)
    assert not unexplained, (
        "ignore entries with no written reason:\n"
        + "\n".join(f"  {r}" for r in unexplained)
        + f"\n\nAppend `  # <why this literal is correct>` to each. An entry "
        "nobody can justify in one line belongs in the docs as a FIX, not here."
    )


def test_the_reason_check_can_actually_fire():
    """POSITIVE CONTROL on the reason check.

    🔴 The test above reports a reassuring zero, and the real file's single entry
    IS explained -- so `_unexplained` never executes its predicate on a bad line
    and a `return []` mutant is invisible. Measured: it SURVIVED the whole
    battery until this probe existed. Driven here against synthetic lines whose
    answer must be non-zero, with the explained forms as the other arm so the
    check is not simply refusing everything.

    Fixture lines are pairwise distinct and name paths that appear nowhere in the
    corpus, so none of them can be satisfied by a constant this module already
    holds.
    """
    bad = [
        "claude/skills/aa/SKILL.md\t/home/ada/workspace/devrc/x.py",
        "claude/skills/bb/SKILL.md\t/srv/eve/workspace/devrc/y.py   #",
        "claude/skills/cc/SKILL.md\t/home/ida/.kube/homelab-nebula.yaml #    ",
    ]
    good = [
        "claude/skills/dd/SKILL.md\t/home/kay/workspace/devrc/z.py  # recorded wire payload",
        "claude/skills/ee/SKILL.md\t/opt/lee/workspace/civit/civitai # per-host probe, handle absent",
    ]
    assert _unexplained(bad) == bad, (
        "the reason check did not fire on lines that carry no reason -- every "
        "zero it reports on the real file means nothing."
    )
    assert _unexplained(good) == []
    assert _unexplained(bad + good) == bad


def test_no_stale_ignore_entries():
    """An entry that no longer names a live violation must be DELETED.

    Without this the list is a ratchet with no pawl: the doc gets fixed, the line
    stays, and the stale entry silently re-licenses the same spelling when
    someone reintroduces it."""
    live = {(d, lit) for d, _, lit, _ in _violations(_corpus_text())}
    ignored, _ = _read_ignore()
    stale = sorted(ignored - live)
    assert not stale, (
        f"{len(stale)} ignore entry/entries no longer name a live site:\n"
        + "\n".join(f"  {d}\t{lit}" for d, lit in stale)
        + f"\n\nThe doc was fixed or moved. Delete these lines from "
        f"scripts/tests/{IGNORE_FILE.name} -- it may only shrink."
    )


# =============================================================================
# PROBES -- the gate's own regression coverage. Every case is planted into
# SYNTHETIC text and graded by the REAL `_violations`, so a mutation anywhere in
# the pipeline (the nix parser, the ordering, the boundary lookarounds, the
# per-offset claim, the remedy builder) moves a verdict here.
#
# 🔴 FIXTURE VALUES ARE PAIRWISE DISTINCT AND DISTINCT FROM EVERY CONSTANT THE
# ASSERTIONS NAME: the planted users are `alice`, `bob`, `root` and `zach`, and
# the tails differ per case. A fixture that could only ever produce the table's
# own value cannot see a mutant that hardcodes that value.
# =============================================================================

PROBE_DOC = "claude/skills/prune-memory/SKILL.md"


def test_probe_doc_is_a_real_corpus_doc():
    """The probes feed synthetic text at PROBE_DOC, so a PROBE_DOC that stopped
    existing would keep them all green while describing nothing in the corpus."""
    assert PROBE_DOC in _corpus(), f"{PROBE_DOC} is not a tracked corpus doc"


def _found(text: str, doc: str = PROBE_DOC):
    return [(lit, h) for _, _, lit, h in _violations({doc: text})]


@pytest.mark.parametrize(
    "text,literal,handle",
    [
        # The exact shape that motivated this module.
        ("Run `python3 /home/zach/workspace/devrc/scripts/memory-audit.py` first.",
         "/home/zach/workspace/devrc/scripts/memory-audit.py", "DEVRC"),
        # 🔴 ANOTHER USER. The whole point of the `${home}` pattern: the gate may
        # not be a fact about whose laptop it runs on.
        ("`cd /home/alice/workspace/homelab-talos && git fetch`",
         "/home/alice/workspace/homelab-talos", "HOMELAB"),
        # 🔴 AND A NON-`/home` ROOT -- a container or a root shell.
        ("`/root/workspace/civit/datapacket-talos/prod-kubeconfig`",
         "/root/workspace/civit/datapacket-talos/prod-kubeconfig", "KC_DPPROD"),
        # A deeper home, to prove the prefix is multi-segment.
        ("`/var/lib/agents/bob/workspace/civit/civitai/src/app.ts`",
         "/var/lib/agents/bob/workspace/civit/civitai/src/app.ts", "CIVITAI"),
        # The bare directory, with nothing after it.
        ("`DEVRC=/home/zach/workspace/devrc`", "/home/zach/workspace/devrc", "DEVRC"),
        # Inside PYTHON, where `$VAR` would not expand -- the site this gate
        # exists to route to `os.environ[...]`.
        ('sys.path.insert(0, "/home/zach/workspace/devrc/scripts/task-spec-drafter")',
         "/home/zach/workspace/devrc/scripts/task-spec-drafter", "DEVRC"),
        # A dot-directory tail: `.kube/homelab-nebula.yaml` is a handle too.
        ("`KUBECONFIG=/home/bob/.kube/homelab-nebula.yaml kubectl get nodes`",
         "/home/bob/.kube/homelab-nebula.yaml", "KC_NEBULA"),
        # Plain prose, no backticks: still a spelled-out path in a file an agent
        # reads as instructions.
        ("State lives in /home/zach/workspace/homelab-talos/containers/x/HANDOFF.md today.",
         "/home/zach/workspace/homelab-talos/containers/x/HANDOFF.md", "HOMELAB"),
    ],
    ids=["devrc-script", "other-user", "root-home", "deep-home", "bare-dir",
         "python-literal", "dotdir-kubeconfig", "bare-prose"],
)
def test_planted_absolute_path_is_caught(text, literal, handle):
    """NEGATIVE CONTROL: the gate must go red, name the literal, AND name the
    handle. A gate that stays green on a planted violation is testing nothing."""
    assert _found(text) == [(literal, handle)], (
        f"planting {text!r} did not produce exactly [({literal!r}, {handle!r})]."
    )


def test_the_longest_handle_wins():
    """🔴 `$KC_WORKBENCH` sits UNDER `$HOMELAB`, and the more specific handle is
    the better remedy -- it is guarded on the FILE existing, not merely the
    directory. Graded on BOTH halves (the handle named AND the remedy string), so
    a mutant that reverses the ordering cannot pass by still reporting *a*
    finding."""
    text = "`KUBECONFIG=/home/zach/workspace/homelab-talos/workbench-kubeconfig kubectl get pods`"
    found = _found(text)
    assert found == [
        ("/home/zach/workspace/homelab-talos/workbench-kubeconfig", "KC_WORKBENCH")
    ], f"expected the most specific handle, got {found}"
    assert _remedy(*found[0]) == "$KC_WORKBENCH"


def test_a_sibling_directory_is_not_a_match():
    """BOUNDARY, NOT SUBSTRING. `workspace/civit/civitai` is a character prefix of
    `workspace/civit/civitai-cli`, which is a DIFFERENT repo with its OWN handle;
    without the right-hand boundary the gate would report `$CIVITAI` for it and
    print a remedy that silently names the wrong checkout."""
    assert _found("`/home/zach/workspace/civit/civitai-cli/main.go`") == [
        ("/home/zach/workspace/civit/civitai-cli/main.go", "CIVITAI_CLI")
    ]
    # ...and a genuine sibling with no handle at all is not matched by the
    # shorter one it merely resembles.
    assert _found("`/home/zach/workspace/devrc-scratch/notes.md`") == []
    assert _found("`/home/zach/workspace/homelab-trunk/containers/x`") == []


@pytest.mark.parametrize(
    "text",
    [
        # THE REMEDIES. Each must stay green or the gate is unsatisfiable, which
        # is the state `claude/RULES.md` calls worse than no gate at all.
        "`python3 $DEVRC/scripts/memory-audit.py`",
        "`KUBECONFIG=$KC_WORKBENCH kubectl get pods -A`",
        '`sys.path.insert(0, os.environ["DEVRC"] + "/scripts/x")`',
        "`~/.claude/skills/clawgate/reference/internals.md`",
        "`~/workspace/homelab-talos/containers/clawgate/HANDOFF.md`",
        "`WT=$(dirname $HOMELAB)/homelab-trunk`",
        # A RELATIVE path that happens to end in a handle suffix: not absolute,
        # so not a spelled-out checkout path.
        "`../workspace/devrc/scripts/x.py`",
        "`./workspace/devrc/scripts/x.py`",
        # A URL whose path resembles a handle suffix. The left boundary is what
        # keeps this out; without it every doc link would be a finding.
        "https://example.dev/home/zach/workspace/devrc/scripts/x.py",
        # Prose merely NAMING a handle.
        "The `$DEVRC` handle is exported by nix/agent-handles.nix.",
        # A path under home that no handle covers.
        "`/home/zach/.config/opencode/plugin/env.js`",
        "`/home/zach/workspace/civit/civitai-orchestration/README.md`",
        # An absolute path that is not under a home at all.
        "`/nix/store/abc-devrc/bin/x`",
        "`/etc/nixos/configuration.nix`",
    ],
    ids=lambda v: v[:52],
)
def test_a_correct_spelling_stays_green(text):
    assert _found(text) == [], f"planting {text!r} turned the gate red on input it must not flag."


def test_an_ignore_entry_licenses_only_the_doc_it_names():
    """🔴 The key is `(doc, literal)`, not the literal alone.

    Otherwise one justified exemption -- a sample API payload showing a real
    `cwd` -- silently exempts the identical string in every doc that copies it,
    which is precisely how a spelled-out path spreads through a skill corpus.
    Both arms graded: the named doc keeps its licence, any other doc does not."""
    ignored, _ = _read_ignore()
    assert ignored, (
        "the ignore list is empty, so this probe is vacuous. If the list is "
        "legitimately empty, keep the probe but plant a synthetic pair instead."
    )
    doc_a, literal = sorted(ignored)[0]
    assert doc_a != PROBE_DOC

    licensed = _filter(_violations({doc_a: literal}), ignored)
    assert licensed == [], (
        f"{doc_a}\t{literal} is on the ignore list but was still reported in its "
        "own doc -- the hatch does not work."
    )
    elsewhere = _filter(_violations({PROBE_DOC: literal}), ignored)
    assert len(elsewhere) == 1 and elsewhere[0][0] == PROBE_DOC, (
        f"an ignore entry for {doc_a} licensed the same literal cited from "
        f"{PROBE_DOC}. The key must be (doc, literal), not literal."
    )


def test_the_remedy_names_the_handle_and_keeps_the_tail():
    """The failure line IS the fix, so the tail must survive verbatim. A remedy
    that dropped it would read as a correct instruction and send the reader to
    the wrong file."""
    assert _remedy("/home/zach/workspace/devrc/scripts/lib/handoff_doc.py", "DEVRC") == (
        "$DEVRC/scripts/lib/handoff_doc.py"
    )
    assert _remedy("/root/workspace/devrc", "DEVRC") == "$DEVRC"
    assert _remedy("/home/alice/.kube/homelab-nebula.yaml", "KC_NEBULA") == "$KC_NEBULA"
    # 🔴 The repeated-suffix case, where `rfind` would delete the middle of the
    # path and still print something that reads like a correct instruction.
    assert _remedy(
        "/home/ada/workspace/devrc/t/workspace/devrc/q.py", "DEVRC"
    ) == "$DEVRC/t/workspace/devrc/q.py"


def test_a_missing_ignore_file_raises_LOUDLY_rather_than_reading_as_empty(monkeypatch,
                                                                          tmp_path):
    """POSITIVE CONTROL on the missing-file guard.

    The file exists in every real checkout, so that branch never executes on a
    healthy tree -- indistinguishable from a guard wired to nothing. 🔴 Pin the
    TYPE and the MESSAGE: deleting the guard still raises, just a different
    exception meaning "the run crashed" instead of "the verdict is meaningless"."""
    monkeypatch.setattr(
        "test_absolute_handle_paths.IGNORE_FILE", tmp_path / "absent.list"
    )
    with pytest.raises(AssertionError, match=r"is missing\. This gate's verdict"):
        _read_ignore()
