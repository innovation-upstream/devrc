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
    POST /dedupe           confirm a duplicate by size + bounded head/tail hash
    POST /discard          remove a PROVEN duplicate this router just wrote
    POST /fetch            yt-dlp job for a stream URL
    GET  /fetch/<id>       job status
    GET  /have?url=        source-URL ledger lookup ("already downloaded?")
    GET  /log              recent routes

Env: DL_ROUTER_HOST, DL_ROUTER_PORT, DL_ROUTER_CONFIG, DL_ROUTER_STATE_DIR,
     DL_ROUTER_TOKEN_FILE, DL_ROUTER_LIBRARY_ROOT (see config.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as config_mod  # noqa: E402
from dedupe import (  # noqa: E402
    HashCache, confirm_duplicate, files_identical, looks_unfilled,
    sample_file,
)
from dirindex import DirIndex, FileIndex  # noqa: E402
from dirkinds import DirKinds  # noqa: E402
from fetcher import Fetcher  # noqa: E402
from matcher import (  # noqa: E402
    KIND_CATEGORY, KIND_PERFORMER, KIND_UNKNOWN, KINDS, MatchContext, Matcher,
    content_tokens,
    find_duplicate, host_of, identity_signals, norm_key, passes_fuzzy_guard,
    suspicious_alias_key, thread_slug, title_subject,
)
from safety import (  # noqa: E402
    UnsafeName, is_safe_dir_name, names_match, safe_rel_path,
)
from store import Store, source_url_key  # noqa: E402

SERVER_VERSION = "dl-router/1"
MAX_BODY = 256 * 1024

# How much older than its own routing decision a file may be and still be
# accepted as "this router created it" (filesystem timestamp granularity, not
# clock skew — both come from the same machine). A torrent payload predates its
# would-be routing decision by hours or days, so a few seconds costs nothing.
MTIME_SLACK_S = 5.0

# How long /discard will spend proving the two files are byte-for-byte
# identical before giving up. It reads BOTH files in full -- the only thing
# that is actually a proof -- and a timeout is a REFUSAL, never an all-clear.
# Generous because the user clicked a button and is waiting; the extension's
# own /discard timeout must exceed it.
DISCARD_VERIFY_TIMEOUT_S = 180.0

# How recently a download must have been routed for /discard to delete its
# file. /relocate has no such window -- correcting where a file sits is
# reversible and a stale correction costs nothing -- but a DELETE is not, so it
# is additionally scoped to "this router just wrote it". The dedupe toast is
# answered within seconds; an hour is generous and still refuses a downloadId
# replayed against a path that has since come to hold something else.
DISCARD_MAX_ROUTE_AGE_S = 3600.0

# Where a discarded duplicate goes. Hidden, so both index scans skip it (they
# already drop dot-prefixed names), on the same filesystem as the library so
# the move is an atomic rename rather than a copy, and inspectable with `ls`.
TRASH_DIR = ".dl-router-trash"

# How many tags a single confirmation may turn into aliases, for a CATEGORY
# directory. A tag list can hold 64 entries; the user confirmed one directory,
# not sixty-four synonyms for it, and the first few are the most specific (site
# rules and JSON-LD are pushed to the front of the list by the capture script).
MAX_LEARNED_TAGS = 3
# A title-derived subject longer than this is a sentence, not a name.
MAX_TITLE_SUBJECT_TOKENS = 5

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

        self._kinds_file: DirKinds | None = None
        self._kinds_stamp = None
        # Bounded head+tail digests, keyed on (path, size, mtime). Lives on the
        # App rather than on FileIndex so it survives an index refresh: the
        # library file a size collision compares against is the SAME file on
        # the next download into that bucket, and re-hashing it every time is
        # the cost this exists to remove.
        self.hashes = HashCache()
        # /discard is serialised. Two concurrent discards of one PAIR each
        # proved the bytes survived in the other, and both won -- reproduced,
        # and unrecoverable under `delete_mode = "unlink"`. It needs only two
        # duplicate toasts open at once. In-process only; the cross-process
        # half is the atomic route-row consumption in `record_discard`.
        self._discard_lock = threading.RLock()

    # --- helpers ----------------------------------------------------------- #
    @property
    def configured(self) -> bool:
        return self.root is not None and self.dirs is not None

    def dir_kinds(self) -> DirKinds:
        """The resolved directory classification.

        The human TOML is re-parsed only when its mtime/size changes -- this is
        on the /match path and /match has a 400 ms budget before the extension
        gives up and uses its cached decision. The picker-assigned kinds come
        from SQLite and are read every time, because /mkdir writes one and the
        very next call must see it.
        """
        path = Path(self.cfg.dirs_file)
        try:
            st = path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        if self._kinds_file is None or stamp != self._kinds_stamp:
            self._kinds_file = DirKinds.load(path)
            self._kinds_stamp = stamp
        overlay = self.store.dir_kind_map()
        if not overlay:
            return self._kinds_file
        merged = dict(overlay)
        # The human file wins: it is the one the operator reviewed.
        merged.update(self._kinds_file.as_map())
        return DirKinds(merged, path=path, present=self._kinds_file.present,
                        error=self._kinds_file.error)

    def matcher(self) -> Matcher:
        return Matcher(self.dirs.entries(), self.store.alias_map(),
                       threshold=self.cfg.auto_threshold,
                       tie_margin=self.cfg.tie_margin,
                       other_dir=self.cfg.other_dir,
                       dir_kinds=self.dir_kinds().as_map())

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
            names = self.dirs.names()
            out["dirs"] = len(names)
            out["aliases"] = self.store.alias_count()
            # The snapshot hash, NOT DirIndex's names-only etag: two different
            # values under one name, in the subsystem whose last bug was an
            # etag that did not cover what it labelled.
            out["etag"] = self.dirs_snapshot()["etag"]
            # Unclassified directories never auto-file, so this count is the
            # single most useful number for "why is it asking every time?".
            kinds = self.dir_kinds()
            out["dirKinds"] = kinds.counts(names)
            out["dirsFile"] = {"path": str(kinds.path or ""),
                               "present": kinds.present}
            if kinds.error:
                out["dirsFile"]["error"] = kinds.error
        return out

    def dirs_snapshot(self) -> dict:
        snap = self.dirs.snapshot()
        # The kind travels WITH each directory: the extension's cached
        # fallback matcher has to apply the same auto-file gate as the sidecar,
        # or a sidecar timeout would auto-file into a category directory that
        # the sidecar itself would have asked about.
        kinds = self.dir_kinds()
        for entry in snap.get("dirs", ()):
            entry["kind"] = kinds.kind(entry.get("name", ""))
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
        # THE ETAG COVERS THE WHOLE SNAPSHOT, not just the directory names.
        #
        # DirIndex's etag hashes the names it scanned, which is all it can
        # know about. But the extension caches this ENTIRE payload and revalidates
        # with `If-None-Match`, so anything else in here that changes without a
        # directory being added or removed -- an alias learned, a kind edited
        # into dirs.toml -- produced a 304 and a permanently stale cache. The
        # kinds made that load-bearing: the cached fallback would keep
        # auto-filing into a directory the operator had just reclassified as a
        # category, and only when the sidecar was unreachable, which is exactly
        # when nobody is watching.
        snap["etag"] = hashlib.sha256(
            json.dumps(snap, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        # PER-DIRECTORY ITEM COUNTS, SERVED BUT DELIBERATELY OUTSIDE THE ETAG.
        #
        # This line is below the hash on purpose. The alternative -- folding the
        # counts in, the way the kinds and aliases above are folded in -- was
        # rejected for three reasons, in increasing order of how much they cost:
        #
        #   1. Churn. A count changes on EVERY completed download, so the etag
        #      would change on every download and the extension's five-minute
        #      revalidation would fetch the whole payload instead of a 304.
        #      Cheap in bytes over loopback; not the real problem.
        #   2. The etag would stop being a cache validator. These counts come
        #      from FileIndex, which is TTL-cached and refreshed opportunistically
        #      -- so the same unchanged library can answer with two different
        #      counts a minute apart. An etag that changes when nothing changed
        #      is not a validator, it is a random number.
        #   3. It would destroy the one thing this etag is FOR. Everything else
        #      in this payload is routing configuration: the directories, their
        #      kinds, the aliases, the threshold. The extension's cached
        #      fallback matcher runs off exactly that, and the comment above
        #      exists because a stale kind kept auto-filing into a directory the
        #      operator had reclassified. "The routing configuration changed" is
        #      a question worth being able to answer -- from `dl-route status`,
        #      whose /healthz etag is the same value, as much as from
        #      If-None-Match. Mixing a per-download counter into it means the
        #      answer is always yes.
        #
        # The cost of this choice, stated honestly: a 304 carries no body, so a
        # revalidating client keeps the counts it already had. That is why the
        # extension asks for the snapshot WITHOUT If-None-Match on the picker
        # path (service_worker.refreshSnapshot's `revalidate` flag) -- the one
        # request whose freshness a human is waiting on -- and keeps
        # revalidating everywhere else. Counts are cosmetic: nothing routes on
        # them, and no code path may start to.
        #
        # A SEPARATE MAP, not a `count` field on each `dirs` entry: the cached
        # fallback matcher iterates `dirs`, and the entries it reads stay
        # byte-identical to what they were before counts existed.
        snap["counts"] = self.file_counts()
        return snap

    def file_counts(self) -> dict:
        """Files per subject directory, for the picker's per-directory count.

        Restricted to directories that are actually in the index: the walk
        tallies whatever top-level names it encountered, and a routing target
        is the only thing worth showing a count for.
        """
        if self.files is None or self.dirs is None:
            return {}
        known = self.dirs.name_set()
        return {name: n for name, n in self.files.dir_counts().items()
                if name in known}

    def match(self, payload: dict) -> dict:
        ctx = MatchContext.from_payload(payload)
        matcher = self.matcher()
        prior = self.store.host_prior(ctx.site) if ctx.site else None
        result = matcher.match(ctx, host_prior=prior)
        # NO HASHING ON THIS PATH, AND THE REASON IS STRUCTURAL, NOT A BUDGET
        # JUDGEMENT CALL.
        #
        # /match runs inside `onDeterminingFilename`, BEFORE Chrome has written
        # a single byte. The file this download will become does not exist yet,
        # so there is nothing here to hash — a head+tail digest needs BOTH
        # files and only one of them is on disk. Even if it were affordable it
        # would be impossible.
        #
        # That settles the 400 ms question by construction. What /match does
        # instead is free: `find_duplicate` reads the size bucket that
        # `FileIndex` already built from stats it already took, which is a dict
        # lookup. The answer is a POSSIBLE duplicate, labelled by `kind`, plus
        # the same-size `candidates` the confirmation step will hash.
        #
        # Honest limit, and it is why the confirmation is not optional: at this
        # point `ctx.size` is the DownloadItem's `totalBytes`, which is 0
        # whenever the response has no Content-Length. So the size signal is
        # OPPORTUNISTIC here and AUTHORITATIVE in `dedupe_check`, which stats
        # the finished file. Nothing routes on either — dedupe is a warning.
        result.dup = find_duplicate(self.files, result.dir, ctx.filename,
                                    ctx.size)
        # `downloadId` is the browser's DownloadItem id. Recording it here is
        # what later lets /relocate prove a file under the library root was put
        # there by this router and not by qBittorrent (see `_prove_owned`).
        download_id = str(payload.get("downloadId") or "")
        self.store.log_route(url=ctx.url, site=ctx.site, filename=ctx.filename,
                             dir_name=result.dir, confidence=result.confidence,
                             reason=result.reason, auto=result.auto,
                             dup=result.dup,
                             download_id=download_id)
        # THE SOURCE-URL LEDGER. Groundwork for "already have this" on a page,
        # written here because /match is the one place every routed download
        # passes through. Best-effort by design: a download with no usable
        # source URL records nothing and must not fail the match it is riding
        # on, which is a decision the browser is blocking on.
        #
        # `source_key` WHEN IT IS USABLE. An embedded player's media URL is
        # signed and rotates in place, so it names the request rather than the
        # asset; the extension sends the embed page URL as a stable name
        # instead (see MatchContext.source_key). The test is `source_url_key`
        # itself rather than truthiness: a non-empty but unusable key (a
        # `blob:`, a typo) would otherwise write NOTHING at all, silently
        # dropping the ledger row that the ordinary URL would have produced.
        ledger_url = ctx.source_key if source_url_key(ctx.source_key) \
            else ctx.url
        try:
            self.store.record_source_url(ledger_url,
                                         site=ctx.site,
                                         dir_name=result.dir,
                                         download_id=download_id)
        except sqlite3.Error as exc:
            log("ledger-write-failed", error=exc.__class__.__name__)
        return result.as_dict(ttl_ms=int(self.cfg.get("dir_cache_ttl_s", 5.0) * 1000))

    def have_url(self, url: str) -> dict:
        """GET /have?url= — the ledger lookup. NO UI consumes this yet.

        It is the read half of the v5 ledger, shipped with it so the table has
        a caller and a test rather than accumulating rows nothing can read.
        """
        row = self.store.source_url(url)
        if not row:
            return {"ok": True, "have": False}
        return {"ok": True, "have": True, "dir": row.get("dir") or "",
                "relPath": row.get("rel_path") or "",
                "hits": int(row.get("hits") or 0),
                "firstTs": row.get("first_ts"), "lastTs": row.get("last_ts")}

    def _rel_to_root(self, path, fallback: str = "") -> str:
        """`path` (already contained) as a `/`-joined path relative to the root.

        WHY THIS IS NOT `path.relative_to(self.root)`. `safe_rel_path` returns
        a FULLY RESOLVED path, while `cfg.library_root` is only
        `Path(raw).expanduser()` -- not resolved. So on the very common NAS
        layout where the library root is a symlink (`~/Media` ->
        `/mnt/pool/Media`), `relative_to` raises ValueError for every file: the
        resolved path genuinely is not under the unresolved root. That would
        have made the whole dedupe feature raise a 400 on every call, and
        `confirmDuplicate` swallows failures, so it would have been silently
        dead with no diagnostic anywhere.

        Both sides are resolved here, and the resolution is done per call
        rather than cached because the root can be re-pointed under a running
        sidecar. It is one `stat`-ish syscall on a path already in the cache.
        """
        try:
            root = Path(self.root).resolve()
        except OSError:
            root = Path(self.root)
        try:
            return str(Path(path).relative_to(root)).replace(os.sep, "/")
        except ValueError:
            # `safe_rel_path` already proved containment, so this is only
            # reachable if the root moved between the two calls. Fall back to
            # the caller's own string rather than raising a confusing
            # "not in the subpath of" out of a dedupe check.
            return str(fallback or "").replace(os.sep, "/")

    # --- dedupe confirmation ------------------------------------------------ #
    def dedupe_check(self, payload: dict) -> dict:
        """POST /dedupe — the AUTHORITATIVE duplicate answer, after the write.

        WHERE THE HASHING HAPPENS, AND WHY IT IS HERE. See the note in
        `match()`: at /match time the downloaded file does not exist, so the
        only place both files are on disk is after completion. That also puts
        every byte of I/O outside the 400 ms budget that /match must respect —
        this endpoint is called from `downloads.onChanged`, where nothing is
        waiting on it and a slow answer costs a late toast, not a misfiled
        download.

        The cost ladder, in order:
          * one `stat` of the finished file (its real size — `totalBytes` at
            /match time is frequently 0);
          * one dict lookup in the size bucket the index already holds;
          * ONLY on a collision, a bounded head+tail digest of the new file and
            of up to `MAX_DUP_CANDIDATES` same-size library files, cached by
            (path, size, mtime) so a library file is hashed once, not once per
            colliding download.

        A name-only hit is reported as a `signal` and never as a confirmed
        duplicate: different byte counts are definitively different files, so
        there is nothing a hash could confirm.
        """
        rel_path = payload.get("relPath")
        # Containment first, exactly like every other path-taking endpoint. A
        # read is less dangerous than a move, but "read any file the sidecar
        # user can read" is still not something this endpoint may offer.
        src = safe_rel_path(rel_path, root=self.root)
        if not src.is_file():
            raise FileNotFoundError(f"not a file: {rel_path!r}")
        rel = self._rel_to_root(src, rel_path)
        try:
            size = src.stat().st_size
        except OSError as exc:
            raise UnsafeName(f"cannot stat the file: {exc}") from exc
        if size <= 0:
            # A zero-byte file matches every other zero-byte file. That is true
            # and useless, and it is the shape a failed download leaves behind.
            return {"ok": True, "duplicate": False, "relPath": rel,
                    "reason": "the file is empty, which identifies nothing"}
        target_dir = rel.split("/", 1)[0] if "/" in rel else ""
        signal = find_duplicate(self.files, target_dir, src.name, size,
                                exclude_rel=rel)
        if not signal:
            return {"ok": True, "duplicate": False, "relPath": rel,
                    "reason": "no other file in the library has this size"}
        candidates = list(signal.get("candidates") or ())
        if not candidates:
            # A name-only hit: same stem, different length. Kept as a signal
            # because a re-download under the same name is worth saying, and
            # labelled so nothing can mistake it for a confirmation.
            return {"ok": True, "duplicate": False, "relPath": rel,
                    "signal": signal,
                    "reason": "same name but a different size, so not the "
                              "same file"}
        match, why = confirm_duplicate(self.root, rel, candidates, self.hashes)
        if match is None:
            return {"ok": True, "duplicate": False, "relPath": rel,
                    "signal": signal, "candidates": len(candidates),
                    "reason": why}
        in_target = match.split("/", 1)[0] == target_dir
        return {"ok": True, "duplicate": True, "relPath": rel,
                "dupRelPath": match, "size": size,
                "kind": ("name+size+hash" if signal.get("kind") == "name+size"
                         else "size+hash"),
                "where": "target-dir" if in_target else "library",
                "candidates": len(candidates), "reason": why}

    def learn(self, payload: dict) -> dict:
        """Persist a correction — ONLY the discriminating signal.

        WHAT THIS REPLACED, and why. The first version wrote an alias for the
        first three SUBJECT PHRASES of the context, plus one at GLOBAL scope.
        On the first forum download that produced four aliases, of which three
        were wrong: a forum section name, and two other posters' usernames --
        one of them global, so it would have auto-filed anything carrying that
        username into a stranger's directory at alias confidence. They were
        deleted by hand; nothing in the system had surfaced them.

        The rule is now keyed on WHAT the directory is:

          * identity signals (Discord channel id, forum thread slug) are
            learned for ANY kind. They are structural, come from the URL, and
            cannot be contaminated by another user's content.
          * a subject name in the page TITLE is learned for a performer (or a
            not-yet-classified) directory. Never for a category.
          * a TAG is learned only for a CATEGORY directory, where a tag is the
            legitimate signal -- and then only from an explicit confirmation,
            capped, site-scoped, and screened by `suspicious_alias_key`.

        NO ALIAS IS EVER LEARNED AT GLOBAL SCOPE. A global alias applies to
        every site at once, which is the largest blast radius the store has and
        the least evidence supports it. `dl-route alias set --site '*'` still
        exists for a deliberate one.
        """
        chosen = payload.get("chosenDir")
        if not is_safe_dir_name(chosen):
            raise UnsafeName("chosenDir is not a valid directory name")
        if not self.dirs.has(chosen):
            raise UnsafeName(f"unknown directory: {chosen!r}")
        ctx = MatchContext.from_payload(payload.get("context") or {})
        kind = self.dir_kinds().kind(chosen)
        confirmed = bool(payload.get("confirmed"))
        dir_names = sorted(self.dirs.name_set())
        written: list = []
        skipped: list = []

        def write(key, site, source, evidence):
            self.store.upsert_alias(key, chosen, site, source=source,
                                    evidence=str(evidence)[:200])
            written.append({"key": key, "site": site, "source": source})

        spread_map = self.store.phrase_dir_spread(
            other_dir=self.cfg.other_dir)

        def screen(phrase, key, source, site):
            # AN EXPLICIT CORRECTION ALWAYS WINS.
            #
            # If a row already exists for this (key, site), the operator is
            # RE-POINTING something the router previously learned, and the
            # screen must not stand in the way. It did: the chrome-spread
            # measure counts the operator's OWN routing history, so correcting
            # the same thread to a second directory looked exactly like a
            # phrase "seen on 2 different directories" -- the fix was refused,
            # the original wrong alias survived, and the next download from
            # that thread still auto-filed into the wrong directory at 1.00. A
            # router that overrules the operator on the second correction is
            # worse than one that never learned anything.
            if self.store.alias(key, site) is not None:
                return True
            why = suspicious_alias_key(phrase, key=key, dir_names=dir_names,
                                       site=site,
                                       spread=spread_map.get(norm_key(phrase), 0))
            if why:
                # `first` is what stops the extension notifying on every
                # correction for a PERMANENTLY refused candidate -- see
                # Store.record_screened. The refusal is durable state, not an
                # event, and `dl-route alias review` shows it permanently.
                first = self.store.record_screened(key, site, chosen, source,
                                                   why)
                skipped.append({"key": key, "source": source, "why": why,
                                "first": first})
            return why is None

        # THE CATCH-ALL LEARNS NOTHING.
        #
        # Sending a download to the catch-all is the user saying "not any of
        # these", which is the absence of a subject, not evidence of one. It
        # cannot auto-file (the directory is unclassified), but a 1.00 identity
        # alias pointing at it would make the catch-all the permanent top
        # candidate in the picker for that channel or thread -- turning one
        # shrug into a standing recommendation.
        if chosen == self.cfg.other_dir:
            self.store.add_example(payload.get("context") or {}, chosen,
                                   payload.get("autoDir"),
                                   bool(payload.get("createdNew")))
            return {"ok": True, "aliases": 0, "written": [],
                    # SOURCE STRING IS A CROSS-LANGUAGE CONTRACT: the
                    # extension's reportNothingLearned filters on this literal
                    # so a routine catch-all filing never notifies. Pinned on
                    # both sides (test_the_catch_all_learns_nothing,
                    # service_worker.test.mjs).
                    "skipped": [{"key": "", "source": "catch-all",
                                 "first": False,
                                 "why": "the catch-all directory is the "
                                        "absence of a subject, not one"}],
                    "dir": chosen, "kind": kind}

        # 1. Identity signals — every kind, always. Site-scoped by construction,
        #    and SCREENED: a badly derived identity is still an identity as far
        #    as the store is concerned, and it lands at 1.00 with auto-file
        #    rather than at 0.85 without. This is the one row that most needed
        #    checking and was the only one exempt from the check.
        for sig in identity_signals(ctx):
            if screen(sig.evidence, sig.key, sig.kind, sig.site):
                write(sig.key, sig.site, sig.kind, sig.evidence)

        if kind == KIND_CATEGORY:
            # 2. Tags — the legitimate signal for a category, and ONLY there.
            if confirmed and ctx.site:
                learned = 0
                for tag in ctx.tags:
                    if learned >= MAX_LEARNED_TAGS:
                        break
                    key = norm_key(tag)
                    if not key:
                        continue
                    # SCREEN FIRST, THEN CAP. Capping the input list meant a
                    # page whose first three tags were junk consumed the whole
                    # budget and a legitimate fourth was never even considered.
                    if screen(tag, key, "tag", ctx.site):
                        write(key, ctx.site, "tag", tag)
                        learned += 1
            elif ctx.tags:
                skipped.append({
                    "key": "", "source": "tag", "first": False,
                    "why": "a tag is learned only from an explicit "
                           "confirmation on a site-scoped context"})
        elif kind == KIND_PERFORMER:
            # 3. The subject inside the page title. PERFORMER ONLY -- an
            #    unclassified directory learns identity signals and nothing
            #    else, which is what the docs have always said. Learning weak
            #    title aliases for it meant a pile of dormant rows that would
            #    all activate at once the moment it was classified.
            slugs = ctx.thread_slugs()
            own_slug = thread_slug(ctx.page_url) or (slugs[0] if slugs else "")
            for title, site, slug in (
                    (ctx.title, ctx.site, own_slug),
                    (ctx.referrer_title, host_of(ctx.referrer_url),
                     thread_slug(ctx.referrer_url))):
                subject = title_subject(title, site, slug)
                if not subject or not site:
                    continue
                toks = content_tokens(subject)
                if not passes_fuzzy_guard(toks) or len(toks) > MAX_TITLE_SUBJECT_TOKENS:
                    continue
                key = norm_key(subject)
                if key and screen(subject, key, "title-subject", site):
                    write(key, site, "title-subject", subject)

        if skipped:
            # AN OVER-STRICT SCREEN MUST NOT BE SILENT. The response carries the
            # detail; this line is what makes it visible without one. Reason
            # CODES only -- the keys themselves are the operator's private
            # library, and this journal is world-readable (see log()).
            log("learn_screened", n=len(skipped),
                sources="|".join(sorted({s["source"] for s in skipped})))

        if ctx.site:
            self.store.set_host_prior(ctx.site, chosen)
        self.store.add_example(payload.get("context") or {}, chosen,
                               payload.get("autoDir"),
                               bool(payload.get("createdNew")))
        return {"ok": True, "aliases": len(written), "written": written,
                "skipped": skipped, "dir": chosen, "kind": kind}

    def mkdir(self, payload: dict) -> dict:
        name = payload.get("name")
        if not is_safe_dir_name(name):
            raise UnsafeName(f"invalid directory name: {name!r}")
        # The picker asks which kind a NEW directory is, because an
        # unclassified one never auto-files -- creating one without an answer
        # would quietly produce a directory that always interrupts.
        kind = payload.get("kind")
        if kind is not None and kind not in KINDS:
            raise UnsafeName(f"invalid directory kind: {kind!r} "
                             f"(expected one of {sorted(KINDS)})")
        target = self.root / name
        # Belt and braces: the name is already one safe component, but resolve
        # it too so a symlinked collision cannot land outside the root.
        resolved = safe_rel_path(name, root=self.root)
        existed = target.is_dir()
        os.makedirs(resolved, exist_ok=True)
        if kind in KINDS:
            self.store.set_dir_kind(name, kind, source="picker")
        self.dirs.refresh(force=True)
        return {"ok": True, "dir": name, "created": not existed,
                "kind": self.dir_kinds().kind(name),
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
        self._prove_created(rel_path, src, download_id)
        self.store.record_routed_file(rel_path, download_id,
                                      rel_path.split("/", 1)[0])

    def _prove_created(self, rel_path: str, src, download_id: str,
                       *, max_route_age_s: float | None = None) -> dict:
        """The identity+age proof itself. Returns the route row it proved from.

        Split out of `_prove_owned` so /discard can require the SAME evidence
        without inheriting the `routed_files` short-circuit. That ledger is a
        standing claim ("this router created this path"), which is the right
        answer for a reversible rename and the wrong one for a delete: a claim
        recorded weeks ago says nothing about the file sitting at that path
        now. /discard therefore proves it from scratch every time, and
        additionally bounds how old the routing decision may be.
        """
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
        route_ts = float(route.get("ts") or 0.0)
        if mtime < route_ts - MTIME_SLACK_S:
            raise UnsafeName(
                "refusing to move a file this router cannot prove it created "
                "(it predates its own routing decision, so it was already on "
                "disk before this download was routed — quite possibly a "
                "torrent payload)")
        if max_route_age_s is not None:
            age = float(self.clock()) - route_ts
            if age > float(max_route_age_s):
                raise UnsafeName(
                    "refusing to act on a routing decision this old "
                    f"({int(age)}s; the limit is {int(max_route_age_s)}s). "
                    "Only a file this router has JUST written may be "
                    "discarded.")
        return route

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
        # The ledger has to follow it too: it answers "you already have this,
        # in <dir>", and after a correction the router's own guess is the wrong
        # answer to give.
        try:
            self.store.set_source_url_dir(
                str(payload.get("downloadId") or ""), to_dir, new_rel)
        except sqlite3.Error as exc:
            log("ledger-update-failed", error=exc.__class__.__name__)
        self.files.refresh(force=True)
        return {"ok": True, "moved": True, "dir": to_dir, "relPath": new_rel}

    @staticmethod
    def _looks_preallocated(st) -> bool:
        """True iff the file has fewer blocks allocated than its size claims.

        qBittorrent PREALLOCATES: a payload has its full final `st_size` from
        the moment it is created, and fills in pieces afterwards. The unwritten
        middle is a hole, so `st_blocks` is short. That is the cheap,
        deterministic, credential-free way to recognise "this length is a
        promise, not content".

        Honest limits, both in the fail-safe direction: a filesystem with
        transparent compression (btrfs/zfs) can report fewer blocks for a
        genuinely complete file, so this can REFUSE a legitimate discard —
        which costs the operator one manual delete. And a torrent that has
        filled every piece it preallocated is no longer sparse, so this alone
        does not prove completeness; that is what the qBittorrent check is for.
        """
        blocks = getattr(st, "st_blocks", None)
        if blocks is None:
            # No information either way -- and "no information" on a
            # destructive path is a refusal, not an all-clear. It used to
            # return False (i.e. "looks complete"), which is the wrong default
            # for the only check standing between a partial file and a delete.
            return True
        return (int(blocks) * 512) < int(st.st_size)

    def _refuse_incomplete_keep(self, keep_rel: str, keep, st, live,
                                qbt_error) -> None:
        """The kept file must be a COMPLETE file, not a promise of one.

        THE BOUNDED DIGEST CANNOT SEE THIS, and that inverted the whole
        guarantee. qBittorrent preallocates, so an in-progress payload has the
        full `st_size` from creation; if its FIRST and LAST pieces have landed
        — the common case, and the default under "download first and last
        pieces first" — its head+tail digest is byte-identical to a finished
        copy while the middle is still zeros. Reproduced end to end through the
        shipped sequence: /dedupe answered `duplicate: true` and /discard
        trashed the COMPLETE browser copy, keeping the 40%-shaped partial.

        `dedupe.py` justifies the 256 KiB bound precisely because the answer is
        "a WARNING with a keep button, never an automatic destruction" — and
        then /discard reused that same digest as its load-bearing proof. This
        is the check that makes the two consistent.
        """
        if self._looks_preallocated(st):
            raise UnsafeName(
                "refusing to discard: the file it is supposed to duplicate "
                f"({keep_rel!r}) is sparse — it has fewer blocks allocated "
                "than its length claims, which is what an in-progress, "
                "preallocated torrent payload looks like. A bounded digest "
                "cannot tell that apart from a finished file, so it is not "
                "proof these bytes exist anywhere else.")
        # AN ALL-ZERO MID SAMPLE IS AN UNFILLED EXTENT, and this is the check
        # `st_blocks` cannot make. `posix_fallocate` -- what qBittorrent's
        # "pre-allocate disk space for all files" uses -- reserves REAL
        # extents, so the partial has the same size AND the same block count
        # as the finished file. Measured: `_looks_preallocated` returns False
        # for both, and their head+tail digests are identical. The difference
        # only exists in the middle, so the middle has to be read.
        record = sample_file(keep)
        if record is None:
            raise UnsafeName(
                "refusing to discard: the file it is supposed to duplicate "
                "could not be read")
        if looks_unfilled(record):
            raise UnsafeName(
                "refusing to discard: the file it is supposed to duplicate "
                f"({keep_rel!r}) has a 128 KiB run of zeros in the middle, "
                "which is an unfilled extent — a preallocated torrent payload "
                "that has not reached that piece. A finished media file does "
                "not look like this.")
        payload = self._payload_verdict(keep, live, qbt_error)
        if payload is not None and not payload.get("complete", False):
            raise UnsafeName(
                "refusing to discard: the file it is supposed to duplicate is "
                f"a torrent payload that is not finished ({payload.get('why')})"
                " — it cannot prove these bytes exist anywhere else")

    def _live_qbt_state(self):
        """Live qBittorrent, derived ONCE per /discard. `(state, error)`.

        `(None, None)` means "not configured" — the callers then fall back to
        the local structural guards. Derived once and threaded through both
        checks because `derive_live_state` lists every torrent AND every
        torrent's files; doing that per-path turned one delete into two full
        sweeps of a ~1000-torrent instance.
        """
        client = self._qbt_client()
        if client is None:
            return None, None
        try:
            import backfill as backfill_mod
            return backfill_mod.derive_live_state(
                client, Path(self.root),
                self.cfg.section("qbt").get("host_roots", ())), None
        except Exception as exc:                     # noqa: BLE001
            # Unreachable, auth failure, malformed answer. The caller decides.
            return None, f"{exc.__class__.__name__}: {exc}"

    def _payload_verdict(self, path, live, error):
        """What live qBittorrent says about this path, or None if it has
        nothing to say (not configured).

        Mirrors what `backfill apply` already derives — `derive_live_state` and
        `TorrentIndex.proves_absent` — rather than inventing a second notion of
        "is this a payload". The callers decide what each verdict means, and
        they differ: the SOURCE must be PROVEN not to be a payload; the KEPT
        file is only checked for completeness.
        """
        if error:
            return {"error": error}
        if live is None:
            return None
        host_path = os.path.normpath(str(path))
        torrent = live.index.get(host_path)
        if torrent is not None:
            state = str(torrent.get("state") or "")
            progress = torrent.get("progress")
            try:
                complete = float(progress) >= 1.0
            except (TypeError, ValueError):
                complete = False
            return {"payload": True, "complete": complete,
                    "why": f"state={state or 'unknown'} progress={progress}"}
        if live.proves_absent(host_path):
            return {"payload": False, "complete": True, "why": "not a payload"}
        return {"error": "live qBittorrent state cannot prove this path is "
                         "not a torrent payload"}

    def _qbt_client(self):
        """A qBittorrent client, or None when none is configured.

        Credentials are DELIBERATELY empty on this host (the backfill refuses
        to move anything without them, which is the safe default), so this
        returns None there and the local structural guards carry the weight.
        """
        qbt = self.cfg.section("qbt")
        if not (qbt.get("username") and qbt.get("password")):
            return None
        try:
            import qbt as qbt_mod
            return qbt_mod.QbtClient(
                qbt.get("url", ""), qbt.get("username", ""),
                qbt.get("password", ""),
                transport=qbt_mod.urllib_transport(
                    float(qbt.get("timeout_s", 8.0))))
        except Exception:                            # noqa: BLE001
            return None

    def _refuse_if_payload(self, rel_path, src, st, live, qbt_error) -> None:
        """The file being DISCARDED must not be a live torrent payload.

        WHY THIS AND NOT THE TRASH DEFAULT IS THE SEEDING GUARD: qBittorrent
        seeds by PATH, so renaming a payload into `.dl-router-trash/` breaks
        the torrent exactly as an unlink would. Trash protects the operator's
        bytes; only this protects the seed. `backfill apply` already refuses to
        move anything live qBittorrent cannot prove is not a payload — and that
        is the REVERSIBLE operation, so the asymmetry was backwards.

        Three local, credential-free structural refusals first, because the
        credentials are deliberately empty on this host and a guard that only
        works when they are set is not a guard here:

          * HARDLINKED (`st_nlink > 1`). The standard seeding layout is a
            payload hardlinked into a subject directory. A browser download is
            always nlink 1.
          * A SYMLINK. `safe_rel_path` RESOLVES, so the discard would remove
            the target and leave a dangling link pointing at nothing.
          * SPARSE. A preallocated, partially written payload.

        Then, only if qBittorrent is configured, the live corroboration.
        """
        # The literal path, before `safe_rel_path` resolved it -- NORMALISED.
        #
        # `os.path.islink("…/new.mp4/")` is False (lstat follows the trailing
        # slash) while `Path()` strips it, so a caller that appended one got
        # the symlink's TARGET trashed and a dangling link left behind. This is
        # the same normalisation shape `forget_routed_file` already had to
        # learn; it is cheap to apply everywhere a raw payload string is used.
        literal = os.path.normpath(os.path.join(str(self.root), str(rel_path)))
        if os.path.islink(literal):
            raise UnsafeName(
                "refusing to discard a symlink: the file it points at would be "
                "removed and the link left dangling. Delete the link by hand "
                "if that is what you meant.")
        if int(getattr(st, "st_nlink", 1)) > 1:
            raise UnsafeName(
                "refusing to discard a file with more than one hard link "
                f"(nlink={st.st_nlink}). A browser download has exactly one; "
                "several is the standard layout for a payload hardlinked into "
                "a subject directory, and removing this path would break the "
                "seed even though the bytes survive.")
        if self._looks_preallocated(st):
            raise UnsafeName(
                "refusing to discard a sparse file: it has fewer blocks "
                "allocated than its length claims, which is what a "
                "preallocated, partly written torrent payload looks like.")
        verdict = self._payload_verdict(src, live, qbt_error)
        if verdict is None:
            return          # qBittorrent not configured; local guards stand
        if verdict.get("error"):
            raise UnsafeName(
                "refusing to discard: qBittorrent is configured but could not "
                f"corroborate that this file is not a live payload "
                f"({verdict['error']}). `backfill apply` refuses on the same "
                "evidence for a REVERSIBLE move; this one is not reversible.")
        if verdict.get("payload"):
            raise UnsafeName(
                "refusing to discard: live qBittorrent says this file IS a "
                f"torrent payload ({verdict.get('why')}). Removing it breaks "
                "the seed -- moving it to the trash breaks it just as much, "
                "because qBittorrent seeds by path.")

    def _trash_dest(self, src) -> Path:
        """A free destination inside the trash, CONTAINMENT-CHECKED.

        THE TRASH DESTINATION WAS THE ONE PATH THAT SKIPPED `safe_rel_path`.
        `os.makedirs(exist_ok=True)` FOLLOWS a symlink, so with
        `<root>/.dl-router-trash` symlinked anywhere the `os.rename` landed the
        file outside the library root entirely -- past the containment that is
        proof #1 of the five. Reproduced.

        Two guards, because they answer different questions. A symlinked trash
        directory is refused outright (this router creates that directory
        itself; a symlink there is never a legitimate layout, and refusing is
        clearer than silently resolving it). And the final destination goes
        through `safe_rel_path` like every other path in this file, which is
        what catches anything the first check does not anticipate.
        """
        trash = self.root / TRASH_DIR
        if trash.is_symlink():
            raise UnsafeName(
                f"refusing to use {TRASH_DIR!r}: it is a symlink, so the "
                "discarded file would be written outside the library root")
        os.makedirs(trash, exist_ok=True, mode=0o700)
        if trash.is_symlink() or not trash.is_dir():
            raise UnsafeName(f"{TRASH_DIR!r} is not a directory")
        name = src.name
        stem, dot, ext = name.rpartition(".")
        base, suffix = (stem, "." + ext) if dot else (name, "")
        candidate = name
        n = 1
        while (trash / candidate).exists() and n < 1000:
            candidate = f"{base} ({n}){suffix}"
            n += 1
        if (trash / candidate).exists():
            raise FileExistsError("the trash has no free name for this file")
        dest = safe_rel_path(f"{TRASH_DIR}/{candidate}", root=self.root)
        # `safe_rel_path` resolves, so this also rejects a pre-planted symlink
        # AT the destination name pointing out of the library.
        if dest.parent != Path(self.root).resolve() / TRASH_DIR:
            raise UnsafeName("the trash destination resolved outside the trash")
        return dest

    def discard(self, payload: dict) -> dict:
        """POST /discard — remove the NEWLY DOWNLOADED copy of a duplicate.

        THIS IS THE ONLY DESTRUCTIVE OPERATION IN THIS SUBSYSTEM. The library
        root is a live qBittorrent seeding target, so a wrong answer destroys
        the operator's data AND breaks a torrent. Every ambiguity is a refusal
        with a reason, never a best-effort delete.

        The checks, and what each is actually worth — an earlier revision of
        this docstring called them "five independent proofs", and an audit
        showed three of them were not what they claimed:

          1. CONTAINMENT. Both paths, AND the trash destination, go through
             `safe_rel_path`. The destination used not to, and a symlinked
             trash directory put the file outside the root.
          2. THE SOURCE IS NOT A LIVE PAYLOAD. Local structural refusals that
             need no credentials — a hardlinked file (`st_nlink > 1`, the
             standard seeding layout), a symlink, a sparse/preallocated file —
             plus, when qBittorrent IS configured, the same live corroboration
             `backfill apply` demands. See `_refuse_if_payload`.
          3. IDENTITY + AGE + RECENCY, via `_prove_created`. Worth less than it
             reads: `names_match` tolerates `uniquify`'s ` (N)` by design, so a
             route recorded as `new.mp4` accepts `new (1).mp4` too. What closes
             that is (4) and the CONSUMPTION of the route row — one routing
             decision now authorises at most one discard, because evidence that
             is never consumed is a capability, not a proof.
          4. THE KEPT FILE IS GENUINELY PRE-EXISTING. It must predate the
             routing decision. Without this the two halves of a uniquify pair
             were interchangeable and /discard could remove the ORIGINAL —
             which is exactly what the test for it failed to catch, because its
             two fixtures had different names.
          5. THE DUPLICATION IS RE-PROVEN NOW, and the kept copy must be a
             COMPLETE file. A bounded head+tail digest cannot tell a finished
             file from a preallocated torrent whose first and last pieces have
             landed, so the digest alone was proving the opposite of what it
             claimed. See `_refuse_incomplete_keep`.

        SERIALISED. The whole critical section holds `_discard_lock`: two
        concurrent discards of one PAIR each proved the bytes survived in the
        other, and both won. (In-process only — the CLI is a separate process.
        The route-row consumption in (3) is the cross-process half, and it is
        an atomic INSERT for that reason.)

        And then it does not unlink; it renames into a hidden trash directory.
        BE CLEAR ABOUT WHAT THAT BUYS: it protects the operator's BYTES, and
        nothing else. qBittorrent seeds by PATH, so a rename into
        `.dl-router-trash/` breaks a torrent exactly as an unlink would — which
        is why check (2), not the trash, is the seeding guard.
        `[dedupe] delete_mode = "unlink"` opts out of the byte protection.
        """
        with self._discard_lock:
            return self._discard_locked(payload)

    def _discard_locked(self, payload: dict) -> dict:
        rel_path = payload.get("relPath")
        dup_rel = payload.get("dupRelPath")
        download_id = str(payload.get("downloadId") or "")
        src = safe_rel_path(rel_path, root=self.root)
        keep = safe_rel_path(dup_rel, root=self.root)
        if not src.is_file():
            raise FileNotFoundError(f"not a file: {rel_path!r}")
        if not keep.is_file():
            raise UnsafeName(
                "refusing to discard: the file it is supposed to duplicate "
                f"({dup_rel!r}) is not there, so there is nothing proving "
                "these bytes exist anywhere else")

        rel = self._rel_to_root(src, rel_path)
        keep_rel = self._rel_to_root(keep, dup_rel)

        # THE TRASH IS NOT EVIDENCE. `safe_rel_path` proves containment, not
        # VISIBILITY -- and a file already discarded is one the operator has
        # said they do not want. Accepting it as "the bytes exist elsewhere"
        # let two discards empty the library of a file entirely: the second
        # was proved against the first one's corpse.
        if keep_rel.split("/", 1)[0] == TRASH_DIR:
            raise UnsafeName(
                "refusing to discard: the file it is supposed to duplicate is "
                f"already in {TRASH_DIR!r}, which proves nothing -- that copy "
                "has itself been discarded")

        # SAME FILE = SAME INODE, not merely the same resolved path.
        #
        # `resolve()` collapses symlinks and nothing else, so it says "these
        # are different files" about two HARDLINKS to one inode -- and
        # hardlinking a payload into a subject directory is standard seeding
        # practice, so this is a shape that really occurs in this library.
        # Getting it wrong is not cosmetic: the toast would claim the user
        # holds two copies when they hold one, the delete would free zero
        # bytes, and under `delete_mode = "unlink"` it would remove the very
        # path qBittorrent registered for that torrent (qBittorrent seeds by
        # PATH -- the inode surviving does not save the seed).
        try:
            src_st, keep_st = src.stat(), keep.stat()
            same = (src_st.st_dev, src_st.st_ino) == (keep_st.st_dev,
                                                      keep_st.st_ino)
        except OSError:
            same = True   # cannot tell them apart => refuse
        if same:
            raise UnsafeName(
                "refusing to discard a file as a duplicate of itself (the two "
                "paths are the same file on disk -- a hardlink or a symlink, "
                "not two copies, so deleting one frees nothing and may break "
                "a seed)")

        live, qbt_error = self._live_qbt_state()
        self._refuse_if_payload(rel_path, src, src_st, live, qbt_error)
        route = self._prove_created(rel, src, download_id,
                                    max_route_age_s=DISCARD_MAX_ROUTE_AGE_S)

        # (4) THE KEPT FILE MUST GENUINELY PREDATE THE DOWNLOAD.
        #
        # Without this the identity proof cannot tell WHICH side of a uniquify
        # pair is the new copy: `names_match` accepts both `new.mp4` and
        # `new (1).mp4` against a route recorded as `new.mp4`, and the only
        # other discriminator was "mtime >= route_ts - slack", which any
        # actively written file satisfies. So /discard could be pointed at the
        # ORIGINAL and would remove it. The router's own file is written after
        # its routing decision; the file it duplicates was already there.
        # STRICTLY OLDER, and the slack goes the OTHER WAY here on purpose.
        #
        # (4) ONLY WHEN THE PAIR IS ACTUALLY AMBIGUOUS.
        #
        # The rule exists for ONE situation: `uniquify` produced two files
        # whose names BOTH match the routing decision (`new.mp4` and
        # `new (1).mp4` against a route recorded as `new.mp4`), so identity
        # cannot say which is the new copy and the timestamp has to.
        #
        # Applied unconditionally it was far too broad. `_prove_created`
        # accepts the source at `mtime >= route_ts - slack`, so demanding the
        # kept file be OLDER than `route_ts - slack` excluded the entire
        # duration of the download -- and refused the shape the toast most
        # often shows: the same file downloaded again a couple of seconds
        # later, and two overlapping downloads of it. Fail-closed, but it
        # stranded exactly the duplicates worth offering to remove.
        #
        # When the kept file's name could NOT be this download (the usual
        # case -- a different name, often a different directory), identity has
        # already distinguished them and this rule has nothing to add.
        route_ts = float(route.get("ts") or 0.0)
        expected = str(route.get("filename") or "")
        keep_could_be_this_download = bool(expected) and names_match(
            keep.name, expected)
        if keep_could_be_this_download and \
                keep_st.st_mtime >= route_ts - MTIME_SLACK_S:
            # `>=`, not `>`, so the two windows are disjoint at exact equality
            # too -- one character, and both this comment and the README
            # claimed it already.
            raise UnsafeName(
                "refusing to discard: BOTH of these names match this "
                "download's routing decision, which is what `uniquify` "
                "produces, and the file it is supposed to duplicate does not "
                "predate that decision -- so there is nothing left to say "
                "which of the two is the original. Refusing rather than "
                "guessing.")

        # (5) THE PROOF, AND IT IS A FULL READ OF BOTH FILES.
        #
        # This is where the sampled digest was, and it should never have been.
        # Sampling cannot carry a destructive decision: eight mid-file samples
        # catch a 40%-complete preallocated payload with overwhelming
        # probability but a 99%-complete one only about 8% of the time, and
        # deleting the finished copy to keep a 99% copy still destroys data. No
        # bounded read PROVES two multi-GB files identical -- it only ever
        # fails to disprove it. Two rounds of this were spent shoring the
        # digest up with another `stat`-derived signal; the information is not
        # in the metadata.
        #
        # /discard can afford the real thing precisely because it is not
        # /match: it is rare, the user asked for it, and nothing is waiting on
        # a 400 ms budget. The cheap checks above still run FIRST, so the
        # expensive one is only reached by a request that has already earned it.
        if src_st.st_size <= 0 or src_st.st_size != keep_st.st_size:
            raise UnsafeName(
                "refusing to discard: these two files are no longer the same "
                f"size ({src_st.st_size} vs {keep_st.st_size})")
        self._refuse_incomplete_keep(keep_rel, keep, keep_st, live, qbt_error)
        budget = float(self.cfg.section("dedupe").get("verify_timeout_s",
                                                      DISCARD_VERIFY_TIMEOUT_S))
        verdict, why = files_identical(src, keep,
                                       deadline=time.monotonic() + budget)
        if verdict is not True:
            # `None` (ran out of budget, or unreadable) and `False` (genuinely
            # different) are BOTH refusals, and the message says which -- but
            # neither is ever an all-clear. That third state is the whole
            # reason `files_identical` does not return a bool.
            raise UnsafeName(f"refusing to discard: {why}")

        # NOTHING MAY HAVE CHANGED UNDER US while the proofs ran.
        #
        # The digest is bounded but not instant, and a file that is still being
        # WRITTEN (the yt-dlp path appends; Chrome's `.crdownload` does not, but
        # nothing here is Chrome-specific) passes every check above and then
        # gets renamed out from under its own writer, which happily keeps
        # appending into the trash. Re-stat both and require them byte-identical
        # to what was proved.
        try:
            src_now, keep_now = src.stat(), keep.stat()
        except OSError as exc:
            raise UnsafeName(f"cannot re-stat both files: {exc}") from exc
        for label, was, now in (("the downloaded file", src_st, src_now),
                                ("the file it duplicates", keep_st, keep_now)):
            if (was.st_size, was.st_mtime_ns) != (now.st_size, now.st_mtime_ns):
                raise UnsafeName(
                    f"refusing to discard: {label} changed while it was being "
                    "checked (it is still being written, or something else "
                    "moved it)")

        # EVERYTHING THAT CAN STILL REFUSE MUST HAVE REFUSED BY NOW.
        #
        # `_trash_dest` can raise (a symlinked trash, no free name), and it
        # used to run AFTER the routing decision was consumed -- so a planted
        # symlink at `<root>/.dl-router-trash` spent the row while the file
        # stayed exactly where it was. The retry was then refused forever and
        # the `discards` table recorded a file as discarded that was not: a
        # permanent one-shot denial of service on every discard, and a lying
        # audit trail. Compute the destination first.
        mode = str(self.cfg.section("dedupe").get("delete_mode", "trash"))
        dest = self._trash_dest(src) if mode != "unlink" else None

        # CONSUME THE ROUTING DECISION. Atomic, and immediately before the
        # destructive act: a crash after this point costs a file that is
        # already proven redundant, while a crash before it costs nothing.
        if not self.store.record_discard(download_id, rel, kept_rel=keep_rel,
                                         mode=mode):
            prior = self.store.discard_for_download(download_id) or {}
            raise UnsafeName(
                "refusing to discard: this download's routing decision has "
                f"already been used to discard {prior.get('rel_path')!r}. One "
                "decision authorises one delete -- otherwise the same id "
                "removes every `(N)` variant of the name in turn.")

        if mode == "unlink":
            try:
                os.unlink(src)
            except OSError:
                # RELEASE THE ROW. Nothing was destroyed, so the decision must
                # not stay spent -- see the note above `dest`.
                self.store.release_discard(download_id)
                raise
            dest_rel = ""
        else:
            try:
                os.rename(src, dest)
            except OSError as exc:
                self.store.release_discard(download_id)
                # EXDEV: the trash is on a different filesystem from the file
                # (a multi-mount library root). Fail CLOSED and say which -- a
                # copy-then-delete fallback would be a non-atomic destructive
                # operation, which is the last thing this path should grow.
                raise UnsafeName(
                    f"refusing to discard: could not move the file into "
                    f"{TRASH_DIR!r} ({exc.__class__.__name__}: {exc}). If the "
                    "library root spans several filesystems the trash cannot "
                    "receive this file; move or delete it by hand."
                ) from exc
            dest_rel = f"{TRASH_DIR}/{dest.name}"
            # WHERE IT WENT, recorded on the row that already exists. A second
            # `record_discard` was dead code: `ON CONFLICT DO NOTHING` cannot
            # update, so every row read `trash_rel = ''` -- and the uniquified
            # `(N)` name in the trash is precisely what someone needs after a
            # wrong discard.
            self.store.set_discard_trash(download_id, dest_rel)
        # The path no longer holds that file, so the standing ownership claim
        # must go with it -- otherwise a later file arriving here inherits a
        # proof it never earned.
        #
        # BOTH KEYS, because the two sides do not agree on one. `_prove_owned`
        # and `move_routed_file` key `routed_files` on the RAW payload string,
        # while `rel` here is normalised from the resolved path -- so a caller
        # that sent `"Jane Doe/./clip.mp4"` earlier recorded under that literal
        # and a single normalised delete would silently miss it, leaving the
        # claim standing at an emptied path.
        self.store.forget_routed_file(rel)
        if str(rel_path) != rel:
            self.store.forget_routed_file(str(rel_path))
        self.hashes.forget(str(src))
        # NO `files.refresh(force=True)` HERE, and that is deliberate.
        #
        # It is a synchronous whole-tree walk of the library (up to
        # `file_index_max` files), and putting it AFTER the rename but BEFORE
        # the response means a slow walk blows the extension's 15 s /discard
        # timeout -- for a delete that already succeeded. The toast then says
        # "Not deleted", re-enables the button, and the natural retry produces
        # a second, differently worded refusal. An active false claim about a
        # destructive operation, reachable with no adversary at all, just a
        # cold index on a large library.
        #
        # The index goes stale by at most one TTL instead, and the cost of that
        # is nil: the trashed path lingers as a dedupe CANDIDATE, and
        # `confirm_duplicate` already treats an unreadable candidate as no
        # answer. `/relocate` keeps its forced refresh -- it is not
        # destructive, so a late answer there is only a late answer.
        log("discard", mode=mode, trashed=bool(dest_rel))
        return {"ok": True, "discarded": True, "mode": mode,
                "relPath": rel, "keptRelPath": keep_rel,
                "trashRelPath": dest_rel}

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
            if path == "/have":
                if not self._need_config():
                    return
                url = (parse_qs(split.query).get("url") or [""])[0]
                self._send(200, app.have_url(url))
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
                      "/dedupe": app.dedupe_check, "/discard": app.discard,
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
