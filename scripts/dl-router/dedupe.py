"""Content-identity for the dedupe check: a BOUNDED partial hash, and a cache.

WHY THIS EXISTS. The original dedupe matched on the normalised filename stem.
Against this traffic that is a dead signal — the filenames are random per
download — and it fired zero times in seventeen real downloads while the
library held groups of same-size files it structurally could not see.

THE SHAPE OF THE ANSWER, and the reason it is cheap:

  1. EXACT BYTE SIZE first. `FileIndex` already stats every file during the
     walk it already performs, so a size bucket costs one dict lookup and no
     I/O at all. On the common path (no other file of that size) the answer is
     "not a duplicate" and nothing here runs.
  2. ONLY when sizes collide, a bounded digest of both files: the first and
     last 128 KiB plus eight 128 KiB samples at mid-file offsets derived from
     the size. ~1.25 MiB, constant regardless of how large the files are.

SAMPLING IS A WARNING. IT IS NOT A PROOF, AND IT MUST NEVER GATE A DELETE.
That distinction was learned the hard way, twice:

  * head+tail alone could not tell a finished file from a PREALLOCATED torrent
    payload. qBittorrent's `posix_fallocate` reserves real extents, so the
    partial has the same size AND the same `st_blocks`, and under "first and
    last pieces first" the same head and tail. The middle is the only place
    the difference lives, which is why the mid samples exist -- an unfilled
    extent reads as zeros (see `looks_unfilled`).
  * but even with mid samples, no bounded read PROVES two multi-GB files
    identical. Eight samples catch a 40%-complete payload with overwhelming
    probability and a 99%-complete one only about 8% of the time. It fails to
    disprove; it never proves.

So the two paths are deliberately different, and the difference is the point:

    /dedupe  (a warning, on a completion path)  -> sample_file, bounded
    /discard (destroys a file, user-initiated)  -> files_identical, FULL read

The full comparison is affordable exactly because /discard is not /match: it is
rare, the user asked for it, and nothing is waiting on a 400 ms budget. A
bounded read on that path was a proof that was not one.

NOTHING HERE IMPORTS FROM matcher/dirindex/server. It is pure filesystem
identity, so it can be exercised against real temp files with no index, no
store and no sidecar.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time

# 128 KiB from each end. Big enough to cover a container header plus the first
# frames (and the trailing index/moov atom that a remux would change), small
# enough that hashing eight candidates is ~2 MiB of reads.
HEAD_BYTES = 128 * 1024
TAIL_BYTES = 128 * 1024

# MID-FILE SAMPLES, and why head+tail alone was not enough.
#
# qBittorrent preallocates. With "pre-allocate disk space for all files" that
# is `posix_fallocate`, which reserves REAL EXTENTS -- so the partial file has
# the final size AND the final block count, and `st_blocks` says nothing. Under
# "download first and last pieces first" its head and tail are the finished
# bytes. A head+tail digest of a 40%-complete payload is therefore
# byte-identical to the finished file, and no amount of `stat` can tell them
# apart: identical size, identical blocks, identical ends.
#
# The information is only in the middle, so the middle has to be read. Eight
# 128 KiB samples at offsets derived from the SIZE (so two files of the same
# length always sample the same places) costs ~1 MiB on top of the ends and
# still does not depend on the file's length.
MID_SAMPLES = 8
SAMPLE_BYTES = 128 * 1024

# The ceiling this module will ever read for a DIGEST: ~1.25 MiB.
MAX_DIGEST_BYTES = HEAD_BYTES + TAIL_BYTES + (MID_SAMPLES * SAMPLE_BYTES)

# How much a full verification reads at a time.
COMPARE_CHUNK = 1024 * 1024

# How many same-size candidates a single check will hash. A size bucket can be
# large (a library accumulates same-size files), and the check runs on a
# download-completion path: an unbounded bucket would turn one completed
# download into an unbounded number of file reads. Target-directory candidates
# are ordered first, so the cap drops the least likely ones.
MAX_DUP_CANDIDATES = 8

# Bounded so a long-lived sidecar cannot accumulate a digest per library file.
# FIFO eviction: the entries a dedupe check re-reads are the ones it just
# inserted, so recency is the right thing to keep.
MAX_CACHE_ENTRIES = 4096


def _feed(fh, count: int, digest) -> int:
    """Read exactly `count` bytes (or to EOF) into `digest`."""
    read = 0
    while read < count:
        chunk = fh.read(min(65536, count - read))
        if not chunk:
            break
        digest.update(chunk)
        read += len(chunk)
    return read


def mid_windows(size: int, *, head: int = HEAD_BYTES, tail: int = TAIL_BYTES,
                samples: int = MID_SAMPLES,
                sample_bytes: int = SAMPLE_BYTES) -> list:
    """`[(offset, length), ...]` for the mid-file samples. NON-OVERLAPPING.

    Derived from `size` ALONE so two files of the same length always sample
    exactly the same places — otherwise comparing their digests would be
    meaningless.

    Two shapes, because a fixed window count does not fit every file:

      * a mid region no bigger than the whole sample budget is read ENTIRELY
        (one window). Slicing it into eight 128 KiB windows would have them
        overlap each other and run into the tail, feeding the same bytes to
        the digest several times — measured at 327680 bytes, where the digest
        read 1.25 MiB out of a 320 KiB file.
      * anything larger gets `samples` evenly spaced windows, clipped so none
        crosses into the tail window.

    Either way the total is at most `samples * sample_bytes`, and no byte is
    ever fed twice.
    """
    start, end = int(head), int(size) - int(tail)
    span = end - start
    samples, sample_bytes = int(samples), int(sample_bytes)
    if span <= 0 or samples <= 0 or sample_bytes <= 0:
        return []
    budget = samples * sample_bytes
    if span <= budget:
        return [(start, span)]
    out = []
    # `samples + 1` intervals so no window sits exactly on either boundary.
    for i in range(1, samples + 1):
        off = start + (span * i) // (samples + 1)
        off = min(off, end - sample_bytes)
        if off < start:
            off = start
        if out and off < out[-1][0] + out[-1][1]:
            continue                      # would overlap its predecessor
        out.append((off, min(sample_bytes, end - off)))
    return out


def mid_offsets(size: int, **kw) -> list:
    """Just the offsets from `mid_windows`."""
    return [off for off, _ in mid_windows(size, **kw)]


def _read_bounded(fh, size: int, head: int, tail: int, digest) -> int:
    """Feed at most `head + tail` bytes into `digest`. Returns bytes read.

    THE TAIL START IS CLAMPED TO THE END OF THE HEAD WINDOW, and that is not a
    detail. The first version guarded the tail read with `size > head + tail`,
    which meant a file LARGER than the head window but no larger than both
    windows combined had its tail silently never read: everything past
    `head` bytes was unhashed, so two files differing only in their final byte
    digested identically. Measured, at 129 KiB, 192 KiB and 256 KiB.

    That is a false negative in the direction that matters -- it is the
    confirmation that gates `/discard` -- and three places documented the
    opposite. Clamping instead of skipping means the two windows meet rather
    than overlap: for a file between `head` and `head + tail` the whole file is
    read (which is still at most `head + tail` bytes), and above that the
    middle is skipped exactly as intended.
    """
    read = _feed(fh, min(size, head), digest)
    # Where the tail window starts, never before the head window ended -- so
    # no byte is fed twice and no byte between them is missed.
    tail_start = max(head, size - tail)
    if size > tail_start:
        # Seek rather than read through the middle: this is the whole point.
        fh.seek(tail_start, os.SEEK_SET)
        read += _feed(fh, size - tail_start, digest)
    return read


def sample_file(path, *, size=None, head: int = HEAD_BYTES,
                tail: int = TAIL_BYTES, samples: int = MID_SAMPLES,
                sample_bytes: int = SAMPLE_BYTES, opener=open):
    """Bounded fingerprint PLUS what the samples looked like. None on failure.

    Returns `{digest, size, mid_samples, zero_samples, bytes_read}`.

    `zero_samples` is the load-bearing extra. An unfilled extent reads as
    zeros, so an all-zero mid sample in a media file is DIRECT evidence that
    the file is not finished — not a probabilistic hint. A finished video file
    does not contain a 128 KiB run of zeros in the middle; a preallocated
    torrent that has not reached that piece does, always.
    """
    try:
        if size is None:
            size = os.stat(path).st_size
        size = int(size)
    except (OSError, TypeError, ValueError):
        return None
    if size <= 0:
        return None
    head, tail = int(head), int(tail)
    # The size is folded in FIRST, so two files whose sampled windows agree but
    # whose lengths differ can never collide even if a caller compares digests
    # across size buckets.
    digest = hashlib.blake2b(str(size).encode("ascii"), digest_size=16)
    windows = mid_windows(size, head=head, tail=tail, samples=samples,
                          sample_bytes=sample_bytes)
    zeros = 0
    taken = 0
    read = 0
    try:
        with opener(path, "rb") as fh:
            read += _read_bounded(fh, size, head, tail, digest)
            for off, want in windows:
                fh.seek(off, os.SEEK_SET)
                chunk = fh.read(want) if want > 0 else b""
                if not chunk:
                    continue
                digest.update(chunk)
                read += len(chunk)
                taken += 1
                if not any(chunk):
                    zeros += 1
    except OSError:
        return None
    return {"digest": digest.hexdigest(), "size": size, "mid_samples": taken,
            "zero_samples": zeros, "bytes_read": read}


def looks_unfilled(record) -> bool:
    """True iff a mid sample was ALL ZEROS — an unfilled extent.

    This is what `st_blocks` cannot see. `posix_fallocate` reserves real
    extents, so a preallocated partial has the same size AND the same block
    count as the finished file; only reading the middle distinguishes them.
    """
    return bool(record) and int(record.get("zero_samples", 0)) > 0


def partial_digest(path, *, size=None, head: int = HEAD_BYTES,
                   tail: int = TAIL_BYTES, opener=open):
    """Just the digest from `sample_file`, or None.

    A THIN WRAPPER ON PURPOSE. Two sampling implementations would drift about
    which bytes they read, and the whole point of the mid samples is that both
    files sample the same places.

    None (never an exception, never a partial answer) for every failure the
    caller has to handle anyway:

      * the file disappeared between the index walk and this read;
      * it is unreadable (permissions, a device node, a dangling symlink);
      * it is EMPTY. A zero-byte file is not evidence of anything: every empty
        file has the same content, so confirming one as a duplicate of another
        would be true and useless, and it is the shape a failed download
        leaves behind.

    `opener` is injectable so a test can count the bytes actually read.
    """
    record = sample_file(path, size=size, head=head, tail=tail, opener=opener)
    return record["digest"] if record else None


def files_identical(a, b, *, chunk: int = COMPARE_CHUNK, deadline=None,
                    clock=time.monotonic, opener=open):
    """FULL byte-for-byte comparison. `(verdict, reason)`.

    `verdict` is True, False, or **None for "could not determine"** — the third
    state is not optional, because a comparison that ran out of budget must
    never be mistaken for either answer.

    WHY THIS EXISTS AT ALL, given everything above samples. Sampling cannot
    carry a destructive decision and it was being asked to. Eight mid-file
    samples catch a 40%-complete preallocated payload with overwhelming
    probability -- but a 99%-complete one only about 8% of the time, and
    deleting the finished copy to keep a 99% copy still destroys data. No
    bounded read proves two multi-GB files identical; it only ever fails to
    disprove it.

    So the WARNING path (`/dedupe`) samples, and the DESTRUCTIVE path
    (`/discard`) calls this. That is affordable precisely because /discard is
    not /match: it is a rare, user-initiated action with no latency budget,
    where reading both files is the only thing that is actually a proof.
    """
    read = 0
    try:
        with opener(a, "rb") as fa, opener(b, "rb") as fb:
            while True:
                if deadline is not None and clock() > deadline:
                    return None, (f"could not finish comparing the two files "
                                  f"within the time budget (compared "
                                  f"{read} bytes)")
                ca = fa.read(chunk)
                cb = fb.read(chunk)
                if ca != cb:
                    return False, ("the two files differ -- they are not the "
                                   "same content")
                if not ca:
                    return True, f"byte-for-byte identical over {read} bytes"
                read += len(ca)
    except OSError as exc:
        return None, f"could not read both files ({exc})"


class HashCache:
    """`path -> digest`, invalidated by (size, mtime).

    A size collision with several candidates hashes the SAME library files
    again on the next download that lands in that bucket, so without this the
    cost is paid per candidate per download rather than once per file. Keyed on
    (size, mtime_ns) as well as the path so a file rewritten in place is
    re-hashed rather than answered from a stale digest.

    CONCURRENCY. One instance is shared by every ThreadingHTTPServer request
    thread, so the dict operations are under a lock -- the same rule
    `DirIndex`/`FileIndex` already follow. Without it the read-modify-write in
    `digest()` interleaves: the eviction sweep pops entries another thread has
    just written, and `len() > max` is evaluated against a size a third thread
    is changing, so the cap drifts.

    BE HONEST ABOUT WHAT THAT COSTS AND HOW IT IS TESTED. Nothing here can
    corrupt an ANSWER -- a lost entry is a recomputed digest -- so the damage
    is wasted I/O and an unbounded-ish cache, not a wrong duplicate. And under
    CPython's GIL a stress test does NOT reliably reproduce it: removing this
    lock and running one leaves it green. So the lock is pinned STRUCTURALLY
    (`test_every_cache_mutation_is_taken_under_the_lock` substitutes a
    recording lock and asserts each entry point acquires it), and the stress
    test is kept as a smoke test that is explicitly labelled as one. A pin that
    passes when the fix is deleted is decoration, not a pin.

    THE HASH ITSELF RUNS OUTSIDE THE LOCK, deliberately, for the reason
    `FileIndex.refresh` spells out: holding a lock across file I/O serialises
    every request thread behind one read. The cost of that choice is that two
    threads racing on the same cold path may both hash it once -- a duplicated
    256 KiB read, not a wrong answer.
    """

    def __init__(self, *, max_entries: int = MAX_CACHE_ENTRIES,
                 head: int = HEAD_BYTES, tail: int = TAIL_BYTES):
        self._entries: dict = {}
        self._max = int(max_entries)
        self._head = int(head)
        self._tail = int(tail)
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def digest(self, path, *, opener=open):
        """Bounded digest for `path`, from cache when it is still valid."""
        key = str(path)
        try:
            st = os.stat(path)
        except OSError:
            # Gone or unreadable. Drop any cached digest: keeping it would let
            # a later file at the same path answer with the old one.
            with self._lock:
                self._entries.pop(key, None)
            return None
        stamp = (st.st_size, st.st_mtime_ns)
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None and cached[0] == stamp:
                self.hits += 1
                return cached[1]
            self.misses += 1
        value = partial_digest(path, size=st.st_size, head=self._head,
                               tail=self._tail, opener=opener)
        with self._lock:
            if value is None:
                self._entries.pop(key, None)
                return None
            self._entries[key] = (stamp, value)
            excess = len(self._entries) - self._max
            if excess > 0:
                for stale in list(self._entries)[:excess]:
                    self._entries.pop(stale, None)
        return value

    def forget(self, path) -> None:
        with self._lock:
            self._entries.pop(str(path), None)

    def stats(self) -> dict:
        with self._lock:
            return {"entries": len(self._entries), "hits": self.hits,
                    "misses": self.misses}

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def confirm_duplicate(root, rel_path: str, candidates, cache: HashCache, *,
                      max_candidates: int = MAX_DUP_CANDIDATES):
    """Confirm `rel_path` against same-size `candidates` by bounded digest.

    Returns `(relpath_of_the_match_or_None, reason)`. `reason` is always a
    plain sentence, because every no-answer here is a thing the operator may
    have to understand: an empty file, a file that vanished, an unreadable
    candidate.

    FAILS CLOSED in every direction. If the new file cannot be digested there
    is no confirmation, no matter how many candidates share its size — the one
    thing that must never happen is a confirmed duplicate that was never
    actually compared.
    """
    root = str(root)
    subject = cache.digest(os.path.join(root, rel_path))
    if subject is None:
        return None, ("the downloaded file could not be read for comparison "
                      "(it may be empty, gone, or unreadable)")
    considered = 0
    unreadable = 0
    for cand in list(candidates or ())[:int(max_candidates)]:
        cand = str(cand)
        if cand == rel_path:
            continue          # never confirm a file as a duplicate of itself
        considered += 1
        other = cache.digest(os.path.join(root, cand))
        if other is None:
            unreadable += 1
            continue
        if other == subject:
            return cand, "same size and the same bounded head+tail digest"
    if not considered:
        return None, "no other file of this size to compare against"
    if unreadable == considered:
        return None, "every same-size candidate was unreadable"
    return None, (f"{considered} file(s) of the same size, none with a "
                  "matching head+tail digest")
