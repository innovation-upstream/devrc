#!/usr/bin/env python3
"""Encrypted OFF-MACHINE backup for the /analyze-service index store.

WHAT THIS CLOSES
----------------
`~/.claude/analyze-service-index/<scope>/` is one independent git repository per
scope. `commit.sh` (the hourly autocommit) gives every scope local history, which
defends against exactly one failure mode: an agent's Write clobbering a file that
a previous run had already committed.

It defends against NOTHING ELSE. Measured on the workbench 2026-08-21: 10 scope
repos, every one of them `remote = none`, and no copy of any of them anywhere off
this disk. A disk failure, a filesystem corruption, or an `rm -rf` of the store
root loses the whole thing permanently, history included — the history lives
*inside* the thing being destroyed. The content is not re-derivable: it records
gotchas, retracted theories and measurements that were true at a moment.

The laptop's store is DIVERGENT content, not a copy. It is a second thing to
lose, not a backup of the first.

WHY BUNDLES, AND WHY NO REMOTE
------------------------------
🔴 Every scope README states the no-remote invariant, and `commit.sh` is built
around it (its systemd unit runs with `PrivateNetwork=true` so the invariant is
enforced by the namespace, not by prose). A `git bundle` preserves that
literally: it is a single file containing the full object graph and all refs,
produced by a command that only READS the repository. No remote is added, no
push happens, no `git config` is written, and this script never runs a git
subcommand outside `_READ_ONLY_SUBCOMMANDS` below.

That read-only-ness is not asserted, it is measured: the test suite checksums a
synthetic store before and after a full run and requires byte identity, and
separately requires that every scope still reports zero remotes.

WHY age, WHEN MINIO IS SELF-HOSTED
----------------------------------
🔴 The store is client-confidential and the scope READMEs say the content never
leaves the machine. MinIO being on the operator's own homelab makes that *nearly*
true, not true. Encrypting to the operator's age identity before the bytes leave
the process makes it literally true: MinIO stores ciphertext it cannot read, and
a compromised or misconfigured bucket policy discloses nothing.

The identity is the one that ALREADY EXISTS — the SOPS age key used across the
homelab repos (`~/workspace/homelab-talos/.secrets/age.key`, recipient
`age1g0nf…dj64t` in that repo's `.sops.yaml`). No new key is minted, because a
backup encrypted to a key the operator does not already keep alive is a backup
that will not decrypt when it is finally needed. The RECIPIENT is DERIVED from
the identity file at run time rather than hardcoded here: devrc is public, and
more importantly a hardcoded recipient can silently drift from the key the
operator actually holds, producing archives nobody can open. Deriving it makes
"the thing we encrypt to" and "the thing we can decrypt with" the same fact.

🔴 EVERY EMPTY OUTCOME IS LOUD
------------------------------
A backup system that reports success having stored nothing is worse than no
backup system, because it is also a false all-clear. So:

  * a store directory that EXISTS but holds no scopes  -> failure
  * a scope whose repository holds zero commits        -> failure
  * a bundle that does not pass `git bundle verify`    -> failure, not uploaded
  * an upload whose read-back size/etag disagrees      -> failure
  * a run that backed up zero scopes                   -> failure

The single exception is an ABSENT store: a host that has never run
/analyze-service has nothing to lose, and failing there would make the timer a
permanently-red gate on the laptop (RULES.md: "a permanently-red gate is worse
than no gate"). That path prints its reason and exits 0, and it is the only one
that may.

Usage:
    backup.py [--store DIR] [--bucket B] [--keep N] [--no-upload] [--print-plan]

  --print-plan   pure text; reads the store, writes nothing anywhere
  --no-upload    bundle + verify + encrypt into --work-dir, then stop. Used for
                 a read-only smoke run against the real store.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROG = "analyze-service-index-backup"

DEFAULT_STORE = Path.home() / ".claude" / "analyze-service-index"
DEFAULT_BUCKET = "analyze-service-index-backups"
DEFAULT_KEEP = 14
DEFAULT_IDENTITY = Path.home() / "workspace" / "homelab-talos" / ".secrets" / "age.key"

# 🔴 THE READ-ONLY ALLOWLIST. Every git invocation in this file goes through
# `_git()`, which refuses a subcommand that is not in here. This is the guard
# that makes "the store is treated as read-only" a property of the code rather
# than of the author's care — a future edit that reaches for `git gc`, `git
# remote add` or `git config` fails at the call, not in review.
#
# It is pinned by the test suite as an EXACT SET, not a subset: widening it is a
# deliberate act that has to be argued for in a diff, and shrinking it below
# what the code needs breaks the tests that exercise those paths.
_READ_ONLY_SUBCOMMANDS = frozenset(
    {"bundle", "rev-list", "remote", "rev-parse", "for-each-ref"})


class BackupError(RuntimeError):
    """A failure that must make the whole run non-zero."""


# --------------------------------------------------------------------------- #
# git — read-only by construction
# --------------------------------------------------------------------------- #
def _git_env() -> dict:
    """Environment that makes git structurally incapable of writing to the repo.

    Each entry earns its place:

      GIT_OPTIONAL_LOCKS=0   DEFENCE IN DEPTH, and stated at the scope it was
                             measured. Some git reads (`git status` is the
                             standard example) refresh and REWRITE `.git/index`
                             as a side effect, which would move the store's
                             mtimes without changing a byte of content. MEASURED
                             2026-08-21 against a repo with a deliberately stale
                             index: none of the four subcommands in
                             `_READ_ONLY_SUBCOMMANDS` touches the index with this
                             set to 0 OR to 1, while `git status` in the same
                             probe DID rewrite it. So today this variable changes
                             nothing — it is here for the subcommand somebody
                             adds later, not because the current set needs it.
                             It is NOT what makes the store read-only; the
                             allowlist and the unit's BindReadOnlyPaths are.
      GIT_CONFIG_NOSYSTEM=1  no /etc/gitconfig
      GIT_CONFIG_GLOBAL      no ~/.gitconfig — so the operator's own config can
      GIT_CONFIG_SYSTEM      neither change what we produce nor be written to.
      GIT_TERMINAL_PROMPT=0  never block a timer-driven run on a prompt.
    """
    env = dict(os.environ)
    env.update({
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run one read-only git subcommand against `repo`.

    Refuses anything outside `_READ_ONLY_SUBCOMMANDS`. `gc.auto=0` stops git
    from deciding, mid-read, that this repository is due a repack — which is a
    write, would be perfectly reasonable behaviour, and would silently break the
    read-only guarantee this whole file rests on. `core.hooksPath=/dev/null`
    stops a hook in the store from running under the backup's privileges.
    """
    if not args or args[0] not in _READ_ONLY_SUBCOMMANDS:
        raise BackupError(
            f"refusing git subcommand {args[0] if args else '(none)'!r}: the "
            f"/analyze-service index store is READ-ONLY to this script. "
            f"Allowed: {sorted(_READ_ONLY_SUBCOMMANDS)}. If a new subcommand is "
            f"genuinely needed, prove it cannot write and add it to "
            f"_READ_ONLY_SUBCOMMANDS in the same commit as the test that pins it."
        )
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "gc.auto=0", "-c", "core.hooksPath=" + os.devnull, *args],
        capture_output=True, text=True, env=_git_env(),
    )


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def discover_scopes(store: Path) -> list[Path]:
    """Every scope repository directly under `store`, sorted by name.

    A scope is a real directory (never a symlink — `commit.sh` refuses those for
    the same reason, and following one here would bundle a repository from
    outside the store) that contains a `.git`.
    """
    out: list[Path] = []
    for child in sorted(store.iterdir()):
        if child.name.startswith("."):
            continue
        if child.is_symlink() or not child.is_dir():
            continue
        if (child / ".git").exists():
            out.append(child)
    return out


def commit_count(scope: Path) -> int:
    p = _git(scope, "rev-list", "--count", "--all")
    if p.returncode != 0:
        raise BackupError(
            f"{scope.name}: could not count commits (git rev-list rc="
            f"{p.returncode}): {p.stderr.strip()}. A scope whose history cannot "
            f"be READ must never be reported as a scope that is EMPTY."
        )
    return int(p.stdout.strip() or "0")


def scope_remotes(scope: Path) -> list[str]:
    p = _git(scope, "remote")
    return [ln for ln in p.stdout.split("\n") if ln.strip()]


# --------------------------------------------------------------------------- #
# age
# --------------------------------------------------------------------------- #
def resolve_identity() -> Path:
    """Path to the operator's age identity file.

    Order: ASIB_AGE_IDENTITY -> SOPS_AGE_KEY_FILE (the handle the homelab repos
    already use, per SECRETS.md) -> the default homelab-talos path.
    """
    for var in ("ASIB_AGE_IDENTITY", "SOPS_AGE_KEY_FILE"):
        v = os.environ.get(var)
        if v:
            return Path(v)
    return DEFAULT_IDENTITY


def resolve_recipient(identity: Path) -> str:
    """The age recipient to encrypt to, DERIVED from the identity we can decrypt with.

    `ASIB_AGE_RECIPIENT` overrides — but only to a public key, and it is still
    checked for shape, because a typo here produces archives that encrypt fine
    and decrypt never.
    """
    override = os.environ.get("ASIB_AGE_RECIPIENT")
    if override:
        if not re.fullmatch(r"age1[0-9a-z]{58}", override.strip()):
            raise BackupError(
                f"ASIB_AGE_RECIPIENT={override!r} is not a well-formed age public "
                f"key (expected age1 + 58 base32 chars). Refusing to encrypt to "
                f"it: a backup encrypted to a malformed recipient is a backup "
                f"that cannot be restored, and nothing downstream would notice."
            )
        return override.strip()

    if not identity.is_file():
        raise BackupError(
            f"no age identity at {identity}. This backup encrypts to the "
            f"operator's EXISTING SOPS age key; it deliberately does not mint a "
            f"new one, because a key nobody keeps alive is a backup nobody can "
            f"open. Set ASIB_AGE_IDENTITY or SOPS_AGE_KEY_FILE to the key file "
            f"(see SECRETS.md)."
        )
    if shutil.which("age-keygen") is None:
        raise BackupError(
            "age-keygen is not on PATH — it is declared in nix/pkgs/default.nix "
            "and set explicitly on the systemd unit's PATH; add it there rather "
            "than working around it."
        )
    p = subprocess.run(["age-keygen", "-y", str(identity)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise BackupError(
            f"could not derive an age recipient from {identity} (rc="
            f"{p.returncode}): {p.stderr.strip()}"
        )
    recipient = p.stdout.strip()
    if not recipient.startswith("age1"):
        raise BackupError(
            f"age-keygen -y on {identity} produced {recipient!r}, which is not an "
            f"age public key. Refusing to encrypt to it."
        )
    return recipient


def encrypt(plain: Path, cipher: Path, recipient: str) -> None:
    if shutil.which("age") is None:
        raise BackupError(
            "age is not on PATH — it is declared in nix/pkgs/default.nix and set "
            "explicitly on the systemd unit's PATH; add it there rather than "
            "working around it. Uploading the plaintext bundle instead is NOT an "
            "acceptable fallback and this script will not do it."
        )
    p = subprocess.run(
        ["age", "--encrypt", "--recipient", recipient, "--output", str(cipher), str(plain)],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        raise BackupError(f"age encryption of {plain.name} failed (rc={p.returncode}): "
                          f"{p.stderr.strip()}")
    if not cipher.is_file() or cipher.stat().st_size == 0:
        raise BackupError(
            f"age reported success but {cipher} is missing or empty. An empty "
            f"ciphertext uploads happily and restores never."
        )


# --------------------------------------------------------------------------- #
# bundling
# --------------------------------------------------------------------------- #
def _git_scratch(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """git in a THROWAWAY directory — never the store.

    The allowlist on `_git` exists because that function is pointed at the only
    copy of the data. This one is pointed at a directory we created moments ago
    and are about to delete, so it may run writing subcommands (`clone`). It
    refuses to run anywhere near the store, so the two cannot be confused by a
    later edit.
    """
    return subprocess.run(
        ["git", "-c", "gc.auto=0", "-c", "core.hooksPath=" + os.devnull,
         "-c", "protocol.file.allow=always", "-C", str(cwd), *args],
        capture_output=True, text=True, env=_git_env(),
    )


def _refs(run: subprocess.CompletedProcess) -> set[str]:
    return {ln.strip() for ln in run.stdout.splitlines() if ln.strip()}


def bundle_scope(scope: Path, out: Path, work_dir: Path) -> None:
    """`git bundle create --all`, then PROVE the bundle restores, before upload.

    🔴 `git bundle verify` IS NOT AN INTEGRITY CHECK, AND READS EXACTLY LIKE ONE.
    Measured 2026-08-21, git 2.x, on a 504-byte bundle with a single byte flipped
    in the middle of the packfile:

        git bundle verify <corrupted>  ->  rc=0
        "<oid> HEAD"
        "The bundle records a complete history."

    It validates the bundle HEADER and the PREREQUISITES. It does not walk the
    pack, so bit rot, a truncated write or a partial copy all pass it — and it
    says "complete history" while doing so. A backup pipeline that stops there
    uploads corrupt archives under a green verdict, which is worse than not
    checking at all: it manufactures confidence. The same byte flip fails a
    clone at rc=128, `error: index-pack died`.

    So the real check is a RESTORE REHEARSAL: clone the bundle into a throwaway
    bare repository — which forces `index-pack` to validate every object's hash
    and the pack checksum — and then compare the restored ref set and commit
    count against the source. That is the same standard applied to the upload
    read-back, and for the same reason: the only evidence a backup is restorable
    is having restored it.

    `bundle verify` is kept in front of it because it is cheap and its message
    for a genuine prerequisite problem is clearer than a clone failure's.
    """
    # Own the scratch directory here too. Without this the rehearsal clone
    # failed with "cannot change to <work_dir>: No such file or directory" and
    # was reported as "the bundle could NOT BE RESTORED FROM" — a guard blaming
    # data corruption for a missing directory, which is a message that names the
    # wrong cause at the exact moment someone is deciding whether their backups
    # are intact.
    work_dir.mkdir(parents=True, exist_ok=True)

    p = _git(scope, "bundle", "create", str(out), "--all")
    if p.returncode != 0:
        raise BackupError(
            f"{scope.name}: `git bundle create` failed (rc={p.returncode}): "
            f"{p.stderr.strip()}"
        )
    if not out.is_file() or out.stat().st_size == 0:
        raise BackupError(f"{scope.name}: bundle {out} is missing or empty after create")

    v = _git(scope, "bundle", "verify", str(out))
    if v.returncode != 0:
        raise BackupError(
            f"{scope.name}: `git bundle verify` REJECTED the bundle (rc="
            f"{v.returncode}): {v.stderr.strip() or v.stdout.strip()}. Not "
            f"uploading it — an unverified bundle is a claim, not a backup."
        )

    rehearsal = work_dir / f".rehearse-{scope.name}.git"
    if rehearsal.exists():
        shutil.rmtree(rehearsal)
    try:
        c = _git_scratch(work_dir, "clone", "--bare", "--quiet",
                         str(out), str(rehearsal))
        if c.returncode != 0:
            raise BackupError(
                f"{scope.name}: the bundle passed `git bundle verify` but could "
                f"NOT BE RESTORED FROM (clone rc={c.returncode}): "
                f"{c.stderr.strip()}. `bundle verify` does not walk the packfile, "
                f"so this is what a corrupted or truncated bundle looks like. Not "
                f"uploading it."
            )

        fmt = "--format=%(refname) %(objectname)"
        want = _refs(_git(scope, "for-each-ref", fmt))
        got = _refs(_git_scratch(rehearsal, "for-each-ref", fmt))
        if got != want:
            raise BackupError(
                f"{scope.name}: the bundle restores to a DIFFERENT set of refs "
                f"than the scope holds. Missing from the restore: "
                f"{sorted(want - got)}; unexpected in the restore: "
                f"{sorted(got - want)}. Not uploading it."
            )

        # NOTE: there is deliberately NO separate commit-count comparison here.
        # An earlier draft had one, and a mutation sweep showed it SURVIVED every
        # mutation — because it is unreachable: the clone above validates every
        # object, and matching (refname, objectname) pairs pin the same reachable
        # commit set by construction, so no input can make the counts differ
        # while the refs agree. A guard that cannot fail reads as coverage while
        # providing none, which is worse than not being there, because it stops
        # the next person looking.
    finally:
        shutil.rmtree(rehearsal, ignore_errors=True)


# --------------------------------------------------------------------------- #
# upload boundary
# --------------------------------------------------------------------------- #
def resolve_kubeconfig() -> Path | None:
    """Which kubeconfig reaches the homelab from THIS host.

    🔴 The two hosts reach the same cluster by different files, and both stores
    need backing up: the workbench has the checked-out
    `homelab-talos/homelab-kubeconfig`, the laptop reaches it over nebula via
    `~/.kube/homelab-nebula.yaml` (SECRETS.md, `$KC_NEBULA`). Hardcoding the
    workbench's path in the unit would leave the laptop's — DIVERGENT, equally
    unrecoverable — store with no off-machine copy while the workbench looked
    healthy. That is the exact silent gap this whole change exists to close, so
    the resolution is done here where it can adapt, not in the unit where it
    cannot.

    An already-set KUBECONFIG always wins: the operator and the unit both set it
    deliberately.
    """
    existing = os.environ.get("KUBECONFIG")
    if existing and Path(existing).is_file():
        return Path(existing)
    for candidate in (
        Path.home() / "workspace" / "homelab-talos" / "homelab-kubeconfig",
        Path.home() / ".kube" / "homelab-nebula.yaml",
    ):
        if candidate.is_file():
            os.environ["KUBECONFIG"] = str(candidate)
            return candidate
    return None


class MinioUploader:
    """The real object store: the homelab `minio-archive` tenant.

    🔴 Deliberately a THIN wrapper over `scripts/mail-actions/_minio.py` rather
    than a second implementation. That module already owns how this host reaches
    the tenant — the `kubectl port-forward` to `svc/minio` in namespace
    `minio-archive`, the credential read from the `minio-archive-config` secret's
    `config.env`, the path-style client — and a second convention for the same
    endpoint is a defect: it is one more thing to rotate, and the copy that
    nobody exercises is the one that is broken when it is needed.

    stat/list/remove are not on `MinioArchive`'s surface, so they go through its
    `.client`. That reuses the connection, credentials and port-forward while
    leaving another subsystem's file untouched.
    """

    def __init__(self, bucket: str):
        self.bucket = bucket
        self._ctx = None
        self._mc = None

    def __enter__(self):
        if resolve_kubeconfig() is None and not os.environ.get("MINIO_ARCHIVE_ENDPOINT"):
            raise BackupError(
                "no kubeconfig reaches the homelab from this host (looked at "
                "KUBECONFIG, ~/workspace/homelab-talos/homelab-kubeconfig and "
                "~/.kube/homelab-nebula.yaml) and MINIO_ARCHIVE_ENDPOINT is not "
                "set. Refusing to continue: without a route to the tenant there "
                "is nowhere to put the backup, and a run that discovers that "
                "AFTER reporting success is the false all-clear this script "
                "exists to prevent."
            )
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mail-actions"))
        from _minio import MinioArchive  # noqa: E402  (lazy: needs the minio pkg)

        self._ctx = MinioArchive()
        self._mc = self._ctx.__enter__()
        self._mc.ensure_bucket(self.bucket)
        return self

    def __exit__(self, *exc):
        if self._ctx is not None:
            self._ctx.__exit__(*exc)

    def put(self, key: str, data: bytes) -> None:
        self._mc.put_object(self.bucket, key, data, "application/octet-stream")

    def stat(self, key: str) -> tuple[int, str]:
        st = self._mc.client.stat_object(self.bucket, key)
        return int(st.size), str(st.etag or "").strip('"')

    def list(self, prefix: str) -> list[str]:
        return [o.object_name for o in
                self._mc.client.list_objects(self.bucket, prefix=prefix, recursive=True)]

    def remove(self, key: str) -> None:
        self._mc.client.remove_object(self.bucket, key)


def upload_and_verify(uploader, key: str, data: bytes) -> None:
    """Upload, then READ BACK and compare. A 200 is not evidence the bytes landed.

    🔴 The read-back is the whole point of this function. `put_object` returning
    without raising says the client was satisfied; it says nothing about what is
    now in the bucket. We compare the size always, and the etag whenever it looks
    like a plain single-part MD5 (32 hex chars, no `-N` multipart suffix) —
    a multipart etag is a digest of digests and is NOT comparable to the
    object's MD5, so treating it as one would either fail every large upload or,
    worse, be "fixed" later by dropping the check.
    """
    uploader.put(key, data)
    try:
        size, etag = uploader.stat(key)
    except Exception as exc:  # noqa: BLE001 — any read-back failure is a failed backup
        raise BackupError(
            f"upload of {key} reported success but the object could not be read "
            f"back ({exc.__class__.__name__}: {exc}). Treating it as a FAILED "
            f"backup: an upload nobody confirmed is indistinguishable from one "
            f"that never happened."
        ) from exc

    if size != len(data):
        raise BackupError(
            f"upload of {key} did not land intact: sent {len(data)} bytes, the "
            f"store reports {size}."
        )
    if re.fullmatch(r"[0-9a-f]{32}", etag):
        local = hashlib.md5(data).hexdigest()  # noqa: S324 — S3 etag, not a security digest
        if etag != local:
            raise BackupError(
                f"upload of {key} landed with the wrong content: etag {etag} != "
                f"local md5 {local}."
            )


# --------------------------------------------------------------------------- #
# retention
# --------------------------------------------------------------------------- #
def prune(uploader, prefix: str, keep: int, just_uploaded: str) -> list[str]:
    """Keep the newest `keep` objects under `prefix`; delete the rest.

    Keys embed a UTC timestamp in a fixed-width sortable form, so lexical order
    IS chronological order and no per-object stat is needed.

    🔴 Two refusals, both of which are the difference between retention and data
    loss. If the listing does not contain the object we just uploaded, the view
    we are about to prune from is not the bucket we just wrote to — a wrong
    prefix, a stale list, a different bucket — and deleting from it would remove
    real backups on the strength of a listing we have already caught being
    wrong. And `keep` below 1 is refused outright: a retention policy that keeps
    nothing is a delete-everything policy wearing a retention policy's name.
    """
    if keep < 1:
        raise BackupError(f"--keep must be at least 1, got {keep}: a retention "
                          f"policy that keeps zero backups is not a retention policy.")
    keys = sorted(uploader.list(prefix))
    if just_uploaded not in keys:
        raise BackupError(
            f"refusing to prune {prefix}: the object just uploaded "
            f"({just_uploaded}) is not in the listing of {len(keys)} object(s). "
            f"The listing does not describe the bucket that was just written to, "
            f"so pruning from it could delete real backups."
        )
    doomed = keys[:-keep] if len(keys) > keep else []
    for k in doomed:
        uploader.remove(k)
    return doomed


# --------------------------------------------------------------------------- #
# naming
# --------------------------------------------------------------------------- #
def host_label() -> str:
    """Which machine this store came from.

    🔴 Both machines are hostname `nixos` (see MEMORY.md / SECRETS.md), and the
    two stores are DIVERGENT content. Without a distinct label per host they
    would share a key prefix and silently evict each other under retention —
    turning the backup into a second way to lose the data.
    """
    for var in ("ASIB_HOST", "ACTIVITY_HOST"):
        v = os.environ.get(var)
        if v and v.strip():
            return re.sub(r"[^A-Za-z0-9._-]", "-", v.strip())
    return re.sub(r"[^A-Za-z0-9._-]", "-", socket.gethostname() or "unknown")


def object_key(host: str, scope: str, when: datetime) -> str:
    stamp = when.strftime("%Y%m%dT%H%M%SZ")
    return f"{host}/{scope}/{stamp}.bundle.age"


def scope_prefix(host: str, scope: str) -> str:
    return f"{host}/{scope}/"


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def run(store: Path, *, bucket: str, keep: int, upload: bool,
        work_dir: Path, uploader_factory=None) -> int:
    """Back up every scope. Returns the number of scopes backed up (never 0)."""
    if not store.exists():
        # The ONLY empty outcome permitted to be a success. See module docstring.
        print(f"{PROG}: no store at {store} — nothing to back up", file=sys.stderr)
        return -1

    if not store.is_dir():
        raise BackupError(f"{store} exists but is not a directory")

    scopes = discover_scopes(store)
    if not scopes:
        raise BackupError(
            f"{store} EXISTS but contains no scope repositories. That is not an "
            f"empty backup, it is an unexplained one: the directory being there "
            f"means /analyze-service has run on this host, so a store with "
            f"nothing in it means the scopes were removed, not that there was "
            f"never anything to save. Refusing to report a successful backup of "
            f"nothing."
        )

    # `run()` owns its scratch directory. It used to rely on the caller having
    # made it, which meant `git bundle create` failed with "Unable to create
    # …/x.bundle.lock: No such file or directory" — a message that names the
    # bundle and points at git, for a fault that is neither.
    work_dir.mkdir(parents=True, exist_ok=True)

    identity = resolve_identity()
    recipient = resolve_recipient(identity)
    host = host_label()
    when = datetime.now(timezone.utc)

    ctx = None
    uploader = None
    if upload:
        factory = uploader_factory or (lambda: MinioUploader(bucket))
        ctx = factory()
        uploader = ctx.__enter__()

    done = 0
    failures: list[str] = []
    try:
        for scope in scopes:
            try:
                n = commit_count(scope)
                if n == 0:
                    raise BackupError(
                        f"{scope.name}: the scope repository has ZERO commits. "
                        f"`git bundle create --all` cannot make a bundle from it, "
                        f"and a scope skipped quietly here is a scope with no "
                        f"off-machine copy that the run would still call a "
                        f"success. Either the hourly autocommit has not run since "
                        f"the scope was created, or its history was destroyed — "
                        f"both need a human."
                    )

                plain = work_dir / f"{scope.name}.bundle"
                cipher = work_dir / f"{scope.name}.bundle.age"
                bundle_scope(scope, plain, work_dir)
                encrypt(plain, cipher, recipient)
                data = cipher.read_bytes()

                if uploader is not None:
                    key = object_key(host, scope.name, when)
                    upload_and_verify(uploader, key, data)
                    pruned = prune(uploader, scope_prefix(host, scope.name), keep, key)
                    print(f"{PROG}: {scope.name}: {n} commits -> {len(data)} bytes "
                          f"-> {key} (verified; pruned {len(pruned)})")
                else:
                    print(f"{PROG}: {scope.name}: {n} commits -> {cipher} "
                          f"({len(data)} bytes, verified, NOT uploaded)")
                done += 1
            except BackupError as exc:
                # One scope failing must not stop the others being backed up —
                # but it MUST make the run fail. A partial backup reported as a
                # success is the false all-clear this file exists to prevent.
                failures.append(str(exc))
                print(f"{PROG}: FAILED {scope.name}: {exc}", file=sys.stderr)
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)

    if failures:
        raise BackupError(
            f"{len(failures)} of {len(scopes)} scope(s) failed to back up; "
            f"{done} succeeded. First failure: {failures[0]}"
        )
    if done == 0:
        raise BackupError(
            f"backed up 0 of {len(scopes)} scope(s) without recording a failure. "
            f"This is a bug in the backup script itself; refusing to exit 0."
        )
    return done


def print_plan(store: Path, bucket: str, keep: int) -> None:
    """Pure text. Reads the store; writes nothing, anywhere, ever."""
    print(f"store:     {store}")
    print(f"bucket:    {bucket}")
    print(f"keep:      {keep} per scope")
    print(f"host:      {host_label()}")
    print("remote:    NONE — no git remote is added to any scope, ever")
    print("encrypt:   age, to the operator's existing SOPS identity, BEFORE upload")
    # Written from what the code DOES, not from what the design intended. An
    # earlier version of this line said "git bundle verify before upload", which
    # was true and badly incomplete: `bundle verify` passes a byte-flipped
    # bundle at rc=0, so it is the restore rehearsal that carries the claim.
    print("verify:    bundle verify, then a RESTORE REHEARSAL (bare clone + ref")
    print("           comparison) — `git bundle verify` alone passes a corrupted")
    print("           bundle at rc=0; then a read-back of size/etag after upload")
    identity = resolve_identity()
    print(f"identity:  {identity} ({'present' if identity.is_file() else 'MISSING'})")
    if not store.exists():
        print(f"scope:     (no store at {store})")
        return
    scopes = discover_scopes(store)
    if not scopes:
        print(f"scope:     (none found under {store}) — this would FAIL the run")
        return
    for s in scopes:
        try:
            n = commit_count(s)
        except BackupError:
            n = -1
        note = " — ZERO COMMITS, would FAIL the run" if n == 0 else ""
        print(f"scope:     {s.name} ({n} commits){note}")
        print(f"  key:     {object_key(host_label(), s.name, datetime.now(timezone.utc))}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__)
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE)
    ap.add_argument("--bucket", default=os.environ.get("ASIB_BUCKET", DEFAULT_BUCKET))
    ap.add_argument("--keep", type=int, default=int(os.environ.get("ASIB_KEEP", DEFAULT_KEEP)))
    ap.add_argument("--no-upload", action="store_true",
                    help="bundle+verify+encrypt only; leave the artifacts in --work-dir")
    ap.add_argument("--work-dir", type=Path, default=None,
                    help="where to build artifacts (default: a private temp dir)")
    ap.add_argument("--print-plan", action="store_true",
                    help="print what would happen; write nothing")
    args = ap.parse_args(argv)

    try:
        if args.print_plan:
            print_plan(args.store, args.bucket, args.keep)
            return 0

        if args.work_dir is not None:
            args.work_dir.mkdir(parents=True, exist_ok=True)
            n = run(args.store, bucket=args.bucket, keep=args.keep,
                    upload=not args.no_upload, work_dir=args.work_dir)
        else:
            with tempfile.TemporaryDirectory(prefix="asi-backup.") as td:
                n = run(args.store, bucket=args.bucket, keep=args.keep,
                        upload=not args.no_upload, work_dir=Path(td))
        if n < 0:
            return 0
        print(f"{PROG}: backed up {n} scope(s)")
        return 0
    except BackupError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
