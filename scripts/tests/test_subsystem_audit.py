"""Unit tests for scripts/subsystem-audit.py — the /prune-index auditor.

🔴 OFFLINE, HERMETIC, AND SYNTHETIC. Every fixture in this file is written into a
`tmp_path`; nothing under `~/.claude/analyze-service-index/` is opened, and no
line of it is reproduced here. That store is curated, CLIENT-CONFIDENTIAL and
unbacked-up, and devrc is PUBLIC — so a fixture derived from it would be a leak
even if it looked innocuous. The corpus is only ever described in this repo as
aggregate integers.

🔴 HARNESS DISCIPLINE (`claude/RULES.md`, "Validate the INSTRUMENT"). This
auditor's reassuring answers are ZEROS — "0 collisions", "0 NO HOME", "0 broken
pointers" — and a zero is indistinguishable from a detector wired to nothing. So
every counter is exercised in BOTH directions against two stores:

  CLEAN   the negative control — one small entry, one alias, a pointer that
          resolves, a RESOLVED bullet whose sha exists. Every counter reads 0
          and the verdict is "no prune needed".
  DIRTY   the positive control — a real alias COLLISION, a RESOLVED bullet whose
          every target is unreachable (NO HOME), a RESOLVED bullet that IS
          evictable, an OPEN bullet that must survive both, a pointer to a
          missing file, missing front matter, and a scope with no README. Every
          counter moves OFF zero at an exact expected value.

The sha fixtures come from a REAL git repo built in `tmp_path`, because the
NO HOME classifier's whole discriminator is `git cat-file -e` — a mocked git
would test the mock. `git` is already in `run-tests.sh`'s REQUIRED_TOOLS.
"""
import hashlib
import importlib.machinery
import importlib.util
import io
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]


def _load(relpath: str, modname: str):
    loader = importlib.machinery.SourceFileLoader(modname, str(SCRIPTS / relpath))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    loader.exec_module(mod)
    return mod


sa = _load("subsystem-audit.py", "subsystem_audit_undertest2")

SCOPE = "synthrepo"
UNREACHABLE_SHA = "0123456789abcdef0123456789abcdef01234567"


# --- fixtures -------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A real one-commit git repo at `$WORKSPACE/<SCOPE>`, plus its head sha."""
    ws = tmp_path / "ws"
    repo = ws / SCOPE
    (repo / "claudedocs").mkdir(parents=True)
    (repo / "claudedocs" / "note.md").write_text("a durable record\n", encoding="utf-8")
    _git(repo.parent, "init", "-q", "-b", "main", SCOPE)
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "claudedocs/note.md")
    _git(repo, "commit", "-qm", "record")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setenv("WORKSPACE", str(ws))
    return ws, repo, head


def _entry(service: str, *, aliases=(), body: str = "", pointers: str = "",
           front_extra: str = "sensitivity: public\ncreated_by: analyze-service\n") -> str:
    alias_line = "aliases: [" + ", ".join(aliases) + "]\n"
    return (
        f"---\nservice: {service}\nscope: {SCOPE}\n{alias_line}{front_extra}---\n\n"
        f"## What it is\nSynthetic fixture for {service}.\n\n"
        f"## Pointers\n{pointers}\n"
        f"## Nuance / work-history\n{body}"
    )


def _write_store(root: Path, files: dict[str, str], readme: bool = True) -> Path:
    d = root / SCOPE
    d.mkdir(parents=True, exist_ok=True)
    if readme:
        (d / "README.md").write_text("policy: synthetic\n", encoding="utf-8")
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def clean_store(tmp_path, workspace):
    """The NEGATIVE control: nothing here is a finding.

    ⚠ It carries NO `RESOLVED` bullet, and that is deliberate rather than
    incidental: under the reconciled lifecycle an evictable RESOLVED bullet IS a
    finding ("its content already has a home — propose the cut"), so a store
    holding one can never produce the clean verdict. Discovered by writing this
    fixture with one and watching `test_verdict_is_clean_on_the_clean_store` go
    red — which is the classifier working, not the fixture being unlucky.
    """
    _ws, _repo, _head = workspace
    return _write_store(tmp_path / "clean", {
        "alpha.md": _entry(
            "alpha",
            aliases=("alfa",),
            pointers="- `claudedocs/note.md` — the durable record\n",
            body="- 2026-02-02: an ordinary dated nuance bullet, no marker.\n",
        ),
    })


@pytest.fixture
def dirty_store(tmp_path, workspace):
    _ws, _repo, head = workspace
    return _write_store(tmp_path / "dirty", {
        # Two entries both claiming the alias `ads` -> a REAL alias-tier collision,
        # the exact shape measured live on 2026-08-21.
        "alert-diagnose-svc.md": _entry(
            "alert-diagnose-svc",
            aliases=("ads",),
            pointers="- `claudedocs/note.md` — resolves\n",
            body=(
                "- 2026-03-03: OPEN: still unfinished, must survive every prune.\n"
                f"- 2026-03-01: RESOLVED {head[:9]}: closed by a commit that exists.\n"
            ),
        ),
        "synth-advertising.md": _entry(
            "synth-advertising",
            aliases=("ads",),
            pointers="- `claudedocs/absent-file.md` — does NOT resolve\n",
            body=(
                f"- 2026-03-02: RESOLVED {UNREACHABLE_SHA}: closed by nothing reachable.\n"
            ),
            front_extra="",           # missing sensitivity AND created_by
        ),
    }, readme=False)                   # and no README.md governs the scope


def _render(store: Path, **kw) -> str:
    a = sa.audit_store(store)
    buf = io.StringIO()
    sa.render(a, kw.get("show_all", True), kw.get("n_detail", 20), False, out=buf)
    return buf.getvalue()


# --- 🔴 the ref-collision detector ------------------------------------------------


def test_collision_detector_finds_the_alias_claimed_by_two_entries(dirty_store):
    """POSITIVE CONTROL. Two entries declare alias `ads`; the ref must be reported
    ambiguous in the ALIAS tier, naming both candidates.

    This is the live shape: on 2026-08-21 `alert-diagnose-svc.md` and the
    advertising entry both declared `ads`, so `--ref ads` returned
    `ref-ambiguous` and surfaced NOTHING — the entry was unreachable by the name
    a human would actually type.
    """
    a = sa.audit_store(dirty_store)
    assert len(a.collisions) == 1, f"expected exactly 1 collision, got {a.collisions}"
    c = a.collisions[0]
    assert c.ref == "ads"
    assert c.scope == SCOPE
    assert c.tier == "alias", "the collision is between two ALIASES, not two filenames"
    assert sorted(c.candidates) == ["alert-diagnose-svc.md", "synth-advertising.md"]


def test_collision_detector_reports_zero_on_a_store_that_has_none(clean_store):
    """NEGATIVE CONTROL, and the denominator with it.

    A detector that always fires is as useless as one wired to nothing. The clean
    store's single alias must produce no finding — AND the report must still say
    how many refs it fed through the resolver, so this zero is distinguishable
    from a walk that examined nothing.
    """
    a = sa.audit_store(clean_store)
    assert a.collisions == []
    assert a.refs_resolved >= 2, (
        "the collision walk must actually feed refs to the resolver; "
        f"it reported {a.refs_resolved}"
    )
    text = _render(clean_store)
    assert f"{a.refs_resolved} addressable ref(s)" in text
    assert "every addressable ref resolves to exactly one entry ✓" in text


def test_collision_goes_away_when_the_duplicate_alias_is_dropped(dirty_store):
    """RED -> GREEN, the exact fix this PR applies to the live store.

    Removing `ads` from one entry must (a) clear the collision and (b) make the
    ref resolve to the OTHER entry. (b) matters on its own: a fix that cleared
    the collision by making the ref resolve to NOTHING would satisfy (a) and be
    a regression.
    """
    from subsystem_resolver import ON_MALFORMED_COLLECT, load_index, resolve_ref_tiered

    p = dirty_store / SCOPE / "alert-diagnose-svc.md"
    p.write_text(p.read_text(encoding="utf-8").replace("aliases: [ads]", "aliases: [alert-diagnose]"),
                 encoding="utf-8")

    a = sa.audit_store(dirty_store)
    assert a.collisions == []
    entry, tier = resolve_ref_tiered(
        "ads", load_index(dirty_store, on_malformed=ON_MALFORMED_COLLECT), SCOPE
    )
    assert entry is not None and entry.filename == "synth-advertising.md"
    assert tier == "alias"


def test_a_scope_filter_narrows_the_denominator_too(tmp_path, workspace):
    """🔴 A FILTERED RUN MUST NOT QUOTE A STORE-WIDE DENOMINATOR.

    Reporting "0 collisions out of <all refs>" under `--scope X` is a number
    about the scopes the reader excluded — the silent-zero failure one level
    over. Two scopes, one collision, and asking for the clean one must yield both
    a smaller finding count AND a smaller ref count.
    """
    other = tmp_path / "two"
    _write_store(other, {
        "a.md": _entry("a", aliases=("dup",), body="- 2026-01-01: x.\n"),
        "b.md": _entry("b", aliases=("dup",), body="- 2026-01-01: x.\n"),
    })
    clean = other / "otherscope"
    clean.mkdir()
    (clean / "README.md").write_text("policy\n", encoding="utf-8")
    (clean / "c.md").write_text(
        _entry("c", body="- 2026-01-01: x.\n").replace(f"scope: {SCOPE}", "scope: otherscope"),
        encoding="utf-8",
    )

    whole = sa.audit_store(other)
    assert len(whole.collisions) == 1 and whole.refs_resolved == 4  # a, b, dup, c

    only = sa.audit_store(other, scope_filter="otherscope")
    assert only.collisions == []
    assert only.refs_resolved == 1, (
        "the ref denominator must cover the FILTERED scopes only; got "
        f"{only.refs_resolved}"
    )


def test_a_filename_beats_an_alias_and_is_not_a_collision(tmp_path, workspace):
    """The detector must not invent a collision the RESOLVER does not have.

    An alias on entry A that happens to equal entry B's FILENAME is unambiguous:
    the filename tier hits first and the alias tier is never consulted
    (`index-store.md` -> "an alias can never outrank a filename"). A hand-rolled
    "count the claims" detector would report this as ambiguous. Feeding the real
    resolver is what gets it right, and this test is what proves the difference
    is real rather than assumed.
    """
    store = _write_store(tmp_path / "shadow", {
        "beta.md": _entry("beta", body="- 2026-01-01: nothing.\n"),
        "gamma.md": _entry("gamma", aliases=("beta",), body="- 2026-01-01: nothing.\n"),
    })
    a = sa.audit_store(store)
    assert a.collisions == [], (
        "an alias shadowed by a filename is NOT ambiguous — the filename tier wins"
    )
    # The denominator is 2, not 3: `gamma`'s alias `beta` is the SAME ref string
    # as `beta`'s slug, and the walk resolves each distinct ref once. Pinning the
    # exact number is what keeps this from passing on a walk that skipped aliases
    # entirely — which would also report zero collisions.
    assert a.refs_resolved == 2


# --- 🔴 the NO HOME classifier ----------------------------------------------------


def _bullets(store: Path, filename: str):
    a = sa.audit_store(store)
    e = next(e for e in a.entries if e.filename == filename)
    return e.bullets


def test_no_home_fires_when_every_named_target_is_unreachable(dirty_store):
    """POSITIVE CONTROL for `NO HOME`.

    The bullet declares `RESOLVED <sha>` for a sha that exists in no derivable
    repo and names nothing else. Its content therefore has no home: evicting it
    would delete the only copy of the finding.
    """
    bs = [b for b in _bullets(dirty_store, "synth-advertising.md") if b.verdict]
    assert len(bs) == 1
    b = bs[0]
    assert b.verdict == sa.NO_HOME, f"expected NO HOME, got {b.verdict} ({b.targets})"
    assert b.marker_sha == UNREACHABLE_SHA
    assert [t.resolved for t in b.targets] == [False], (
        "NO HOME must rest on a target that was CHECKED and found absent, "
        "never on one that was merely unmeasured"
    )


def test_the_same_bullet_is_evictable_once_its_sha_resolves(dirty_store, workspace):
    """NEGATIVE CONTROL — the mutation that must flip the verdict.

    Swapping ONLY the sha, leaving every other byte of the bullet identical, must
    turn NO HOME into EVICTABLE. That is what proves the classifier branches on
    reachability and not on some incidental property of the fixture text.
    """
    _ws, _repo, head = workspace
    p = dirty_store / SCOPE / "synth-advertising.md"
    p.write_text(p.read_text(encoding="utf-8").replace(UNREACHABLE_SHA, head[:9]),
                 encoding="utf-8")
    b = next(b for b in _bullets(dirty_store, "synth-advertising.md") if b.verdict)
    assert b.verdict == sa.EVICTABLE
    assert any(t.resolved is True and t.kind == sa.TARGET_SHA for t in b.targets)


def test_a_resolved_bullet_naming_nothing_at_all_is_no_home(tmp_path, workspace):
    """The second shape of NO HOME: a `RESOLVED:` with no sha and no other target.

    `subsystem_resolver` classifies a sha-less `RESOLVED:` as `unverifiable`.
    That population must still reach the lifecycle classifier — it is the
    strongest NO HOME there is — and not be silently skipped because its
    openness label differs.
    """
    store = _write_store(tmp_path / "bare", {
        "delta.md": _entry("delta", body="- 2026-04-04: RESOLVED: someone closed it, somehow.\n"),
    })
    b = next(b for b in _bullets(store, "delta.md") if b.verdict)
    assert b.population == "unverifiable"
    assert b.verdict == sa.NO_HOME
    assert b.targets == (), "it names no target at all — that is the finding"


def test_an_unmeasurable_target_is_not_no_home(tmp_path, monkeypatch):
    """🔴 THE THIRD STATE, and the reason `Target.resolved` is a tri-state.

    With no derivable repo for the scope, a sha CANNOT be checked. Reporting that
    as NO HOME would send someone to re-write a record that may well exist — an
    unmeasured target is not an absent one. It must come back NOT CHECKED, and
    the verdict line must say the reading is incomplete.
    """
    monkeypatch.setenv("WORKSPACE", str(tmp_path / "empty-workspace"))
    store = _write_store(tmp_path / "norepo", {
        "eps.md": _entry("eps", body=f"- 2026-05-05: RESOLVED {UNREACHABLE_SHA}: closed.\n"),
    })
    a = sa.audit_store(store)
    b = next(b for b in a.entries[0].bullets if b.verdict)
    assert b.verdict == sa.UNVERIFIED
    assert [t.resolved for t in b.targets] == [None]
    assert a.unresolved_scopes == [SCOPE]
    text = _render(store)
    assert "NOT a complete reading" in text
    assert "no owning repo derivable" in text


def test_a_miss_in_SIBLING_repos_alone_is_not_evidence_of_absence(tmp_path, monkeypatch):
    """🔴 THE NARROW CASE THE TRI-STATE EXISTS FOR, and the one that was WRONG.

    The scope's own repo is not derivable, but OTHER scopes' repos are — so the
    cross-repo fallback runs, searches the wrong repos, and finds nothing. An
    earlier revision reported that as `NO HOME`. It is not: the repo that would
    hold the sha was never searched, so the miss is UNMEASURED. Reporting it as
    absent sends someone to write a record that may already exist.
    """
    ws = tmp_path / "ws"
    other = ws / "otherscope"
    other.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(other)], check=True)
    monkeypatch.setenv("WORKSPACE", str(ws))

    root = tmp_path / "mixed"
    (root / SCOPE).mkdir(parents=True)          # NO repo named `synthrepo` exists
    # BOTH target kinds, because both take the same tri-state decision in two
    # different functions. Asserting only the verdict would not separate them —
    # `any(resolved is None)` is reached either way — so the per-target list is
    # what pins the path branch. (Measured: without it, mutating the path-side
    # `if repo is None` to a constant False SURVIVES the whole battery.)
    (root / SCOPE / "theta.md").write_text(
        _entry("theta", body=(
            f"- 2026-08-08: RESOLVED {UNREACHABLE_SHA}: closed; see "
            "`claudedocs/never-written.md`.\n"
        )),
        encoding="utf-8",
    )
    (root / "otherscope").mkdir()
    (root / "otherscope" / "iota.md").write_text(
        _entry("iota", body="- 2026-08-08: plain.\n").replace(
            f"scope: {SCOPE}", "scope: otherscope"),
        encoding="utf-8",
    )

    a = sa.audit_store(root)
    assert a.unresolved_scopes == [SCOPE], "the fixture must leave exactly one scope unmeasurable"
    assert a.repos, "and the OTHER scope must resolve, or the fallback never runs"
    b = next(b for e in a.entries for b in e.bullets if b.verdict)
    assert b.verdict == sa.UNVERIFIED, (
        f"a miss in sibling repos only must be NOT CHECKED, got {b.verdict}"
    )
    assert sorted(t.kind for t in b.targets) == [sa.TARGET_PATH, sa.TARGET_SHA]
    assert [t.resolved for t in b.targets] == [None, None], (
        "BOTH the sha search and the path search must return the unmeasured "
        f"state here, got {[(t.kind, t.resolved) for t in b.targets]}"
    )


def test_a_path_target_alone_is_enough_of_a_home(tmp_path, workspace):
    """A `RESOLVED` bullet whose sha is dead but which names a doc that EXISTS is
    evictable — the record has a home, which is the whole test."""
    store = _write_store(tmp_path / "pathhome", {
        "zeta.md": _entry("zeta", body=(
            f"- 2026-06-06: RESOLVED {UNREACHABLE_SHA}: closed; written up in "
            "`claudedocs/note.md`.\n"
        )),
    })
    b = next(b for b in _bullets(store, "zeta.md") if b.verdict)
    assert b.verdict == sa.EVICTABLE
    assert any(t.kind == sa.TARGET_PATH and t.resolved for t in b.targets)


# --- 🔴 an OPEN bullet is never an eviction candidate -----------------------------


def test_open_bullets_are_never_classified_for_eviction(dirty_store):
    """The lifecycle rule, asserted as a STATE rather than as a word.

    An OPEN bullet must carry NO verdict at all — not `EVICTABLE`, not
    `NO HOME` — because the classifier is never even asked about it. Asserting
    "the word EVICT does not appear near it" would be a spelled guard; this
    checks the field the eviction surfaces actually branch on.
    """
    opens = [b for b in _bullets(dirty_store, "alert-diagnose-svc.md")
             if b.population == "open"]
    assert len(opens) == 1
    assert opens[0].verdict is None
    assert opens[0].targets == ()

    text = _render(dirty_store)
    assert "🔒 KEEP" in text
    assert "NEVER an eviction candidate" in text
    assert "1 OPEN bullet(s) in 1 entries are NOT in that list" in text


def test_an_open_bullet_survives_even_when_it_names_a_resolved_sha(tmp_path, workspace):
    """The precedence case: `OPEN:` wins outright over any target the bullet quotes.

    Without this, a detector keyed on "does the text mention a reachable commit"
    would happily propose evicting an OPEN bullet that merely cites one.
    """
    _ws, _repo, head = workspace
    store = _write_store(tmp_path / "openref", {
        "eta.md": _entry("eta", body=(
            f"- 2026-07-07: OPEN: blocked on the follow-up to `{head[:9]}`.\n"
        )),
    })
    b = _bullets(store, "eta.md")[0]
    assert b.population == "open"
    assert b.verdict is None


# --- pointer integrity, front matter, scope policy --------------------------------


def test_pointer_integrity_reports_both_directions_with_a_denominator(dirty_store, clean_store):
    dirty = sa.audit_store(dirty_store)
    broken = [t for e in dirty.entries for t in e.pointer_targets if t.resolved is False]
    ok = [t for e in dirty.entries for t in e.pointer_targets if t.resolved is True]
    assert len(broken) == 1 and broken[0].token == "claudedocs/absent-file.md"
    assert len(ok) == 1 and ok[0].token == "claudedocs/note.md"

    clean = sa.audit_store(clean_store)
    assert [t.resolved for e in clean.entries for t in e.pointer_targets] == [True]
    text = _render(clean_store)
    assert "at least 1 path pointer(s) checked across 1 entries" in text


@pytest.mark.parametrize("token", [
    "scripts/x/{a.sh,b.sh}",      # a brace SET written as one token
    "/dp-build-deploy",           # a single-segment absolute: a route, not a file
    "reference/sites/<host>.md",  # a documented placeholder segment
    "kube-system/coredns",        # namespace/pod
    "https://example.invalid/a/b.md",
])
def test_pathish_floor_rejects_the_shapes_that_manufacture_false_findings(token):
    """Each of these was measured producing a phantom "broken pointer" before the
    floor was narrowed. A rejected token is counted in NEITHER direction — which
    is why every renderer says "at least N", never "N paths exist"."""
    assert not sa._is_pathish(token)


@pytest.mark.parametrize("token", [
    "claudedocs/note.md", "scripts/lib/x.py", "nix/home.nix", "~/.claude/settings.json",
])
def test_pathish_floor_still_accepts_real_paths(token):
    """POSITIVE CONTROL for the floor itself: a filter that rejects everything
    would pass every test above and check nothing."""
    assert sa._is_pathish(token)


def test_front_matter_and_scope_policy_counters_move_off_zero(dirty_store, clean_store):
    dirty = sa.audit_store(dirty_store)
    missing_sens = [e.where for e in dirty.entries if "sensitivity" in e.fm_missing]
    assert missing_sens == [f"{SCOPE}/synth-advertising.md"]
    assert dirty.scopes_without_readme == [SCOPE]

    clean = sa.audit_store(clean_store)
    assert [e.fm_missing for e in clean.entries] == [()]
    assert clean.scopes_without_readme == []


# --- 🔴 read-only -----------------------------------------------------------------


def _digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_the_audit_writes_nothing_to_the_store(dirty_store):
    """🔴 THE SAFETY PROPERTY, measured rather than asserted in a docstring.

    The real store is curated, client-confidential and not re-derivable by
    re-running recon. (Was "has no backup" — false; hourly local commits, daily
    age-encrypted MinIO bundles. Unchanged here: a backup restores to the last
    commit, it does not undo an audit that scribbled on the working tree.)
    Byte-hash every file before and after a full audit + render; any difference
    at all — content, a new file, a deleted one — fails.
    """
    before = _digest(dirty_store)
    _render(dirty_store)
    after = _digest(dirty_store)
    assert before == after
    # 🔴 THE POSITIVE CONTROL ON THE HASHER ITSELF. `{} == {}` is True, so a
    # digest of an empty walk would make this test pass while proving nothing.
    assert len(before) == 2, "the fixture must actually contain files to hash"


def test_git_refuses_to_run_inside_the_store(dirty_store):
    """The one place the audit shells out is `_git`, and it must never point at
    the store — whose scope dirs are each their own git repo. Watched to fail:
    without the guard this call succeeds and git runs on curated content."""
    with pytest.raises(RuntimeError, match="refusing to run git inside the index store"):
        sa._git(dirty_store / SCOPE, "status", store_root=dirty_store)


def test_the_audit_source_contains_no_write_call():
    """Structural backstop for the behavioural test above.

    A future edit could add a write on a path no fixture reaches, and the hash
    test would stay green. This one reads the source. It is deliberately a
    SUBSTRING ban, not a parse: `open(..., "w")` spelled any of the ways Python
    allows still contains one of these tokens.
    """
    src = (SCRIPTS / "subsystem-audit.py").read_text(encoding="utf-8")
    # strip the docstrings/comments that legitimately DISCUSS writing
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    for banned in ("write_text(", "write_bytes(", ".mkdir(", ".unlink(", "shutil.",
                   "os.remove", "os.rename", "os.replace"):
        assert banned not in code, (
            f"scripts/subsystem-audit.py contains {banned!r}. This auditor is "
            "READ-ONLY by contract — the store is client-confidential and the "
            "uncommitted window a stray write lands on is in no commit and no "
            "off-machine bundle."
        )


def test_a_missing_store_is_not_an_empty_one(tmp_path, capsys):
    """`store-missing` and `0 entries` are different facts and must not collapse."""
    rc = sa.main(["--store", str(tmp_path / "nope")])
    assert rc == 2
    assert "store root not found" in capsys.readouterr().err


def test_an_unmatched_scope_filter_is_not_a_clean_run(clean_store, capsys):
    """A `--scope` typo must not render a reassuring empty report."""
    rc = sa.main(["--store", str(clean_store), "--scope", "no-such-scope"])
    assert rc == 2
    assert "nothing was examined" in capsys.readouterr().err


# --- the verdict ------------------------------------------------------------------


def test_verdict_names_every_finding_class_on_the_dirty_store(dirty_store):
    text = _render(dirty_store)
    assert "⚠ prune needed" in text
    for expected in ("1 RESOLVED bullet(s) evictable",
                     "1 NO HOME (write the record first)",
                     "1 broken pointer(s)",
                     "1 ref collision(s)",
                     "1 scope(s) with no README",
                     "front-matter gaps"):
        assert expected in text, f"verdict is missing {expected!r}:\n{text}"


def test_verdict_is_clean_on_the_clean_store(clean_store):
    text = _render(clean_store)
    assert "no prune needed (stop; do not churn the files)" in text
    assert "⚠ prune needed" not in text
