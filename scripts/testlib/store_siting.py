"""Where a test's store root lives — tmpfs when one is usable, disk otherwise.

🔴 THIS EXISTS BECAUSE THE PREDICATE WAS OPEN-CODED AND THE FIX REACHED ONE SITE.

`server.py:_replace_bytes` fsyncs the file and then the parent directory INSIDE the
request, before the response is written, and fsync blocks in uninterruptible sleep.
Under disk contention on the single node `devrc-ci` is pinned to, one fsync exceeds
the client's `HANG_TIMEOUT` and the gate reports a CODE failure for an I/O stall — on
PRs whose diff cannot reach the test at all. Mechanism and reproducer:
`scripts/ci-repro/README.md`.

devrc#1211 fixed that by siting the store on tmpfs, where there is no backing device
to contend for — but it fixed `test_subsystem_store_api.py` ONLY. The very next PR
gated after it merged went red on `TestAppendLands` in `test_cairn_write.py`, which
open-codes its own disk-backed `store(tmp_path)` against the same `build_server`. A
predicate copied per call site is wrong at every site it was not copied to; this
module is the single place, and `test_store_siting_ledger.py` is what stops the set
of call sites drifting again.

Measured 2026-09-01, replaying `_replace_bytes`'s sequence (mkstemp → write → fsync
file → `os.replace` → fsync dir), reporting MAX because the bound is breached by a
single worst-case call and a mean would hide it:

             idle                          under 3 concurrent fsync writers
  disk    median 6.562ms  MAX 12.431ms     median 11.725ms  MAX 17.843ms
  tmpfs   median 0.017ms  MAX  0.140ms     median  0.011ms  MAX  0.090ms

Disk doubled under a deliberately modest load; tmpfs did not move. That load was
nowhere near CI's and no 60s stall was reproduced — the claim is that tmpfs is FLAT
under contention, not that this measured the CI event.

🔴 EVERY FAILURE MODE **THIS MODULE CAN DETECT** FALLS BACK TO THE CALLER'S
`tmp_path`: not a directory, not tmpfs, under `_MIN_FREE_BYTES` free, unwritable, or
`mkdtemp` refusing. That matters concretely: in the nix build sandbox `TMPDIR` is
`/build` on ext2/ext3 while `/dev/shm` is tmpfs and writable, but CI builds
UNSANDBOXED (its traceback shows `/tmp/nix-build-…`, not `/build`), so there
`/dev/shm` is the container's own mount — 64Mi by default, possibly absent or
read-only. All of those land on the fallback.

⚠ **It is NOT unconditionally "never worse than disk", and an earlier draft of this
docstring claimed that.** Two residual windows, both real and both narrower than the
free-space floor above rather than closed by it:
  * a tmpfs that passes the checks and then FILLS mid-run — a concurrent writer, or a
    suite far larger than these stores — surfaces ENOSPC where disk would have passed.
  * a run killed by SIGKILL (a gate timeout, `panic: test timed out`) skips the
    `finally`, so `/dev/shm/devrc-store-*` survives; on a persistent container
    `/dev/shm` repeated kills accumulate toward the first case. Nothing reaps them.
Say which of these you have ruled out before restating the guarantee.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path

# Consulted in order. The env var exists so a test can point the siting at a
# directory it controls — including a deliberately disk-backed one, which is how
# the rejection path is exercised.
_CANDIDATE_ENV = "DEVRC_TEST_TMPFS"
_DEFAULT_CANDIDATE = "/dev/shm"

# The mounts table, as a SEAM. It is a module attribute rather than a literal
# inside `mount_fstype` because the shadowed-mount case that `>=` fixes cannot be
# reached hermetically otherwise — the only alternative is `unshare -Urm`, which a
# test suite cannot rely on. Audit round 2 found all three of this round's fixes
# SURVIVING mutation precisely because none of them had a test; this is what lets
# one exist.
_MOUNTS_PATH = "/proc/mounts"

# The largest store this suite builds, in tmpfs PAGE-ALLOCATED bytes. Measured
# 2026-09-01 by reproducing the 305-entry fixture of
# `test_cairn_cli.py::TestConcurrentSync::test_ten_concurrent_syncs_never_leave_a_SHORT_cache`:
# apparent 53,985 B, page-allocated **1,257,472 B (1.199 MiB)**.
#
# 🔴 APPARENT BYTES UNDERSTATE THIS BY ~23x, and that is what makes the floor easy to
# set wrong. tmpfs charges whole 4 KiB pages, so 305 small entries cost 1.2 MiB of
# tmpfs however little text they contain. A floor reasoned from file sizes lands an
# order of magnitude low.
_LARGEST_STORE_BYTES = 1_257_472

# Free space a candidate must have before we will site a store on it: the measured
# peak above, with better than 3x margin.
#
# 🔴 THIS WAS 1 MiB FOR EXACTLY ONE COMMIT AND THAT WAS A REGRESSION — BELOW the peak.
# It was lowered from 8 MiB on the strength of an observation that 8 MiB rejects a
# usable 4 MiB tmpfs, WITHOUT measuring what the stores actually write. Reproduced
# under `unshare -Urm` with /dev/shm at 1200k and the fallback candidate shadowed:
# the 1 MiB floor accepted the mount and the run died `OSError: [Errno 28]`, while
# the 8 MiB floor refused it and passed. The hazard window was 1 MiB <= free < ~1.3 MiB.
# 🔴 Both directions are real and they are NOT symmetric: too LOW is an ENOSPC error
# on a test that would have passed on disk; too HIGH only makes the fix inert (falls
# back to disk, i.e. today's behaviour). Prefer too high, and pin the value —
# `test_the_floor_CONSTANT_clears_the_largest_store_this_suite_builds` does that
# directly rather than through a call whose own monkeypatch would mask it.
_MIN_FREE_BYTES = 4 * 1024 * 1024


def mount_fstype(path: Path) -> str | None:
    """The filesystem type backing `path`, from /proc/mounts — or None.

    Resolved by LONGEST matching mount point, not by string prefix: `/dev` and
    `/dev/shm` are both prefixes of a path under the latter, and a first-match-wins
    scan reports `devtmpfs` for what is really a `tmpfs` mount.
    """
    try:
        target = path.resolve()
        entries = Path(_MOUNTS_PATH).read_text().splitlines()
    except OSError:
        return None
    best: tuple[int, str] | None = None
    for line in entries:
        parts = line.split()
        if len(parts) < 3:
            continue
        mount_point, fstype = parts[1], parts[2]
        try:
            mp = Path(mount_point).resolve()
        except OSError:
            continue
        if target == mp or mp in target.parents:
            depth = len(mp.parts)
            # 🔴 `>=`, NOT `>`. /proc/mounts is LAST-WINS for a shadowed mount point:
            # when two mounts share one path the later line is the live one. With `>`
            # the FIRST entry at a given depth won, so a disk-backed bind mounted over
            # a tmpfs reported `tmpfs` and the store silently landed on disk — the
            # exact hazard `tmpfs_dir`'s docstring claims to prevent, arriving through
            # the check meant to prevent it. Measured under `unshare -Urm`: tmpfs at
            # /dev/shm, then an ext4 bind over it, `st_dev(store) == st_dev(/tmp)`.
            if best is None or depth >= best[0]:
                best = (depth, fstype)
    return None if best is None else best[1]


def tmpfs_dir() -> Path | None:
    """A writable tmpfs directory, or None when the caller should use disk.

    Three conditions, all required, because each fails independently in a real
    container: it is a directory, /proc/mounts says the filesystem backing it is
    `tmpfs`, and an actual write succeeds. 🔴 The PATH is never the test —
    `/dev/shm` is tmpfs by convention only and a container may mount anything
    there, so trusting the name would be a guard that passes while the hazard (a
    disk-backed store) is present under a different spelling.
    """
    for candidate in (os.environ.get(_CANDIDATE_ENV), _DEFAULT_CANDIDATE):
        if not candidate:
            continue
        path = Path(candidate)
        try:
            if not path.is_dir():
                continue
            if mount_fstype(path) != "tmpfs":
                continue
            # 🔴 HEADROOM, NOT JUST WRITABILITY. A five-byte probe succeeds on a
            # tmpfs with five bytes free, and the caller's real writes then raise
            # ENOSPC — turning a test that would have PASSED on disk into an error,
            # which is precisely the "can never make a suite worse" guarantee this
            # module claims. Measured under `unshare -Urm` with /dev/shm sized 64k
            # and 4096 bytes free: the probe passed and the store writes died
            # `OSError: [Errno 28] No space left on device`.
            # A container's default /dev/shm is 64Mi, so this floor keeps that case
            # usable while rejecting a genuinely exhausted one.
            stat = os.statvfs(path)
            if stat.f_bavail * stat.f_frsize < _MIN_FREE_BYTES:
                continue
            probe = path / f".devrc-tmpfs-probe-{os.getpid()}"
            probe.write_bytes(b"probe")
            probe.unlink()
        except OSError:
            continue
        return path
    return None


@contextlib.contextmanager
def store_root(tmp_path: Path, name: str = "store") -> Generator[Path]:
    """Yield a store root sited off the contended disk when that is possible.

    The returned path does NOT exist yet — callers `mkdir(parents=True)` their own
    scope directories under it, exactly as they did against `tmp_path / "store"`.

    Cleanup is the reason this is a context manager rather than a function: pytest
    auto-cleans `tmp_path`, but a tmpfs directory is not pytest's to clean and tmpfs
    is RAM, so leaking one per test would hold memory for the whole run.
    """
    base = tmpfs_dir()
    if base is None:
        yield tmp_path / name
        return
    try:
        holder = Path(tempfile.mkdtemp(prefix="devrc-store-", dir=str(base)))
    except OSError:
        # The candidate passed every check above and still would not give us a
        # directory (it filled between the probe and here, hit a quota, or went
        # read-only). Falling back reproduces the pre-tmpfs behaviour for THIS
        # call — which is the contract for this branch, and not the absolute
        # "never worse than disk" the module docstring above retracts.
        yield tmp_path / name
        return
    try:
        yield holder / name
    finally:
        # Best-effort: a tmpfs leak costs RAM for the run, but raising in teardown
        # would turn a passing test red for a cleanup problem.
        shutil.rmtree(holder, ignore_errors=True)
