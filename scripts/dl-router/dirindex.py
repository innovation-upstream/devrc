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
"""
from __future__ import annotations

import hashlib
import os
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

    # --- scanning ---------------------------------------------------------- #
    def _scan(self) -> list:
        entries, errors = [], []
        try:
            it = os.scandir(self.root)
        except (OSError, ValueError) as exc:
            self._errors = [f"{self.root}: {exc.__class__.__name__}"]
            return []
        with it:
            for de in it:
                try:
                    # follow_symlinks=True on purpose: a symlinked subject dir is
                    # a legitimate layout and should still be a routing target.
                    if not de.is_dir(follow_symlinks=True):
                        continue
                except OSError as exc:
                    errors.append(f"{de.name}: {exc.__class__.__name__}")
                    continue
                name = de.name
                if name.startswith("."):
                    continue
                entries.append(DirEntry.of(name))
        entries.sort(key=lambda e: e.name)
        self._errors = errors
        return entries

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
        if force or self._stale():
            self._root_mtime = self._root_stat_mtime()
            self._entries = self._scan()
            self._loaded_at = self._clock()
            payload = "\n".join(e.name for e in self._entries)
            self._etag = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    # --- accessors --------------------------------------------------------- #
    def entries(self) -> list:
        self.refresh()
        return list(self._entries)

    def names(self) -> list:
        return [e.name for e in self.entries()]

    def name_set(self) -> set:
        return {e.name for e in self.entries()}

    def etag(self) -> str:
        self.refresh()
        return self._etag

    def errors(self) -> list:
        self.refresh()
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
        """The payload the extension caches (GET /dirs)."""
        entries = self.entries()
        return {
            "etag": self._etag,
            "otherDir": self.other_dir,
            "dirs": [{"name": e.name, "key": e.key, "tokens": list(e.tokens)}
                     for e in entries],
            "errors": self._errors,
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

    def _scan(self) -> dict:
        out: dict = {}
        count = 0
        self._truncated = False
        # followlinks=False: never walk out of the library through a symlink.
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False,
                                                    onerror=lambda _e: None):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fname in filenames:
                if fname.startswith("."):
                    continue
                if count >= self._max:
                    self._truncated = True
                    return out
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
        self._count = count
        return out

    def refresh(self, force: bool = False) -> None:
        if force or self._loaded_at < 0 or \
                (self._clock() - self._loaded_at) >= self._ttl:
            self._map = self._scan()
            self._loaded_at = self._clock()

    def by_name_key(self, key: str) -> list:
        self.refresh()
        return list(self._map.get(key, ()))

    def stats(self) -> dict:
        self.refresh()
        return {"files": self._count, "keys": len(self._map),
                "truncated": self._truncated}
