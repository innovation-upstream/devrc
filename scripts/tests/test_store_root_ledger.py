#!/usr/bin/env python3
"""Which files resolve a subsystem-store root, and does each one go through the resolver?

🔴 WHY THIS EXISTS: THE SAME DEFECT SHIPPED THREE TIMES.

The Cairn cutover made a hosted pod the canonical datastore, FROZE the per-host
mirror at `~/.claude/analyze-service-index` (entry files `0444`, nothing
refreshes it) and introduced a synced cache at `~/.cache/subsystem-store` that
`cairn sync` maintains. `scripts/lib/subsystem_read_store.py` is the ONE place
that answers "where does this host read the store from".

Three shipped readers went on resolving the FROZEN mirror anyway:

    subsystem_recall.py's CLI     the reader `/resume` step 4 runs
    service_recon.py              the recon `/analyze-service` runs
    subsystem-audit.py            the auditor `/prune-index` runs

Each was found by a human noticing, one at a time, after the previous one was
fixed. Nothing in the tree could see the fourth. `subsystem_read_store`'s
docstring records the measurement: the frozen mirror served **26** `devrc/`
entries and the cache **29**, and the frozen one printed
"ALL 26 entries in `devrc/`, none omitted" — a completeness claim about a store
that had stopped moving the day before.

🔴 SO THIS IS A LEDGER, NOT A FOURTH ONE-OFF PIN. Two of them, each two-way:

  * `ROUTED` — every file that CALLS the resolver. Fails when the set SHRINKS,
    which is the direction that matters: a reader silently ceasing to route is
    the regression itself, and no per-file test can see it because the per-file
    tests are the ones that get deleted with it.
  * `SITED` — every file that computes a store-root path of its own. Fails when
    the set GROWS, which catches the fourth open-coded reader on the commit that
    introduces it rather than on the day somebody notices a stale answer.

A file may be in both: `scripts/cairn` routes for its `--cache` default and
separately names `~/.config/subsystem-store/env`, which is a credential file.
Over-inclusion is the SAFE direction here — a benign match costs one ledger row
with its reason written down, and a row nobody can justify is the finding.

🔴 EVERY EXPECTATION BELOW IS A LITERAL WRITTEN OUT IN THIS FILE. This
subsystem's PRs have hit `assert X == module.X` — a constant agreeing with
itself — five times, each fix narrower than the class. The scanners' vocabulary
(`STORE_ROOT_COMPONENTS`) is spelled here and then PINNED against the two live
constants by `TestTheScannerVocabularyMatchesTheRealPaths`, which is the seam:
rename the cache directory without updating this file and the scanner goes blind
to the very path it exists to police, silently, while every other test stays
green.

⚠ STATED RESIDUALS. Every one of these was MEASURED by planting the spelling as a
tracked file and running this module; the list is what SURVIVED, not what seemed
plausible. An approximate residual list is worse than none — it reads as coverage.

  * 🔴 **SCOPE IS `scripts/` ONLY.** 31 tracked `.py`/`.sh` files live outside it
    and are invisible here, and this is not theoretical: `nix/home.nix` names the
    frozen mirror today. `.nix` is neither of the two languages this module
    parses, so covering it needs a third arm and a third set of controls — named
    as a gap rather than half-built.
  * `scripts/tests/` and `scripts/testlib/` are OUT OF SCOPE. A test that
    hardcodes the mirror path is building a fixture, not resolving a store for a
    caller, and sweeping them in would bury the production population under ~14
    rows of fixtures. A reader hidden in a test directory is invisible here.
  * The Python scanner reads the AST, so a path assembled at RUN TIME — out of
    `os.environ`, or from a name bound earlier — is invisible. (`str.join` of
    literals is NOT in this class: the components are `ast.Constant`s and are
    caught. An earlier version of this list said otherwise, which is the
    approximate-residual failure in its own residual list.)
  * The shell scanner is a comment-stripped TEXT match over `.sh` files and
    shell shebangs. It would not see a path assembled from two variables, and it
    does not read `.nix`, `.yaml` or a systemd unit.
  * A file that RE-EXPORTS a ledgered constant under a new name is caught (the
    `import … as` arm), but a module that returns the mirror from a FUNCTION with
    no store name in its own source is not.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import subsystem_read_store as rs  # noqa: E402
import subsystem_touch as st  # noqa: E402

# =============================================================================
# THE SCANNERS' VOCABULARY. Literals, pinned against the live paths below.
# =============================================================================

#: The directory NAMES that make a path a subsystem-store root. Written out, not
#: read off either module — `TestTheScannerVocabularyMatchesTheRealPaths` is what
#: keeps them in step, and it is the only assertion in this file that touches
#: both sides of that relationship.
STORE_ROOT_COMPONENTS: frozenset[str] = frozenset(
    {"analyze-service-index", "subsystem-store"}
)

#: Names a file uses when it takes another module's store root, or declares its
#: own. `escrow-verify.py` and `restore-verify.py` reach the frozen mirror as
#: `B.DEFAULT_STORE` and spell no path at all, so a component scan alone would
#: report them clean — the transitive shape is why this set exists.
SITING_NAMES: frozenset[str] = frozenset(
    {"DEFAULT_STORE", "DEFAULT_STORE_ROOT", "DEFAULT_CACHE_ROOT"}
)

#: The resolver's two entry points. A file that calls one of these has asked
#: `subsystem_read_store` where to read, which is the whole property.
RESOLVER_CALLS: frozenset[str] = frozenset({"read_store_root", "resolve_read_store"})

#: Directories whose files build fixtures rather than resolve stores. See the
#: residuals in the module docstring — this is an enumerated exclusion, not a
#: pattern, so a new production directory cannot fall into it by accident.
OUT_OF_SCOPE_PREFIXES: tuple[str, ...] = ("scripts/tests/", "scripts/testlib/")


# =============================================================================
# THE LEDGERS
# =============================================================================

#: Files that MUST route through `subsystem_read_store`. The value says what the
#: file reads and therefore why it may not answer the question itself.
#:
#: 🔴 THE THREE HISTORICAL DEFECTS ARE ALL IN HERE. Each one is a file that once
#: hardcoded the frozen mirror; a row disappearing from this ledger is that
#: regression happening again.
ROUTED: dict[str, str] = {
    "scripts/lib/subsystem_read_store.py": (
        "THE resolver. `read_store_root` reads the module global at call time, "
        "which is what lets one `monkeypatch.setattr` repoint every consumer"
    ),
    "scripts/lib/subsystem_recall.py": (
        "the READER `/resume` step 4 runs. Regression #1 of three: its CLI "
        "defaulted `--store` to the frozen mirror and printed a completeness "
        "claim about a store that had stopped moving"
    ),
    "scripts/lib/service_recon.py": (
        "the recon `/analyze-service` runs. Regression #2 of three: same "
        "hardcoded mirror, found only after #1 was fixed"
    ),
    "scripts/subsystem-audit.py": (
        "the auditor `/prune-index` runs. Regression #3 of three: it declared "
        "its own `DEFAULT_STORE_ROOT`, which is why the constant is gone from it "
        "and only a comment records that it was ever there"
    ),
    "scripts/cairn": (
        "the client. Its `--cache` default is `read_store_root()` so the WRITER "
        "of the cache and every reader of it move together, and `doctor` "
        "resolves the read store to report which one this host actually reads"
    ),
}

#: Files that compute a store-root path THEMSELVES, each with the reason it is
#: allowed to. A row here is a deliberate exemption from `ROUTED`, and the reason
#: is the artifact: an exemption nobody can justify in a sentence is the finding.
SITED: dict[str, str] = {
    "scripts/lib/subsystem_read_store.py": (
        "THE OWNER. `DEFAULT_CACHE_ROOT` is the one definition of the synced "
        "cache; every other reader imports the MODULE and calls the accessor"
    ),
    "scripts/lib/subsystem_touch.py": (
        "THE WRITER's target. `DEFAULT_STORE_ROOT` is the pre-cutover local "
        "store — the constant `subsystem_read_store` was created to supersede "
        "for READS. It is not a read surface: `--validate` and `--store` here "
        "parse and write entries, and `cairn validate` passes the cache "
        "explicitly. Exempt because pointing it at the synced cache would make "
        "the writer's default target a directory `cairn sync` replaces wholesale"
    ),
    "scripts/cairn-cutover.py": (
        "EXEMPT — the mirror is its SUBJECT, not a store it reads for an answer. "
        "It plans the delta FROM the pre-cutover local store, pushes it to the "
        "pod, and P5 chmods that same tree `0444`; `--unfreeze` restores the "
        "recorded modes on it. Pointed at `~/.cache/subsystem-store` it would "
        "plan an empty delta (a copy of the pod, against the pod) and then "
        "freeze a directory the next `cairn sync` deletes and recreates. Its "
        "other two matches are not store roots at all: `DEFAULT_BACKUP_"
        "NAMESPACE` is the Kubernetes namespace"
    ),
    "scripts/analyze-service-index/backup.py": (
        "EXEMPT — it backs up the mirror's GIT HISTORY, which exists nowhere "
        "else. Each `<scope>/` is an independent repository and this produces "
        "one `git bundle` per scope; the synced cache is an extracted tar with "
        "no repositories in it, so this cannot be repointed at the cache and "
        "still do its job. ⚠ RECORDED, NOT RESOLVED: the pod is canonical now "
        "and has its own backup CronJob, so this is an archive of a frozen tree "
        "rather than the store's backup, and its docstring still says 'a disk "
        "failure … loses the whole thing permanently'. That sentence is no "
        "longer true. Whether the job should still run is a live question, "
        "deliberately left open here rather than answered by an exemption"
    ),
    "scripts/analyze-service-index/escrow-verify.py": (
        "EXEMPT, transitively — `DEFAULT_STORE = B.DEFAULT_STORE`, re-exported "
        "from `backup.py` so the verifier and the producer cannot disagree "
        "about which tree was backed up. It inherits `backup.py`'s reason and "
        "must not grow a second spelling of the path"
    ),
    "scripts/analyze-service-index/restore-verify.py": (
        "EXEMPT, transitively — same `B.DEFAULT_STORE` re-export, same reason: "
        "a restore verified against a different tree than the one backed up "
        "verifies nothing"
    ),
    "scripts/analyze-service-index/commit.sh": (
        "EXEMPT — the hourly autocommit of the mirror's per-scope git "
        "repositories. Same subject as `backup.py`: it versions the mirror, it "
        "does not read the store to answer anybody. It also refuses an EMPTY "
        "positional rather than silently taking the default, which is the "
        "opposite failure and already guarded by test_repo_path_defaults.py"
    ),
    "scripts/present/measure.py": (
        "⚠ OPEN QUESTION, RECORDED RATHER THAN WAVED THROUGH. `Env.live()` "
        "resolves `~/.claude/analyze-service-index` for the devrc explainer "
        "page's store measurements, and its own provenance string still reads "
        "'read via subsystem_recall.load_store()'. Post-cutover that measures "
        "the FROZEN tree and presents the numbers as the store's — the same "
        "shape as the three regressions above, in a page rather than a recall. "
        "It is not fixed here because the fix is a judgement about what the "
        "page is claiming, not a repoint; it is ledgered so the question cannot "
        "go quiet again"
    ),
    "scripts/subsystem-store-api/server.py": (
        "EXEMPT — POD-SIDE. Its `DEFAULT_STORE` is `/data`, the container mount, "
        "which is neither of the two host locations; the `subsystem-store` "
        "component it also names is the secret mount path. This process IS the "
        "authority the resolver points other hosts at, so routing it through a "
        "host-local resolver would be circular"
    ),
    "scripts/cairn": (
        "EXEMPT, for TWO distinct hits — and an earlier version of this row said "
        "'the one path it sites', which was FALSE THE DAY IT WAS WRITTEN. "
        "(1) `~/.config/subsystem-store/env` is the CREDENTIAL file, not a store "
        "root. (2) `subsystem_touch.DEFAULT_STORE_ROOT`, which `cairn doctor` "
        "reads to check the FROZEN MIRROR is still frozen. That second one is "
        "not open-coding: `subsystem_read_store` deliberately does not name the "
        "mirror (its `refusal_message` docstring says so — the mirror's path "
        "belongs to `subsystem_touch`), so taking the constant IS taking the one "
        "definition. Its actual READ resolution is in `ROUTED` above. 🔴 The "
        "false reason is why `SELF_RESOLVED_KINDS` exists: a row whose PROSE "
        "silently stops describing the file is the ledger failing in the exact "
        "direction it was built to catch"
    ),
}

#: 🔴 THE PER-FILE KIND PIN, AND IT EXISTS BECAUSE A REASON WENT STALE SILENTLY.
#:
#: The file-set ledger above only fails when a file JOINS or LEAVES. `cairn`
#: was already in it, so when this PR added a second resolution to it —
#: `mirror_root=subsystem_touch.DEFAULT_STORE_ROOT` in `cmd_doctor` — nothing
#: moved, and the row went on saying "the one path it sites" while the scan
#: reported two. A ledger whose reasons can drift out of agreement with the code
#: is a rubber stamp, and this repo has a name for that shape.
#:
#: So each file also pins WHICH KINDS of resolution it performs, with line
#: numbers stripped so ordinary edits do not churn it. A file growing a NEW kind
#: fails here even though it is already ledgered.
SELF_RESOLVED_KINDS: dict[str, frozenset[str]] = {
    "scripts/lib/subsystem_read_store.py": frozenset(
        {"name:DEFAULT_CACHE_ROOT", "path:subsystem-store"}),
    "scripts/lib/subsystem_touch.py": frozenset(
        {"name:DEFAULT_STORE_ROOT", "path:analyze-service-index"}),
    "scripts/cairn-cutover.py": frozenset(
        {"name:DEFAULT_STORE", "path:analyze-service-index", "path:subsystem-store"}),
    "scripts/analyze-service-index/backup.py": frozenset(
        {"name:DEFAULT_STORE", "path:analyze-service-index"}),
    "scripts/analyze-service-index/escrow-verify.py": frozenset(
        {"attr:DEFAULT_STORE", "name:DEFAULT_STORE"}),
    "scripts/analyze-service-index/restore-verify.py": frozenset(
        {"attr:DEFAULT_STORE", "name:DEFAULT_STORE"}),
    "scripts/analyze-service-index/commit.sh": frozenset(
        {"shell:${HOME}/.claude/analyze-service-index",
         # the `${POSITIONAL[0]:-…}` default, brace still attached
         "shell:${HOME}/.claude/analyze-service-index}",
         # a self-reference in an error message, not a resolution
         "shell:/analyze-service-index/commit.sh"}),
    "scripts/present/measure.py": frozenset({"path:analyze-service-index"}),
    "scripts/subsystem-store-api/server.py": frozenset(
        {"name:DEFAULT_STORE", "path:subsystem-store",
         "path:/run/secrets/subsystem-store/token"}),
    # 🔴 TWO KINDS. See the row above for why each is allowed.
    "scripts/cairn": frozenset({"attr:DEFAULT_STORE_ROOT", "path:subsystem-store"}),
}


def hit_kinds(hits: list[str]) -> set[str]:
    """`kind:detail`, line number stripped — stable across unrelated edits."""
    return {":".join(h.split(":")[:-1]) for h in hits}


# =============================================================================
# THE SCANNERS
# =============================================================================

#: Directories a filesystem walk must skip to match what `git ls-files` returns.
#: Enumerated, because the fallback tier has no `.gitignore` to consult.
_WALK_SKIP: frozenset[str] = frozenset(
    {"__pycache__", ".git", "node_modules", ".venv", "venv", ".mypy_cache",
     ".pytest_cache", ".ruff_cache", "result"}
)


def _git_is_available() -> bool:
    return (ROOT / ".git").exists()


def tracked_sources() -> list[str]:
    """Repo-relative paths under `scripts/`, minus the out-of-scope directories.

    🔴 TWO TIERS, AND THE SECOND ONE IS WHY THIS FUNCTION EXISTS IN THIS SHAPE.
    The first version ran `git ls-files` with `check=True` and no fallback. The
    authoritative gate — `nix build .#checks.x86_64-linux.pytests` — builds from
    a `cp -r ${./.}` store copy with **no `.git`**, so that call exited 128 out
    of a module-scoped fixture and reddened every test here on the ONE tier the
    merge is gated on, while the dev-host tier stayed green. `claude/RULES.md`
    → "a suite that runs in TWO TIERS must be green in BOTH". The repo had
    already solved this and named the pattern: `testlib/public_ip_scan.py`'s
    `repo_files()`, whose docstring makes the same argument.

    🔴 A `pytest.skip` HERE WOULD BE WORSE THAN THE BUG. It would make both
    ledgers VACUOUS in the sandbox — the tier that gates the merge — so the
    guard would report green over a tree it never enumerated. The fallback keeps
    both tiers looking at the same set; that is the whole point of having one.

    Preferring git is not cosmetic either: the flake ships tracked files only, so
    an untracked reader deploys as an absence, and `claude/RULES.md` records that
    this repo's `grep -r` is `.gitignore`-blind in the other direction.
    """
    if _git_is_available():
        try:
            out = subprocess.run(
                ["git", "-C", str(ROOT), "ls-files", "-z", "scripts"],
                capture_output=True, text=True, check=True, timeout=60,
            ).stdout
            return [
                rel for rel in out.split("\0")
                if rel and not rel.startswith(OUT_OF_SCOPE_PREFIXES)
            ]
        except (OSError, subprocess.SubprocessError):
            pass  # fall through to the walk

    scripts = ROOT / "scripts"
    found: list[str] = []
    for path in scripts.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(ROOT).parts
        if any(part in _WALK_SKIP for part in rel_parts):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(OUT_OF_SCOPE_PREFIXES):
            continue
        found.append(rel)
    return sorted(found)


#: The two-character interpreter prefix, ASSEMBLED FROM CHARACTER CODES rather
#: than written as a literal.
#:
#: 🔴 NOT A STYLE CHOICE, AND NOT AN EXEMPTION. `scripts/tests/
#: test_runtime_shebangs.py` is a repo-wide text scan for tests that write an
#: `/usr/bin/env` shebang at runtime — the sandbox where the merge is gated has
#: no `/usr/bin/env`, so such a test is structurally invisible on the dev-host
#: tier and only ever red on the tier that matters. One of its needles is a QUOTE
#: immediately followed by these two characters, so a source line merely READING
#: a shebang trips it. That guard is right to be a text scan (intent is not
#: greppable) and it must not be weakened to accommodate this file, so this file
#: stops carrying the literal instead. `testlib/shebang_scan.py` assembles its
#: own needles exactly this way, for exactly this reason.
#:
#: 🔴 IT IS ALSO LOAD-BEARING FOR BOTH LEDGERS. `scripts/cairn` has no `.py`
#: extension and is identified as Python by its shebang alone, so a change here
#: that stopped matching would silently drop the store's own client out of the
#: ROUTED and SITED scans — the reassuring-zero shape, one level down. Graded by
#: `TestTheScannersCanActuallySee::test_a_python_file_with_NO_extension_is_
#: still_scanned` and `::test_the_shell_shape_is_caught`, which build their
#: fixtures the same way and assert on the CLASSIFICATION, never on this string.
_HASHBANG = chr(35) + chr(33)


def _shebang(text: str) -> str:
    first = text.splitlines()[0] if text else ""
    return first if first.startswith(_HASHBANG) else ""


def _is_python(path: Path, text: str) -> bool:
    """`.py`, or a shebang naming python — `scripts/cairn` has no extension."""
    return path.suffix == ".py" or "python" in _shebang(text)


def _is_shell(path: Path, text: str) -> bool:
    """`.sh`, or a shebang naming a shell.

    🔴 NOT "everything that is not Python". The first version let every tracked
    file fall through to the shell matcher, and `subsystem-store-api/README.md`
    landed in the ledger for QUOTING an operator command. A document cannot
    resolve a store root at run time, and a ledger row for one is noise that
    makes a real row easier to wave through. The population is CODE.
    """
    shebang = _shebang(text)
    return path.suffix == ".sh" or any(
        sh in shebang for sh in ("bash", "/sh", "zsh", "dash")
    )


#: Tokens in a shell line that could be a path: anchored at `$HOME`, `~` or `/`.
#:
#: 🔴 THE ABSOLUTE ARM WAS MEASURED MISSING. `STORE="/home/<user>/.claude/
#: analyze-service-index"` — no `$HOME`, no `~` — SURVIVED the home-anchored-only
#: pattern, and that is an ordinary way to write the line. Requiring a home
#: anchor made the guard a test of SPELLING rather than of what the path is.
_SHELL_PATH_TOKEN = re.compile(r"(?:\$\{?HOME\}?|~|/)[^\s\"']*")


#: Punctuation a shell expansion leaves clinging to the ENDS of a path segment.
#:
#: 🔴 MEASURED, NOT DEFENSIVE. `commit.sh` writes
#: `STORE="${POSITIONAL[0]:-${HOME}/.claude/analyze-service-index}"`, so the last
#: segment arrives as `analyze-service-index}` and segment EQUALITY missed the
#: very line the ledger was built around. Stripping is from the ENDS only, so
#: `subsystem-store-api` and `subsystem-store-api:1.2.3` are still not segments —
#: which is what keeps this from undoing the noise fix above.
_SEGMENT_PUNCTUATION = "{}\"'()[],;:"


def _names_a_store_root_segment(value: str) -> bool:
    """Is `value` a path one of whose SEGMENTS is a store-root component?

    🔴 SEGMENT EQUALITY, NOT SUBSTRING — and both halves of that were measured.

    Exact whole-STRING equality (the first version) let four ordinary spellings
    through: `os.path.expanduser("~/.claude/analyze-service-index")` and
    `Path.home() / ".claude/analyze-service-index"` both bury the component in a
    longer literal, so a whole-string match never fired.

    Widening to a plain SUBSTRING then over-fired into noise, also measured:
    `subsystem-store-api`, `subsystem-store-backup`, `analyze-service-index-backups`
    and the `subsystem-store-client/1` User-Agent all matched. Those are unit,
    image and product names, not store roots — and a ledger padded with rows
    nobody can justify is how a REAL row gets waved through, which is the
    failure this whole module exists to prevent, one level up.

    A path segment is the actual property: `analyze-service-index` names the
    store, `analyze-service-index-backups` names a bucket. Whitespace still
    disqualifies the whole value, so an English sentence quoting the mirror is
    not a path however it is reworded.
    """
    if not value or any(ch.isspace() for ch in value):
        return False
    return any(
        seg.strip(_SEGMENT_PUNCTUATION) in STORE_ROOT_COMPONENTS
        for seg in value.split("/")
    )


def resolves_a_store_root(path: Path, text: str) -> list[str]:
    """`["<kind>:<detail>:<line>", …]` — every place this file computes one.

    Empty means "this file names no store root", NOT "I could not look": a file
    that fails to parse raises rather than returning `[]`, because a silent
    parse failure is exactly the reassuring zero this whole ledger is about.
    """
    hits: list[str] = []
    if _is_python(path, text):
        tree = ast.parse(text)  # deliberately not caught — see the docstring
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _names_a_store_root_segment(node.value)
            ):
                hits.append(f"path:{node.value}:{node.lineno}")
            elif isinstance(node, ast.Attribute) and node.attr in SITING_NAMES:
                hits.append(f"attr:{node.attr}:{node.lineno}")
            elif isinstance(node, ast.Name) and node.id in SITING_NAMES:
                hits.append(f"name:{node.id}:{node.lineno}")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                # 🔴 THE ALIAS ARM, AND IT IS NOT HYPOTHETICAL. `scripts/cairn`
                # shipped `from subsystem_read_store import DEFAULT_CACHE_ROOT as
                # DEFAULT_CACHE`, and that spelling binds no `Name` and no
                # `Attribute` this walk would see — so `from subsystem_touch
                # import DEFAULT_STORE_ROOT as MIRROR` took the frozen mirror
                # straight past the scan, measured. Key on the name being
                # IMPORTED, never on what it was renamed to.
                for alias in node.names:
                    if alias.name in SITING_NAMES:
                        hits.append(f"import:{alias.name}:{node.lineno}")
        return sorted(set(hits))

    if not _is_shell(path, text):
        return []

    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    for match in _SHELL_PATH_TOKEN.finditer(body):
        token = match.group(0)
        if not _names_a_store_root_segment(token):
            continue
        hits.append(f"shell:{token}:{body[: match.start()].count(chr(10)) + 1}")
    return sorted(set(hits))


def calls_the_resolver(path: Path, text: str) -> list[str]:
    """`["<call>:<line>", …]` — every call to `subsystem_read_store`'s accessors.

    🔴 THE CALL, NOT THE IMPORT. `import subsystem_read_store` routes nothing;
    the three regressions this ledger exists for could each have carried the
    import and still resolved the frozen mirror.
    """
    if not _is_python(path, text):
        return []
    hits: list[str] = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr if isinstance(func, ast.Attribute)
            else func.id if isinstance(func, ast.Name)
            else None
        )
        if name in RESOLVER_CALLS:
            hits.append(f"{name}:{node.lineno}")
    return sorted(set(hits))


def _scan(predicate) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for rel in tracked_sources():
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits = predicate(path, text)
        if hits:
            found[rel] = hits
    return found


@pytest.fixture(scope="module")
def sited() -> dict[str, list[str]]:
    return _scan(resolves_a_store_root)


@pytest.fixture(scope="module")
def routed() -> dict[str, list[str]]:
    return _scan(calls_the_resolver)


# =============================================================================
# The seam: the scanners' vocabulary vs the live paths
# =============================================================================

class TestTheScannerVocabularyMatchesTheRealPaths:
    """🔴 THE HALF THAT KEEPS THE LEDGER FROM GOING BLIND.

    Both scanners are only as wide as `STORE_ROOT_COMPONENTS`. Rename the synced
    cache and every assertion below keeps passing over a scan that now matches
    nothing new — a green ledger over a store it can no longer see. So the
    vocabulary is pinned to the two live constants, in both directions.

    The literals are written out HERE and compared against the modules; neither
    side is read off the other.
    """

    def test_the_cache_root_is_the_component_the_scanner_knows(self) -> None:
        assert rs.DEFAULT_CACHE_ROOT == Path.home() / ".cache" / "subsystem-store"
        assert rs.DEFAULT_CACHE_ROOT.name in STORE_ROOT_COMPONENTS

    def test_the_frozen_mirror_is_the_component_the_scanner_knows(self) -> None:
        assert st.DEFAULT_STORE_ROOT == Path.home() / ".claude" / "analyze-service-index"
        assert st.DEFAULT_STORE_ROOT.name in STORE_ROOT_COMPONENTS

    def test_the_two_roots_are_DIFFERENT_directories(self) -> None:
        """The premise of the whole cutover, and of this file. If these ever
        became one path the ledger would be policing nothing."""
        assert rs.DEFAULT_CACHE_ROOT != st.DEFAULT_STORE_ROOT

    def test_every_component_the_scanner_knows_is_a_LIVE_root(self) -> None:
        """The other direction: no dead vocabulary. A component in the set that
        matches no real store root would widen both scans forever with nothing to
        find, and the noise is how a real row gets waved through."""
        live = {rs.DEFAULT_CACHE_ROOT.name, st.DEFAULT_STORE_ROOT.name}
        assert STORE_ROOT_COMPONENTS == live, (
            f"the scanner's vocabulary {sorted(STORE_ROOT_COMPONENTS)} and the "
            f"live store roots {sorted(live)} disagree"
        )

    def test_the_resolver_exports_the_calls_the_scanner_looks_for(self) -> None:
        """A renamed accessor must red this file, not silently empty `ROUTED`."""
        for name in sorted(RESOLVER_CALLS):
            assert hasattr(rs, name), f"`subsystem_read_store` has no `{name}`"
            assert name in rs.__all__, f"`{name}` is not exported"


# =============================================================================
# The ledgers, two-way
# =============================================================================

class TestTheRoutedLedgerIsTwoWay:
    def test_the_scan_found_routers_at_all(self, routed) -> None:
        """POSITIVE CONTROL. Every assertion below is satisfied by a scan that
        walked nothing — an empty `routed` makes `set(routed) == set(ROUTED)`
        merely a claim that the ledger is also empty."""
        assert len(routed) >= 3, (
            f"the resolver scan found only {sorted(routed)}. It is not looking "
            f"at what it claims to look at."
        )

    def test_the_ledger_is_exactly_the_scan(self, routed) -> None:
        found, expected = set(routed), set(ROUTED)
        assert found == expected, (
            "the set of files routing through `subsystem_read_store` changed.\n"
            f"  NEW routers (add a row saying what they read): {sorted(found - expected)}\n"
            f"  LOST routers 🔴 (a reader STOPPED asking the resolver where to "
            f"read — this is the regression, not a cleanup): {sorted(expected - found)}\n"
            "Ledger:\n  " + "\n  ".join(f"{k} — {v}" for k, v in ROUTED.items())
        )

    @pytest.mark.parametrize("rel", sorted(ROUTED))
    def test_each_routed_file_exists_and_is_ENUMERATED(self, rel: str) -> None:
        """🔴 "ENUMERATED", NOT "TRACKED BY GIT". This used to assert tracked-ness
        and reddened the whole sandbox tier, where there is no `.git`. The claim
        that holds in BOTH tiers is that the file is in the set this module
        actually scans — which is the property the ledger depends on anyway."""
        assert (ROOT / rel).is_file(), f"{rel} is gone"
        assert rel in set(tracked_sources()), f"{rel} is not in the enumerated set"

    @pytest.mark.parametrize("rel", sorted(ROUTED))
    def test_each_routed_file_is_git_tracked_WHERE_GIT_EXISTS(self, rel: str) -> None:
        """The git-only half, kept as its own test rather than folded above.

        The flake ships tracked files only, so an untracked reader deploys as an
        absence — a real property, and one the sandbox tier structurally cannot
        check. Splitting it says which tier establishes which claim instead of
        letting one assertion mean different things in two places.

        🔴 It RETURNS rather than skipping: `run-tests.sh` pins its EXPECTED_SKIPS
        set exactly, so an unpinned skip breaks the gate.
        """
        if not _git_is_available():
            return
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--error-unmatch", "--", rel],
            capture_output=True, text=True,
        )
        assert out.returncode == 0, f"{rel} is not tracked by git\n{out.stderr}"


class TestTheSitedLedgerIsTwoWay:
    def test_the_scan_found_siting_at_all(self, sited) -> None:
        """POSITIVE CONTROL, same argument as above."""
        assert len(sited) >= 5, (
            f"the siting scan found only {sorted(sited)} — it is wired to "
            f"nothing, and a clean ledger over nothing is not a clean ledger."
        )

    def test_the_scan_reaches_BOTH_languages(self, sited) -> None:
        """A Python-only scan would report `commit.sh` clean forever. The shell
        arm has no shared code with the Python arm, so its silence is its own
        unproven claim until something is seen through it."""
        assert any(rel.endswith(".sh") for rel in sited), (
            "the siting scan matched no shell file at all"
        )
        assert any(not rel.endswith(".sh") for rel in sited)

    def test_the_ledger_is_exactly_the_scan(self, sited) -> None:
        found, expected = set(sited), set(SITED)
        assert found == expected, (
            "the set of files computing a subsystem-store root changed.\n"
            f"  NEW siting 🔴 (a fourth open-coded store path — route it through "
            f"`subsystem_read_store`, or add a row here saying why it may not): "
            f"{sorted(found - expected)}\n"
            f"  LOST siting (a row here names a file that no longer does it — "
            f"delete the row): {sorted(expected - found)}\n"
            "Ledger:\n  " + "\n  ".join(f"{k} — {v}" for k, v in SITED.items())
        )

    @pytest.mark.parametrize("rel", sorted(SITED))
    def test_each_sited_file_exists_and_is_ENUMERATED(self, rel: str) -> None:
        """See the routed twin: "enumerated" is the claim that holds in BOTH
        tiers; git-tracked-ness is checked separately where git exists."""
        assert (ROOT / rel).is_file(), f"{rel} is gone"
        assert rel in set(tracked_sources()), f"{rel} is not in the enumerated set"

    @pytest.mark.parametrize("rel", sorted(SITED))
    def test_each_sited_file_is_git_tracked_WHERE_GIT_EXISTS(self, rel: str) -> None:
        if not _git_is_available():
            return
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--error-unmatch", "--", rel],
            capture_output=True, text=True,
        )
        assert out.returncode == 0, f"{rel} is not tracked by git\n{out.stderr}"

    def test_the_KIND_ledger_is_two_way_over_the_same_files(self, sited) -> None:
        """🔴 THE PIN THAT CATCHES A ROW GOING STALE WITHOUT MOVING.

        The set ledger above only fires when a file joins or leaves. `scripts/cairn`
        was already in it when `cairn doctor` added a SECOND resolution to it, so
        nothing moved and the row's prose went on describing one hit while the scan
        saw two. Pinning the kinds makes that a failure.
        """
        assert set(SELF_RESOLVED_KINDS) == set(SITED), (
            "the reason ledger and the kind ledger name different files:\n"
            f"  reasons only: {sorted(set(SITED) - set(SELF_RESOLVED_KINDS))}\n"
            f"  kinds only:   {sorted(set(SELF_RESOLVED_KINDS) - set(SITED))}"
        )
        drifted = {
            rel: (sorted(hit_kinds(hits)), sorted(SELF_RESOLVED_KINDS.get(rel, ())))
            for rel, hits in sited.items()
            if hit_kinds(hits) != set(SELF_RESOLVED_KINDS.get(rel, ()))
        }
        assert not drifted, (
            "a ledgered file's KINDS of store-root resolution changed. A NEW kind "
            "in an already-ledgered file is the shape that slipped past the set "
            "ledger once already — check the row's reason still describes the "
            "file, then update both.\n  "
            + "\n  ".join(f"{rel}\n      scan:   {got}\n      ledger: {want}"
                          for rel, (got, want) in sorted(drifted.items()))
        )

    @pytest.mark.parametrize("rel", sorted(SITED))
    def test_every_exemption_carries_a_REASON(self, rel: str) -> None:
        """🔴 A LEDGER OF EMPTY STRINGS IS NOT A LEDGER. The reason is the whole
        artifact — it is what a reviewer reads instead of re-deriving the
        judgement — so a row is required to say something a person could disagree
        with. 40 characters is roughly one clause; it is a floor on effort, not a
        quality claim, and this docstring says so rather than implying more."""
        assert len(SITED[rel].strip()) >= 40, (
            f"{rel}'s exemption reason is too short to be a reason: {SITED[rel]!r}"
        )

    def test_the_two_ledgers_overlap_only_where_stated(self) -> None:
        """A file in BOTH is doing two different things and must say so.

        TWO are: `scripts/cairn` (routes for `--cache`; separately names the
        credential file and the mirror `doctor` inspects) and
        `subsystem_read_store.py` (it IS the resolver, and it declares the cache
        root every router asks it for). Pinned as a literal so a third arriving
        unexplained is a failure rather than a shrug.

        ⚠ The docstring said "Today exactly one is" while the assertion pinned
        two — a description narrower than its own body, which is the shape this
        module is otherwise built to catch.
        """
        assert set(ROUTED) & set(SITED) == {
            "scripts/cairn",
            "scripts/lib/subsystem_read_store.py",
        }


# =============================================================================
# CONTROLS. The scanners must be shown to fire, and shown not to over-fire.
# =============================================================================

class TestTheScannersCanActuallySee:
    """🔴 A LEDGER IS A REASSURING SET. Until each scanner has been watched to
    fire on a planted case and stay quiet on a benign one, "the ledger matches"
    is a fact about the ledger only."""

    def test_the_three_shipped_regressions_would_ALL_be_caught(self, tmp_path) -> None:
        """🔴 THE REGRESSION PROOF, reconstructed rather than asserted.

        Each fixture is the shape one of the three shipped defects actually had:
        a reader defaulting its store to the frozen mirror. If the scanner cannot
        see these, this file would not have caught any of the three it exists for
        — and that is a claim worth grading, not repeating.
        """
        cases = {
            # subsystem_recall.py's CLI, pre-fix.
            "recall_cli.py":
                'import argparse\n'
                'from pathlib import Path\n'
                'DEFAULT_STORE_ROOT = Path.home() / ".claude" / "analyze-service-index"\n'
                'p = argparse.ArgumentParser()\n'
                'p.add_argument("--store", default=str(DEFAULT_STORE_ROOT))\n',
            # service_recon.py, pre-fix: it took the WRITER's constant instead.
            "recon.py":
                'import subsystem_touch as st\n'
                'def brief(root=None):\n'
                '    return root or st.DEFAULT_STORE_ROOT\n',
            # subsystem-audit.py, pre-fix: its own copy of the constant.
            "audit.py":
                'from pathlib import Path\n'
                'DEFAULT_STORE_ROOT = Path.home() / ".claude" / "analyze-service-index"\n',
        }
        for name, source in cases.items():
            path = tmp_path / name
            path.write_text(source, encoding="utf-8")
            assert resolves_a_store_root(path, source), (
                f"the siting scan did not see {name}, which is the shape of a "
                f"defect that actually shipped"
            )
            assert not calls_the_resolver(path, source), (
                f"{name} routes nothing, yet the resolver scan claims it does"
            )

    @pytest.mark.parametrize(
        "name, source",
        [
            # 1. expanduser with the whole path in ONE literal.
            ("expanduser.py",
             'import os\n'
             'STORE = os.path.expanduser("~/.claude/analyze-service-index")\n'),
            # 2. Path.home() with the tail JOINED into one literal.
            ("joined.py",
             'from pathlib import Path\n'
             'STORE = Path.home() / ".claude/analyze-service-index"\n'),
            # 3. the ledgered constant imported under a DIFFERENT name.
            ("aliased.py",
             'from subsystem_touch import DEFAULT_STORE_ROOT as MIRROR\n'
             'def load():\n    return MIRROR\n'),
            # 4. shell, ABSOLUTE — no $HOME and no ~.
            ("absolute.sh",
             'STORE="/home/someone/.claude/analyze-service-index"\n'),
        ],
        ids=["expanduser", "joined-literal", "aliased-import", "absolute-shell"],
    )
    def test_the_four_MEASURED_survivors_are_now_caught(
        self, tmp_path, name, source
    ) -> None:
        """🔴 EACH OF THESE WAS PLANTED AS A TRACKED FILE AND SURVIVED THE WHOLE
        MODULE, GREEN. They are not imagined mutants — they are the audit's
        measured escapes, kept as fixtures so the widening cannot be undone
        quietly.

        The aliased import is the one that matters most: `scripts/cairn` really
        did ship `from subsystem_read_store import DEFAULT_CACHE_ROOT as
        DEFAULT_CACHE`, so this spelling is what the codebase reaches for. It
        binds no `Name` and no `Attribute` the walk would see.
        """
        path = tmp_path / name
        path.write_text(source, encoding="utf-8")
        assert resolves_a_store_root(path, source), (
            f"{name} still escapes the scan"
        )

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("analyze-service-index", True),
            ("~/.claude/analyze-service-index", True),
            (".claude/analyze-service-index", True),
            ("/home/someone/.claude/analyze-service-index", True),
            ("subsystem-store", True),
            ("/run/secrets/subsystem-store/token", True),
            # …and the near-misses the SUBSTRING version wrongly claimed.
            ("analyze-service-index-backups", False),
            ("analyze-service-index-backup", False),
            ("subsystem-store-api", False),
            ("subsystem-store-client/1", False),
            ("subsystem-store-backup", False),
            ("harbor.example.lan/library/subsystem-store-api:1.2.3", False),
            # Shell punctuation clinging to the segment — the live `commit.sh`
            # shape, which segment EQUALITY alone missed.
            ("${POSITIONAL[0]:-${HOME}/.claude/analyze-service-index}", True),
            # Prose naming the mirror is not a path, however it is worded.
            ("the store at ~/.claude/analyze-service-index is frozen", False),
        ],
    )
    def test_the_segment_rule_separates_a_store_root_from_a_PRODUCT_NAME(
        self, value, expected
    ) -> None:
        """🔴 THE ISOLATED PIN ON THE PREDICATE ITSELF.

        Values are pairwise distinct and every FALSE case shares a prefix with a
        TRUE one, so a mutant that drops the segment rule back to a substring
        match cannot pass: it flips six of these at once. Written as literals
        here, never derived from `STORE_ROOT_COMPONENTS`.
        """
        assert _names_a_store_root_segment(value) is expected

    def test_the_transitive_shape_is_caught(self, tmp_path) -> None:
        """`DEFAULT_STORE = B.DEFAULT_STORE` spells no path at all. A component
        scan alone reports it clean, and two shipped files have this shape."""
        source = "import backup as B\nDEFAULT_STORE = B.DEFAULT_STORE\n"
        path = tmp_path / "verify.py"
        path.write_text(source, encoding="utf-8")
        assert resolves_a_store_root(path, source)

    def test_a_python_file_with_NO_extension_is_still_scanned(self, tmp_path) -> None:
        """`scripts/cairn` has no `.py`. A scanner keyed on the suffix alone
        would skip the client that owns the cache.

        🔴 THE FIXTURE'S SHEBANG IS ASSEMBLED, NOT WRITTEN — see `_HASHBANG`.
        The interpreter line still lands on disk verbatim, because that is the
        input `_is_python` has to classify; what changes is that this SOURCE FILE
        no longer carries the literal `test_runtime_shebangs.py` scans for.
        `testlib.mockbin.write_exec` is the sanctioned answer for a stub a test
        EXECS, and it is the wrong tool here twice over: it owns the shebang and
        writes `/bin/sh`, which would make `_is_python` return False and delete
        this test's whole point — and nothing execs this file. It is read.
        """
        source = (
            _HASHBANG + "/usr/bin/env python3\n"
            'from pathlib import Path\n'
            'X = Path.home() / ".cache" / "subsystem-store"\n'
        )
        path = tmp_path / "cairn"
        path.write_text(source, encoding="utf-8")
        assert _is_python(path, source), "the no-extension file was not read as Python"
        assert resolves_a_store_root(path, source)

    def test_a_SHELL_file_with_no_extension_is_classified_by_its_shebang_too(
        self, tmp_path
    ) -> None:
        """The mirror of the test above, and the other half `_shebang` decides.

        Both language arms read the SAME two characters, so a change to
        `_HASHBANG` that stopped matching would silently empty the shell arm as
        well — and its own control (`test_the_scan_reaches_BOTH_languages`) is a
        claim about the live tree, where every shell file happens to end `.sh`.
        This is the extensionless case that arm has no live example of.
        """
        source = (
            _HASHBANG + "/usr/bin/env bash\n"
            'STORE="${HOME}/.claude/analyze-service-index"\n'
        )
        path = tmp_path / "commit-hook"
        path.write_text(source, encoding="utf-8")
        assert _is_shell(path, source), "the no-extension file was not read as shell"
        assert not _is_python(path, source)
        assert resolves_a_store_root(path, source)

    def test_the_shell_shape_is_caught(self, tmp_path) -> None:
        source = 'STORE="${POSITIONAL[0]:-${HOME}/.claude/analyze-service-index}"\n'
        path = tmp_path / "commit.sh"
        path.write_text(source, encoding="utf-8")
        assert resolves_a_store_root(path, source)

    def test_the_resolver_scan_fires_on_BOTH_spellings(self, tmp_path) -> None:
        for source in (
            "import subsystem_read_store as _rs\nX = _rs.read_store_root()\n",
            "from subsystem_read_store import resolve_read_store\nX = resolve_read_store(None)\n",
        ):
            path = tmp_path / "r.py"
            path.write_text(source, encoding="utf-8")
            assert calls_the_resolver(path, source), source

    def test_an_IMPORT_alone_does_not_count_as_routing(self, tmp_path) -> None:
        """NEGATIVE CONTROL, and the one that matters most: each of the three
        regressions could have carried the import while resolving the mirror."""
        source = "import subsystem_read_store as _rs  # noqa: F401\n"
        path = tmp_path / "r.py"
        path.write_text(source, encoding="utf-8")
        assert not calls_the_resolver(path, source)

    def test_prose_about_the_mirror_is_NOT_siting(self, tmp_path) -> None:
        """NEGATIVE CONTROL. `subsystem_read_store`'s own docstring names the
        frozen mirror, `subsystem-audit.py` carries a comment recording the
        constant it deleted, and `host_identity.py` describes the layout. A
        scanner that counted prose would bury the real rows."""
        source = (
            '"""The store at ~/.claude/analyze-service-index is FROZEN.\n\n'
            'It used to be `Path.home() / ".claude" / "analyze-service-index"`.\n"""\n'
            "# DEFAULT_STORE_ROOT used to be declared here.\n"
            "VALUE = 1\n"
        )
        path = tmp_path / "prose.py"
        path.write_text(source, encoding="utf-8")
        assert not resolves_a_store_root(path, source), (
            "a docstring and a comment were counted as siting a store root"
        )

    def test_a_REQUIRED_store_argument_is_NOT_siting(self, tmp_path) -> None:
        """NEGATIVE CONTROL. `seed.sh` and `verify-byte-identity.sh` demand
        `--store` and default to nothing; they resolve no root and must stay out
        of the ledger, or the ledger stops being about defaults."""
        source = 'STORE=""\n[[ -n "$STORE" ]] || { echo "--store is required" >&2; exit 2; }\n'
        path = tmp_path / "seed.sh"
        path.write_text(source, encoding="utf-8")
        assert not resolves_a_store_root(path, source)

    def test_a_MARKDOWN_file_quoting_the_path_is_not_siting(self, tmp_path) -> None:
        """NEGATIVE CONTROL, and a measured one: `subsystem-store-api/README.md`
        quotes `--store ~/.claude/analyze-service-index` in an operator recipe
        and landed in this ledger before the language check existed. Prose cannot
        resolve a path at run time, and a spurious row is what makes a real row
        easy to wave through."""
        source = "Run:\n\n    seed.sh --store ~/.claude/analyze-service-index\n"
        path = tmp_path / "README.md"
        path.write_text(source, encoding="utf-8")
        assert not resolves_a_store_root(path, source)

    def test_a_shell_COMMENT_naming_the_mirror_is_not_siting(self, tmp_path) -> None:
        source = "# the store lives at ${HOME}/.claude/analyze-service-index\nSTORE=\"$1\"\n"
        path = tmp_path / "c.sh"
        path.write_text(source, encoding="utf-8")
        assert not resolves_a_store_root(path, source)

    def test_an_unparseable_python_file_RAISES_rather_than_reading_clean(
        self, tmp_path
    ) -> None:
        """🔴 A parse failure swallowed into `[]` is a file reported clean by a
        scan that never looked at it — the silent zero, arriving through the one
        door a `try/except` would open."""
        source = "def broken(:\n"
        path = tmp_path / "b.py"
        path.write_text(source, encoding="utf-8")
        with pytest.raises(SyntaxError):
            resolves_a_store_root(path, source)


class TestTheEnumeratorItself:
    def test_it_lists_real_tracked_files(self) -> None:
        files = tracked_sources()
        assert len(files) > 100, f"only {len(files)} tracked files under scripts/"
        assert "scripts/cairn" in files
        assert "scripts/lib/subsystem_read_store.py" in files

    def test_the_out_of_scope_directories_really_are_excluded(self) -> None:
        """The exclusion is load-bearing and enumerated, so it is graded: a test
        file that hardcodes the mirror must not appear, and one that DOES exist
        must be findable outside the enumerator — otherwise this asserts nothing.

        🔴 The "does exist" half is counted off the FILESYSTEM, not off a second
        `git ls-files`. The earlier version shelled git with `check=True` here
        too, which is the same 128 that reddened the sandbox tier one function up.
        """
        files = tracked_sources()
        assert not [f for f in files if f.startswith(OUT_OF_SCOPE_PREFIXES)]
        every = [
            p for p in (ROOT / "scripts" / "tests").rglob("*.py")
            if "__pycache__" not in p.parts
        ]
        assert len(every) > 50, (
            "there is no `scripts/tests/` content, so excluding it excluded "
            "nothing and this control proves nothing"
        )

    def test_the_enumerator_WORKS_WITH_NO_GIT_DIRECTORY(self, tmp_path) -> None:
        """🔴 THE SANDBOX TIER, SIMULATED — the one that gates the merge.

        `nix build .#checks.x86_64-linux.pytests` builds from a `cp -r ${./.}`
        store copy with NO `.git`. The first version of `tracked_sources()` ran
        `git ls-files` with `check=True` and no fallback, so it exited 128 out of
        a module-scoped fixture and reddened EVERY test in this file there, while
        the dev-host tier stayed green — `claude/RULES.md` → "a suite that runs in
        TWO TIERS must be green in BOTH".

        🔴 THIS DOES NOT SKIP, AND IT MUST NOT. A `pytest.skip` when git is absent
        would make both ledgers vacuous in exactly the tier the merge is gated on,
        which is strictly worse than the crash: a crash is loud.

        It repoints the module's `ROOT` at a git-less copy of `scripts/lib`, so
        the fallback is exercised for real rather than asserted about.
        """
        fake = tmp_path / "repo"
        (fake / "scripts" / "lib").mkdir(parents=True)
        (fake / "scripts" / "lib" / "reader.py").write_text(
            'from pathlib import Path\nS = Path.home() / ".claude" / "analyze-service-index"\n',
            encoding="utf-8",
        )
        (fake / "scripts" / "tests").mkdir()
        (fake / "scripts" / "tests" / "test_x.py").write_text("x = 1\n", encoding="utf-8")
        (fake / "scripts" / "lib" / "__pycache__").mkdir()
        (fake / "scripts" / "lib" / "__pycache__" / "junk.pyc").write_bytes(b"\x00")
        assert not (fake / ".git").exists()

        module = sys.modules[__name__]
        original = module.ROOT
        try:
            module.ROOT = fake
            listed = tracked_sources()
        finally:
            module.ROOT = original

        assert listed == ["scripts/lib/reader.py"], listed
        # …and the ledger's scanner still works over what the fallback returned.
        src = (fake / "scripts" / "lib" / "reader.py")
        assert resolves_a_store_root(src, src.read_text(encoding="utf-8"))

    def test_the_no_git_fallback_is_NOT_BLIND_to_anything_the_git_tier_sees(
        self,
    ) -> None:
        """🔴 THE LOAD-BEARING DIRECTION: the sandbox tier must not see LESS.

        If the walk misses a file git tracks, the ledger in the tier that gates
        the merge is enumerating a smaller population than the one anybody
        reviewed — a guard that is a different guard per tier.

        ⚠ THE REVERSE DIRECTION IS DELIBERATELY NOT A SET EQUALITY, and an
        earlier version made it one. That version failed the moment ANY untracked
        file sat under `scripts/` — which my own mutation harness caused by
        snapshotting `<file>.orig` beside the original, so the NEGATIVE CONTROL
        came back KILLED and the sweep was, for one round, measuring itself. A
        stray scratch file is not a defect in this ledger. What WOULD be is an
        untracked file that resolves a store root, since it changes the sandbox
        tier's answer while being invisible to the git tier — so that is the
        property asserted, instead of tidiness.
        """
        if not _git_is_available():
            return
        from_git = set(tracked_sources())

        module = sys.modules[__name__]
        original = module._git_is_available
        try:
            module._git_is_available = lambda: False
            from_walk = set(tracked_sources())
        finally:
            module._git_is_available = original

        assert from_git, "the git tier enumerated nothing"
        assert from_walk, "the walk tier enumerated nothing"
        only_git = sorted(from_git - from_walk)
        assert not only_git, (
            "the walk is BLIND to files git tracks, so the sandbox tier would "
            f"enumerate a smaller population than the dev-host tier: {only_git[:10]}"
        )

        offenders = []
        for rel in sorted(from_walk - from_git):
            path = ROOT / rel
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            try:
                if resolves_a_store_root(path, text):
                    offenders.append(rel)
            except SyntaxError:
                continue  # an unparseable scratch file is not this guard's business
        assert not offenders, (
            "an UNTRACKED file under scripts/ resolves a store root. The sandbox "
            "tier would enumerate it and the git tier would not, so the two "
            f"ledgers disagree: {offenders}"
        )
