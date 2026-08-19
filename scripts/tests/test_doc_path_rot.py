"""Doc-rot gate: every repo path a devrc doc CLAIMS exists, must exist.

WHY THIS EXISTS
---------------
`claude/skills/<name>/SKILL.md` is loaded VERBATIM into an agent's context as
authoritative operating instructions. A path that no longer exists does not read
as stale prose -- it reads as an instruction, and it gets executed. The agent
burns a round trip discovering the file is not there, or worse, hand-rebuilds a
procedure the repo already ships.

The trigger case: `claude/skills/prune-skill/SKILL.md` pointed at
`tests/test_skill_size.py`. Nothing in this repo has that path. The nearest file
with that basename is `scripts/browser-bridge/tests/test_skill_size.py`, which
gates a DIFFERENT skill and holds DIFFERENT constants -- so an agent following
the pointer would have read the wrong ceiling and been confident about it. The
dominant failure mode is not deletion; it is a REAL file described by a path
that resolves somewhere else. (That token is not one this gate can settle -- see
`test_stated_limit_a_non_root_first_segment_is_invisible` -- but its repaired
form, `scripts/browser-bridge/tests/test_skill_size.py`, IS checked here, so the
repair is now pinned.)

The corpus is ~80 tracked markdown files under `claude/` plus `CLAUDE.md`, and
none of it was checked before this module.

WHAT COUNTS AS A CHECKED PATH -- the placeholder convention
-----------------------------------------------------------
A token is checked only if ALL of these hold. Anything else is IGNORED, which is
how you write a placeholder that does not trip the gate.

  1. It sits inside a MATCHED pair of `backticks`, inside a fenced code block,
     or as the DESTINATION of a markdown link (`](dest)`). Prose is never
     scanned -- including the trailing text on a line with an ODD number of
     backticks, which has no closing delimiter.
  2. It RESOLVES: its first segment is a real top-level directory of this repo
     (`ROOTS`), or it is `..`-relative, or it starts with a DEPLOYED spelling
     that `nix/home.nix` populates from this repo (`DEPLOYED_MAP`). It has at
     least two segments.
  3. It contains no placeholder / glob / shell metacharacter. Write a variable
     segment as `<name>` -- `claude/skills/<name>/SKILL.md` is skipped
     automatically. NEVER a bare stand-in like `claude/skills/X/SKILL.md`: that
     is shaped exactly like a real path and WILL be flagged.
  4. Its shape is unambiguously file-or-dir: it ends in `/`, or its last segment
     carries a known extension, or its first segment is `scripts` (extensionless
     executables like `scripts/obs-read` are real). An extensionless path
     anywhere else is treated as prose.

Escape hatch for a genuine exception: `IGNORE_FILE`, one token per line, exact
or a `/`-terminated prefix.

RELATIVE PATHS RESOLVE AGAINST THE CITING DOC'S OWN DIRECTORY
--------------------------------------------------------------
A token starting with `..` is normalised against `dirname(<the doc>)`. This
matters because `prune-skill`'s own headline remedy is DEMOTE_TO_REFERENCE --
move a block from `SKILL.md` into `reference/<topic>.md`, ONE DIRECTORY DEEPER
-- and every `../` in the moved text silently changes meaning. Prefer a
repo-root-relative path in a doc: it is depth-independent, so a later demotion
cannot break it.

HERMETICITY -- the gate never stats anything outside this repo
---------------------------------------------------------------
`~/.claude/` is the DEPLOYED tree, populated by `nix/home.nix` from this repo. A
doc may legitimately write either spelling, so the deployed one is MAPPED back
in-tree and settled there. Nothing here reads the real `~/.claude/`: the verdict
must be identical in a fresh clone, in a worktree, and on a host that has never
run `home-manager switch`. Three things enforce that:

  * ONLY the spellings `nix/home.nix` actually deploys are mapped. A per-host
    file that this repo does not own -- `~/.claude/settings.json` (the home.nix
    comments call the registration "per-host/unmanaged"), `~/.claude/CLAUDE.md`
    ("stays per-host/mutable"), `~/.claude/clawgate.env`,
    `~/.claude/analyze-service-index/` -- is UNVERIFIABLE, not dead. It is
    skipped and never stat'd. Mapping all of `~/.claude/` onto `claude/`
    instead would have reported 22 live host files as rot;
  * no `$HOME`-EXPANDED spelling is mapped. A row keyed on `Path.home()` would
    make whether a token is even COUNTED depend on which user runs the gate --
    `/home/zach/...` classified on one host and skipped on another. Absolute
    paths are never claims this repo can settle;
  * `_under_repo` contains every resolution: an unmapped-but-absolute remainder
    cannot discard its base (`Path("/a") / "/b" == Path("/b")`), and a `..` run
    escaping the root is redirected onto `OFF_TREE_MARKER` with the `..`
    segments dropped, since the OS resolves `..` at stat time.

THE DELTA CONTRACT
------------------
A pre-existing dead reference WARNS; a newly-introduced one BLOCKS. The
currently-dead set is checked in at `BASELINE_FILE`. It may only SHRINK: a
baselined reference that is no longer dead is a FAILURE telling you to delete
the line. Growing it requires an explicit, visible edit to a tracked file.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HERE = Path(__file__).resolve().parent
BASELINE_FILE = HERE / "doc-path-baseline.txt"
IGNORE_FILE = HERE / "doc-path-ignore.txt"

# The corpus. Built from the INDEX (`git ls-files`), not the working tree, so
# the same commit validates identically for everyone -- and so an untracked
# scratch doc cannot change the verdict. Untracked candidates get their own
# test rather than being silently skipped.
#
# `claudedocs/` is deliberately OUT: it is the handoff/scratch tree, where a
# reference is a pointer rather than an operational instruction and docs are
# routinely left uncommitted for a while. `docs/`, `README.md` and `SECRETS.md`
# are out for the opposite reason -- they are read by humans, who notice a 404.
# What is in is exactly what an agent loads as instructions.
CORPUS_DIRS = ("claude", "CLAUDE.md")

# Top-level directories whose paths are claims about THIS repo. A token whose
# first segment is not one of these is prose, or a path relative to some OTHER
# repo's root, and is not our business. Pinned against the real tree by
# `test_roots_are_the_real_top_level_directories`.
ROOTS = frozenset({"claude", "claudedocs", "cmd", "docs", "githooks", "nix", "scripts"})

# Extensions that make a last segment unambiguously a file.
EXTS = frozenset(
    """yaml yml sh py mjs cjs js ts tsx json jsonc jsonnet md sql go rs tf toml
    txt nix conf lock html css service timer socket target env ini cfg xml csv
    png jpg svg gz zst sops age""".split()
)

# Characters that opt a token out: a placeholder, a glob, or shell syntax.
META = re.compile(r"""[<>{}*?\[\]$()|!=%@,"'\\…]""")

# The segment an off-tree route is redirected onto. No committed tree contains
# it, so a route that would otherwise resolve outside the repo dangles on EVERY
# host instead of borrowing a verdict from whatever sits next to the checkout.
OFF_TREE_MARKER = "<outside-this-repo>"

# --- the deployed tree -------------------------------------------------------
# `nix/home.nix` populates `~/.claude/` from this repo, so a `~/.claude/...`
# token IS a claim about this tree -- written in the spelling an agent sees at
# runtime. Mapping it back in-tree is what lets the gate settle it hermetically.
#
# ORDER MATTERS: first match wins, so the exceptions to the recursive
# `claude/skills` mapping come first. They are `mkOutOfStoreSymlink`s pointing
# at OTHER trees, and resolving them against `claude/skills/` would report every
# one of them dead:
#
#   ~/.claude/skills/browser/      <- scripts/browser-bridge/
#   ~/.claude/skills/dl-router/    <- scripts/dl-router/
#   ~/.claude/skills/close-the-loop/{STATE,ARCHIVE}.md
#                                  <- claudedocs/close-the-loop/
#   ~/.claude/hooks/agent_ledger.py <- scripts/lib/agent_ledger.py
#
# This table is NOT trusted prose: `test_deployed_map_matches_home_nix` parses
# every `home.file.".claude/…"` entry out of `nix/home.nix` and asserts the two
# sets agree in BOTH directions. A new deploy entry fails that test rather than
# silently producing a false verdict here.
#
# A key ending in "/" is a PREFIX; anything else must match the token exactly.
DEPLOYED_MAP: tuple[tuple[str, Path], ...] = (
    ("~/.claude/skills/browser/", REPO_ROOT / "scripts/browser-bridge"),
    ("~/.claude/skills/dl-router/", REPO_ROOT / "scripts/dl-router"),
    ("~/.claude/skills/close-the-loop/STATE.md", REPO_ROOT / "claudedocs/close-the-loop/STATE.md"),
    ("~/.claude/skills/close-the-loop/ARCHIVE.md", REPO_ROOT / "claudedocs/close-the-loop/ARCHIVE.md"),
    ("~/.claude/hooks/agent_ledger.py", REPO_ROOT / "scripts/lib/agent_ledger.py"),
    ("~/.claude/hooks/", REPO_ROOT / "scripts/claude-hooks"),
    ("~/.claude/skills/", REPO_ROOT / "claude/skills"),
    ("~/.claude/RULES.md", REPO_ROOT / "claude/RULES.md"),
    ("~/.claude/PRINCIPLES.md", REPO_ROOT / "claude/PRINCIPLES.md"),
    # Not a deploy entry -- a spelling of this repo's own root that docs use.
    # Tilde-only on purpose: see the HERMETICITY note in the module docstring.
    ("~/workspace/devrc/", REPO_ROOT),
)


def _under_repo(base: Path, rel: str) -> Path:
    """`rel` joined under `base`, guaranteed to stay inside the repo tree.

    Two steps, both load-bearing for hermeticity:

      * the containment redirect: anything that normalises ABOVE the repo root
        -- a `..` run, or an absolute remainder, which DISCARDS its base
        (`os.path.join("/a", "/b") == "/b"`) -- is sent to `OFF_TREE_MARKER`,
        with the `..` segments DROPPED rather than carried into the result (the
        OS resolves `..` at stat time, so keeping them would let the escape
        complete if such a directory ever existed). This is the guard that
        makes hermeticity hold, and it holds unconditionally: every base here
        is inside the repo, so an escape can only ever land outside it;
      * `rel.lstrip("/")`, which is NOT a second hermeticity guard -- the
        redirect above already covers that case, and a mutation test proved it
        (deleting the lstrip left every hermeticity assertion green). Its real
        job is avoiding a FALSE RED: a doubled slash (`~/workspace/devrc//x`,
        the shape a concatenated path takes) yields an absolute remainder, and
        without the strip a perfectly live file resolves off-tree and is
        reported dead. Pinned by `test_a_doubled_slash_still_resolves`.
    """
    joined = Path(os.path.normpath(os.path.join(str(base), rel.lstrip("/"))))
    if joined != REPO_ROOT and REPO_ROOT not in joined.parents:
        tail = [seg for seg in rel.split("/") if seg not in ("", ".", "..")]
        return REPO_ROOT.joinpath(OFF_TREE_MARKER, *tail)
    return joined


def _resolve(token: str, doc: str) -> Path | None:
    """Where `token`, cited from `doc`, points -- as a path INSIDE this repo.

    `None` means "not a claim this repo can settle", which is a SKIP, not a
    failure. That covers another repo's relative paths (`internal/crawler/x.go`),
    other `~/` locations, `.claude/...` without a tilde (in this corpus that
    spelling always names ANOTHER repo's in-tree `.claude/`, never devrc's
    deployed tree), and every absolute path.

    Deliberately the SINGLE classifier: `_is_path_claim` calls it rather than
    re-deriving the namespace test. Two open-coded copies would drift, and the
    failure is silent in both directions.
    """
    for key, base in DEPLOYED_MAP:
        if key.endswith("/"):
            if token.startswith(key):
                return _under_repo(base, token[len(key):])
            if token == key.rstrip("/"):
                return base
        elif token == key:
            return base
    # `"../"` WITH the slash, not `".."`. `.../a/b` is ELLIPSIS NOTATION for an
    # elided prefix -- it is written in this corpus -- and `".."` would match it,
    # normalise it against the citing doc and report a dead path for a token
    # that never named a file. Pinned by the `.../static/icons/icon-192.png`
    # probe below.
    if token.startswith("../"):
        return _under_repo(REPO_ROOT / (os.path.dirname(doc) or "."), token)
    if token.split("/", 1)[0] in ROOTS:
        return _under_repo(REPO_ROOT, token)
    return None


# --- extraction --------------------------------------------------------------

_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})(.*)$")
_LINK_DEST = re.compile(r"\]\(([^()\s]+)\)")
_EXT = re.compile(r"\.([A-Za-z0-9]+)$")


def _code_spans(line: str) -> list[str]:
    """The contents of every MATCHED pair of backticks on `line`.

    Splitting on backticks puts inline code in the ODD-indexed parts. The upper
    bound stops one short of the last part: with an ODD number of backticks the
    final part has no CLOSING delimiter, so it is ordinary prose. Without that,
    a sentence merely mentioning the backtick character would flag paths the
    identical sentence without it would not -- an inconsistency that makes a
    gate look arbitrary.
    """
    parts = line.split("`")
    return [parts[i] for i in range(1, len(parts) - 1, 2)]


def _candidate_tokens(text: str):
    """Yield `(lineno, token)` for every token in CODE CONTEXT in `text`.

    FENCE TRACKING IS COMMONMARK-DELIMITER-AWARE, NOT A PARITY TOGGLE.
    A naive `fence = not fence` fires on ANY line whose first non-blank run is
    ``` or ~~~, so it cannot tell an OPENING fence (which may carry an info
    string) from a CLOSING one (which per CommonMark may NOT), and it ignores
    the delimiter's char and run length. ONE correctly-written nested example --
    an outer 4-backtick block wrapping 3-backtick samples -- is then enough to
    invert the parity for the WHOLE REST OF THE FILE, and the error is silent:
    the scanner keeps running, it just applies the wrong mode. In the reference
    implementation this hid ~520 unscanned lines of one skill.

    So: remember the opening delimiter's CHARACTER and LENGTH, and close only on
    a conforming line -- same char, at least as long, nothing but whitespace
    after it. A ``` line inside a ~~~ block is content, and vice versa; a run
    shorter than the opener is content; ```action can never close anything.

    Deliberate deviation from CommonMark: the leading-whitespace bound is NOT
    capped at 3 columns. CommonMark measures that cap relative to the containing
    block, so a fence nested in a list item legitimately sits deeper, and a flat
    column-0 cap misreads every one of them.
    """
    fence_char = ""
    fence_len = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = _FENCE.match(line)
        if m:
            run, rest = m.group(1), m.group(2)
            char, length = run[0], len(run)
            if not fence_char:
                # OPENING: an info string is allowed, except that a backtick
                # fence may not carry a backtick in it (CommonMark).
                if not (char == "`" and "`" in rest):
                    fence_char, fence_len = char, length
                    continue
            elif char == fence_char and length >= fence_len and not rest.strip():
                fence_char, fence_len = "", 0
                continue
            # Not a conforming delimiter -> ordinary content; fall through.
        if fence_char:
            for word in line.split():
                yield lineno, word
        else:
            # A markdown link destination is a MACHINE-FOLLOWABLE claim -- a
            # stronger promise than a backticked path, not a weaker one. Scanned
            # outside fences only: inside one the whole line is word-split
            # already, and `[t](dest)` carries brackets that rule 3 rejects.
            for dest in _LINK_DEST.findall(line):
                yield lineno, dest.split("#", 1)[0]
            for span in _code_spans(line):
                for word in span.split():
                    yield lineno, word


# A SELECTOR appended to a real path: a pytest node id (`file.py::TestCase`) or
# a line/range citation (`file.sh:28-34`, `file.py:120`). Both name a file that
# exists and both were reported DEAD by the first draft of this gate -- a
# correct reference the gate called rot, which is a defect in the gate.
_SELECTOR = re.compile(r"(::.*|:\d+(-\d+)?)$")


def _normalise(token: str) -> str:
    token = token.lstrip("([").rstrip(".,;:)]")
    token = _SELECTOR.sub("", token)
    if token.startswith("./"):
        token = token[2:]
    return token


def _is_path_claim(token: str, doc: str) -> bool:
    """Rules 2-4: is this token a claim that a file exists in this repo?"""
    if not token or META.search(token):
        return False
    segments = token.split("/")
    if len(segments) < 2:
        return False
    if token.startswith("../"):
        # A token made of NOTHING BUT dots and slashes (`../`, `../../../`) is
        # notation: it names no file, so it cannot be dead. Prose explaining
        # relative paths -- which this repo's docs do -- is full of them, and a
        # long enough run escapes the repo root and would be reported dead.
        if not any(s not in ("..", "", ".") for s in segments):
            return False
    if _resolve(token, doc) is None:
        return False
    if token.endswith("/"):
        return True
    last = segments[-1]
    if not last:
        return False
    # The extensionless affordance is a `scripts/` one only, and it is keyed on
    # what the AUTHOR WROTE -- a relative or deployed token is never granted it,
    # so a bare `../something` stays prose.
    if segments[0] == "scripts":
        return True
    ext = _EXT.search(last)
    return bool(ext and ext.group(1) in EXTS)


# --- data files --------------------------------------------------------------


def _read_list(path: Path) -> list[str]:
    if not path.is_file():
        # LOUD, never silent. A missing data file does not degrade gracefully:
        # it reclassifies every exempt reference as dead, or (for the baseline)
        # every known-dead one as NEW. That is exactly how the reference
        # implementation's repo produced a confident false alarm -- the script
        # was pulled into a clone without its sidecar.
        raise AssertionError(
            f"{path} is missing. This gate's verdict is meaningless without it; "
            "restore it from git rather than letting the run continue."
        )
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def _ignored(token: str, ignores: list[str]) -> bool:
    return any(token == i or (i.endswith("/") and token.startswith(i)) for i in ignores)


# --- the gate ----------------------------------------------------------------


def _git(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _corpus() -> list[str]:
    """Tracked markdown docs in the corpus, repo-relative, from the INDEX."""
    return sorted(p for p in _git("ls-files", "-z", "--", *CORPUS_DIRS) if p.endswith(".md"))


def _references(docs: dict[str, str] | None = None) -> list[tuple[str, int, str]]:
    """Every checked `(doc, lineno, token)`, ignore-list applied.

    `docs` overrides the corpus with `{path: text}` -- used by the probes so
    they grade THE GATE rather than a re-implementation of it.
    """
    ignores = _read_list(IGNORE_FILE)
    if docs is None:
        docs = {d: (REPO_ROOT / d).read_text(encoding="utf-8") for d in _corpus()}
    refs = []
    for doc, text in docs.items():
        for lineno, raw in _candidate_tokens(text):
            token = _normalise(raw)
            if _is_path_claim(token, doc) and not _ignored(token, ignores):
                refs.append((doc, lineno, token))
    return refs


def _dead(refs) -> list[tuple[str, int, str]]:
    out = []
    for doc, lineno, token in refs:
        target = _resolve(token, doc)
        if target is None or not target.exists():
            out.append((doc, lineno, token))
    return out


def _baseline() -> set[tuple[str, str]]:
    """The checked-in set of KNOWN-dead references, as `(doc, token)` pairs.

    Deliberately WITHOUT line numbers: those churn on every unrelated edit and
    would turn the baseline into merge-conflict bait, which is how a delta
    baseline stops being maintained.
    """
    out = set()
    for line in _read_list(BASELINE_FILE):
        doc, sep, token = line.partition("\t")
        assert sep and token.strip(), (
            f"malformed baseline line (want '<doc>\\t<token>'): {line!r}"
        )
        out.add((doc.strip(), token.strip()))
    return out


# --- tests: positive controls ------------------------------------------------


def test_corpus_is_not_empty():
    """POSITIVE CONTROL on the corpus builder. A reassuring zero here is
    indistinguishable from a gate wired to nothing."""
    docs = _corpus()
    assert len(docs) >= 70, (
        f"only {len(docs)} corpus doc(s) found -- expected the ~80 tracked "
        f"markdown files under {CORPUS_DIRS}. The corpus builder has broken."
    )


def test_reference_count_is_in_the_hundreds():
    """POSITIVE CONTROL on the extractor itself.

    A gate that checks 0 references passes forever and means nothing. This
    corpus is ~700 KB of operational prose densely written in backticked paths;
    if the count collapses, the extractor broke, not the corpus. The floor is
    set well under the measured value (300 vs ~330) so ordinary doc churn does
    not red it, and it is a FLOOR -- raise it, never lower it.
    """
    n = len(_references())
    assert n >= 300, (
        f"the extractor found only {n} path reference(s) in the corpus. That is "
        "far below what this corpus contains -- the fence tracker, the code-span "
        "reader or the namespace test has broken, and a PASS from this module "
        "would be a claim about the extractor, not about the docs."
    )


# --- tests: the gate ---------------------------------------------------------


def test_no_new_dead_paths():
    """THE GATE. A pre-existing dead reference warns; a new one blocks."""
    dead = _dead(_references())
    baseline = _baseline()
    new = [(d, n, t) for d, n, t in dead if (d, t) not in baseline]
    assert not new, (
        f"{len(new)} NEW dead path reference(s) -- a doc claims a file that does "
        "not exist:\n"
        + "\n".join(f"  {d}:{n}  DEAD PATH: {t}  ->  {_resolve(t, d)}" for d, n, t in new)
        + "\n\nA SKILL.md is loaded as AUTHORITATIVE guidance, so a dead path "
        "sends an agent down a route that is not there. Fix the prose to "
        "describe current reality, or write a variable segment as <name> so it "
        f"is skipped. Genuine exception -> scripts/tests/{IGNORE_FILE.name}.\n"
        f"Do NOT add it to {BASELINE_FILE.name}: that file records rot that "
        "PREDATES this gate and may only shrink."
    )


def test_baseline_only_shrinks():
    """A baselined reference that is no longer dead must be DELETED from the
    baseline.

    Without this the baseline is a ratchet with no pawl: the rot gets fixed, the
    line stays, and the stale entry silently re-licenses the same dead path when
    someone reintroduces it later.
    """
    dead = {(d, t) for d, _, t in _dead(_references())}
    stale = sorted(_baseline() - dead)
    assert not stale, (
        f"{len(stale)} baseline entry/entries no longer name a dead reference:\n"
        + "\n".join(f"  {d}\t{t}" for d, t in stale)
        + f"\n\nThe rot was fixed, or the doc moved. Delete these lines from "
        f"scripts/tests/{BASELINE_FILE.name} -- it may only shrink."
    )


def test_no_untracked_corpus_candidates():
    """THE SILENT ZERO. The corpus is `git ls-files`, i.e. the INDEX, so a doc
    created and not yet `git add`ed is not merely unchecked -- it is UNCOUNTED,
    and the only tell would be a reference count that fails to move.

    Reported rather than checked, because widening the corpus to the working
    tree would make the same commit validate differently for two people.
    """
    untracked = sorted(
        p for p in _git("ls-files", "-z", "--others", "--exclude-standard", "--", *CORPUS_DIRS)
        if p.endswith(".md")
    )
    assert not untracked, (
        "untracked corpus candidate doc(s) -- NOT CHECKED, NOT COUNTED:\n"
        + "\n".join(f"  {p}" for p in untracked)
        + "\n`git add` them and re-run so their path claims are actually checked."
    )


def test_roots_are_the_real_top_level_directories():
    """ROOTS is a claim about the tree; pin it so a new top-level directory is a
    visible decision rather than a silently unchecked namespace."""
    actual = {p.name for p in REPO_ROOT.iterdir() if p.is_dir() and not p.name.startswith(".")}
    assert ROOTS == actual, (
        f"ROOTS disagrees with the tree.\n  only in ROOTS: {sorted(ROOTS - actual)}"
        f"\n  only on disk: {sorted(actual - ROOTS)}\n"
        "A directory missing from ROOTS means every path claim into it is "
        "silently unchecked."
    )


# --- tests: the deployed-tree ledger -----------------------------------------

_HOME_FILE_ENTRY = re.compile(r'home\.file\."(\.claude/[^"]*)"')


def test_deployed_map_matches_home_nix():
    """SEAM GUARD: `DEPLOYED_MAP` is a claim about `nix/home.nix`.

    The two are different files nobody owns together, which is exactly the seam
    where a defect lives: add a `home.file.".claude/…"` entry pointing at a new
    source tree and this gate keeps resolving it against the OLD base, reporting
    a live file as dead or -- worse -- a dead one as live. Verified against
    home.nix rather than trusted as prose, and asserted as a LEDGER that fails
    when the set GROWS or SHRINKS, not as a spot check.
    """
    nix = (REPO_ROOT / "nix/home.nix").read_text(encoding="utf-8")
    deployed = {"~/" + p for p in _HOME_FILE_ENTRY.findall(nix)}
    assert len(deployed) >= 15, (
        f"parsed only {len(deployed)} home.file .claude entr(ies) from "
        "nix/home.nix -- the parser broke, so this ledger proves nothing."
    )

    keys = [k for k, _ in DEPLOYED_MAP if k.startswith("~/.claude/")]

    unmapped = sorted(
        d for d in deployed
        if not any(d == k or d == k.rstrip("/") or d.startswith(k) for k in keys)
    )
    assert not unmapped, (
        f"nix/home.nix deploys these into ~/.claude/ but DEPLOYED_MAP has no "
        f"mapping: {unmapped}\nAdd a row (BEFORE the general prefix if it is an "
        "exception) so a doc citing the deployed spelling is settled against the "
        "right source tree."
    )

    orphaned = sorted(
        k for k in keys
        if not any(d == k or d == k.rstrip("/") or d.startswith(k) or k.startswith(d + "/")
                   for d in deployed)
    )
    assert not orphaned, (
        f"DEPLOYED_MAP rows with no matching home.file entry in nix/home.nix: "
        f"{orphaned}\nThe deploy changed; re-derive the mapping."
    )


def test_deployed_mapping_targets_exist():
    """Each mapped source tree must be real -- a mapping onto a path that does
    not exist would report every doc reference through it as dead."""
    missing = sorted(str(b) for _, b in DEPLOYED_MAP if not b.exists())
    assert not missing, f"DEPLOYED_MAP maps onto nonexistent trees: {missing}"


# =============================================================================
# PROBES -- the gate's own regression coverage.
#
# Every case is planted into a SYNTHETIC doc at a real corpus path and graded by
# the REAL `_references` / `_dead`, so a mutation anywhere in the pipeline -- the
# fence tracker, the code-span reader, the link scanner, `_normalise`,
# `_is_path_claim`, `DEPLOYED_MAP`, `_resolve`, `_under_repo` -- moves a verdict
# here. Grading is by EQUALITY, not "is non-empty": that catches a mutation
# which ADDS a reference as well as one that drops it.
# =============================================================================

# A doc path deep enough that `../` is meaningful, and that really exists --
# pinned by `test_probe_doc_is_a_real_corpus_doc`, because a PROBE_DOC that does
# not exist still "works" (the probes feed synthetic text, and `../` resolves
# off the DIRECTORY) while quietly no longer describing the corpus.
PROBE_DOC = "claude/skills/prune-skill/reference/always-loaded-and-landing.md"
# A file that really exists, addressed from PROBE_DOC's directory:
#   claude/skills/prune-skill/reference/../SKILL.md
LIVE_RELATIVE = "../SKILL.md"
LIVE_ROOTED = "claude/skills/prune-skill/SKILL.md"


def test_probe_doc_is_a_real_corpus_doc():
    """The probes plant into SYNTHETIC text at PROBE_DOC, so a PROBE_DOC that
    stopped existing would keep them all green while they no longer describe
    anything in the corpus. Caught for real: the first draft named a file that
    had been renamed."""
    assert PROBE_DOC in _corpus(), f"{PROBE_DOC} is not a tracked corpus doc"
    assert (REPO_ROOT / os.path.dirname(PROBE_DOC) / LIVE_RELATIVE).exists(), (
        f"{LIVE_RELATIVE} from {PROBE_DOC} no longer names a real file, so every "
        "legitimate-relative-pointer probe is green for the wrong reason."
    )
    assert (REPO_ROOT / LIVE_ROOTED).exists()


def _refs(markdown: str, doc: str = PROBE_DOC):
    return [(d, t) for d, _, t in _references({doc: markdown})]


def _dead_tokens(markdown: str, doc: str = PROBE_DOC):
    return sorted(t for _, _, t in _dead(_references({doc: markdown})))


@pytest.mark.parametrize(
    "markdown,token",
    [
        # NEGATIVE CONTROL 1 -- a backticked span.
        ("Run `claude/skills/no-such-skill/SKILL.md` first.",
         "claude/skills/no-such-skill/SKILL.md"),
        # NEGATIVE CONTROL 2 -- a markdown link destination. A link is a
        # machine-followable claim; prose is not scanned, so this is the only
        # thing that catches it.
        ("See [the notes](claude/skills/no-such-skill/notes.md) for detail.",
         "claude/skills/no-such-skill/notes.md"),
        # NEGATIVE CONTROL 3 -- a `../` reference, resolved against the CITING
        # DOC's directory. From claude/skills/prune-skill/reference/ this is
        # claude/skills/prune-skill/gone.md, which does not exist.
        ("Back up to `../gone.md`.", "../gone.md"),
        # A `../` that escapes the repo root: contained, and reported.
        ("`../../../../../../etc/passwd.md`", "../../../../../../etc/passwd.md"),
        # Inside a fenced code block, where the whole line is word-split.
        ("```bash\nbash scripts/no-such-script.sh\n```", "scripts/no-such-script.sh"),
        # The DEPLOYED spelling of a skill that does not exist.
        ("`~/.claude/skills/no-such-skill/SKILL.md`",
         "~/.claude/skills/no-such-skill/SKILL.md"),
        # A deployed hook -- resolved against scripts/claude-hooks/, not claude/.
        ("`~/.claude/hooks/no-such-hook.py`", "~/.claude/hooks/no-such-hook.py"),
        # `scripts/` extensionless affordance: a real class of executable here.
        ("`scripts/no-such-tool`", "scripts/no-such-tool"),
    ],
    ids=["backtick", "link-dest", "dotdot", "dotdot-escape", "fence", "deployed",
         "deployed-hook", "extensionless"],
)
def test_planted_dead_path_is_reported(markdown, token):
    """NEGATIVE CONTROL: the gate must go red AND name the file and the token."""
    assert _dead_tokens(markdown) == [token], (
        f"planting {markdown!r} did not produce exactly [{token!r}] as dead. A "
        "gate that stays green on a planted dead path is testing nothing."
    )
    assert (PROBE_DOC, token) in _refs(markdown), "the reference was not attributed to its doc"


@pytest.mark.parametrize(
    "markdown",
    [
        # Correct pointers, in every shape the gate understands.
        f"`{LIVE_ROOTED}`",
        f"`{LIVE_RELATIVE}`",
        f"[core]({LIVE_RELATIVE})",
        "`~/.claude/RULES.md`",
        "`~/.claude/skills/prune-skill/SKILL.md`",
        "`~/.claude/skills/browser/SKILL.md`",          # mkOutOfStoreSymlink exception
        "`~/.claude/skills/close-the-loop/STATE.md`",   # ledger exception
        "`~/.claude/hooks/bash-guard.py`",              # hooks -> scripts/claude-hooks
        "`~/.claude/hooks/agent_ledger.py`",            # hooks exception -> scripts/lib
        "`~/workspace/devrc/CLAUDE.md`",
        "`scripts/obs-read`",                           # extensionless, real
        # Extensionless OUTSIDE scripts/: prose, not a path. This is what keeps
        # k8s-style resource names and ordinary slashed words out.
        "`docs/no-such-thing`",
        "`scripts/tests/test_prune_skill_size.py::test_skill_md_exists`",  # pytest node id
        "`scripts/gate.sh:1-40`",                       # line-range citation
        # Placeholders and notation: skipped, never flagged.
        "`claude/skills/<name>/SKILL.md`",
        "`claude/skills/*/reference/*.md`",
        "`.../static/icons/icon-192.png`",
        # A dots-only run long enough to escape the repo root: notation, not a
        # claim. Six `..` from claude/skills/prune-skill/reference/ is two
        # levels above the root.
        "`../../../../../../`",
        "`$DEVRC/claude/RULES.md`",
        # A doubled slash -- the shape a concatenated path takes -- must still
        # resolve to the live file, not off-tree.
        "`~/workspace/devrc//claude/RULES.md`",
        # Not claims about this repo.
        "`internal/crawler/ssrf.go`",                   # another repo, relative
        "`~/.claude/settings.json`",                    # per-host, not deployed here
        "`.claude/skills/pyroscope/SKILL.md`",          # another repo's in-tree .claude
        "https://example.dev/claude/skills/x/SKILL.md",
        # Prose is never scanned, even when it is path-shaped.
        "The file claude/skills/no-such-skill/SKILL.md is gone.",
        # An ODD number of backticks: the trailing part has no closing
        # delimiter, so it is prose.
        "A `backtick` and then a stray ` claude/skills/nope/SKILL.md",
    ],
    ids=lambda v: v[:58],
)
def test_planted_legitimate_or_skipped_stays_green(markdown):
    """A correct pointer -- or a token this repo cannot settle -- must NOT be
    reported. `claude/RULES.md` names a permanently-red gate as worse than no
    gate, and a gate that reds on correct input becomes one."""
    assert _dead_tokens(markdown) == [], (
        f"planting {markdown!r} turned the gate red on input it must not flag."
    )


# --- fence handling ----------------------------------------------------------

# A legitimately-nested fence: an outer 4-backtick block wrapping 3-backtick
# examples. The inner markers number THREE -- odd on purpose. A doc that shows
# both halves of a fence and then a bare closing delimiter is an ordinary shape
# for a markdown-about-markdown page, and an EVEN count would leave a naive
# parity toggle accidentally balanced at EOF, so the fixture would not
# discriminate between the two implementations. That exact vacuity was caught by
# the control below on the first draft of this fixture, which is why the control
# exists.
NESTED_FENCE_DOC = """\
Intro.

````markdown
Here is how you write a fenced example:

```bash
bash scripts/gate.sh
```

…and a bare delimiter on its own, which is how you close one:

```
````

After the outer fence closes, checking must still be live:
`claude/skills/no-such-skill/AFTER.md`
"""

AFTER_TOKEN = "claude/skills/no-such-skill/AFTER.md"


def test_nested_fence_does_not_stop_the_scan():
    """A legitimately-nested fence must not blind the rest of the file.

    This is the exact shape that hid ~520 unscanned lines in the reference
    implementation, and the failure is SILENT: the scanner keeps running, it
    just applies the wrong mode. The dead token AFTER the outer fence is the
    observable.
    """
    assert _dead_tokens(NESTED_FENCE_DOC) == [AFTER_TOKEN]


def _naive_parity_tokens(text: str):
    """The fence tracker this module replaced: `fence = not fence`.

    Kept verbatim as a MUTANT, wired through the same downstream extraction, so
    the control below grades the real pipeline against the real old behaviour
    rather than against a description of it.
    """
    fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            fence = not fence
            continue
        if fence:
            for word in line.split():
                yield lineno, word
        else:
            for dest in _LINK_DEST.findall(line):
                yield lineno, dest.split("#", 1)[0]
            for span in _code_spans(line):
                for word in span.split():
                    yield lineno, word


def test_a_naive_parity_toggle_would_have_missed_that_token():
    """CONTROL ON THE CONTROL: prove the fixture discriminates.

    A fixture only earns its keep if the bug it targets actually walks into it.
    Run the OLD tracker over the same fixture and confirm it MISSES the dead
    token -- if it finds it too, `test_nested_fence_does_not_stop_the_scan` is
    passing vacuously and the fixture must be rebuilt.
    """
    naive = {
        _normalise(tok) for _, tok in _naive_parity_tokens(NESTED_FENCE_DOC)
    }
    assert AFTER_TOKEN not in naive, (
        "the naive parity toggle finds the post-fence token too, so the fixture "
        "no longer discriminates between the two fence implementations. Rebuild "
        "it with an ODD number of nested fence markers."
    )
    real = {_normalise(tok) for _, tok in _candidate_tokens(NESTED_FENCE_DOC)}
    assert AFTER_TOKEN in real, "the delimiter-aware walk lost the post-fence token"


@pytest.mark.parametrize(
    "markdown,expected",
    [
        # A tilde fence: a ``` line inside it is content, not a delimiter.
        ("~~~\n```\nbash scripts/no-such-script.sh\n```\n~~~", ["scripts/no-such-script.sh"]),
        # A closer may not carry an info string, so ```action never closes.
        ("```\nbash scripts/nope-a.sh\n```action\nbash scripts/nope-b.sh\n```",
         ["scripts/nope-a.sh", "scripts/nope-b.sh"]),
        # A run SHORTER than the opener is content, not a closer.
        ("````\nbash scripts/nope-a.sh\n```\nbash scripts/nope-b.sh\n````",
         ["scripts/nope-a.sh", "scripts/nope-b.sh"]),
        # A fence indented inside a list item is still a fence (no 3-column cap).
        ("1. Step:\n\n    ```bash\n    bash scripts/nope-c.sh\n    ```\n",
         ["scripts/nope-c.sh"]),
    ],
    ids=["tilde-fence", "info-string-cannot-close", "short-run-is-content",
         "indented-in-list-item"],
)
def test_fence_delimiter_rules(markdown, expected):
    assert _dead_tokens(markdown) == sorted(expected)


# --- hermeticity, executed ---------------------------------------------------


def test_no_resolution_ever_leaves_the_repo(tmp_path):
    """Machine state may not decide the verdict.

    A real file OUTSIDE the repo, addressed absolutely, must not be stat'd where
    it points -- otherwise the gate says one thing on this host and another in a
    fresh clone or in CI. Executed against a file that genuinely exists.
    """
    real = tmp_path / "claude" / "skills" / "ghost" / "SKILL.md"
    real.parent.mkdir(parents=True)
    real.write_text("a real file, outside the repo\n", encoding="utf-8")
    assert real.is_file(), "fixture broken: the off-repo file was not created"

    # An absolute path is not a claim this repo can settle: skipped entirely.
    assert _resolve(str(real), PROBE_DOC) is None
    assert _dead_tokens(f"`{real}`") == []

    # And every resolution that DOES happen stays inside the tree.
    for token in (
        str(Path.home() / ".claude/skills/prune-skill/SKILL.md"),
        "~/.claude/skills/../../../../etc/passwd.md",
        "../../../../../../etc/passwd.md",
        "~/workspace/devrc/../../../etc/passwd.md",
    ):
        target = _resolve(token, PROBE_DOC)
        if target is None:
            continue
        assert REPO_ROOT in target.parents or target == REPO_ROOT, (
            f"{token} resolved to {target}, OUTSIDE the repo -- the gate is "
            "reading the host filesystem and its verdict depends on the machine."
        )


def test_a_doubled_slash_still_resolves():
    """`rel.lstrip("/")` in `_under_repo`, made REACHABLE and graded.

    A doubled slash leaves an ABSOLUTE remainder after the prefix strip, and
    `os.path.join(base, "/x")` discards the base. Without the strip the
    containment redirect fires and a live file is reported dead -- a false red,
    which is the failure mode that gets a gate bypassed.
    """
    token = "~/workspace/devrc//claude/RULES.md"
    assert _resolve(token, PROBE_DOC) == REPO_ROOT / "claude/RULES.md"
    assert _dead_tokens(f"`{token}`") == []


def test_home_expanded_spelling_is_not_a_claim():
    """`$HOME`-expanded absolute paths are skipped, on every host.

    Mapping them would make whether a token is even COUNTED depend on which user
    runs the gate: `/home/zach/.claude/…` classified here, skipped in CI. The
    corpus does contain such a path (a per-host Claude project-memory dir), and
    it must be invisible rather than host-dependently dead.
    """
    token = str(Path.home() / ".claude/skills/prune-skill/SKILL.md")
    assert _resolve(token, PROBE_DOC) is None
    assert _refs(f"`{token}`") == []


def test_ignore_matcher_handles_both_forms():
    """POSITIVE CONTROL on the escape hatch.

    `doc-path-ignore.txt` is EMPTY today, so nothing in the corpus exercises
    `_ignored` — a matcher wired to nothing would look identical. Graded here
    directly, on both documented forms, so the hatch is known to work the day
    someone needs it.
    """
    ignores = ["claude/skills/gone/SKILL.md", "claudedocs/scratch/"]
    assert _ignored("claude/skills/gone/SKILL.md", ignores)          # exact
    assert _ignored("claudedocs/scratch/anything/deep.md", ignores)  # prefix
    assert not _ignored("claude/skills/gone/SKILL.md.bak", ignores)
    assert not _ignored("claudedocs/other/deep.md", ignores)
    assert not _ignored("claude/skills/gone/SKILL.md", [])


# --- stated limits -----------------------------------------------------------


@pytest.mark.parametrize(
    "markdown",
    [
        # The trigger case's ORIGINAL spelling. `tests` is not a top-level
        # directory of this repo, so the token is not a claim this gate can
        # settle -- it is invisible, not red.
        "`tests/test_skill_size.py`",
        # The deployed tree, misspelled ABOVE the mapped prefix.
        "`~/.claude/skils/prune-skill/SKILL.md`",
        # A path with no directory part: one segment, indistinguishable from
        # prose (`README.md`, `flake.nix` are also ordinary words in a sentence).
        "`no-such-file.md`",
    ],
    ids=["non-root-first-segment", "misspelt-above-the-mapped-prefix", "single-segment"],
)
def test_stated_limit_a_non_root_first_segment_is_invisible(markdown):
    """STATED LIMITS, pinned so they stay KNOWN limits rather than surprises.

    Each shape below is a dead reference the gate does NOT see. They are kept
    out deliberately -- widening to catch them would flag the ~200 tokens in
    this corpus that are correct paths relative to ANOTHER repo's root
    (`internal/crawler/ssrf.go`, `clusters/homelab/apps/…`, `web/css/input.css`)
    and turn the gate permanently red. If one of these ever needs catching, this
    test is where the decision was recorded.
    """
    assert _refs(markdown) == []
