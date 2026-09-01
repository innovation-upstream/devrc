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

🔴 EVERY FAILURE MODE FALLS BACK TO THE CALLER'S `tmp_path`, so this can never make a
suite worse than it was. That matters concretely: in the nix build sandbox `TMPDIR` is
`/build` on ext2/ext3 while `/dev/shm` is tmpfs and writable, but CI builds
UNSANDBOXED (its traceback shows `/tmp/nix-build-…`, not `/build`), so there
`/dev/shm` is the container's own mount — 64Mi by default, possibly absent or
read-only. All of those land on the fallback.
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


def mount_fstype(path: Path) -> str | None:
    """The filesystem type backing `path`, from /proc/mounts — or None.

    Resolved by LONGEST matching mount point, not by string prefix: `/dev` and
    `/dev/shm` are both prefixes of a path under the latter, and a first-match-wins
    scan reports `devtmpfs` for what is really a `tmpfs` mount.
    """
    try:
        target = path.resolve()
        entries = Path("/proc/mounts").read_text().splitlines()
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
            if best is None or depth > best[0]:
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
    holder = Path(tempfile.mkdtemp(prefix="devrc-store-", dir=str(base)))
    try:
        yield holder / name
    finally:
        # Best-effort: a tmpfs leak costs RAM for the run, but raising in teardown
        # would turn a passing test red for a cleanup problem.
        shutil.rmtree(holder, ignore_errors=True)
