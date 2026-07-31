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
  2. ONLY when sizes collide, a bounded head+tail digest of both files. Two
     files of identical length whose first and last 128 KiB agree are the same
     file for every practical purpose here; two that disagree anywhere in
     either window are definitively not.

NEVER READ A WHOLE FILE. These are multi-GB media files and this runs on a
download-completion path. The digest reads at most `HEAD_BYTES + TAIL_BYTES`
and folds the size in, so the read cost is constant regardless of file size.
The cost of the bound, stated honestly: two files that share their first and
last 128 KiB and differ only in the middle are reported as duplicates. For
media that means two encodes of the same source with identical length and
identical container head/tail — and the answer is a WARNING with a keep
button, never an automatic destruction, which is what makes the bound
affordable. `tests/test_dedupe.py` pins the bound behaviourally: a file that
differs ONLY in the middle digests the same, so widening this to a whole-file
read breaks a named test.

NOTHING HERE IMPORTS FROM matcher/dirindex/server. It is pure filesystem
identity, so it can be exercised against real temp files with no index, no
store and no sidecar.
"""
from __future__ import annotations

import hashlib
import os

# 128 KiB from each end. Big enough to cover a container header plus the first
# frames (and the trailing index/moov atom that a remux would change), small
# enough that hashing eight candidates is ~2 MiB of reads.
HEAD_BYTES = 128 * 1024
TAIL_BYTES = 128 * 1024

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


def _read_bounded(fh, size: int, head: int, tail: int, digest) -> int:
    """Feed at most `head + tail` bytes into `digest`. Returns bytes read."""
    read = 0
    remaining = head
    while remaining > 0:
        chunk = fh.read(min(65536, remaining))
        if not chunk:
            break
        digest.update(chunk)
        read += len(chunk)
        remaining -= len(chunk)
    if size > head + tail:
        # Seek rather than read through the middle: this is the whole point.
        fh.seek(size - tail, os.SEEK_SET)
        remaining = tail
        while remaining > 0:
            chunk = fh.read(min(65536, remaining))
            if not chunk:
                break
            digest.update(chunk)
            read += len(chunk)
            remaining -= len(chunk)
    return read


def partial_digest(path, *, size=None, head: int = HEAD_BYTES,
                   tail: int = TAIL_BYTES, opener=open):
    """Bounded content fingerprint, or None when the file cannot supply one.

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
    try:
        if size is None:
            size = os.stat(path).st_size
        size = int(size)
    except (OSError, TypeError, ValueError):
        return None
    if size <= 0:
        return None
    # The size is folded in FIRST, so two files whose sampled windows agree but
    # whose lengths differ can never collide even if a caller compares digests
    # across size buckets.
    digest = hashlib.blake2b(str(size).encode("ascii"), digest_size=16)
    try:
        with opener(path, "rb") as fh:
            _read_bounded(fh, size, int(head), int(tail), digest)
    except OSError:
        return None
    return digest.hexdigest()


class HashCache:
    """`path -> digest`, invalidated by (size, mtime).

    A size collision with several candidates hashes the SAME library files
    again on the next download that lands in that bucket, so without this the
    cost is paid per candidate per download rather than once per file. Keyed on
    (size, mtime_ns) as well as the path so a file rewritten in place is
    re-hashed rather than answered from a stale digest.
    """

    def __init__(self, *, max_entries: int = MAX_CACHE_ENTRIES,
                 head: int = HEAD_BYTES, tail: int = TAIL_BYTES):
        self._entries: dict = {}
        self._max = int(max_entries)
        self._head = int(head)
        self._tail = int(tail)
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
            self._entries.pop(key, None)
            return None
        stamp = (st.st_size, st.st_mtime_ns)
        cached = self._entries.get(key)
        if cached is not None and cached[0] == stamp:
            self.hits += 1
            return cached[1]
        self.misses += 1
        value = partial_digest(path, size=st.st_size, head=self._head,
                               tail=self._tail, opener=opener)
        if value is None:
            self._entries.pop(key, None)
            return None
        self._entries[key] = (stamp, value)
        if len(self._entries) > self._max:
            for stale in list(self._entries)[:len(self._entries) - self._max]:
                self._entries.pop(stale, None)
        return value

    def forget(self, path) -> None:
        self._entries.pop(str(path), None)

    def stats(self) -> dict:
        return {"entries": len(self._entries), "hits": self.hits,
                "misses": self.misses}

    def __len__(self) -> int:
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
