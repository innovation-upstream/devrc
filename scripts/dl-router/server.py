#!/usr/bin/env python3
"""dl-router sidecar — the loopback brain behind the download-routing extension.

Shape (mirrors browser-bridge's server, deliberately — same host, same threat
model, same operational habits):

  * binds 127.0.0.1 ONLY, and refuses to start on any non-loopback address;
  * bearer-token auth on EVERY endpoint, token from ~/.config/dl-router/token
    (0600, auto-created on first run);
  * Host-header allowlist, so a malicious page cannot DNS-rebind onto it;
  * stdlib only, single file, no sibling imports at runtime beyond this package.

It owns the directory index, the alias/example store, dedupe, and the matcher.
The extension keeps a cached snapshot so a sidecar outage degrades to "route
from cache", never to "hang the download" (see the ladder in the extension).

Endpoints (all require `Authorization: Bearer <token>`)

    GET  /healthz          liveness + index summary
    GET  /dirs             directory index + alias snapshot (ETag'd)
    POST /match            page context -> {dir, confidence, reason, ...}
    POST /learn            persist a correction (alias + labelled example)
    POST /mkdir            create a validated new directory
    POST /relocate         validated rename WITHIN the library root (undo)
    POST /fetch            yt-dlp job for a stream URL
    GET  /fetch/<id>       job status
    GET  /log              recent routes

Env: DL_ROUTER_HOST, DL_ROUTER_PORT, DL_ROUTER_CONFIG, DL_ROUTER_STATE_DIR,
     DL_ROUTER_TOKEN_FILE, DL_ROUTER_LIBRARY_ROOT (see config.py).
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as config_mod  # noqa: E402
from dirindex import DirIndex, FileIndex  # noqa: E402
from fetcher import Fetcher  # noqa: E402
from matcher import MatchContext, Matcher, find_duplicate, norm_key  # noqa: E402
from safety import (  # noqa: E402
    UnsafeName, is_safe_dir_name, names_match, safe_rel_path,
)
from store import Store  # noqa: E402

SERVER_VERSION = "dl-router/1"
MAX_BODY = 256 * 1024

# How much older than its own routing decision a file may be and still be
# accepted as "this router created it" (filesystem timestamp granularity, not
# clock skew — both come from the same machine). A torrent payload predates its
# would-be routing decision by hours or days, so a few seconds costs nothing.
MTIME_SLACK_S = 5.0

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_ALLOWED_HOST_HEADERS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def log(event: str, **fields) -> None:
    """One structured line per notable event. No page content, no filenames of
    library files — this journal is readable by anything that can read the user
    journal, and the library's contents are private."""
    parts = [f"dl-router {event}"]
    for k, v in fields.items():
        parts.append(f"{k}={v}")
    print(" ".join(parts), flush=True)


class App:
    """Everything the handler needs. Constructed once; injectable for tests."""

    def __init__(self, cfg: config_mod.Config, *, store: Store | None = None,
                 dir_index: DirIndex | None = None,
                 file_index: FileIndex | None = None,
                 fetcher: Fetcher | None = None, clock=time.time,
                 config_error: str | None = None):
        self.cfg = cfg
        self.clock = clock
        self.config_error = config_error
        self.root = cfg.library_root
        self.store = store if store is not None else Store(cfg.db_path,
                                                           clock=clock)
        if dir_index is not None:
            self.dirs = dir_index
        elif self.root is not None:
            self.dirs = DirIndex(self.root, ttl=float(cfg.get("dir_cache_ttl_s", 5.0)),
                                 other_dir=cfg.other_dir)
        else:
            self.dirs = None
        if file_index is not None:
            self.files = file_index
        elif self.root is not None:
            self.files = FileIndex(self.root,
                                   ttl=float(cfg.get("file_cache_ttl_s", 60.0)),
                                   max_files=int(cfg.get("file_index_max", 200000)))
        else:
            self.files = None
        yt = cfg.section("ytdlp")
        if fetcher is not None:
            self.fetcher = fetcher
        elif self.root is not None:
            self.fetcher = Fetcher(
                self.root, ytdlp_bin=yt.get("bin", "yt-dlp"),
                cookies_from_browser=yt.get("cookies_from_browser", ""),
                output_template=yt.get("output_template",
                                       "%(title).150B [%(id)s].%(ext)s"),
                max_jobs=int(yt.get("max_jobs", 4)), clock=clock)
        else:
            self.fetcher = None

    # --- helpers ----------------------------------------------------------- #
    @property
    def configured(self) -> bool:
        return self.root is not None and self.dirs is not None

    def matcher(self) -> Matcher:
        return Matcher(self.dirs.entries(), self.store.alias_map(),
                       threshold=self.cfg.auto_threshold,
                       tie_margin=self.cfg.tie_margin,
                       other_dir=self.cfg.other_dir)

    # --- endpoint logic (HTTP-free, so tests can call it directly) --------- #
    def healthz(self) -> dict:
        out = {"ok": True, "version": SERVER_VERSION,
               "configured": self.configured}
        if self.config_error:
            # The whole point of degrading instead of exiting: the operator can
            # see WHY without reading the journal of a restart loop.
            out["configError"] = self.config_error
        if self.configured:
            out["root_present"] = self.root.is_dir()
            out["dirs"] = len(self.dirs.entries())
            out["aliases"] = self.store.alias_count()
            out["etag"] = self.dirs.etag()
        return out

    def dirs_snapshot(self) -> dict:
        snap = self.dirs.snapshot()
        # The extension needs the root to prove a completed download actually
        # landed inside the library before it asks /relocate to move it (see
        # relPathFromAbsolute). Loopback + bearer token only, and it never
        # leaves the machine.
        snap["root"] = str(self.root)
        snap["aliases"] = [{"key": k, "site": s, "dir": d}
                           for (k, s), d in sorted(self.store.alias_map().items())]
        snap["threshold"] = self.cfg.auto_threshold
        # Per-site capture rules travel with the snapshot so adding a site is a
        # config edit, never an extension code change.
        snap["siteRules"] = self.cfg.section("site_rules")
        snap["matchTimeoutMs"] = int(self.cfg.get("match_timeout_ms", 400))
        snap["captureWindowS"] = int(self.cfg.get("capture_window_s", 15))
        snap["toastMs"] = int(self.cfg.get("toast_ms", 8000))
        return snap

    def match(self, payload: dict) -> dict:
        ctx = MatchContext.from_payload(payload)
        matcher = self.matcher()
        prior = self.store.host_prior(ctx.site) if ctx.site else None
        result = matcher.match(ctx, host_prior=prior)
        result.dup = find_duplicate(self.files, result.dir, ctx.filename,
                                    ctx.size)
        # `downloadId` is the browser's DownloadItem id. Recording it here is
        # what later lets /relocate prove a file under the library root was put
        # there by this router and not by qBittorrent (see `_prove_owned`).
        self.store.log_route(url=ctx.url, site=ctx.site, filename=ctx.filename,
                             dir_name=result.dir, confidence=result.confidence,
                             reason=result.reason, auto=result.auto,
                             dup=result.dup,
                             download_id=str(payload.get("downloadId") or ""))
        return result.as_dict(ttl_ms=int(self.cfg.get("dir_cache_ttl_s", 5.0) * 1000))

    def learn(self, payload: dict) -> dict:
        chosen = payload.get("chosenDir")
        if not is_safe_dir_name(chosen):
            raise UnsafeName("chosenDir is not a valid directory name")
        if not self.dirs.has(chosen):
            raise UnsafeName(f"unknown directory: {chosen!r}")
        ctx = MatchContext.from_payload(payload.get("context") or {})
        phrases = ctx.subject_phrases()
        keys = [norm_key(p) for p in phrases[:3]]
        keys = [k for k in keys if k]
        written = 0
        for key in keys:
            if ctx.site:
                self.store.upsert_alias(key, chosen, ctx.site)
                written += 1
            elif self.store.alias(key, "") is None:
                # No site context (a direct-link download): a global alias is
                # the only thing we can learn.
                self.store.upsert_alias(key, chosen, "")
                written += 1
        # A global alias is seeded only when absent, so a site-specific
        # correction never silently re-points every other site.
        for key in keys[:1]:
            if self.store.alias(key, "") is None:
                self.store.upsert_alias(key, chosen, "")
                written += 1
        if ctx.site:
            self.store.set_host_prior(ctx.site, chosen)
        self.store.add_example(payload.get("context") or {}, chosen,
                               payload.get("autoDir"),
                               bool(payload.get("createdNew")))
        return {"ok": True, "aliases": written, "dir": chosen}

    def mkdir(self, payload: dict) -> dict:
        name = payload.get("name")
        if not is_safe_dir_name(name):
            raise UnsafeName(f"invalid directory name: {name!r}")
        target = self.root / name
        # Belt and braces: the name is already one safe component, but resolve
        # it too so a symlinked collision cannot land outside the root.
        resolved = safe_rel_path(name, root=self.root)
        existed = target.is_dir()
        os.makedirs(resolved, exist_ok=True)
        self.dirs.refresh(force=True)
        return {"ok": True, "dir": name, "created": not existed,
                "etag": self.dirs.etag()}

    def _prove_owned(self, rel_path: str, src, download_id: str) -> None:
        """Refuse unless this router demonstrably created `rel_path`.

        WHY THIS IS NOT OPTIONAL: the library root is a live qBittorrent
        seeding target and /relocate is an `os.rename`. Renaming a torrent
        payload makes the files vanish from qBittorrent's point of view and
        seeding stops. The endpoint validated `toDir` and confined the source
        to the root -- there was never a path escape -- but it moved ANY file
        that happened to exist at the supplied relative path, and that path was
        derived in the extension from the last two components of the browser's
        absolute filename with nothing checking it landed under the library
        root at all.

        Two independent proofs, BOTH required, and the absence of either is a
        refusal (never a warning, never a best-effort move):

          1. IDENTITY. The file's name must be the name of the download this
             `downloadId` refers to, modulo `conflictAction: "uniquify"`'s
             " (1)" suffix. This is what binds the proof to THIS file.
          2. AGE. The file's mtime must be at or after the routing decision.
             A browser writes the file after the decision, so this holds for a
             genuine download; a COMPLETED payload that was already on disk
             predates it.

             Honest limit: an IN-PROGRESS torrent is still writing pieces, so
             its mtime is current and the age test passes vacuously for it.
             Against that shape the identity proof is the only one doing work
             -- which is why identity is not optional and why it binds to the
             file rather than to its folder.

        WHAT THIS DELIBERATELY NO LONGER CHECKS, and why: it used to require
        the file to be sitting in the directory the /match decision NAMED. That
        was never a proof of anything -- any file that happened to be in that
        directory passed, so a `/match` for `innocent.mp4` authorised moving a
        live payload that merely shared the directory -- AND it was wrong for
        every case the correction flow exists to serve:

          * below threshold or a tie: route_core deliberately files into the
            catch-all while /match logs the CANDIDATE dir, so the two always
            disagreed and the picker's whole purpose was dead;
          * the 400 ms timeout: the extension uses its cached local decision
            while the server logged the answer it computed too late;
          * a correction that has already been applied once.

        Name+time is strictly stronger (it binds to the file, not the folder)
        and is true in all of those cases.
        """
        if self.store.routed_file(rel_path) is not None:
            return

        disk_name = rel_path.rsplit("/", 1)[-1]
        route = self.store.route_for_download(download_id)

        # NO ROUTE ROW = NO PROOF. There is deliberately no fallback here.
        #
        # A "the extension says this download is recent and named X" fallback
        # was tried and removed: with no routing decision to check it against
        # there is nothing to verify the claim WITH, so it reduces to trusting
        # the caller -- on the one code path whose entire purpose is to refuse
        # a move it cannot prove. A live torrent payload written in the last
        # hour with a matching name is exactly the shape that gets through.
        #
        # The cost of refusing is small and recoverable: a download that was
        # never routed (the sidecar was unreachable when it started) cannot be
        # auto-undone, and the user moves that one file by hand. The cost of
        # the alternative is a broken seed.
        if route is None:
            # Do NOT guess a cause here. An earlier draft of this message
            # blamed a sidecar restart; the route log is persistent SQLite and
            # log_route commits, so a restart does not lose anything. Shipping
            # a confident wrong diagnosis is worse than naming the real
            # possibilities.
            raise UnsafeName(
                "refusing to move a file this router cannot prove it created: "
                "no routing decision is on record for this download. Either "
                "the sidecar was unreachable when the download started (so "
                "/match never ran for it), no downloadId was sent, or the "
                "route log has been cleared. Move this one file by hand; "
                "downloads routed while the sidecar is reachable can be "
                "corrected normally.")

        expected = str(route.get("filename") or "")
        if not expected:
            raise UnsafeName(
                "refusing to move a file this router cannot prove it created "
                "(the recorded route has no filename to identify it by)")
        if not names_match(disk_name, expected):
            raise UnsafeName(
                "refusing to move a file this router cannot prove it created "
                f"(this download was {expected!r}; the file on disk is "
                f"{disk_name!r})")

        try:
            mtime = src.stat().st_mtime
        except OSError as exc:
            raise UnsafeName(f"cannot stat the source: {exc}") from exc
        if mtime < float(route.get("ts") or 0.0) - MTIME_SLACK_S:
            raise UnsafeName(
                "refusing to move a file this router cannot prove it created "
                "(it predates its own routing decision, so it was already on "
                "disk before this download was routed — quite possibly a "
                "torrent payload)")

        self.store.record_routed_file(rel_path, download_id,
                                      rel_path.split("/", 1)[0])

    def relocate(self, payload: dict) -> dict:
        to_dir = payload.get("toDir")
        if not is_safe_dir_name(to_dir):
            raise UnsafeName(f"invalid target directory: {to_dir!r}")
        if not self.dirs.has(to_dir):
            raise UnsafeName(f"unknown directory: {to_dir!r}")
        rel_path = payload.get("fromRelPath")
        src = safe_rel_path(rel_path, root=self.root)
        if not src.is_file():
            raise FileNotFoundError(f"not a file: {rel_path!r}")
        # Fail closed BEFORE anything is moved.
        self._prove_owned(str(rel_path), src,
                          str(payload.get("downloadId") or ""))
        dest_dir = safe_rel_path(to_dir, root=self.root)
        dest = dest_dir / src.name
        if dest == src:
            return {"ok": True, "moved": False, "dir": to_dir}
        if dest.exists():
            # Never overwrite. Uniquify exactly like the download path does.
            stem, dot, ext = src.name.rpartition(".")
            base, suffix = (stem, "." + ext) if dot else (src.name, "")
            n = 1
            while dest.exists() and n < 1000:
                dest = dest_dir / f"{base} ({n}){suffix}"
                n += 1
            if dest.exists():
                raise FileExistsError("could not find a free destination name")
        os.rename(src, dest)
        new_rel = str(dest.relative_to(self.root))
        # Follow the file, so a second correction still has provenance for it.
        self.store.move_routed_file(str(rel_path), new_rel, to_dir)
        self.files.refresh(force=True)
        return {"ok": True, "moved": True, "dir": to_dir, "relPath": new_rel}

    def known_dirs(self) -> set:
        """The directories anything may write into: the index plus the
        catch-all, which is created on demand and so may not be indexed yet."""
        return self.dirs.name_set() | {self.cfg.other_dir}

    def fetch(self, payload: dict) -> dict:
        url = payload.get("url")
        dir_name = payload.get("dir") or self.cfg.other_dir
        # `known_dirs=None` here meant /fetch bypassed the allowlist that every
        # other write path enforces: any syntactically valid name was accepted
        # and yt-dlp silently created the directory. /mkdir is the only
        # endpoint allowed to bring a directory into existence.
        job = self.fetcher.submit(url, dir_name, known_dirs=self.known_dirs())
        # Metadata only: the directory name is a subject in the user's private
        # library, and this line goes to the user journal (see log()'s own
        # docstring, which this call used to contradict).
        log("fetch", job=job.id, state=job.state)
        return {"ok": True, **job.as_dict()}

    def fetch_status(self, job_id: str):
        return self.fetcher.status(job_id)

    def recent(self, limit: int = 50) -> dict:
        return {"ok": True, "routes": self.store.recent_routes(limit)}


def make_handler(app: App, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = SERVER_VERSION
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # noqa: A003 — we log structurally
            pass

        # --- plumbing ------------------------------------------------------ #
        def _send(self, code: int, obj=None, extra_headers=None):
            raw = b"" if obj is None else json.dumps(
                obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            # This service must never be embedded or fetched cross-origin.
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if raw and self.command != "HEAD":
                self.wfile.write(raw)

        def _host_ok(self) -> bool:
            host = self.headers.get("Host", "")
            hostname = host.split("]")[0] + "]" if host.startswith("[") \
                else host.split(":")[0]
            return hostname in _ALLOWED_HOST_HEADERS

        def _auth_ok(self) -> bool:
            hdr = self.headers.get("Authorization", "")
            if not hdr.startswith("Bearer "):
                return False
            try:
                return secrets.compare_digest(hdr[len("Bearer "):].strip(),
                                              token)
            except TypeError:
                # `compare_digest` raises on a non-ASCII str. This runs inside
                # _guard, OUTSIDE _run's error mapping, so the exception closed
                # the connection with no response at all and printed a
                # traceback into the journal -- fail-closed, but a violation of
                # this file's "a bad request must never 500" contract, and
                # trivially reachable from any page that can reach the socket.
                return False

        def _guard(self) -> bool:
            if not self._host_ok():
                self._send(403, {"ok": False, "error": "bad_host"})
                return False
            if not self._auth_ok():
                self._send(401, {"ok": False, "error": "unauthorized"})
                return False
            return True

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None, "bad_length"
            if length <= 0 or length > MAX_BODY:
                return None, "bad_length"
            try:
                obj = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return None, "bad_json"
            if not isinstance(obj, dict):
                return None, "bad_json"
            return obj, None

        def _need_config(self) -> bool:
            if app.configured:
                return True
            self._send(503, {"ok": False, "error": "library_root not configured"})
            return False

        def _run(self, fn, *args):
            """Shared error mapping: a bad request must never 500."""
            try:
                self._send(200, fn(*args))
            except UnsafeName as exc:
                self._send(400, {"ok": False, "error": "unsafe", "detail": str(exc)})
            except (ValueError, TypeError) as exc:
                self._send(400, {"ok": False, "error": "bad_request",
                                 "detail": str(exc)})
            except FileNotFoundError as exc:
                self._send(404, {"ok": False, "error": "not_found",
                                 "detail": str(exc)})
            except (FileExistsError, RuntimeError) as exc:
                self._send(409, {"ok": False, "error": "conflict",
                                 "detail": str(exc)})
            except OSError as exc:
                self._send(500, {"ok": False, "error": "io", "detail": str(exc)})

        # --- routes -------------------------------------------------------- #
        def do_GET(self):
            if not self._guard():
                return
            split = urlsplit(self.path)
            path = split.path
            if path == "/healthz":
                self._send(200, app.healthz())
                return
            if path == "/dirs":
                if not self._need_config():
                    return
                snap = app.dirs_snapshot()
                etag = f'"{snap["etag"]}"'
                if self.headers.get("If-None-Match") == etag:
                    self._send(304, None, {"ETag": etag})
                    return
                self._send(200, snap, {"ETag": etag, "Cache-Control": "no-cache"})
                return
            if path.startswith("/fetch/"):
                if not self._need_config():
                    return
                job = app.fetch_status(path[len("/fetch/"):])
                if job is None:
                    self._send(404, {"ok": False, "error": "no_such_job"})
                else:
                    self._send(200, {"ok": True, **job})
                return
            if path == "/log":
                try:
                    limit = int((parse_qs(split.query).get("limit") or ["50"])[0])
                except ValueError:
                    limit = 50
                self._send(200, app.recent(max(1, min(limit, 500))))
                return
            self._send(404, {"ok": False, "error": "no_such_endpoint"})

        def do_POST(self):
            if not self._guard():
                return
            path = urlsplit(self.path).path
            routes = {"/match": app.match, "/learn": app.learn,
                      "/mkdir": app.mkdir, "/relocate": app.relocate,
                      "/fetch": app.fetch}
            fn = routes.get(path)
            if fn is None:
                self._send(404, {"ok": False, "error": "no_such_endpoint"})
                return
            if not self._need_config():
                return
            body, err = self._body()
            if err:
                self._send(400, {"ok": False, "error": err})
                return
            self._run(fn, body)

        # `_send` already suppresses the body for HEAD, but without this method
        # BaseHTTPRequestHandler answered HEAD with an HTML 501 that never
        # passed _guard -- no bypass, just an inconsistent shape that made the
        # HEAD branch in _send unreachable.
        def do_HEAD(self):
            self.do_GET()

        def _method_not_allowed(self):
            if not self._guard():
                return
            self._send(405, {"ok": False, "error": "method_not_allowed"},
                       {"Allow": "GET, HEAD, POST"})

        do_OPTIONS = _method_not_allowed
        do_PUT = _method_not_allowed
        do_DELETE = _method_not_allowed
        do_PATCH = _method_not_allowed

    return Handler


def build_server(host: str, port: int, app: App, token: str) -> ThreadingHTTPServer:
    """Construct the server. REFUSES any non-loopback bind address.

    There is no override: this process holds a token that grants filesystem
    writes under the library root, so binding it to a routable interface is
    never a legitimate configuration.
    """
    if host not in _LOOPBACK:
        raise ValueError(f"refusing non-loopback bind address: {host!r} "
                         f"(allowed: {sorted(_LOOPBACK)})")
    server = ThreadingHTTPServer((host, port), make_handler(app, token))
    server.daemon_threads = True
    return server


def main(argv=None) -> int:
    # A malformed config.toml used to raise out of here, and the unit is
    # `Restart=always` with `RestartSec=10` — a silent 6/min restart loop with
    # nothing listening and no way to see why except the journal. Degrade to
    # the same "unconfigured" state an un-set-up host already has: /healthz
    # answers and names the problem, routing endpoints return 503.
    cfg, cfg_error = config_mod.load_degraded()
    token = config_mod.load_or_create_token(cfg.token_file)
    try:
        app = App(cfg, config_error=cfg_error)
    except Exception as exc:  # noqa: BLE001
        # Constructing the App opens the SQLite store and runs migrations. A
        # corrupt or half-migrated database would otherwise raise out of here
        # into the same `Restart=always` loop the config path already handles.
        # Degrade the same way: serve, and say why.
        log("startup_error", kind=type(exc).__name__)
        blank, _ = config_mod.load_degraded(Path(os.devnull))
        blank.data["library_root"] = ""
        blank.state_dir, blank.token_file = cfg.state_dir, cfg.token_file
        blank.data["host"], blank.data["port"] = cfg.host, cfg.port
        app = App(blank, store=Store(":memory:"),
                  config_error=f"{cfg_error or ''} {type(exc).__name__}: "
                               f"{exc}".strip())
    server = build_server(cfg.host, cfg.port, app, token)
    log("listening", host=cfg.host, port=cfg.port,
        token_file=str(cfg.token_file), configured=app.configured,
        dirs=(len(app.dirs.entries()) if app.configured else 0))
    if cfg_error:
        log("config_error", detail=cfg_error.replace(" ", "_"))
        log("warning", msg="serving 503 until config.toml is fixed")
    elif not app.configured:
        log("warning", msg="library_root unset — routing endpoints return 503")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
