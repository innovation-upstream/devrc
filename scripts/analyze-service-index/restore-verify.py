#!/usr/bin/env python3
"""RESTORE VERIFIER for the /analyze-service index off-machine backup.

WHAT THIS CLOSES — A SEAM NOBODY OWNED
--------------------------------------
`backup.py` is careful, and every check it performs happens BEFORE the bytes
leave the process. It bundles, it `bundle verify`s, it rehearses a restore from
the PLAINTEXT bundle, and only then does it encrypt and upload. Encryption,
upload, object storage and retention are all DOWNSTREAM of every check that
exists today. Nothing has ever verified the artifact **as it actually sits in
MinIO**.

That is the shape RULES.md calls an isolation seam: two surfaces each tested in
isolation, and the defect lives between them. `backup.py` proves "the bundle I
built restores". This proves "the object in the bucket restores" — which is a
different claim, and it is the only one a disaster recovery depends on. The
things that can only be seen from this side:

  * an `age` recipient that no longer matches the identity the operator holds
    (backup.py DERIVES the recipient, so it cannot notice its own drift),
  * bit rot, a partial PUT, or a storage-layer fault after the read-back,
  * retention that deleted the scope's artifacts,
  * a scope that silently stopped being backed up,
  * a backup timer that stopped firing days ago behind a green-looking bucket.

So this script starts from THE BYTES IN THE BUCKET and ends at RECONSTRUCTED
GIT HISTORY, using the operator's real age identity. It rehearses the actual
disaster-recovery path documented in SECRETS.md.

🔴 `git bundle verify` IS NOT AN INTEGRITY CHECK AND THIS SCRIPT NEVER CALLS IT
------------------------------------------------------------------------------
Measured on this repo, git 2.x, on a bundle with a single byte flipped in the
middle of the packfile:

    git bundle verify <corrupted>  ->  rc=0
    "The bundle records a complete history."
    git clone         <corrupted>  ->  rc=128, "error: index-pack died"

`bundle verify` validates the bundle HEADER and the PREREQUISITES. It does not
walk the pack, so bit rot, a truncated write or a partial copy all pass it while
it prints the word "complete". A verifier that stopped there would manufacture
confidence, which is worse than not checking at all.

So verification here is a RESTORE: `git clone --mirror` the decrypted bundle
(which forces `index-pack` to validate every object's hash and the pack
checksum) followed by `git fsck` on the result. `bundle` is deliberately absent
from `_READ_ONLY_SUBCOMMANDS` below, and `scripts/tests/
test_analyze_service_index_restore_verify.py` pins both halves: that the
corrupted artifact fails HERE, and that `git bundle verify` returns 0 on the
very same bundle.

WHY THE CROSS-CHECK IS A RELATIONSHIP, NOT AN EQUALITY
------------------------------------------------------
🔴 The live store advances hourly (`analyze-service-index-commit.timer`), and
this verifier is meant to be runnable at any moment. Asserting that the restored
refs EQUAL the live refs would go red the instant the next autocommit lands — a
permanently-red gate, which this repo treats as worse than no gate at all
(RULES.md), because it trains everyone to click through the one alarm that
matters.

What is asserted instead is stable under forward progress and still catches
corruption, truncation and staleness:

  1. every commit reachable in the RESTORED repository also exists in the live
     scope repository — history cannot have been rewritten or lost;
  2. every restored ref is present in the live scope, and the restored tip is an
     ANCESTOR OF (or equal to) the live tip — the artifact is a prefix of
     today's history, never a fork of it;
  3. the LAG (how many live commits are newer than the artifact) is reported as
     INFORMATION — a growing lag is normal, that is what "hourly commits, daily
     backup" means;
  4. the artifact's AGE is gated by `--max-lag-days`, and that CAN go red. A
     bucket whose newest object is a week old is a backup that stopped, and it
     looks identical to a healthy one from every other angle.

🔴 AN ABSENT LIVE SCOPE IS THE ACTUAL DISASTER CASE, AND IT IS NOT A FAILURE
---------------------------------------------------------------------------
If the store is gone, there is nothing to cross-check against — that is the
scenario these backups exist for, and failing there would make the verifier
useless at the exact moment it is needed. So a scope with no live counterpart is
verified for SELF-CONSISTENCY only (restore + `git fsck` + a non-zero commit
count) and reported as `NOT CROSS-CHECKED`, never folded into the same word as a
scope that was checked against live. "Verified" and "verified against what" are
two facts; printing one of them is how a partial answer gets read as a whole one.

🔴 EVERY EMPTY OUTCOME IS LOUD
------------------------------
Mirroring `backup.py`'s stance, for the same reason: a verifier that reports
success having checked nothing is a false all-clear, and a false all-clear about
backups is worse than no verifier.

  * zero objects under the prefix                      -> failure
  * a selected scope with zero objects                 -> failure
  * a decrypted bundle that restores to zero commits   -> failure
  * zero commits compared against the live scope       -> failure
  * a run that verified zero scopes                    -> failure

The single exception is the same one `backup.py` permits: a host that has never
run the backup at all — no local store AND no objects under its own default
prefix. That path prints its reason and exits 0, and it is the only one that
may. Asking about a prefix EXPLICITLY (`--host` / `--prefix`) removes the
exception: you named a host and it has no backups, which is an answer, not a
no-op.

🔴 THE DECRYPTED BUNDLE IS A FULL PLAINTEXT COPY OF A CLIENT-CONFIDENTIAL SCOPE
------------------------------------------------------------------------------
It exists only between `age -d` and `git clone`, and it is unlinked on EVERY
path out of that window including the failure paths — a failed restore is
exactly when a plaintext copy is most likely to be left behind and least likely
to be noticed. The restored repository is plaintext history too, and dies with
it. Everything created here is mode 0600 inside a 0700 directory.

`--keep-work-dir` is the one way to keep the restored repositories, and it
REQUIRES `--work-dir` (an option that silently does nothing is a lie) and prints
what it left behind. The decrypted bundle dies even then.

Usage:
    restore-verify.py [--bucket B | --from-dir DIR] [--host H | --prefix P]
                      [--scope S] [--identity FILE] [--store DIR]
                      [--all | --latest-only] [--max-lag-days N]
                      [--work-dir DIR] [--keep-work-dir] [--print-plan]

  --print-plan   pure text; reads the local store, touches NEITHER the network
                 nor the identity. Writes nothing anywhere.
  --from-dir     verify artifacts already fetched to disk (SECRETS.md's restore
                 recipe step 1b) instead of reaching MinIO. Everything below the
                 reader is identical, so it is the same verification.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup as B  # noqa: E402  (sibling module; the producer this verifies)

PROG = "analyze-service-index-restore-verify"

DEFAULT_BUCKET = B.DEFAULT_BUCKET
DEFAULT_STORE = B.DEFAULT_STORE
DEFAULT_IDENTITY = B.DEFAULT_IDENTITY

# 🔴 The backup timer runs DAILY (04:30 + up to 30 minutes of randomised delay),
# so a healthy bucket's newest object is under ~1.3 days old and a single missed
# run leaves it under ~2.3. Three days is "two consecutive runs did not happen",
# which is a real fault and not a blip — chosen to be loose enough that the gate
# cannot go red for a reason nobody can act on, and tight enough that a stopped
# timer is caught inside a working week rather than at recovery time.
DEFAULT_MAX_LAG_DAYS = 3.0

# 🔴 THERE IS DELIBERATELY NO LOCAL DIR-MODE CONSTANT. The work directory is
# created 0700 by `B._private_dir()`, which owns that number and chmods
# unconditionally (an existing directory keeps its mode through
# `mkdir(exist_ok=True)`, which is every run after the first with an
# explicit --work-dir). A second copy of the constant here would be an
# unused alias that reads like the thing enforcing the mode while the
# enforcement lived elsewhere — and would keep reading that way if the
# `_private_dir` call were ever dropped. The FILE mode is aliased because
# this module really does chmod files itself.
_FILE_MODE = B._FILE_MODE    # 0600

# 🔴 THE READ-ONLY ALLOWLIST, the same guard `backup.py` carries and for the same
# reason: this script is pointed at the ONLY copy of the data, and "we only read
# it" has to be a property of the code rather than of the author's care.
#
# It is DELIBERATELY NOT THE SAME SET as backup.py's. Two differences, both
# load-bearing:
#
#   * `bundle` is ABSENT. This script must never call `git bundle verify` — the
#     measurement in the module docstring is the entire reason it exists, and a
#     later "simplification" that reaches for the cheap check is refused at the
#     call rather than in review.
#   * `cat-file`, `merge-base` and `log` are PRESENT, because the cross-check
#     asks the live scope three questions backup.py never asks it: does this
#     object exist, is this commit an ancestor of that one, and when was it
#     made. None of the three can write.
#
# Pinned by the test suite as an EXACT set, not a subset.
_READ_ONLY_SUBCOMMANDS = frozenset(
    {"rev-list", "rev-parse", "for-each-ref", "cat-file", "merge-base", "log",
     "remote"})

# 🔴 A VERB IS NOT GRANULAR ENOUGH — see the long note in backup.py, measured
# there: `git remote` lists (a read) while `git remote add` writes `.git/config`,
# and the verb-only allowlist permitted both. `remote` is the only dispatcher in
# the set above; `None` means "the bare verb, options only", which is the listing
# form and the only one this script uses.
_ALLOWED_SUBVERBS: dict[str, frozenset] = {
    "remote": frozenset({None}),
}

# `<prefix>/<scope>/YYYYmmddTHHMMSSZ.bundle.age` — the shape `backup.object_key`
# writes. Pinned against that function by the test suite rather than remembered,
# because a key you cannot parse is an artifact whose AGE you cannot judge.
_STAMP_FMT = "%Y%m%dT%H%M%SZ"
_STAMP_RE = re.compile(r"^(?P<stamp>\d{8}T\d{6}Z)\.bundle\.age$")


class RestoreVerifyError(B.BackupError):
    """A failure that must make the whole run non-zero.

    Subclasses `BackupError` so that `main()`'s one handler covers both this
    script's own refusals and the ones raised by the helpers it reuses from
    `backup.py` (`_git_scratch`'s store guard, most importantly). Two exception
    hierarchies over one pipeline is how a failure escapes to a bare traceback.
    """


# --------------------------------------------------------------------------- #
# git — read-only by construction, against the LIVE store
# --------------------------------------------------------------------------- #
def _subverb(args: tuple[str, ...]) -> str | None:
    """The dispatcher subverb in `args`, or None for a bare verb."""
    for a in args[1:]:
        if not a.startswith("-"):
            return a
    return None


def _git(repo: Path, *args: str, stdin_text: str | None = None
         ) -> subprocess.CompletedProcess:
    """Run one read-only git subcommand against `repo` (the LIVE store scope).

    Refuses anything outside `_READ_ONLY_SUBCOMMANDS`. `gc.auto=0` stops git
    deciding mid-read that the repository is due a repack — a write, perfectly
    reasonable behaviour, and it would silently break the read-only guarantee
    this whole file rests on. `core.hooksPath=/dev/null` stops a hook in the
    store running under the verifier's privileges.
    """
    if not args or args[0] not in _READ_ONLY_SUBCOMMANDS:
        raise RestoreVerifyError(
            f"refusing git subcommand {args[0] if args else '(none)'!r}: the "
            f"/analyze-service index store is READ-ONLY to this script. "
            f"Allowed: {sorted(_READ_ONLY_SUBCOMMANDS)}. Note that `bundle` is "
            f"absent ON PURPOSE — `git bundle verify` passes a byte-flipped "
            f"bundle at rc=0, so verification here is a real restore. If a new "
            f"subcommand is genuinely needed, prove it cannot write and add it "
            f"to _READ_ONLY_SUBCOMMANDS in the same commit as the test that "
            f"pins it."
        )
    verb = args[0]
    if verb in _ALLOWED_SUBVERBS:
        sub = _subverb(args)
        if sub not in _ALLOWED_SUBVERBS[verb]:
            raise RestoreVerifyError(
                f"refusing `git {verb} {sub}`: {sub!r} is not a READ-ONLY mode "
                f"of `git {verb}`. The verb is allowlisted; its subverbs are not "
                f"all reads — `git remote add` writes .git/config against the "
                f"only copy of the data. Allowed here: the bare verb only."
            )
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "gc.auto=0",
         "-c", "core.hooksPath=" + os.devnull, *args],
        capture_output=True, text=True, env=B._git_env(), input=stdin_text,
    )


def _scratch(cwd: Path, *args: str, forbidden: Path,
             ) -> subprocess.CompletedProcess:
    """git in a THROWAWAY directory — never the store. See `backup._git_scratch`.

    Reused rather than reimplemented: it already carries the mandatory,
    keyword-only `forbidden` refusal and its own tests. A second copy of a guard
    is a second thing to get wrong, and the copy nobody exercises is the broken
    one.
    """
    return B._git_scratch(cwd, *args, forbidden=forbidden)


def _refs(run: subprocess.CompletedProcess) -> dict[str, str]:
    """`{refname: objectname}` from a `for-each-ref` in that format."""
    out: dict[str, str] = {}
    for ln in run.stdout.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        name, _, oid = ln.partition(" ")
        if name and oid:
            out[name] = oid.strip()
    return out


# --------------------------------------------------------------------------- #
# object store — a READER over the same tenant the producer writes to
# --------------------------------------------------------------------------- #
class MinioDownloader(B.MinioUploader):
    """The real object store, read-only.

    🔴 A THIN subclass of the producer's `MinioUploader`, which is itself a thin
    wrapper over `scripts/mail-actions/_minio.py`. That module owns how this host
    reaches the tenant — the `kubectl port-forward` to `svc/minio` in namespace
    `minio-archive`, the credential read from `minio-archive-config` — and a
    third convention for the same endpoint would be one more thing to rotate.

    🔴 `__enter__` is overridden for ONE reason: `MinioUploader` calls
    `ensure_bucket`, which CREATES the bucket if it is missing. For a producer
    that is correct. For a verifier it would turn the loudest possible finding —
    "the bucket holding every off-machine copy is GONE" — into the quiet one,
    "0 objects", and it would do it by writing to the store it is auditing. So
    this asks whether the bucket exists and refuses if it does not.
    """

    def __enter__(self):
        if B.resolve_kubeconfig() is None and not os.environ.get("MINIO_ARCHIVE_ENDPOINT"):
            raise RestoreVerifyError(
                "no kubeconfig reaches the homelab from this host (looked at "
                "KUBECONFIG, ~/workspace/homelab-talos/homelab-kubeconfig and "
                "~/.kube/homelab-nebula.yaml) and MINIO_ARCHIVE_ENDPOINT is not "
                "set. Refusing to continue: without a route to the tenant the "
                "artifacts cannot be read, and 'could not look' must never be "
                "reported as 'looked and found nothing wrong'."
            )
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mail-actions"))
        from _minio import MinioArchive  # noqa: E402  (lazy: needs the minio pkg)

        self._ctx = MinioArchive()
        self._mc = self._ctx.__enter__()
        # `__exit__` is NOT called when `__enter__` raises, so anything after the
        # inner enter must clean up after itself or the `kubectl port-forward`
        # child outlives the run.
        try:
            if not self._mc.client.bucket_exists(self.bucket):
                raise RestoreVerifyError(
                    f"bucket {self.bucket!r} DOES NOT EXIST. This is not an "
                    f"empty result — it is the whole off-machine copy of the "
                    f"/analyze-service index store being absent. Refusing to "
                    f"report anything other than a failure."
                )
        except BaseException:
            self._ctx.__exit__(*sys.exc_info())
            self._ctx = None
            self._mc = None
            raise
        return self

    def get(self, key: str) -> bytes:
        r = self._mc.client.get_object(self.bucket, key)
        try:
            return r.read()
        finally:
            r.close()
            r.release_conn()


class DirectoryStore:
    """The same reader interface over a LOCAL DIRECTORY of artifacts.

    Two reasons it exists, and neither is "for the tests":

      * SECRETS.md's restore recipe has the operator FETCH objects to disk
        before doing anything with them. Verifying what is on disk — without a
        cluster, a port-forward or a second download — is the natural next step
        of that procedure, and it is the same verification either way because
        everything below this class is unchanged.
      * A run against MinIO cannot be exercised offline, so without this the
        CLI's own exit codes could only ever be reasoned about rather than
        measured. A gate you have not watched go red is a claim.

    Keys are paths relative to `root`, so a `mc mirror`/`aws s3 sync` of the
    bucket is verifiable as-is.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.bucket = str(self.root)

    def __enter__(self):
        if not self.root.is_dir():
            raise RestoreVerifyError(
                f"--from-dir {self.root} is not a directory. Refusing to treat a "
                f"missing path as an object store with nothing in it: 'could not "
                f"look' and 'looked and found nothing' are different answers and "
                f"only one of them is a verdict.")
        return self

    def __exit__(self, *_exc):
        return False

    def list(self, prefix: str) -> list[str]:
        out = []
        for p in sorted(self.root.rglob("*")):
            if not p.is_file():
                continue
            k = str(p.relative_to(self.root))
            if k.startswith(prefix):
                out.append(k)
        return out

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()


# --------------------------------------------------------------------------- #
# keys
# --------------------------------------------------------------------------- #
def normalise_prefix(prefix: str) -> str:
    """A listing prefix always ends in exactly one `/`.

    Without this, `--host workbench` would also list `workbench-laptop/…`: S3
    prefixes are byte prefixes, not path components, so the missing slash is a
    silent scope widening rather than an error.
    """
    return prefix.rstrip("/") + "/"


def split_key(key: str, prefix: str) -> tuple[str, datetime]:
    """`(scope, stamp)` for one object key, or raise.

    🔴 An unparseable key is a FAILURE, never a skip. The stamp is the only thing
    that answers "when was this backup taken", which is the question
    `--max-lag-days` is built on; a key this cannot read is an artifact whose age
    is unknown, and an unknown age silently passes a staleness check.
    """
    if not key.startswith(prefix):
        raise RestoreVerifyError(
            f"object key {key!r} does not start with the prefix {prefix!r} it "
            f"was listed under. The listing does not describe the bucket that "
            f"was queried, so nothing derived from it can be trusted.")
    rest = key[len(prefix):]
    scope, sep, fname = rest.partition("/")
    if not sep or not scope or "/" in fname:
        raise RestoreVerifyError(
            f"object key {key!r} is not <prefix>/<scope>/<stamp>.bundle.age — "
            f"this verifier cannot say which scope it belongs to, and a scope "
            f"it cannot name is a scope it cannot report as unverified.")
    m = _STAMP_RE.match(fname)
    if not m:
        raise RestoreVerifyError(
            f"object key {key!r} carries no parseable UTC stamp. The stamp is "
            f"what `--max-lag-days` reads; an artifact whose age cannot be "
            f"determined would pass a staleness check by default, which is the "
            f"false all-clear this script exists to prevent.")
    stamp = datetime.strptime(m.group("stamp"), _STAMP_FMT).replace(
        tzinfo=timezone.utc)
    return scope, stamp


def group_by_scope(keys: list[str], prefix: str) -> dict[str, list[tuple[datetime, str]]]:
    """`{scope: [(stamp, key), …]}`, each list oldest-first."""
    out: dict[str, list[tuple[datetime, str]]] = {}
    for k in keys:
        scope, stamp = split_key(k, prefix)
        out.setdefault(scope, []).append((stamp, k))
    for v in out.values():
        v.sort()
    return out


# --------------------------------------------------------------------------- #
# verdicts
# --------------------------------------------------------------------------- #
@dataclass
class ArtifactVerdict:
    """What one artifact was proven to be. Every field is a measured number."""
    scope: str
    key: str
    size: int
    commits_restored: int
    refs_restored: int
    cross_checked: bool
    commits_compared: int = 0
    lag_commits: int | None = None
    age_days: float = 0.0
    no_cross_check_reason: str | None = None
    ref_names: list[str] = field(default_factory=list)

    def line(self) -> str:
        head = (f"{self.scope}: {self.key} ({self.size} bytes) -> RESTORED "
                f"{self.commits_restored} commit(s) over {self.refs_restored} "
                f"ref(s), fsck OK, age {self.age_days:.2f}d")
        if self.cross_checked:
            return (f"{head}; VERIFIED AGAINST LIVE "
                    f"({self.commits_compared} commit(s) compared, lag "
                    f"{self.lag_commits} commit(s))")
        return (f"{head}; NOT CROSS-CHECKED ({self.no_cross_check_reason}) — "
                f"self-consistency only")


# --------------------------------------------------------------------------- #
# the verification of ONE artifact
# --------------------------------------------------------------------------- #
def decrypt(cipher: Path, plain: Path, identity: Path) -> None:
    """`age -d -i <identity>`. A failure here is a DECRYPT failure, and says so.

    🔴 Named distinctly from a restore failure on purpose. "The key does not open
    it" and "the bytes do not reconstruct" are different faults with different
    remedies — one is a key-management problem, the other is data loss — and a
    verifier that reports them with one word sends the operator to the wrong
    place at the worst possible time.
    """
    if shutil.which("age") is None:
        raise RestoreVerifyError(
            "age is not on PATH — it is declared in nix/pkgs/default.nix and in "
            "flake.nix `gateTools`; add it there rather than working around it. "
            "There is no fallback: an artifact this cannot decrypt is an "
            "artifact this cannot verify, and skipping it would report safety "
            "that was never measured.")
    p = subprocess.run(
        ["age", "--decrypt", "--identity", str(identity),
         "--output", str(plain), str(cipher)],
        capture_output=True, text=True)
    if p.returncode != 0:
        raise RestoreVerifyError(
            f"DECRYPT FAILED for {cipher.name} (age rc={p.returncode}): "
            f"{p.stderr.strip()}. The object was retrieved but the identity at "
            f"{identity} does not open it, or the ciphertext is damaged. This is "
            f"NOT a corruption verdict about the git history inside — nothing "
            f"here has read it.")
    if not plain.is_file() or plain.stat().st_size == 0:
        # 🔴 MEASURED: `age` encrypts a ZERO-BYTE payload to a perfectly valid
        # 200-byte ciphertext, and decrypts it back at rc=0. So an object that
        # lists, stats and decrypts cleanly can still carry nothing at all —
        # which is not the empty-object case `verify_artifact` catches (that one
        # is 0 bytes in the bucket), and it is not a corrupt bundle either. Its
        # own token, `decrypted to ZERO bytes`, so a test asserting on the rc
        # branch above cannot be satisfied by this one instead.
        raise RestoreVerifyError(
            f"DECRYPT FAILED for {cipher.name}: the object decrypted to ZERO "
            f"bytes. age reported success, so this is not damage — it is a "
            f"valid encryption of nothing. An empty bundle restores to nothing "
            f"and would otherwise be reported as a clean restore of a scope "
            f"with no history.")
    os.chmod(plain, _FILE_MODE)


def restore(bundle: Path, dest: Path, work_dir: Path, forbidden: Path) -> None:
    """THE verification: clone the bundle, then `git fsck` what came out.

    🔴 This is where `git bundle verify` would go if it worked, and it does not —
    see the module docstring's measurement. `git clone --mirror` runs
    `index-pack`, which validates every object's hash and the pack checksum, so a
    bundle whose bytes do not reconstruct cannot survive this call.

    `--mirror`, NOT `--bare`: MEASURED in `backup.py`'s own tests, a `--bare`
    clone uses the default `+refs/heads/*:refs/heads/*` refspec and SILENTLY
    DROPS every other ref. A restore that quietly loses `refs/notes/commits` is
    not a restore, and a comparison built on it would go red on every future run.
    """
    if dest.exists():
        shutil.rmtree(dest)
    c = _scratch(work_dir, "clone", "--mirror", "--quiet", str(bundle), str(dest),
                 forbidden=forbidden)
    if c.returncode != 0:
        raise RestoreVerifyError(
            f"RESTORE FAILED for {bundle.name}: the decrypted bundle could NOT "
            f"BE CLONED (git rc={c.returncode}): {c.stderr.strip()}. This is "
            f"what a corrupted, truncated or partially-stored artifact looks "
            f"like. Note that `git bundle verify` PASSES such a bundle at rc=0 "
            f"printing 'complete history' — which is precisely why this "
            f"verifier restores instead of asking it.")

    f = _scratch(dest, "fsck", "--no-progress", forbidden=forbidden)
    if f.returncode != 0:
        raise RestoreVerifyError(
            f"FSCK FAILED for {bundle.name}: the restored repository is not "
            f"internally consistent (git fsck rc={f.returncode}): "
            f"{(f.stderr.strip() or f.stdout.strip())}. The clone succeeded, so "
            f"this is damage the pack checksum did not cover.")


def _restored_facts(dest: Path, forbidden: Path) -> tuple[dict[str, str], list[str]]:
    """`(refs, commit oids)` of the restored repository."""
    fmt = "--format=%(refname) %(objectname)"
    refs = _refs(_scratch(dest, "for-each-ref", fmt, forbidden=forbidden))
    rl = _scratch(dest, "rev-list", "--all", forbidden=forbidden)
    if rl.returncode != 0:
        raise RestoreVerifyError(
            f"could not walk the restored repository's history (git rev-list "
            f"rc={rl.returncode}): {rl.stderr.strip()}. A repository whose "
            f"history cannot be READ must never be reported as one that is "
            f"EMPTY.")
    commits = [ln.strip() for ln in rl.stdout.splitlines() if ln.strip()]
    return refs, commits


def cross_check(scope_repo: Path, restored_refs: dict[str, str],
                restored_commits: list[str]) -> tuple[int, int]:
    """Compare the restored history against the LIVE scope. `(compared, lag)`.

    🔴 The relationship, not an equality — see the module docstring. Three
    assertions, each of which a corrupted, truncated or rewritten artifact
    breaks and ordinary forward progress does not:

      * every restored commit still EXISTS in the live scope,
      * every restored ref is PRESENT in the live scope,
      * every restored tip is an ANCESTOR OF (or equal to) the live tip.

    Returns the number of commits actually compared and the lag in commits. The
    count is returned rather than discarded because "0 problems" and "0 things
    looked at" are indistinguishable in a verdict, and only one of them is good
    news.

    NOTE: there is deliberately NO empty-`restored_commits` guard here. The
    caller refuses a zero-commit restore before it ever gets this far, and a
    second copy of that check could not fail — a guard that cannot fire reads as
    coverage while providing none, which is worse than not being there.
    """
    # 1. every restored commit exists in the live scope. `--batch-check` is one
    #    process for the whole set; a per-commit `cat-file -e` would be hundreds.
    probe = _git(scope_repo, "cat-file", "--batch-check=%(objectname) %(objecttype)",
                 stdin_text="\n".join(restored_commits) + "\n")
    lines = [ln.strip() for ln in probe.stdout.splitlines() if ln.strip()]
    if len(lines) != len(restored_commits):
        raise RestoreVerifyError(
            f"{scope_repo.name}: asked the live scope about "
            f"{len(restored_commits)} commit(s) and got {len(lines)} answer(s) "
            f"back (git rc={probe.returncode}): {probe.stderr.strip()}. The "
            f"instrument did not answer the question it was asked, so its "
            f"silence is not evidence the commits are there.")
    missing = [ln.split()[0] for ln in lines if "missing" in ln.split()[1:]]
    if missing:
        raise RestoreVerifyError(
            f"CROSS-CHECK FAILED for {scope_repo.name}: {len(missing)} of "
            f"{len(restored_commits)} commit(s) in the RESTORED artifact do not "
            f"exist in the live scope (first: {missing[0]}). The artifact is not "
            f"an earlier state of this repository — history was rewritten, or "
            f"the artifact belongs to a different scope.")

    fmt = "--format=%(refname) %(objectname)"
    live_refs = _refs(_git(scope_repo, "for-each-ref", fmt))

    # 2. + 3. per-ref presence and ancestry.
    for name, oid in sorted(restored_refs.items()):
        if name not in live_refs:
            raise RestoreVerifyError(
                f"CROSS-CHECK FAILED for {scope_repo.name}: ref {name} is in "
                f"the RESTORED artifact but ABSENT from the live scope. The "
                f"hourly committer only ever MOVES a branch — it has no code "
                f"that creates or deletes a ref and no network to fetch one — "
                f"so a ref that vanished is a deletion, not forward progress.")
        live_oid = live_refs[name]
        if live_oid == oid:
            continue
        a = _git(scope_repo, "merge-base", "--is-ancestor",
                 f"{oid}^{{commit}}", f"{live_oid}^{{commit}}")
        if a.returncode != 0:
            raise RestoreVerifyError(
                f"CROSS-CHECK FAILED for {scope_repo.name}: the restored "
                f"{name} ({oid[:12]}) is NOT an ancestor of the live "
                f"{name} ({live_oid[:12]}) (git rc={a.returncode}"
                f"{': ' + a.stderr.strip() if a.stderr.strip() else ''}). The "
                f"artifact is a FORK of today's history rather than a prefix of "
                f"it, which forward progress cannot produce.")

    # The lag: live commits NOT reachable from any restored tip. Exact, and
    # exactly the number an operator wants — "how much would I lose if I
    # restored this right now".
    tips = sorted(set(restored_refs.values()))
    lag = _git(scope_repo, "rev-list", "--count", "--all", "--not", *tips)
    if lag.returncode != 0:
        raise RestoreVerifyError(
            f"{scope_repo.name}: could not measure the lag against the live "
            f"scope (git rev-list rc={lag.returncode}): {lag.stderr.strip()}. "
            f"An unmeasured lag is not a lag of zero.")
    return len(restored_commits), int(lag.stdout.strip() or "0")


def verify_artifact(downloader, key: str, *, scope: str, stamp: datetime,
                    identity: Path, work_dir: Path, store: Path,
                    keep: bool, now: datetime,
                    live_scope: Path | None) -> ArtifactVerdict:
    """Bucket bytes -> reconstructed git history, for exactly one object."""
    data = downloader.get(key)
    if not data:
        raise RestoreVerifyError(
            f"{scope}: object {key} is EMPTY (0 bytes). An empty object lists "
            f"and stats like any other and restores to nothing.")

    stem = f"{scope}.{stamp.strftime(_STAMP_FMT)}"
    cipher = work_dir / f"{stem}.bundle.age"
    plain = work_dir / f"{stem}.bundle"
    dest = work_dir / f"{stem}.restored.git"

    cipher.write_bytes(data)
    os.chmod(cipher, _FILE_MODE)
    try:
        try:
            # 🔴 THE PLAINTEXT BUNDLE DIES IN THE `finally`, ON EVERY PATH. It is
            # a complete unencrypted copy of a client-confidential scope. A
            # failed restore is exactly when it is most likely to be left behind
            # and least likely to be noticed, so the unlink is not on the success
            # path — and `--keep-work-dir` does NOT extend to it.
            decrypt(cipher, plain, identity)
            restore(plain, dest, work_dir, forbidden=store)
        finally:
            plain.unlink(missing_ok=True)

        refs, commits = _restored_facts(dest, forbidden=store)
        if not commits:
            raise RestoreVerifyError(
                f"{scope}: the artifact {key} restored to ZERO commits. It is a "
                f"valid, empty backup — which is the quiet way for a scope to "
                f"have no off-machine copy while every count says it does.")
        # 🔴 NO `if not refs` GUARD. It looks like the natural sibling of the one
        # above and it is DEAD: `rev-list --all` walks the refs, so zero refs
        # implies zero commits and the check above always speaks first. The
        # reverse is not true — a ref pointing at a blob gives refs with no
        # commits — which is why the COMMIT count is the one that is checked.
        age_days = max(0.0, (now - stamp).total_seconds() / 86400.0)
        verdict = ArtifactVerdict(
            scope=scope, key=key, size=len(data),
            commits_restored=len(commits), refs_restored=len(refs),
            cross_checked=False, age_days=age_days, ref_names=sorted(refs),
        )
        if live_scope is None:
            verdict.no_cross_check_reason = (
                f"no live scope repository at {store / scope}")
            return verdict
        compared, lag = cross_check(live_scope, refs, commits)
        verdict.cross_checked = True
        verdict.commits_compared = compared
        verdict.lag_commits = lag
        return verdict
    finally:
        # 🔴 NO SECOND `plain.unlink()` HERE. There was one, as "belt and
        # braces", and it made the inner `finally` untestable: a mutation
        # deleting the tight one — the guard that actually bounds how long
        # plaintext history exists — was scored SURVIVED, because this line
        # cleaned up after it. One rule, one place; the inner `finally` owns the
        # plaintext bundle on every path.
        if not keep:
            shutil.rmtree(dest, ignore_errors=True)
            cipher.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# main run
# --------------------------------------------------------------------------- #
def local_scopes(store: Path) -> list[str]:
    """Scope names present in the LIVE store, or `[]` if there is no store."""
    if not store.is_dir():
        return []
    return [p.name for p in B.discover_scopes(store)]


def live_scope_path(store: Path, scope: str) -> Path | None:
    """The live repository for `scope`, or None — the disaster case, not an error."""
    p = store / scope
    if p.is_symlink() or not p.is_dir() or not (p / ".git").exists():
        return None
    return p


def uncovered_local_scopes(store: Path, covered: set[str], now: datetime,
                           max_lag_days: float) -> list[str]:
    """Local scopes with NO artifact in the bucket at all. One message each.

    🔴 THE FAILURE THAT IS INVISIBLE FROM EVERY OTHER ANGLE. Every check above
    iterates the objects the bucket HAS, so a scope that stopped being backed up
    — or was never picked up — simply never appears, and the run reports a clean
    verification of the scopes that did. `backup.py` cannot see it either: it
    fails the whole run when a scope errors, but a scope silently missing from
    the bucket after retention expired its last copy leaves no trace on either
    side. Only comparing the two directions finds it.

    🔴 GATED ON THE SCOPE'S AGE, so it cannot be a permanently-red gate. A scope
    created this afternoon genuinely has no artifact until the next 04:30 run,
    and failing on that would put the check into the state RULES.md warns about
    within a day of somebody running /analyze-service on a new repo. The unit is
    the scope's FIRST commit: once it is older than `max_lag_days`, a backup
    should have covered it, and it self-heals on the next run either way.
    """
    out: list[str] = []
    for name in local_scopes(store):
        if name in covered:
            continue
        repo = live_scope_path(store, name)
        if repo is None:                      # not a repository; not our claim
            continue
        p = _git(repo, "log", "--max-parents=0", "--all", "--format=%ct")
        if p.returncode != 0:
            raise RestoreVerifyError(
                f"{name}: the scope exists locally with NO artifact in the "
                f"bucket, and its first commit could not be read (git log rc="
                f"{p.returncode}): {p.stderr.strip()}. A scope whose age cannot "
                f"be determined must never be excused as 'probably too new'.")
        stamps = [int(s) for s in p.stdout.split() if s.strip().isdigit()]
        if not stamps:
            out.append(
                f"{name}: NO ARTIFACT IN THE BUCKET, and the scope has ZERO "
                f"commits locally. `git bundle create --all` cannot make a "
                f"bundle from it, so it has no off-machine copy and cannot get "
                f"one until a human looks.")
            continue
        age_days = (now.timestamp() - min(stamps)) / 86400.0
        if age_days > max_lag_days:
            out.append(
                f"{name}: NO ARTIFACT IN THE BUCKET — the scope exists locally "
                f"and its first commit is {age_days:.1f} days old, so the daily "
                f"backup has had {int(age_days)} chance(s) to cover it and has "
                f"not. Every other check here iterates the objects that ARE "
                f"there, so a scope that stopped being backed up is otherwise "
                f"invisible: the run reports a clean verification of the scopes "
                f"that remain.")
    return out


def run(*, bucket: str, prefix: str, store: Path, identity: Path,
        scope_filter: str | None, verify_all: bool, max_lag_days: float,
        work_dir: Path, keep_work_dir: bool, prefix_was_explicit: bool,
        downloader_factory=None, now: datetime | None = None,
        ) -> list[ArtifactVerdict]:
    """Verify every selected artifact. Returns the verdicts; raises on any failure."""
    now = now or datetime.now(timezone.utc)
    prefix = normalise_prefix(prefix)

    if not identity.is_file():
        raise RestoreVerifyError(
            f"no age identity at {identity}. This verifier decrypts with the "
            f"operator's EXISTING SOPS age key — the same one `backup.py` "
            f"derives its recipient from. Without it the artifacts cannot be "
            f"opened, and 'could not open' must never be reported as 'opened "
            f"and found intact'. Set ASIB_AGE_IDENTITY or SOPS_AGE_KEY_FILE "
            f"(see SECRETS.md).")

    B._private_dir(work_dir)

    factory = downloader_factory or (lambda: MinioDownloader(bucket))
    ctx = factory()
    downloader = ctx.__enter__()
    try:
        keys = sorted(downloader.list(prefix))

        if not keys:
            # 🔴 THE ONLY PERMITTED CLEAN NO-OP, and it is narrow. A host that has
            # never run the backup has nothing to verify; failing there would make
            # this a permanently-red gate on the laptop, which RULES.md says is
            # worse than no gate. Everything else about an empty listing is a
            # failure, because a prefix that was full yesterday and empty today is
            # the single worst thing this script can find.
            if prefix_was_explicit or local_scopes(store):
                raise RestoreVerifyError(
                    f"ZERO objects under {bucket}/{prefix}. "
                    + (f"The prefix was named explicitly, so this is an answer "
                       f"about a host you asked about: it has no off-machine "
                       f"backups at all."
                       if prefix_was_explicit else
                       f"The local store at {store} holds "
                       f"{len(local_scopes(store))} scope(s), so /analyze-service "
                       f"HAS run here and there should be artifacts. An empty "
                       f"prefix beside a populated store means the backup never "
                       f"ran, stopped running, or its objects were deleted.")
                    + " Refusing to report a successful verification of nothing.")
            print(f"{PROG}: no objects under {bucket}/{prefix} and no local "
                  f"store at {store} — this host has never run the backup, so "
                  f"there is nothing to verify", file=sys.stderr)
            return []

        by_scope = group_by_scope(keys, prefix)

        if scope_filter is not None:
            if scope_filter not in by_scope:
                raise RestoreVerifyError(
                    f"scope {scope_filter!r} has ZERO objects under "
                    f"{bucket}/{prefix}. Scopes that DO have artifacts: "
                    f"{sorted(by_scope)}. A scope selected explicitly and found "
                    f"empty is a scope with no off-machine copy — reporting "
                    f"that as 'nothing to do' is the false all-clear this "
                    f"script exists to prevent.")
            by_scope = {scope_filter: by_scope[scope_filter]}

        verdicts: list[ArtifactVerdict] = []
        failures: list[str] = []
        # 🔴 THE OTHER DIRECTION, and it runs BEFORE the per-artifact loop so it
        # is reported even if every artifact that IS there fails. Skipped when
        # the question was narrowed: with `--scope` you asked about one scope,
        # and with an explicit `--host`/`--prefix` you are asking about ANOTHER
        # machine, whose scope list this host's store does not describe.
        if scope_filter is None and not prefix_was_explicit:
            for msg in uncovered_local_scopes(store, set(by_scope), now,
                                              max_lag_days):
                failures.append(msg)
                print(f"{PROG}: FAILED {msg}", file=sys.stderr)

        for scope in sorted(by_scope):
            entries = by_scope[scope]
            # NOTE: no empty-`entries` guard. `group_by_scope` builds each list
            # by appending, so it cannot produce an empty one from a non-empty
            # key set — a check here could not fail, and a guard that cannot
            # fail reads as coverage while providing none.
            # Lexical order IS chronological order for these keys (the
            # stamp is fixed-width UTC), so the last entry is the newest.
            newest_stamp = entries[-1][0]
            chosen = entries if verify_all else [entries[-1]]
            live = live_scope_path(store, scope)
            for stamp, key in chosen:
                try:
                    v = verify_artifact(
                        downloader, key, scope=scope, stamp=stamp,
                        identity=identity, work_dir=work_dir, store=store,
                        keep=keep_work_dir, now=now, live_scope=live)
                    verdicts.append(v)
                    print(f"{PROG}: {v.line()}")
                except Exception as exc:  # noqa: BLE001 — see backup.py: the point
                    # One artifact failing must not stop the others being
                    # verified — but it MUST make the run fail. The scopes are
                    # independent by construction, and the minio client raises
                    # `S3Error`/urllib3 errors that are not BackupError, so the
                    # isolation has to be as wide as the failures that arrive.
                    label = (str(exc) if isinstance(exc, B.BackupError)
                             else f"{type(exc).__name__}: {exc}")
                    failures.append(f"{scope}: {label}")
                    print(f"{PROG}: FAILED {scope}: {label}", file=sys.stderr)
                    if not isinstance(exc, B.BackupError):
                        traceback.print_exc()

            # 🔴 STALENESS IS JUDGED ON THE NEWEST ARTIFACT ONLY. Under `--all`
            # every older object is verified for restorability, and calling those
            # stale would make the flag permanently red by design.
            age_days = max(0.0, (now - newest_stamp).total_seconds() / 86400.0)
            if age_days > max_lag_days:
                msg = (f"{scope}: STALE ARTIFACT — the newest object under "
                       f"{prefix}{scope}/ is {age_days:.2f} days old "
                       f"(threshold {max_lag_days}). The backup timer runs "
                       f"DAILY, so a bucket that stopped advancing looks "
                       f"identical to a healthy one from every angle except "
                       f"this one.")
                failures.append(msg)
                print(f"{PROG}: FAILED {msg}", file=sys.stderr)
    finally:
        ctx.__exit__(None, None, None)

    if failures:
        raise RestoreVerifyError(
            f"{len(failures)} artifact/scope check(s) FAILED; "
            f"{len(verdicts)} verified. First failure: {failures[0]}")
    if not verdicts:
        # 🔴 NOT REACHABLE TODAY, AND LABELLED AS SUCH — a mutation sweep scored
        # its removal SURVIVED, which is the honest answer: `keys` is non-empty
        # by the guard above, `group_by_scope` cannot produce an empty scope
        # list from non-empty keys, and every artifact either verifies or is
        # recorded as a failure. It is a refusal for a FUTURE refactor that
        # breaks one of those, not measured coverage, and nothing in the test
        # suite should be read as covering it.
        raise RestoreVerifyError(
            f"verified 0 artifact(s) under {bucket}/{prefix} without recording "
            f"a failure. This is a bug in the verifier itself; refusing to "
            f"exit 0.")
    return verdicts


def summarise(verdicts: list[ArtifactVerdict]) -> str:
    """One line that never merges 'verified against live' with 'could not be'."""
    live = [v for v in verdicts if v.cross_checked]
    alone = [v for v in verdicts if not v.cross_checked]
    compared = sum(v.commits_compared for v in live)
    lag = sum(v.lag_commits or 0 for v in live)
    return (f"{PROG}: verified {len(verdicts)} artifact(s): {len(live)} against "
            f"the live store ({compared} commit(s) compared, {lag} commit(s) of "
            f"lag in total), {len(alone)} self-consistency only (no live scope)")


def print_plan(*, bucket: str, prefix: str, store: Path, identity: Path,
               scope_filter: str | None, verify_all: bool,
               max_lag_days: float) -> None:
    """Pure text. Reads the local store; touches neither the network nor the key."""
    prefix = normalise_prefix(prefix)
    # `source`, not `bucket`: with `--from-dir` it is a local directory, and a
    # plan that called a directory a bucket would be describing a run that is
    # not the one about to happen.
    print(f"source:    {bucket}")
    print(f"prefix:    {prefix}")
    print(f"store:     {store}")
    print(f"identity:  {identity} ({'present' if identity.is_file() else 'MISSING'})")
    print(f"scope:     {scope_filter or '(every scope found under the prefix)'}")
    print(f"artifacts: {'ALL per scope' if verify_all else 'newest per scope'}")
    print(f"max lag:   {max_lag_days} day(s) — older than this is a FAILURE")
    # Written from what the code DOES. `git bundle verify` is not in the
    # allowlist at all, and saying so here is the point: this line is what
    # somebody reads when deciding whether to trust the verdict.
    print("verify:    download -> age -d -> `git clone --mirror` -> `git fsck`.")
    print("           NEVER `git bundle verify`: it passes a byte-flipped bundle")
    print("           at rc=0 printing 'complete history'. Only a real restore")
    print("           walks the packfile.")
    print("compare:   restored commits must EXIST in the live scope and each")
    print("           restored tip must be an ANCESTOR of the live tip. Never an")
    print("           equality — the store advances hourly and an equality check")
    print("           would be a permanently-red gate.")
    print("coverage:  every LOCAL scope must have at least one artifact. A scope")
    print("           that stopped being backed up is invisible to every other")
    print("           check here, because they all iterate the objects that ARE")
    print("           in the bucket. Gated on the scope's FIRST COMMIT being")
    print("           older than the max lag, so a scope created today is not a")
    print("           red gate. Skipped under --scope or an explicit --host.")
    print("writes:    NOTHING to the store, NOTHING to the bucket. The store is")
    print("           read through an allowlist of read-only subcommands.")
    scopes = local_scopes(store)
    if not scopes:
        print(f"local:     (no scope repositories under {store})")
        return
    for s in scopes:
        try:
            n = B.commit_count(store / s)
        except B.BackupError:
            n = -1
        print(f"local:     {s} ({n} commits) -> would compare against "
              f"{prefix}{s}/<newest>")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__)
    ap.add_argument("--bucket", default=os.environ.get("ASIB_BUCKET", DEFAULT_BUCKET))
    ap.add_argument("--host", default=None,
                    help="verify the artifacts of this host label (default: this "
                         "host's own, the same value backup.py writes)")
    ap.add_argument("--prefix", default=None,
                    help="the object key prefix to verify under; overrides --host")
    ap.add_argument("--scope", default=None,
                    help="verify only this scope (a scope with no objects FAILS)")
    ap.add_argument("--identity", type=Path, default=None,
                    help=f"age identity file (default: {DEFAULT_IDENTITY}, or "
                         f"ASIB_AGE_IDENTITY / SOPS_AGE_KEY_FILE)")
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE,
                    help="the LIVE store to cross-check against; an absent one "
                         "is the disaster case, not an error")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--latest-only", action="store_true", default=True,
                       help="verify only the newest artifact per scope (default)")
    group.add_argument("--all", dest="verify_all", action="store_true",
                       help="verify EVERY retained artifact per scope")
    ap.add_argument("--max-lag-days", type=float,
                    default=float(os.environ.get("ASIB_MAX_LAG_DAYS",
                                                 DEFAULT_MAX_LAG_DAYS)),
                    help=f"fail if a scope's newest artifact is older than this "
                         f"(default {DEFAULT_MAX_LAG_DAYS})")
    ap.add_argument("--work-dir", type=Path, default=None,
                    help="where artifacts are decrypted and restored, created "
                         "0700 (default: a private temp dir, deleted on exit)")
    ap.add_argument("--keep-work-dir", action="store_true",
                    help="keep the ciphertext and the restored repositories for "
                         "inspection. REQUIRES --work-dir. The decrypted bundle "
                         "is unlinked even so.")
    ap.add_argument("--from-dir", type=Path, default=None,
                    help="verify artifacts already downloaded into this "
                         "directory (keys are paths relative to it) instead of "
                         "reaching MinIO — the step after SECRETS.md's fetch")
    ap.add_argument("--print-plan", action="store_true",
                    help="print what would happen; touch neither network nor key")
    args = ap.parse_args(argv)

    identity = args.identity or B.resolve_identity()
    if args.prefix is not None:
        prefix, explicit = args.prefix, True
    elif args.host is not None:
        prefix, explicit = args.host, True
    else:
        prefix, explicit = B.host_label(), False

    bucket = str(args.from_dir) if args.from_dir is not None else args.bucket
    factory = ((lambda: DirectoryStore(args.from_dir))
               if args.from_dir is not None else None)

    try:
        if args.print_plan:
            print_plan(bucket=bucket, prefix=prefix, store=args.store,
                       identity=identity, scope_filter=args.scope,
                       verify_all=args.verify_all, max_lag_days=args.max_lag_days)
            return 0

        if args.keep_work_dir and args.work_dir is None:
            # An option that silently does nothing is a lie, and this one would
            # lie about where a plaintext-derived repository ended up.
            raise RestoreVerifyError(
                "--keep-work-dir requires --work-dir. Without one the artifacts "
                "are built in a private temp directory that is deleted on exit "
                "whatever this flag says, so the flag would promise to keep "
                "restored copies of client-confidential history and then remove "
                "them — or, worse, be 'fixed' later by leaving the temp dir "
                "behind where nobody looks.")

        def _go(work: Path) -> list[ArtifactVerdict]:
            return run(bucket=bucket, prefix=prefix, store=args.store,
                       identity=identity, scope_filter=args.scope,
                       verify_all=args.verify_all,
                       max_lag_days=args.max_lag_days, work_dir=work,
                       keep_work_dir=args.keep_work_dir,
                       prefix_was_explicit=explicit,
                       downloader_factory=factory)

        if args.work_dir is not None:
            B._private_dir(args.work_dir)
            verdicts = _go(args.work_dir)
        else:
            with tempfile.TemporaryDirectory(prefix="asi-restore-verify.") as td:
                verdicts = _go(Path(td))

        if not verdicts:
            return 0        # the documented never-ran-here no-op; reason on stderr
        print(summarise(verdicts))
        if args.keep_work_dir:
            print(f"{PROG}: --keep-work-dir left restored repositories under "
                  f"{args.work_dir}. They hold PLAINTEXT history of "
                  f"client-confidential scopes; remove them when you are done.",
                  file=sys.stderr)
        return 0
    except B.BackupError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — the failure signal, not a swallow
        # 🔴 NOT EVERY FAILURE IS A BackupError, and the ones that are not are the
        # ones nobody wrote a message for: `S3Error`, a urllib3 connection error,
        # a `FileNotFoundError` for a binary the PATH forgot. The traceback is
        # kept because for these the type and the stack ARE the diagnosis; what
        # is added is the sentence saying this run verified nothing.
        traceback.print_exc()
        print(f"{PROG}: FAILED with an unexpected {type(exc).__name__}: {exc}. "
              f"Nothing was verified on this run, or only part of it was — "
              f"treat this as NO verification and read the traceback above.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
