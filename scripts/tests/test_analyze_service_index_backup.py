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
import re
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
SECRETS_MD = ROOT / "SECRETS.md"

sys.path.insert(0, str(SCRIPTS / "analyze-service-index"))
sys.path.insert(0, str(SCRIPTS))

import backup as B  # noqa: E402
from testlib.mockbin import write_exec  # noqa: E402
from testlib import hermetic_git  # noqa: E402


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
    # 🔴 Background maintenance OFF — `_manifest` hashes `.git`, so a
    # transient `.git/objects/maintenance.lock` reads as a repository change.
    # Broke CI twice before this was shared (#743, #780); see
    # scripts/testlib/hermetic_git.py for the measurement and why the
    # /dev/null pins above do NOT cover it.
    e.update(hermetic_git.MAINTENANCE_OFF)
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


def _bundle_ref_pairs(bundle: Path, work_dir: Path, store: Path) -> set[str]:
    """The refs a BUNDLE declares, normalised to `_refs`'s "<refname> <oid>" form.

    A TEST helper, deliberately not in the producer: `bundle_scope` compares the
    restore against the SCOPE by ref name and has no use for the bundle header
    (an object-level comparison against it was tried there and removed as
    unreachable — see the note in `bundle_scope`). The tests still need it to
    pin the `--mirror` decision, which is a production behaviour observed from
    outside.

    🔴 `git bundle list-heads` prints `<objectname> <refname>` — the REVERSE of
    `for-each-ref --format=%(refname) %(objectname)`. Flipped here so the two
    are comparable; getting it wrong would make every comparison mismatch, or
    (if both sides were flipped) compare nothing. It also reports `HEAD`, which
    `for-each-ref` never does, so non-`refs/` entries are dropped.
    """
    p = B._git_scratch(work_dir, "bundle", "list-heads", str(bundle), forbidden=store)
    assert p.returncode == 0, f"git bundle list-heads rc={p.returncode}: {p.stderr}"
    out = set()
    for ln in p.stdout.splitlines():
        parts = ln.strip().split(None, 1)
        if len(parts) != 2:
            continue
        oid, name = parts
        if not name.strip().startswith("refs/"):
            continue
        out.add(f"{name.strip()} {oid}")
    assert out, f"{bundle} declares no refs/* head — a comparison against it is vacuous"
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


def test_the_scratch_git_helper_REFUSES_a_cwd_inside_the_store(tmp_path):
    """🔴 The refusal `_git_scratch`'s docstring CLAIMED and did not implement.

    Before this, the docstring said the helper "refuses to run anywhere near the
    store" and there was no such check — a comment describing a guard that is
    not there, which is worse than no guard because it stops the next person
    looking. Watched to fail on this guard's own message, at the store root AND
    at a scope inside it (a boundary and an interior point).
    """
    store = tmp_path / "store"
    scope = _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=1)
    for cwd in (store, scope):
        with pytest.raises(B.BackupError) as exc:
            B._git_scratch(cwd, "clone", "--bare", str(tmp_path / "x.bundle"),
                           str(tmp_path / "out.git"), forbidden=store)
        assert "inside the /analyze-service index store" in str(exc.value), (
            f"cwd={cwd} was refused by some OTHER guard: {exc.value}")


def test_the_scratch_git_helper_ACCEPTS_a_cwd_outside_the_store(tmp_path):
    """POSITIVE CONTROL for the refusal above: it is not simply always red."""
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=1)
    elsewhere = tmp_path / "scratch"
    elsewhere.mkdir()
    p = B._git_scratch(elsewhere, "rev-parse", "--git-dir", forbidden=store)
    assert p.returncode != 0 and "not a git repository" in p.stderr.lower(), (
        f"expected git itself to answer, not the guard: rc={p.returncode} "
        f"{p.stderr!r}")


def test_the_scratch_guard_cannot_be_INHERITED_BY_FORGETTING_IT():
    """🔴 STRUCTURAL, not spelled: `forbidden` is required and keyword-only, so a
    later call site that omits it is a TypeError at the call rather than a
    silently unguarded write."""
    import inspect
    sig = inspect.signature(B._git_scratch)
    p = sig.parameters["forbidden"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY, p.kind
    assert p.default is inspect.Parameter.empty, (
        "`forbidden` has a default, so a call site can drop the guard without "
        "any error — which is exactly how the unimplemented version read")


@pytest.mark.parametrize("argv", [
    ("config", "--local", "user.name", "x"),
    ("gc", "--prune=all"),
    ("commit", "--allow-empty", "-m", "x"),
    ("push", "origin", "trunk"),
    ("fetch", "origin"),
    ("reflog", "expire", "--all"),
])
def test_git_refuses_a_write_subcommand(tmp_path, argv):
    """NEGATIVE CONTROL, watched to fail with THIS guard's own message.

    🔴 Every entry is a REAL git command line. The previous version parametrised
    `"remote-add"`, which is not a git subcommand at all: it could only ever
    prove that the allowlist rejects a string nobody would type, and it sat in
    the list looking exactly like coverage of `git remote add` — the write this
    file's own allowlist comment named as an example of what it refuses, and
    which was in fact ALLOWED. See the subverb tests below.
    """
    repo = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=1)
    with pytest.raises(B.BackupError) as exc:
        B._git(repo, *argv)
    assert "READ-ONLY to this script" in str(exc.value), (
        f"{argv[0]} was refused by some OTHER guard — this control is green for "
        f"the wrong reason")


def test_the_dispatcher_subverb_allowlist_is_pinned_exactly():
    """An EXACT mapping. `remote` and `bundle` are dispatchers whose subverbs are
    not all reads; widening either has to be a visible diff against this line."""
    assert B._ALLOWED_SUBVERBS == {
        "bundle": frozenset({"create", "verify"}),
        "remote": frozenset({None}),
    }
    assert set(B._ALLOWED_SUBVERBS) <= B._READ_ONLY_SUBCOMMANDS, (
        "a subverb policy names a verb the outer allowlist does not permit — it "
        "can never be reached and reads as coverage while providing none")


@pytest.mark.parametrize("argv,written", [
    (("remote", "add", "exfil", "file:///dev/null"), "config"),
    (("remote", "set-url", "origin", "file:///dev/null"), "config"),
    (("remote", "rename", "origin", "other"), "config"),
    (("bundle", "unbundle", "foreign.bundle"), "objects"),
])
def test_git_refuses_a_WRITING_SUBVERB_of_an_allowlisted_verb(tmp_path, argv, written):
    """🔴 THE GAP: the verb reads, the SUBVERB writes.

    `remote` and `bundle` are on the read-only allowlist because `git remote`
    lists and `git bundle create|verify` read. `git remote add` writes
    `.git/config`; `git bundle unbundle` writes a foreign packfile into the
    object store. Both were rc=0 through `_git()` before this, against the only
    copy of the data — while the allowlist's own comment named `git remote add`
    as an example of what it refused.

    Asserted on THIS guard's message (the verb guard has a different one), and
    on the repository being byte-identical afterwards.
    """
    repo = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=1)
    before = _manifest(repo)
    with pytest.raises(B.BackupError) as exc:
        B._git(repo, *argv)
    assert "is not a READ-ONLY mode of" in str(exc.value), (
        f"`git {' '.join(argv)}` was refused by the VERB guard, not the subverb "
        f"guard — this control would stay green with the subverb guard deleted: "
        f"{exc.value}")
    assert _manifest(repo) == before, f"the repository's {written} changed anyway"


def test_the_subverb_refusal_is_REACHABLE_and_the_writes_are_REAL(tmp_path):
    """🔴 POSITIVE CONTROL: without the guard these commands SUCCEED and MUTATE.

    A refusal test proves nothing about the hazard unless the thing refused
    would otherwise have happened. Run through raw git — the same command line,
    the same repository shape — and watch `.git/config` gain a remote and the
    object store gain a foreign pack. If this ever stops being true the guard
    above is guarding nothing and should be re-argued, not kept.
    """
    repo = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=1)
    cfg = repo / ".git" / "config"

    before = cfg.read_text(encoding="utf-8")
    p = _git_run(repo, "remote", "add", "exfil", "file:///dev/null")
    assert p.returncode == 0, f"raw `git remote add` failed: {p.stderr}"
    after = cfg.read_text(encoding="utf-8")
    assert after != before and 'remote "exfil"' in after, (
        "raw `git remote add` did not change .git/config — the refusal above "
        "refuses something that could not happen, and proves nothing")

    donor = _make_scope(tmp_path / "elsewhere", "scope-donor", {"d.md": "y"}, commits=2)
    foreign = tmp_path / "foreign.bundle"
    b = _git_run(donor, "bundle", "create", str(foreign), "--all")
    assert b.returncode == 0, b.stderr
    objs_before = sorted(str(q) for q in (repo / ".git" / "objects").rglob("*"))
    u = _git_run(repo, "bundle", "unbundle", str(foreign))
    assert u.returncode == 0, f"raw `git bundle unbundle` failed: {u.stderr}"
    objs_after = sorted(str(q) for q in (repo / ".git" / "objects").rglob("*"))
    assert objs_after != objs_before, (
        "raw `git bundle unbundle` wrote nothing into the object store — the "
        "refusal above refuses something that could not happen")


@pytest.mark.parametrize("argv", [
    ("remote",),
    ("remote", "-v"),
    ("bundle", "verify", "x.bundle"),
])
def test_the_subverb_guard_still_ALLOWS_the_read_only_forms(tmp_path, argv):
    """POSITIVE CONTROL for the subverb guard: it is not simply always red.

    Each of these must reach git (whatever git then says about the arguments),
    not the guard. Without this, tightening `_ALLOWED_SUBVERBS` to the empty set
    would satisfy every negative control in this file and break the producer.
    """
    repo = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=1)
    p = B._git(repo, *argv)          # must NOT raise
    assert isinstance(p, subprocess.CompletedProcess)


def test_the_producer_still_reads_the_remote_list_through_the_guard(tmp_path):
    """The one call site of the bare-verb form. `scope_remotes()` is what the
    no-remote invariant test rests on, so a subverb policy that refused `git
    remote` would make that invariant unmeasurable rather than false."""
    repo = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=1)
    assert B.scope_remotes(repo) == []
    assert _git_run(repo, "remote", "add", "origin", "file:///dev/null").returncode == 0
    assert B.scope_remotes(repo) == ["origin"], (
        "scope_remotes() cannot see a remote that IS there — the no-remote "
        "invariant test would pass on a scope that had gained one")


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
                       str(good), str(tmp_path / "rehearse.git"),
                       forbidden=tmp_path / "store")
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


def test_a_COMMIT_LANDING_MID_REHEARSAL_does_not_condemn_a_GOOD_bundle(
        tmp_path, monkeypatch):
    """🔴 THE COMMITTER/BACKUP RACE — a good bundle reported as corrupt.

    `analyze-service-index-commit` runs hourly and holds NO lock against this
    daily job. The rehearsal window it raced with contains a full bare clone of
    the scope, so it is not narrow. An earlier version read the scope's refs
    AFTER `bundle create` returned and required the restore to match them: one
    ordinary commit landing in that window moved `refs/heads/trunk`, the
    comparison mismatched, and the run reported

        "the bundle restores to a DIFFERENT set of refs than the scope holds"

    — losing that day's backup for the scope and telling the operator their
    archive was corrupt, while the bundle cloned cleanly.

    Reproduced here by committing to the scope the instant `bundle create`
    returns. RED at the parent of this fix, on that exact message; green now,
    because the restore is compared against the BUNDLE'S OWN declared heads.
    """
    scope = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=3)

    def commit_now(_bundle: Path, repo: Path):
        (repo / "e.md").write_text("the committer got there first\n", encoding="utf-8")
        _git_run(repo, "add", "e.md")
        r = _git_run(repo, "commit", "-q", "-m", "concurrent autocommit")
        assert r.returncode == 0, r.stderr

    _mangle_bundle_after_create(monkeypatch, commit_now)
    B.bundle_scope(scope, tmp_path / "out.bundle", tmp_path / "work")   # must NOT raise
    assert (tmp_path / "out.bundle").stat().st_size > 0


def test_the_race_repro_ACTUALLY_MOVES_A_REF(tmp_path, monkeypatch):
    """POSITIVE CONTROL for the test above: the injected commit must really move
    `refs/heads/trunk` between `bundle create` and the comparison.

    Without this, a hook that silently failed to commit would leave the race
    test passing for the wrong reason — it would be asserting that an unchanged
    scope produces a good bundle, which every other test here already says.
    """
    scope = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=3)
    fmt = "--format=%(refname) %(objectname)"
    seen: dict[str, set] = {}

    def commit_now(_bundle: Path, repo: Path):
        seen["before"] = B._refs(B._git(repo, "for-each-ref", fmt))
        (repo / "e.md").write_text("moved\n", encoding="utf-8")
        _git_run(repo, "add", "e.md")
        _git_run(repo, "commit", "-q", "-m", "concurrent autocommit")
        seen["after"] = B._refs(B._git(repo, "for-each-ref", fmt))

    _mangle_bundle_after_create(monkeypatch, commit_now)
    B.bundle_scope(scope, tmp_path / "out.bundle", tmp_path / "work")
    assert seen["before"] != seen["after"], (
        "the injected commit did not move any ref — the race test above is not "
        "exercising the race it claims to")
    assert {ln.split(" ", 1)[0] for ln in seen["before"]} == \
           {ln.split(" ", 1)[0] for ln in seen["after"]}, (
        "the injected commit changed the set of ref NAMES; the completeness "
        "guard would fire for a legitimate reason and this test would be "
        "measuring that instead")


def test_the_bundle_ref_reader_normalises_list_heads_output(tmp_path):
    """VALIDATE THE INSTRUMENT — the two `--mirror` tests below read a bundle's
    contents through `_bundle_ref_pairs`, so every claim they make is a claim
    about this reader until it has been watched to work.

    `git bundle list-heads` prints `<oid> <refname>`, the REVERSE of the
    `for-each-ref` format used everywhere else, and it also reports `HEAD`,
    which `for-each-ref` never does. A reader that did not flip the fields would
    disagree with every bundle git has ever made; one that flipped both sides
    would compare nothing at all.
    """
    scope = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=2)
    work = tmp_path / "work"
    bundle = tmp_path / "ok.bundle"
    B.bundle_scope(scope, bundle, work)

    declared = _bundle_ref_pairs(bundle, work, tmp_path / "store")
    fmt = "--format=%(refname) %(objectname)"
    live = B._refs(B._git(scope, "for-each-ref", fmt))
    assert declared, "list-heads produced NOTHING — the comparison is vacuous"
    assert declared == live, f"declared={declared} live={live}"
    for ln in declared:
        name, oid = ln.split(" ", 1)
        assert name.startswith("refs/"), f"fields are the wrong way round: {ln!r}"
        assert re.fullmatch(r"[0-9a-f]{40,64}", oid), f"not an object id: {ln!r}"


def _scope_with_a_non_branch_ref(tmp_path) -> Path:
    """A scope carrying `refs/notes/commits` and one other non-branch ref.

    Not exotic: `git notes` is an ordinary thing for a repository to acquire,
    and `git bundle create --all` captures every ref regardless.
    """
    scope = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=3)
    head = _git_run(scope, "rev-parse", "HEAD").stdout.strip()
    assert _git_run(scope, "notes", "add", "-m", "a note").returncode == 0
    assert _git_run(scope, "update-ref", "refs/weird/thing", head).returncode == 0
    return scope


def test_a_BARE_rehearsal_silently_drops_every_non_branch_ref(tmp_path):
    """🔴 THE MEASUREMENT BEHIND `--mirror`, pinned so nobody re-simplifies it.

    `git clone --bare` uses the default `+refs/heads/*:refs/heads/*` refspec and
    drops everything else WITHOUT SAYING SO. Measured 2026-08-22 from a bundle
    created with `--all` that declared all three refs below:

        --bare    restored refs/heads/trunk ONLY
        --mirror  restored all three

    Two separate defects, which is why this is pinned rather than trusted. A
    `--bare` rehearsal never checks that the non-branch refs come back at all,
    so it is not rehearsing the restore for them. And the comparison against the
    bundle's declared heads would then mismatch on every single run — a
    permanently-red gate on a disaster-recovery job, which trains everyone to
    ignore it.
    """
    scope = _scope_with_a_non_branch_ref(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    bundle = work / "b.bundle"
    assert B._git(scope, "bundle", "create", str(bundle), "--all").returncode == 0

    declared = {ln.split(" ", 1)[0] for ln in _bundle_ref_pairs(bundle, work, tmp_path / "store")}
    assert declared == {"refs/heads/trunk", "refs/notes/commits", "refs/weird/thing"}, declared

    fmt = "--format=%(refname) %(objectname)"
    seen = {}
    for mode in ("--bare", "--mirror"):
        tgt = work / f"r{mode.strip('-')}.git"
        c = B._git_scratch(work, "clone", mode, "--quiet", str(bundle), str(tgt),
                           forbidden=tmp_path / "store")
        assert c.returncode == 0, f"{mode}: {c.stderr}"
        seen[mode] = {ln.split(" ", 1)[0] for ln in
                      B._refs(B._git_scratch(tgt, "for-each-ref", fmt,
                                             forbidden=tmp_path / "store"))}
    assert seen["--bare"] == {"refs/heads/trunk"}, seen["--bare"]
    assert seen["--mirror"] == declared, seen["--mirror"]


def test_a_scope_with_a_NON_BRANCH_REF_backs_up_and_restores_INTACT(tmp_path, identity):
    """🔴 REACHABILITY for the `got != declared` comparison, and the regression
    guard for the `--bare` defect above.

    This is the input that makes the guard fire: with a `--bare` rehearsal the
    restore is missing two of the three refs the bundle declares, so the
    comparison goes red — and it would go red on every future run too. With
    `--mirror` the round trip is intact. Mutating `--mirror` back to `--bare` is
    therefore killed HERE, by this test, on this guard's own message.
    """
    scope = _scope_with_a_non_branch_ref(tmp_path)
    work = tmp_path / "work"
    B.bundle_scope(scope, work / "ok.bundle", work)     # must NOT raise

    declared = _bundle_ref_pairs(work / "ok.bundle", work, tmp_path / "store")
    fmt = "--format=%(refname) %(objectname)"
    live = B._refs(B._git(scope, "for-each-ref", fmt))
    assert declared == live, (
        f"the bundle does not carry the scope's non-branch refs: "
        f"declared={sorted(declared)} live={sorted(live)}")

    # The whole run, through the real producer, with the store byte-identical.
    before = _manifest(scope)
    up = FakeUploader()
    _run_backup(tmp_path / "store", tmp_path / "work2", up, identity)
    assert len(up.puts) == 1, up.puts
    assert _manifest(scope) == before, "the store changed during the run"


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
    c = B._git_scratch(work, "clone", "--bare", "--quiet", str(partial),
                       str(work / "r.git"), forbidden=tmp_path / "store")
    assert c.returncode == 0, "the partial bundle must clone fine, or this proves nothing"
    got = B._git_scratch(work / "r.git", "rev-list", "--count", "--all",
                         forbidden=tmp_path / "store").stdout.strip()
    assert got == "1" and B.commit_count(scope) == 4

    # Only the ref/commit comparison can see the shortfall.
    fmt = "--format=%(refname) %(objectname)"
    want = B._refs(B._git(scope, "for-each-ref", fmt))
    restored = B._refs(B._git_scratch(work / "r.git", "for-each-ref", fmt,
                                      forbidden=tmp_path / "store"))
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
# 4b. 🔴 the PLAINTEXT bundle — a full unencrypted copy of a confidential scope
# --------------------------------------------------------------------------- #
def test_the_PLAINTEXT_bundle_does_not_survive_a_successful_run(tmp_path, identity):
    """🔴 The artifact the whole design exists to avoid leaving anywhere.

    `git bundle create` writes the scope's entire history in the clear. It is
    supposed to exist only between that call and `age`. It was never deleted on
    any path — `PrivateTmp` cleaned up the default case under the unit, but the
    PR's own documented smoke run (`--no-upload --work-dir X` against the REAL
    store) has no namespace around it and left plaintext history on disk.
    """
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "canary-plaintext"}, commits=2)
    work = tmp_path / "work"
    _run_backup(store, work, FakeUploader(), identity)

    leftovers = sorted(p.name for p in work.iterdir() if p.name.endswith(".bundle"))
    assert leftovers == [], f"plaintext bundle(s) left behind: {leftovers}"
    for p in work.rglob("*"):
        if p.is_file():
            assert b"canary-plaintext" not in p.read_bytes(), (
                f"{p} holds the scope's content in the clear")


def test_the_PLAINTEXT_bundle_does_not_survive_a_FAILED_run(tmp_path, identity):
    """🔴 THE PATH THAT MATTERS MOST, and the one a success-path unlink misses.

    A failed encryption is exactly when a plaintext copy is most likely to be
    left behind and least likely to be noticed. `age` is made to fail after the
    bundle exists; the bundle must still be gone.
    """
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "canary-plaintext"}, commits=2)
    work = tmp_path / "work"

    def boom(plain, cipher, recipient):
        assert Path(plain).is_file(), "the bundle must exist, or this proves nothing"
        raise B.BackupError("synthetic encryption failure")

    real_encrypt = B.encrypt
    B.encrypt = boom
    try:
        with pytest.raises(B.BackupError):
            _run_backup(store, work, FakeUploader(), identity)
    finally:
        B.encrypt = real_encrypt

    leftovers = sorted(p.name for p in work.iterdir() if p.name.endswith(".bundle"))
    assert leftovers == [], f"a FAILED run left plaintext behind: {leftovers}"


def test_artifacts_are_not_readable_by_anyone_else(tmp_path, identity):
    """0600 files in a 0700 directory. Both were 0644/0755 under the operator's
    umask, on a real disk, holding a client-confidential scope."""
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=2)
    work = tmp_path / "work"
    up = FakeUploader()
    _run_backup(store, work, up, identity)

    assert (work.stat().st_mode & 0o077) == 0, (
        f"the work dir is {oct(work.stat().st_mode & 0o777)}, not 0700")
    ciphers = [p for p in work.iterdir() if p.name.endswith(".bundle.age")]
    assert ciphers, "no ciphertext to check — this test measured nothing"
    for c in ciphers:
        assert (c.stat().st_mode & 0o077) == 0, (
            f"{c.name} is {oct(c.stat().st_mode & 0o777)}, not 0600")


def test_the_permission_check_can_SEE_a_loose_mode(tmp_path):
    """POSITIVE CONTROL for the assertion above: a 0644 file must fail it.

    Without this, a check written against a mode that the umask already
    guarantees would report a tightening it never made.
    """
    d = tmp_path / "d"
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    f = d / "loose"
    f.write_text("x", encoding="utf-8")
    os.chmod(f, 0o644)
    assert (d.stat().st_mode & 0o077) != 0 and (f.stat().st_mode & 0o077) != 0, (
        "the mode check cannot distinguish 0755/0644 from 0700/0600 — every "
        "permission assertion in this file would be vacuous")


def test_an_EXISTING_work_dir_is_tightened_too(tmp_path, identity):
    """`mkdir(exist_ok=True)` does nothing to an existing directory's mode, and
    with an explicit `--work-dir` the second run onwards is exactly that case."""
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=1)
    work = tmp_path / "work"
    work.mkdir(mode=0o777)
    os.chmod(work, 0o777)
    assert (work.stat().st_mode & 0o077) != 0, "fixture is not loose; nothing to tighten"
    _run_backup(store, work, FakeUploader(), identity)
    assert (work.stat().st_mode & 0o077) == 0, (
        f"a pre-existing work dir stayed {oct(work.stat().st_mode & 0o777)}")


# --------------------------------------------------------------------------- #
# 4c. 🔴 per-scope isolation — one scope failing must not abandon the others
# --------------------------------------------------------------------------- #
class _ExplodingUploader(FakeUploader):
    """An uploader that raises a NON-BackupError, the way the real one does.

    `uploader.put/list/remove` go through the minio client, which raises
    `S3Error` and urllib3 connection errors. Neither is a BackupError, and the
    per-scope handler used to catch only BackupError — so one scope hitting a
    dead port-forward escaped the loop AND `run()`, abandoning every scope after
    it in the alphabet while the comment above the handler said "one scope
    failing must not stop the others".
    """

    def __init__(self, boom_on: str):
        super().__init__()
        self.boom_on = boom_on

    def put(self, key, data):
        if f"/{self.boom_on}/" in key:
            raise ConnectionResetError("synthetic: the port-forward died")
        super().put(key, data)


def test_a_NON_BackupError_in_one_scope_does_not_abandon_the_others(tmp_path, identity):
    """🔴 The isolation the comment claimed and the code did not provide."""
    store = tmp_path / "store"
    for n in ("scope-alpha", "scope-beta", "scope-gamma"):
        _make_scope(store, n, {"e.md": n}, commits=2)
    up = _ExplodingUploader(boom_on="scope-alpha")   # FIRST in sort order

    with pytest.raises(B.BackupError) as exc:
        _run_backup(store, tmp_path / "work", up, identity)

    assert "1 of 3 scope(s) failed" in str(exc.value), (
        f"the run did not report a partial failure: {exc.value}")
    assert "ConnectionResetError" in str(exc.value), (
        f"the failure's TYPE was dropped; for a non-BackupError it is most of "
        f"the diagnosis: {exc.value}")
    uploaded = sorted(k.split("/")[1] for k in up.puts)
    assert uploaded == ["scope-beta", "scope-gamma"], (
        f"scopes after the failing one were abandoned: {uploaded}. Alphabetical "
        f"order puts scope-alpha first precisely so this is observable.")


def test_the_exploding_uploader_control_raises_a_NON_BackupError():
    """VALIDATE THE INSTRUMENT: if the fixture raised a BackupError the test
    above would pass against the OLD handler and prove nothing."""
    up = _ExplodingUploader(boom_on="s")
    with pytest.raises(Exception) as exc:
        up.put("h/s/k.age", b"x")
    assert not isinstance(exc.value, B.BackupError), type(exc.value)


def test_the_CLI_frames_a_NON_BackupError_instead_of_bare_tracebacking(
        tmp_path, identity):
    """`main()` caught only BackupError, so anything else reached the
    interpreter's default handler: a traceback and no statement of what it means
    for the backup. The traceback is kept — for these the stack IS the
    diagnosis — and a sentence saying nothing was backed up is added."""
    store = tmp_path / "store"
    _make_scope(store, "scope-alpha", {"e.md": "x"}, commits=1)
    os.chmod(store, 0o000)
    try:
        # POSITIVE CONTROL: the fixture must really raise a non-BackupError, and
        # from OUTSIDE the per-scope loop (which now catches everything itself,
        # and would turn this into an ordinary BackupError).
        with pytest.raises(PermissionError):
            list(store.iterdir())
        r = _cli(store, "--no-upload", "--work-dir", str(tmp_path / "work"),
                 identity=identity)
    finally:
        os.chmod(store, 0o700)
    assert r.returncode == 1, f"rc={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "unexpected PermissionError" in r.stderr, (
        f"the failure was not framed for the operator:\n{r.stderr}")
    assert "Traceback" in r.stderr, (
        f"the traceback was swallowed; for a non-BackupError it is the "
        f"diagnosis:\n{r.stderr}")


# --------------------------------------------------------------------------- #
# 5. retention
# --------------------------------------------------------------------------- #
def test_retention_keeps_the_newest_and_prunes_the_rest():
    """🔴 THE FIXTURE IS INSERTED OUT OF ORDER, ON PURPOSE.

    `FakeUploader.list` returns keys in insertion order, and a real S3 listing
    is not ordered either. `prune()`'s `sorted()` is what turns a listing into
    chronology, and with a pre-sorted fixture a `sorted()` -> `list()` mutant
    SURVIVES a fully green suite — the fixture can only ever produce the order
    the assertion expects. Shuffled here so the sort is load-bearing.
    """
    up = FakeUploader()
    prefix = "h/scope-alpha/"
    for i in (3, 0, 5, 2, 4, 1):
        up.objects[f"{prefix}2026010{i}T000000Z.bundle.age"] = b"x"
    assert up.list(prefix) != sorted(up.list(prefix)), (
        "the fixture is already sorted — a sorted() -> list() mutant would "
        "survive this test")
    newest = f"{prefix}20260105T000000Z.bundle.age"
    doomed = B.prune(up, prefix, keep=3, just_uploaded=newest)
    assert len(doomed) == 3
    assert sorted(doomed) == [
        f"{prefix}20260100T000000Z.bundle.age",
        f"{prefix}20260101T000000Z.bundle.age",
        f"{prefix}20260102T000000Z.bundle.age",
    ], doomed
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


def test_retention_refuses_to_DELETE_THE_OBJECT_IT_JUST_UPLOADED():
    """🔴 MEMBERSHIP IS NOT SURVIVAL, and only survival is the property wanted.

    The membership refusal above asks whether the new object is SOMEWHERE in the
    listing. It says nothing about whether it is in the set about to be deleted,
    and there is a real way for it to be: lexical order is chronological order
    only while the clock moves forward. An NTP correction, or a laptop RTC
    restored after suspend, steps it BACKWARDS — today's key then sorts OLDEST.

    Reproduced here with keys dated ahead of the "new" one. Before the fix this
    run uploaded, verified, printed "verified", and deleted its own upload; the
    bucket silently stopped advancing while the timer stayed green — every
    future run doing the same. That is the false all-clear the whole file exists
    to prevent, so it is refused rather than reordered around.
    """
    up = FakeUploader()
    prefix = "h/scope-alpha/"
    # The bucket already holds backups stamped AFTER the one this run just made.
    for d in ("20260210", "20260211", "20260212"):
        up.objects[f"{prefix}{d}T000000Z.bundle.age"] = b"old-but-newer-looking"
    stale = f"{prefix}20260105T000000Z.bundle.age"      # the clock went backwards
    up.objects[stale] = b"the object this run just uploaded"

    with pytest.raises(B.BackupError) as exc:
        B.prune(up, prefix, keep=3, just_uploaded=stale)
    assert "DELETE THE OBJECT THIS RUN JUST UPLOADED" in str(exc.value), (
        f"refused by some OTHER guard — this control would stay green with the "
        f"survival check deleted: {exc.value}")
    assert up.removed == [], "it deleted objects before refusing"
    assert stale in up.objects, "the upload this run just made is gone"


def test_the_self_deletion_repro_IS_REACHED_past_the_membership_guard():
    """🔴 REACHABILITY: prove the earlier membership guard does not win first.

    A mutation still passes when an earlier check always fires, so the guard
    under test never executes. Here the just-uploaded key IS in the listing —
    membership is satisfied — and it is nonetheless first in sorted order. This
    asserts that shape directly, so the test above cannot be green because a
    different guard spoke.
    """
    up = FakeUploader()
    prefix = "h/scope-alpha/"
    for d in ("20260210", "20260211", "20260212"):
        up.objects[f"{prefix}{d}T000000Z.bundle.age"] = b"x"
    stale = f"{prefix}20260105T000000Z.bundle.age"
    up.objects[stale] = b"x"

    keys = sorted(up.list(prefix))
    assert stale in keys, "membership would fire first and the survival guard never runs"
    assert keys[0] == stale, "the just-uploaded key does not sort oldest; no self-delete"
    assert stale in keys[:-3], "the just-uploaded key is not in the doomed slice"


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


_BACKUP_SERVICE = "systemd.user.services.analyze-service-index-backup"
_BACKUP_TIMER = "systemd.user.timers.analyze-service-index-backup"
_COMMIT_SERVICE = "systemd.user.services.analyze-service-index-commit"
_COMMIT_TIMER = "systemd.user.timers.analyze-service-index-commit"


def _strip_nix_comments(block: str) -> str:
    """Drop whole-line `#` comments.

    These blocks are heavily commented and several comments QUOTE directives
    (`#   ProtectHome=read-only …`, `# BindPaths = [ … "-%h" ];`). A reader that
    did not strip them would answer questions about the prose instead of the
    configuration — and would do it silently.
    """
    return "\n".join(ln for ln in block.splitlines()
                     if not ln.lstrip().startswith("#"))


def _unit_block(start: str, end: str) -> str:
    """The source of exactly one systemd unit, bounded at both ends.

    Bounded at BOTH ends on purpose. The previous version of these tests took
    `src.split(marker)[1][:6000]` — everything after the marker, truncated at a
    byte count — so the window's contents depended on how long the comments
    happened to be and could run into whatever unit came next.
    """
    src = _home_nix()
    assert src.count(start) == 1, (
        f"{start!r} appears {src.count(start)}x in nix/home.nix; this reader "
        f"assumes exactly one and would otherwise slice an arbitrary one")
    assert src.count(end) == 1, (
        f"{end!r} appears {src.count(end)}x in nix/home.nix; the block below "
        f"would be bounded at the wrong place")
    body = src.split(start, 1)[1]
    assert end in body, f"{end!r} does not follow {start!r} — cannot bound the block"
    return _strip_nix_comments(body.split(end, 1)[0])


def _backup_block() -> str:
    return _unit_block(_BACKUP_SERVICE, _BACKUP_TIMER)


def _commit_block() -> str:
    return _unit_block(_COMMIT_SERVICE, _COMMIT_TIMER)


# 🔴 A CLOSED VOCABULARY of the systemd directives that decide what a unit can
# REACH. The assertion below compares a unit's intersection with this set
# against an exact expected mapping, which is what makes three different
# mutations one failure: a directive DELETED (key missing), a value FLIPPED
# (value differs) and a loosening directive ADDED (extra key) all produce a
# mapping that is not the expected one. An open "whatever the block contains"
# reader could not see the third.
_CONTAINMENT_VOCAB = frozenset({
    "ProtectSystem", "ProtectHome", "PrivateTmp", "PrivateNetwork",
    "PrivateDevices", "PrivateIPC", "PrivateUsers", "PrivateMounts",
    "NoNewPrivileges", "InaccessiblePaths", "ReadWritePaths", "ReadOnlyPaths",
    "BindPaths", "BindReadOnlyPaths", "TemporaryFileSystem", "RootDirectory",
    "RootImage", "DynamicUser", "ProtectProc", "ProcSubset",
    "RestrictNamespaces", "ProtectKernelTunables", "ProtectControlGroups",
    "ProtectKernelModules", "ProtectKernelLogs", "ProtectClock",
    "ProtectHostname", "RestrictAddressFamilies", "IPAddressDeny",
})


def _containment_directives(block: str) -> dict[str, str]:
    """{directive: normalised value} for every `_CONTAINMENT_VOCAB` name present."""
    import re
    out: dict[str, str] = {}
    for name in _CONTAINMENT_VOCAB:
        m = re.search(rf"^\s*{name}\s*=\s*(.*?);\s*$", block, re.M | re.S)
        if m:
            out[name] = " ".join(m.group(1).split())
    return out


def _environment_entries(block: str) -> list[str]:
    """The `Environment = [ … ];` entries of a unit, one per source line."""
    import re
    m = re.search(r"^\s*Environment\s*=\s*\[(.*?)^\s*\];\s*$", block, re.M | re.S)
    assert m, "the unit has no `Environment = [ … ];` list"
    out = []
    for ln in m.group(1).splitlines():
        ln = ln.strip()
        if not ln:
            continue
        assert ln.startswith('"') and ln.endswith('"'), (
            f"unparsed Environment entry {ln!r}: this reader takes one entry per "
            f"line and would otherwise make claims about a string it did not "
            f"understand")
        out.append(ln[1:-1])
    return out


# 🔴 THE EXACT CONTAINMENT OF THE BACKUP UNIT. Every value here is load-bearing;
# see the comment block above the unit in nix/home.nix for the systemd-run
# measurement behind `ProtectHome` and for why the bind list is frozen.
_EXPECTED_BACKUP_CONTAINMENT = {
    "ProtectSystem": '"strict"',
    "ProtectHome": '"tmpfs"',
    "InaccessiblePaths": '[ "/dev/shm" "/dev/mqueue" ]',
    "PrivateTmp": "true",
    "NoNewPrivileges": "true",
    "BindReadOnlyPaths": (
        '[ "%h/workspace/devrc/scripts" '
        '"-%h/.claude/analyze-service-index" '
        '"-%h/workspace/homelab-talos/.secrets/age.key" '
        '"-%h/workspace/homelab-talos/homelab-kubeconfig" '
        '"-%h/.kube/homelab-nebula.yaml" ]'
    ),
}


def test_the_home_nix_directive_reader_is_VALIDATED_before_it_is_believed():
    """🔴 VALIDATE THE INSTRUMENT. Every assertion about nix/home.nix below is a
    claim about this reader until the reader itself has been watched to work.

    Positive control: it must see all three value shapes that occur (a quoted
    scalar, a bare `true`, a multi-line list). Negative controls: a DELETED
    directive must be observably absent and a CHANGED value observably
    different — a reader that returned a hardcoded mapping, or one whose regex
    never matched, would satisfy neither.
    """
    synth = '''
      ProtectSystem = "strict";
      ProtectHome = "tmpfs";
      BindReadOnlyPaths = [
        "a"
        "b"
      ];
      PrivateTmp = true;
      # ProtectHome = "read-only";   <- a comment, not configuration
    '''
    got = _containment_directives(_strip_nix_comments(synth))
    assert got == {
        "ProtectSystem": '"strict"',
        "ProtectHome": '"tmpfs"',
        "BindReadOnlyPaths": '[ "a" "b" ]',
        "PrivateTmp": "true",
    }, got

    deleted = _containment_directives(
        _strip_nix_comments(synth.replace('ProtectHome = "tmpfs";', "")))
    assert "ProtectHome" not in deleted, (
        "the reader reports a directive that is NOT in the source — every "
        "'the unit has X' assertion below would be vacuous")

    flipped = _containment_directives(
        _strip_nix_comments(synth.replace('ProtectHome = "tmpfs";',
                                          'ProtectHome = "read-only";')))
    assert flipped["ProtectHome"] == '"read-only"', (
        "the reader cannot see a directive's VALUE change — it would report "
        "`tmpfs` for a unit that ships `read-only`")


def test_the_unit_block_reader_separates_the_two_units():
    """POSITIVE CONTROL on the block bounding, using the fact that the two units
    are deliberately DIFFERENT.

    The committer has `PrivateNetwork` and a WRITABLE `BindPaths`; the backup
    unit has neither (it needs a network, and the store must be read-only to
    it). If the bounding leaked one block into the other — or returned the whole
    file — these disagreements would vanish and both units' assertions would go
    green for the wrong reason.
    """
    b = _containment_directives(_backup_block())
    c = _containment_directives(_commit_block())
    assert c, "the committer block yielded NO directives — the reader walked nothing"
    assert b, "the backup block yielded NO directives — the reader walked nothing"
    assert "PrivateNetwork" in c and "PrivateNetwork" not in b, (
        f"the two blocks are not being separated: PrivateNetwork in "
        f"commit={'PrivateNetwork' in c} backup={'PrivateNetwork' in b}")
    assert "BindPaths" in c and "BindPaths" not in b
    assert c["BindReadOnlyPaths"] != b["BindReadOnlyPaths"]


def test_the_backup_unit_pins_its_CONTAINMENT_directives_exactly():
    """🔴 THE CONTAINMENT, PINNED AS AN EXACT MAPPING — the guard this suite
    previously did not have.

    Measured before this test existed: a mutant that DELETED `ProtectHome`,
    repointed the store bind at a nonexistent decoy path, and widened the age
    key bind to the whole `.secrets/` DIRECTORY passed all 64 tests. The only
    assertion in the area required the substring "analyze-service-index" to
    appear somewhere in the block — which the service's own NAME satisfies — and
    nothing anywhere mentioned ProtectHome, ProtectSystem, PrivateTmp,
    InaccessiblePaths or NoNewPrivileges at all.

    🔴 This is a SEAM guard, not a component one: no test in this repo can run
    systemd, so the suite can pin what the unit DECLARES and never that it
    works. The measurements behind each value are in nix/home.nix beside the
    directive, taken with `systemd-run --user`; this test is what stops them
    drifting away from what ships.
    """
    got = _containment_directives(_backup_block())

    for name, want in sorted(_EXPECTED_BACKUP_CONTAINMENT.items()):
        assert name in got, (
            f"the backup unit has NO `{name}` directive. Its containment is not "
            f"defence in depth around backup.py — it IS the control, because "
            f"this is the one unit here with a network. Expected {name} = {want};")
        assert got[name] == want, (
            f"the backup unit's `{name}` is `{got[name]}`, expected `{want}`.")

    extra = {k: v for k, v in got.items() if k not in _EXPECTED_BACKUP_CONTAINMENT}
    assert not extra, (
        f"the backup unit gained containment directive(s) {sorted(extra)} that "
        f"this test does not know about. Adding one without a `systemd-run "
        f"--user` measurement is how a declared-but-ineffective hardening line "
        f"ships (see the committer's comment on PrivateDevices/PrivateIPC). Add "
        f"it to _EXPECTED_BACKUP_CONTAINMENT with the measurement in the same "
        f"commit.")
    assert got == _EXPECTED_BACKUP_CONTAINMENT


def test_the_backup_unit_does_not_leave_HOME_READABLE():
    """🔴 THE SPECIFIC HAZARD, NAMED, so a future `read-only` cannot slip past as
    a plausible-looking value.

    `ProtectHome=read-only` makes $HOME unwritable and leaves it fully READABLE,
    which means the bind list confers nothing — it is a list of things already
    visible. MEASURED 2026-08-22 with `systemd-run --user`, this unit's exact
    directive set otherwise, probing readability from inside the namespace:

        read-only  ~/.ssh/id_ed25519 READABLE, ~/.kube/config READABLE,
                   ~/workspace/homelab-talos/.secrets/ listed 6 entries
        tmpfs      ~/.ssh ABSENT, ~/.kube/config ABSENT,
                   .secrets/ listed 1 entry — the bound key file

    `read-only` is the plausible wrong answer here (it is even the tidier-
    sounding one), so it gets its own assertion and its own sentence.
    """
    ph = _containment_directives(_backup_block()).get("ProtectHome")
    assert ph == '"tmpfs"', (
        f"the backup unit's ProtectHome is {ph!r}. Only `tmpfs` makes $HOME "
        f"disappear; `read-only` leaves ~/.ssh, ~/.kube/config and every "
        f"sibling of the age key in ~/workspace/homelab-talos/.secrets/ "
        f"READABLE to a unit that has a network.")


def test_the_backup_unit_binds_the_age_key_as_a_FILE_not_its_directory():
    """The narrow bind is the whole reason the sibling cluster credentials in
    `.secrets/` stay out of a networked unit's namespace. Widening it to the
    directory is a one-token change that looks like a convenience."""
    binds = _containment_directives(_backup_block())["BindReadOnlyPaths"]
    assert '"-%h/workspace/homelab-talos/.secrets/age.key"' in binds, binds
    assert '.secrets"' not in binds and '.secrets/"' not in binds, (
        f"the backup unit binds the `.secrets` DIRECTORY, not just the age key: "
        f"{binds}. That directory holds cluster credentials this job has no "
        f"business seeing.")


def test_the_backup_unit_pins_a_PER_HOST_key_prefix():
    """🔴 `ASIB_HOST` IS THE ONLY THING SEPARATING THE TWO HOSTS' BACKUPS.

    Measured: deleting this one Environment line leaves all 64 other tests
    green, and `host_label()` then falls through to `socket.gethostname()` —
    which is `nixos` on BOTH machines. The two stores are DIVERGENT content, so
    a shared key prefix makes each host's retention pass evict the other's
    backups: the backup becomes a second way to lose the data, visibly only as
    archives going missing.
    """
    env = _environment_entries(_backup_block())
    hosts = [e for e in env if e.startswith("ASIB_HOST=")]
    assert len(hosts) == 1, (
        f"the backup unit sets ASIB_HOST {len(hosts)}x (expected exactly once) "
        f"in {env}. Without it both hosts label their objects `nixos` and "
        f"overwrite each other under retention.")
    value = hosts[0].split("=", 1)[1]
    assert "%m" in value, (
        f"ASIB_HOST={value!r} does not contain systemd's `%m` machine ID. A "
        f"readable name alone is not enough: `isLaptop` is a backlight probe "
        f"that fails OPEN, so an ACPI-only laptop would label itself "
        f"`workbench` and collide silently.")
    assert "isLaptop" in value, (
        f"ASIB_HOST={value!r} carries no human-readable host name; %m alone is "
        f"unreadable in a bucket listing.")


def test_without_an_explicit_host_handle_the_two_hosts_COLLIDE():
    """The consequence the test above prevents, measured rather than asserted.

    POSITIVE CONTROL for it: with both handles unset the label is the bare
    hostname, which is `nixos` on both machines — so the unit's Environment line
    is doing real work, not decorating a value that was already distinct.
    """
    import re as _re
    import socket as _socket
    saved = {k: os.environ.pop(k, None) for k in ("ASIB_HOST", "ACTIVITY_HOST")}
    try:
        assert B.host_label() == _re.sub(
            r"[^A-Za-z0-9._-]", "-", _socket.gethostname() or "unknown"), (
            "host_label() no longer falls back to the hostname; the collision "
            "this documents may have moved rather than been fixed")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


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
    got = _containment_directives(_commit_block())
    assert got.get("PrivateNetwork") == "true", (
        f"the commit unit lost PrivateNetwork — its no-exfiltration control. "
        f"Its containment reads {got}")
    assert got.get("ProtectHome") == '"tmpfs"', (
        f"the commit unit's ProtectHome is {got.get('ProtectHome')!r}; `tmpfs` "
        f"is what makes its single BindPath the only part of $HOME it can see")


def test_the_backup_unit_mounts_the_store_READ_ONLY():
    """Defence in depth for the read-only claim: even a bug in backup.py cannot
    write the store, because the namespace does not offer it writable.

    🔴 This test used to assert that the substring "analyze-service-index"
    appeared somewhere in the block — which the SERVICE'S OWN NAME satisfies, so
    it held for a unit whose store bind pointed at a nonexistent decoy path. It
    now names the REAL path and requires it in the read-only list specifically.
    """
    got = _containment_directives(_backup_block())
    assert '"-%h/.claude/analyze-service-index"' in got["BindReadOnlyPaths"], (
        f"the backup unit does not bind the real store path read-only; its "
        f"BindReadOnlyPaths is {got['BindReadOnlyPaths']}. A bind pointing "
        f"somewhere else leaves the unit backing up nothing, or backing up "
        f"whatever is at the decoy path.")
    assert "BindPaths" not in got, (
        "the backup unit binds a WRITABLE path — the store must be read-only to "
        "it. `BindPaths` is the committer's directive, not this one's.")


def test_the_backup_unit_puts_age_on_its_path():
    path = [e for e in _environment_entries(_backup_block()) if e.startswith("PATH=")]
    assert len(path) == 1, f"expected exactly one PATH entry, got {path}"
    assert "pkgs.age" in path[0], f"age is not on the backup unit's PATH: {path[0]}"


def test_age_is_declared_as_a_package():
    assert "age" in PKGS_NIX.read_text(encoding="utf-8").split(), (
        "age is not in nix/pkgs/default.nix — it is not on PATH without it")


def test_age_is_available_to_the_pytest_gate():
    """🔴 A new hard test dependency that the gate does not provide turns this
    whole file into an import error in the sandbox."""
    assert "pkgs.age" in FLAKE_NIX.read_text(encoding="utf-8"), (
        "age is missing from flake.nix gateTools — these tests would fail to "
        "import inside `nix build .#checks.x86_64-linux.pytests`")


# --------------------------------------------------------------------------- #
# 8. 🔴 the documented RESTORE RECIPE — the only part of this a human executes
# --------------------------------------------------------------------------- #
def test_the_documented_restore_recipe_DROPS_THE_REMOTE_the_clone_adds(tmp_path):
    """🔴 `git clone <bundle> <dir>` SETS `origin` TO THE BUNDLE PATH.

    That breaks the `remote = none` invariant every scope README, `commit.sh`'s
    PrivateNetwork unit and this feature's own `test_no_scope_gains_a_remote`
    rest on — and it breaks it at the one moment a human is following written
    instructions under pressure. The recipe must say to remove it.

    POSITIVE CONTROL first: prove the clone really does add the remote, so the
    documented step is fixing something that happens rather than reading as a
    precaution nobody needs.
    """
    scope = _make_scope(tmp_path / "store", "scope-alpha", {"e.md": "x"}, commits=2)
    bundle = tmp_path / "restore.bundle"
    B.bundle_scope(scope, bundle, tmp_path / "work")

    dest = tmp_path / "restored"
    p = subprocess.run([GIT, "clone", str(bundle), str(dest)],
                       capture_output=True, text=True, env=_git_env())
    assert p.returncode == 0, p.stderr
    assert _git_run(dest, "remote").stdout.split() == ["origin"], (
        "a clone from a bundle did NOT gain a remote — the documented removal "
        "step would be guarding nothing")

    assert _git_run(dest, "remote", "remove", "origin").returncode == 0
    assert _git_run(dest, "remote").stdout.strip() == "", (
        "the documented remedy does not actually clear the remote")

    doc = SECRETS_MD.read_text(encoding="utf-8")
    assert "remote remove origin" in doc, (
        "SECRETS.md's restore recipe leaves the restored scope with a remote "
        "pointing at the bundle — the invariant the whole store is built on")


def test_the_documented_object_key_matches_the_one_the_code_WRITES():
    """The recipe named the artifact `<scope>-<stamp>.bundle.age`. The real key
    is `<host>/<scope>/<stamp>.bundle.age` — a different shape, not just a
    different spelling, and you cannot fetch an object by a name it does not
    have. Pinned against `object_key()` rather than against a remembered
    string."""
    from datetime import datetime, timezone
    key = B.object_key("workbench-abc", "scope-alpha",
                       datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
    assert key == "workbench-abc/scope-alpha/20260102T030405Z.bundle.age"

    doc = SECRETS_MD.read_text(encoding="utf-8")
    assert "<host>/<scope>/<UTC stamp>.bundle.age" in doc, (
        "SECRETS.md does not state the real key shape")
    assert "<scope>-<stamp>.bundle.age" not in doc, (
        "SECRETS.md still names the artifact by a key that does not exist")


def test_the_restore_recipe_tells_you_how_to_RETRIEVE_the_object():
    """🔴 The recipe began at "you already have the .age file", which is the one
    thing you will not have in the scenario it exists for: the local disk is
    gone. It must name the bucket, the bridge, and a listing step."""
    doc = SECRETS_MD.read_text(encoding="utf-8")
    recipe = doc.split("**Restoring**", 1)[1].split("\n---", 1)[0]
    for needle in ("list_objects", "get_object", "analyze-service-index-backups",
                   "MinioArchive", "KUBECONFIG"):
        assert needle in recipe, (
            f"the restore recipe never mentions {needle!r} — it does not say "
            f"how to get the object out of MinIO in the first place")


def test_the_python_in_the_restore_recipe_is_SYNTACTICALLY_VALID():
    """VALIDATE THE INSTRUCTION. A recipe nobody has run is a claim; the cheapest
    part of it to check mechanically is that the python it embeds compiles."""
    doc = SECRETS_MD.read_text(encoding="utf-8")
    recipe = doc.split("**Restoring**", 1)[1].split("\n---", 1)[0]
    snippets = re.findall(r'python3 -c "\n(.*?)\n"', recipe, re.S)
    assert len(snippets) == 2, (
        f"expected the two documented python3 -c snippets, found {len(snippets)}")
    for i, s in enumerate(snippets):
        # The doc escapes double quotes for the surrounding shell; undo that.
        compile(s.replace('\\"', '"'), f"<SECRETS.md snippet {i}>", "exec")


def test_the_producer_reuses_the_mail_actions_minio_helper():
    """One convention for reaching the tenant, not two. A second implementation
    is one more thing to rotate and the copy nobody exercises is the broken one."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "from _minio import MinioArchive" in src
    assert "mail-actions" in src
    assert (SCRIPTS / "mail-actions" / "_minio.py").is_file()


# --------------------------------------------------------------------------- #
# 12. 🔴 WHICH REPOSITORY — a leaked git repo-pointer must not aim this
#     program, which HAS A NETWORK, at a foreign repository (clawgate #343)
# --------------------------------------------------------------------------- #
#
# THE MECHANISM. Every git call in backup.py is `git -C <scope> …`, and `-C` is
# the weakest possible claim about where a command lands: GIT_DIR OVERRIDES IT.
# The pre-fix `_git_env()` built from `dict(os.environ)` and added five config
# keys, stripping none of the repo-pointer family.
#
# MEASURED 2026-08-23, git 2.55.0, driving the real CLI end to end
# (`--no-upload --work-dir`) under the systemd unit's own environment SHAPE
# (`env -i` plus PATH/HOME/SOPS_AGE_KEY_FILE/ASIB_HOST — NOT the operator's
# shell, whose GIT_AUTHOR_* would fix a dimension the unit does not have), on
# the pre-fix tree at d7d682f0. One variable at a time, exit code included:
#
#   VARIABLE                          rc   the BUNDLE declared        decoy
#   ------------------------------------------------------------------------
#   (control: no leak)                 0   refs/heads/trunk           identical
#   GIT_DIR                            0   refs/heads/foreign-branch  identical  <- EXFILTRATION
#   GIT_WORK_TREE                      0   refs/heads/trunk           identical
#   GIT_COMMON_DIR                     1   (no bundle: rev-list 128)  identical
#   GIT_INDEX_FILE                     0   refs/heads/trunk           identical
#   GIT_OBJECT_DIRECTORY               1   (no bundle: rev-list 128)  identical
#   GIT_ALTERNATE_OBJECT_DIRECTORIES   0   refs/heads/trunk           identical
#   GIT_NAMESPACE                      0   refs/heads/trunk           identical
#   GIT_PREFIX                         0   refs/heads/trunk           identical
#   GIT_GRAFT_FILE                     0   refs/heads/trunk           identical
#   GIT_SHALLOW_FILE                   0   refs/heads/trunk           identical
#   GIT_CONFIG                         0   refs/heads/trunk           identical
#
# 🔴 EVERY ROW WAS RUN, exit code included. #721 shipped a table row claiming
# `rc 0` where the truth was `rc 1`, because its probe exported GIT_AUTHOR_*.
#
# 🔴 READ THE SECOND COLUMN, NOT THE THIRD. The exfiltration signature is a
# bundle full of the WRONG repository's refs while the foreign repo sits there
# byte-identical — a read-only theft leaves nothing behind. A test that only
# asserted "the decoy did not move" would have called the GIT_DIR row clean.
#
# 🔴 AND THE RESTORE REHEARSAL IN `bundle_scope` CANNOT CATCH IT: `want_names`
# is read with `_git(scope, "for-each-ref")`, through the same poisoned
# environment, so both sides report the decoy's refs and agree. rc=0, "verified".
# That is a second sample of the unknown, not a control. Independently
# re-measured here (card criterion 1) — the audit's two reported claims HOLD.
#
# The fix is the `strip_repo_pointers()` call in `_git_env()`, using the ledger
# `testlib/gitenv.py` OWNS. Post-fix every row above is rc=0 / refs/heads/trunk,
# including the two that used to fail outright.

# 🔴 PARAMETRISED FROM THE LEDGER'S OWNER, NOT FROM `B`. Importing it here
# rather than reading `B.REPO_POINTER_VARS` is what lets these tests COLLECT
# against the pre-fix producer, where that attribute does not exist: parametrise
# off `B` and the whole section dies at collection with an AttributeError, which
# is red for an earlier check's reason and proves nothing about the guard
# (claude/RULES.md → "prove it REACHABLE, not just breakable"). Whether the
# producer uses this same object is a SEPARATE assertion, below.
from testlib.gitenv import REPO_POINTER_VARS as _LEDGER  # noqa: E402

_POINTER_VERDICTS: dict[str, str] = {
    # name                             -> what the PRE-FIX tree did with it
    "GIT_DIR": "spoofed: the bundle carried the DECOY's refs at rc=0",
    "GIT_WORK_TREE": "no effect: the scope's own refs, rc=0",
    "GIT_COMMON_DIR": "broke the run: rev-list rc=128, no bundle, rc=1",
    "GIT_INDEX_FILE": "no effect: the scope's own refs, rc=0",
    "GIT_OBJECT_DIRECTORY": "broke the run: rev-list rc=128, no bundle, rc=1",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES": "no effect: the scope's own refs, rc=0",
    "GIT_NAMESPACE": "no effect: the scope's own refs, rc=0",
    "GIT_PREFIX": "no effect: the scope's own refs, rc=0",
    "GIT_GRAFT_FILE": "no effect: the scope's own refs, rc=0",
    "GIT_SHALLOW_FILE": "no effect: the scope's own refs, rc=0",
    "GIT_CONFIG": "no effect: the scope's own refs, rc=0",
}

# The pre-fix rows that are REGRESSION coverage rather than invariant guards:
# these are the parametrisations watched RED at d7d682f0. The rest are labelled
# invariant guards below and are NOT counted as regression coverage.
_RED_AT_BASE_SECURITY = ("GIT_DIR",)
_RED_AT_BASE_AVAILABILITY = ("GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY")


def _leak_value(var: str, decoy: Path) -> str:
    """A REALISTIC value for `var` that aims git at `decoy`.

    Realistic, not a textbook fixture: each is what the variable would actually
    hold if it had leaked out of a session working in that repository.
    """
    return {
        "GIT_DIR": str(decoy / ".git"),
        "GIT_WORK_TREE": str(decoy),
        "GIT_COMMON_DIR": str(decoy / ".git"),
        "GIT_INDEX_FILE": str(decoy / ".git" / "index"),
        "GIT_OBJECT_DIRECTORY": str(decoy / ".git" / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(decoy / ".git" / "objects"),
        "GIT_NAMESPACE": "foreignns",
        "GIT_PREFIX": "sub/",
        "GIT_GRAFT_FILE": str(decoy / ".git" / "info" / "grafts"),
        "GIT_SHALLOW_FILE": str(decoy / ".git" / "shallow"),
        "GIT_CONFIG": str(decoy / ".git" / "config"),
    }[var]


def _git_dir_fingerprint(git_dir: Path) -> dict[str, str]:
    """sha256 of EVERY file under a git dir — `objects/` INCLUDED.

    🔴 `objects/` IS THE POINT. #721's foreign-repo fingerprint omitted it, and
    that blindness produced a confident, wrong "harmless" verdict for
    GIT_OBJECT_DIRECTORY — the variable that decides where NEW OBJECTS ARE
    WRITTEN, i.e. the one whose whole damage class lives in the directory the
    fingerprint could not see. Walking the entire git dir covers `objects/`,
    `refs/`, `HEAD`, `config`, `logs/` and `index` by construction, and covers
    whatever git grows next without anyone remembering to add it.
    """
    out: dict[str, str] = {}
    for dirpath, _dirs, files in os.walk(git_dir):
        for name in files:
            p = Path(dirpath) / name
            rel = p.relative_to(git_dir).as_posix()
            try:
                out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError as exc:  # unreadable is a CHANGE we must not hide
                out[rel] = f"<unreadable {exc.__class__.__name__}>"
    return out


def test_the_foreign_fingerprint_covers_the_categories_it_CLAIMS(tmp_path):
    """🔴 VALIDATE THE INSTRUMENT BEFORE READING ITS VERDICT.

    A fingerprint helper that walked nothing would return `{}` and make every
    byte-identity assertion below pass vacuously — and one that walked only refs
    would report "harmless" for an object-store write, which is exactly the
    blind spot this section exists to not repeat. So: it must SEE `objects/`,
    `refs/`, `HEAD`, `config`, `logs/` and `index`, and it must MOVE when each
    of those changes.
    """
    repo = _make_scope(tmp_path / "store", "probe", {"a.md": "x"})
    git_dir = repo / ".git"

    fp = _git_dir_fingerprint(git_dir)
    assert fp, "the fingerprint walked nothing — every comparison below is vacuous"
    for needed, why in (
        ("objects/", "#721's blind spot: where GIT_OBJECT_DIRECTORY writes"),
        ("refs/", "where a branch lands"),
        ("HEAD", "which branch the repo is on"),
        ("config", "the incident's config damage"),
        ("logs/", "the reflog"),
        ("index", "what a stage writes"),
    ):
        assert any(k == needed or k.startswith(needed) for k in fp), (
            f"the fingerprint does not cover {needed!r} — {why}. Present: "
            f"{sorted(fp)[:20]}")

    def moved(before, after):
        return sorted(k for k in set(before) | set(after)
                      if before.get(k) != after.get(k))

    # POSITIVE CONTROL, per category: watch the fingerprint move for each one.
    before = _git_dir_fingerprint(git_dir)
    (repo / "b.md").write_text("new\n", encoding="utf-8")
    _git_run(repo, "add", "b.md")
    _git_run(repo, "commit", "-q", "-m", "second")
    delta = moved(before, _git_dir_fingerprint(git_dir))
    assert any(k.startswith("objects/") for k in delta), (
        f"a real commit wrote no object the fingerprint could see: {delta}")
    assert any(k.startswith("refs/") or k == "packed-refs" for k in delta), delta
    assert any(k.startswith("logs/") for k in delta), delta
    assert "index" in delta, delta

    before = _git_dir_fingerprint(git_dir)
    _git_run(repo, "config", "user.name", "mutated")
    assert "config" in moved(before, _git_dir_fingerprint(git_dir))

    before = _git_dir_fingerprint(git_dir)
    _git_run(repo, "symbolic-ref", "HEAD", "refs/heads/elsewhere")
    assert "HEAD" in moved(before, _git_dir_fingerprint(git_dir))


def _unit_shaped_env(home: Path, identity: Path) -> dict:
    """The systemd unit's environment SHAPE, not the operator's shell.

    🔴 `env -i` plus exactly what nix/home.nix sets: PATH, HOME, the age key
    handle, ASIB_HOST. NOT `dict(os.environ)`. #721 shipped a measured table row
    with the wrong exit code because its probe exported
    GIT_AUTHOR_*/GIT_COMMITTER_*, fixing a dimension the unit does not have — a
    probe built from the harness's own environment measures the harness.
    """
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "SOPS_AGE_KEY_FILE": str(identity),
        "ASIB_HOST": "synthetic-host",
    }


def _run_isolated(store: Path, work: Path, home: Path, identity: Path,
                  leak: dict | None = None) -> subprocess.CompletedProcess:
    env = _unit_shaped_env(home, identity)
    env.update(leak or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--store", str(store),
         "--no-upload", "--work-dir", str(work)],
        capture_output=True, text=True, env=env)


def _bundle_heads_cleanly(bundle: Path, home: Path) -> list[str]:
    """`git bundle list-heads`, read with an environment WITHOUT the leak.

    🔴 The artifact must be inspected through a channel the leaked variable does
    not touch. Reading it back through the same poisoned environment is the
    mistake `bundle_scope`'s own cross-check makes — a second sample of the
    unknown, which is why that check reports "verified" over a foreign bundle.
    """
    p = subprocess.run(
        [GIT, "bundle", "list-heads", str(bundle)],
        capture_output=True, text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(home)})
    assert p.returncode == 0, f"git bundle list-heads rc={p.returncode}: {p.stderr}"
    return [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]


@pytest.fixture()
def leak_bed(tmp_path, identity):
    """A scope to back up, a DECOY repository to try to steal, and a fake HOME."""
    home = tmp_path / "home"
    home.mkdir()
    store = tmp_path / "store"
    scope = _make_scope(store, "some-scope", {"NOTES.md": "scope content"})

    decoy = tmp_path / "decoy"
    decoy.mkdir()
    subprocess.run([GIT, "init", "-q", "-b", "foreign-branch", str(decoy)],
                   check=True, capture_output=True, env=_git_env())
    (decoy / "SECRET.md").write_text("foreign secret content\n", encoding="utf-8")
    _git_run(decoy, "add", "SECRET.md")
    _git_run(decoy, "commit", "-q", "-m", "foreign secret commit")

    return {"home": home, "store": store, "scope": scope, "decoy": decoy,
            "work": tmp_path / "work", "identity": identity}


def test_the_leak_bed_really_builds_two_DIFFERENT_repositories(leak_bed):
    """Positive control for the fixture. If the decoy shared the scope's refs,
    every "did the bundle carry the decoy's refs" assertion below would be
    unable to tell the two apart and would pass no matter what."""
    scope_refs = set(_git_run(leak_bed["scope"], "for-each-ref",
                              "--format=%(refname)").stdout.split())
    decoy_refs = set(_git_run(leak_bed["decoy"], "for-each-ref",
                              "--format=%(refname)").stdout.split())
    assert scope_refs == {"refs/heads/trunk"}, scope_refs
    assert decoy_refs == {"refs/heads/foreign-branch"}, decoy_refs
    assert scope_refs.isdisjoint(decoy_refs)


def test_a_clean_run_bundles_the_SCOPE(leak_bed):
    """The control the parametrised tests below are read against. Without it a
    green row cannot distinguish "the strip worked" from "the run never produced
    a bundle at all"."""
    p = _run_isolated(leak_bed["store"], leak_bed["work"],
                      leak_bed["home"], leak_bed["identity"])
    assert p.returncode == 0, f"control run failed rc={p.returncode}: {p.stderr}"
    cipher = leak_bed["work"] / "some-scope.bundle.age"
    assert cipher.is_file(), "the control run produced no artifact"
    plain = leak_bed["work"] / "readback.bundle"
    d = _decrypt(cipher.read_bytes(), leak_bed["identity"], plain)
    assert d.returncode == 0, d.stderr
    heads = _bundle_heads_cleanly(plain, leak_bed["home"])
    assert any("refs/heads/trunk" in h for h in heads), heads
    assert not any("foreign" in h for h in heads), heads


@pytest.mark.parametrize("var", _LEDGER)
def test_a_leaked_pointer_cannot_aim_the_backup_at_a_FOREIGN_repository(leak_bed, var):
    """🔴 THE REGRESSION TEST. Red at d7d682f0 for GIT_DIR; green at HEAD.

    Two claims, and the second is the one a foreign-repo-unchanged assertion
    alone would miss:

      1. the DECOY is byte-identical afterwards, `objects/` INCLUDED, and
      2. the BUNDLE — the thing that gets encrypted and UPLOADED OFF-BOX —
         declares the SCOPE's refs and none of the decoy's.

    Claim 2 is the exfiltration signature. On the pre-fix tree with GIT_DIR
    leaked the run exited 0, the decoy was untouched, `bundle_scope`'s restore
    rehearsal passed, and the artifact was a complete copy of the decoy.

    Rows other than GIT_DIR / GIT_COMMON_DIR / GIT_OBJECT_DIRECTORY were already
    green at d7d682f0 — see `_POINTER_VERDICTS`. Those parametrisations are
    INVARIANT GUARDS, not regression coverage, and they are here because "no
    effect on this program today" is a measurement with a date on it, not a
    property.
    """
    before = _git_dir_fingerprint(leak_bed["decoy"] / ".git")

    p = _run_isolated(leak_bed["store"], leak_bed["work"], leak_bed["home"],
                      leak_bed["identity"],
                      leak={var: _leak_value(var, leak_bed["decoy"])})

    cipher = leak_bed["work"] / "some-scope.bundle.age"
    if p.returncode != 0:
        # "Refuses" is an acceptable outcome per the card — but it must not have
        # left an artifact behind that something downstream could upload.
        assert not cipher.is_file(), (
            f"{var}: the run FAILED (rc={p.returncode}) but still left an "
            f"encrypted artifact behind: {cipher}")
    else:
        assert cipher.is_file(), f"{var}: rc=0 but no artifact: {p.stdout}\n{p.stderr}"
        plain = leak_bed["work"] / "readback.bundle"
        d = _decrypt(cipher.read_bytes(), leak_bed["identity"], plain)
        assert d.returncode == 0, f"{var}: could not decrypt the artifact: {d.stderr}"
        heads = _bundle_heads_cleanly(plain, leak_bed["home"])
        assert heads, f"{var}: the artifact declares no heads at all"
        assert not any("foreign" in h for h in heads), (
            f"🔴 {var} AIMED THE BACKUP AT THE DECOY. The artifact that gets "
            f"encrypted and uploaded declares the FOREIGN repository's refs: "
            f"{heads}. This program has no PrivateNetwork and uploads what it "
            f"bundles to MinIO, so this is content exfiltration, not local "
            f"corruption. `git -C <scope>` does not win against a repo pointer "
            f"— `_git_env()` must strip "
            f"`testlib/gitenv.py::REPO_POINTER_VARS`.")
        assert any("refs/heads/trunk" in h for h in heads), (
            f"{var}: the artifact does not carry the SCOPE's own branch: {heads}")

    after = _git_dir_fingerprint(leak_bed["decoy"] / ".git")
    moved = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert not moved, (
        f"🔴 {var} let the backup WRITE INTO the foreign repository. Paths that "
        f"changed under {leak_bed['decoy'] / '.git'} ({len(moved)}, of which "
        f"{sum(1 for k in moved if k.startswith('objects/'))} under objects/): "
        f"{moved[:20]}")


@pytest.mark.parametrize("var", _LEDGER)
def test_a_leaked_pointer_does_not_BREAK_the_backup(leak_bed, var):
    """The availability half. Red at d7d682f0 for GIT_COMMON_DIR and
    GIT_OBJECT_DIRECTORY (both `rev-list` rc=128, so the run exited 1).

    Refusing is safe, but a backup that refuses is a backup that does not exist,
    and this one runs unattended on a daily timer behind an `OnFailure` toast.
    Stripping the pointers makes the run correct AND available; the card's
    "refuses or operates on the scope" is the floor, this is the standard.
    """
    p = _run_isolated(leak_bed["store"], leak_bed["work"], leak_bed["home"],
                      leak_bed["identity"],
                      leak={var: _leak_value(var, leak_bed["decoy"])})
    assert p.returncode == 0, (
        f"{var} broke the backup (rc={p.returncode}). A leaked pointer must be "
        f"neutralised, not survived-by-failing:\n{p.stderr[-1500:]}")
    assert (leak_bed["work"] / "some-scope.bundle.age").is_file()


def test_the_leak_is_ANNOUNCED_so_the_broken_caller_gets_fixed(leak_bed):
    """The strip fixes THIS program; it does not fix whoever exported the
    variable. Saying so in the journal is the only thing that does."""
    p = _run_isolated(leak_bed["store"], leak_bed["work"], leak_bed["home"],
                      leak_bed["identity"],
                      leak={"GIT_DIR": str(leak_bed["decoy"] / ".git")})
    assert p.returncode == 0, p.stderr
    assert "STRIPPED GIT_DIR=" in p.stderr, (
        f"the run neutralised a leaked GIT_DIR silently:\n{p.stderr}")


def test_a_clean_run_announces_NOTHING(leak_bed):
    """The other half of the pair. A message printed unconditionally is not a
    signal, and would train the operator to ignore the one that matters."""
    p = _run_isolated(leak_bed["store"], leak_bed["work"],
                      leak_bed["home"], leak_bed["identity"])
    assert p.returncode == 0, p.stderr
    assert "STRIPPED" not in p.stderr, p.stderr


# --------------------------------------------------------------------------- #
# 12b. the LEDGER: one owner, no second spelling
# --------------------------------------------------------------------------- #
def test_the_producer_uses_the_SHARED_ledger_object_itself():
    """🔴 ONE RULE, ONE PLACE — and here it can be an identity check.

    `commit.sh` has to RE-SPELL the ledger as a bash array (bash cannot import)
    and is pinned two-way by `test_git_repo_isolation.py`. backup.py is Python,
    so it uses the owner's object directly: there is no copy, so there is
    nothing to drift, and a name added to `testlib/gitenv.py` reaches this
    program with no edit here at all.
    """
    from testlib import gitenv
    assert getattr(B, "REPO_POINTER_VARS", None) is gitenv.REPO_POINTER_VARS, (
        "backup.py is not using testlib/gitenv.py's ledger OBJECT. Either it "
        "does not import it at all (the pre-fix shape: `_git_env()` built from "
        "`dict(os.environ)` and stripped nothing), or it re-spells the names — "
        "and a second spelling can drift from the owner, which is the failure "
        "this pins.")
    assert getattr(B, "strip_repo_pointers", None) is gitenv.strip_repo_pointers, (
        "backup.py does not use the owner's `strip_repo_pointers`; a local "
        "re-implementation is a second place for the fix to be wrong.")


def test_the_measured_table_covers_every_ledger_name():
    """Two-way pin. A name added to `REPO_POINTER_VARS` with no measured verdict
    here is a name nobody ran against this program; a verdict for a name that
    left the ledger is a claim about something no longer stripped."""
    assert set(_POINTER_VERDICTS) == set(_LEDGER), (
        f"the measured table and the ledger disagree:\n"
        f"  measured but not on the ledger: "
        f"{sorted(set(_POINTER_VERDICTS) - set(_LEDGER))}\n"
        f"  on the ledger but never measured: "
        f"{sorted(set(_LEDGER) - set(_POINTER_VERDICTS))}\n"
        "Run it — do not infer the row. #721 shipped a row claiming rc 0 where "
        "the truth was rc 1.")
    for var in _RED_AT_BASE_SECURITY + _RED_AT_BASE_AVAILABILITY:
        assert var in _LEDGER, var
    # And every name must have a leak value, or its parametrisation is vacuous.
    for var in _LEDGER:
        assert _leak_value(var, Path("/nonexistent/decoy")), var


def test_the_git_environment_strips_every_pointer_on_the_ledger(monkeypatch):
    """The unit-level assertion the mutation sweep is read against.

    Ledger-driven, so it grows and shrinks with `REPO_POINTER_VARS` instead of
    pinning a hand-typed list that could quietly cover fewer names than the
    docstring claims.
    """
    for name in _LEDGER:
        monkeypatch.setenv(name, f"/tmp/foreign/{name}")
    env = B._git_env()
    still = sorted(n for n in _LEDGER if n in env)
    assert not still, (
        f"_git_env() passed {still} straight through to git. Each of these "
        f"decides WHICH repository a command lands in and OVERRIDES `git -C`.")
    # The config half must survive the strip — the two sets are disjoint.
    assert env["GIT_OPTIONAL_LOCKS"] == "0"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_CONFIG_SYSTEM"] == os.devnull
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_the_strip_does_not_mutate_the_CALLERS_environment(monkeypatch):
    """`strip_repo_pointers()` mutates what it is handed. It is handed a COPY
    here; handing it `os.environ` would make one `_git_env()` call silently
    reshape the whole process, which is a behaviour change nobody asked for."""
    monkeypatch.setenv("GIT_DIR", "/tmp/foreign/x")
    B._git_env()
    assert os.environ.get("GIT_DIR") == "/tmp/foreign/x"


def test_every_git_invocation_takes_its_environment_from_git_env():
    """🔴 A SEAM GUARD: it pins the RELATIONSHIP, not one function.

    `_git` and `_git_scratch` are two separate `subprocess.run` sites and both
    must inherit the strip. The failure this closes is a THIRD site added later
    that builds `dict(os.environ)` itself — which is exactly the shape the bug
    had. Walking the AST rather than grepping, because a grep for `_git_env`
    would be satisfied by the two that already exist while a new one sat beside
    them.
    """
    import ast
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    classified = [_classify_subprocess(n) for n in ast.walk(tree)]
    git_sites = [c for c in classified if c and c[1] == _KIND_GIT]
    unknown = [c for c in classified if c and c[1] == _KIND_UNRECOGNISED]

    assert len(git_sites) >= 2, (
        f"the AST walk found {len(git_sites)} git subprocess site(s) in "
        f"{SCRIPT.name}; it should see at least `_git` and `_git_scratch`. A "
        f"walker that finds nothing would make this pass vacuously.")

    # 🔴 AN UNRECOGNISED SHAPE IS A FAILURE, NOT A PASS. `subprocess.run(argv)`
    # or `subprocess.run([_GITBIN, …])` cannot be read statically, so the walker
    # cannot say whether it is a git site — and "cannot say" must never render
    # as compliant. Widen the classifier deliberately, or hoist the literal.
    assert not unknown, (
        f"{SCRIPT.name} has subprocess call(s) at line(s) "
        f"{[ln for ln, _k, _ok in unknown]} whose argv[0] is not a literal, so "
        f"this guard CANNOT tell whether they invoke git. It refuses to read "
        f"that as compliant. Either spell argv[0] as a literal, or widen "
        f"`_classify_subprocess` in the same commit.")

    bad = [ln for ln, _k, ok in git_sites if not ok]
    assert not bad, (
        f"{SCRIPT.name} invokes git at line(s) {bad} with an environment that "
        f"did not come from `_git_env()`. That site does not get the "
        f"repo-pointer strip, so a leaked GIT_DIR aims it at a foreign "
        f"repository — the whole of clawgate #343.")


_KIND_GIT = "git"
_KIND_OTHER = "other"
_KIND_UNRECOGNISED = "unrecognised"


def _classify_subprocess(node):
    """`(lineno, kind, env_ok)` for any `subprocess.*` call, else None.

    🔴 WIDENED AFTER AN AUDIT MEASURED IT NARROWER THAN THE SENTENCE BESIDE IT.
    The first version matched only `subprocess.run` whose argv[0] was the
    literal `"git"`, while `backup.py`'s docstring told the next maintainer that
    EVERY git subprocess in the file is pinned by this test. Two realistic
    third-site shapes survived a fully green 139-test run:

        subprocess.check_output(["git", "--version"], env=dict(os.environ))
        subprocess.run([_GITBIN, ...], env=dict(os.environ))

    Both are exactly the "third site added later" this guard exists for. So:

      * ANY attribute on the `subprocess` module counts, not just `run` —
        `check_output`, `check_call`, `call`, `Popen`, whatever arrives next.
      * a non-literal argv, or a non-literal argv[0], is `_KIND_UNRECOGNISED`
        and the caller FAILS on it. A guard that cannot read a call must not
        report it as fine; that is the same "wider description than
        implementation" defect one level up.
      * `env=` is accepted as `_git_env()` or `<module>._git_env()`, because
        `restore-verify.py` reaches it as `B._git_env()`.
    """
    import ast
    if not isinstance(node, ast.Call):
        return None
    fn = node.func
    if not (isinstance(fn, ast.Attribute)
            and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess"):
        return None

    env_ok = _env_is_git_env(node)
    if not node.args:
        return (node.lineno, _KIND_UNRECOGNISED, env_ok)
    argv = node.args[0]
    if not isinstance(argv, (ast.List, ast.Tuple)):
        return (node.lineno, _KIND_UNRECOGNISED, env_ok)
    if not argv.elts:
        return (node.lineno, _KIND_UNRECOGNISED, env_ok)
    first = argv.elts[0]
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return (node.lineno, _KIND_UNRECOGNISED, env_ok)
    kind = _KIND_GIT if Path(first.value).name == "git" else _KIND_OTHER
    return (node.lineno, kind, env_ok)


def _env_is_git_env(node) -> bool:
    """`env=_git_env()` or `env=<module>._git_env()`."""
    import ast
    env = {k.arg: k.value for k in node.keywords}.get("env")
    if not isinstance(env, ast.Call):
        return False
    f = env.func
    if isinstance(f, ast.Name):
        return f.id == "_git_env"
    return isinstance(f, ast.Attribute) and f.attr == "_git_env"


_SEAM_CONTROL_SRC = """
import subprocess, os
def _git_env():
    return {}
def compliant_run():
    return subprocess.run(['git', 'status'], env=_git_env())
def compliant_via_module():
    return subprocess.run(['git', 'status'], env=B._git_env())
def compliant_abs_path():
    return subprocess.run(['/usr/bin/git', 'status'], env=_git_env())
def offending_run():
    return subprocess.run(['git', 'log'], env=dict(os.environ))
def offending_check_output():
    return subprocess.check_output(['git', '--version'], env=dict(os.environ))
def offending_popen():
    return subprocess.Popen(['git', 'fsck'], env=dict(os.environ))
def not_git():
    return subprocess.run(['age', '-e'], env=dict(os.environ))
def unreadable_name():
    return subprocess.run([_GITBIN, 'log'], env=dict(os.environ))
def unreadable_argv(argv):
    return subprocess.run(argv, env=dict(os.environ))
"""


def test_the_git_env_seam_guard_can_go_RED():
    """🔴 NEGATIVE CONTROL, REBUILT AFTER AN AUDIT WALKED THE OLD ONE.

    The previous control fed exactly two shapes — a compliant `subprocess.run`
    and an offending one — so it proved the walker could tell those two apart
    and nothing else. An audit then measured two realistic third-site mutants
    surviving a fully green run: `subprocess.check_output(["git", …])` and
    `subprocess.run([_GITBIN, …])`. Both are now in the fixture, alongside the
    module-qualified `env=B._git_env()` that `restore-verify.py` uses and an
    absolute-path `argv[0]`.

    The unreadable shapes must classify as UNRECOGNISED — not as compliant, and
    not as non-git. That distinction is the whole point: "I cannot read this"
    and "this is fine" must not produce the same verdict.
    """
    import ast
    tree = ast.parse(_SEAM_CONTROL_SRC)
    by_func = {}
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for n in ast.walk(fn):
            c = _classify_subprocess(n)
            if c is not None:
                by_func[fn.name] = (c[1], c[2])

    assert by_func == {
        "compliant_run": (_KIND_GIT, True),
        "compliant_via_module": (_KIND_GIT, True),
        "compliant_abs_path": (_KIND_GIT, True),
        "offending_run": (_KIND_GIT, False),
        "offending_check_output": (_KIND_GIT, False),
        "offending_popen": (_KIND_GIT, False),
        "not_git": (_KIND_OTHER, False),
        "unreadable_name": (_KIND_UNRECOGNISED, False),
        "unreadable_argv": (_KIND_UNRECOGNISED, False),
    }, (
        "the seam walker misclassified one of the shapes it exists to catch:\n"
        f"{by_func}\n"
        "Every `offending_*` must be (git, False) so the guard fails on it; "
        "every `unreadable_*` must be UNRECOGNISED so the guard refuses rather "
        "than passing it.")


def _transplant(root: Path, with_ledger: bool) -> Path:
    """A copy of `backup.py` at the same depth, with or without `testlib/`.

    Same depth matters: the producer resolves the ledger as
    `Path(__file__).resolve().parents[1] / "testlib"`, so the copy has to sit
    one level under a scripts-like root for either outcome to mean anything.
    """
    area = root / "analyze-service-index"
    area.mkdir(parents=True)
    shutil.copy(SCRIPT, area / SCRIPT.name)
    if with_ledger:
        shutil.copytree(SCRIPTS / "testlib", root / "testlib")
    return area / SCRIPT.name


def _bare_env() -> dict:
    """PATH and HOME only — no PYTHONPATH to smuggle the ledger back in."""
    return {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}


def test_a_MISSING_pointer_ledger_STOPS_the_program(tmp_path):
    """🔴 THE ONE CLAIM NO OTHER TEST CAN SEE — measured, not asserted.

    `backup.py`'s import banner says the ledger import is "A HARD FAILURE, NEVER
    A SILENT DEGRADE". An audit measured that replacing the `raise` with

        except ImportError:
            REPO_POINTER_VARS = ()
            def strip_repo_pointers(env=None): return {}

    leaves the whole suite GREEN — 139/139. That degrade is precisely the
    false all-clear this program exists to prevent: a backup that silently
    re-acquires clawgate #343 and still reports success, on a daily timer,
    uploading off-box. Every other test here runs where `testlib/` is present,
    so none of them can distinguish the two.

    So: transplant the producer somewhere the ledger is NOT, and require it to
    refuse. Paired with `test_the_transplant_control_RUNS_when_the_ledger_is_
    there`, without which "non-zero exit" could be any other cause.
    """
    script = _transplant(tmp_path / "noledger", with_ledger=False)
    p = subprocess.run([sys.executable, str(script), "--print-plan"],
                       capture_output=True, text=True, env=_bare_env())

    assert p.returncode != 0, (
        "backup.py STARTED with no repo-pointer ledger reachable. The import "
        "degraded instead of raising, so this run has no strip at all and would "
        "bundle and upload whatever a leaked GIT_DIR pointed it at — while "
        f"reporting success.\nstdout:\n{p.stdout[-800:]}")
    assert "cannot import the git repo-pointer ledger" in p.stderr, (
        f"it failed, but not with the message that tells an operator WHY — so "
        f"the failure is indistinguishable from an unrelated crash:\n"
        f"{p.stderr[-1200:]}")


def test_the_transplant_control_RUNS_when_the_ledger_is_there(tmp_path):
    """The other half of the pair, and it is not optional.

    A transplanted copy could exit non-zero for a dozen reasons that have
    nothing to do with the ledger — a missing sibling, a bad path, an unrelated
    import. Unless the SAME transplant with `testlib/` present exits 0, the test
    above is measuring the transplant, not the guard.
    """
    script = _transplant(tmp_path / "withledger", with_ledger=True)
    p = subprocess.run([sys.executable, str(script), "--print-plan"],
                       capture_output=True, text=True, env=_bare_env())
    assert p.returncode == 0, (
        f"the transplant itself is broken, so the missing-ledger test above "
        f"proves nothing:\nstderr:\n{p.stderr[-1200:]}")
    assert "remote:    NONE" in p.stdout, p.stdout[:400]


def test_the_announcement_is_ONCE_PER_NAME_not_once_per_git_call(leak_bed):
    """🟢 The dedupe is a claim in the docstring, so it gets a pin.

    A full run calls `_git_env()` many times per scope (`rev-list`, `bundle
    create`, `bundle verify`, the rehearsal clone, `for-each-ref`), so a missing
    `continue` turns one useful line into a wall that buries the backup's own
    output. Measured across a SUBPROCESS boundary on purpose —
    `_POINTERS_ANNOUNCED` is process-global and never reset, so an in-process
    assertion would depend on which test ran first.
    """
    p = _run_isolated(leak_bed["store"], leak_bed["work"], leak_bed["home"],
                      leak_bed["identity"],
                      leak={"GIT_DIR": str(leak_bed["decoy"] / ".git")})
    assert p.returncode == 0, p.stderr
    n = p.stderr.count("STRIPPED GIT_DIR=")
    assert n == 1, (
        f"the leak was announced {n} times in one run; it must be once per "
        f"variable. `_git_env()` is rebuilt for every git invocation, so a "
        f"per-call line buries the run's own output.")


def test_the_announcement_names_the_PROGRAM_THAT_IS_RUNNING(monkeypatch):
    """🟢 `_git_env()` is SHARED — `restore-verify.py` calls it directly.

    Hardcoding `PROG` announced a leak hit during a restore VERIFICATION under
    the BACKUP's name, pointing whoever read the journal at the wrong program
    and the wrong systemd unit.

    Snapshot/restore `_POINTERS_ANNOUNCED` because it is process-global by
    design; see its note in the producer.
    """
    saved = set(B._POINTERS_ANNOUNCED)
    try:
        B._POINTERS_ANNOUNCED.clear()
        monkeypatch.setattr(sys, "argv", ["/somewhere/restore-verify.py", "--x"])
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            B._announce_stripped_pointers({"GIT_DIR": "/tmp/foreign/x"})
        out = buf.getvalue()
        assert out.startswith("restore-verify.py: STRIPPED GIT_DIR="), (
            f"the announcement does not name the running program: {out!r}")
        assert B.PROG not in out, (
            f"the announcement still hardcodes the BACKUP's name while "
            f"restore-verify.py is what ran: {out!r}")
    finally:
        B._POINTERS_ANNOUNCED.clear()
        B._POINTERS_ANNOUNCED.update(saved)


def test_scope_remotes_has_no_production_call_site():
    """Card criterion 7, machine-checked in BOTH directions.

    `scope_remotes()` is called by the test suite only. Its docstring says so;
    this is what stops that sentence from quietly becoming false — and what
    stops a future reader citing it as the control that ENFORCES the no-remote
    invariant when it enforces nothing. Wire it in and this goes red, which is
    the point: the docstring has to change in the same commit.

    🔴 SCANS EVERY PRODUCTION MODULE IN THE DIRECTORY, not just `backup.py`.
    An audit noted the first version parsed one file while the docstring it
    protects makes an UNQUALIFIED claim ("there is no production call site").
    `restore-verify.py` imports `backup` as `B`, so `B.scope_remotes(scope)`
    there would be a production call the narrow version could not see — the
    same "description wider than the implementation" defect the guard exists to
    prevent, one level up. Both the bare name and any attribute access on it
    count. Derived from the directory at scan time, so a third program is
    covered the day it appears.
    """
    import ast
    modules = sorted((SCRIPTS / "analyze-service-index").glob("*.py"))
    assert len(modules) >= 2, (
        f"the production-module sweep found {len(modules)} file(s) in "
        f"scripts/analyze-service-index/; it must see at least backup.py and "
        f"restore-verify.py, or this scan is vacuous: "
        f"{[m.name for m in modules]}")
    assert {m.name for m in modules} >= {"backup.py", "restore-verify.py"}, (
        f"{[m.name for m in modules]} — the two known producers must both be in "
        f"the swept set")

    defs, calls = [], []
    for mod in modules:
        tree = ast.parse(mod.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == "scope_remotes":
                defs.append(f"{mod.name}:{n.lineno}")
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            named = ((isinstance(f, ast.Name) and f.id == "scope_remotes")
                     or (isinstance(f, ast.Attribute) and f.attr == "scope_remotes"))
            if named:
                calls.append(f"{mod.name}:{n.lineno}")

    assert len(defs) == 1 and defs[0].startswith("backup.py:"), (
        f"expected exactly one `scope_remotes` definition, in backup.py; "
        f"found {defs}")
    assert not calls, (
        f"scope_remotes() now HAS a production call site ({calls}). Its "
        f"docstring says it has none and that no future reader may cite it as a "
        f"run-path control. Update that docstring in this commit — and note "
        f"that a post-hoc no-remote assertion on the run path would be "
        f"UNREACHABLE, since nothing in this program can add a remote.")
    src = SCRIPT.read_text(encoding="utf-8")
    assert "TEST-ONLY" in src and "NO PRODUCTION CALL SITE" in src, (
        "the dead-code status is no longer stated where a reader lands")


def test_the_unit_can_actually_reach_the_pointer_ledger_at_runtime():
    """🔴 THE IMPORT IS A NEW RUNTIME DEPENDENCY, so the sandbox must contain it.

    backup.py imports `testlib/gitenv.py`, and the unit runs under
    `ProtectSystem=strict` + `ProtectHome=tmpfs` — the ledger is reachable ONLY
    because `BindReadOnlyPaths` mounts the whole `scripts/` tree. If someone
    narrows that mount to `scripts/analyze-service-index`, the import raises and
    the backup stops. Loudly, by design — but this says so before it happens.
    """
    ledger = SCRIPTS / "testlib" / "gitenv.py"
    assert ledger.is_file(), f"{ledger} is missing — backup.py imports it"
    binds = _containment_directives(_backup_block())["BindReadOnlyPaths"]
    assert '"%h/workspace/devrc/scripts"' in binds, (
        f"the backup unit no longer mounts the whole `scripts/` tree: {binds}. "
        f"backup.py imports its repo-pointer ledger from "
        f"`scripts/testlib/gitenv.py`; without that mount the unit cannot even "
        f"import, and the daily backup stops.")


def test_the_docstring_no_longer_claims_more_than_the_code_does():
    """Card criterion 6. The old wording — "makes git structurally incapable of
    writing to the repo" — was wider than the check in two directions at once:
    nothing in `_git_env()` made git incapable of writing (the allowlist and
    `BindReadOnlyPaths` do that), and it said nothing at all about WHICH
    repository git resolves, which was the half that was missing.

    🔴 A BAN ON THE PHRASE WOULD BE THE WRONG GUARD TWICE OVER: the docstring
    QUOTES the old claim in order to retract it (which is the useful thing to
    do), and a word-ban is walkable by rewording anyway. So this pins the whole
    normalised RETRACTION — claude/RULES.md → "when the artifact under test IS
    prose, pin the WHOLE normalised string". A cosmetic reword fails this test;
    that is the price of a machine-readable claim, and it is worth paying here
    because the sentence is the only thing standing between a reader and a
    guarantee the code does not provide.
    """
    doc = " ".join((B._git_env.__doc__ or "").split())
    retraction = (
        'It said this environment "makes git structurally incapable of writing '
        'to the repo" — a claim WIDER than the code: nothing here made git '
        'incapable of writing (the allowlist in `_git` and the unit\'s '
        '`BindReadOnlyPaths` do that), and nothing here said anything about '
        'WHICH repository git resolves, which was the half that was actually '
        'missing.'
    )
    assert retraction in doc, (
        "`_git_env`'s docstring no longer carries the retraction of its old, "
        "over-wide claim verbatim. Either the claim came back un-retracted, or "
        "the sentence was reworded — re-read the implementation against it and "
        "update this pin in the same commit.\n\n"
        f"docstring now reads:\n{doc[:1200]}")
    assert "WHICH REPOSITORY — `strip_repo_pointers`" in doc, (
        "`_git_env`'s docstring does not name repo resolution and the strip "
        "that provides it, which is now the main thing the function does")
    assert "GIT_DIR OVERRIDES IT" in doc, (
        "the docstring no longer states WHY `-C` is not enough, which is the "
        "fact the whole function turns on")
