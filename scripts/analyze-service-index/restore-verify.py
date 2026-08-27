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

🔴 TWO REASONS THERE MAY BE NOTHING TO COMPARE AGAINST, AND NEITHER IS A FAILURE
------------------------------------------------------------------------------
`cross_check_target()` owns both, so they cannot drift apart, and they get
DIFFERENT sentences because one is the disaster case and the other is routine.

  1. THE LIVE SCOPE IS GONE. That is the scenario these backups exist for, and
     failing there would make the verifier useless at the exact moment it is
     needed.
  2. THE ARTIFACTS BELONG TO ANOTHER HOST (`--host` / `--prefix`). The two
     machines' stores are DIVERGENT CONTENT that merely SHARE SCOPE NAMES —
     `devrc` and `homelab-talos` exist on both. Comparing the laptop's `devrc`
     artifact against the workbench's `devrc` repository compares two histories
     that were never the same one, and reports "N of N commits do not exist in
     the live scope — history was rewritten": a false DATA-LOSS alarm,
     permanently red, on the exact command SECRETS.md tells an operator to run.
     Measured; it was invisible only because the other host has no objects in
     the bucket yet, so such a run hit the ZERO-objects refusal first.

Either way the artifact is verified for SELF-CONSISTENCY (restore + `git fsck` +
a non-zero commit count) and reported as `NOT CROSS-CHECKED` WITH ITS REASON,
never folded into the same word as a scope that was checked against live.
"Verified" and "verified against what" are two facts; printing one of them is
how a partial answer gets read as a whole one.

Note the flag is the PREFIX being explicit, not `--scope`: narrowing to one
scope of THIS host's artifacts leaves the comparison perfectly valid. The
coverage check below adds the `--scope` condition on top of the shared one; they
are deliberately not the same boolean.

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
  * THIS host's artifacts, and NOT ONE of them cross-checked because the store
    side gave nothing                          -> failure, exit 40

The last one is the STORE-side twin of the first, and it was exit-0 until it
was measured: `--host <this host> --store <an empty directory>` printed 13
`RESTORED … fsck OK … NOT CROSS-CHECKED … self-consistency only` lines and
rc=0. An exit code is all a timer reads, so a wrong or absent `--store`
downgraded the whole run to "it decrypts and is internally consistent" and
called that success. Foreign artifacts and PARTIAL runs (one scope with no live
repository beside scopes that have one) are outside it by construction — see
`nothing_cross_checked_message`.

🔴 IT HAS ITS OWN EXIT CODE, AND THE REASON IS THE SAME ONE `escrow-verify.py`
GIVES: "every cause has its own exit code, so a timer can act on the number".
`40 NOTHING-CROSS-CHECKED` means *your backups restored perfectly, and nothing
here compared them to anything* — which is the EXPECTED outcome during a real
recovery. `1` means a check FAILED. Collapsing the two would tell an operator
mid-disaster that their backups are broken, and it is the reading that the
absent-live-scope path was originally left at exit 0 to avoid. A real failure
OUTRANKS it: 40 is raised only when it is the whole story, and the finding is
still printed as `ALSO:` when something louder wins. The message lists every
mechanism CONSISTENT with what was observed, states that the list is NOT closed,
and asserts none of them.

(This docstring is `--help`, so it is operator-facing prose. The paragraph above
is whole-string pinned by the test suite for the same reason the refusal itself
is: it carries the claim that 40 is not a verdict against the backups, and that
claim is worth nothing if a reword can silently invert it.)

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
it. Everything created here ends up mode 0600 inside a 0700 directory —
INCLUDING the restored repository, which `git clone` creates at the process
umask (measured: 0755 dirs, 0644 files, a 0444 packfile, 31 group/other-readable
entries) and `_tighten_tree()` repairs before anything else touches it.

`--keep-work-dir` is the one way to keep the restored repositories, and it
REQUIRES `--work-dir` (an option that silently does nothing is a lie). It prints
what it left behind on EVERY path out including the failure paths — which is the
path it exists for, since you keep the work dir precisely BECAUSE something went
wrong and you want to look at it. The decrypted bundle dies even then.

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

# The work directory itself is created 0700 by `B._private_dir()`, which owns
# that number. These two are used by `_tighten_tree()` below, which has to
# repair what `git clone` creates at the process umask.
_DIR_MODE = B._DIR_MODE      # 0700
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


# 🔴 MACHINE-READABLE CAUSES FOR THE DECRYPT STEP. `decrypt()` below has three
# distinct failure branches and they mean COMPLETELY different things to a
# caller — one is an environment fault, one says the identity does not open the
# artifact, and one says the identity DID open it and the artifact holds
# nothing. Every one of them raises `RestoreVerifyError`, so from the outside
# they were separable only by reading the message text.
#
# `escrow-verify.py` has to make exactly that distinction to decide whether a
# failure is a verdict on the ESCROWED KEY or on the ARTIFACT, and getting it
# wrong there reports a tampered backup as "nothing to worry about" or gets a
# working disaster-recovery key rotated. Substring-matching another module's
# prose is the approach that whole feature exists to remove, so the cause is
# published as a value instead — set HERE, by the module that knows.
DECRYPT_AGE_MISSING = "age-missing"          # the binary is not on PATH
DECRYPT_AGE_REFUSED = "age-refused"          # age exited NON-ZERO
DECRYPT_EMPTY_PLAINTEXT = "empty-plaintext"  # age exited ZERO, wrote 0 bytes

DECRYPT_CAUSES = frozenset(
    {DECRYPT_AGE_MISSING, DECRYPT_AGE_REFUSED, DECRYPT_EMPTY_PLAINTEXT})


# --------------------------------------------------------------------------- #
# the classified refusals
# --------------------------------------------------------------------------- #
# 🔴 ONE TOKEN AND ONE EXIT CODE PER DISTINGUISHABLE CAUSE — the convention
# `escrow-verify.py` already runs on, in the same directory, for the same
# reason it states: "Every cause has its own exit code, so a timer can act on
# the number." SECRETS.md documents both tables side by side, and an operator
# reading a number during a recovery must not find two answers, so these start
# at 40 — clear of escrow's classified range.
#
# 🔴 WHY THIS TABLE EXISTS AT ALL: `NOTHING-CROSS-CHECKED` is NOT the same fact
# as a failed verification, and collapsing them into one `1` conflates
#
#     "your backups restored perfectly, I just could not compare them"
#
# with
#
#     "your backups are broken".
#
# Those call for opposite reactions, and the first is the EXPECTED outcome
# during a real recovery — the scenario this whole feature exists for. This
# module already states the principle one screen down ("An UNKNOWN must not be
# summarised as a decision"); the exit code is the only part of the output a
# timer reads, so it is where the principle matters most.
EXIT_OK = 0
# 🔴 1 IS DELIBERATELY TWO THINGS: an unclassified verification failure AND an
# unexpected exception. `escrow-verify.py` gives 1 the same "read the traceback"
# meaning. Splitting it is a separate change with its own blast radius, and
# leaving it alone keeps SECRETS.md's existing "exits 1" statements true.
EXIT_FAILED = 1

NOTHING_CROSS_CHECKED = "NOTHING-CROSS-CHECKED"

EXIT_CODES: dict[str, int] = {
    # This host's artifacts all verified, and NOT ONE of them was compared
    # against a live store. The artifacts are NOT implicated — see
    # `nothing_cross_checked_message`, which is careful to assert neither of the
    # two mechanisms that share this observation.
    NOTHING_CROSS_CHECKED: 40,
}


class RestoreVerifyError(B.BackupError):
    """A failure that must make the whole run non-zero.

    Subclasses `BackupError` so that `main()`'s one handler covers both this
    script's own refusals and the ones raised by the helpers it reuses from
    `backup.py` (`_git_scratch`'s store guard, most importantly). Two exception
    hierarchies over one pipeline is how a failure escapes to a bare traceback.

    `cause` is an OPTIONAL machine-readable tag, currently set only by
    `decrypt()` (see `DECRYPT_CAUSES` above). It defaults to None everywhere
    else, so a caller that reads it gets an explicit "this failure carries no
    published cause" rather than a wrong one — never treat None as a default
    cause.

    🔴 `token` IS OPTIONAL AND THE EXIT CODE IS DERIVED FROM IT, NEVER PASSED
    IN. A constructor taking both could raise a token carrying the wrong code —
    exactly the conflation the table exists to prevent (`EscrowError` makes the
    same choice for the same reason). `token is None` means "this refusal is not
    a distinguishable cause with its own remedy", and it maps to `EXIT_FAILED`.
    """

    def __init__(self, message: str, *, cause: str | None = None,
                 token: str | None = None):
        super().__init__(message)
        if cause is not None and cause not in DECRYPT_CAUSES:
            raise KeyError(
                f"{cause!r} is not a published cause. The set is closed on "
                f"purpose: a consumer branches on these, so inventing one "
                f"silently lands in whatever `else` that consumer has.")
        if token is not None and token not in EXIT_CODES:
            raise KeyError(
                f"{token!r} is not in EXIT_CODES. A classified refusal is an "
                f"ENUMERATED cause with its own exit code and its own remedy; "
                f"adding one means adding it to the table in the same commit as "
                f"the test that pins it.")
        self.cause = cause
        self.token = token
        self.exit_code = EXIT_FAILED if token is None else EXIT_CODES[token]


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
def _identity_note(identity: Path) -> str:
    """" (NOT the default identity...)" when the file is not DEFAULT_IDENTITY.

    🔴 A DECRYPT FAILURE HAS TWO MECHANISMS — wrong key, or damaged ciphertext —
    and the message below names both. A THIRD is invisible without this note:
    the identity was silently redirected by an environment variable, so the
    "wrong key" arm is not a fact about the escrow or the artifact at all.
    Asked of the FILE, never of what named it: the deployed backup unit exports
    SOPS_AGE_KEY_FILE=<the default path>, which redirects nothing.
    """
    if B.same_identity_file(identity, B.DEFAULT_IDENTITY):
        return ""
    # 🔴 NAMES ALL THREE WAYS AN IDENTITY GETS CHOSEN, because this function
    # is given the PATH and cannot know which one was used. Naming only the two
    # environment variables told an operator who had passed `--identity` to go
    # check two variables that had no effect on their run — and escrow-verify's
    # mismatch message now explicitly hands over with "pass it the SAME
    # --identity you used here", so that is the likely path, not a rare one.
    return (" (which is NOT the default identity — it came from --identity, "
            "ASIB_AGE_IDENTITY or SOPS_AGE_KEY_FILE; check which before "
            "concluding the key is wrong)")


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
            "that was never measured.",
            cause=DECRYPT_AGE_MISSING)
    # 🔴 A STALE OUTPUT FILE MAKES age's OWN FAILURE UNREADABLE. MEASURED: on
    # BOTH the wrong-key and damaged-header paths age leaves a PRE-EXISTING
    # `--output` file completely untouched (rc=1, file present, original 34-byte
    # content intact) — it only creates the file once it has authenticated the
    # header. Consumers therefore read the PRESENCE of `plain` as "age got past
    # the header", and a leftover from an aborted earlier run turns that into a
    # lie: a WRONG KEY reads as a corrupt artifact.
    #
    # `work_dir` is routinely REUSED — `B._private_dir` documents the
    # already-exists case as "every run after the first with an explicit
    # --work-dir" — so this is an ordinary sequence, not an exotic one: abort a
    # run mid-decrypt, re-run with the same --work-dir, get a confident wrong
    # answer.
    #
    # One unlink removes the whole class. The same reasoning already guards the
    # throwaway identity in `escrow-verify.py::_create_private_file`; it was
    # applied there and not here, which is a predicate that was right at one of
    # its two sites.
    plain.unlink(missing_ok=True)
    p = subprocess.run(
        ["age", "--decrypt", "--identity", str(identity),
         "--output", str(plain), str(cipher)],
        capture_output=True, text=True)
    if p.returncode != 0:
        raise RestoreVerifyError(
            f"DECRYPT FAILED for {cipher.name} (age rc={p.returncode}): "
            f"{p.stderr.strip()}. The object was retrieved but the identity at "
            f"{identity}{_identity_note(identity)} does not open it, or the ciphertext "
            f"is damaged. This is "
            f"NOT a corruption verdict about the git history inside — nothing "
            f"here has read it.",
            cause=DECRYPT_AGE_REFUSED)
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
            f"with no history.",
            cause=DECRYPT_EMPTY_PLAINTEXT)
    os.chmod(plain, _FILE_MODE)


def _tighten_tree(root: Path) -> None:
    """chmod every directory 0700 and every file 0600 under `root`.

    🔴 `git clone` CREATES THE RESTORED REPOSITORY AT THE PROCESS UMASK, and the
    restored repository is the OTHER complete plaintext copy of a
    client-confidential scope — the decrypted bundle is the first, and it is
    already unlinked immediately. MEASURED under umask 022, on a real restore:

        directories 0755, files 0644, the packfile 0444
        -> 31 entries readable by group and other

    The 0700 work directory bounds all of it today, so this is defence in depth
    rather than a fix for an active leak. It is here because the module
    docstring says "everything created here is mode 0600 inside a 0700
    directory", and a sentence wider than the code is the exact failure this
    file exists to argue against — the honest options were to tighten the modes
    or to narrow the claim, and tightening is the one that leaves the guarantee
    true if the work dir is ever created by something other than
    `_private_dir()`. It also matters under `--keep-work-dir`, where this tree
    outlives the run in a directory the operator chose.
    """
    os.chmod(root, _DIR_MODE)
    for p in root.rglob("*"):
        if p.is_symlink():          # chmod would follow it out of the tree
            continue
        os.chmod(p, _DIR_MODE if p.is_dir() else _FILE_MODE)


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

    # Before ANYTHING else touches it, and before it can outlive the run under
    # `--keep-work-dir`. See `_tighten_tree`'s measurement.
    _tighten_tree(dest)

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
                    live_scope: Path | None,
                    no_live_reason: str | None = None) -> ArtifactVerdict:
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
            # 🔴 The REASON is passed in, not re-derived here. "No live scope on
            # this machine" and "these artifacts are another machine's" are
            # different findings, and an operator reading `NOT CROSS-CHECKED`
            # needs to know which one they are looking at — re-deriving it here
            # could only ever produce the first sentence, including for the
            # second case.
            verdict.no_cross_check_reason = (
                no_live_reason or f"no live scope repository at {store / scope}")
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


# The THREE states a `--store` path can be in, which "no local store" used to
# spell as one. See `store_state`.
STORE_ABSENT = "absent"
STORE_EMPTY = "empty"
STORE_POPULATED = "populated"


def store_state(store: Path) -> tuple[str, list[str]]:
    """`(state, scope names)` for the live store as a WHOLE.

    🔴 "NO LOCAL STORE" WAS THREE DIFFERENT FACTS SHARING ONE SENTENCE, and
    they call for three different actions:

      * ABSENT     — the path does not exist. Either the disaster this whole
        feature exists for, or `--store` points at nothing (a typo, a host where
        the store lives elsewhere). The operator must find out WHICH.
      * EMPTY      — the directory is there and holds no scope repository at
        all. /analyze-service has never run here, or the store was emptied.
      * POPULATED  — scope repositories are there; the one being compared is
        simply not among them. That is a per-scope finding, not a store one, and
        printing "no local store" for it sends the reader to look at the wrong
        thing entirely.

    An EMPTY RESULT CANNOT DISTINGUISH ITS CAUSES (RULES.md); this is the split
    that makes it discriminating, and it is read by both the per-artifact reason
    and the whole-run refusal below.
    """
    if not store.is_dir():
        return STORE_ABSENT, []
    names = local_scopes(store)
    return (STORE_POPULATED, names) if names else (STORE_EMPTY, [])


# 🔴 THE FIVE WAYS A SCOPE CAN HAVE NO LIVE REPOSITORY, AS VALUES. The
# sentences below are for the operator and may be reworded; these are what the
# refusal BRANCHES on, so its explanation can never be a substring match on
# prose — and, more importantly, so it cannot offer a two-way choice that
# EXCLUDES what was actually observed. Measured: a scope that is a SYMLINK, and
# a scope directory with no `.git`, both reached exit 40 being told the store
# held "NO scope repositories" and offered only "--store is wrong" or "the store
# is GONE". Both are false — the repository is right there and `--store` is
# correct; `live_scope_path` refuses symlinks and `discover_scopes` skips them.
WHY_STORE_ABSENT = "store-absent"
WHY_STORE_EMPTY = "store-empty"
WHY_SCOPE_SYMLINK = "scope-symlink"
WHY_SCOPE_NOT_A_REPO = "scope-not-a-repo"
WHY_SCOPE_NOT_PRESENT = "scope-not-present"

WHY_KINDS = frozenset({WHY_STORE_ABSENT, WHY_STORE_EMPTY, WHY_SCOPE_SYMLINK,
                       WHY_SCOPE_NOT_A_REPO, WHY_SCOPE_NOT_PRESENT})


def _why(kind: str, sentence: str) -> tuple[str, str]:
    """Every `why_no_live_scope` return goes through here. Closes the set.

    🔴 A SIXTH BRANCH RETURNING AN INLINE LITERAL WOULD `KeyError` INSIDE THE
    REFUSAL AN OPERATOR IS READING DURING A RECOVERY. `_MECHANISMS` is pinned
    key-for-key against `WHY_KINDS` by the test suite, but that pin says nothing
    about what this function actually RETURNS — a branch spelling its kind
    inline satisfies the ledger test and blows up at the worst possible moment,
    on the disaster path this whole feature exists to serve. The two halves
    together (ledger pinned to WHY_KINDS, returns pinned to WHY_KINDS) are what
    make the `_MECHANISMS[kind]` lookup total.

    It raises rather than degrading: an unknown kind is a programming error with
    no correct operator-facing text, and inventing one would be the confident
    wrong answer this module exists to refuse.
    """
    if kind not in WHY_KINDS:
        raise RestoreVerifyError(
            f"{kind!r} is not in WHY_KINDS. Every store state this verifier can "
            f"observe is an ENUMERATED value with an entry in _MECHANISMS; "
            f"adding one means adding it to both, in the same commit as the "
            f"test that pins them.")
    return kind, sentence


def why_no_live_scope(store: Path, scope: str) -> tuple[str, str]:
    """`(kind, sentence)` for WHICH store state blocked `scope`'s cross-check.

    Never "no local store": that spelling was three facts in one, and the two
    scope-level ones below are a fourth and fifth that a store-level answer
    cannot express at all. Every return goes through `_why`, which closes the
    kind set — see its docstring.
    """
    state, names = store_state(store)
    if state == STORE_ABSENT:
        return _why(WHY_STORE_ABSENT, f"the store directory {store} DOES NOT EXIST")
    p = store / scope
    if p.is_symlink():
        return _why(WHY_SCOPE_SYMLINK, (
            f"{p} is a SYMLINK, and a symlink is never followed here — "
            f"following one would compare against a repository from "
            f"OUTSIDE the store"))
    if p.is_dir() and not (p / ".git").exists():
        return _why(WHY_SCOPE_NOT_A_REPO, (
            f"{p} exists but is not a git repository (it has no .git)"))
    if state == STORE_EMPTY:
        return _why(WHY_STORE_EMPTY, (
            f"the store directory {store} EXISTS but holds NO scope "
            f"repositories"))
    return _why(WHY_SCOPE_NOT_PRESENT, (
        f"the store {store} holds {len(names)} scope "
        f"repositor{'y' if len(names) == 1 else 'ies'} but none named "
        f"{scope!r}"))


# Where `%m` comes from. A module-level tuple so the test suite can point the
# reader at synthetic files and exercise the REAL function, rather than
# re-implementing its shape check in the test — which would only ever prove the
# test agrees with itself.
_MACHINE_ID_FILES = ("/etc/machine-id", "/var/lib/dbus/machine-id")


def machine_id() -> str | None:
    """systemd's `%m` — the token the backup unit stamps into every object key.

    🔴 THIS IS THE ONLY RELIABLE "WHICH MACHINE AM I" SIGNAL HERE, and the
    design already knew it: the unit sets `ASIB_HOST=<name>-%m` precisely
    BECAUSE both hosts are hostname `nixos` and `isLaptop` is a backlight probe
    that fails OPEN, so a readable name alone can collide silently.

    `host_label()` cannot reproduce that label outside the unit — MEASURED: the
    unit writes `workbench-d48f…` while a hand-run of `host_label()` returns
    `nixos`, because `%m` is expanded by systemd and `ASIB_HOST` is unset in an
    interactive shell. So a label comparison alone declares this host's OWN
    artifacts foreign on every hand-run, which is the silent-weakening failure.
    The machine id is exact, needs no systemd, and is the same token already in
    the key.
    """
    for p in _MACHINE_ID_FILES:
        try:
            v = Path(p).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        # \U0001f534 SHAPE-CHECKED, not just non-empty. Returning whatever junk a
        # file happened to hold would make `prefix_belongs_to_this_host` answer
        # True for any prefix containing it — an error in the FALSE DATA-LOSS
        # direction, which is the one that gets someone to act destructively.
        if re.fullmatch(r"[0-9a-f]{32}", v):
            return v
    return None


def prefix_belongs_to_this_host(prefix: str, this_host: str,
                                this_machine_id: str | None) -> bool:
    """Are the artifacts under `prefix` THIS machine's?

    Two independent ways to say yes, because neither alone covers both how the
    verifier gets run:

      * the prefix IS this host's label — true under the unit, where `ASIB_HOST`
        is set, and in the tests;
      * the prefix CONTAINS this machine's id — true for a hand-run, where
        `ASIB_HOST` is unset and `host_label()` degrades to the (shared)
        hostname, but the KEY still carries `%m`.

    🔴 IT FAILS TOWARDS "FOREIGN", and that is deliberate. Being wrong in
    that direction means declining to compare and SAYING SO per artifact; being
    wrong the other way means reporting `history was rewritten` for a healthy
    backup. One is a visible gap in coverage, the other is a false data-loss
    alarm — and only the second gets someone to do something destructive.
    """
    if normalise_prefix(this_host) == normalise_prefix(prefix):
        return True
    return bool(this_machine_id) and this_machine_id in prefix


# 🔴 THE REASON IN MACHINE-READABLE FORM. The prose reason is for the operator
# and is free to be reworded; the KIND is what the whole-run refusal branches
# on, so that predicate can never be a substring match on a sentence. Keyed on
# WHY the comparison was dropped, because the two answers are not the same
# finding and only one of them is a gap in verification:
#
#   * the artifacts are ANOTHER host's        -> suppressing the compare is
#     CORRECT (the two stores are divergent content sharing scope names), so a
#     run with zero cross-checks is a correct run;
#   * this host's own artifacts, and the LOCAL STORE offered nothing -> the
#     comparison was silently dropped, and a run with zero of them exits 0
#     having compared nothing at all. That is the one this refuses.
NO_CROSS_CHECK_FOREIGN_HOST = "foreign-host"
NO_CROSS_CHECK_MACHINE_ID_UNREADABLE = "machine-id-unreadable"
NO_CROSS_CHECK_NO_LIVE_SCOPE = "no-live-scope"

# The kinds that are a fact about THE STORE SIDE — i.e. the artifacts are this
# machine's and the local half contributed nothing. Membership, not a default:
# a kind added later is NOT store-side until someone puts it here on purpose.
STORE_SIDE_BLOCK_KINDS = frozenset({NO_CROSS_CHECK_NO_LIVE_SCOPE})


def cross_check_target(store: Path, scope: str, *, artifacts_are_foreign: bool,
                       machine_id_unreadable: bool = False,
                       ) -> tuple[Path | None, str | None, str | None]:
    """The live repo to compare `scope`'s artifact against, or `(None, reason, kind)`.

    🔴 ONE PLACE OWNS "IS THERE ANYTHING TO COMPARE AGAINST", because the
    predicate used to be open-coded at two sites and was WRONG AT ONE of them —
    the shape RULES.md describes, where a duplicated predicate is wrong at N−1
    sites in the same direction and consolidating is what makes the disagreement
    audible. The coverage check was correctly skipped for another host's
    artifacts; this one was not.

    🔴 FOREIGN ARTIFACTS MUST NOT BE COMPARED TO THIS HOST'S STORE. `backup.py`
    states that the two machines' stores are DIVERGENT CONTENT, and they share
    scope NAMES — `devrc` and `homelab-talos` exist on both. So cross-checking
    the laptop's `devrc` artifact against the workbench's `devrc` repository
    compares two histories that were never the same one, and reports

        CROSS-CHECK FAILED … 3 of 3 commit(s) in the RESTORED artifact do not
        exist in the live scope … history was rewritten

    which is a false DATA-LOSS alarm, permanently red, on the exact command
    SECRETS.md tells an operator to run (`restore-verify.py --host <label>`).
    It is invisible today only because the other host has no objects in the
    bucket yet, so such a run hits the ZERO-objects refusal first — i.e. the bug
    was scheduled to appear the day the feature started working.

    🔴 `artifacts_are_foreign` IS A LABEL COMPARISON, NOT "WAS A FLAG
    TYPED". Keying it on the flag made `--prefix <this host's own label>`
    suppress the cross-check for all 10 real scopes — caught on the first live
    run after that fix, and it is the worse failure of the two, because it makes
    the verification quietly weaker rather than loudly wrong.

    Note it is also not keyed on `--scope`: narrowing to one scope of THIS
    host's artifacts leaves the comparison perfectly valid, so the two call
    sites share this reason and only the coverage check adds the `--scope`
    condition on top. They are deliberately not the same boolean.
    """
    if artifacts_are_foreign:
        # 🔴 TWO WAYS TO BE FOREIGN, AND THEY ARE NOT THE SAME FINDING.
        # `prefix_belongs_to_this_host` returns False both for "this really is
        # another machine" and for "I could not read a machine id and the labels
        # differ" — and the reason string used to assert only the first. On a
        # host where neither /etc/machine-id nor the dbus copy parses, that
        # printed a confident false cause and dropped every cross-check at
        # rc=0: the same conflation B-1 was about, one branch over.
        if machine_id_unreadable:
            return None, (
                "this machine's id could NOT BE READ (neither /etc/machine-id "
                "nor the dbus copy parsed), and the prefix does not match this "
                "host's label — so whether these artifacts are this machine's "
                "is UNKNOWN, not decided. Declining to cross-check rather than "
                "risk a false data-loss alarm; re-run with a matching --host, "
                "or fix the machine id"), NO_CROSS_CHECK_MACHINE_ID_UNREADABLE
        return None, ("the artifacts belong to ANOTHER host (--host/--prefix) "
                      "and this machine's store is DIVERGENT content that only "
                      "shares scope NAMES — comparing them would report a false "
                      "data-loss alarm"), NO_CROSS_CHECK_FOREIGN_HOST
    live = live_scope_path(store, scope)
    if live is None:
        return None, (f"no live scope repository at {store / scope}: "
                      f"{why_no_live_scope(store, scope)[1]}"
                      ), NO_CROSS_CHECK_NO_LIVE_SCOPE
    return live, None, None


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


def sibling_prefixes(all_keys: list[str], exclude: str) -> list[str]:
    """Every `<host>/` prefix the bucket actually holds, except the one searched."""
    out = {k.split("/", 1)[0] + "/" for k in all_keys if "/" in k}
    out.discard(normalise_prefix(exclude))
    return sorted(out)


def diagnose_empty_prefix(*, bucket: str, prefix: str, siblings: list[str],
                          store: Path, this_host: str,
                          this_machine_id: str | None,
                          prefix_was_explicit: bool) -> tuple[bool, str]:
    """`(is_failure, message)` for a prefix that listed ZERO objects.

    🔴 AN EMPTY RESULT CANNOT DISTINGUISH ITS CAUSES — RULES.md names this
    exactly, and the first version of this code walked straight into it. "Zero
    objects here" is the observable shared by *four* mechanisms, and the message
    confidently asserted three of them:

        the backup never ran, stopped running, or its objects were deleted

    while the real cause, every single time on this host, was the fourth: THE
    RUN LOOKED UNDER THE WRONG PREFIX. The unit writes `ASIB_HOST=<name>-%m` and
    systemd expands `%m`; a hand-run has `ASIB_HOST` unset, so `host_label()`
    falls back to the hostname — `nixos` on BOTH machines. So the bare
    no-argument command, which is the first thing anybody types and what a timer
    would run, searches `nixos/` and can never find anything.

    🔴 THE FAILURE MODE IS HUMAN, AND IT IS THE WORST ONE THIS TOOL HAS.
    Someone runs this during an actual disaster, reads "the backup never ran,
    stopped running, or its objects were deleted", and concludes the backups are
    gone — while they sit intact under the next prefix over. So the absence is
    made DISCRIMINATING before anything is asserted: one extra list call, on a
    path that is already failing, turns "nothing here" into "nothing here, and
    here is what IS there".

    🔴 AND THE EXIT-0 VARIANT IS WORSE THAN THE REFUSAL. The permitted
    no-op requires `not local_scopes(store)` — i.e. THE STORE IS GONE, which is
    precisely the disaster this whole feature exists for. Bare command + missing
    store therefore exited **0** with "this host has never run the backup, so
    there is nothing to verify", while every artifact was present. That is the
    false all-clear the module docstring promises to prevent, on the one path
    where it costs the most. `ours` is checked BEFORE the no-op for that reason.
    """
    ours = [p for p in siblings
            if prefix_belongs_to_this_host(p, this_host, this_machine_id)]

    # 🔴 A PREFIX THE OPERATOR TYPED IS A QUESTION, NOT A TYPO — and this
    # round's own fix re-created this round's own bug one branch over. With
    # `ours` checked first, `--host laptop-ffff…` on the workbench answered
    # "you looked in the wrong place, the bucket DOES hold artifacts" and
    # pointed at the WORKBENCH's prefix, because a sibling of ours is always
    # present here. The true and important answer — "the laptop has no
    # off-machine backups at all" — became unreachable, on the exact command
    # SECRETS.md documents for checking the other machine.
    #
    # The "wrong place" diagnosis only makes sense when the prefix was DERIVED
    # rather than STATED. So explicit wins, and the sibling list is folded in as
    # INFORMATION instead of replacing the answer.
    if prefix_was_explicit:
        where = ""
        if ours:
            where = (f" (FYI: the bucket does hold artifacts under {ours}, "
                     f"which carries THIS machine's id — that is this host's "
                     f"own backup, not the one you asked about)")
        elif siblings:
            where = (f" (FYI: the bucket holds artifacts under {siblings}, "
                     f"none carrying this machine's id)")
        return True, (
            f"ZERO objects under {bucket}/{prefix}. The prefix was named "
            f"explicitly, so this is an answer about a host you asked about: "
            f"it has no off-machine backups at all.{where} Refusing to report "
            f"a successful verification of nothing.")

    if ours:
        return True, (
            f"ZERO objects under {bucket}/{prefix} — BUT THE BUCKET DOES HOLD "
            f"ARTIFACTS, under {ours}, and that prefix carries THIS MACHINE'S "
            f"ID. So the backups are almost certainly intact and this run simply "
            f"looked in the wrong place. The backup unit writes "
            f"ASIB_HOST=<name>-%m (systemd expands %m to the machine id); a "
            f"hand-run has ASIB_HOST unset, so the label falls back to the "
            f"hostname — which is the same on both machines. Re-run with "
            f"--host {ours[0].rstrip('/')}. NOTHING HERE SAYS YOUR BACKUPS ARE "
            f"MISSING.")

    # NOTE: `prefix_was_explicit` is handled above and returns, so this branch
    # is reached only for a DERIVED prefix. The explicit half of the old
    # condition is gone rather than left as an unreachable arm.
    if local_scopes(store):
        why = (f"The local store at {store} holds {len(local_scopes(store))} "
               f"scope(s), so /analyze-service HAS run here and there should be "
               f"artifacts.")
        # 🔴 THE CAUSES ARE ONLY ASSERTED WHEN THE BUCKET IS GENUINELY
        # EMPTY. With siblings present the honest statement is what IS there,
        # not a list of three guesses about what is not.
        where = (f" The bucket holds artifacts under other prefixes: "
                 f"{siblings} — none of which carries this machine's id, so "
                 f"this is NOT evidence that THOSE backups are gone."
                 if siblings else
                 f" The bucket holds NO objects under ANY prefix, so this is "
                 f"not a lookup problem: the backup never ran, stopped running, "
                 f"or its objects were deleted.")
        return True, (f"ZERO objects under {bucket}/{prefix}. {why}{where} "
                      f"Refusing to report a successful verification of nothing.")

    where = (f" (the bucket does hold artifacts under {siblings}, none carrying "
             f"this machine's id)" if siblings else "")
    return False, (f"no objects under {bucket}/{prefix} and no local store at "
                   f"{store} — this host has never run the backup, so there is "
                   f"nothing to verify{where}")


# 🔴 ONE MECHANISM PER OBSERVED KIND, AND THE LIST IS OPEN. A CLOSED two-way
# choice is a claim about what CANNOT be true, and it was wrong for two
# reachable states (see `WHY_KINDS`). Each entry says what is CONSISTENT with
# the observation; none says it is THE cause.
#
# Read by `nothing_cross_checked_message`, and the `_MECHANISMS[kind]` lookup is
# TOTAL — which takes BOTH halves, not the one this comment used to claim. The
# test suite pins this table key-for-key against `WHY_KINDS`, AND `_why()`
# refuses at runtime to return a kind outside `WHY_KINDS`. The ledger pin alone
# said nothing about what `why_no_live_scope` actually RETURNS: a sixth branch
# spelling its kind inline satisfied it and would have raised `KeyError` inside
# the refusal an operator reads during a recovery.
_MECHANISMS: dict[str, tuple[str, ...]] = {
    WHY_STORE_ABSENT: (
        "--store names a path that is not this host's store (a typo, a "
        "different layout, a default that does not apply to this run)",
        "the live store is GONE — the disaster these backups exist for"),
    WHY_STORE_EMPTY: (
        "--store names a path that is not this host's store (a typo, a "
        "different layout, a default that does not apply to this run)",
        "the live store is GONE — the disaster these backups exist for"),
    WHY_SCOPE_SYMLINK: (
        "the scope path IS present but is a SYMLINK, which is never followed "
        "here — the repository it points at may be perfectly intact, and "
        "--store may be exactly right",),
    WHY_SCOPE_NOT_A_REPO: (
        "the scope path IS present but carries no .git — an interrupted move, "
        "or a repository that lost it; --store may be exactly right",),
    WHY_SCOPE_NOT_PRESENT: (
        "the scope repositor(y/ies) named below were removed, RENAMED, or "
        "never created in this store",
        "--store names a DIFFERENT store that happens to hold other scopes"),
}


def nothing_cross_checked_message(*, bucket: str, prefix: str, store: Path,
                                  artifacts: int, scopes: list[str],
                                  scope_filter: str | None = None) -> str:
    """The classified refusal for a run of THIS host's artifacts that compared
    NOTHING. Raised as `NOTHING-CROSS-CHECKED` (exit 40), never as a bare 1.

    🔴 THE EXIT-0 VARIANT IS WORSE THAN THE REFUSAL — the same argument
    `diagnose_empty_prefix` already makes for the BUCKET side, made here for the
    STORE side. MEASURED on this host: `restore-verify.py --host <this host>
    --store <an empty directory>` printed 13 lines of `RESTORED N commit(s) …,
    fsck OK … NOT CROSS-CHECKED … self-consistency only`, a summary reading
    `13 self-consistency only`, and **rc=0**. An exit code is the only thing a
    systemd timer reads, so a wrong or absent `--store` silently downgrades the
    run to "the artifact decrypts and is internally consistent" — which proves
    nothing about whether the backup still matches the history it is a backup
    OF — and reports success while doing it.

    🔴 BUT IT MUST NOT ASSERT *WHY*, AND THE FIRST VERSION DID. It ended
    "Point --store at the live store, or fix it" — advice that is unfollowable
    by the one operator who most needs this tool, because in a real disaster
    THERE IS NO LIVE STORE TO POINT AT; that IS the disaster. "You pointed at
    the wrong store" and "the store is genuinely gone" are the SAME OBSERVABLE,
    which is the empty-result trap RULES.md names, and picking the one that
    happened to be true in testing is a coin flip recorded as a diagnosis. So
    the message names BOTH mechanisms, says plainly that this run cannot
    separate them, and gives a remedy that works under either reading.

    🔴 IT ALSO LEADS WITH THE GOOD NEWS, because that is what is actually
    PROVEN: the artifacts decrypted, restored and passed `git fsck`. The
    original design note on the absent-live-scope path — "failing here would
    make the verifier useless at the exact moment it is needed" — is answered by
    this shape, not overruled by it: the per-artifact evidence is unchanged, the
    partial run still exits 0, and the disaster case gets its OWN code rather
    than being folded into "your backups are broken".

    🔴 IT FIRES ON THE OBSERVED PROPERTY, NEVER ON "WAS A FLAG TYPED". The
    v1/v2/v3 note in `run()` and `cross_check_target`'s docstring are both about
    the same mistake made in the other direction; keying this on `--store`
    having been passed would let the DEFAULT store path — the case actually
    measured — sail through.

    🔴 AND IT IS NOT A PERMANENTLY-RED GATE. Three legitimate zero-cross-check
    runs are outside it by construction:
      * FOREIGN artifacts (`--host`/`--prefix` naming the other machine), where
        suppressing the compare is correct and its kind is not store-side;
      * a PARTIAL run — one scope with no live repository beside scopes that
        have one — because a single live target anywhere clears the guard;
      * a host that has never run the backup, which never reaches here (the
        zero-objects no-op returns first, with no verdicts).
    """
    # 🔴 THE OBSERVATIONS AND THE MECHANISMS BOTH COME FROM `why_no_live_scope`,
    # which already knows the real reason per scope. Deriving them from
    # `store_state` alone is what produced a two-way choice that EXCLUDED the
    # truth for a symlinked or non-repository scope.
    # 🔴 ONE ORDER FOR BOTH LISTS, KEYED ON THE KIND. Sorting the SENTENCES
    # instead made the order depend on incidental string content — a path
    # sorting before the word "the" — so the observations and the mechanisms
    # they explain could appear in unrelated orders. Sorting the (kind,
    # sentence) PAIRS groups observations by kind in exactly the order the
    # mechanism list below walks, which is what lets a reader line them up.
    seen = sorted({why_no_live_scope(store, s) for s in scopes})
    observed = [sentence for _, sentence in seen]
    mechanisms: list[str] = []
    for kind in sorted({k for k, _ in seen}):
        mechanisms.extend(_MECHANISMS[kind])
    listed = "; ".join(f"({i}) {m}" for i, m in enumerate(mechanisms, 1))

    # 🔴 THE COUNT IS ATTRIBUTED TO WHAT WAS ACTUALLY VERIFIED, NOT TO THE
    # PREFIX. Under `--scope` only the selected scope ran, so "N artifact(s)
    # under <prefix>" over-claimed — and a fixture holding one object cannot
    # tell the two readings apart, which is why the test now holds several.
    scanned = (f"scope {scope_filter!r} under {bucket}/{prefix}"
               if scope_filter is not None
               else f"every scope under {bucket}/{prefix}")
    return (
        f"{NOTHING_CROSS_CHECKED}: all {artifacts} artifact(s) this run "
        f"verified ({scanned}) DECRYPTED, RESTORED and passed `git fsck`. That "
        f"much is PROVEN, and it is the good news — these objects are readable "
        f"backups. What did NOT happen is any comparison against a live store: "
        f"zero commits were compared, for every one of them. "
        f"WHY IS NOT DECIDED HERE. What was observed is: {'; '.join(observed)}. "
        f"That is consistent with AT LEAST these mechanisms, and the list is "
        f"NOT closed: {listed}. Nothing measured on this run separates them, so "
        f"none of them is asserted. "
        f"Self-consistency proves an object decrypts and restores; it proves "
        f"NOTHING about whether it still matches the history it is a backup OF, "
        f"and that is the claim this run could not make. "
        f"WHAT TO DO: the store this run looked at is {store} — it is named "
        f"above, so no second command is needed to find it. If that is not the "
        f"store you meant, re-run with --store pointing at the one you did. If "
        f"it IS the store you meant, then something on the LIVE side is missing "
        f"or unreachable, which is what these backups are for: this code is the "
        f"EXPECTED outcome during a recovery, not a verdict against the "
        f"backups, and the per-artifact lines above are the evidence that they "
        f"are intact. `restore-verify.py --print-plan` WITH THE SAME FLAGS "
        f"re-prints this run's settings and touches neither the network nor the "
        f"key. Scope(s) with no live repository: {scopes}.")


def run(*, bucket: str, prefix: str, this_host: str,
        this_machine_id: str | None, store: Path, identity: Path,
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
            # 🔴 MAKE THE ABSENCE DISCRIMINATING BEFORE SAYING ANYTHING
            # ABOUT IT. One extra list call, on a path that is already failing,
            # is what separates "the backups are gone" from "you looked under
            # the wrong prefix" — see `diagnose_empty_prefix`. The permitted
            # clean no-op still exists (a host that has never run the backup
            # must not be a permanently-red gate on the laptop), but it no
            # longer swallows the case where THIS machine's artifacts are
            # sitting one prefix over.
            siblings = sibling_prefixes(downloader.list(""), prefix)
            failed, message = diagnose_empty_prefix(
                bucket=bucket, prefix=prefix, siblings=siblings, store=store,
                this_host=this_host, this_machine_id=this_machine_id,
                prefix_was_explicit=prefix_was_explicit)
            if failed:
                raise RestoreVerifyError(message)
            print(f"{PROG}: {message}", file=sys.stderr)
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
        # The classified NOTHING-CROSS-CHECKED finding, if the run produces one.
        # Deliberately NOT an entry in `failures` — see where it is set.
        nothing_cross_checked: str | None = None

        # 🔴 ONE NAME for "these artifacts are not this machine's", read by
        # both the coverage check below and `cross_check_target()` in the loop.
        # The two sites had this open-coded and only one of them got it right;
        # the reason now lives in exactly one docstring.
        #
        # 🔴 IT IS A PROPERTY OF THE PREFIX, NOT "DID YOU TYPE A FLAG" —
        # and it took TWO live runs to get right, each one caught by actually
        # running it rather than by reasoning about it.
        #
        #   v1  `prefix_was_explicit`      -> `--prefix <our own>` suppressed
        #                                     all 10 scopes' cross-checks.
        #   v2  label comparison alone     -> STILL suppressed all 10: the unit
        #                                     writes `workbench-%m` while a
        #                                     hand-run's `host_label()` is
        #                                     `nixos`, so they never match
        #                                     outside systemd.
        #   v3  label OR machine id        -> what ships. See
        #                                     `prefix_belongs_to_this_host`.
        #
        # Both wrong versions failed the SAME way: quietly doing less
        # verification while still exiting 0.
        artifacts_are_foreign = not prefix_belongs_to_this_host(
            prefix, this_host, this_machine_id)

        # 🔴 THE OTHER DIRECTION, and it runs BEFORE the per-artifact loop so it
        # is reported even if every artifact that IS there fails. Skipped when
        # the question was narrowed: with `--scope` you asked about one scope,
        # and with an explicit `--host`/`--prefix` you are asking about ANOTHER
        # machine, whose scope list this host's store does not describe. This
        # site carries the EXTRA `--scope` condition; the foreign-artifact half
        # is shared.
        if scope_filter is None and not artifacts_are_foreign:
            for msg in uncovered_local_scopes(store, set(by_scope), now,
                                              max_lag_days):
                failures.append(msg)
                print(f"{PROG}: FAILED {msg}", file=sys.stderr)

        # Two counters read by the whole-run refusal after the loop. They record
        # what was OBSERVED per scope — never what was typed — so the refusal
        # cannot be argued out of firing by a flag.
        scopes_with_a_live_target = 0
        store_blocked_scopes: list[str] = []

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
            live, no_live_reason, no_live_kind = cross_check_target(
                store, scope, artifacts_are_foreign=artifacts_are_foreign,
                machine_id_unreadable=(this_machine_id is None))
            if live is not None:
                scopes_with_a_live_target += 1
            if no_live_kind in STORE_SIDE_BLOCK_KINDS:
                store_blocked_scopes.append(scope)
            for stamp, key in chosen:
                try:
                    v = verify_artifact(
                        downloader, key, scope=scope, stamp=stamp,
                        identity=identity, work_dir=work_dir, store=store,
                        keep=keep_work_dir, now=now, live_scope=live,
                        no_live_reason=no_live_reason)
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

        # 🔴 THE RUN COMPARED NOTHING AGAINST LIVE, AND THE STORE IS WHY.
        # THREE conjuncts, each excluding a run this must not fire on, and each
        # able to fail on its own; the whole argument is in
        # `nothing_cross_checked_message`.
        #
        #   verdicts                      a run that verified ZERO artifacts has
        #                                 its own, louder finding. Saying "all 0
        #                                 artifacts were self-consistency only"
        #                                 there blames the STORE for what is
        #                                 actually a broken artifact.
        #   scopes_with_a_live_target==0  ONE usable live target anywhere clears
        #                                 this: a partial run, and equally a run
        #                                 whose only comparable artifact was
        #                                 corrupt — the store DID contribute.
        #   store_blocked_scopes          at least one scope was dropped by the
        #                                 STORE side rather than because the
        #                                 artifacts are another host's.
        #
        # 🔴 `not any(v.cross_checked ...)` IS DELIBERATELY ABSENT. It reads
        # like the heart of the check and it is implied by the second conjunct —
        # a verdict can only be cross-checked against a live target, so zero
        # targets means zero cross-checks. A mutation sweep scored flipping it
        # to `not all(...)` SURVIVED, which is the honest answer for a conjunct
        # that cannot fail: it would have read as coverage while providing none.
        if (verdicts
                and scopes_with_a_live_target == 0
                and store_blocked_scopes):
            # 🔴 KEPT OUT OF `failures` ON PURPOSE. It is a DIFFERENT FACT from
            # a verification failure — "your backups restored perfectly, I just
            # could not compare them" versus "your backups are broken" — and it
            # carries its own exit code so a timer can act on the number.
            # Mixing it into `failures` would both mis-count the failures and
            # make it impossible to tell which of the two the run found.
            # 🔴 NOT PRINTED HERE, AND NOT PREFIXED `FAILED`. It was both, which
            # cost twice: the 1.4 KB paragraph went to stderr once here and
            # again from `main()`'s handler, and the first word an operator read
            # was `FAILED` — the exact claim this message exists to retract. It
            # is emitted ONCE below, by whichever path actually reports it.
            nothing_cross_checked = nothing_cross_checked_message(
                bucket=bucket, prefix=prefix, store=store,
                artifacts=len(verdicts), scopes=sorted(store_blocked_scopes),
                scope_filter=scope_filter)
    finally:
        ctx.__exit__(None, None, None)

    # 🔴 A REAL FAILURE OUTRANKS "COULD NOT COMPARE". An artifact that did not
    # restore is the louder finding, and a timer that saw 40 for a corrupt
    # artifact would read "just a store problem" — the same conflation in the
    # other direction. So the classified code is raised ONLY when it is the
    # whole story.
    if failures:
        # The outranked finding is still REPORTED — withholding the CODE must
        # not withhold the fact. `ALSO:`, never `FAILED:`: it is a second
        # finding beside the failure, not a second failure.
        if nothing_cross_checked is not None:
            print(f"{PROG}: ALSO: {nothing_cross_checked}", file=sys.stderr)
        raise RestoreVerifyError(
            f"{len(failures)} artifact/scope check(s) FAILED; "
            f"{len(verdicts)} verified. First failure: {failures[0]}")
    if nothing_cross_checked is not None:
        # Printed by `main()`'s handler, exactly once. See where it is built.
        raise RestoreVerifyError(nothing_cross_checked,
                                 token=NOTHING_CROSS_CHECKED)
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
    line = (f"{PROG}: verified {len(verdicts)} artifact(s): {len(live)} against "
            f"the live store ({compared} commit(s) compared, {lag} commit(s) of "
            f"lag in total), {len(alone)} self-consistency only (NOT "
            f"cross-checked — see the per-artifact reason)")
    # 🔴 An UNKNOWN must not be summarised as a decision. Ten
    # per-artifact reasons scroll past; the summary is what gets read.
    if any("could NOT BE READ" in (v.no_cross_check_reason or "") for v in alone):
        line += ("  ⚠ this machine's id was UNREADABLE, so 'not this "
                 "host's artifacts' is an ASSUMPTION here, not a measurement")
    return line


def print_plan(*, bucket: str, prefix: str, store: Path, identity: Path,
               scope_filter: str | None, verify_all: bool,
               max_lag_days: float, identity_source: str | None = None) -> None:
    """Pure text. Reads the local store; touches neither the network nor the key."""
    prefix = normalise_prefix(prefix)
    # `source`, not `bucket`: with `--from-dir` it is a local directory, and a
    # plan that called a directory a bucket would be describing a run that is
    # not the one about to happen.
    print(f"source:    {bucket}")
    print(f"prefix:    {prefix}")
    print(f"store:     {store}")
    print(f"identity:  {identity} ({'present' if identity.is_file() else 'MISSING'})")
    # Every age identity file is the same size, so the line above cannot tell a
    # redirected key from the right one. Asked of the FILE, never of what named
    # it: the deployed unit exports SOPS_AGE_KEY_FILE=<the default>.
    if identity_source is not None:
        note = ("" if B.same_identity_file(identity, B.DEFAULT_IDENTITY)
                else "  <- NOT the default identity")
        print(f"chosen by: {identity_source}{note}")
    mid = machine_id()
    ours = prefix_belongs_to_this_host(prefix, B.host_label(), mid)
    print(f"host:      {B.host_label()} (machine-id "
          f"{mid or 'UNREADABLE'}) -> these artifacts are "
          f"{'THIS host\'s: they WILL be cross-checked' if ours else
             'ANOTHER host\'s: NOT cross-checked, self-consistency only'}")
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
    # 🔴 THIS PARAGRAPH SAID THE RUN "FAILS", TWELVE LINES ABOVE THE BLOCK
    # SAYING 40 IS NOT A VERDICT AGAINST THE BACKUPS — and the refusal's own
    # remedy routes a recovering operator to this screen, so the retracted claim
    # was the first thing they read. Whole-string pinned by
    # `test_print_plan_PROSE_never_contradicts_the_FORTY_framing`.
    print("           A run of THIS host's artifacts in which NOT ONE scope could")
    print("           be compared exits 40, NOT 0: self-consistency alone says")
    print("           the object decrypts, never that it still matches the")
    print("           history it backs up, so 0 would claim what was not")
    print("           measured. 40 is NOT a verdict against the backups — see")
    print("           `exit:` below. Another host's artifacts are exempt (their")
    print("           comparison is suppressed on purpose), and so is a PARTIAL")
    print("           run: both stay 0.")
    # 🔴 EVERY CLASSIFIED CAUSE IS LISTED, so an operator can map a number by
    # eye during a recovery — the same reason `escrow-verify.py --print-plan`
    # prints its table. `--print-plan` touches neither the network nor the key,
    # which is exactly why the refusal's remedy sends people here.
    print(f"exit:      {EXIT_OK}=verified, {EXIT_FAILED}=a check FAILED or an "
          f"unexpected error, plus one code per classified cause:")
    for token, code in sorted(EXIT_CODES.items(), key=lambda kv: kv[1]):
        print(f"           {code}={token}")
    print(f"           {EXIT_CODES[NOTHING_CROSS_CHECKED]} is NOT a verdict "
          f"against the backups: every artifact restored and")
    print("           passed fsck, and NONE could be compared to a live store.")
    print("           It is the EXPECTED code during a real recovery.")
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
    # 🔴 `--latest-only` NAMES THE DEFAULT and does not otherwise drive anything
    # — `verify_all` is what the run reads. Its one real behaviour is being in
    # this mutually-exclusive group, so `--latest-only --all` is an ERROR rather
    # than a silent win for whichever argparse saw last. `default=False` (not
    # True) so the parsed value reflects whether the operator actually typed it;
    # it read `True` unconditionally before, which is a value that answers no
    # question. Pinned by test_latest_only_and_all_CANNOT_be_combined.
    group.add_argument("--latest-only", action="store_true", default=False,
                       help="verify only the newest artifact per scope (the "
                            "default; explicit spelling, and refuses --all)")
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

    # 🔴 RESOLVE THE PATH AND WHAT CHOSE IT TOGETHER — the same seam
    # escrow-verify.py closes. This tool is where escrow-verify's mismatch
    # message SENDS the operator ("restore-verify.py answers that"), so if it
    # silently resolves its own identity through the same env vars, the
    # handover lands on the redirected key too and the second opinion is a
    # second sample of the first mistake.
    if args.identity is not None:
        identity, identity_source = args.identity, "the --identity flag"
    else:
        identity, identity_source = B.resolve_identity_with_source()
    if args.prefix is not None:
        prefix, explicit = args.prefix, True
    elif args.host is not None:
        prefix, explicit = args.host, True
    else:
        prefix, explicit = B.host_label(), False

    bucket = str(args.from_dir) if args.from_dir is not None else args.bucket
    factory = ((lambda: DirectoryStore(args.from_dir))
               if args.from_dir is not None else None)

    # None until a work dir has actually been created AND --keep-work-dir asked
    # for it to survive. Neither --print-plan nor the --keep-work-dir refusal
    # leaves anything behind, so neither may warn about it.
    kept_work_dir: Path | None = None

    try:
        if args.print_plan:
            print_plan(bucket=bucket, prefix=prefix, store=args.store,
                       identity_source=identity_source,
                       identity=identity, scope_filter=args.scope,
                       verify_all=args.verify_all, max_lag_days=args.max_lag_days)
            return EXIT_OK

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
            return run(bucket=bucket, prefix=prefix, this_host=B.host_label(),
                       this_machine_id=machine_id(),
                       store=args.store, identity=identity,
                       scope_filter=args.scope, verify_all=args.verify_all,
                       max_lag_days=args.max_lag_days, work_dir=work,
                       keep_work_dir=args.keep_work_dir,
                       prefix_was_explicit=explicit,
                       downloader_factory=factory)

        if args.work_dir is not None:
            B._private_dir(args.work_dir)
            # 🔴 ARMED BEFORE THE RUN, WARNED IN A `finally`. This warning used
            # to print after `summarise()` — the SUCCESS PATH ONLY — so a run
            # that failed left restored plaintext history on disk and said
            # nothing, which is precisely the path `--keep-work-dir` exists for:
            # you keep the work dir BECAUSE something went wrong and you want to
            # look at it. The module docstring claimed the flag "prints what it
            # left behind"; on the path people actually use it, that was false.
            kept_work_dir = args.work_dir if args.keep_work_dir else None
            verdicts = _go(args.work_dir)
        else:
            with tempfile.TemporaryDirectory(prefix="asi-restore-verify.") as td:
                verdicts = _go(Path(td))

        if not verdicts:
            return EXIT_OK  # the documented never-ran-here no-op; reason on stderr
        print(summarise(verdicts))
        return EXIT_OK
    except RestoreVerifyError as exc:
        # 🔴 THE CODE COMES OFF THE EXCEPTION, DERIVED FROM ITS TOKEN. A
        # classified refusal keeps its own number; an unclassified one is
        # EXIT_FAILED, which is what every raise here used to be. Ordered before
        # the `BackupError` arm below because this subclasses it — reversed, the
        # classified codes would be unreachable and 40 would never be seen.
        print(f"{PROG}: {exc}", file=sys.stderr)
        return exc.exit_code
    except B.BackupError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return EXIT_FAILED
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
        return EXIT_FAILED
    finally:
        # EVERY path out: success, BackupError, and an unexpected exception. A
        # failing run is the one most likely to have left a partly-restored
        # repository behind, and the least likely to have anyone re-reading the
        # flag's documentation to find out.
        # 🔴 WARN ABOUT WHAT IS ACTUALLY THERE, NOT ABOUT WHAT THE FLAG
        # COULD HAVE LEFT. Armed-before-the-run fixed the silence on failures
        # and introduced the mirror defect: the warning fired unconditionally,
        # so a clean refusal with `--work-dir W --keep-work-dir` announced
        # "decrypted artifacts and restored repositories" holding "PLAINTEXT
        # history" while W was EMPTY — and on the corrupted-artifact path W
        # holds one 0600 ciphertext and NO restored repository, so "decrypted
        # artifacts" misnamed the one thing this file is emphatic never
        # survives. A test that asserts only that the string appears certifies
        # a message that is false on the run it tests.
        if kept_work_dir is not None:
            kept = sorted(p.name for p in kept_work_dir.iterdir()) \
                if kept_work_dir.is_dir() else []
            if kept:
                repos = [n for n in kept if n.endswith(".restored.git")]
                ciphers = [n for n in kept if n.endswith(".bundle.age")]
                what = []
                if repos:
                    what.append(f"{len(repos)} restored repositor"
                                f"{'y' if len(repos) == 1 else 'ies'} "
                                f"(PLAINTEXT history)")
                if ciphers:
                    what.append(f"{len(ciphers)} age ciphertext(s)")
                other = [n for n in kept if n not in repos and n not in ciphers]
                if other:
                    what.append(f"{len(other)} other file(s): {other}")
                print(f"{PROG}: --keep-work-dir left {', '.join(what)} under "
                      f"{kept_work_dir}. Remove them when you are done.",
                      file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
