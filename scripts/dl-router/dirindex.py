"""Cached views of the library root: the directory index and the file index.

Both are read-only. `DirIndex` lists the top-level subject directories (the
routing targets); `FileIndex` walks the whole tree once for dedupe lookups.

Caching is mtime-plus-TTL: a cheap `stat()` on the root catches the common case
(a new directory appeared), and the TTL bounds staleness from a change deeper in
the tree that does not move the root's mtime. The clock is injectable so tests
never sleep.

Robustness matters more than completeness here — the library is a live seeding
target with unusual names and the occasional unreadable directory. A permission
error skips that entry; it never takes the sidecar down.

Concurrency: `refresh()` mutates shared state (`_entries`, `_etag`, `_map`) and
is reached from EVERY ThreadingHTTPServer request thread, plus /mkdir and
/relocate which force it. Each index therefore holds a lock across the whole
scan-and-publish, so a reader can never observe a half-built index. The tests
are single-threaded, so this is the one invariant here they cannot demonstrate.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

from matcher import DirEntry, norm_key


class DirIndex:
    """Top-level directories under `root`, normalised and cached."""

    def __init__(self, root, *, clock=time.monotonic, ttl: float = 5.0,
                 other_dir: str = "other"):
        self.root = Path(root)
        self._clock = clock
        self._ttl = float(ttl)
        self.other_dir = other_dir
        self._entries: list = []
        self._etag: str = ""
        self._loaded_at: float = -1.0
        self._root_mtime = None
        self._errors: list = []
        self._lock = threading.RLock()

    # --- scanning ---------------------------------------------------------- #
    def _scan(self) -> list:
        entries, errors = [], []
        try:
            root_resolved = Path(self.root).resolve()
        except OSError:
            root_resolved = None
        try:
            it = os.scandir(self.root)
        except (OSError, ValueError) as exc:
            self._errors = [f"{self.root}: {exc.__class__.__name__}"]
            return []
        with it:
            for de in it:
                try:
                    # follow_symlinks=True on purpose: a symlinked subject dir
                    # is a legitimate layout and should still be a routing
                    # target -- PROVIDED it resolves back inside the library
                    # root.
                    if not de.is_dir(follow_symlinks=True):
                        continue
                except OSError as exc:
                    errors.append(f"{de.name}: {exc.__class__.__name__}")
                    continue
                name = de.name
                if name.startswith("."):
                    continue
                # ONE invariant, honoured by every writer. safe_rel_path (used
                # by /mkdir, /relocate and the yt-dlp fetcher) refuses a
                # directory that resolves outside the root, so advertising one
                # here produced a routing target that browser downloads could
                # write through -- straight out of the library, past every
                # containment check -- while yt-dlp refused it. Escaping
                # symlinks are excluded and REPORTED, not silently dropped.
                if not self._resolves_inside(de.path, root_resolved):
                    errors.append(f"{name}: symlink resolves outside the root")
                    continue
                entries.append(DirEntry.of(name))
        entries.sort(key=lambda e: e.name)
        self._errors = errors
        return entries

    @staticmethod
    def _resolves_inside(path, root_resolved) -> bool:
        """`root_resolved` is hoisted out of the scan loop: this used to
        re-resolve the library root once per directory entry."""
        if root_resolved is None:
            return False
        try:
            target = Path(path).resolve()
        except OSError:
            return False
        return target == root_resolved or target.is_relative_to(root_resolved)

    def _root_stat_mtime(self):
        try:
            return os.stat(self.root).st_mtime_ns
        except OSError:
            return None

    def _stale(self) -> bool:
        if self._loaded_at < 0:
            return True
        mtime = self._root_stat_mtime()
        if mtime != self._root_mtime:
            return True
        return (self._clock() - self._loaded_at) >= self._ttl

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            if not (force or self._stale()):
                return
            root_mtime = self._root_stat_mtime()
            entries = self._scan()
            payload = "\n".join(e.name for e in entries)
            # Publish only once everything is built, so a concurrent reader
            # never sees entries that do not match the etag.
            self._root_mtime = root_mtime
            self._entries = entries
            self._loaded_at = self._clock()
            self._etag = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    # --- accessors --------------------------------------------------------- #
    def entries(self) -> list:
        self.refresh()
        with self._lock:
            return list(self._entries)

    def names(self) -> list:
        return [e.name for e in self.entries()]

    def name_set(self) -> set:
        return {e.name for e in self.entries()}

    def etag(self) -> str:
        self.refresh()
        with self._lock:
            return self._etag

    def errors(self) -> list:
        self.refresh()
        with self._lock:
            return list(self._errors)

    def has(self, name: str) -> bool:
        return name in self.name_set()

    def by_key(self, name: str):
        key = norm_key(name)
        for entry in self.entries():
            if entry.key == key:
                return entry
        return None

    def snapshot(self) -> dict:
        """The payload the extension caches (GET /dirs).

        entries + etag + errors are read under ONE lock hold. Reading `_etag`
        outside it was the exact invariant the locking change advertised and
        did not deliver: a concurrent refresh could pair refresh-N's entries
        with refresh-N+1's etag, and server.py serves that as the HTTP `ETag`
        -- so the extension caches a stale directory list under a fresh etag
        and its `If-None-Match` never refetches it.
        """
        self.refresh()
        with self._lock:
            return {
                "etag": self._etag,
                "otherDir": self.other_dir,
                "dirs": [{"name": e.name, "key": e.key,
                          "tokens": list(e.tokens)}
                         for e in self._entries],
                "errors": list(self._errors),
            }


class FileIndex:
    """Whole-tree file index for dedupe: normalised stem -> [(relpath, size)].

    Bounded by `max_files` so a pathological tree cannot exhaust memory; the
    cap being hit is reported rather than silently truncating the answer.
    """

    def __init__(self, root, *, clock=time.monotonic, ttl: float = 60.0,
                 max_files: int = 200000):
        self.root = Path(root)
        self._clock = clock
        self._ttl = float(ttl)
        self._max = int(max_files)
        self._map: dict = {}
        self._count = 0
        self._truncated = False
        self._loaded_at = -1.0
        self._lock = threading.RLock()
        self._scanning = False

    def _scan(self):
        out: dict = {}
        count = 0
        truncated = False
        # followlinks=False: never walk out of the library through a symlink.
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False,
                                                    onerror=lambda _e: None):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fname in filenames:
                if fname.startswith("."):
                    continue
                if count >= self._max:
                    truncated = True
                    return out, count, truncated
                full = os.path.join(dirpath, fname)
                try:
                    size = os.stat(full).st_size
                except OSError:
                    size = 0
                rel = os.path.relpath(full, self.root)
                stem = fname.rsplit(".", 1)[0] if "." in fname else fname
                key = norm_key(stem)
                if key:
                    out.setdefault(key, []).append((rel, size))
                count += 1
        return out, count, truncated

    def refresh(self, force: bool = False) -> None:
        """Single-flight, and the scan runs OUTSIDE the lock.

        This is a whole-tree walk of a media library. Holding the lock across
        it serialised every ThreadingHTTPServer request thread behind one scan
        and would push /match straight past its 400 ms budget -- which is the
        timeout that makes the extension fall back to its cached decision. One
        thread scans; the others serve the previous (slightly stale) index,
        which is exactly what a dedupe WARNING can tolerate.
        """
        with self._lock:
            stale = (force or self._loaded_at < 0
                     or (self._clock() - self._loaded_at) >= self._ttl)
            if not stale or self._scanning:
                return
            self._scanning = True
        try:
            data, count, truncated = self._scan()
        except Exception:
            with self._lock:
                self._scanning = False
            raise
        with self._lock:
            self._map = data
            self._count = count
            self._truncated = truncated
            self._loaded_at = self._clock()
            self._scanning = False

    def by_name_key(self, key: str) -> list:
        self.refresh()
        with self._lock:
            return list(self._map.get(key, ()))

    def stats(self) -> dict:
        self.refresh()
        with self._lock:
            return {"files": self._count, "keys": len(self._map),
                    "truncated": self._truncated}
