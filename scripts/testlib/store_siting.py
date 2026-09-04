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
    Half of that is now closed and half is not, and the halves are different in kind:
    a store of OURS growing past what `_MIN_FREE_BYTES` was sized for is caught by
    `_check_store_budget`, which walks the real tree at teardown; a CONCURRENT writer
    filling the mount is not, and cannot be from in here.
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

# tmpfs charges whole 4 KiB pages per file, and directories cost nothing. Both halves
# are load-bearing: an earlier revision of the measurement below double-counted
# directory pages and landed 12,288 B high (1,253,376 + 3*4096 = 1,265,664 exactly).
_PAGE_BYTES = 4096

# The peak page-allocated footprint any store in this suite has been MEASURED to reach.
#
# 🔴 THIS IS A MEASUREMENT, AND IT IS NOT WHAT MAKES THE BUDGET HONEST. Rounds 4-6 tried
# to keep `_LARGEST_STORE_BYTES` truthful by DERIVING it from a syntactic sweep of the
# ledgered test files — counting `write_text` calls and `range(N)` loops. Every revision
# of that sweep was walked through by a shape it could not see: two write loops in one
# test, nested loops summed instead of multiplied (`for a in range(4): for b in
# range(300)` reported 304 for 1,200 real files), `range(303)` respelled as
# `range(0, 303)`, a for-loop rewritten as a list comprehension, a loop body extracted
# into a helper. Each of those under-reported SILENTLY with the whole suite green —
# re-arming the ENOSPC hazard through the guard added to prevent it.
#
# A syntactic sweep can only ever see the shapes its author imagined. So the enforcement
# moved to `_check_store_budget` below, which WALKS THE REAL STORE at teardown and
# raises when it exceeds the budget. That cannot be fooled by a spelling, a nesting or a
# refactor, because it is not reading the source at all.
#
# Measured 2026-09-02 by `store_root` itself over a full run of the three ledgered files
# (`scripts/tests/test_{subsystem_store_api,cairn_write,cairn_cli}.py`): **448 stores**
# walked, largest 1,253,376 B / 306 entries, second largest 176,128 B / 43 entries. The
# peak is `test_cairn_cli.py`'s concurrency fixture — `_populate_source_store`'s 3 seed
# entries + 303 bulk entries = 306 files, apparent 74,613 B — a 16.8x ratio, confirmed
# four independent ways (`sum(st_size)`, `st_blocks*512`, `du -sB1`, and the `/dev/shm`
# statvfs used-delta).
#
# ⚠ SAY WHAT WAS MEASURED, because this constant has been wrong twice by being a
# measurement of something else. The largest store that run saw AT ALL was 1,884,160 B
# / 460 entries — bigger than the number below — and that one is the ledger suite's OWN
# deliberately-over-budget probe (`_OVER_BUDGET_ENTRIES`), not a fixture. 1,253,376 is
# the largest store the suite builds in the course of testing something else, which is
# what this constant is for. The gap to the runner-up (176,128) is 7x, so the peak is
# not a close call.
#
# ⚠ THAT WAS A ONE-OFF, HAND-RUN MEASUREMENT AND NOTHING RE-RUNS IT. There is no
# instrumentation left in this module that records a peak; re-deriving this constant
# means adding a print to `_check_store_budget` and running the three ledgered files
# again. What IS automated is the other direction: `_check_store_budget` fails loudly
# when a real store exceeds the budget below, so this number going stale LOW is caught
# and this number going stale HIGH only wastes headroom.
#
# 🔴 APPARENT BYTES UNDERSTATE TMPFS COST ~17x. Entry COUNT drives this, not text size;
# a floor reasoned from `st_size` lands an order of magnitude low.
_MEASURED_PEAK_STORE_BYTES = 1253376

# Headroom the budget carries over that measured peak.
#
# 🔴 THE PREVIOUS VALUE HAD ZERO SLACK AND THAT MADE IT A TRIPWIRE, NOT A BUDGET.
# `_LARGEST_STORE_BYTES` was 1,875,968 = (442 + 16) * 4096, where 442 was the sweep's
# own output — so the required value and the constant were the same number and the gate
# sat exactly on its own boundary. Appending a single unrelated five-byte scratch write
# to a ledgered file turned the required merge check RED, on a machine with no store,
# no server and no tmpfs involved. Measured base rate: that derived requirement moved
# SIX times in nineteen commits, three of them on one day.
#
# 1.5x is chosen so a fixture can grow by half again — the kind of change an author
# makes without thinking about tmpfs — before anyone has to touch a constant, while
# still leaving `_MIN_FREE_BYTES` (4 MiB) more than 2x above the budget. It is pinned
# by `test_store_siting_ledger.py::test_the_budget_keeps_real_HEADROOM_over_the_
# measured_peak`, so shrinking the slack to buy room is a visible act, not a quiet one.
_BUDGET_HEADROOM = 1.5

# The budget: no ONE store this suite builds may page-allocate more than this.
#
# 🔴 SAY WHICH POPULATION — THIS NUMBER STOPPED BEING A SUM AND NOTHING SAID SO.
# `main`'s predecessor constant was documented as the sum over every ledgered file's
# fixtures, with the note that under `-n 4 --dist loadfile` they do not all hold stores
# at once, i.e. it was deliberately conservative about CONCURRENCY. This value is a
# single measured PEAK x `_BUDGET_HEADROOM`, and `_check_store_budget` enforces it
# PER STORE. Two consequences, both real:
#   * adding a second large fixture no longer moves this number at all. Only a single
#     store growing past the budget does. That is the whole point of the move — the
#     old sum moved six times in nineteen commits on edits that touched no store — but
#     it means this constant is NOT an estimate of what the suite holds in RAM.
#   * the gated tier runs `-n 4 --dist loadfile`, so up to `PYTEST_JOBS` stores can be
#     live on one `/dev/shm` simultaneously, each permitted this much. `_MIN_FREE_BYTES`
#     below is a margin over ONE store, not over four.
# It is not a regression in VALUE today — 1,880,064 slightly exceeds the old sum of
# 1,875,968 — and `tmpfs_dir()` re-reads `statvfs` on EVERY `store_root` entry, so the
# free-space floor is a live check against whatever the other workers have already
# taken rather than a static reservation. What is gone is the modelling, not the check.
#
# 🔴 ENFORCED AT RUNTIME, against the real directory tree, by `_check_store_budget`.
# It is not a floor reasoned from source and it is not checked against another constant:
# every store `store_root` yields is walked at teardown and compared to this number, so
# a fixture that grows past it fails LOUDLY on the test that grew it, whatever spelling
# the growth arrived in.
#
# 🔴 COMPUTED, NOT TRANSCRIBED — and that is not tidiness. Written as the literal
# 1,880,064 it would be a THIRD number that can disagree with the two above it, so
# `_BUDGET_HEADROOM = 1.5` could read 1.5 while the budget carried 1.26x and nothing
# would notice. That is the same defect as the "better than 3x margin" comment one
# constant further down, which was false for a whole round. Rounded UP to a whole page
# because a budget is a page count.
_LARGEST_STORE_BYTES = (
    -(-int(_MEASURED_PEAK_STORE_BYTES * _BUDGET_HEADROOM) // _PAGE_BYTES) * _PAGE_BYTES
)  # 1,880,064 at the two values above — a snapshot, not a second definition

# Free space a candidate must have before we will site a store on it: the budget above,
# with better than 2x margin — over ONE store. 🔴 IT IS NOT A MARGIN OVER A PARALLEL
# RUN, and the budget above says why: `PYTEST_JOBS` xdist workers (4 in the gated
# sandbox) may each hold a store this large at the same moment on one `/dev/shm`.
# What keeps that honest is not this constant but the fact that `tmpfs_dir()`
# consults `statvfs` afresh at every `store_root` entry, so a worker arriving after the
# others have filled the mount sees the reduced free space and falls back to disk.
# 🔴 **2x IS THE CLAIM AND IT IS THE CLAIM A TEST READS**
# (`test_the_free_space_floor_keeps_the_MARGIN_its_comment_claims`). Today the ratio
# happens to be 2.23x (4,194,304 / 1,880,064), and that figure is a SNAPSHOT — do not
# promote it to the guarantee, because a snapshot in this position is precisely what
# went stale last time.
#
# ⚠ The margin used to be stated as "better than 3x" and round 6 falsified that without
# updating the sentence — it was 2.24x by then, and had been 3.18x when written. The
# fix is not a better sentence — it is that the RATIO IS NOW READ BY A TEST.
# `test_the_floor_CONSTANT_clears_the_largest_store_this_suite_builds` in
# `test_subsystem_store_api.py` enforces the bare inequality;
# `test_store_siting_ledger.py::test_the_free_space_floor_keeps_the_MARGIN_its_comment_
# claims` enforces the 2x promised above. A prose margin nothing reads is how the "3x"
# survived being false, and rewording it would have left that mechanism intact.
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


class StoreBudgetExceeded(AssertionError):
    """A store grew past `_LARGEST_STORE_BYTES`, so `_MIN_FREE_BYTES` is now a lie.

    An `AssertionError` on purpose: this is a failed invariant of the test suite, not
    an environment problem, and pytest should report it as a failure rather than an
    internal error.
    """


# 🔴 THERE USED TO BE THREE `PEAK_STORE_*` MODULE GLOBALS HERE AND THEIR COMMENT WAS
# FALSE. It said they were "read by the ledger tests as the positive control that the
# measurement is wired to something"; nothing in this repo read them — `git grep`
# across the whole branch found only the lines that WROTE them. They were deleted
# rather than given a reader, because the reachability claim they pretended to make is
# already made properly, by two tests that were mutation-verified:
#   * `test_store_root_INVOKES_the_budget_check_on_the_root_it_yielded` — the check is
#     actually called on the root `store_root` yielded (a checker that walks nothing
#     reports a reassuring zero, indistinguishable from a suite that builds no stores);
#   * `test_a_store_over_the_budget_RAISES_with_the_budget_checks_own_message` — it can
#     go red, on THIS check's own wording rather than on "something failed".
# A global that a run leaves holding the ledger suite's own over-budget probe would
# have been the wrong number to read anyway — see `_MEASURED_PEAK_STORE_BYTES` above.


def page_allocated_bytes(root: Path) -> tuple[int, int]:
    """`(entries, page-allocated bytes)` for the tree under `root`.

    Files only. Directories are excluded because tmpfs charges nothing for them —
    measured, and an earlier revision of `_LARGEST_STORE_BYTES` was 12,288 B high
    precisely for counting three of them.

    A missing root is `(0, 0)`: a caller may take a root and never create it, and
    that is not a budget violation. An unreadable entry is skipped rather than
    raising — the walk is a measurement, and it must not be the thing that fails a
    test whose store was fine.

    Every file costs AT LEAST one page, including an empty one. That overstates a
    zero-byte file, which tmpfs charges no data pages for, and it is the deliberate
    direction: under-reporting is what re-arms ENOSPC, and these stores hold no empty
    files anyway. `lstat`, not `stat`, so a symlink is measured as the link it is
    rather than followed — the suite plants broken ones on purpose.
    """
    entries = 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root, onerror=lambda _e: None):
        for filename in filenames:
            try:
                size = os.lstat(os.path.join(dirpath, filename)).st_size
            except OSError:
                continue
            entries += 1
            pages = max(1, -(-size // _PAGE_BYTES))
            total += pages * _PAGE_BYTES
    return entries, total


def _check_store_budget(root: Path) -> None:
    """Walk the store that was just used and fail if it broke the budget.

    🔴 THIS IS THE ENFORCEMENT, AND IT REPLACES A SYNTACTIC SWEEP OF THE TEST FILES.
    See `_MEASURED_PEAK_STORE_BYTES` for the six shapes that walked through that
    sweep. Reading the directory rather than the source removes the whole class:
    there is no spelling of a write loop that produces files this cannot see.

    It runs on BOTH branches of `store_root` — tmpfs and the `tmp_path` fallback —
    on purpose. Enforcing only on tmpfs would make the guard structurally blind on
    exactly the machines that have no tmpfs, so a fixture could grow unchecked on a
    developer's box and only fail once it reached a host where it mattered.
    """
    entries, allocated = page_allocated_bytes(root)
    if allocated <= _LARGEST_STORE_BYTES:
        return
    raise StoreBudgetExceeded(
        f"store {root} page-allocates {allocated:,} bytes across {entries:,} entries, "
        f"over the _LARGEST_STORE_BYTES budget of {_LARGEST_STORE_BYTES:,}. tmpfs "
        f"charges whole {_PAGE_BYTES}-byte pages per file, so entry COUNT drives this "
        "and apparent size understates it ~17x. This is the ENOSPC hazard, not a "
        "bookkeeping nit: _MIN_FREE_BYTES "
        f"({_MIN_FREE_BYTES:,}) is what decides whether a tmpfs is accepted, and it "
        "was sized against that budget. Either shrink the fixture, or raise BOTH "
        "constants together and re-check the margin."
    )


@contextlib.contextmanager
def store_root(tmp_path: Path, name: str = "store") -> Generator[Path]:
    """Yield a store root sited off the contended disk when that is possible.

    The returned path does NOT exist yet — callers `mkdir(parents=True)` their own
    scope directories under it, exactly as they did against `tmp_path / "store"`.

    Cleanup is the reason this is a context manager rather than a function: pytest
    auto-cleans `tmp_path`, but a tmpfs directory is not pytest's to clean and tmpfs
    is RAM, so leaking one per test would hold memory for the whole run.

    On the way out it also MEASURES the store against `_LARGEST_STORE_BYTES` — see
    `_check_store_budget`. That check is skipped when the body is already raising:
    a budget violation reported over the top of the real failure would replace the
    error the author needs to read with one about a constant.
    """
    base = tmpfs_dir()
    holder: Path | None = None
    if base is not None:
        try:
            holder = Path(tempfile.mkdtemp(prefix="devrc-store-", dir=str(base)))
        except OSError:
            # The candidate passed every check above and still would not give us a
            # directory (it filled between the probe and here, hit a quota, or went
            # read-only). Falling back reproduces the pre-tmpfs behaviour for THIS
            # call — which is the contract for this branch, and not the absolute
            # "never worse than disk" the module docstring above retracts.
            holder = None
    root = (tmp_path if holder is None else holder) / name
    body_raised = False
    try:
        yield root
    except BaseException:
        body_raised = True
        raise
    finally:
        try:
            if not body_raised:
                _check_store_budget(root)
        finally:
            if holder is not None:
                # Best-effort: a tmpfs leak costs RAM for the run, but raising in
                # teardown would turn a passing test red for a cleanup problem.
                shutil.rmtree(holder, ignore_errors=True)
