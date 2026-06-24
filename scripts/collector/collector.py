#!/usr/bin/env python3
"""collector — per-host activity-telemetry daemon.

Consumes events that source-hooks append to a local spool, batches them, and
ships them to a homelab ClickHouse `activity.events` table via the JSONEachRow
HTTP insert. Design goals (in priority order):

  * LOSSLESS + OFFLINE-BUFFERED: when ClickHouse is unreachable (e.g. laptop off
    nebula), events accumulate on disk and ship on recovery. Nothing is dropped
    on transient errors.
  * NO DOUBLE-SHIP: a segment file is deleted ONLY after the insert returns
    HTTP 200. A crash between ship and delete re-ships at most one segment; that
    is acceptable (ClickHouse insert is the unit of work) and far safer than
    fragile byte-offset bookkeeping.
  * BOUNDED: the on-disk buffer is capped by age and total size; over-cap the
    OLDEST segments are dropped and the drop is logged loudly, never silently.

Spool layout under $ACTIVITY_SPOOL_DIR (default ~/.local/state/activity/spool):
    current.log         writers (emit) append here
    seg-<ts>-<n>.log    rotated segments awaiting ship (daemon-owned)
The daemon atomically renames current.log -> a seg-* file (rotation), so a torn
final line from a writer can never be split across the boundary mid-ship.

Emit/daemon line contract (v1): see scripts/collector/emit. Each line is
TAB-separated key=value; keys prefixed `b64:` carry base64 values (arbitrary
free text). The daemon decodes, maps known keys to ClickHouse columns, and
bundles unknown keys into the JSON `payload` string.

Config via environment (see .env.example), loaded from an EnvironmentFile by the
systemd user service — secrets stay out of the nix store and out of git:
    CLICKHOUSE_URL        base URL (default http://clickhouse.homelab.lan)
    CLICKHOUSE_USER       default: default
    CLICKHOUSE_PASSWORD   default: empty (authed user slots in later, no code change)
    CLICKHOUSE_DATABASE   default: activity
    CLICKHOUSE_TABLE      default: events
    ACTIVITY_SPOOL_DIR    default ~/.local/state/activity/spool
    ACTIVITY_BATCH_SIZE   max events per insert (default 500)
    ACTIVITY_FLUSH_SECONDS  rotate+ship interval (default 10)
    ACTIVITY_MAX_BUFFER_BYTES  on-disk cap (default 64 MiB)
    ACTIVITY_MAX_BUFFER_AGE_SECONDS  segment age cap (default 7 days)
    ACTIVITY_HTTP_TIMEOUT  seconds (default 10)
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger("activity-collector")

# Columns that map straight to ClickHouse `activity.events` table columns.
# Everything NOT in here (and not `ts`/`host`) is bundled into `payload` JSON.
STRING_COLS = {
    "host", "source", "kind", "project", "cwd",
    "session", "app", "text", "payload",
}
INT_COLS = {"duration_ms", "exit_code"}
# `ts` handled specially (kept as a string in ClickHouse DateTime64 local format).
KNOWN_COLS = STRING_COLS | INT_COLS | {"ts"}


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    clickhouse_url: str = "http://clickhouse.homelab.lan"
    user: str = "default"
    password: str = ""
    database: str = "activity"
    table: str = "events"
    spool_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "ACTIVITY_SPOOL_DIR",
                Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
                / "activity" / "spool",
            )
        )
    )
    batch_size: int = 500
    flush_seconds: float = 10.0
    max_buffer_bytes: int = 64 * 1024 * 1024
    max_buffer_age_seconds: float = 7 * 24 * 3600
    http_timeout: float = 10.0

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Config":
        e = os.environ if env is None else env
        spool = e.get("ACTIVITY_SPOOL_DIR")
        return cls(
            clickhouse_url=e.get("CLICKHOUSE_URL", cls.clickhouse_url).rstrip("/"),
            user=e.get("CLICKHOUSE_USER", cls.user),
            password=e.get("CLICKHOUSE_PASSWORD", cls.password),
            database=e.get("CLICKHOUSE_DATABASE", cls.database),
            table=e.get("CLICKHOUSE_TABLE", cls.table),
            spool_dir=Path(spool) if spool else cls.__dataclass_fields__["spool_dir"].default_factory(),
            batch_size=int(e.get("ACTIVITY_BATCH_SIZE", cls.batch_size)),
            flush_seconds=float(e.get("ACTIVITY_FLUSH_SECONDS", cls.flush_seconds)),
            max_buffer_bytes=int(e.get("ACTIVITY_MAX_BUFFER_BYTES", cls.max_buffer_bytes)),
            max_buffer_age_seconds=float(
                e.get("ACTIVITY_MAX_BUFFER_AGE_SECONDS", cls.max_buffer_age_seconds)
            ),
            http_timeout=float(e.get("ACTIVITY_HTTP_TIMEOUT", cls.http_timeout)),
        )

    @property
    def insert_url(self) -> str:
        q = f"INSERT INTO {self.database}.{self.table} FORMAT JSONEachRow"
        base = self.clickhouse_url.rstrip("/")
        return f"{base}/?{urllib.parse.urlencode({'query': q})}"


# --------------------------------------------------------------------------- #
# Line parsing (the emit contract)
# --------------------------------------------------------------------------- #
def parse_line(line: str) -> dict | None:
    """Parse one v1 spool line into an event dict, or None if unparseable.

    Returns a dict with ClickHouse-ready values: known string/int columns plus a
    `payload` JSON string holding any unknown keys. `ts`/`host` pass through.
    """
    line = line.rstrip("\n")
    if not line:
        return None
    parts = line.split("\t")
    if not parts or parts[0] != "v1":
        return None

    raw: dict[str, str] = {}
    extra: dict[str, str] = {}
    for tok in parts[1:]:
        if "=" not in tok:
            return None  # malformed token -> reject whole line
        key, val = tok.split("=", 1)
        if key.startswith("b64:"):
            key = key[4:]
            try:
                val = base64.b64decode(val.encode("ascii"), validate=True).decode(
                    "utf-8", "replace"
                )
            except (binascii.Error, ValueError):
                return None
        raw[key] = val

    if "ts" not in raw or "source" not in raw or "kind" not in raw:
        return None  # require the minimum identifying triple

    event: dict[str, object] = {}
    for key, val in raw.items():
        if key in STRING_COLS or key == "ts":
            event[key] = val
        elif key in INT_COLS:
            try:
                event[key] = int(val)
            except ValueError:
                event[key] = 0
        else:
            extra[key] = val

    if extra:
        # Merge with any explicit payload= passed by the hook.
        existing = event.get("payload")
        merged = {}
        if isinstance(existing, str) and existing:
            try:
                merged = json.loads(existing)
                if not isinstance(merged, dict):
                    merged = {"_payload": existing}
            except json.JSONDecodeError:
                merged = {"_payload": existing}
        merged.update(extra)
        event["payload"] = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))

    return event


def format_jsoneachrow(events: list[dict]) -> bytes:
    """Render events as a JSONEachRow body (newline-delimited JSON, UTF-8)."""
    return ("\n".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in events) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- #
# Spool
# --------------------------------------------------------------------------- #
CURRENT_NAME = "current.log"
SEG_PREFIX = "seg-"
SEG_SUFFIX = ".log"


class Spool:
    """Owns the spool directory: rotation, segment enumeration, cap enforcement."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.dir = cfg.spool_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._seq = 0

    @property
    def current(self) -> Path:
        return self.dir / CURRENT_NAME

    def segments(self) -> list[Path]:
        """Rotated segments awaiting ship, oldest first (by name → by mtime)."""
        segs = [
            p for p in self.dir.glob(f"{SEG_PREFIX}*{SEG_SUFFIX}") if p.is_file()
        ]
        segs.sort(key=lambda p: (p.name, p.stat().st_mtime))
        return segs

    def rotate(self) -> Path | None:
        """Atomically move current.log to a new segment file. Returns the new
        segment path, or None if there was nothing to rotate."""
        cur = self.current
        if not cur.exists() or cur.stat().st_size == 0:
            return None
        self._seq += 1
        seg = self.dir / f"{SEG_PREFIX}{int(time.time()*1000):013d}-{self._seq:04d}{SEG_SUFFIX}"
        # os.rename within a dir is atomic; a concurrent writer's open fd keeps
        # writing to the now-renamed inode, so we may lose at most the few lines
        # written between rename and the writer's next append targeting a fresh
        # current.log. To minimise that window we rename immediately and writers
        # always re-open current.log per append (emit does `>>`, which re-opens).
        os.replace(cur, seg)
        return seg

    def enforce_cap(self) -> int:
        """Drop oldest segments exceeding the age/size cap. Returns count dropped.
        Loud WARNING per drop — never silent."""
        dropped = 0
        now = time.time()
        segs = self.segments()

        # Age cap.
        for seg in segs:
            try:
                age = now - seg.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > self.cfg.max_buffer_age_seconds:
                LOG.warning(
                    "BUFFER CAP: dropping over-age segment %s (age %.0fs > %.0fs)",
                    seg.name, age, self.cfg.max_buffer_age_seconds,
                )
                seg.unlink(missing_ok=True)
                dropped += 1

        # Size cap (oldest-first until under the limit).
        segs = self.segments()
        total = sum(p.stat().st_size for p in segs if p.exists())
        for seg in segs:
            if total <= self.cfg.max_buffer_bytes:
                break
            try:
                sz = seg.stat().st_size
            except FileNotFoundError:
                continue
            LOG.warning(
                "BUFFER CAP: dropping oldest segment %s (%d bytes) — buffer %d > cap %d",
                seg.name, sz, total, self.cfg.max_buffer_bytes,
            )
            seg.unlink(missing_ok=True)
            total -= sz
            dropped += 1
        return dropped


def read_segment(path: Path) -> tuple[list[dict], int, int]:
    """Parse a segment file → (events, parsed_ok, parse_failed).

    Unparseable lines are counted and logged but do not block the rest; they are
    dropped (already-rotated, can't re-queue a single bad line cleanly), which is
    the malformed-record handling path.
    """
    events: list[dict] = []
    ok = 0
    bad = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return [], 0, 0
    for line in text.splitlines():
        if not line:
            continue
        ev = parse_line(line)
        if ev is None:
            bad += 1
            LOG.warning("dropping malformed spool line in %s: %.120r", path.name, line)
        else:
            ok += 1
            events.append(ev)
    return events, ok, bad


# --------------------------------------------------------------------------- #
# Shipping
# --------------------------------------------------------------------------- #
class ClickHouseClient:
    def __init__(self, cfg: Config, opener=None):
        self.cfg = cfg
        self._opener = opener or urllib.request.urlopen

    def insert(self, body: bytes) -> None:
        """POST a JSONEachRow body. Raises on any non-2xx / network error."""
        req = urllib.request.Request(self.cfg.insert_url, data=body, method="POST")
        req.add_header("Content-Type", "text/plain; charset=utf-8")
        if self.cfg.user:
            req.add_header("X-ClickHouse-User", self.cfg.user)
        if self.cfg.password:
            req.add_header("X-ClickHouse-Key", self.cfg.password)
        resp = self._opener(req, timeout=self.cfg.http_timeout)
        try:
            code = getattr(resp, "status", None) or resp.getcode()
            if not (200 <= code < 300):
                raise RuntimeError(f"ClickHouse insert returned HTTP {code}")
        finally:
            close = getattr(resp, "close", None)
            if close:
                close()


def ship_segment(
    seg: Path, client: ClickHouseClient, batch_size: int
) -> bool:
    """Parse + insert one segment in batches; delete ON 200 ONLY.

    Returns True if the segment was fully shipped (and deleted), False if it
    should be retried later (network/HTTP error). No-double-ship: the file is
    deleted only after every batch in it has been accepted.
    """
    events, ok, bad = read_segment(seg)
    if not events:
        # Nothing shippable (empty or all-malformed) — drop the file so it does
        # not wedge the queue. Malformed counts already logged.
        seg.unlink(missing_ok=True)
        return True
    try:
        for i in range(0, len(events), batch_size):
            batch = events[i : i + batch_size]
            client.insert(format_jsoneachrow(batch))
    except (urllib.error.URLError, OSError, RuntimeError) as exc:
        LOG.warning("ship failed for %s (%d events): %s — will retry", seg.name, len(events), exc)
        return False
    seg.unlink(missing_ok=True)
    LOG.info("shipped %s: %d events (%d malformed dropped)", seg.name, ok, bad)
    return True


# --------------------------------------------------------------------------- #
# Daemon loop
# --------------------------------------------------------------------------- #
def flush_once(spool: Spool, client: ClickHouseClient) -> dict:
    """One rotate→cap→ship pass. Returns a small stats dict (for tests/logging)."""
    spool.rotate()
    spool.enforce_cap()
    shipped = 0
    failed = 0
    for seg in spool.segments():
        if ship_segment(seg, client, spool.cfg.batch_size):
            shipped += 1
        else:
            failed += 1
            # Stop on first failure: backend is down, no point hammering. Remaining
            # segments stay on disk and ship on the next successful pass.
            break
    return {"shipped": shipped, "failed": failed}


def run(cfg: Config) -> None:
    spool = Spool(cfg)
    client = ClickHouseClient(cfg)
    LOG.info(
        "activity-collector starting: spool=%s url=%s batch=%d flush=%.0fs",
        spool.dir, cfg.clickhouse_url, cfg.batch_size, cfg.flush_seconds,
    )
    backoff = cfg.flush_seconds
    max_backoff = 300.0
    while True:
        try:
            stats = flush_once(spool, client)
            if stats["failed"]:
                backoff = min(backoff * 2, max_backoff)
                LOG.debug("backing off to %.0fs after ship failure", backoff)
            else:
                backoff = cfg.flush_seconds
        except Exception:  # never let the loop die
            LOG.exception("unexpected error in flush loop")
            backoff = min(backoff * 2, max_backoff)
        time.sleep(backoff)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("ACTIVITY_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    cfg = Config.from_env()
    if argv and "--flush-once" in argv:
        spool = Spool(cfg)
        client = ClickHouseClient(cfg)
        stats = flush_once(spool, client)
        LOG.info("flush-once: %s", stats)
        return 0 if not stats["failed"] else 1
    run(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
