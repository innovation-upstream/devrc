"""Tests for scripts/analyze-service-index/restore-verify.py — the RESTORE VERIFIER.

WHAT IS BEING PROTECTED, AND WHY IT IS NOT WHAT backup.py PROTECTS
------------------------------------------------------------------
`backup.py` checks the bundle it built, BEFORE encryption and BEFORE upload. It
is careful and it is thorough, and every one of its checks happens upstream of
encryption, upload, object storage and retention. Nothing has ever verified the
artifact as it actually sits in MinIO.

That is the isolation seam RULES.md names: two surfaces each hermetically
tested, the defect living in the join. `restore-verify.py` starts from the bytes
in the bucket and ends at reconstructed git history; this file is what proves it
can tell the difference between an artifact that restores and one that does not.

🔴 THE ONE MEASUREMENT THE WHOLE DESIGN RESTS ON
------------------------------------------------
`git bundle verify` DOES NOT DETECT CORRUPTION. It validates the bundle header
and the prerequisites; it never walks the packfile. A byte flipped in the middle
of the pack passes it at rc=0 while a clone of the same bundle dies. So this
suite asserts BOTH halves, in the same file, deliberately:

  * `test_a_corrupted_artifact_FAILS_the_verifier` — the verifier catches it,
    on its own message, through the real CLI as well as the library.
  * `test_git_bundle_verify_returns_ZERO_on_that_same_corrupted_bundle` — the
    weakness, pinned. If a future git tightens `bundle verify` this goes red and
    whoever sees it can re-argue the design with evidence rather than hope.

Delete either and the next person "simplifies" the restore back into a
`bundle verify` that certifies corrupt archives.

🔴 ALL FIXTURES ARE SYNTHETIC. devrc is a public repo and the real store is
client-confidential: no scope name, entry title, path, host label or key
material from the live store appears here. The synthetic scopes are
`scope-alpha`/`scope-beta`/`scope-gamma`, the synthetic host label is
`synthetic-host`, and the age identities are generated and destroyed inside the
test run.

🔴 NOTHING HERE SKIPS. `git`, `age` and `age-keygen` are hard requirements
resolved once at import, exactly as in the backup suite: a skipped
disaster-recovery test reports safety it never measured, and it is quiet at
precisely the moment it is wrong.

Every negative control asserts THIS guard's own message, not merely a non-zero
exit — a control that goes red because a different guard fired is green for the
wrong reason and stays green with the guard it claims to test deleted.
"""
from __future__ import annotations

import hashlib
import importlib.util
from testlib import hermetic_git  # noqa: E402
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
SCRIPT = SCRIPTS / "analyze-service-index" / "restore-verify.py"
BACKUP_SCRIPT = SCRIPTS / "analyze-service-index" / "backup.py"
SECRETS_MD = ROOT / "SECRETS.md"

sys.path.insert(0, str(SCRIPTS / "analyze-service-index"))
sys.path.insert(0, str(SCRIPTS))

import backup as B  # noqa: E402


def _load_restore_verify():
    """Import a module whose FILE NAME contains a hyphen.

    🔴 `sys.modules[...] = mod` BEFORE `exec_module` is REQUIRED, not tidiness.
    `@dataclass` resolves its annotations by looking the defining module up in
    `sys.modules`; without the registration the decorator raises
    `AttributeError: 'NoneType' object has no attribute '__dict__'` at import
    time and every test in this file becomes a collection error. Measured here
    before it was added.

    The producer is `backup.py` and imports normally; only this one needs the
    dance, because the CLI name the operator types is `restore-verify.py`.
    """
    spec = importlib.util.spec_from_file_location("restore_verify", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["restore_verify"] = mod
    spec.loader.exec_module(mod)
    return mod


RV = _load_restore_verify()


def _require(tool: str, why: str) -> str:
    p = shutil.which(tool)
    if p is None:  # pragma: no cover - the flake check puts all three on PATH
        raise RuntimeError(
            f"{tool} is not on PATH. {why} It is declared in nix/pkgs/default.nix "
            f"and in flake.nix `gateTools`; add it there rather than skipping "
            f"these tests — a skipped restore test reports recoverability it "
            f"never measured.")
    return p


GIT: str = _require("git", "Every scope and every restore is a real git repository.")
AGE: str = _require("age", "The artifacts in the bucket are age ciphertext.")
AGE_KEYGEN: str = _require("age-keygen", "The identities here are generated per run.")

HOST = "synthetic-host"
PREFIX = HOST + "/"

# A synthetic /etc/machine-id. Never this machine's real one: devrc is public.
SYNTH_MACHINE_ID = "0123456789abcdef0123456789abcdef"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _git_env() -> dict:
    """A git environment that cannot read or write the OPERATOR's real config,
    and in which git's BACKGROUND MAINTENANCE cannot fire.

    🔴 THE MAINTENANCE PIN IS WHAT MAKES `_manifest` HONEST, and it is the same
    fix #743 made for `tree_hash` in `test_handoff_doc.py` — the source removed,
    not the guard weakened.

    `_manifest` fingerprints every file under the repo root INCLUDING `.git`,
    deliberately: a read that refreshes `.git/index` moves an mtime, and a
    manifest blind to that would call a mutating read "unchanged". The cost of
    that honesty is that any file git creates under `.git` on its own initiative
    is a difference. Git's auto-maintenance does exactly that, transiently:

        AssertionError: the repository changed anyway
          Right contains 1 more item:
          {'.git/objects/maintenance.lock': (0, …, 'e3b0c44…')}

    MEASURED in CI 2026-08-23 on `devrc-ci-7w4kx` (PR #764, a branch touching
    neither this file nor the script it tests): 15,481 collected, 1 failed, at
    `test_git_refuses_a_write_or_forbidden_subcommand`. The same tree passed
    three local runs. `claude/RULES.md` calls a flaky test fixable — "remove the
    timing dependency rather than re-running" — and this is the second module to
    need it, so the mechanism is copied rather than reinvented.

    `GIT_CONFIG_COUNT`/`_KEY_n`/`_VALUE_n` inject config that outranks the repo's
    own, and because they are ENVIRONMENT variables they are inherited by every
    git process spawned from one that has them -- including the ones the script
    under test launches, since `_run` merges this dict.

    ⚠ `GIT_CONFIG_COUNT` is NOT redundant with the `/dev/null` pins beside it.
    Those two stop git READING the operator's config; neither turns maintenance
    off, because `maintenance.auto` and `gc.auto` default to ON with no config
    file at all. `test_git_maintenance_cannot_fire_inside_a_fixture_repo` below
    proves that distinction with a negative control rather than asserting it.
    """
    e = dict(os.environ)
    e.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@localhost",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@localhost",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "file",
        **hermetic_git.MAINTENANCE_OFF,
    })
    return e


def _git_run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([GIT, "-C", str(repo), *args],
                          capture_output=True, text=True, env=_git_env())


def test_git_maintenance_cannot_fire_inside_a_fixture_repo(tmp_path):
    """🔴 THE REGRESSION GUARD for the `maintenance.lock` flake.

    Asserts the pin REACHES a real git process launched the way this module
    launches them. A structural check on the dict would prove nothing — a
    literal cannot be misspelled in an interesting way — so this reads the
    values back out of git itself, inside a fixture repo built by `_make_scope`.

    🔴 THE NEGATIVE CONTROL IS THE LOAD-BEARING HALF. It re-runs the identical
    query with ONLY the `GIT_CONFIG_COUNT` injection stripped, leaving
    `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM`/`GIT_CONFIG_NOSYSTEM` pinned — so
    exactly ONE variable moves. Without it, a git that happened to default these
    off would satisfy the assertions above while the pin did nothing, which is
    `claude/RULES.md`'s "a green that is a fact about the instrument".
    """
    repo = _make_scope(tmp_path / "store", "scope-maint", {"e.md": "x"}, commits=1)

    # `--default ''` so an UNSET key exits 0 and returns empty. Without it a
    # broken pin dies on the non-zero exit and the failure names the helper
    # rather than the property under test.
    def _cfg(key: str, env: dict) -> str:
        return subprocess.run(
            [GIT, "-C", str(repo), "config", "--get", "--default", "", key],
            capture_output=True, text=True, env=env,
        ).stdout.strip()

    pinned = _git_env()
    assert _cfg("maintenance.auto", pinned) == "false", (
        "maintenance.auto is not pinned off inside a fixture repo — git can "
        "create .git/objects/maintenance.lock mid-test and _manifest will "
        "correctly report the repository as changed"
    )
    assert _cfg("gc.auto", pinned) == "0", "gc.auto is not pinned off"

    stripped = {
        k: v for k, v in pinned.items()
        if not k.startswith(("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_",
                             "GIT_CONFIG_VALUE_"))
    }
    assert _cfg("maintenance.auto", stripped) == "", (
        "git reports maintenance.auto even with the injection removed, so the "
        "assertion above is green for a reason other than this pin — it proves "
        "nothing about the fix"
    )


def _make_scope(store: Path, name: str, entries: dict[str, str],
                commits: int = 1) -> Path:
    """A synthetic scope repository: `commits` commits over `entries`."""
    scope = store / name
    scope.mkdir(parents=True)
    subprocess.run([GIT, "init", "-q", "-b", "trunk", str(scope)],
                   check=True, capture_output=True, env=_git_env())
    for i in range(commits):
        for fname, body in entries.items():
            (scope / fname).write_text(f"{body}\nrevision {i}\n", encoding="utf-8")
        _git_run(scope, "add", *entries.keys())
        _git_run(scope, "commit", "-q", "-m", f"synthetic commit {i}")
    return scope


def _commit_more(scope: Path, n: int = 1) -> None:
    """Advance the live scope the way the hourly autocommit does: MOVE a branch."""
    for i in range(n):
        (scope / "e.md").write_text(f"the committer moved on {i}\n", encoding="utf-8")
        _git_run(scope, "add", "e.md")
        r = _git_run(scope, "commit", "-q", "-m", f"autocommit {i}")
        assert r.returncode == 0, r.stderr


@pytest.fixture()
def identity(tmp_path_factory) -> Path:
    """A throwaway age identity. 🔴 NEVER the operator's real key."""
    d = tmp_path_factory.mktemp("age-id")
    key = d / "test.key"
    p = subprocess.run([AGE_KEYGEN, "-o", str(key)], capture_output=True, text=True)
    assert p.returncode == 0, f"age-keygen failed: {p.stderr}"
    key.chmod(0o600)
    return key


def _recipient(identity: Path) -> str:
    p = subprocess.run([AGE_KEYGEN, "-y", str(identity)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def _make_artifact(scope: Path, identity: Path, tmp: Path, *, mangle=None) -> bytes:
    """Build a REAL artifact the way the producer does: bundle -> age.

    `mangle(bundle_path)` runs on the PLAINTEXT bundle after `bundle_scope` has
    accepted it and before `age` encrypts it. That is the only seam where a
    corrupted-but-decryptable artifact can be constructed: damaging the
    CIPHERTEXT makes age refuse it, which exercises the decrypt guard and never
    reaches the restore guard at all.
    """
    work = tmp / f"mk-{scope.name}-{abs(hash((str(scope), id(mangle)))) % 10**8}"
    work.mkdir(parents=True, exist_ok=True)
    bundle = work / "a.bundle"
    cipher = work / "a.bundle.age"
    B.bundle_scope(scope, bundle, work)
    if mangle is not None:
        mangle(bundle)
    B.encrypt(bundle, cipher, _recipient(identity))
    data = cipher.read_bytes()
    bundle.unlink(missing_ok=True)
    cipher.unlink(missing_ok=True)
    return data


NOW = datetime(2026, 5, 17, 6, 11, 23, tzinfo=timezone.utc)


def _key(scope: str, when: datetime | None = None, host: str = HOST) -> str:
    """An object key, written by the PRODUCER's own key function.

    Deliberately not spelled out by hand here: the verifier's parser and the
    producer's writer are a seam, and a fixture that hand-wrote the key would
    only ever prove the parser agrees with the test author. The literal shape is
    pinned separately, once, in `test_the_key_shape_is_pinned_LITERALLY`.
    """
    return B.object_key(host, scope, when or NOW)


class FakeDownloader:
    """The object store, faked. Serves list/get; records every get.

    This is where the network hop is cut out and nothing else is. Decryption,
    the restore, `git fsck` and the whole cross-check above it are REAL, so the
    drill still proves the artifact is restorable.
    """

    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects: dict[str, bytes] = dict(objects or {})
        self.gets: list[str] = []
        self.entered = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *exc):
        return False

    def list(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    def get(self, key):
        self.gets.append(key)
        return self.objects[key]


def _verify(store: Path, work: Path, downloader, identity: Path, *,
            prefix: str = HOST, this_host: str | None = None,
            this_machine_id: str | None = None,
            scope_filter=None, verify_all=False,
            max_lag_days: float = 3.0, explicit: bool = False,
            keep_work_dir: bool = False, now: datetime | None = None):
    """Drive the real `run()` with the object store faked.

    `this_host` defaults to the PREFIX under test, i.e. "these artifacts are
    this machine's" — the ordinary case. A cross-host test passes the two
    apart, which is the only thing that makes the artifacts foreign.
    """
    return RV.run(bucket="test-bucket", prefix=prefix,
                  this_host=prefix if this_host is None else this_host,
                  this_machine_id=this_machine_id,
                  store=store, identity=identity, scope_filter=scope_filter,
                  verify_all=verify_all, max_lag_days=max_lag_days,
                  work_dir=work, keep_work_dir=keep_work_dir,
                  prefix_was_explicit=explicit,
                  downloader_factory=lambda: downloader, now=now or NOW)


def _cli(*args: str, identity: Path | None = None,
         env: dict | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.update(_git_env())
    if identity is not None:
        e["ASIB_AGE_IDENTITY"] = str(identity)
    e["ASIB_HOST"] = HOST
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, env=e)


def _dir_store(root: Path, objects: dict[str, bytes]) -> Path:
    """Write `objects` out as a `--from-dir` object store.

    The directory is created even when `objects` is empty: an EMPTY store and a
    MISSING one are different findings and the tests need to name which one they
    are exercising.
    """
    root.mkdir(parents=True, exist_ok=True)
    for k, v in objects.items():
        p = root / k
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(v)
    return root


def _manifest(root: Path) -> dict[str, tuple[int, int, str]]:
    """Byte-level fingerprint of every file under `root`: size, mtime_ns, sha256.

    🔴 mtime_ns is IN the fingerprint on purpose. A git read that refreshes
    `.git/index` leaves the content identical and moves the mtime; a manifest
    that only hashed content would call that "unchanged" and the read-only claim
    would be false in exactly the way nobody checks.
    """
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        st = p.stat()
        out[str(p.relative_to(root))] = (
            st.st_size, st.st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest(),
        )
    return out


def _flip_middle_byte(path: Path) -> None:
    raw = bytearray(path.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    path.write_bytes(bytes(raw))


# --------------------------------------------------------------------------- #
# 0. harness self-validation — validate the INSTRUMENT before reading its verdict
# --------------------------------------------------------------------------- #
def test_the_verifier_exists_and_actually_imported():
    """POSITIVE CONTROL for the hyphenated-filename loader.

    If `_load_restore_verify()` silently returned an empty module every test
    below would fail confusingly, or worse, an `getattr`-tolerant one would pass
    against nothing. Watch the module carry the symbols the suite reads.
    """
    assert SCRIPT.is_file(), f"{SCRIPT} missing — every test below is vacuous"
    for name in ("run", "verify_artifact", "cross_check", "restore", "decrypt",
                 "split_key", "_READ_ONLY_SUBCOMMANDS", "RestoreVerifyError"):
        assert hasattr(RV, name), f"the loaded module has no {name!r}"


def test_the_fake_downloader_can_observe_a_get():
    """POSITIVE CONTROL for the instrument every artifact test reads.

    A FakeDownloader wired to nothing would serve zero objects, and every
    assertion of the form "the artifact verified" would then be about a harness
    that never ran. Watch the counter move before trusting it.
    """
    d = FakeDownloader({"h/s/x.bundle.age": b"abc"})
    assert d.gets == []
    assert d.list("h/s/") == ["h/s/x.bundle.age"]
    assert d.get("h/s/x.bundle.age") == b"abc"
    assert d.gets == ["h/s/x.bundle.age"]
    assert d.list("other/") == [], "list() must respect the prefix"


def test_the_artifact_builder_really_produces_age_CIPHERTEXT(tmp_path, identity):
    """POSITIVE CONTROL for the fixture: if `_make_artifact` produced the
    plaintext bundle, the decrypt guard would never be exercised and the
    corruption tests would be measuring something else entirely."""
    scope = _make_scope(tmp_path / "store", "scope-alpha",
                        {"e.md": "canary-plaintext-marker"}, commits=2)
    data = _make_artifact(scope, identity, tmp_path)
    assert data.startswith(b"age-encryption.org/"), "not an age file"
    assert b"canary-plaintext-marker" not in data, "content readable in the artifact"


# --------------------------------------------------------------------------- #
# 1. 🔴 THE CORRUPTION CONTROL — the reason this script exists at all
# --------------------------------------------------------------------------- #
def test_git_bundle_verify_returns_ZERO_on_a_pack_corrupted_bundle(tmp_path):
    """🔴 THE MEASUREMENT THAT CHANGED THE DESIGN. Pinned so nobody re-simplifies.

    `git bundle verify` validates the bundle HEADER and the PREREQUISITES. It
    does not walk the packfile. Measured here, every run: a bundle with one byte
    flipped in the middle passes it at rc=0.

    This test asserts the WEAKNESS, deliberately. If a future git ever tightens
    `bundle verify`, this goes red and whoever sees it can delete the restore
    path with evidence rather than hope.
    """
    scope = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=3)
    good = tmp_path / "good.bundle"
    B.bundle_scope(scope, good, tmp_path / "work")

    bad = tmp_path / "bad.bundle"
    bad.write_bytes(good.read_bytes())
    _flip_middle_byte(bad)
    assert bad.read_bytes() != good.read_bytes(), "the fixture did not damage anything"

    v = B._git(scope, "bundle", "verify", str(bad))
    assert v.returncode == 0, (
        "git bundle verify now REJECTS a byte-flipped bundle. restore-verify.py "
        "restores instead of asking it BECAUSE it did not; re-measure and "
        "re-argue the design if that has genuinely changed.")


def test_a_corrupted_artifact_FAILS_the_verifier(tmp_path, identity):
    """🔴 THE NEGATIVE CONTROL THE WHOLE DELIVERABLE IS FOR.

    Same damage as the test above — one byte flipped mid-packfile, header
    intact, `git bundle verify` waving it through — reaching the verifier as a
    real age artifact. Asserted on THIS guard's own message: a run that failed
    with `DECRYPT FAILED` would be green for the wrong reason and would stay
    green with the restore deleted.
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    data = _make_artifact(scope, identity, tmp_path, mangle=_flip_middle_byte)
    dl = FakeDownloader({_key("scope-alpha"): data})

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work", dl, identity)
    msg = str(exc.value)
    assert "RESTORE FAILED" in msg, f"failed for the wrong reason: {msg}"
    assert "could NOT BE CLONED" in msg, f"failed for the wrong reason: {msg}"
    assert "DECRYPT FAILED" not in msg, (
        "the corruption was reported as a decryption problem — the two faults "
        "have different remedies and must never share a word")


def test_a_corrupted_artifact_fails_THROUGH_THE_REAL_CLI(tmp_path, identity):
    """The library raising is not the same claim as the PROCESS failing, and the
    process exit code is what an operator and a timer read."""
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    data = _make_artifact(scope, identity, tmp_path, mangle=_flip_middle_byte)
    src = _dir_store(tmp_path / "objects", {_key("scope-alpha"): data})

    r = _cli("--from-dir", str(src), "--prefix", HOST, "--store", str(store),
             "--max-lag-days", "100000", identity=identity)
    assert r.returncode == 1, f"a corrupted artifact exited {r.returncode}\n{r.stderr}"
    assert "RESTORE FAILED" in r.stderr, f"failed for the wrong reason:\n{r.stderr}"
    assert "verified 0" not in r.stdout, "a failed run printed a success summary"


def _literal_string_args(src: str) -> list[list[str]]:
    """Every call site's literal string arguments, from the AST.

    🔴 An AST walk and NOT a `grep`. This module says "bundle verify" in six
    places — a docstring, a refusal message, and `--print-plan`'s output — all of
    which exist precisely to warn people off it. A text scan cannot tell a
    warning from a call, so it would either be permanently red or have to be
    weakened until it matched nothing. Comments and docstrings are not Call
    nodes, so they are invisible here for free.
    """
    import ast
    out: list[list[str]] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        out.append([a.value for a in node.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)])
    return out


def test_the_ast_reader_can_SEE_a_call_and_IGNORES_prose():
    """VALIDATE THE INSTRUMENT before reading its verdict.

    A reader that returned nothing would make the two tests below pass over a
    file that called `git bundle verify` on every line. Positive control: it
    finds a real call's literals. Negative control: the identical words inside a
    docstring and a comment are NOT reported.
    """
    seen = _literal_string_args(
        '"""a docstring mentioning bundle and verify"""\n'
        '# a comment mentioning bundle and verify\n'
        'x = "bundle verify in a plain string"\n'
        '_git(repo, "bundle", "verify", path)\n'
        'other(1, 2)\n')
    assert ["bundle", "verify"] in seen, (
        f"the reader cannot see a call's literal arguments: {seen}")
    assert all("a docstring mentioning bundle and verify" not in a
               for args in seen for a in args), seen


def test_the_verifier_NEVER_CALLS_git_bundle_verify():
    """🔴 THE STRUCTURAL HALF of the rule. The behavioural proof is the pair of
    tests above; this one guards the REWRITE those cannot survive.

    The failure mode is not a bug, it is an edit: somebody reads
    `git clone --mirror` + `git fsck`, notices `git bundle verify` is cheaper,
    and swaps it in — deleting or weakening the behavioural control in the same
    change, because it is now failing. This makes that substitution fail at the
    call site (the allowlist) AND visible in the diff (here).
    """
    assert "bundle" not in RV._READ_ONLY_SUBCOMMANDS, (
        "`bundle` is on the verifier's read-only allowlist, so `git bundle "
        "verify` against the store is reachable again")
    for args in _literal_string_args(SCRIPT.read_text(encoding="utf-8")):
        assert not ("bundle" in args and "verify" in args), (
            f"restore-verify.py CALLS something with {args} — `git bundle "
            f"verify` passes a byte-flipped bundle at rc=0, so verification "
            f"here must be a real restore.")


def test_the_bundle_verify_prohibition_is_ENFORCED_at_the_call(tmp_path):
    """REACHABILITY for the allowlist half of the rule above: the guard fires on
    its own message, for the exact command a "simplification" would reach for."""
    scope = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=1)
    with pytest.raises(RV.RestoreVerifyError) as exc:
        RV._git(scope, "bundle", "verify", str(tmp_path / "x.bundle"))
    assert "READ-ONLY to this script" in str(exc.value)
    assert "absent ON PURPOSE" in str(exc.value), (
        f"the refusal does not say WHY bundle is missing, so the next person "
        f"will add it back: {exc.value}")


# --------------------------------------------------------------------------- #
# 2. 🔴 THE POSITIVE CONTROL — the comparison can actually observe something
# --------------------------------------------------------------------------- #
def test_an_intact_artifact_verifies_and_the_comparison_is_NON_EMPTY(
        tmp_path, identity):
    """🔴 A verifier wired to nothing also reports '0 problems'.

    So the count of commits actually compared is asserted to be the source's
    exact commit count — a literal, pinned from the fixture rather than read
    back out of the implementation — and printed.
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"one.md": "a", "two.md": "b"},
                        commits=6)
    dl = FakeDownloader({_key("scope-alpha"): _make_artifact(scope, identity, tmp_path)})

    verdicts = _verify(store, tmp_path / "work", dl, identity)

    assert len(verdicts) == 1, verdicts
    v = verdicts[0]
    print(f"commits compared: {v.commits_compared}, lag: {v.lag_commits}")
    assert v.cross_checked is True, "the intact artifact was not cross-checked"
    assert v.commits_restored == 6, v.commits_restored
    assert v.commits_compared == 6, (
        f"the cross-check compared {v.commits_compared} commits against a scope "
        f"with 6 — a comparison that looks at nothing reports the same '0 "
        f"problems' as one that looks at everything")
    assert v.lag_commits == 0, v.lag_commits
    assert v.refs_restored == 1 and v.ref_names == ["refs/heads/trunk"], v.ref_names
    assert dl.gets == [_key("scope-alpha")], (
        f"the verifier did not fetch the object it reported on: {dl.gets}")


def test_several_scopes_are_each_verified_and_summarised(tmp_path, identity):
    """The multi-scope shape the real bucket has, with three DIFFERENT commit
    counts so a summary that reported one scope's number for all of them, or a
    hardcoded constant, cannot pass."""
    store = tmp_path / "store"
    sources = {"scope-alpha": 4, "scope-beta": 1, "scope-gamma": 7}
    objects = {}
    for name, n in sources.items():
        s = _make_scope(store, name, {"e.md": name}, commits=n)
        objects[_key(name)] = _make_artifact(s, identity, tmp_path)

    verdicts = _verify(store, tmp_path / "work", FakeDownloader(objects), identity)

    assert {v.scope: v.commits_compared for v in verdicts} == sources
    assert all(v.cross_checked for v in verdicts)
    line = RV.summarise(verdicts)
    assert "verified 3 artifact(s)" in line, line
    assert "3 against the live store" in line, line
    assert "12 commit(s) compared" in line, line        # 4 + 1 + 7, pinned literally
    assert "0 self-consistency only" in line, line


def test_the_summary_never_merges_cross_checked_with_NOT_cross_checked(
        tmp_path, identity):
    """🔴 'Verified' and 'verified against what' are two facts. A summary that
    printed one of them is how a partial answer gets read as a whole one."""
    store = tmp_path / "store"
    alpha = _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=3)
    # scope-beta exists in the BUCKET but has no live counterpart — the store
    # half of a partial disaster.
    beta = _make_scope(tmp_path / "elsewhere", "scope-beta", {"e.md": "b"}, commits=2)
    objects = {_key("scope-alpha"): _make_artifact(alpha, identity, tmp_path),
               _key("scope-beta"): _make_artifact(beta, identity, tmp_path)}

    verdicts = _verify(store, tmp_path / "work", FakeDownloader(objects), identity)

    got = {v.scope: v.cross_checked for v in verdicts}
    assert got == {"scope-alpha": True, "scope-beta": False}, got
    line = RV.summarise(verdicts)
    assert "1 against the live store" in line, line
    assert "1 self-consistency only" in line, line


# --------------------------------------------------------------------------- #
# 3. 🔴 DAMAGE — truncation, and the decrypt/restore fault line
# --------------------------------------------------------------------------- #
def test_a_truncated_CIPHERTEXT_fails_as_a_DECRYPT_failure(tmp_path, identity):
    """The object was stored short. age's AEAD sees it first, so this is a
    decrypt fault and must be named as one."""
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    data = _make_artifact(scope, identity, tmp_path)

    # POSITIVE CONTROL: the INTACT artifact verifies, so the failure below is
    # about the truncation and not about the fixture.
    assert _verify(store, tmp_path / "w-ok", FakeDownloader({_key("scope-alpha"): data}),
                   identity)[0].cross_checked

    short = data[: len(data) // 3]
    assert len(short) < len(data)
    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work",
                FakeDownloader({_key("scope-alpha"): short}), identity)
    assert "DECRYPT FAILED" in str(exc.value), f"wrong reason: {exc.value}"


def test_a_truncated_BUNDLE_inside_a_valid_artifact_fails_as_a_RESTORE_failure(
        tmp_path, identity):
    """🔴 The case a ciphertext truncation CANNOT reach.

    Damage the plaintext bundle and re-encrypt: age is satisfied, the bytes
    decrypt perfectly, and only the restore can see that half the packfile is
    gone. Without this test the restore guard is only ever reached by the
    byte-flip, and a mutation that special-cased that one shape would survive.
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=4)

    def truncate(bundle: Path) -> None:
        raw = bundle.read_bytes()
        bundle.write_bytes(raw[: len(raw) // 2])

    data = _make_artifact(scope, identity, tmp_path, mangle=truncate)
    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work",
                FakeDownloader({_key("scope-alpha"): data}), identity)
    assert "RESTORE FAILED" in str(exc.value), f"wrong reason: {exc.value}"
    assert "DECRYPT FAILED" not in str(exc.value)


def test_a_ZERO_BYTE_object_is_a_failure_not_an_empty_restore(tmp_path, identity):
    """An empty object lists and stats like any other."""
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work",
                FakeDownloader({_key("scope-alpha"): b""}), identity)
    assert "is EMPTY (0 bytes)" in str(exc.value), f"wrong reason: {exc.value}"


def test_a_WRONG_IDENTITY_fails_as_a_DECRYPT_failure_not_a_corruption_one(
        tmp_path, identity, tmp_path_factory):
    """🔴 Two faults, two remedies, and they must never share a word.

    'The key does not open it' is a key-management problem — probably the
    recipient drifting from the identity the operator actually holds, which is
    the one failure `backup.py` structurally cannot notice about itself. 'The
    bytes do not reconstruct' is data loss. Reporting the first as the second
    sends someone hunting bit rot while their key is wrong.
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    data = _make_artifact(scope, identity, tmp_path)      # encrypted to `identity`

    other = tmp_path / "other.key"
    subprocess.run([AGE_KEYGEN, "-o", str(other)], check=True, capture_output=True)
    other.chmod(0o600)
    assert _recipient(other) != _recipient(identity), "the two keys are the same"

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work",
                FakeDownloader({_key("scope-alpha"): data}), other)
    msg = str(exc.value)
    assert "DECRYPT FAILED" in msg, f"wrong reason: {msg}"
    assert "(age rc=" in msg, (
        f"the failure did not come from age REFUSING the file — it came from "
        f"the sibling zero-bytes guard, which would stay green with the rc "
        f"check deleted: {msg}")
    assert "decrypted to ZERO bytes" not in msg, f"wrong decrypt guard: {msg}"
    assert "RESTORE FAILED" not in msg and "CROSS-CHECK" not in msg, (
        f"a wrong key was reported as damage to the history: {msg}")

    # POSITIVE CONTROL: the RIGHT key opens the very same bytes.
    assert _verify(store, tmp_path / "w-ok",
                   FakeDownloader({_key("scope-alpha"): data}), identity)[0].cross_checked


def test_a_VALID_ENCRYPTION_OF_NOTHING_is_a_failure(tmp_path, identity):
    """🔴 MEASURED: `age` encrypts a ZERO-BYTE payload to a valid 200-byte
    ciphertext and decrypts it back at rc=0.

    So an object can list, stat, decrypt and be non-empty in the bucket while
    carrying no history whatsoever. It is not the zero-byte-object case and it
    is not corruption — `age` is entirely happy — so only a guard that looks at
    the PLAINTEXT size can see it. Reached by an input no earlier check rejects.
    """
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)

    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    cipher = tmp_path / "empty.age"
    B.encrypt(empty, cipher, _recipient(identity))
    data = cipher.read_bytes()
    assert len(data) > 0, "the fixture produced an empty OBJECT, not an empty payload"
    assert data.startswith(b"age-encryption.org/"), "the fixture is not an age file"

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work",
                FakeDownloader({_key("scope-alpha"): data}), identity)
    msg = str(exc.value)
    assert "decrypted to ZERO bytes" in msg, f"wrong reason: {msg}"
    assert "is EMPTY (0 bytes)" not in msg, (
        f"caught by the zero-byte-OBJECT guard instead — that one cannot see "
        f"this case, and this control would stay green without it: {msg}")


def _malformed_commit_bundle(tmp: Path, scope: Path) -> Path:
    """A bundle that CLONES CLEANLY and fails `git fsck`.

    🔴 MEASURED, and it is the whole reason `fsck` runs after the clone:
    `index-pack` validates object HASHES and the pack checksum; it does not
    validate object GRAMMAR. A commit object with `author t<t@localhost>` (no
    space before the email) is hashed correctly, packs correctly, clones at
    rc=0 — and `git fsck` rejects it at rc=4 with `missingSpaceBeforeEmail`.

    Written with `hash-object --literally`, which is the documented way to store
    an object git would otherwise refuse to create.
    """
    tree = _git_run(scope, "rev-parse", "HEAD^{tree}").stdout.strip()
    parent = _git_run(scope, "rev-parse", "HEAD").stdout.strip()
    body = (f"tree {tree}\n"
            f"parent {parent}\n"
            f"author t<t@localhost> 1700000000 +0000\n"
            f"committer t <t@localhost> 1700000000 +0000\n"
            f"\nmalformed on purpose\n")
    p = subprocess.run([GIT, "-C", str(scope), "hash-object", "-t", "commit",
                        "-w", "--stdin", "--literally"],
                       input=body, capture_output=True, text=True, env=_git_env())
    assert p.returncode == 0, f"could not write the malformed object: {p.stderr}"
    oid = p.stdout.strip()
    assert _git_run(scope, "update-ref", "refs/heads/trunk", oid).returncode == 0

    bundle = tmp / "malformed.bundle"
    b = _git_run(scope, "bundle", "create", str(bundle), "--all")
    assert b.returncode == 0, f"bundle create refused it: {b.stderr}"
    return bundle


def test_a_bundle_that_CLONES_CLEANLY_but_FAILS_FSCK_is_rejected(tmp_path, identity):
    """🔴 REACHABILITY for the `git fsck` guard, past the clone guard.

    POSITIVE CONTROLS FIRST, because without them this test could not tell "the
    fsck guard fired" from "the clone guard fired": the clone is watched to
    SUCCEED on this exact bundle, and `git fsck` is watched to REJECT it. Only
    then is it fed through the verifier.
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    bundle = _malformed_commit_bundle(tmp_path, scope)

    probe = tmp_path / "probe.git"
    c = B._git_scratch(tmp_path, "clone", "--mirror", "--quiet", str(bundle),
                       str(probe), forbidden=store)
    assert c.returncode == 0, (
        f"the malformed bundle did not clone — the fsck guard is unreachable "
        f"through it and this test would be measuring the clone guard: {c.stderr}")
    f = B._git_scratch(probe, "fsck", "--no-progress", forbidden=store)
    assert f.returncode != 0, (
        "git fsck ACCEPTS the malformed commit; the guard under test cannot "
        "fire and should be re-argued rather than kept")
    assert "missingSpaceBeforeEmail" in (f.stderr + f.stdout), f.stderr

    cipher = tmp_path / "malformed.bundle.age"
    B.encrypt(bundle, cipher, _recipient(identity))
    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work",
                FakeDownloader({_key("scope-alpha"): cipher.read_bytes()}),
                identity)
    msg = str(exc.value)
    assert "FSCK FAILED" in msg, f"wrong reason: {msg}"
    assert "RESTORE FAILED" not in msg, (
        f"the clone guard fired instead — this control would stay green with "
        f"the fsck check deleted: {msg}")
    assert "not internally consistent" in msg, msg


def test_an_ABSENT_identity_file_is_refused_before_anything_is_fetched(
        tmp_path, identity):
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    dl = FakeDownloader({_key("scope-alpha"): _make_artifact(scope, identity, tmp_path)})
    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work", dl, tmp_path / "no-such.key")
    assert "no age identity at" in str(exc.value)
    assert dl.gets == [] and dl.entered == 0, (
        "it opened the object store before discovering it had no key — a "
        "port-forward and a download for a run that could never succeed")


# --------------------------------------------------------------------------- #
# 4. 🔴 THE CROSS-CHECK — a relationship, not an equality
# --------------------------------------------------------------------------- #
def test_FORWARD_PROGRESS_does_not_make_the_gate_RED(tmp_path, identity):
    """🔴 THE PERMANENTLY-RED GATE THIS DESIGN EXISTS TO AVOID.

    The live store advances hourly. An equality of restored refs against live
    refs would go red the moment the next autocommit lands — and a gate that is
    always red trains everyone to click through the one alarm that matters.

    Measured at TWO points, not one: 1 commit of drift and 9. A guard that
    happened to tolerate a single commit (an off-by-one, a `<=`) but not real
    drift would pass the first and fail the second.
    """
    store = tmp_path / "store"
    for name, extra in (("scope-alpha", 1), ("scope-beta", 9)):
        scope = _make_scope(store, name, {"e.md": name}, commits=3)
        data = _make_artifact(scope, identity, tmp_path)
        _commit_more(scope, extra)
        v = _verify(store, tmp_path / f"work-{name}",
                    FakeDownloader({_key(name): data}), identity)[0]
        assert v.cross_checked is True, f"{name}: forward progress went RED"
        assert v.commits_compared == 3, v.commits_compared
        assert v.lag_commits == extra, (
            f"{name}: reported lag {v.lag_commits}, {extra} commit(s) were added")


def test_the_lag_is_REPORTED_and_moves_with_the_store(tmp_path, identity):
    """POSITIVE CONTROL for the lag number itself: a hardcoded 0 would satisfy
    the round-trip tests above, where nothing has advanced."""
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    data = _make_artifact(scope, identity, tmp_path)
    seen = []
    for i, extra in enumerate((0, 3, 4)):
        _commit_more(scope, extra)
        seen.append(_verify(store, tmp_path / f"w{i}",
                            FakeDownloader({_key("scope-alpha"): data}),
                            identity)[0].lag_commits)
    assert seen == [0, 3, 7], seen


def test_a_REWRITTEN_history_is_caught_as_a_missing_commit(tmp_path, identity):
    """🔴 REACHABILITY for the 'every restored commit exists in live' guard.

    The artifact is taken, then the live scope's history is REPLACED (an
    orphan branch at the same ref name). Ref names still match and the ref still
    exists, so only the object-existence check can see it — this is the input
    that reaches it past every earlier guard.
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    data = _make_artifact(scope, identity, tmp_path)
    old_tip = _git_run(scope, "rev-parse", "HEAD").stdout.strip()

    # 🔴 REBUILT FROM SCRATCH, not `--orphan` + `branch -D`. A branch deletion
    # leaves the old commits in the object database until a gc, so `cat-file`
    # would still find every one of them and the guard under test would never
    # fire — the test would pass on the ref-presence guard instead and stay
    # green with the existence check deleted. Deleting the directory is also
    # the realistic shape: somebody removed the scope and /analyze-service
    # recreated it.
    shutil.rmtree(scope)
    scope = _make_scope(store, "scope-alpha", {"e.md": "a different history"},
                        commits=2)
    assert set(_git_run(scope, "for-each-ref", "--format=%(refname)").stdout.split()) \
        == {"refs/heads/trunk"}, "the fixture left extra refs; a different guard would fire"
    assert _git_run(scope, "cat-file", "-e", old_tip).returncode != 0, (
        "the artifact's tip is STILL in the live object database — the "
        "existence guard cannot fire and this test would prove nothing")

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work",
                FakeDownloader({_key("scope-alpha"): data}), identity)
    msg = str(exc.value)
    assert "CROSS-CHECK FAILED" in msg, f"wrong reason: {msg}"
    assert "do not exist in the live scope" in msg, f"wrong reason: {msg}"


def test_a_FORKED_tip_is_caught_by_the_ANCESTRY_check(tmp_path, identity):
    """🔴 REACHABILITY for the ancestry guard, past the existence guard.

    Every commit in the artifact still exists in the live scope — they are all
    on a side branch — so the existence check is SATISFIED and cannot be the
    thing that fires. `refs/heads/trunk` has moved to a commit that is not a
    descendant of the artifact's tip, which forward progress cannot produce.
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    data = _make_artifact(scope, identity, tmp_path)

    artifact_tip = _git_run(scope, "rev-parse", "HEAD").stdout.strip()
    root = _git_run(scope, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
    # Keep the old history reachable so the EXISTENCE check passes...
    assert _git_run(scope, "branch", "keepalive", artifact_tip).returncode == 0
    # ...and move trunk onto a sibling line that is not a descendant of it.
    assert _git_run(scope, "checkout", "-q", "-B", "trunk", root).returncode == 0
    (scope / "e.md").write_text("a divergent line\n", encoding="utf-8")
    _git_run(scope, "add", "e.md")
    assert _git_run(scope, "commit", "-q", "-m", "divergent").returncode == 0

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work",
                FakeDownloader({_key("scope-alpha"): data}), identity)
    msg = str(exc.value)
    assert "is NOT an ancestor of the live" in msg, (
        f"caught by the EXISTENCE guard instead of the ancestry one — this "
        f"control would stay green with the ancestry check deleted: {msg}")


def test_a_DELETED_ref_is_caught(tmp_path, identity):
    """The committer only ever MOVES a branch, so a ref that vanished from the
    live scope is a deletion, not forward progress."""
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    head = _git_run(scope, "rev-parse", "HEAD").stdout.strip()
    assert _git_run(scope, "update-ref", "refs/weird/thing", head).returncode == 0
    data = _make_artifact(scope, identity, tmp_path)
    assert _git_run(scope, "update-ref", "-d", "refs/weird/thing").returncode == 0

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work",
                FakeDownloader({_key("scope-alpha"): data}), identity)
    msg = str(exc.value)
    assert "ABSENT from the live scope" in msg, f"wrong reason: {msg}"
    assert "refs/weird/thing" in msg, f"the message does not name the ref: {msg}"


def test_a_NON_BRANCH_ref_round_trips_and_is_cross_checked(tmp_path, identity):
    """POSITIVE CONTROL for the test above, and the `--mirror` regression guard.

    `git notes` is an ordinary thing for a repository to acquire and
    `bundle create --all` captures it. A `--bare` restore would drop it silently,
    the ref-presence comparison would then never see it, and the deletion test
    above would be the only thing in this file that mentions non-branch refs —
    passing for a reason unrelated to restores.
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    head = _git_run(scope, "rev-parse", "HEAD").stdout.strip()
    assert _git_run(scope, "notes", "add", "-m", "a note").returncode == 0
    assert _git_run(scope, "update-ref", "refs/weird/thing", head).returncode == 0
    data = _make_artifact(scope, identity, tmp_path)

    v = _verify(store, tmp_path / "work",
                FakeDownloader({_key("scope-alpha"): data}), identity)[0]
    assert v.cross_checked is True
    assert v.ref_names == ["refs/heads/trunk", "refs/notes/commits",
                           "refs/weird/thing"], v.ref_names
    assert v.refs_restored == 3, v.refs_restored


# --------------------------------------------------------------------------- #
# 5. 🔴 THE ABSENT LIVE SCOPE — the actual disaster case, and NOT a failure
# --------------------------------------------------------------------------- #
def test_an_absent_live_scope_verifies_SELF_CONSISTENCY_and_says_so(
        tmp_path, identity):
    """🔴 If the store is gone there is nothing to cross-check against — which is
    the scenario these backups exist for. Failing here would make the verifier
    useless at the exact moment it is needed.

    But it must be DISTINGUISHABLE. `cross_checked` is False, the reason is
    stated, and the printed line says NOT CROSS-CHECKED rather than a word that
    reads like a full verification.
    """
    store = tmp_path / "store"
    scope = _make_scope(tmp_path / "elsewhere", "scope-alpha", {"e.md": "x"},
                        commits=5)
    data = _make_artifact(scope, identity, tmp_path)
    store.mkdir()          # the store exists; THIS SCOPE does not

    verdicts = _verify(store, tmp_path / "work",
                       FakeDownloader({_key("scope-alpha"): data}), identity)
    v = verdicts[0]
    assert v.cross_checked is False
    assert v.commits_restored == 5, v.commits_restored
    assert v.commits_compared == 0, "it claims to have compared commits it could not"
    assert v.lag_commits is None, "a lag was invented against a store that is gone"
    assert "no live scope repository at" in (v.no_cross_check_reason or "")
    assert "NOT CROSS-CHECKED" in v.line(), v.line()
    assert "VERIFIED AGAINST LIVE" not in v.line(), (
        "an unverifiable scope is reported with the words used for a verified "
        "one — the two claims are folded into one OK")


def test_an_absent_live_scope_EXITS_ZERO_through_the_real_CLI(tmp_path, identity):
    """The library returning is not the same claim as the PROCESS exiting 0."""
    scope = _make_scope(tmp_path / "elsewhere", "scope-alpha", {"e.md": "x"},
                        commits=4)
    data = _make_artifact(scope, identity, tmp_path)
    src = _dir_store(tmp_path / "objects", {_key("scope-alpha"): data})

    r = _cli("--from-dir", str(src), "--prefix", HOST,
             "--store", str(tmp_path / "gone"), "--max-lag-days", "100000",
             identity=identity)
    assert r.returncode == 0, f"rc={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "NOT CROSS-CHECKED" in r.stdout, r.stdout
    assert "self-consistency only" in r.stdout, r.stdout
    assert "1 against the live store" not in r.stdout, (
        f"the summary claims a live comparison that did not happen:\n{r.stdout}")


def test_an_EMPTY_artifact_is_still_a_failure_with_no_live_scope(
        tmp_path, identity, monkeypatch):
    """🔴 The self-consistency path must still be LOUD ON EMPTY.

    A restore that produces a valid repository with no commits is a backup of
    nothing — and with no live scope to compare against, the commit count is the
    ONLY thing left standing between that and a clean report.

    Reached by making the restored repository genuinely empty at the seam
    `_restored_facts` reads, which no earlier check rejects: the artifact
    decrypts, clones and passes fsck.
    """
    store = tmp_path / "store"
    scope = _make_scope(tmp_path / "elsewhere", "scope-alpha", {"e.md": "x"},
                        commits=2)
    data = _make_artifact(scope, identity, tmp_path)
    store.mkdir()

    real = RV._restored_facts
    monkeypatch.setattr(RV, "_restored_facts",
                        lambda dest, forbidden: (real(dest, forbidden=forbidden)[0], []))
    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work",
                FakeDownloader({_key("scope-alpha"): data}), identity)
    assert "restored to ZERO commits" in str(exc.value), f"wrong reason: {exc.value}"


# --------------------------------------------------------------------------- #
# 6. 🔴 LOUD ON EMPTY — a zero that looks like success is the failure mode
# --------------------------------------------------------------------------- #
def test_zero_objects_beside_a_POPULATED_store_is_a_FAILURE(tmp_path, identity):
    """The single worst thing this script can find: the store is here, the
    backups are not."""
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work", FakeDownloader({}), identity)
    msg = str(exc.value)
    assert "ZERO objects under" in msg, f"wrong reason: {msg}"
    assert "Refusing to report a successful verification of nothing" in msg


def test_zero_objects_under_an_EXPLICIT_prefix_is_a_FAILURE(tmp_path, identity):
    """🔴 Naming a host REMOVES the never-ran-here exception.

    You asked about a specific host's backups and there are none. That is an
    answer, not a no-op, and reporting it as 'nothing to do' is how a laptop
    with no off-machine copy passes a check that was pointed straight at it.
    """
    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(tmp_path / "no-store", tmp_path / "work", FakeDownloader({}),
                identity, explicit=True)
    msg = str(exc.value)
    assert "ZERO objects under" in msg, f"wrong reason: {msg}"
    assert "named explicitly" in msg, (
        f"the message does not distinguish an explicit prefix from the "
        f"never-ran-here no-op: {msg}")


def test_a_host_that_has_NEVER_run_the_backup_is_the_only_clean_no_op(
        tmp_path, identity, capsys):
    """🔴 THE ONE PERMITTED EXIT-0 EMPTY CASE, and it prints its reason.

    Same stance as `backup.py`: a host with no store has nothing to verify, and
    failing there would make this a permanently-red gate on the laptop.
    """
    verdicts = _verify(tmp_path / "no-store", tmp_path / "work",
                       FakeDownloader({}), identity)
    assert verdicts == []
    err = capsys.readouterr().err
    assert "has never run the backup" in err, err
    assert "nothing to verify" in err, err


def test_the_never_ran_no_op_EXITS_ZERO_through_the_CLI(tmp_path, identity):
    src = _dir_store(tmp_path / "objects", {})
    r = _cli("--from-dir", str(src), "--store", str(tmp_path / "gone"),
             identity=identity)
    assert r.returncode == 0, f"rc={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "has never run the backup" in r.stderr, r.stderr
    assert "verified" not in r.stdout, (
        f"a no-op printed a verification summary:\n{r.stdout}")


OTHER_PREFIX = "workbench-" + SYNTH_MACHINE_ID + "/"


def _bucket_with_only_the_other_prefix(identity, scope, tmp_path):
    """A bucket whose ONLY artifacts live under this machine's UNIT label.

    The exact live shape: the unit writes `ASIB_HOST=workbench-%m`, the bare
    hand-run looks under `nixos/`, and every object is one prefix over.
    """
    return {OTHER_PREFIX + "scope-alpha/20260822T234650Z.bundle.age":
            _make_artifact(scope, identity, tmp_path)}


def test_an_empty_prefix_NAMES_the_prefix_that_DOES_hold_artifacts(
        tmp_path, identity):
    """🔴 AN EMPTY RESULT CANNOT DISTINGUISH ITS CAUSES.

    "Zero objects here" is shared by four mechanisms and the message asserted
    three of them — never the real one, which on this host is always the fourth:
    THE RUN LOOKED UNDER THE WRONG PREFIX. The human failure is what matters:
    somebody runs this during a real disaster, reads "the backup never ran,
    stopped running, or its objects were deleted", and concludes the backups are
    gone while they sit intact one prefix over.
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    objects = _bucket_with_only_the_other_prefix(identity, scope, tmp_path)

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work", FakeDownloader(objects), identity,
                prefix="nixos", this_host="nixos",
                this_machine_id=SYNTH_MACHINE_ID, now=datetime.now(timezone.utc))
    msg = str(exc.value)
    assert OTHER_PREFIX.rstrip("/") in msg, (
        f"the message does not NAME the prefix that actually holds the "
        f"artifacts, so the absence is still ambiguous: {msg}")
    assert "THIS MACHINE'S ID" in msg, msg
    assert "--host " + OTHER_PREFIX.rstrip("/") in msg, (
        f"the message does not say how to re-run: {msg}")
    assert "NOTHING HERE SAYS YOUR BACKUPS ARE MISSING" in msg, msg
    assert "never ran, stopped running, or its objects were deleted" not in msg, (
        f"it still asserts three causes, none of which is the real one: {msg}")


def test_a_GENUINELY_EMPTY_bucket_still_says_the_original_thing(
        tmp_path, identity):
    """🔴 THE OTHER DIRECTION — or the fix has replaced one misleading message
    with another. When the bucket holds NOTHING under ANY prefix, it is NOT a
    lookup problem, and the three causes are the honest answer."""
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work", FakeDownloader({}), identity,
                prefix="nixos", this_host="nixos",
                this_machine_id=SYNTH_MACHINE_ID, now=datetime.now(timezone.utc))
    msg = str(exc.value)
    assert "NO objects under ANY prefix" in msg, f"wrong reason: {msg}"
    assert "never ran, stopped running, or its objects were deleted" in msg, msg
    assert "Refusing to report a successful verification of nothing" in msg, msg
    assert "BUT THE BUCKET DOES HOLD ARTIFACTS" not in msg, (
        f"it claims artifacts exist in an empty bucket: {msg}")


def test_a_MISSING_STORE_with_our_artifacts_elsewhere_FAILS_instead_of_exit_0(
        tmp_path, identity):
    """🔴 THE EXIT-0 VARIANT, AND IT IS THE WORST PATH THIS TOOL HAS.

    The permitted clean no-op requires `not local_scopes(store)` — i.e. THE
    STORE IS GONE, which is precisely the disaster this feature exists for. So
    the bare command during a real disaster exited **0** saying "this host has
    never run the backup, so there is nothing to verify", while all the
    artifacts were present under the unit's label. A false all-clear, delivered
    at the exact moment someone is deciding whether they have lost their data.
    """
    scope = _make_scope(tmp_path / "elsewhere", "scope-alpha", {"e.md": "x"},
                        commits=3)
    objects = _bucket_with_only_the_other_prefix(identity, scope, tmp_path)

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(tmp_path / "gone-store", tmp_path / "work",
                FakeDownloader(objects), identity, prefix="nixos",
                this_host="nixos", this_machine_id=SYNTH_MACHINE_ID,
                now=datetime.now(timezone.utc))
    msg = str(exc.value)
    assert OTHER_PREFIX.rstrip("/") in msg, msg
    assert "never run the backup" not in msg, (
        f"it still reports 'never run the backup' while this machine's "
        f"artifacts are in the bucket: {msg}")


def test_the_LAPTOP_no_op_is_PRESERVED_when_no_prefix_is_ours(
        tmp_path, identity):
    """🔴 THE PERMANENTLY-RED GATE THE NO-OP EXISTS TO PREVENT, still prevented.

    A host that has genuinely never run /analyze-service — no store, and no
    prefix in the bucket carrying ITS machine id — must still exit 0. The fix
    above must not have turned every laptop run red just because the workbench's
    artifacts exist.

    The only difference from the test above is WHOSE machine id is in the
    sibling prefix.
    """
    scope = _make_scope(tmp_path / "elsewhere", "scope-alpha", {"e.md": "x"},
                        commits=2)
    objects = _bucket_with_only_the_other_prefix(identity, scope, tmp_path)

    verdicts = _verify(tmp_path / "gone-store", tmp_path / "work",
                       FakeDownloader(objects), identity, prefix="laptop",
                       this_host="laptop",
                       this_machine_id="ffffffffffffffffffffffffffffffff",
                       now=datetime.now(timezone.utc))
    assert verdicts == [], verdicts


def test_the_no_op_still_MENTIONS_what_the_bucket_holds(tmp_path, identity,
                                                        capsys):
    """Exit 0, but not silent about the sibling: "nothing for me" and "nothing
    at all" are different facts, and only one of them is reassuring."""
    scope = _make_scope(tmp_path / "elsewhere", "scope-alpha", {"e.md": "x"},
                        commits=2)
    objects = _bucket_with_only_the_other_prefix(identity, scope, tmp_path)
    _verify(tmp_path / "gone-store", tmp_path / "work", FakeDownloader(objects),
            identity, prefix="laptop", this_host="laptop",
            this_machine_id="ffffffffffffffffffffffffffffffff",
            now=datetime.now(timezone.utc))
    err = capsys.readouterr().err
    assert "never run the backup" in err, err
    assert OTHER_PREFIX.rstrip("/") in err, (
        f"the no-op does not say what the bucket DOES hold: {err}")


@pytest.mark.parametrize("keys,exclude,want", [
    (["a/s/x.age", "b/s/x.age"], "a", ["b/"]),
    (["a/s/x.age", "a/t/y.age"], "a", []),
    (["a/s/x.age", "b/s/x.age", "c/s/x.age"], "z", ["a/", "b/", "c/"]),
    (["loose-object"], "a", []),           # no "/" -> not a host prefix
    ([], "a", []),
])
def test_the_sibling_prefix_reader_is_VALIDATED(keys, exclude, want):
    """VALIDATE THE INSTRUMENT. Every message above is a claim about this
    reader until it has been watched to work: one that returned nothing would
    make the discriminating branch unreachable and quietly restore the old
    three-causes message everywhere."""
    assert RV.sibling_prefixes(keys, exclude) == want


def test_the_directory_store_exit_PROPAGATES_rather_than_swallowing(tmp_path):
    """🔴 `__exit__(self, *_exc): return False` — pyright flags `_exc` as unused,
    which is the CONTEXT-MANAGER PROTOCOL, not a swallowed failure.

    Pinned because the audit specifically credited this file for keeping
    decrypt-vs-corruption-vs-network distinguishable, and a `return True` here
    would silently swallow every one of them. A falsy return re-raises.
    """
    d = _dir_store(tmp_path / "objects", {})
    with pytest.raises(ValueError, match="propagated"):
        with RV.DirectoryStore(d):
            raise ValueError("propagated")


def _cli_fixture(tmp_path, identity, host_prefix, *, commits=4):
    """A `--from-dir` object store plus the matching live scope.

    Returns `(objects_dir, store)`. Used by the `main()` derivation tests, which
    must go all the way to `VERIFIED AGAINST LIVE` — anything less would pass
    while the cross-check was silently suppressed, which is the exact defect.
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=commits)
    now = datetime.now(timezone.utc)
    key = B.object_key(host_prefix.rstrip("/"), "scope-alpha",
                       now - timedelta(hours=2))
    return _dir_store(tmp_path / "objects",
                      {key: _make_artifact(scope, identity, tmp_path)}), store


def test_main_DERIVES_the_machine_id_and_cross_checks(tmp_path, identity,
                                                      monkeypatch, capsys):
    """🔴 THE SEAM THAT FAILED TWICE, AND WAS UNPINNED UNTIL NOW.

    Every other test in this file INJECTS `this_host`/`this_machine_id`, so none
    of them exercises `main()`'s own derivation — which is precisely where both
    wrong versions of the foreign predicate lived. Measured by an auditor
    against a 130/130 green suite: mutating `this_machine_id=machine_id()` to
    `None` SURVIVED, and end-to-end it reproduced the v2 defect exactly
    (`--host workbench-<mid>` -> NOT CROSS-CHECKED, rc=0, false cause printed).

    So this drives the REAL `main()` in-process with `ASIB_HOST` UNSET — the
    hand-run shape — and a synthetic machine-id file. In-process rather than as
    a subprocess so `_MACHINE_ID_FILES` can be pointed at a fixture; everything
    under test (`host_label()`, `machine_id()`, the predicate) still runs for
    real. The ONLY thing that can make this pass is the machine-id half of the
    predicate, because the label half cannot match.
    """
    mid_file = tmp_path / "machine-id"
    mid_file.write_text(SYNTH_MACHINE_ID + "\n", encoding="utf-8")
    monkeypatch.setattr(RV, "_MACHINE_ID_FILES", (str(mid_file),))
    monkeypatch.delenv("ASIB_HOST", raising=False)
    monkeypatch.delenv("ACTIVITY_HOST", raising=False)
    monkeypatch.setenv("ASIB_AGE_IDENTITY", str(identity))

    host_prefix = "workbench-" + SYNTH_MACHINE_ID
    objects, store = _cli_fixture(tmp_path, identity, host_prefix, commits=4)

    # POSITIVE CONTROL on the fixture: the label half genuinely CANNOT match,
    # so a pass here can only come from the machine id.
    assert RV.normalise_prefix(B.host_label()) != RV.normalise_prefix(host_prefix)

    rc = RV.main(["--from-dir", str(objects), "--host", host_prefix,
                  "--store", str(store), "--max-lag-days", "3"])
    out = capsys.readouterr().out
    assert rc == 0, f"rc={rc}\n{out}"
    assert "VERIFIED AGAINST LIVE" in out, (
        f"main() did not cross-check: the machine-id half of the predicate is "
        f"not wired up, which is the v2 defect verbatim.\n{out}")
    assert "4 commit(s) compared" in out, out
    assert "NOT CROSS-CHECKED" not in out, out


def test_main_DERIVES_the_host_label_and_cross_checks(tmp_path, identity,
                                                      monkeypatch, capsys):
    """🔴 THE OTHER HALF, pinned the same way. Mutating `this_host=B.host_label()`
    to `''` also SURVIVED the 130-test suite.

    Here the MACHINE ID is unreadable, so only the label half can match — the
    mirror of the test above, and between them neither half can be deleted.
    """
    monkeypatch.setattr(RV, "_MACHINE_ID_FILES", (str(tmp_path / "absent"),))
    monkeypatch.setenv("ASIB_HOST", "declared-host")
    monkeypatch.setenv("ASIB_AGE_IDENTITY", str(identity))
    assert RV.machine_id() is None, "the fixture must make the id unreadable"

    objects, store = _cli_fixture(tmp_path, identity, "declared-host", commits=3)
    rc = RV.main(["--from-dir", str(objects), "--host", "declared-host",
                  "--store", str(store), "--max-lag-days", "3"])
    out = capsys.readouterr().out
    assert rc == 0, f"rc={rc}\n{out}"
    assert "VERIFIED AGAINST LIVE" in out, (
        f"main() did not cross-check via the LABEL half.\n{out}")
    assert "3 commit(s) compared" in out, out


def test_the_COVERAGE_check_keys_on_FOREIGN_not_on_the_flag(tmp_path, identity):
    """🔴 THE OTHER SITE OF THE CONSOLIDATION, unpinned until now.

    Reverting `not artifacts_are_foreign` to `not prefix_was_explicit` at the
    coverage-check call site SURVIVED the suite. Under that mutant, naming this
    host's OWN prefix silently stops running the uncovered-scope detector — a
    whole class of "a scope stopped being backed up" going unreported, quietly,
    behind a green exit. Same failure shape as the cross-check half.

    So: explicit prefix, OUR OWN label, one covered scope and one uncovered.
    """
    now = datetime.now(timezone.utc)
    store = tmp_path / "store"
    alpha = _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=3)
    forgotten = _make_scope(store, "scope-beta", {"e.md": "b"}, commits=2)
    _backdate_root_commit(forgotten, days=31)
    objects = {_key("scope-alpha", now - timedelta(hours=2)):
               _make_artifact(alpha, identity, tmp_path)}

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work", FakeDownloader(objects), identity,
                prefix=HOST, this_host=HOST, explicit=True,
                max_lag_days=3.0, now=now)
    assert "NO ARTIFACT IN THE BUCKET" in str(exc.value), (
        f"the coverage check was skipped for this host's OWN prefix just "
        f"because the flag was typed: {exc.value}")


def test_an_EXPLICIT_host_with_no_backups_gets_ITS_OWN_answer(tmp_path, identity):
    """🔴 B-1: A PREFIX THE OPERATOR TYPED IS A QUESTION, NOT A TYPO.

    This round's own fix re-created this round's own bug one branch over: with
    `ours` checked first, `--host laptop-ffff…` on the workbench answered "you
    looked in the wrong place, the bucket DOES hold artifacts" and pointed at
    the WORKBENCH's prefix — because a sibling of ours is ALWAYS present here.
    The true and important answer, "the laptop has no off-machine backups at
    all", became unreachable on the exact command SECRETS.md documents.

    🔴 THE FIXTURE MUST HAVE A SIBLING PREFIX PRESENT. The existing explicit
    test uses a totally empty bucket, where `ours` cannot fire, so it is
    structurally unable to see this.
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    objects = {"workbench-" + SYNTH_MACHINE_ID + "/scope-alpha/"
               "20260822T234650Z.bundle.age":
               _make_artifact(scope, identity, tmp_path)}

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work", FakeDownloader(objects), identity,
                prefix="laptop-ffffffffffffffffffffffffffffffff",
                this_host="workbench-" + SYNTH_MACHINE_ID,
                this_machine_id=SYNTH_MACHINE_ID, explicit=True,
                now=datetime.now(timezone.utc))
    msg = str(exc.value)
    assert "named explicitly" in msg, f"wrong branch: {msg}"
    assert "no off-machine backups at all" in msg, (
        f"the honest answer about the host actually asked about is missing: {msg}")
    assert "looked in the wrong place" not in msg, (
        f"a deliberate question was answered as if it were a typo: {msg}")
    # The sibling is still surfaced — as INFORMATION, not as the answer.
    assert "FYI" in msg and "this host's own backup" in msg, msg


def test_an_UNREADABLE_machine_id_is_reported_as_UNKNOWN_not_as_foreign(
        tmp_path, identity, capsys):
    """🔴 B-4: TWO WAYS TO BE FOREIGN, ONE HARDCODED REASON.

    `prefix_belongs_to_this_host` returns False both for "genuinely another
    machine" and for "machine id unreadable and the labels differ". On a host
    where neither candidate parses, `--host workbench-<mid>` printed a confident
    false cause and dropped every cross-check at rc=0 — the same conflation,
    one branch over.
    """
    now = datetime.now(timezone.utc)
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=3)
    objects = {_key("scope-alpha", now - timedelta(hours=2), host="workbench-x"):
               _make_artifact(scope, identity, tmp_path)}

    v = _verify(store, tmp_path / "work", FakeDownloader(objects), identity,
                prefix="workbench-x", this_host="nixos", this_machine_id=None,
                explicit=True, max_lag_days=3.0, now=now)[0]
    assert v.cross_checked is False
    reason = v.no_cross_check_reason or ""
    assert "could NOT BE READ" in reason, f"wrong reason: {reason}"
    assert "UNKNOWN, not decided" in reason, reason
    assert "belong to ANOTHER host" not in reason, (
        f"an UNKNOWN was reported as a decision: {reason}")
    assert "UNREADABLE" in RV.summarise([v]), (
        "the summary hides it; ten per-artifact reasons scroll past and the "
        "summary is what gets read")


def test_a_KNOWN_foreign_host_still_says_ANOTHER_HOST(tmp_path, identity):
    """POSITIVE CONTROL for the test above: with a readable id the reason is the
    decided one, not the UNKNOWN one. Without this, collapsing both branches
    into the UNKNOWN wording would pass."""
    now = datetime.now(timezone.utc)
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=3)
    objects = {_key("scope-alpha", now - timedelta(hours=2), host="other-host"):
               _make_artifact(scope, identity, tmp_path)}

    v = _verify(store, tmp_path / "work", FakeDownloader(objects), identity,
                prefix="other-host", this_host="nixos",
                this_machine_id=SYNTH_MACHINE_ID, explicit=True,
                max_lag_days=3.0, now=now)[0]
    reason = v.no_cross_check_reason or ""
    assert "belong to ANOTHER host" in reason, reason
    assert "could NOT BE READ" not in reason, reason
    assert "UNREADABLE" not in RV.summarise([v]), RV.summarise([v])


def test_keep_work_dir_does_NOT_warn_when_it_left_NOTHING(tmp_path, identity):
    """🔴 B-3: the mirror of the bug I fixed last round.

    Arming the warning before the run cured the silence on failures and made it
    unconditional, so a clean refusal with `--work-dir W --keep-work-dir`
    announced "decrypted artifacts and restored repositories" holding "PLAINTEXT
    history" while W was EMPTY. A warning that fires when nothing was created is
    the same class of false claim as one that stays silent when something was.
    """
    src = _dir_store(tmp_path / "objects", {})
    work = tmp_path / "work"
    r = _cli("--from-dir", str(src), "--store", str(tmp_path / "gone"),
             "--work-dir", str(work), "--keep-work-dir", identity=identity)
    assert r.returncode == 0, f"rc={r.returncode}\n{r.stderr}"
    assert list(work.iterdir()) == [], f"the run DID leave something: {list(work.iterdir())}"
    assert "--keep-work-dir left" not in r.stderr, (
        f"it warned about PLAINTEXT history in an EMPTY directory:\n{r.stderr}")


def test_the_keep_work_dir_warning_NAMES_what_is_actually_there(
        tmp_path, identity):
    """🔴 B-3, the other half: on the failing path W holds ONE ciphertext and NO
    restored repository, so "decrypted artifacts and restored repositories"
    misnamed the one thing this file is emphatic never survives.

    Asserted on the COUNT and the KIND, not on the string appearing.
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    data = _make_artifact(scope, identity, tmp_path, mangle=_flip_middle_byte)
    src = _dir_store(tmp_path / "objects", {_key("scope-alpha"): data})
    work = tmp_path / "work"

    r = _cli("--from-dir", str(src), "--prefix", HOST, "--store", str(store),
             "--max-lag-days", "100000", "--work-dir", str(work),
             "--keep-work-dir", identity=identity)
    assert r.returncode == 1, r.stderr
    left = sorted(p.name for p in work.iterdir())
    assert len(left) == 1 and left[0].endswith(".bundle.age"), left
    assert "1 age ciphertext(s)" in r.stderr, (
        f"the warning does not name what is actually there:\n{r.stderr}")
    assert "restored repositor" not in r.stderr, (
        f"it claims a restored repository that does not exist: {left}\n{r.stderr}")
    assert "PLAINTEXT history" not in r.stderr, (
        f"it calls a 0600 age ciphertext plaintext history:\n{r.stderr}")


def test_a_SELECTED_scope_with_zero_objects_is_a_FAILURE(tmp_path, identity):
    store = tmp_path / "store"
    alpha = _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=2)
    _make_scope(store, "scope-beta", {"e.md": "b"}, commits=2)
    dl = FakeDownloader({_key("scope-alpha"): _make_artifact(alpha, identity, tmp_path)})

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work", dl, identity, scope_filter="scope-beta")
    msg = str(exc.value)
    assert "has ZERO objects under" in msg, f"wrong reason: {msg}"
    assert "scope-alpha" in msg, "the message does not say which scopes DO have artifacts"


def test_a_SELECTED_scope_that_EXISTS_still_verifies(tmp_path, identity):
    """POSITIVE CONTROL for the filter: it is not simply always red, and it
    really narrows — the other scope must not be fetched."""
    store = tmp_path / "store"
    objects = {}
    for name, n in (("scope-alpha", 2), ("scope-beta", 3)):
        s = _make_scope(store, name, {"e.md": name}, commits=n)
        objects[_key(name)] = _make_artifact(s, identity, tmp_path)
    dl = FakeDownloader(objects)

    verdicts = _verify(store, tmp_path / "work", dl, identity,
                       scope_filter="scope-beta")
    assert [v.scope for v in verdicts] == ["scope-beta"]
    assert verdicts[0].commits_compared == 3
    assert dl.gets == [_key("scope-beta")], dl.gets


def _backdate_root_commit(scope: Path, days: float) -> None:
    """Rewrite the scope's history so its FIRST commit is `days` old.

    `git commit --date` sets the AUTHOR date; the coverage check reads `%ct`,
    the COMMITTER date, so `GIT_COMMITTER_DATE` is what has to move. Done by
    rebuilding the repository rather than by rewriting it, because a filter-branch
    here would leave the old objects behind and the ages would disagree.
    """
    when = datetime.now(timezone.utc) - timedelta(days=days)
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    shutil.rmtree(scope)
    scope.mkdir(parents=True)
    subprocess.run([GIT, "init", "-q", "-b", "trunk", str(scope)],
                   check=True, capture_output=True, env=_git_env())
    (scope / "e.md").write_text("backdated\n", encoding="utf-8")
    e = _git_env()
    e["GIT_AUTHOR_DATE"] = stamp
    e["GIT_COMMITTER_DATE"] = stamp
    subprocess.run([GIT, "-C", str(scope), "add", "e.md"], check=True,
                   capture_output=True, env=e)
    subprocess.run([GIT, "-C", str(scope), "commit", "-q", "-m", "backdated"],
                   check=True, capture_output=True, env=e)


def test_the_backdating_fixture_really_MOVES_the_first_commit(tmp_path):
    """VALIDATE THE INSTRUMENT. `git commit --date` moves the AUTHOR date and
    the check under test reads the COMMITTER date — a fixture that got that
    wrong would leave every scope looking brand new, and the coverage tests
    below would pass or fail for reasons unrelated to what they claim."""
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=1)
    fresh = int(_git_run(scope, "log", "--max-parents=0", "--all",
                         "--format=%ct").stdout.split()[0])
    _backdate_root_commit(scope, days=40)
    old = int(_git_run(scope, "log", "--max-parents=0", "--all",
                       "--format=%ct").stdout.split()[0])
    age_days = (fresh - old) / 86400.0
    assert 39 < age_days < 41, f"the fixture backdated by {age_days:.1f} days"


def test_a_LOCAL_scope_with_NO_ARTIFACT_at_all_is_a_FAILURE(tmp_path, identity):
    """🔴 THE FAILURE EVERY OTHER CHECK HERE IS STRUCTURALLY BLIND TO.

    Every other assertion iterates the objects the bucket HAS, so a scope that
    stopped being backed up never appears and the run reports a clean
    verification of the scopes that remain. `backup.py` cannot see it either.
    Only comparing the two directions finds it.
    """
    store = tmp_path / "store"
    now = datetime.now(timezone.utc)
    alpha = _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=3)
    forgotten = _make_scope(store, "scope-beta", {"e.md": "b"}, commits=2)
    _backdate_root_commit(forgotten, days=31)
    # A FRESH artifact for the covered scope, so the staleness guard stays
    # silent and the only thing that can speak is the coverage check.
    objects = {_key("scope-alpha", now - timedelta(hours=2)):
               _make_artifact(alpha, identity, tmp_path)}

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work", FakeDownloader(objects), identity,
                max_lag_days=3.0, now=now)
    msg = str(exc.value)
    assert "NO ARTIFACT IN THE BUCKET" in msg, f"wrong reason: {msg}"
    assert "scope-beta" in msg, f"the message does not name the scope: {msg}"
    assert "STALE ARTIFACT" not in msg, (
        f"reported as staleness — that guard reads the objects that exist and "
        f"cannot see a scope with none: {msg}")


def test_a_scope_created_TODAY_is_NOT_a_failure(tmp_path, identity):
    """🔴 THE PERMANENTLY-RED GATE THE AGE GATE PREVENTS, measured.

    A scope created this afternoon genuinely has no artifact until the next
    04:30 run. Failing on that would put this check into the state RULES.md
    warns about within a day of somebody running /analyze-service on a new repo.

    POSITIVE CONTROL for the test above: the ONLY difference between the two is
    the age of the scope's first commit.
    """
    store = tmp_path / "store"
    now = datetime.now(timezone.utc)
    alpha = _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=3)
    _make_scope(store, "scope-beta", {"e.md": "b"}, commits=2)     # brand new
    objects = {_key("scope-alpha", now - timedelta(hours=2)):
               _make_artifact(alpha, identity, tmp_path)}

    verdicts = _verify(store, tmp_path / "work", FakeDownloader(objects),
                       identity, max_lag_days=3.0, now=now)
    assert [v.scope for v in verdicts] == ["scope-alpha"]


def test_a_local_scope_with_ZERO_COMMITS_and_no_artifact_is_a_FAILURE(
        tmp_path, identity):
    """The age gate must not become an excuse: a scope with no commits has no
    first commit to be old, and `git bundle create --all` cannot make a bundle
    from it either — so it has no off-machine copy and cannot get one."""
    store = tmp_path / "store"
    now = datetime.now(timezone.utc)
    alpha = _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=3)
    empty = store / "scope-beta"
    empty.mkdir(parents=True)
    subprocess.run([GIT, "init", "-q", "-b", "trunk", str(empty)],
                   check=True, capture_output=True, env=_git_env())
    objects = {_key("scope-alpha", now - timedelta(hours=2)):
               _make_artifact(alpha, identity, tmp_path)}

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work", FakeDownloader(objects), identity,
                max_lag_days=3.0, now=now)
    msg = str(exc.value)
    assert "ZERO commits locally" in msg, f"wrong reason: {msg}"
    assert "scope-beta" in msg, msg


def test_the_coverage_check_is_SKIPPED_when_the_question_was_NARROWED(
        tmp_path, identity):
    """🔴 With `--scope` you asked about one scope; with an explicit `--host` you
    are asking about ANOTHER machine, whose scope list this host's store does
    not describe. Running the check there would fail every cross-host query on
    the strength of a store that is not the one being asked about."""
    store = tmp_path / "store"
    now = datetime.now(timezone.utc)
    alpha = _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=3)
    forgotten = _make_scope(store, "scope-beta", {"e.md": "b"}, commits=2)
    _backdate_root_commit(forgotten, days=31)
    objects = {_key("scope-alpha", now - timedelta(hours=2)):
               _make_artifact(alpha, identity, tmp_path)}

    # --scope: narrowed to one scope, which does have an artifact.
    assert _verify(store, tmp_path / "work-s", FakeDownloader(objects), identity,
                   scope_filter="scope-alpha", max_lag_days=3.0, now=now)
    # ANOTHER host's prefix: this store does not describe the question.
    other_objects = {_key("scope-alpha", now - timedelta(hours=2),
                          host="other-host"): next(iter(objects.values()))}
    assert _verify(store, tmp_path / "work-p", FakeDownloader(other_objects),
                   identity, prefix="other-host", this_host=HOST, explicit=True,
                   max_lag_days=3.0, now=now)


def test_ANOTHER_HOSTS_artifacts_are_NOT_compared_to_THIS_HOSTS_store(
        tmp_path, identity):
    """🔴 THE FALSE DATA-LOSS ALARM on the exact command SECRETS.md documents.

    `backup.py` states the two machines' stores are DIVERGENT CONTENT, and they
    share scope NAMES — `devrc` and `homelab-talos` exist on both. So
    `restore-verify.py --host <other>` restores the OTHER host's `devrc`
    artifact and would compare it against THIS host's `devrc` repository: two
    histories that were never the same one. Before this fix that produced

        CROSS-CHECK FAILED … 3 of 3 commit(s) in the RESTORED artifact do not
        exist in the live scope … history was rewritten

    — a permanently-red gate reporting DATA LOSS for a healthy backup, on the
    one command an operator runs to check the other machine.

    🔴 THE FIXTURE IS THE WHOLE POINT: the artifact is built from a store
    that is NOT the one it is cross-checked against. The sibling test
    `test_the_coverage_check_is_SKIPPED_when_the_question_was_NARROWED` cannot
    catch this — its artifacts come from the same store it compares against, so
    the comparison succeeds either way and the bug is invisible to it.
    """
    now = datetime.now(timezone.utc)
    # THIS host's store: a scope named `scope-alpha` with its own history.
    this_host = tmp_path / "store"
    _make_scope(this_host, "scope-alpha", {"e.md": "this host's content"},
                commits=4)
    # ANOTHER host's store: the SAME scope name, unrelated history.
    other_host = tmp_path / "other-host-store"
    other = _make_scope(other_host, "scope-alpha",
                        {"e.md": "other host's content"}, commits=3)
    objects = {_key("scope-alpha", now - timedelta(hours=2), host="other-host"):
               _make_artifact(other, identity, tmp_path)}

    # 🔴 A READABLE machine id, so this is a DECIDED foreign verdict and
    # not the "id unreadable, so unknown" one. The earlier version of this test
    # passed None and asserted the decided reason anyway — it was asserting a
    # measurement the fixture had not made.
    verdicts = _verify(this_host, tmp_path / "work", FakeDownloader(objects),
                       identity, prefix="other-host", this_host=HOST,
                       this_machine_id=SYNTH_MACHINE_ID,
                       explicit=True, max_lag_days=3.0, now=now)

    assert len(verdicts) == 1, verdicts
    v = verdicts[0]
    assert v.cross_checked is False, (
        "the OTHER host's artifact was cross-checked against THIS host's store "
        "— the two are divergent content sharing a scope name, so this reports "
        "data loss for a healthy backup, forever")
    assert v.commits_restored == 3, v.commits_restored
    assert "ANOTHER host" in (v.no_cross_check_reason or ""), (
        f"the reason does not say WHY it was not cross-checked, so an operator "
        f"cannot tell it from a missing store: {v.no_cross_check_reason!r}")
    assert "NOT CROSS-CHECKED" in v.line(), v.line()
    assert "VERIFIED AGAINST LIVE" not in v.line(), v.line()


def test_the_cross_host_fixture_WOULD_have_gone_red_when_compared(
        tmp_path, identity):
    """🔴 POSITIVE CONTROL — WITHOUT IT THE TEST ABOVE IS VACUOUS.

    If the two synthetic stores happened to share history, skipping the
    comparison would be indistinguishable from doing it. This drives the SAME
    fixture down the non-foreign path and watches it fail exactly the way the
    bug reported it, so the test above is measuring a suppression that matters.
    """
    now = datetime.now(timezone.utc)
    this_host = tmp_path / "store"
    _make_scope(this_host, "scope-alpha", {"e.md": "this host's content"},
                commits=4)
    other_host = tmp_path / "other-host-store"
    other = _make_scope(other_host, "scope-alpha",
                        {"e.md": "other host's content"}, commits=3)
    objects = {_key("scope-alpha", now - timedelta(hours=2)):
               _make_artifact(other, identity, tmp_path)}

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(this_host, tmp_path / "work", FakeDownloader(objects), identity,
                explicit=False, max_lag_days=3.0, now=now)
    assert "do not exist in the live scope" in str(exc.value), (
        f"the two synthetic stores are NOT divergent, so the test above would "
        f"pass whether or not the comparison was skipped: {exc.value}")


def test_naming_THIS_HOSTS_OWN_PREFIX_still_cross_checks(tmp_path, identity):
    """🔴 THE REGRESSION THE FIRST LIVE RUN CAUGHT, pinned.

    The first version of the cross-host fix keyed "foreign" on
    `prefix_was_explicit` — on whether a FLAG WAS TYPED. But
    `--prefix workbench-<machine-id>` is THIS host's own prefix, so naming it
    explicitly turned all 10 real scopes into `NOT CROSS-CHECKED …
    self-consistency only`: the verification got quietly WEAKER because of how
    the operator spelled the request. That is worse than the bug it replaced,
    because nothing goes red — it just stops checking.

    What makes artifacts foreign is whose LABEL is on them. Measured here at
    both points: same label -> cross-checked; different label -> not.
    """
    now = datetime.now(timezone.utc)
    store = tmp_path / "store"
    alpha = _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=3)
    objects = {_key("scope-alpha", now - timedelta(hours=2)):
               _make_artifact(alpha, identity, tmp_path)}

    # Explicit, and it is OUR OWN label — the live-run shape.
    own = _verify(store, tmp_path / "w-own", FakeDownloader(objects), identity,
                  prefix=HOST, this_host=HOST, explicit=True,
                  max_lag_days=3.0, now=now)[0]
    assert own.cross_checked is True, (
        "naming this host's OWN prefix suppressed the cross-check — the "
        "verification is weaker for a spelling, and nothing goes red")
    assert own.commits_compared == 3, own.commits_compared

    # NEGATIVE CONTROL, same fixture: a DIFFERENT label is foreign.
    foreign_objects = {_key("scope-alpha", now - timedelta(hours=2),
                            host="other-host"): next(iter(objects.values()))}
    far = _verify(store, tmp_path / "w-far", FakeDownloader(foreign_objects),
                  identity, prefix="other-host", this_host=HOST, explicit=True,
                  max_lag_days=3.0, now=now)[0]
    assert far.cross_checked is False, (
        "a different host's label was still compared against this store")


def test_the_DEFAULT_run_is_never_treated_as_foreign(tmp_path, identity):
    """POSITIVE CONTROL for the predicate's other end: with no flags at all the
    prefix IS `host_label()`, so a plain run must cross-check everything. A
    predicate that got this wrong would make the DEFAULT invocation — the one
    the timer would use — silently self-consistency-only."""
    now = datetime.now(timezone.utc)
    store = tmp_path / "store"
    alpha = _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=4)
    objects = {_key("scope-alpha", now - timedelta(hours=2)):
               _make_artifact(alpha, identity, tmp_path)}
    v = _verify(store, tmp_path / "work", FakeDownloader(objects), identity,
                prefix=HOST, this_host=HOST, explicit=False,
                max_lag_days=3.0, now=now)[0]
    assert v.cross_checked is True and v.commits_compared == 4


def test_the_foreign_predicate_tolerates_a_TRAILING_SLASH_difference(
        tmp_path, identity):
    """`--prefix workbench-x/` and `--prefix workbench-x` name the same host.
    Comparing the raw strings would call one of them foreign, which is the
    silent-weakening failure again in a different spelling."""
    now = datetime.now(timezone.utc)
    store = tmp_path / "store"
    alpha = _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=2)
    objects = {_key("scope-alpha", now - timedelta(hours=2)):
               _make_artifact(alpha, identity, tmp_path)}
    v = _verify(store, tmp_path / "work", FakeDownloader(objects), identity,
                prefix=HOST + "/", this_host=HOST, explicit=True,
                max_lag_days=3.0, now=now)[0]
    assert v.cross_checked is True, (
        "a trailing slash made this host's own artifacts look foreign")


@pytest.mark.parametrize("prefix,this_host,mid,ours,why", [
    # The unit's shape: ASIB_HOST is set, so the labels match outright.
    ("workbench-" + SYNTH_MACHINE_ID, "workbench-" + SYNTH_MACHINE_ID,
     SYNTH_MACHINE_ID, True, "labels match under the unit"),
    # 🔴 THE HAND-RUN, which two earlier versions got wrong: ASIB_HOST is
    # unset so host_label() degrades to the shared hostname, and only the
    # machine id in the KEY can still identify the machine.
    ("workbench-" + SYNTH_MACHINE_ID, "nixos", SYNTH_MACHINE_ID, True,
     "machine id present in the prefix"),
    # The other host, from a hand-run: different id, so foreign.
    ("laptop-ffffffffffffffffffffffffffffffff", "nixos", SYNTH_MACHINE_ID,
     False, "a different machine's id"),
    # No machine id readable and labels differ -> foreign (fails SAFE).
    ("laptop-ffffffffffffffffffffffffffffffff", "nixos", None, False,
     "cannot tell, so it declines to compare"),
    # A trailing slash is a spelling, not a different host.
    ("workbench-" + SYNTH_MACHINE_ID + "/", "workbench-" + SYNTH_MACHINE_ID,
     None, True, "trailing slash normalised"),
])
def test_which_prefixes_count_as_THIS_HOSTS(prefix, this_host, mid, ours, why):
    """🔴 THE PREDICATE, AT FIVE POINTS — it took two live runs to get right.

    Both wrong versions failed the same way: quietly doing LESS verification
    while still exiting 0.

      v1  keyed on `prefix_was_explicit`  -> naming our OWN prefix suppressed
                                             all 10 real scopes.
      v2  label comparison alone          -> STILL suppressed all 10, because
                                             the unit writes `workbench-%m`
                                             while a hand-run's `host_label()`
                                             is `nixos`.

    The rows below are the two spellings of "ours", the two of "theirs", and the
    unreadable-id case, so neither wrong version can pass this table.
    """
    got = RV.prefix_belongs_to_this_host(prefix, this_host, mid)
    assert got is ours, f"{prefix!r} vs {this_host!r}/{mid!r}: expected {ours} ({why})"


def test_the_machine_id_reader_accepts_only_a_REAL_id(tmp_path, monkeypatch):
    """VALIDATE THE INSTRUMENT, through the REAL function.

    A reader that returned whatever a file happened to hold would make
    `prefix_belongs_to_this_host` answer True for any prefix containing that
    junk — an error in the FALSE DATA-LOSS direction, which is the one that
    gets someone to act destructively. So the shape check is exercised, not
    described: `_MACHINE_ID_FILES` is a module-level seam and the test points it
    at synthetic files.
    """
    good = tmp_path / "good"
    good.write_text(SYNTH_MACHINE_ID + "\n", encoding="utf-8")
    bad = tmp_path / "bad"
    bad.write_text("not-a-machine-id\n", encoding="utf-8")
    missing = tmp_path / "missing"

    # POSITIVE CONTROL: it reads a well-formed id.
    monkeypatch.setattr(RV, "_MACHINE_ID_FILES", (str(good),))
    assert RV.machine_id() == SYNTH_MACHINE_ID

    # NEGATIVE CONTROLS, each on its own: malformed, and absent.
    monkeypatch.setattr(RV, "_MACHINE_ID_FILES", (str(bad),))
    assert RV.machine_id() is None, "a malformed machine id was accepted"
    monkeypatch.setattr(RV, "_MACHINE_ID_FILES", (str(missing),))
    assert RV.machine_id() is None, "a missing file did not read as unknown"

    # \U0001f534 FALLTHROUGH, AT BOTH KINDS OF "UNUSABLE" — they are different
    # code paths and a mutation sweep proved it: an earlier version of this test
    # only fed a MALFORMED first candidate, which exercises the shape check's
    # fallthrough. A mutant that turned the `except OSError: continue` into
    # `return None` — i.e. gave up on an ABSENT first candidate — SURVIVED,
    # because nothing here ever made the first file missing. `/etc/machine-id`
    # absent with the dbus copy present is the realistic shape on non-systemd
    # hosts, and it is exactly the case that was uncovered.
    monkeypatch.setattr(RV, "_MACHINE_ID_FILES", (str(bad), str(good)))
    assert RV.machine_id() == SYNTH_MACHINE_ID, (
        "a MALFORMED first candidate stopped the search instead of falling "
        "through to the next")
    monkeypatch.setattr(RV, "_MACHINE_ID_FILES", (str(missing), str(good)))
    assert RV.machine_id() == SYNTH_MACHINE_ID, (
        "an ABSENT first candidate stopped the search instead of falling "
        "through — /etc/machine-id is absent on some systems and the dbus copy "
        "is the whole reason there is a second candidate")



def test_naming_THIS_HOSTS_OWN_PREFIX_by_MACHINE_ID_still_cross_checks(
        tmp_path, identity):
    """🔴 THE LIVE SHAPE, end to end: `--prefix workbench-<machine-id>` with
    `ASIB_HOST` unset, which is every hand-run and is how the two earlier
    versions were caught. It must cross-check, not degrade to
    self-consistency-only."""
    now = datetime.now(timezone.utc)
    host_prefix = "workbench-" + SYNTH_MACHINE_ID
    store = tmp_path / "store"
    alpha = _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=5)
    objects = {_key("scope-alpha", now - timedelta(hours=2), host=host_prefix):
               _make_artifact(alpha, identity, tmp_path)}

    v = _verify(store, tmp_path / "work", FakeDownloader(objects), identity,
                prefix=host_prefix, this_host="nixos",
                this_machine_id=SYNTH_MACHINE_ID, explicit=True,
                max_lag_days=3.0, now=now)[0]
    assert v.cross_checked is True, (
        "a hand-run naming this host's own prefix degraded to "
        "self-consistency-only — less verification, still exit 0")
    assert v.commits_compared == 5, v.commits_compared


def test_a_SCOPE_FILTER_alone_still_cross_checks(tmp_path, identity):
    """🔴 THE PREDICATES ARE DELIBERATELY NOT THE SAME BOOLEAN.

    Narrowing to one scope of THIS host's artifacts leaves the comparison
    perfectly valid — only an explicit PREFIX means "another machine". A fix
    that suppressed the cross-check for `--scope` too would silently turn every
    single-scope run into self-consistency-only, which reads as success.
    """
    now = datetime.now(timezone.utc)
    store = tmp_path / "store"
    alpha = _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=3)
    objects = {_key("scope-alpha", now - timedelta(hours=2)):
               _make_artifact(alpha, identity, tmp_path)}

    v = _verify(store, tmp_path / "work", FakeDownloader(objects), identity,
                scope_filter="scope-alpha", max_lag_days=3.0, now=now)[0]
    assert v.cross_checked is True, (
        "--scope suppressed the live comparison; only --host/--prefix may")
    assert v.commits_compared == 3, v.commits_compared


def test_the_two_NO_CROSS_CHECK_reasons_are_DISTINGUISHABLE(tmp_path, identity):
    """"No live scope here" and "these are another machine's" are different
    findings. An operator reading NOT CROSS-CHECKED must be able to tell which,
    because one of them is the disaster case and the other is routine."""
    now = datetime.now(timezone.utc)
    elsewhere = _make_scope(tmp_path / "elsewhere", "scope-alpha", {"e.md": "x"},
                            commits=2)
    data = _make_artifact(elsewhere, identity, tmp_path)
    key_own = _key("scope-alpha", now - timedelta(hours=2))
    key_foreign = _key("scope-alpha", now - timedelta(hours=2), host="other-host")

    empty_store = tmp_path / "store"
    empty_store.mkdir()
    missing = _verify(empty_store, tmp_path / "w1",
                      FakeDownloader({key_own: data}), identity,
                      max_lag_days=3.0, now=now)[0]
    foreign = _verify(empty_store, tmp_path / "w2",
                      FakeDownloader({key_foreign: data}), identity,
                      prefix="other-host", this_host=HOST,
                      this_machine_id=SYNTH_MACHINE_ID, explicit=True,
                      max_lag_days=3.0, now=now)[0]

    assert "no live scope repository at" in (missing.no_cross_check_reason or "")
    assert "ANOTHER host" in (foreign.no_cross_check_reason or "")
    assert missing.no_cross_check_reason != foreign.no_cross_check_reason, (
        "the two findings share a sentence, so the disaster case and the "
        "routine one are indistinguishable in the output")


def test_the_cross_check_target_helper_is_the_SINGLE_source_of_the_rule():
    """🔴 ONE RULE, ONE PLACE. The predicate was open-coded at two sites
    and was wrong at one; a third site must not be able to regrow it.

    Pins that `run()` asks the helper rather than calling `live_scope_path`
    directly — the exact shape of the bug.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    body = src.split("def run(", 1)[1]
    assert "cross_check_target(" in body, (
        "run() no longer routes through cross_check_target()")
    assert "live_scope_path(store, scope)" not in body, (
        "run() calls live_scope_path() directly again — that is the open-coded "
        "site that skipped the foreign-artifact check")


def test_latest_only_and_all_CANNOT_be_combined(tmp_path, identity):
    """`--latest-only` names the default; its ONE real behaviour is refusing
    `--all`, so that is what gets pinned. A flag whose only effect is untested
    is a flag nobody can tell is dead."""
    src = _dir_store(tmp_path / "objects", {})
    both = _cli("--latest-only", "--all", "--from-dir", str(src),
                "--store", str(tmp_path / "gone"), identity=identity)
    assert both.returncode == 2, f"rc={both.returncode}\n{both.stderr}"
    assert "not allowed with argument" in both.stderr, both.stderr

    # POSITIVE CONTROL: alone it is accepted and behaves as the default.
    alone = _cli("--latest-only", "--from-dir", str(src),
                 "--store", str(tmp_path / "gone"), identity=identity)
    assert alone.returncode == 0, f"rc={alone.returncode}\n{alone.stderr}"


# --------------------------------------------------------------------------- #
# 7. 🔴 STALENESS — an artifact that stopped advancing
# --------------------------------------------------------------------------- #
def test_an_artifact_PAST_max_lag_days_FAILS(tmp_path, identity):
    """🔴 A bucket that stopped advancing looks identical to a healthy one from
    every angle except this one. The artifact still decrypts, still restores,
    still cross-checks — and is a week out of date."""
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    data = _make_artifact(scope, identity, tmp_path)
    old = NOW - timedelta(days=9, hours=7)

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work",
                FakeDownloader({_key("scope-alpha", old): data}), identity,
                max_lag_days=3.0)
    msg = str(exc.value)
    assert "STALE ARTIFACT" in msg, f"wrong reason: {msg}"
    assert "9.29 days old" in msg, f"the age is not reported accurately: {msg}"


@pytest.mark.parametrize("hours,stale", [
    (17, False),      # well inside — a healthy daily backup
    (67, False),      # 2.79d, just under a 3d threshold
    (72, False),      # EXACTLY 3d — the only point that separates `>` from `>=`
    (77, True),       # 3.21d, just over it
    (223, True),      # 9.29d — a timer that stopped
])
def test_the_staleness_threshold_is_measured_at_FIVE_points(
        tmp_path, identity, hours, stale):
    """🔴 MEASURED ON BOTH SIDES, AT THE BOUNDARY, AND EXACTLY ON IT.

    One measurement is not a general claim. The 67h/77h pair straddles the
    threshold by four hours in each direction, which kills a wrong UNIT
    (seconds compared against days) and a grossly wrong threshold.

    🔴 IT DOES NOT, HOWEVER, SEPARATE `>` FROM `>=` — and an earlier version of
    this docstring claimed it did. Both operators agree everywhere except at the
    boundary itself, so only a point EXACTLY at 72h can tell them apart: `>`
    leaves it fresh, `>=` calls it stale. That case now exists and expects
    fresh, which is also the right policy — an artifact exactly at the limit has
    not yet exceeded it.

    Claiming a discrimination the cases cannot deliver is the same failure this
    whole file argues against, one level up: a test docstring that reads as
    coverage while providing none.
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    data = _make_artifact(scope, identity, tmp_path)
    stamp = NOW - timedelta(hours=hours)
    dl = FakeDownloader({_key("scope-alpha", stamp): data})

    if stale:
        with pytest.raises(RV.RestoreVerifyError) as exc:
            _verify(store, tmp_path / "work", dl, identity, max_lag_days=3.0)
        assert "STALE ARTIFACT" in str(exc.value), f"wrong reason: {exc.value}"
    else:
        v = _verify(store, tmp_path / "work", dl, identity, max_lag_days=3.0)[0]
        assert v.cross_checked is True
        assert abs(v.age_days - hours / 24.0) < 0.01, v.age_days


def test_staleness_is_judged_on_the_NEWEST_artifact_only(tmp_path, identity):
    """🔴 Under `--all` every retained object is verified for restorability.
    Calling the old ones stale would make the flag permanently red by design —
    retention keeps 14 days on purpose."""
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    data = _make_artifact(scope, identity, tmp_path)
    objects = {_key("scope-alpha", NOW - timedelta(days=d)): data
               for d in (11, 6, 0)}
    assert len(objects) == 3

    verdicts = _verify(store, tmp_path / "work", FakeDownloader(objects), identity,
                       verify_all=True, max_lag_days=3.0)
    assert len(verdicts) == 3, [v.key for v in verdicts]
    assert all(v.cross_checked for v in verdicts)


def test_latest_only_is_the_DEFAULT_and_picks_the_NEWEST_key(tmp_path, identity):
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    data = _make_artifact(scope, identity, tmp_path)
    newest = _key("scope-alpha", NOW - timedelta(hours=4))
    objects = {_key("scope-alpha", NOW - timedelta(days=d)): data for d in (9, 5, 2)}
    objects[newest] = data
    dl = FakeDownloader(objects)

    verdicts = _verify(store, tmp_path / "work", dl, identity, max_lag_days=3.0)
    assert [v.key for v in verdicts] == [newest], [v.key for v in verdicts]
    assert dl.gets == [newest], dl.gets


# --------------------------------------------------------------------------- #
# 8. 🔴 READ-ONLY PROOF — the store must come out byte-identical
# --------------------------------------------------------------------------- #
def test_a_full_verify_run_leaves_the_store_BYTE_IDENTICAL(tmp_path, identity):
    """🔴 The store is the ONLY copy of client-confidential content that is not
    re-derivable. A verifier is a reader; this is the measurement that says so."""
    store = tmp_path / "store"
    objects = {}
    for name, n in (("scope-alpha", 3), ("scope-beta", 1), ("scope-gamma", 6)):
        s = _make_scope(store, name, {"e.md": name}, commits=n)
        objects[_key(name)] = _make_artifact(s, identity, tmp_path)
    # 🔴 A FOURTH scope with NO artifact, so the run also walks the coverage
    # check's `git log` against the store. Without it that read is outside the
    # byte-identity measurement and the read-only claim would be narrower than
    # the code — `now` is deliberately the fixture's past, so the age gate stays
    # silent and the run still succeeds while the READ still happens.
    _make_scope(store, "scope-delta", {"e.md": "delta"}, commits=2)

    before = _manifest(store)
    assert before, "manifest is empty — this test would pass vacuously"

    verdicts = _verify(store, tmp_path / "work", FakeDownloader(objects), identity)
    assert len(verdicts) == 3

    after = _manifest(store)
    assert after == before, (
        "the verifier MODIFIED the store. Differing paths: "
        f"{sorted(set(before) ^ set(after)) or [k for k in before if before[k] != after.get(k)]}")


def test_no_scope_gains_a_remote_during_a_verify_run(tmp_path, identity):
    """🔴 The invariant every scope README states. `git clone <bundle>` sets
    `origin` to the bundle path — the restore happens in a THROWAWAY directory
    for exactly that reason, and this is what proves it never leaked back."""
    store = tmp_path / "store"
    objects = {}
    for name in ("scope-alpha", "scope-beta"):
        s = _make_scope(store, name, {"e.md": name}, commits=2)
        objects[_key(name)] = _make_artifact(s, identity, tmp_path)
    assert all(B.scope_remotes(store / n) == []
               for n in ("scope-alpha", "scope-beta"))

    _verify(store, tmp_path / "work", FakeDownloader(objects), identity)

    for name in ("scope-alpha", "scope-beta"):
        assert B.scope_remotes(store / name) == [], (
            f"{name} gained a git remote — the no-remote invariant is broken")


def test_no_restored_repository_lands_inside_the_store(tmp_path, identity):
    store = tmp_path / "store"
    s = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    work = tmp_path / "work"
    _verify(store, work, FakeDownloader({_key("scope-alpha"):
                                         _make_artifact(s, identity, tmp_path)}),
            identity)
    assert not list(store.glob("**/*.restored.git")), (
        "a restored clone landed in the store")
    assert not list(work.glob("*.restored.git")), (
        "the restored clone was not cleaned up; it holds a full copy of the "
        "scope's history in plaintext")


def test_the_read_only_subcommand_allowlist_is_pinned_EXACTLY():
    """An EXACT set, not a subset.

    🔴 `bundle` is NOT in it and that is the point: `git bundle verify` passes a
    byte-flipped bundle at rc=0, so a "simplification" that reaches for it fails
    at the call rather than in review. Widening this set is how a write verb
    arrives in a script whose whole premise is that it cannot write, and it has
    to be a visible diff against this line.
    """
    assert RV._READ_ONLY_SUBCOMMANDS == frozenset(
        {"rev-list", "rev-parse", "for-each-ref", "cat-file", "merge-base",
         "log", "remote"})
    assert RV._ALLOWED_SUBVERBS == {"remote": frozenset({None})}
    assert set(RV._ALLOWED_SUBVERBS) <= RV._READ_ONLY_SUBCOMMANDS, (
        "a subverb policy names a verb the outer allowlist does not permit — it "
        "can never be reached and reads as coverage while providing none")


@pytest.mark.parametrize("argv", [
    ("config", "--local", "user.name", "x"),
    ("gc", "--prune=all"),
    ("commit", "--allow-empty", "-m", "x"),
    ("push", "origin", "trunk"),
    ("fetch", "origin"),
    ("clone", "--mirror", "x.bundle", "y.git"),
    ("reflog", "expire", "--all"),
    ("bundle", "create", "x.bundle", "--all"),
])
def test_git_refuses_a_write_or_forbidden_subcommand(tmp_path, argv):
    """NEGATIVE CONTROL, watched to fail with THIS guard's own message, and on
    REAL git command lines only — a parametrised string nobody would type can
    only prove the allowlist rejects nonsense."""
    repo = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=1)
    before = _manifest(repo)
    with pytest.raises(RV.RestoreVerifyError) as exc:
        RV._git(repo, *argv)
    assert "READ-ONLY to this script" in str(exc.value), (
        f"{argv[0]} was refused by some OTHER guard — this control is green for "
        f"the wrong reason")
    assert _manifest(repo) == before, "the repository changed anyway"


@pytest.mark.parametrize("argv", [
    ("remote", "add", "exfil", "file:///dev/null"),
    ("remote", "set-url", "origin", "file:///dev/null"),
])
def test_git_refuses_a_WRITING_SUBVERB_of_the_allowlisted_remote_verb(tmp_path, argv):
    """🔴 THE GAP backup.py measured: the verb reads, the SUBVERB writes.

    `git remote` lists; `git remote add` writes `.git/config`. Asserted on the
    SUBVERB guard's own message — the verb guard has a different one, and a
    control that fired on the verb guard would stay green with the subverb
    policy deleted.
    """
    repo = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=1)
    before = _manifest(repo)
    with pytest.raises(RV.RestoreVerifyError) as exc:
        RV._git(repo, *argv)
    assert "is not a READ-ONLY mode of" in str(exc.value), (
        f"`git {' '.join(argv)}` was refused by the VERB guard, not the subverb "
        f"guard: {exc.value}")
    assert _manifest(repo) == before, "the repository's config changed anyway"


@pytest.mark.parametrize("argv", [
    ("remote",),
    ("remote", "-v"),
    ("rev-list", "--count", "--all"),
    ("for-each-ref", "--format=%(refname)"),
])
def test_the_guard_still_ALLOWS_the_read_only_forms(tmp_path, argv):
    """POSITIVE CONTROL: the guard is not simply always red. Without this,
    tightening the allowlist to the empty set would satisfy every negative
    control in this file and break the verifier."""
    repo = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=1)
    p = RV._git(repo, *argv)          # must NOT raise
    assert isinstance(p, subprocess.CompletedProcess)
    assert p.returncode == 0, f"git itself rejected {argv}: {p.stderr}"


def test_the_scratch_helper_is_the_SHARED_one_with_its_store_refusal(tmp_path):
    """🔴 ONE RULE, ONE PLACE. `_git_scratch` may run WRITING subcommands, so the
    only thing keeping it safe is where it is aimed. This verifier reuses
    `backup.py`'s — a second copy of a guard is a second thing to get wrong, and
    the copy nobody exercises is the broken one.

    Watched to refuse at the store root AND at a scope inside it (a boundary and
    an interior point), through the verifier's own wrapper.
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=1)
    for cwd in (store, scope):
        with pytest.raises(B.BackupError) as exc:
            RV._scratch(cwd, "clone", "--mirror", str(tmp_path / "x.bundle"),
                        str(tmp_path / "out.git"), forbidden=store)
        assert "inside the /analyze-service index store" in str(exc.value), (
            f"cwd={cwd} was refused by some OTHER guard: {exc.value}")


def test_the_verify_run_aims_the_scratch_helper_AWAY_from_the_store(
        tmp_path, identity, monkeypatch):
    """🔴 REACHABILITY: prove the `forbidden` argument the run passes is the REAL
    store root, not a decoy that could never match.

    A guard is only as good as the value it is handed. This intercepts every
    `_git_scratch` call the run makes and pins what it was aimed at — a mutation
    passing `forbidden=Path("/nonexistent")` would leave the refusal test above
    green while the run itself was unguarded.
    """
    store = tmp_path / "store"
    s = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    data = _make_artifact(s, identity, tmp_path)

    seen: list[tuple[Path, Path]] = []
    real = B._git_scratch

    def spy(cwd, *args, forbidden):
        seen.append((Path(cwd), Path(forbidden)))
        return real(cwd, *args, forbidden=forbidden)

    monkeypatch.setattr(B, "_git_scratch", spy)
    _verify(store, tmp_path / "work", FakeDownloader({_key("scope-alpha"): data}),
            identity)

    assert seen, "the run made no scratch-git calls — this test measured nothing"
    assert {f for _c, f in seen} == {store}, (
        f"the run aimed _git_scratch at {sorted({str(f) for _c, f in seen})}, "
        f"not at the store root {store}")
    for cwd, _f in seen:
        assert store not in cwd.resolve().parents and cwd.resolve() != store.resolve(), (
            f"a scratch git ran at {cwd}, inside the store")


# --------------------------------------------------------------------------- #
# 9. 🔴 THE DECRYPTED BUNDLE — a full unencrypted copy of a confidential scope
# --------------------------------------------------------------------------- #
def test_no_PLAINTEXT_bundle_survives_a_SUCCESSFUL_run(tmp_path, identity):
    store = tmp_path / "store"
    s = _make_scope(store, "scope-alpha", {"e.md": "canary-plaintext"}, commits=3)
    work = tmp_path / "work"
    _verify(store, work, FakeDownloader({_key("scope-alpha"):
                                         _make_artifact(s, identity, tmp_path)}),
            identity)

    leftovers = sorted(p.name for p in work.rglob("*") if p.is_file())
    assert leftovers == [], f"artifacts left behind: {leftovers}"


def test_no_PLAINTEXT_bundle_survives_a_FAILED_run(tmp_path, identity):
    """🔴 THE PATH THAT MATTERS MOST, and the one a success-path unlink misses.

    A failed restore is exactly when a plaintext copy is most likely to be left
    behind and least likely to be noticed. Reached with a genuinely corrupted
    artifact — the bundle really is written to disk and really does fail.
    """
    store = tmp_path / "store"
    s = _make_scope(store, "scope-alpha", {"e.md": "canary-plaintext"}, commits=3)
    data = _make_artifact(s, identity, tmp_path, mangle=_flip_middle_byte)
    work = tmp_path / "work"

    with pytest.raises(RV.RestoreVerifyError):
        _verify(store, work, FakeDownloader({_key("scope-alpha"): data}), identity)

    leftovers = sorted(p.name for p in work.rglob("*") if p.is_file())
    assert leftovers == [], f"a FAILED run left artifacts behind: {leftovers}"


def test_the_plaintext_bundle_REALLY_EXISTS_during_the_run(tmp_path, identity,
                                                           monkeypatch):
    """🔴 POSITIVE CONTROL for both tests above: they would pass identically if
    the verifier never wrote a bundle at all — a `find` for something that is
    never created finds nothing, and reads as hygiene.

    Watched from inside `restore()`, at the moment the file is on disk — and the
    copy taken there is CLONED afterwards, because a git bundle is a packfile:
    the entry text is zlib-compressed inside it and a raw substring search finds
    nothing. Grepping for it would have "proved" the file was harmless.
    """
    store = tmp_path / "store"
    s = _make_scope(store, "scope-alpha", {"e.md": "canary-plaintext"}, commits=3)
    # 🔴 TYPED LISTS, not a `dict[str, object]`. The earlier version indexed an
    # `object`-typed dict, so `Path(seen["path"])` was a claim about a value
    # nothing had checked the type of — and an empty dict would have raised a
    # KeyError that reads like a harness bug rather than "restore() never ran".
    # Emptiness is now its own assertion with its own sentence.
    bundle_paths: list[Path] = []
    bundle_sizes: list[int] = []
    stolen = tmp_path / "stolen.bundle"
    real = RV.restore

    def spy(bundle, dest, work_dir, forbidden):
        b = Path(bundle)
        assert b.is_file(), "restore() was handed a bundle path that is not a file"
        bundle_paths.append(b)
        bundle_sizes.append(b.stat().st_size)
        stolen.write_bytes(b.read_bytes())
        return real(bundle, dest, work_dir, forbidden=forbidden)

    monkeypatch.setattr(RV, "restore", spy)
    _verify(store, tmp_path / "work",
            FakeDownloader({_key("scope-alpha"): _make_artifact(s, identity, tmp_path)}),
            identity)

    assert bundle_paths, (
        "restore() was never called, so no plaintext bundle was ever written — "
        "the hygiene tests above are guarding something that never happens")
    assert bundle_sizes[0] > 0, "the plaintext bundle on disk was empty"
    assert not bundle_paths[0].exists(), "the plaintext bundle survived the run"

    # 🔴 What made that file dangerous: it is a COMPLETE, UNENCRYPTED copy of
    # the scope. Cloning the stolen copy is the only way to say so — and it
    # needs no key, which is the whole point.
    out = tmp_path / "from-stolen"
    c = subprocess.run([GIT, "clone", "-q", str(stolen), str(out)],
                       capture_output=True, text=True, env=_git_env())
    assert c.returncode == 0, f"the on-disk bundle did not clone: {c.stderr}"
    assert "canary-plaintext" in (out / "e.md").read_text(encoding="utf-8"), (
        "the bundle on disk did not carry the scope's content — the hygiene "
        "tests above are guarding something that never happens")


def test_the_work_dir_and_its_files_are_not_readable_by_anyone_else(
        tmp_path, identity, monkeypatch):
    """0700 directory, 0600 files. Both would be 0644/0755 under the operator's
    umask, on a real disk, holding a client-confidential scope."""
    store = tmp_path / "store"
    s = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    work = tmp_path / "work"
    work.mkdir(mode=0o777)
    os.chmod(work, 0o777)
    assert (work.stat().st_mode & 0o077) != 0, "fixture is not loose; nothing to tighten"

    modes: dict[str, int] = {}
    real = RV.restore

    def spy(bundle, dest, work_dir, forbidden):
        modes["bundle"] = Path(bundle).stat().st_mode & 0o777
        modes["cipher"] = Path(str(bundle) + ".age").stat().st_mode & 0o777
        modes["dir"] = Path(work_dir).stat().st_mode & 0o777
        return real(bundle, dest, work_dir, forbidden=forbidden)

    monkeypatch.setattr(RV, "restore", spy)
    _verify(store, work, FakeDownloader({_key("scope-alpha"):
                                         _make_artifact(s, identity, tmp_path)}),
            identity)

    assert modes["dir"] & 0o077 == 0, f"work dir is {oct(modes['dir'])}, not 0700"
    assert modes["bundle"] & 0o077 == 0, f"bundle is {oct(modes['bundle'])}, not 0600"
    assert modes["cipher"] & 0o077 == 0, f"ciphertext is {oct(modes['cipher'])}, not 0600"


def test_the_RESTORED_REPOSITORY_is_not_readable_by_anyone_else(
        tmp_path, identity):
    """🔴 THE OTHER COMPLETE PLAINTEXT COPY, and it was created at the umask.

    The decrypted bundle is unlinked immediately; the restored repository is the
    one that survives under `--keep-work-dir`, in a directory the operator
    chose. MEASURED under umask 022 before this fix: 0755 directories, 0644
    files, a 0444 packfile — 31 entries readable by group and other.

    The 0700 work dir bounded it, so this was never an active leak. It is fixed
    because the module docstring says "everything created here is mode 0600
    inside a 0700 directory", and the sibling test's TITLE said "the work dir
    and ITS FILES" while its body inspected only the bundle and the ciphertext —
    a name wider than the body, which is why this survived.
    """
    store = tmp_path / "store"
    s = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    work = tmp_path / "work"
    _verify(store, work, FakeDownloader({_key("scope-alpha"):
                                         _make_artifact(s, identity, tmp_path)}),
            identity, keep_work_dir=True)

    restored = [p for p in work.iterdir() if p.name.endswith(".restored.git")]
    assert len(restored) == 1, f"no restored repository was kept: {list(work.iterdir())}"

    loose = []
    checked = 0
    for p in [restored[0], *restored[0].rglob("*")]:
        if p.is_symlink():
            continue
        checked += 1
        if p.stat().st_mode & 0o077:
            loose.append(f"{p.relative_to(work)} {oct(p.stat().st_mode & 0o777)}")
    assert checked > 10, (
        f"only {checked} entries walked — a restored git repository has dozens, "
        f"so this assertion is not looking at what it claims")
    assert loose == [], (
        f"{len(loose)} of {checked} entries in the restored repository are "
        f"readable by group or other: {loose[:8]}")


def test_the_restored_tree_tightener_can_SEE_a_loose_tree(tmp_path):
    """POSITIVE CONTROL for the walk above: it must be able to report a loose
    entry. A checker that always finds zero is indistinguishable from one wired
    to nothing — and the umask on the gate host is not something this test may
    assume."""
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    f = root / "sub" / "loose"
    f.write_text("x", encoding="utf-8")
    os.chmod(root, 0o755)
    os.chmod(root / "sub", 0o755)
    os.chmod(f, 0o644)

    loose = [p for p in [root, *root.rglob("*")] if p.stat().st_mode & 0o077]
    assert len(loose) == 3, f"the walk cannot see loose modes: {loose}"

    RV._tighten_tree(root)
    still = [p for p in [root, *root.rglob("*")] if p.stat().st_mode & 0o077]
    assert still == [], f"_tighten_tree left {still} readable"


def test_keep_work_dir_WARNS_on_a_FAILING_run_not_just_a_successful_one(
        tmp_path, identity):
    """🔴 THE PATH THE FLAG EXISTS FOR. You keep the work dir BECAUSE
    something went wrong and you want to look at it.

    The warning used to print after `summarise()` — the success path only — so a
    failing run left restored plaintext history on disk and said nothing, while
    the module docstring claimed the flag "prints what it left behind".
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    data = _make_artifact(scope, identity, tmp_path, mangle=_flip_middle_byte)
    src = _dir_store(tmp_path / "objects", {_key("scope-alpha"): data})
    work = tmp_path / "work"

    r = _cli("--from-dir", str(src), "--prefix", HOST, "--store", str(store),
             "--max-lag-days", "100000", "--work-dir", str(work),
             "--keep-work-dir", identity=identity)

    assert r.returncode == 1, f"the corrupted artifact did not fail: {r.stderr}"
    assert "--keep-work-dir left" in r.stderr, (
        f"a FAILING run left the work dir behind and never said so:\n{r.stderr}")
    # 🔴 This assertion used to read `"PLAINTEXT history" in r.stderr` —
    # and it PASSED, certifying a sentence that was false on the very run it
    # tests: this path leaves one 0600 age ciphertext and NO restored
    # repository. A test that asserts a string appears cannot tell a true
    # message from a confident wrong one. What is actually there is pinned by
    # test_the_keep_work_dir_warning_NAMES_what_is_actually_there; this test
    # owns only "it speaks at all on a failure".
    assert "age ciphertext(s)" in r.stderr, r.stderr


def test_keep_work_dir_WARNS_on_a_SUCCESSFUL_run_too(tmp_path, identity):
    """POSITIVE CONTROL for the test above: moving the warning into a `finally`
    must not have moved it OFF the success path."""
    now = datetime.now(timezone.utc)
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=3)
    src = _dir_store(tmp_path / "objects",
                     {_key("scope-alpha", now - timedelta(hours=2)):
                      _make_artifact(scope, identity, tmp_path)})

    r = _cli("--from-dir", str(src), "--prefix", HOST, "--store", str(store),
             "--work-dir", str(tmp_path / "work"), "--keep-work-dir",
             identity=identity)
    assert r.returncode == 0, f"rc={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "--keep-work-dir left" in r.stderr, r.stderr


def test_the_keep_work_dir_REFUSAL_does_not_warn_about_a_dir_it_never_made(
        tmp_path, identity):
    """The warning is armed only once a work dir actually exists. `--print-plan`
    and the `--keep-work-dir requires --work-dir` refusal both leave nothing
    behind, so a warning there would be a false claim in the other direction."""
    src = _dir_store(tmp_path / "objects", {})
    r = _cli("--keep-work-dir", "--from-dir", str(src),
             "--store", str(tmp_path / "gone"), identity=identity)
    assert r.returncode == 1
    assert "--keep-work-dir requires --work-dir" in r.stderr
    assert "--keep-work-dir left" not in r.stderr, (
        f"it warned about artifacts it never created:\n{r.stderr}")

    plan = _cli("--print-plan", "--store", str(tmp_path / "gone"),
                identity=identity)
    assert plan.returncode == 0
    assert "--keep-work-dir left" not in plan.stderr, plan.stderr


def test_the_permission_check_can_SEE_a_loose_mode(tmp_path):
    """POSITIVE CONTROL for the assertion above: a 0644 file must fail it.
    Without this, a check written against a mode the umask already guarantees
    would report a tightening it never made."""
    d = tmp_path / "d"
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    f = d / "loose"
    f.write_text("x", encoding="utf-8")
    os.chmod(f, 0o644)
    assert (d.stat().st_mode & 0o077) != 0 and (f.stat().st_mode & 0o077) != 0


def test_keep_work_dir_WITHOUT_work_dir_is_REFUSED(tmp_path, identity):
    """🔴 An option that silently does nothing is a lie — and this one would lie
    about where a plaintext-derived repository ended up.

    Without `--work-dir` everything is built in a private temp directory that is
    removed on exit whatever the flag says. Refused rather than ignored, because
    the tidy-looking "fix" is to leave the temp dir behind where nobody looks.
    """
    src = _dir_store(tmp_path / "objects", {})
    r = _cli("--keep-work-dir", "--from-dir", str(src),
             "--store", str(tmp_path / "gone"), identity=identity)
    assert r.returncode == 1, f"rc={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "--keep-work-dir requires --work-dir" in r.stderr, r.stderr


def test_keep_work_dir_WITH_work_dir_is_ACCEPTED(tmp_path, identity):
    """POSITIVE CONTROL for the refusal above: it is not simply always red."""
    src = _dir_store(tmp_path / "objects", {})
    r = _cli("--keep-work-dir", "--work-dir", str(tmp_path / "w"),
             "--from-dir", str(src), "--store", str(tmp_path / "gone"),
             identity=identity)
    assert r.returncode == 0, f"rc={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "--keep-work-dir requires" not in r.stderr, r.stderr


def test_keep_work_dir_WITH_work_dir_keeps_the_restore_but_not_the_bundle(
        tmp_path, identity):
    """The documented exception, measured: the ciphertext and the restored
    repository stay, the DECRYPTED BUNDLE still dies."""
    store = tmp_path / "store"
    s = _make_scope(store, "scope-alpha", {"e.md": "canary-plaintext"}, commits=3)
    work = tmp_path / "work"
    _verify(store, work, FakeDownloader({_key("scope-alpha"):
                                         _make_artifact(s, identity, tmp_path)}),
            identity, keep_work_dir=True)

    names = sorted(p.name for p in work.iterdir())
    assert any(n.endswith(".bundle.age") for n in names), names
    assert any(n.endswith(".restored.git") for n in names), names
    assert not any(n.endswith(".bundle") and not n.endswith(".bundle.age")
                   for n in names), (
        f"--keep-work-dir kept the DECRYPTED bundle: {names}")


# --------------------------------------------------------------------------- #
# 10. 🔴 THE SEAM WITH THE PRODUCER — keys, prefixes, and the shared helper
# --------------------------------------------------------------------------- #
def test_the_key_shape_is_pinned_LITERALLY_both_ways():
    """🔴 The producer WRITES the key and this verifier PARSES it. That is a seam,
    and a seam guard has to pin the RELATIONSHIP, not one side of it.

    The literal is written out here rather than derived, so a change to
    `object_key` cannot quietly redefine what "correct" means for both.
    """
    t = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    key = B.object_key("workbench-abc", "scope-alpha", t)
    assert key == "workbench-abc/scope-alpha/20260102T030405Z.bundle.age"

    scope, stamp = RV.split_key(key, "workbench-abc/")
    assert scope == "scope-alpha"
    assert stamp == t, f"parsed {stamp}, the producer wrote {t}"


@pytest.mark.parametrize("key,why", [
    ("h/scope-alpha/notastamp.bundle.age", "no parseable UTC stamp"),
    ("h/scope-alpha/20260102T030405Z.bundle", "no parseable UTC stamp"),
    ("h/loose-object.bundle.age", "not <prefix>/<scope>/"),
    ("h/a/b/20260102T030405Z.bundle.age", "not <prefix>/<scope>/"),
])
def test_an_UNPARSEABLE_key_is_a_FAILURE_never_a_skip(key, why):
    """🔴 The stamp is what `--max-lag-days` reads. An artifact whose age cannot
    be determined would pass a staleness check BY DEFAULT, which is the false
    all-clear this whole script exists to prevent."""
    with pytest.raises(RV.RestoreVerifyError) as exc:
        RV.split_key(key, "h/")
    assert why in str(exc.value), f"wrong reason for {key!r}: {exc.value}"


def test_a_key_outside_its_own_listing_prefix_is_refused():
    with pytest.raises(RV.RestoreVerifyError) as exc:
        RV.split_key("other-host/scope-alpha/20260102T030405Z.bundle.age", "h/")
    assert "does not start with the prefix" in str(exc.value)


@pytest.mark.parametrize("given,want", [
    ("workbench", "workbench/"),
    ("workbench/", "workbench/"),
    ("workbench//", "workbench/"),
])
def test_the_listing_prefix_always_ends_in_exactly_one_slash(given, want):
    """🔴 S3 prefixes are BYTE prefixes, not path components. Without the slash,
    `--host workbench` also lists `workbench-laptop/…` — a silent scope widening
    that reads as a successful verification of somebody else's backups."""
    assert RV.normalise_prefix(given) == want


def test_the_missing_slash_really_WOULD_widen_the_scope():
    """POSITIVE CONTROL for the rule above: prove the hazard is real rather than
    a precaution nobody needs."""
    objects = {"workbench/s/20260102T030405Z.bundle.age": b"x",
               "workbench-laptop/s/20260102T030405Z.bundle.age": b"y"}
    d = FakeDownloader(objects)
    assert len(d.list("workbench")) == 2, "the two prefixes are not confusable"
    assert len(d.list(RV.normalise_prefix("workbench"))) == 1


def test_the_verifier_reuses_the_producers_minio_and_guard_helpers():
    """One convention for reaching the tenant, one implementation of the store
    guard. A second copy is one more thing to rotate, and the copy nobody
    exercises is the broken one."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "import backup as B" in src
    assert "class MinioDownloader(B.MinioUploader)" in src
    assert "B._git_scratch(" in src
    assert (SCRIPTS / "mail-actions" / "_minio.py").is_file()
    assert issubclass(RV.MinioDownloader, B.MinioUploader)
    assert issubclass(RV.RestoreVerifyError, B.BackupError), (
        "two exception hierarchies over one pipeline is how a failure escapes "
        "to a bare traceback")


class _FakeMinioClient:
    def __init__(self, exists: bool):
        self._exists = exists
        self.exists_calls: list[str] = []

    def bucket_exists(self, b):
        self.exists_calls.append(b)
        return self._exists


class _FakeMinioArchive:
    """Stands in for `_minio.MinioArchive`, recording the WRITE nobody may make."""
    last: "_FakeMinioArchive | None" = None

    def __init__(self, exists: bool = True):
        self._exists = exists
        self.client = _FakeMinioClient(exists)
        self.ensure_calls: list[str] = []
        self.exited = 0
        _FakeMinioArchive.last = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.exited += 1
        return False

    def ensure_bucket(self, b):          # the producer's call; must NOT happen
        self.ensure_calls.append(b)
        return True


@pytest.fixture()
def fake_minio(monkeypatch):
    """Inject a fake `_minio` module so `MinioDownloader.__enter__` runs for real.

    The import inside `__enter__` is lazy and by name, so putting a module in
    `sys.modules` is what makes the REAL branch execute — as opposed to
    re-typing its body in the test, which would only ever prove the test agrees
    with itself.
    """
    import types
    mod = types.ModuleType("_minio")
    holder: dict[str, bool] = {"exists": True}

    class _Arch(_FakeMinioArchive):
        def __init__(self, *a, **k):
            super().__init__(exists=holder["exists"])

    mod.MinioArchive = _Arch
    monkeypatch.setitem(sys.modules, "_minio", mod)
    monkeypatch.setenv("MINIO_ARCHIVE_ENDPOINT", "http://127.0.0.1:1")
    # 🔴 CLEARED PER TEST. `_FakeMinioArchive.last` is a CLASS attribute, so
    # without this it survives into the next test — and a test whose `__enter__`
    # never ran would read the PREVIOUS test's instance, whose `ensure_calls` is
    # also `[]`, and pass while observing nothing. That is precisely the failure
    # a positive control exists to rule out.
    _FakeMinioArchive.last = None
    return holder


def test_the_downloader_does_NOT_create_the_bucket(fake_minio):
    """🔴 `MinioUploader.__enter__` calls `ensure_bucket`, which CREATES it.

    For a producer that is correct. For a verifier it would turn the loudest
    possible finding — the whole off-machine copy being GONE — into the quiet
    one, '0 objects', and it would do it by WRITING to the store it is auditing.

    Exercised through the real `__enter__`, both ways.
    """
    fake_minio["exists"] = True
    with RV.MinioDownloader("some-bucket") as d:
        assert d is not None
    arch = _FakeMinioArchive.last
    assert arch is not None, (
        "MinioDownloader.__enter__ never constructed the fake archive, so the "
        "assertions below would be about nothing")
    assert arch.ensure_calls == [], (
        "the verifier CREATED the bucket it audits — an absent bucket would "
        "then report as an empty one")
    assert arch.client.exists_calls == ["some-bucket"], (
        "it never asked whether the bucket exists")


def test_an_ABSENT_bucket_is_the_LOUDEST_finding_not_an_empty_one(fake_minio):
    """NEGATIVE CONTROL for the check above, on its own message — and the
    port-forward must still be torn down, because `__exit__` is NOT called when
    `__enter__` raises."""
    fake_minio["exists"] = False
    with pytest.raises(RV.RestoreVerifyError) as exc:
        with RV.MinioDownloader("some-bucket"):
            pass
    assert "DOES NOT EXIST" in str(exc.value), f"wrong reason: {exc.value}"
    assert "not an empty result" in str(exc.value)
    arch = _FakeMinioArchive.last
    assert arch is not None, "the fake archive was never constructed"
    assert arch.exited == 1, (
        "the inner context was left open when __enter__ raised — the `kubectl "
        "port-forward` child outlives the run")


def test_the_defaults_track_the_producers(tmp_path):
    """A verifier pointed at a different bucket, store or identity than the
    producer writes to verifies nothing, and says nothing about it."""
    assert RV.DEFAULT_BUCKET == B.DEFAULT_BUCKET == "analyze-service-index-backups"
    assert RV.DEFAULT_STORE == B.DEFAULT_STORE
    assert RV.DEFAULT_IDENTITY == B.DEFAULT_IDENTITY


# --------------------------------------------------------------------------- #
# 11. failure isolation and the CLI's framing
# --------------------------------------------------------------------------- #
def test_ONE_bad_scope_does_not_abandon_the_others_but_DOES_fail_the_run(
        tmp_path, identity):
    """🔴 The partial-success trap. Scopes are independent by construction, so
    one failing says nothing about the next — but a partial verification
    reported as a success is the false all-clear this file exists to prevent."""
    store = tmp_path / "store"
    objects = {}
    for name, n in (("scope-alpha", 2), ("scope-beta", 3), ("scope-gamma", 4)):
        s = _make_scope(store, name, {"e.md": name}, commits=n)
        mangle = _flip_middle_byte if name == "scope-alpha" else None
        objects[_key(name)] = _make_artifact(s, identity, tmp_path, mangle=mangle)
    dl = FakeDownloader(objects)

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work", dl, identity)

    msg = str(exc.value)
    assert "1 artifact/scope check(s) FAILED" in msg, msg
    assert "2 verified" in msg, msg
    assert sorted(k.split("/")[1] for k in dl.gets) == [
        "scope-alpha", "scope-beta", "scope-gamma"], (
        f"scopes after the failing one were abandoned: {dl.gets}. Alphabetical "
        f"order puts scope-alpha first precisely so this is observable.")


class _ExplodingDownloader(FakeDownloader):
    """Raises a NON-BackupError, the way the real minio client does (`S3Error`,
    urllib3 connection errors). A per-artifact handler catching only BackupError
    would let one dead port-forward abandon every scope after it."""

    def __init__(self, objects, boom_on: str):
        super().__init__(objects)
        self.boom_on = boom_on

    def get(self, key):
        # Record BEFORE raising: the test below reads `gets` to prove the run
        # reached every scope, and a fixture that only recorded successes would
        # make "the failing scope was skipped" and "the failing scope failed"
        # indistinguishable.
        self.gets.append(key)
        if f"/{self.boom_on}/" in key:
            raise ConnectionResetError("synthetic: the port-forward died")
        return self.objects[key]


def test_the_exploding_downloader_control_raises_a_NON_BackupError():
    """VALIDATE THE INSTRUMENT: if the fixture raised a BackupError the test
    below would pass against a narrower handler and prove nothing."""
    d = _ExplodingDownloader({}, boom_on="s")
    with pytest.raises(Exception) as exc:
        d.get("h/s/k.age")
    assert not isinstance(exc.value, B.BackupError), type(exc.value)


def test_a_NON_BackupError_in_one_scope_does_not_abandon_the_others(
        tmp_path, identity):
    store = tmp_path / "store"
    objects = {}
    for name in ("scope-alpha", "scope-beta", "scope-gamma"):
        s = _make_scope(store, name, {"e.md": name}, commits=2)
        objects[_key(name)] = _make_artifact(s, identity, tmp_path)
    dl = _ExplodingDownloader(objects, boom_on="scope-alpha")

    with pytest.raises(RV.RestoreVerifyError) as exc:
        _verify(store, tmp_path / "work", dl, identity)
    msg = str(exc.value)
    assert "1 artifact/scope check(s) FAILED" in msg, msg
    assert "ConnectionResetError" in msg, (
        f"the failure's TYPE was dropped; for a non-BackupError it is most of "
        f"the diagnosis: {msg}")
    assert sorted(k.split("/")[1] for k in dl.gets) == [
        "scope-alpha", "scope-beta", "scope-gamma"], dl.gets


def test_the_CLI_frames_a_NON_BackupError_instead_of_bare_tracebacking(
        tmp_path, identity):
    """`main()` catching only BackupError would send anything else to the
    interpreter's default handler: a traceback and no statement of what it means
    for the VERIFICATION."""
    r = _cli("--from-dir", str(tmp_path / "does-not-exist"), "--store",
             str(tmp_path / "gone"), identity=identity)
    assert r.returncode == 1, f"rc={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "is not a directory" in r.stderr, r.stderr

    # And a genuinely unexpected type, framed rather than dumped.
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=1)
    os.chmod(store, 0o000)
    try:
        with pytest.raises(PermissionError):
            list(store.iterdir())
        r2 = _cli("--from-dir", str(_dir_store(tmp_path / "objects", {})),
                  "--store", str(store), identity=identity)
    finally:
        os.chmod(store, 0o700)
    assert r2.returncode == 1, f"rc={r2.returncode}\n{r2.stdout}\n{r2.stderr}"
    assert "unexpected PermissionError" in r2.stderr, r2.stderr
    assert "Traceback" in r2.stderr, r2.stderr


# --------------------------------------------------------------------------- #
# 12. --print-plan: a claim in operator-facing output is a claim like any other
# --------------------------------------------------------------------------- #
def test_print_plan_mutates_NOTHING(tmp_path, identity):
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    before = _manifest(store)
    r = _cli("--print-plan", "--store", str(store), identity=identity)
    assert r.returncode == 0, r.stderr
    assert _manifest(store) == before


def test_print_plan_does_not_OVERSTATE_what_it_checks(tmp_path, identity):
    """🔴 `--print-plan` is what somebody reads when deciding whether to trust
    this verdict. It must say that verification is a RESTORE and that
    `git bundle verify` is deliberately never used, or the reader will assume
    the cheap check is what happens."""
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=1)
    r = _cli("--print-plan", "--store", str(store), identity=identity)
    out = r.stdout
    assert "NEVER `git bundle verify`" in out, out
    assert "git clone --mirror" in out and "git fsck" in out, out
    assert "ANCESTOR" in out, out
    assert "permanently-red gate" in out, out
    assert "every LOCAL scope must have at least one artifact" in out, out
    assert "scope-alpha (1 commits)" in out, out


def test_print_plan_touches_neither_the_network_nor_the_key(tmp_path):
    """It must work — and say what is missing — with no identity and no cluster."""
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=1)
    r = _cli("--print-plan", "--store", str(store),
             "--identity", str(tmp_path / "absent.key"),
             env={"KUBECONFIG": str(tmp_path / "no-such-kubeconfig")})
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    assert "MISSING" in r.stdout, r.stdout


# --------------------------------------------------------------------------- #
# 13. the documented recipe — the only part of this a human executes
# --------------------------------------------------------------------------- #
def test_SECRETS_md_points_at_the_verifier_from_the_restore_recipe():
    """🔴 A verifier nobody knows about is a verifier nobody runs.

    The restore recipe is where somebody lands when they need this, and it is
    the one place the rehearsal instruction belongs — RULES.md's "rehearse it
    before you need it" is only actionable if the command is written down.
    """
    doc = SECRETS_MD.read_text(encoding="utf-8")
    recipe = doc.split("**Restoring**", 1)[1].split("\n## ", 1)[0]
    assert "restore-verify.py" in recipe, (
        "SECRETS.md's restore section never names the verifier, so the "
        "rehearsal it tells you to do has no command")
    assert "--print-plan" in recipe and "--from-dir" in recipe, (
        "the doc names the verifier but none of the ways to run it")
    assert "It never runs `git bundle verify`, and neither should you." in recipe, (
        "the doc does not warn the reader off `git bundle verify`. It passes a "
        "byte-flipped bundle at rc=0 while printing 'complete history', and "
        "somebody restoring under pressure will reach for it.")
    assert "rc=0" in recipe and "index-pack died" in recipe, (
        "the warning is an assertion with no measurement behind it — state what "
        "was observed, not that a thing is bad")


def test_the_PRODUCER_points_at_the_verifier_for_the_downstream_half():
    """🔴 A reader of `backup.py` must not conclude the artifact in the BUCKET is
    checked. It is not — every check that file performs is upstream of
    encryption and upload, and that is the seam this verifier exists for.

    So the producer's own plan output names the downstream check. Discoverability
    is a real property here: a verifier nobody knows about is a verifier nobody
    runs, and the two places anyone looks are `--print-plan` and SECRETS.md.
    """
    src = BACKUP_SCRIPT.read_text(encoding="utf-8")
    plan = src.split("def print_plan(")[1].split("\ndef ")[0]
    assert "restore-verify.py" in plan, (
        "backup.py's --print-plan never mentions restore-verify.py, so nothing "
        "the operator reads says the uploaded object is verified separately — "
        "or that it is verified at all")


# --------------------------------------------------------------------------- #
# 🔴 WHICH IDENTITY DID IT ACTUALLY USE — the seam with escrow-verify.py
#
# escrow-verify.py's mismatch message SENDS the operator here ("restore-verify.py
# answers that"). If this tool silently resolves its own identity through the
# same environment variables, the handover lands on the same redirected key and
# the second opinion is a second sample of the first mistake — a control that
# shares the step under suspicion is not a control.
# --------------------------------------------------------------------------- #
def test_the_identity_note_asks_about_the_FILE_not_the_env(tmp_path):
    """The deployed backup unit exports SOPS_AGE_KEY_FILE=<the default path>,
    so "an env var is set" is TRUE on the normal configuration and must not
    produce a warning. Only a genuinely different file may."""
    assert RV._identity_note(B.DEFAULT_IDENTITY) == ""
    note = RV._identity_note(tmp_path / "somewhere-else.key")
    assert "NOT the default identity" in note
    assert "ASIB_AGE_IDENTITY" in note and "SOPS_AGE_KEY_FILE" in note


def test_the_DECRYPT_FAILED_message_names_a_redirect_as_a_THIRD_mechanism():
    """That message enumerates two mechanisms — wrong key, damaged ciphertext.
    A redirected identity is a third, and without it the 'wrong key' arm reads
    as a fact about the escrow or the artifact when it is neither."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "_identity_note(identity)" in src, (
        "the DECRYPT FAILED message no longer states which identity it used")


def test_print_plan_discloses_a_REDIRECTED_identity(tmp_path, identity):
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=1)
    # `_cli` supplies the identity through ASIB_AGE_IDENTITY, so this also
    # exercises the env arm — the one that caused the incident.
    r = _cli("--print-plan", "--store", str(store), identity=identity)
    assert "chosen by: $ASIB_AGE_IDENTITY" in r.stdout, r.stdout
    # the fixture identity is a throwaway key, never the operator's default
    assert "NOT the default identity" in r.stdout, r.stdout

    # ...and the explicit-flag arm reports itself as the flag, not as an env var
    r2 = _cli("--print-plan", "--store", str(store), "--identity", str(identity))
    assert "chosen by: the --identity flag" in r2.stdout, r2.stdout


def test_restore_print_plan_is_SILENT_for_the_DEFAULT_identity(
        tmp_path, monkeypatch, capsys):
    """🔴 THE NEGATIVE CONTROL. Without it, mutating this tool's trigger to warn
    UNCONDITIONALLY leaves the whole suite green — i.e. the sibling of the exact
    permanently-red-warning bug that was just fixed in escrow-verify.py could be
    reintroduced here undetected. The positive case alone cannot see it.

    Hermetic: the default is monkeypatched onto a tmp file, so this never reads
    the operator's real key (a test that did turned the sandbox tier red).
    """
    key = tmp_path / "the-default.key"
    key.write_text("an identity file's contents", encoding="utf-8")
    monkeypatch.setattr(B, "DEFAULT_IDENTITY", key)
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=1)

    RV.print_plan(bucket="a-bucket", prefix="a-host/", store=store,
                  identity=key, scope_filter=None, verify_all=False,
                  max_lag_days=1.0, identity_source="$SOPS_AGE_KEY_FILE")
    out = capsys.readouterr().out
    assert "chosen by: $SOPS_AGE_KEY_FILE" in out, out
    assert "NOT the default identity" not in out, (
        "restore-verify warned about the DEFAULT identity — the same "
        "permanently-red warning that was just removed from escrow-verify")

    # ...and the positive case, so this pair discriminates rather than just
    # asserting silence (a print_plan that printed nothing would pass alone).
    other = tmp_path / "somewhere-else.key"
    other.write_text("different", encoding="utf-8")
    RV.print_plan(bucket="a-bucket", prefix="a-host/", store=store,
                  identity=other, scope_filter=None, verify_all=False,
                  max_lag_days=1.0, identity_source="$SOPS_AGE_KEY_FILE")
    out2 = capsys.readouterr().out
    assert "NOT the default identity" in out2, out2
