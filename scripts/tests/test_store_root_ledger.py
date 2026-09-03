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

⚠ STATED RESIDUALS, so nobody reads this as wider than it is:
  * `scripts/tests/` and `scripts/testlib/` are OUT OF SCOPE. A test that
    hardcodes the mirror path is building a fixture, not resolving a store for a
    caller, and sweeping them in would bury the production population under ~14
    rows of fixtures. A reader hidden in a test directory is therefore invisible
    to this guard.
  * The shell scanner is a comment-stripped TEXT match. It sees
    `${HOME}/.claude/analyze-service-index`; it would not see a path assembled
    from two variables.
  * The Python scanner reads the AST, so a path assembled at run time out of
    `os.environ` or `str.join` is invisible to it. It sees the shapes that have
    actually shipped: a string COMPONENT in a path expression, and an attribute
    or name reference to another module's store constant.
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
        "EXEMPT for the one path it sites: `~/.config/subsystem-store/env` is "
        "the CREDENTIAL file, not a store root. Its actual store resolution is "
        "in `ROUTED` above, and both facts have to be true of this file"
    ),
}


# =============================================================================
# THE SCANNERS
# =============================================================================

def tracked_sources() -> list[str]:
    """Repo-relative paths under `scripts/`, minus the out-of-scope directories.

    🔴 `git ls-files`, NOT `rglob`. The flake ships tracked files only, so an
    untracked reader deploys as an absence — but a `rglob` sweep would also drag
    in `__pycache__`, virtualenvs and build output, and `claude/RULES.md` records
    that this repo's `grep -r` is `.gitignore`-blind in the opposite direction.
    Listing the index is the one answer that matches what ships.
    """
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "scripts"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [
        rel for rel in out.split("\0")
        if rel and not rel.startswith(OUT_OF_SCOPE_PREFIXES)
    ]


def _shebang(text: str) -> str:
    first = text.splitlines()[0] if text else ""
    return first if first.startswith("#!") else ""


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


#: Shell siting: a home anchor, then a path ending in a store-root component.
_SHELL_SITING = re.compile(
    r"(?:\$\{?HOME\}?|~)/[^\s\"']*(?:" + "|".join(sorted(STORE_ROOT_COMPONENTS)) + r")"
)


def sites_a_store_root(path: Path, text: str) -> list[str]:
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
                and node.value in STORE_ROOT_COMPONENTS
            ):
                # 🔴 EXACT EQUALITY, NOT `in`. A path COMPONENT is a whole string
                # in a `/` expression; requiring the constant to BE the component
                # is what keeps every docstring and prose mention of the mirror
                # out of this scan, without a comment stripper that would have to
                # understand Python string syntax to be right.
                hits.append(f"component:{node.value}:{node.lineno}")
            elif isinstance(node, ast.Attribute) and node.attr in SITING_NAMES:
                hits.append(f"attr:{node.attr}:{node.lineno}")
            elif isinstance(node, ast.Name) and node.id in SITING_NAMES:
                hits.append(f"name:{node.id}:{node.lineno}")
        return sorted(set(hits))

    if not _is_shell(path, text):
        return []

    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    for match in _SHELL_SITING.finditer(body):
        hits.append(f"shell:{match.group(0)}:{body[: match.start()].count(chr(10)) + 1}")
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
    return _scan(sites_a_store_root)


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
    def test_each_routed_file_exists_and_is_tracked(self, rel: str) -> None:
        assert (ROOT / rel).is_file(), f"{rel} is gone"
        assert rel in set(tracked_sources()), f"{rel} is not tracked by git"


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
    def test_each_sited_file_exists_and_is_tracked(self, rel: str) -> None:
        assert (ROOT / rel).is_file(), f"{rel} is gone"
        assert rel in set(tracked_sources()), f"{rel} is not tracked by git"

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

    def test_the_two_ledgers_overlap_only_where_stated(self, sited, routed) -> None:
        """A file in BOTH is doing two different things and must say so. Today
        exactly one is: `scripts/cairn` routes for `--cache` and separately names
        the credential file. Pinned as a literal so a second overlap arriving
        unexplained is a failure rather than a shrug."""
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
            assert sites_a_store_root(path, source), (
                f"the siting scan did not see {name}, which is the shape of a "
                f"defect that actually shipped"
            )
            assert not calls_the_resolver(path, source), (
                f"{name} routes nothing, yet the resolver scan claims it does"
            )

    def test_the_transitive_shape_is_caught(self, tmp_path) -> None:
        """`DEFAULT_STORE = B.DEFAULT_STORE` spells no path at all. A component
        scan alone reports it clean, and two shipped files have this shape."""
        source = "import backup as B\nDEFAULT_STORE = B.DEFAULT_STORE\n"
        path = tmp_path / "verify.py"
        path.write_text(source, encoding="utf-8")
        assert sites_a_store_root(path, source)

    def test_a_python_file_with_NO_extension_is_still_scanned(self, tmp_path) -> None:
        """`scripts/cairn` has no `.py`. A scanner keyed on the suffix alone
        would skip the client that owns the cache."""
        source = '#!/usr/bin/env python3\nfrom pathlib import Path\nX = Path.home() / ".cache" / "subsystem-store"\n'
        path = tmp_path / "cairn"
        path.write_text(source, encoding="utf-8")
        assert sites_a_store_root(path, source)

    def test_the_shell_shape_is_caught(self, tmp_path) -> None:
        source = 'STORE="${POSITIONAL[0]:-${HOME}/.claude/analyze-service-index}"\n'
        path = tmp_path / "commit.sh"
        path.write_text(source, encoding="utf-8")
        assert sites_a_store_root(path, source)

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
        assert not sites_a_store_root(path, source), (
            "a docstring and a comment were counted as siting a store root"
        )

    def test_a_REQUIRED_store_argument_is_NOT_siting(self, tmp_path) -> None:
        """NEGATIVE CONTROL. `seed.sh` and `verify-byte-identity.sh` demand
        `--store` and default to nothing; they resolve no root and must stay out
        of the ledger, or the ledger stops being about defaults."""
        source = 'STORE=""\n[[ -n "$STORE" ]] || { echo "--store is required" >&2; exit 2; }\n'
        path = tmp_path / "seed.sh"
        path.write_text(source, encoding="utf-8")
        assert not sites_a_store_root(path, source)

    def test_a_MARKDOWN_file_quoting_the_path_is_not_siting(self, tmp_path) -> None:
        """NEGATIVE CONTROL, and a measured one: `subsystem-store-api/README.md`
        quotes `--store ~/.claude/analyze-service-index` in an operator recipe
        and landed in this ledger before the language check existed. Prose cannot
        resolve a path at run time, and a spurious row is what makes a real row
        easy to wave through."""
        source = "Run:\n\n    seed.sh --store ~/.claude/analyze-service-index\n"
        path = tmp_path / "README.md"
        path.write_text(source, encoding="utf-8")
        assert not sites_a_store_root(path, source)

    def test_a_shell_COMMENT_naming_the_mirror_is_not_siting(self, tmp_path) -> None:
        source = "# the store lives at ${HOME}/.claude/analyze-service-index\nSTORE=\"$1\"\n"
        path = tmp_path / "c.sh"
        path.write_text(source, encoding="utf-8")
        assert not sites_a_store_root(path, source)

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
            sites_a_store_root(path, source)


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
        """
        files = tracked_sources()
        assert not [f for f in files if f.startswith(OUT_OF_SCOPE_PREFIXES)]
        every = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "scripts/tests"],
            capture_output=True, text=True, check=True,
        ).stdout.split("\0")
        assert len([f for f in every if f]) > 50, (
            "there is no `scripts/tests/` content, so excluding it excluded "
            "nothing and this control proves nothing"
        )
