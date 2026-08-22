"""Tests for scripts/analyze-service-index/backup.py — the ENCRYPTED OFF-MACHINE backup.

WHAT IS BEING PROTECTED
-----------------------
`~/.claude/analyze-service-index/<scope>/` is one git repository per scope. Until
this change every one of them had `remote = none` and no copy anywhere off the
local disk, so the local history — the thing `commit.sh` exists to create — was
stored inside the same object that a disk failure or an `rm -rf` destroys. The
content is not re-derivable.

🔴 THE ONLY TEST THAT PROVES THE FEATURE IS THE RESTORE DRILL.
Everything else here is a supporting control. A backup that produces bytes is
not a backup; a backup you have restored from is. `test_round_trip_*` builds a
synthetic store, runs the real producer end to end, decrypts the artifact it
actually uploaded, clones a repository out of it, and requires the restored
entry set and commit count to match the source EXACTLY. If that test is ever
deleted or weakened, this suite stops making any claim worth having.

🔴 ALL FIXTURES ARE SYNTHETIC. devrc is a public repo and the real store is
client-confidential: no scope name, entry title or path from the live store
appears here. The synthetic scopes are `scope-alpha`/`scope-beta`/`scope-gamma`
and their content is lorem-grade filler. Aggregate integers are the only real
numbers quoted anywhere in this change.

🔴 NOTHING HERE SKIPS. `git` and `age` are both hard requirements, resolved once
at import: a skipped backup test reports safety it never measured, which for a
disaster-recovery feature is the worst possible failure mode — it is quiet
exactly when it is wrong. `age` is declared in nix/pkgs/default.nix and in
flake.nix's `gateTools`, so its absence is a deployment fault to fix there, not
a condition to skip on.

Every negative control asserts THIS guard's own message, not merely a non-zero
exit. A control that goes red because a different guard fired is green for the
wrong reason and stays green with the guard it claims to test deleted.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
SCRIPT = SCRIPTS / "analyze-service-index" / "backup.py"
HOME_NIX = ROOT / "nix" / "home.nix"
FLAKE_NIX = ROOT / "flake.nix"
PKGS_NIX = ROOT / "nix" / "pkgs" / "default.nix"

sys.path.insert(0, str(SCRIPTS / "analyze-service-index"))
sys.path.insert(0, str(SCRIPTS))

import backup as B  # noqa: E402
from testlib.mockbin import write_exec  # noqa: E402


def _require(tool: str, why: str) -> str:
    p = shutil.which(tool)
    if p is None:  # pragma: no cover - the flake check puts both on PATH
        raise RuntimeError(
            f"{tool} is not on PATH. {why} It is declared in nix/pkgs/default.nix "
            f"and in flake.nix `gateTools`; add it there rather than skipping "
            f"these tests — a skipped backup test reports safety it never measured."
        )
    return p


GIT: str = _require("git", "Every scope is a real git repository.")
AGE: str = _require("age", "The backup is encrypted before it leaves the box.")
AGE_KEYGEN: str = _require("age-keygen", "The recipient is derived from the identity.")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _git_env() -> dict:
    """A git environment that cannot read or write the OPERATOR's real config.

    RULES.md: never `git config --global`. These tests create many repositories;
    without this they would inherit (and a misbehaving code path could write)
    the real `~/.gitconfig`.
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
    })
    return e


def _git_run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([GIT, "-C", str(repo), *args],
                          capture_output=True, text=True, env=_git_env())


def _make_scope(store: Path, name: str, entries: dict[str, str], commits: int = 1) -> Path:
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


def _empty_scope(store: Path, name: str) -> Path:
    """A scope repository with a working file but ZERO commits."""
    scope = store / name
    scope.mkdir(parents=True)
    subprocess.run([GIT, "init", "-q", "-b", "trunk", str(scope)],
                   check=True, capture_output=True, env=_git_env())
    (scope / "uncommitted.md", )[0].write_text("never committed\n", encoding="utf-8")
    return scope


@pytest.fixture()
def identity(tmp_path_factory) -> Path:
    """A throwaway age identity for the suite.

    🔴 NEVER the operator's real key. The tests must exercise the real derive →
    encrypt → decrypt path, but with key material that is created and destroyed
    inside the test run.
    """
    d = tmp_path_factory.mktemp("age-id")
    key = d / "test.key"
    p = subprocess.run([AGE_KEYGEN, "-o", str(key)], capture_output=True, text=True)
    assert p.returncode == 0, f"age-keygen failed: {p.stderr}"
    key.chmod(0o600)
    return key


class FakeUploader:
    """The upload boundary, faked. Records every put; serves stat/list/remove.

    This is where the object store is cut out. The bundling, verification and
    encryption above it are all REAL — only the network hop is replaced, so the
    round-trip drill still proves the artifact is restorable.
    """

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.removed: list[str] = []
        self.entered = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *exc):
        return False

    def put(self, key, data):
        self.puts.append(key)
        self.objects[key] = data

    def stat(self, key):
        data = self.objects[key]          # KeyError -> "could not be read back"
        return len(data), hashlib.md5(data).hexdigest()  # noqa: S324

    def list(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    def remove(self, key):
        self.removed.append(key)
        self.objects.pop(key, None)


def _run_backup(store: Path, work: Path, uploader, identity: Path,
                keep: int = 14) -> int:
    """Drive the real `run()` with the upload boundary faked."""
    os.environ["ASIB_AGE_IDENTITY"] = str(identity)
    os.environ["ASIB_HOST"] = "synthetic-host"
    try:
        return B.run(store, bucket="test-bucket", keep=keep, upload=True,
                     work_dir=work, uploader_factory=lambda: uploader)
    finally:
        os.environ.pop("ASIB_AGE_IDENTITY", None)
        os.environ.pop("ASIB_HOST", None)


def _cli(store: Path, *args: str, identity: Path | None = None,
         env: dict | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.update(_git_env())
    if identity is not None:
        e["ASIB_AGE_IDENTITY"] = str(identity)
    e["ASIB_HOST"] = "synthetic-host"
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(SCRIPT), "--store", str(store), *args],
                          capture_output=True, text=True, env=e)


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


def _decrypt(cipher: bytes, identity: Path, out: Path) -> subprocess.CompletedProcess:
    src = out.with_suffix(".age")
    src.write_bytes(cipher)
    return subprocess.run(
        [AGE, "--decrypt", "--identity", str(identity), "--output", str(out), str(src)],
        capture_output=True, text=True)


# --------------------------------------------------------------------------- #
# 0. harness self-validation — validate the INSTRUMENT before reading its verdict
# --------------------------------------------------------------------------- #
def test_the_producer_exists():
    assert SCRIPT.is_file(), f"{SCRIPT} missing — every test below is vacuous"


def test_the_fake_uploader_can_observe_a_put():
    """POSITIVE CONTROL for the instrument every upload test reads.

    A FakeUploader wired to nothing would record zero puts, and every assertion
    of the form "the object landed" would then be a reassuring zero rather than
    a measurement. Watch the counter move before trusting it.
    """
    u = FakeUploader()
    assert u.puts == []
    u.put("k", b"abc")
    assert u.puts == ["k"] and u.stat("k")[0] == 3
    assert u.list("") == ["k"], "list() must see what put() stored"


def test_the_synthetic_scope_builder_really_makes_commits(tmp_path):
    """POSITIVE CONTROL for the fixture: if _make_scope produced 0 commits, the
    zero-commit guard test below would pass for the wrong reason and the
    round-trip drill would be testing an empty repository."""
    s = _make_scope(tmp_path / "store", "scope-alpha", {"a.md": "x"}, commits=3)
    assert B.commit_count(s) == 3


# --------------------------------------------------------------------------- #
# 1. 🔴 THE RESTORE DRILL — the only test that proves the backup is sufficient
# --------------------------------------------------------------------------- #
def test_round_trip_restores_the_exact_entry_set_and_commit_count(tmp_path, identity):
    """bundle -> verify -> encrypt -> (upload boundary) -> decrypt -> clone.

    The assertion that matters: the CLONED repository's tracked entry set and
    commit count equal the source's, exactly. Not "a repo came out" — the same
    repo came out.
    """
    store = tmp_path / "store"
    sources = {
        "scope-alpha": ({"one.md": "alpha one", "two.md": "alpha two"}, 4),
        "scope-beta": ({"only.md": "beta only"}, 1),
        "scope-gamma": ({"a.md": "g a", "b.md": "g b", "c.md": "g c"}, 7),
    }
    for name, (entries, n) in sources.items():
        _make_scope(store, name, entries, commits=n)

    up = FakeUploader()
    done = _run_backup(store, tmp_path / "work", up, identity)
    assert done == 3, "all three synthetic scopes must be backed up"
    assert len(up.puts) == 3

    for name, (entries, n) in sources.items():
        key = next(k for k in up.objects if f"/{name}/" in k)

        # decrypt what was ACTUALLY uploaded — not a local by-product
        plain = tmp_path / f"{name}.bundle"
        d = _decrypt(up.objects[key], identity, plain)
        assert d.returncode == 0, f"{name}: decryption failed: {d.stderr}"

        # restore by cloning FROM THE BUNDLE, the way a real recovery would
        dest = tmp_path / f"restored-{name}"
        c = subprocess.run([GIT, "clone", "-q", str(plain), str(dest)],
                           capture_output=True, text=True, env=_git_env())
        assert c.returncode == 0, f"{name}: clone from bundle failed: {c.stderr}"

        restored_entries = set(
            _git_run(dest, "ls-tree", "-r", "--name-only", "HEAD").stdout.split())
        assert restored_entries == set(entries), (
            f"{name}: restored entry set {sorted(restored_entries)} != source "
            f"{sorted(entries)}")

        restored_commits = int(
            _git_run(dest, "rev-list", "--count", "HEAD").stdout.strip())
        assert restored_commits == n, (
            f"{name}: restored {restored_commits} commits, source had {n}")

        source_head = _git_run(store / name, "rev-parse", "HEAD").stdout.strip()
        assert _git_run(dest, "rev-parse", "HEAD").stdout.strip() == source_head, (
            f"{name}: restored HEAD is a different commit than the source's")


def test_round_trip_restores_the_entry_CONTENT_byte_for_byte(tmp_path, identity):
    """The set of names matching is not the content matching.

    A bundle that restored the right filenames with the wrong bytes would pass
    the test above. This one pins the bytes.
    """
    store = tmp_path / "store"
    entries = {"one.md": "distinctive alpha body", "two.md": "distinctive beta body"}
    _make_scope(store, "scope-alpha", entries, commits=2)

    up = FakeUploader()
    _run_backup(store, tmp_path / "work", up, identity)

    plain = tmp_path / "restore.bundle"
    assert _decrypt(up.objects[up.puts[0]], identity, plain).returncode == 0
    dest = tmp_path / "restored"
    subprocess.run([GIT, "clone", "-q", str(plain), str(dest)],
                   check=True, capture_output=True, env=_git_env())

    for fname in entries:
        assert (dest / fname).read_bytes() == (store / "scope-alpha" / fname).read_bytes(), (
            f"{fname} restored with different bytes than the source")


def test_the_uploaded_object_is_CIPHERTEXT_not_the_bundle(tmp_path, identity):
    """🔴 The confidentiality claim, measured rather than asserted.

    MinIO must hold something it cannot read. If the producer ever uploaded the
    plaintext bundle, every other test here would still pass — the round trip
    would work fine. This is the only control that would notice.
    """
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"one.md": "canary-plaintext-marker"}, commits=1)

    up = FakeUploader()
    _run_backup(store, tmp_path / "work", up, identity)
    blob = up.objects[up.puts[0]]

    assert blob.startswith(b"age-encryption.org/"), (
        "the uploaded object is not an age file — it may be the plaintext bundle")
    assert b"canary-plaintext-marker" not in blob, (
        "entry content is readable in the uploaded object")
    assert b"PACK" not in blob[:64], "the uploaded object looks like a raw git bundle"


# --------------------------------------------------------------------------- #
# 2. 🔴 READ-ONLY PROOF — the store must come out byte-identical
# --------------------------------------------------------------------------- #
def test_a_full_backup_run_leaves_the_store_byte_identical(tmp_path, identity):
    store = tmp_path / "store"
    for name, n in (("scope-alpha", 3), ("scope-beta", 1), ("scope-gamma", 5)):
        _make_scope(store, name, {"e.md": name}, commits=n)

    before = _manifest(store)
    assert before, "manifest is empty — this test would pass vacuously"

    up = FakeUploader()
    assert _run_backup(store, tmp_path / "work", up, identity) == 3

    after = _manifest(store)
    assert after == before, (
        "the backup MODIFIED the store. Differing paths: "
        f"{sorted(set(before) ^ set(after)) or [k for k in before if before[k] != after.get(k)]}")


def test_no_scope_gains_a_remote(tmp_path, identity):
    """🔴 The invariant every scope README states. Bundles exist to preserve it."""
    store = tmp_path / "store"
    for name in ("scope-alpha", "scope-beta"):
        _make_scope(store, name, {"e.md": name}, commits=2)
    assert all(B.scope_remotes(store / n) == [] for n in ("scope-alpha", "scope-beta"))

    _run_backup(store, tmp_path / "work", FakeUploader(), identity)

    for name in ("scope-alpha", "scope-beta"):
        assert B.scope_remotes(store / name) == [], (
            f"{name} gained a git remote — the no-remote invariant is broken")


def test_the_git_config_of_every_scope_is_untouched(tmp_path, identity):
    """A remote is one way to write `.git/config`; `git config` is another.

    Pinning the file's mtime AND bytes catches both, plus anything else that
    writes it for a reason nobody anticipated.
    """
    store = tmp_path / "store"
    for name in ("scope-alpha", "scope-beta"):
        _make_scope(store, name, {"e.md": name}, commits=2)
    cfgs = {n: (store / n / ".git" / "config") for n in ("scope-alpha", "scope-beta")}
    before = {n: (p.stat().st_mtime_ns, p.read_bytes()) for n, p in cfgs.items()}

    _run_backup(store, tmp_path / "work", FakeUploader(), identity)

    for n, p in cfgs.items():
        assert (p.stat().st_mtime_ns, p.read_bytes()) == before[n], (
            f"{n}/.git/config changed during a backup run")


def test_the_read_only_subcommand_allowlist_is_pinned_exactly(tmp_path):
    """An EXACT set, not a subset.

    Widening this is how a write verb arrives in a script whose whole premise is
    that it cannot write. Adding one has to be a visible diff against this line.
    """
    assert B._READ_ONLY_SUBCOMMANDS == frozenset(
        {"bundle", "rev-list", "remote", "rev-parse", "for-each-ref"})


def test_the_scratch_git_helper_is_never_pointed_at_the_store(tmp_path, identity):
    """`_git_scratch` may run WRITING subcommands (`clone`), so the thing that
    keeps it safe is where it is aimed. A full run must leave no rehearsal
    directory behind and must not have created one inside the store."""
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    work = tmp_path / "work"
    _run_backup(store, work, FakeUploader(), identity)

    assert not list(store.glob("**/.rehearse-*")), "a rehearsal clone landed in the store"
    assert not list(work.glob(".rehearse-*")), (
        "the rehearsal clone was not cleaned up; it holds a full copy of the "
        "scope's history in plaintext")


@pytest.mark.parametrize("verb", ["config", "remote-add", "gc", "commit", "push", "fetch"])
def test_git_refuses_a_write_subcommand(tmp_path, verb):
    """NEGATIVE CONTROL, watched to fail with THIS guard's own message."""
    repo = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=1)
    with pytest.raises(B.BackupError) as exc:
        B._git(repo, verb, "whatever")
    assert "READ-ONLY to this script" in str(exc.value), (
        f"{verb} was refused by some OTHER guard — this control is green for the "
        f"wrong reason")


def test_the_git_environment_disables_optional_locks():
    """DEFENCE IN DEPTH — and this test is honest about being a SPELLING check.

    🔴 MEASURED 2026-08-21, against a repo with a deliberately stale index: none
    of the four allowlisted subcommands rewrites `.git/index` with
    GIT_OPTIONAL_LOCKS set to 0 OR to 1, while `git status` in the same probe DID
    rewrite it — so the probe could observe a change and simply had none to see.
    A mutation flipping the value to "1" is therefore killed by THIS assertion
    only, not by the byte-identity test, and calling it a behavioural guard would
    be a coverage claim wider than the code.

    It stays because the cost is one line and the subcommand somebody adds later
    may well need it. What makes the store read-only is the allowlist and the
    unit's BindReadOnlyPaths, not this.
    """
    assert B._git_env()["GIT_OPTIONAL_LOCKS"] == "0"
    assert B._git_env()["GIT_CONFIG_GLOBAL"] == os.devnull


# --------------------------------------------------------------------------- #
# 3. 🔴 NEGATIVE CONTROLS — each watched to fail, each on its own message
# --------------------------------------------------------------------------- #
def test_git_bundle_verify_ALONE_does_not_detect_corruption(tmp_path):
    """🔴 THE MEASUREMENT THAT CHANGED THE DESIGN. Pinned so nobody re-simplifies.

    `git bundle verify` validates the bundle HEADER and PREREQUISITES. It does
    NOT walk the packfile. Measured here, every run: a bundle with one byte
    flipped in the middle passes it at rc=0, printing "The bundle records a
    complete history."

    This test asserts the WEAKNESS, deliberately. The original design said
    "`git bundle verify` the bundle before upload — an unverified bundle is a
    claim, not a backup", which is right about the principle and wrong about the
    command: stopping there uploads corrupt archives under a green verdict. If a
    future git ever tightens `bundle verify`, this test goes red and whoever
    sees it can delete the restore rehearsal with evidence rather than hope.
    """
    scope = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=2)
    good = tmp_path / "good.bundle"
    B.bundle_scope(scope, good, tmp_path / "work")   # positive control: passes
    assert good.stat().st_size > 0

    bad = tmp_path / "bad.bundle"
    raw = bytearray(good.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    bad.write_bytes(bytes(raw))

    v = B._git(scope, "bundle", "verify", str(bad))
    assert v.returncode == 0, (
        "git bundle verify now REJECTS a byte-flipped bundle. The restore "
        "rehearsal in bundle_scope() was added because it did not; re-measure "
        "and simplify if that has genuinely changed.")


def test_a_corrupted_bundle_is_caught_by_the_restore_rehearsal(tmp_path):
    """The guard that ACTUALLY catches the corruption `bundle verify` waves through."""
    scope = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=2)
    work = tmp_path / "work"
    good = tmp_path / "good.bundle"
    B.bundle_scope(scope, good, work)

    raw = bytearray(good.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    good.write_bytes(bytes(raw))

    c = B._git_scratch(work, "clone", "--bare", "--quiet",
                       str(good), str(tmp_path / "rehearse.git"))
    assert c.returncode != 0, (
        "a clone from a byte-flipped bundle SUCCEEDED — the restore rehearsal "
        "would not catch corruption and the whole verification story is vacuous")


def test_a_corrupted_bundle_fails_the_run_and_is_never_uploaded(tmp_path, identity):
    """END-TO-END reachability proof, through the real CLI.

    A `git` shim earlier on PATH passes everything through to the real binary,
    then TRUNCATES whatever `bundle create` produced. `create` still exits 0 —
    exactly the case the guard exists for — and the corrupt bundle reaches
    verification. Asserting on the guard's own wording, and on the run failing.
    """
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)

    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    write_exec(shim_dir / "git", f"""
{GIT} "$@"
rc=$?
for a in "$@"; do
  case "$a" in
    *.bundle) if [ -f "$a" ]; then printf 'not-a-bundle' > "$a"; fi ;;
  esac
done
exit $rc
""")

    r = _cli(store, "--no-upload", "--work-dir", str(tmp_path / "work"),
             identity=identity,
             env={"PATH": f"{shim_dir}:{os.environ['PATH']}"})
    assert r.returncode != 0, "a corrupted bundle produced a SUCCESSFUL backup run"
    assert "Not uploading it" in r.stderr, f"failed for the wrong reason:\n{r.stderr}"
    assert "could NOT BE RESTORED FROM" in r.stderr or "REJECTED the bundle" in r.stderr, (
        f"failed for the wrong reason:\n{r.stderr}")


def _mangle_bundle_after_create(monkeypatch, mangle):
    """Let `bundle_scope` create a REAL bundle, then damage it before it is checked.

    🔴 This exists because the obvious end-to-end control does not reach the
    guards it appears to test. Replacing the bundle with junk breaks the HEADER,
    so `git bundle verify` rejects it and the restore rehearsal never runs — a
    mutation sweep scored the clone-failure and ref-comparison guards SURVIVED
    while that test was green. Damaging the bundle in ways that PASS verify is
    what actually reaches them.
    """
    real = B._git

    def fake(repo, *args):
        p = real(repo, *args)
        if args[:2] == ("bundle", "create") and p.returncode == 0:
            mangle(Path(args[2]), repo)
        return p

    monkeypatch.setattr(B, "_git", fake)


def test_a_pack_corrupted_bundle_that_PASSES_verify_fails_the_rehearsal(
        tmp_path, monkeypatch):
    """🔴 The guard the whole redesign rests on, reached with an input no earlier
    check rejects: a byte flipped in the packfile, header intact."""
    scope = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=3)

    def flip(bundle: Path, _repo):
        raw = bytearray(bundle.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        bundle.write_bytes(bytes(raw))

    _mangle_bundle_after_create(monkeypatch, flip)
    with pytest.raises(B.BackupError) as exc:
        B.bundle_scope(scope, tmp_path / "out.bundle", tmp_path / "work")
    assert "could NOT BE RESTORED FROM" in str(exc.value), (
        f"failed for the wrong reason: {exc.value}")
    assert "Not uploading it" in str(exc.value)


def test_a_bundle_that_RESTORES_CLEANLY_but_INCOMPLETELY_is_rejected(
        tmp_path, monkeypatch):
    """🔴 The quiet failure: a perfectly valid bundle holding less than the scope.

    It passes `bundle verify` AND clones without error. Only comparing the
    restored refs against the source can see it. Before this test the ref
    comparison survived being replaced with `if False:`.
    """
    scope = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=4)

    def make_partial(bundle: Path, repo):
        first = _git_run(repo, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
        _git_run(repo, "branch", "-f", "partial-probe", first)
        bundle.unlink()
        p = subprocess.run(
            [GIT, "-C", str(repo), "bundle", "create", str(bundle),
             "refs/heads/partial-probe"],
            capture_output=True, text=True, env=_git_env())
        assert p.returncode == 0, p.stderr

    _mangle_bundle_after_create(monkeypatch, make_partial)
    with pytest.raises(B.BackupError) as exc:
        B.bundle_scope(scope, tmp_path / "out.bundle", tmp_path / "work")
    assert "DIFFERENT set of refs" in str(exc.value), (
        f"failed for the wrong reason: {exc.value}")


def test_the_rehearsal_ACCEPTS_a_good_bundle(tmp_path):
    """POSITIVE CONTROL for the two above: the rehearsal is not simply always red.

    Without this, a `bundle_scope` that raised unconditionally would satisfy
    every negative control in this file."""
    scope = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=3)
    B.bundle_scope(scope, tmp_path / "ok.bundle", tmp_path / "work")
    assert (tmp_path / "ok.bundle").stat().st_size > 0


def test_a_bundle_missing_commits_fails_the_rehearsal(tmp_path):
    """A bundle that restores CLEANLY but INCOMPLETELY is the quiet failure:
    the clone succeeds, so only the ref/commit comparison can see it."""
    scope = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=4)
    work = tmp_path / "work"
    work.mkdir()

    # A legitimate, uncorrupted bundle carrying only the FIRST commit, via a
    # branch created for the purpose (a bundle needs refs, not a bare sha).
    first = _git_run(scope, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
    _git_run(scope, "branch", "partial-probe", first)
    partial = work / "partial.bundle"
    p = subprocess.run(
        [GIT, "-C", str(scope), "bundle", "create", str(partial), "refs/heads/partial-probe"],
        capture_output=True, text=True, env=_git_env())
    assert p.returncode == 0, p.stderr

    # POSITIVE CONTROL: it is a perfectly valid bundle. Both weaker checks pass.
    assert B._git(scope, "bundle", "verify", str(partial)).returncode == 0
    c = B._git_scratch(work, "clone", "--bare", "--quiet", str(partial), str(work / "r.git"))
    assert c.returncode == 0, "the partial bundle must clone fine, or this proves nothing"
    got = B._git_scratch(work / "r.git", "rev-list", "--count", "--all").stdout.strip()
    assert got == "1" and B.commit_count(scope) == 4

    # Only the ref/commit comparison can see the shortfall.
    fmt = "--format=%(refname) %(objectname)"
    want = B._refs(B._git(scope, "for-each-ref", fmt))
    restored = B._refs(B._git_scratch(work / "r.git", "for-each-ref", fmt))
    assert restored != want, (
        "a bundle holding 1 of 4 commits restored the SAME ref set as the "
        "source — the comparison in bundle_scope() cannot detect a partial "
        "backup and its guard is unreachable")


def test_a_truncated_ciphertext_fails_decryption(tmp_path, identity):
    """NEGATIVE CONTROL: the ciphertext is not silently recoverable when damaged."""
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    up = FakeUploader()
    _run_backup(store, tmp_path / "work", up, identity)
    blob = up.objects[up.puts[0]]

    assert _decrypt(blob, identity, tmp_path / "ok.bundle").returncode == 0, (
        "positive control failed: the INTACT ciphertext did not decrypt, so the "
        "negative result below would prove nothing")

    d = _decrypt(blob[: len(blob) // 2], identity, tmp_path / "trunc.bundle")
    assert d.returncode != 0, "a truncated ciphertext decrypted successfully"


def test_an_altered_ciphertext_fails_decryption(tmp_path, identity):
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    up = FakeUploader()
    _run_backup(store, tmp_path / "work", up, identity)
    raw = bytearray(up.objects[up.puts[0]])
    raw[-1] ^= 0xFF
    d = _decrypt(bytes(raw), identity, tmp_path / "alt.bundle")
    assert d.returncode != 0, "an altered ciphertext decrypted successfully"


def test_a_wrong_identity_cannot_decrypt(tmp_path, identity, tmp_path_factory):
    """The confidentiality claim's other half: only the operator's key opens it."""
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=1)
    up = FakeUploader()
    _run_backup(store, tmp_path / "work", up, identity)

    other = tmp_path / "other.key"
    subprocess.run([AGE_KEYGEN, "-o", str(other)], check=True, capture_output=True)
    d = _decrypt(up.objects[up.puts[0]], other, tmp_path / "nope.bundle")
    assert d.returncode != 0, "an unrelated age identity decrypted the backup"


class _DroppingUploader(FakeUploader):
    """Accepts the put, stores nothing. The '200 OK, object absent' case."""
    def put(self, key, data):
        self.puts.append(key)


class _TruncatingUploader(FakeUploader):
    def put(self, key, data):
        self.puts.append(key)
        self.objects[key] = data[:-16]


class _CorruptingUploader(FakeUploader):
    """Same LENGTH, different bytes — invisible to a size-only check."""
    def put(self, key, data):
        self.puts.append(key)
        self.objects[key] = bytes(len(data))


@pytest.mark.parametrize("cls,needle", [
    (_DroppingUploader, "could not be read back"),
    (_TruncatingUploader, "did not land intact"),
    (_CorruptingUploader, "landed with the wrong content"),
])
def test_a_failed_upload_is_caught_by_the_read_back(tmp_path, identity, cls, needle):
    """🔴 NEGATIVE CONTROLS: a put that returns without raising is NOT evidence.

    Three distinct ways an upload can 'succeed' and store nothing usable, each
    asserted on its own message so a single over-broad catch cannot make all
    three pass.
    """
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    with pytest.raises(B.BackupError) as exc:
        _run_backup(store, tmp_path / "work", cls(), identity)
    assert needle in str(exc.value), f"failed for the wrong reason: {exc.value}"


def test_the_read_back_passes_on_a_good_upload(tmp_path, identity):
    """POSITIVE CONTROL for the three above: the check is not simply always red."""
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    assert _run_backup(store, tmp_path / "work", FakeUploader(), identity) == 1


def test_a_malformed_recipient_override_is_refused(tmp_path):
    with pytest.raises(B.BackupError) as exc:
        B.resolve_recipient(Path("/nonexistent"))  # no identity, no override
    assert "no age identity at" in str(exc.value)

    os.environ["ASIB_AGE_RECIPIENT"] = "age1-obviously-not-a-key"
    try:
        with pytest.raises(B.BackupError) as exc:
            B.resolve_recipient(Path("/nonexistent"))
        assert "not a well-formed age public key" in str(exc.value)
    finally:
        os.environ.pop("ASIB_AGE_RECIPIENT", None)


def test_a_valid_recipient_override_is_accepted(tmp_path, identity):
    """POSITIVE CONTROL: the shape check accepts a REAL key, so the rejection
    above is discriminating rather than universal."""
    real = subprocess.run([AGE_KEYGEN, "-y", str(identity)],
                          capture_output=True, text=True).stdout.strip()
    os.environ["ASIB_AGE_RECIPIENT"] = real
    try:
        assert B.resolve_recipient(Path("/nonexistent")) == real
    finally:
        os.environ.pop("ASIB_AGE_RECIPIENT", None)


def test_the_derived_recipient_is_the_one_that_can_decrypt(tmp_path, identity):
    """🔴 The reason the recipient is DERIVED rather than hardcoded.

    A hardcoded recipient can drift from the key the operator holds, producing
    archives that encrypt cleanly and never open. This pins that the thing we
    encrypt to and the thing we decrypt with are the same fact.
    """
    derived = B.resolve_recipient(identity)
    plain = tmp_path / "p.txt"
    plain.write_text("payload\n", encoding="utf-8")
    cipher = tmp_path / "p.age"
    B.encrypt(plain, cipher, derived)
    out = tmp_path / "back.txt"
    assert _decrypt(cipher.read_bytes(), identity, out).returncode == 0
    assert out.read_text(encoding="utf-8") == "payload\n"


# --------------------------------------------------------------------------- #
# 4. 🔴 EMPTY-CASE GUARDS — a zero that looks like success is the failure mode
# --------------------------------------------------------------------------- #
def test_a_store_with_zero_scopes_fails_loudly(tmp_path, identity):
    store = tmp_path / "store"
    store.mkdir()
    with pytest.raises(B.BackupError) as exc:
        _run_backup(store, tmp_path / "work", FakeUploader(), identity)
    assert "contains no scope repositories" in str(exc.value)
    assert "Refusing to report a successful backup of nothing" in str(exc.value)


def test_a_store_with_zero_scopes_exits_non_zero_through_the_cli(tmp_path, identity):
    """The library raising is not the same claim as the PROCESS failing — and
    the process exit code is what systemd reads."""
    store = tmp_path / "store"
    store.mkdir()
    r = _cli(store, "--no-upload", "--work-dir", str(tmp_path / "w"), identity=identity)
    assert r.returncode != 0, "an empty store produced exit 0"
    assert "contains no scope repositories" in r.stderr


def test_a_scope_with_zero_commits_fails_loudly(tmp_path, identity):
    store = tmp_path / "store"
    _empty_scope(store, "scope-alpha")
    with pytest.raises(B.BackupError) as exc:
        _run_backup(store, tmp_path / "work", FakeUploader(), identity)
    assert "ZERO commits" in str(exc.value)


def test_a_zero_commit_scope_fails_the_run_even_beside_healthy_ones(tmp_path, identity):
    """🔴 The partial-success trap. Two good scopes and one broken one must not
    add up to a green run: a backup missing one scope is a backup that will be
    discovered to be missing it at recovery time."""
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "a"}, commits=2)
    _empty_scope(store, "scope-beta")
    _make_scope(store, "scope-gamma", {"e.md": "g"}, commits=2)

    up = FakeUploader()
    with pytest.raises(B.BackupError) as exc:
        _run_backup(store, tmp_path / "work", up, identity)
    assert "1 of 3 scope(s) failed" in str(exc.value)
    assert len(up.puts) == 2, "the healthy scopes should still have been uploaded"


def test_an_absent_store_is_the_ONLY_permitted_clean_no_op(tmp_path, identity):
    """A host that has never run /analyze-service has nothing to lose. Failing
    here would make the timer a permanently-red gate on the laptop."""
    r = _cli(tmp_path / "does-not-exist", "--no-upload",
             "--work-dir", str(tmp_path / "w"), identity=identity)
    assert r.returncode == 0
    assert "nothing to back up" in r.stderr
    assert "backed up" not in r.stdout, (
        "an absent store must not print a success count")


def test_print_plan_names_a_zero_commit_scope_as_a_failure(tmp_path, identity):
    store = tmp_path / "store"
    _empty_scope(store, "scope-alpha")
    r = _cli(store, "--print-plan", identity=identity)
    assert r.returncode == 0
    assert "ZERO COMMITS, would FAIL the run" in r.stdout


def test_print_plan_mutates_nothing(tmp_path, identity):
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    before = _manifest(store)
    _cli(store, "--print-plan", identity=identity)
    assert _manifest(store) == before


def test_print_plan_states_the_no_remote_invariant(tmp_path, identity):
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=1)
    r = _cli(store, "--print-plan", identity=identity)
    assert "remote:    NONE" in r.stdout
    assert "age" in r.stdout and "BEFORE upload" in r.stdout


def test_print_plan_does_not_overstate_what_bundle_verify_proves():
    """🔴 A claim in operator-facing output is a claim like any other.

    `--print-plan` is what someone reads when deciding whether to trust these
    backups. It said "git bundle verify before upload" while the guarantee
    actually comes from the restore rehearsal — a sentence narrower than the
    code, describing the weaker half. Pinned so it cannot drift back.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    plan = src.split("def print_plan(")[1].split("\ndef ")[0]
    assert "RESTORE REHEARSAL" in plan
    assert "passes a corrupted" in plan


# --------------------------------------------------------------------------- #
# 5. retention
# --------------------------------------------------------------------------- #
def test_retention_keeps_the_newest_and_prunes_the_rest():
    up = FakeUploader()
    prefix = "h/scope-alpha/"
    for i in range(6):
        up.objects[f"{prefix}2026010{i}T000000Z.bundle.age"] = b"x"
    newest = f"{prefix}20260105T000000Z.bundle.age"
    doomed = B.prune(up, prefix, keep=3, just_uploaded=newest)
    assert len(doomed) == 3
    assert sorted(up.objects) == sorted([
        f"{prefix}20260103T000000Z.bundle.age",
        f"{prefix}20260104T000000Z.bundle.age",
        newest,
    ])


def test_retention_does_nothing_when_under_the_limit():
    up = FakeUploader()
    prefix = "h/scope-alpha/"
    up.objects[f"{prefix}20260101T000000Z.bundle.age"] = b"x"
    assert B.prune(up, prefix, keep=5,
                   just_uploaded=f"{prefix}20260101T000000Z.bundle.age") == []
    assert up.removed == []


def test_retention_refuses_when_the_listing_omits_the_new_object():
    """NEGATIVE CONTROL: pruning from a listing already caught being wrong would
    delete real backups."""
    up = FakeUploader()
    prefix = "h/scope-alpha/"
    for i in range(4):
        up.objects[f"{prefix}2026010{i}T000000Z.bundle.age"] = b"x"
    with pytest.raises(B.BackupError) as exc:
        B.prune(up, prefix, keep=1, just_uploaded=f"{prefix}NOT-THERE.bundle.age")
    assert "is not in the listing" in str(exc.value)
    assert up.removed == [], "it deleted objects before refusing"


@pytest.mark.parametrize("keep", [0, -1])
def test_retention_refuses_to_keep_nothing(keep):
    up = FakeUploader()
    up.objects["h/s/a.age"] = b"x"
    with pytest.raises(B.BackupError) as exc:
        B.prune(up, "h/s/", keep=keep, just_uploaded="h/s/a.age")
    assert "not a retention policy" in str(exc.value)
    assert up.removed == []


def test_retention_never_reaches_another_scope_or_host(tmp_path):
    """The prefix is per (host, scope). A prune that spanned scopes would
    evict good backups of one scope because another was written often."""
    up = FakeUploader()
    for i in range(4):
        up.objects[f"hostA/scope-alpha/2026010{i}T000000Z.bundle.age"] = b"x"
    up.objects["hostA/scope-beta/20260101T000000Z.bundle.age"] = b"x"
    up.objects["hostB/scope-alpha/20260101T000000Z.bundle.age"] = b"x"

    B.prune(up, "hostA/scope-alpha/", keep=1,
            just_uploaded="hostA/scope-alpha/20260103T000000Z.bundle.age")
    assert "hostA/scope-beta/20260101T000000Z.bundle.age" in up.objects
    assert "hostB/scope-alpha/20260101T000000Z.bundle.age" in up.objects


def test_the_object_key_separates_the_two_hosts(tmp_path):
    """🔴 Both machines are hostname `nixos` and their stores are DIVERGENT.
    A shared prefix would make retention on one host evict the other's backups."""
    from datetime import datetime, timezone
    t = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    a = B.object_key("workbench", "scope-alpha", t)
    b = B.object_key("laptop", "scope-alpha", t)
    assert a != b
    assert not a.startswith(B.scope_prefix("laptop", "scope-alpha"))
    assert a == "workbench/scope-alpha/20260102T030405Z.bundle.age"


def test_the_host_label_prefers_an_explicit_handle():
    os.environ["ASIB_HOST"] = "workbench"
    try:
        assert B.host_label() == "workbench"
    finally:
        os.environ.pop("ASIB_HOST", None)
    os.environ["ACTIVITY_HOST"] = "laptop"
    try:
        assert B.host_label() == "laptop"
    finally:
        os.environ.pop("ACTIVITY_HOST", None)


def test_the_host_label_is_sanitised_for_use_as_a_key_prefix():
    os.environ["ASIB_HOST"] = "weird/host name"
    try:
        assert "/" not in B.host_label() and " " not in B.host_label()
    finally:
        os.environ.pop("ASIB_HOST", None)


# --------------------------------------------------------------------------- #
# 6. discovery
# --------------------------------------------------------------------------- #
def test_a_symlinked_scope_is_not_followed(tmp_path, identity):
    """`commit.sh` refuses symlinked scopes because supporting them means
    widening its BindPaths. Following one here would bundle a repository from
    outside the store — a different confidentiality question entirely."""
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=1)
    outside = _make_scope(tmp_path / "elsewhere", "scope-outside", {"e.md": "y"}, commits=1)
    (store / "scope-linked").symlink_to(outside)

    names = [p.name for p in B.discover_scopes(store)]
    assert names == ["scope-alpha"], f"symlinked scope was followed: {names}"


def test_a_non_repo_directory_is_not_treated_as_a_scope(tmp_path):
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=1)
    (store / "not-a-repo").mkdir()
    (store / "not-a-repo" / "loose.md").write_text("x", encoding="utf-8")
    assert [p.name for p in B.discover_scopes(store)] == ["scope-alpha"]


def test_scopes_are_discovered_in_a_stable_order(tmp_path):
    store = tmp_path / "store"
    for n in ("scope-gamma", "scope-alpha", "scope-beta"):
        _make_scope(store, n, {"e.md": n}, commits=1)
    assert [p.name for p in B.discover_scopes(store)] == [
        "scope-alpha", "scope-beta", "scope-gamma"]


# --------------------------------------------------------------------------- #
# 7. 🔴 SEAM — a perfect producer wired to the wrong path is a dead feature
# --------------------------------------------------------------------------- #
def _home_nix() -> str:
    return HOME_NIX.read_text(encoding="utf-8")


def test_the_unit_execstart_resolves_to_the_file_these_tests_exercise():
    import re
    m = re.search(r'ExecStart\s*=\s*"([^"]*backup\.py[^"]*)"', _home_nix())
    assert m, "no systemd unit ExecStart references backup.py — the producer is not wired up"
    tail = m.group(1).split("%h/")[-1]
    assert tail.startswith("workspace/devrc/scripts/analyze-service-index/backup.py"), tail
    assert (ROOT / "scripts" / "analyze-service-index" / "backup.py").is_file()


def test_the_backup_unit_is_separate_from_the_commit_unit():
    """🔴 The commit unit runs with PrivateNetwork=true and a single writable
    BindPath. Backing up needs the network and must NOT be folded into it:
    adding network to that unit would destroy a containment control that took
    several measured rounds to get right."""
    src = _home_nix()
    assert "systemd.user.services.analyze-service-index-backup" in src
    assert "systemd.user.services.analyze-service-index-commit" in src
    assert "systemd.user.timers.analyze-service-index-backup" in src


def test_the_commit_unit_still_has_no_network():
    """A regression guard on the OTHER unit: this change must not have loosened it."""
    src = _home_nix()
    commit_block = src.split("systemd.user.services.analyze-service-index-commit")[1]
    commit_block = commit_block.split("systemd.user.services.analyze-service-index-backup")[0] \
        if "systemd.user.services.analyze-service-index-backup" in commit_block else commit_block
    commit_block = commit_block[:4000]
    assert "PrivateNetwork = true;" in commit_block, (
        "the commit unit lost PrivateNetwork — its no-exfiltration control")


def test_the_backup_unit_mounts_the_store_READ_ONLY():
    """Defence in depth for the read-only claim: even a bug in backup.py cannot
    write the store, because the namespace does not offer it writable."""
    src = _home_nix()
    block = src.split("systemd.user.services.analyze-service-index-backup")[1][:6000]
    assert "BindReadOnlyPaths" in block
    assert "analyze-service-index" in block
    assert "BindPaths" not in block.split("ExecStart")[0], (
        "the backup unit binds a WRITABLE path — the store must be read-only to it")


def test_the_backup_unit_puts_age_on_its_path():
    src = _home_nix()
    block = src.split("systemd.user.services.analyze-service-index-backup")[1][:6000]
    assert "pkgs.age" in block, "age is not on the backup unit's PATH"


def test_age_is_declared_as_a_package():
    assert "age" in PKGS_NIX.read_text(encoding="utf-8").split(), (
        "age is not in nix/pkgs/default.nix — it is not on PATH without it")


def test_age_is_available_to_the_pytest_gate():
    """🔴 A new hard test dependency that the gate does not provide turns this
    whole file into an import error in the sandbox."""
    assert "pkgs.age" in FLAKE_NIX.read_text(encoding="utf-8"), (
        "age is missing from flake.nix gateTools — these tests would fail to "
        "import inside `nix build .#checks.x86_64-linux.pytests`")


def test_the_producer_reuses_the_mail_actions_minio_helper():
    """One convention for reaching the tenant, not two. A second implementation
    is one more thing to rotate and the copy nobody exercises is the broken one."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "from _minio import MinioArchive" in src
    assert "mail-actions" in src
    assert (SCRIPTS / "mail-actions" / "_minio.py").is_file()
