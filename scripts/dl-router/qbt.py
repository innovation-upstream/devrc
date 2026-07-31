"""qBittorrent WebUI client + host<->container path mapping.

WHY this exists: the library root is a live seeding target. Files already there
may be torrent payloads, and a plain `mv` makes them vanish from qBittorrent's
point of view (torrent errors, seeding stops). Any move of an EXISTING file must
therefore go through `torrents/setLocation`.

New downloads are unaffected — they are fresh files written by the browser, not
torrent payloads — so nothing on the download path talks to qBittorrent. Only
the backfill does.

Path mapping: qBittorrent runs in a container that sees the disk at one prefix
while the host sees another. The mapping is DERIVED AT RUNTIME by correlating
`torrents/info[].save_path` against paths that actually exist on the host. It is
deliberately not read from qBittorrent's stored config, whose `LastSavePath`
references a mount point that no longer exists.

The HTTP layer is a single injectable `transport` callable, so every test runs
against a stub. Nothing here ever contacts the live instance under test.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path, PurePosixPath

API = "/api/v2"

# Torrent states that mean "still fine after the move". `pausedUP` counts: the
# payload is complete and intact, the user simply paused it.
SEEDING_STATES = frozenset({
    "uploading", "stalledUP", "queuedUP", "checkingUP", "forcedUP", "pausedUP",
})
BROKEN_STATES = frozenset({"error", "missingFiles", "unknown"})

# TRANSIENT, and the reason verify_seeding used to abort a backfill mid-move:
# `setLocation` returns immediately and qBittorrent then moves the payload in
# the background, sitting in `moving` for as long as the copy takes. That state
# is in neither SEEDING_STATES nor BROKEN_STATES, so verification returned
# False, the row raised ApplyError, and the run aborted leaving a torrent
# part-way through a relocation with no rollback.
MOVING_STATES = frozenset({"moving", "checkingResumeData"})

DEFAULT_MOVE_TIMEOUT_S = 300.0
DEFAULT_MOVE_POLL_S = 1.0


class QbtError(RuntimeError):
    """Any qBittorrent WebUI failure (transport, auth, or API-level)."""


class Response:
    __slots__ = ("status", "body", "headers")

    def __init__(self, status: int, body: bytes, headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        try:
            return json.loads(self.text())
        except ValueError as exc:
            raise QbtError(f"non-JSON response: {self.text()[:120]!r}") from exc


def urllib_transport(timeout: float = 8.0):
    """Default transport: urllib with a cookie jar for the SID session."""
    import http.cookiejar

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))

    def transport(method: str, url: str, data=None, headers=None) -> Response:
        body = urllib.parse.urlencode(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method,
                                     headers=headers or {})
        # qBittorrent rejects requests whose Referer/Origin is off-host unless
        # CSRF protection is disabled; sending the base URL satisfies it.
        try:
            with opener.open(req, timeout=timeout) as resp:
                return Response(resp.status, resp.read(), dict(resp.headers))
        except urllib.error.HTTPError as exc:
            return Response(exc.code, exc.read(), dict(exc.headers or {}))
        except (urllib.error.URLError, OSError) as exc:
            raise QbtError(f"{method} {url}: {exc}") from exc

    return transport


class QbtClient:
    def __init__(self, base_url: str, username: str = "", password: str = "",
                 *, transport=None, timeout: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._transport = transport or urllib_transport(timeout)
        self._logged_in = False

    # --- plumbing ---------------------------------------------------------- #
    def _call(self, method: str, path: str, data=None) -> Response:
        url = f"{self.base_url}{API}{path}"
        headers = {"Referer": self.base_url, "Origin": self.base_url}
        return self._transport(method, url, data, headers)

    def login(self) -> None:
        """Authenticate. qBittorrent answers 200 with the body `Ok.`/`Fails.`."""
        resp = self._call("POST", "/auth/login",
                          {"username": self.username, "password": self.password})
        if resp.status == 403:
            raise QbtError("login refused (403) — banned IP or CSRF check")
        if resp.status != 200:
            raise QbtError(f"login failed: HTTP {resp.status}")
        if resp.text().strip() != "Ok.":
            raise QbtError("login failed: bad credentials")
        self._logged_in = True

    def ensure_login(self) -> None:
        if not self._logged_in:
            self.login()

    # --- API --------------------------------------------------------------- #
    def torrents_info(self) -> list:
        self.ensure_login()
        resp = self._call("GET", "/torrents/info")
        if resp.status == 403:
            # Session expired mid-run — one re-login, then give up.
            self._logged_in = False
            self.ensure_login()
            resp = self._call("GET", "/torrents/info")
        if resp.status != 200:
            raise QbtError(f"torrents/info: HTTP {resp.status}")
        data = resp.json()
        if not isinstance(data, list):
            raise QbtError("torrents/info: expected a list")
        return data

    def set_location(self, torrent_hash: str, location: str) -> None:
        """Move a torrent's payload. `location` is a CONTAINER path."""
        self.ensure_login()
        resp = self._call("POST", "/torrents/setLocation",
                          {"hashes": torrent_hash, "location": location})
        if resp.status == 400:
            raise QbtError("setLocation: save path is empty")
        if resp.status == 403:
            raise QbtError("setLocation: user has no write access to the path")
        if resp.status == 409:
            raise QbtError("setLocation: unable to create the save path")
        if resp.status != 200:
            raise QbtError(f"setLocation: HTTP {resp.status}")

    def torrents_files(self, torrent_hash: str) -> list:
        """Every file in one torrent, as `[{name, size, ...}, ...]`.

        `name` is relative to the torrent's `save_path`. This is what makes a
        multi-file or no-root-folder torrent visible to the backfill: those
        payloads sit DIRECTLY at the library root -- exactly the population the
        backfill targets -- and `content_path`/`save_path+name` alone never
        named them.
        """
        self.ensure_login()
        query = urllib.parse.urlencode({"hash": torrent_hash})
        resp = self._call("GET", f"/torrents/files?{query}")
        if resp.status == 404:
            raise QbtError(f"torrents/files: unknown hash {torrent_hash[:12]}")
        if resp.status != 200:
            raise QbtError(f"torrents/files: HTTP {resp.status}")
        data = resp.json()
        if not isinstance(data, list):
            raise QbtError("torrents/files: expected a list")
        return data

    def torrent_state(self, torrent_hash: str):
        for t in self.torrents_info():
            if str(t.get("hash", "")).lower() == torrent_hash.lower():
                return t.get("state")
        return None

    def verify_seeding(self, torrent_hash: str, *,
                       timeout_s: float = DEFAULT_MOVE_TIMEOUT_S,
                       poll_s: float = DEFAULT_MOVE_POLL_S,
                       sleep=time.sleep, clock=time.monotonic) -> bool:
        """True iff the torrent is in a healthy post-move state.

        WAITS OUT `moving`. `setLocation` returns as soon as qBittorrent has
        accepted the request, not when the payload has arrived, so an immediate
        check saw `moving` -- neither seeding nor broken -- and reported
        failure. The backfill then aborted with the torrent part-way through a
        relocation and nothing to roll back. Bounded, and the clock and sleep
        are injectable so tests never actually wait.
        """
        deadline = clock() + float(timeout_s)
        while True:
            state = self.torrent_state(torrent_hash)
            if state is None:
                return False
            if state in BROKEN_STATES:
                return False
            if state in MOVING_STATES:
                if clock() >= deadline:
                    return False        # still moving after the whole budget
                sleep(poll_s)
                continue
            return state in SEEDING_STATES


class PathMap:
    """Translates between the host view and the container view of one disk."""

    __slots__ = ("container_prefix", "host_prefix")

    def __init__(self, container_prefix: str, host_prefix: str):
        self.container_prefix = str(container_prefix).rstrip("/") or "/"
        self.host_prefix = str(host_prefix).rstrip("/") or "/"

    def __repr__(self) -> str:
        return f"PathMap({self.container_prefix!r} <-> {self.host_prefix!r})"

    def __eq__(self, other) -> bool:
        return (isinstance(other, PathMap)
                and self.container_prefix == other.container_prefix
                and self.host_prefix == other.host_prefix)

    def __hash__(self) -> int:
        return hash((self.container_prefix, self.host_prefix))

    @staticmethod
    def _rel(path: str, prefix: str):
        path = str(path).rstrip("/")
        if prefix == "/":
            return path.lstrip("/")
        if path == prefix:
            return ""
        if path.startswith(prefix + "/"):
            return path[len(prefix) + 1:]
        return None

    def to_host(self, container_path: str):
        rel = self._rel(container_path, self.container_prefix)
        if rel is None:
            return None
        return str(Path(self.host_prefix) / rel) if rel else self.host_prefix

    def to_container(self, host_path: str):
        rel = self._rel(str(host_path), self.host_prefix)
        if rel is None:
            return None
        return str(PurePosixPath(self.container_prefix) / rel) if rel \
            else self.container_prefix


def host_root_candidates(library_root, extra=()) -> list:
    """Host mount points to try, most specific first: the library root, then
    each of its ancestors, then any explicitly configured roots."""
    root = Path(library_root).resolve()
    out = [str(root)] + [str(p) for p in root.parents]
    for item in extra or ():
        item = str(item)
        if item not in out:
            out.append(item)
    # Drop "/" — mapping the whole filesystem is never the intended answer.
    return [p for p in out if p != "/"]


# Bound on how many torrents `derive_path_map` will probe. Not a sample: the
# whole list is considered up to this many, which is what "the mapping most
# torrents agree on" was already claiming.
MAX_PROBE = 5000

# A mapping has to be corroborated. One accidental correlation -- a file that
# happens to exist at a plausible-but-wrong host path -- used to be enough to
# fix the map for the whole run, and the map is what decides which files are
# torrent-backed.
MIN_PATH_MAP_VOTES = 2


def derive_path_map(torrents, host_roots, *, exists=os.path.exists,
                    sample: int | None = None,
                    min_votes: int = MIN_PATH_MAP_VOTES,
                    library_root=None):
    """Derive the host<->container mapping by probing the host filesystem.

    For each torrent we know its container `save_path` and its `name`. We try
    progressively shorter tails of the save path under each candidate host root
    and keep the ones where `<host_root>/<tail>/<name>` actually exists.

    Three things this refuses to do, all of which it used to:

      * decide from a 50-torrent prefix. `sample=None` (the default) considers
        the whole list up to MAX_PROBE.
      * accept a mapping on a SINGLE vote. `min_votes` corroborating torrents
        are required and the winner must beat the runner-up outright -- a tie
        means the evidence does not identify one mount point.
      * return a mapping that cannot even express the library root. Pass
        `library_root` and the winner must translate it to a container path,
        otherwise the map is useless for the backfill and worse than None (it
        would silently classify every root file as not-torrent-backed).

    Returns a `PathMap` or None when nothing correlates.
    """
    host_roots = sorted({str(h).rstrip("/") or "/" for h in host_roots},
                        key=len, reverse=True)
    limit = MAX_PROBE if sample is None else int(sample)
    votes = Counter()
    for t in list(torrents)[:limit]:
        save = str(t.get("save_path") or "").strip()
        name = str(t.get("name") or "").strip()
        if not save or not name or not save.startswith("/"):
            continue
        segs = [s for s in PurePosixPath(save).parts if s != "/"]
        found = None
        for k in range(len(segs), -1, -1):
            tail = segs[len(segs) - k:] if k else []
            for hroot in host_roots:
                if exists(str(Path(hroot, *tail) / name)):
                    container_prefix = "/" + "/".join(segs[:len(segs) - k])
                    found = PathMap(container_prefix, hroot)
                    break
            if found:
                break
        if found:
            votes[found] += 1
    if not votes:
        return None
    ranked = sorted(votes.items(),
                    key=lambda kv: (-kv[1], -len(kv[0].container_prefix)))
    best, best_votes = ranked[0]
    if best_votes < int(min_votes):
        return None
    if len(ranked) > 1 and ranked[1][1] >= best_votes:
        return None                      # no outright winner
    if library_root is not None:
        try:
            root = str(Path(library_root).resolve())
        except OSError:
            return None
        if best.to_container(root) is None:
            return None
    return best


class TorrentIndex:
    """`host path -> torrent`, plus whether that mapping can be TRUSTED to be
    exhaustive.

    `complete` is the important field and the reason this is not a bare dict:
    absence from the index is only proof a file is not torrent-backed if every
    torrent's file list was actually read. When a listing fails, or none was
    requested, absence means "unknown" and the backfill must SKIP rather than
    fall back to a plain rename.

    Behaves enough like a mapping that callers (and the existing tests) can
    treat it as one.
    """

    __slots__ = ("by_path", "complete", "errors")

    def __init__(self, by_path=None, complete=False, errors=None):
        self.by_path = dict(by_path or {})
        self.complete = bool(complete)
        self.errors = list(errors or [])

    def get(self, key, default=None):
        return self.by_path.get(key, default)

    def __getitem__(self, key):
        return self.by_path[key]

    def __contains__(self, key):
        return key in self.by_path

    def __len__(self):
        return len(self.by_path)

    def __iter__(self):
        return iter(self.by_path)

    def __eq__(self, other):
        if isinstance(other, TorrentIndex):
            return (self.by_path == other.by_path
                    and self.complete == other.complete)
        return self.by_path == other

    def __repr__(self):
        return (f"TorrentIndex({len(self.by_path)} paths, "
                f"complete={self.complete}, errors={len(self.errors)})")


def index_by_host_path(torrents, path_map: PathMap, *, files_for=None):
    """`host path -> torrent` for a torrent's content path, save path AND files.

    The docstring used to claim "and its files" while only ever indexing
    `content_path` and `save_path/name`. That misses exactly the population the
    backfill exists for: a multi-file or no-root-folder torrent whose payload
    sits directly at the library root. Those files were absent from the index,
    the backfill read absence as "not torrent-backed", and classified them `fs`
    -- a plain rename of a live seeding payload.

    `files_for(hash) -> [{name, ...}]` enables the per-file pass (in practice
    `QbtClient.torrents_files`). Without it the index is returned with
    `complete=False`, which forbids any `fs` classification downstream.
    """
    out: dict = {}
    errors: list = []
    if path_map is None:
        return TorrentIndex({}, complete=False,
                            errors=["no host<->container path map"])

    torrents = list(torrents)
    for t in torrents:
        content = str(t.get("content_path") or "").strip()
        save = str(t.get("save_path") or "").strip()
        name = str(t.get("name") or "").strip()
        for container in filter(None, [content,
                                       (f"{save.rstrip('/')}/{name}"
                                        if save and name else "")]):
            host = path_map.to_host(container)
            if host:
                out.setdefault(os.path.normpath(host), t)

    complete = files_for is not None
    if files_for is not None:
        for t in torrents:
            thash = str(t.get("hash") or "").strip()
            save = str(t.get("save_path") or "").strip()
            if not thash or not save:
                complete = False
                errors.append(f"{thash[:12] or '?'}: no hash or save_path")
                continue
            try:
                entries = files_for(thash) or []
            except Exception as exc:  # noqa: BLE001 -- any failure = unproven
                complete = False
                errors.append(f"{thash[:12]}: {exc}")
                continue
            for entry in entries:
                rel = str((entry or {}).get("name") or "").strip()
                if not rel:
                    continue
                host = path_map.to_host(f"{save.rstrip('/')}/{rel}")
                if host:
                    out.setdefault(os.path.normpath(host), t)

    return TorrentIndex(out, complete=complete, errors=errors)
