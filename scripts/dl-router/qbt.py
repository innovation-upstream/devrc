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

    def torrent_state(self, torrent_hash: str):
        for t in self.torrents_info():
            if str(t.get("hash", "")).lower() == torrent_hash.lower():
                return t.get("state")
        return None

    def verify_seeding(self, torrent_hash: str) -> bool:
        """True iff the torrent is in a healthy post-move state."""
        state = self.torrent_state(torrent_hash)
        if state is None:
            return False
        if state in BROKEN_STATES:
            return False
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


def derive_path_map(torrents, host_roots, *, exists=os.path.exists,
                    sample: int = 50):
    """Derive the host<->container mapping by probing the host filesystem.

    For each torrent we know its container `save_path` and its `name`. We try
    progressively shorter tails of the save path under each candidate host root
    and keep the ones where `<host_root>/<tail>/<name>` actually exists. The
    mapping most torrents agree on wins (ties -> the longest container prefix,
    i.e. the most specific mount point).

    Returns a `PathMap` or None when nothing correlates.
    """
    host_roots = sorted({str(h).rstrip("/") or "/" for h in host_roots},
                        key=len, reverse=True)
    votes = Counter()
    for t in list(torrents)[:sample]:
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
    best = max(votes.items(),
               key=lambda kv: (kv[1], len(kv[0].container_prefix)))
    return best[0]


def index_by_host_path(torrents, path_map: PathMap) -> dict:
    """`host path -> torrent` for both a torrent's content path and its files.

    Used by the backfill to decide `qbt` vs `fs` for each candidate row.
    """
    out: dict = {}
    if path_map is None:
        return out
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
    return out
