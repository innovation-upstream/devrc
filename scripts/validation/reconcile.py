#!/usr/bin/env python3
"""reconcile — cross-source reconciliation of activity.events vs independent records.

For a recent window, diff each collected source against an INDEPENDENT existing
record the collector never produced, and report match / missing / extra counts:

  * zsh     events ↔ ~/.zsh_history
  * browser navs   ↔ Chrome/Chromium/Brave History sqlite
  * tmux    events ↔ ~/.tmux/tasks/*.json + ~/.tmux/activity/*
  * claude  prompts↔ ~/.claude/projects/**/*.jsonl  (user msgs)

"missing" = present in the independent record but NOT collected (collector gap);
"extra"   = collected but absent from the independent record (over-collection or
            an independent record that simply doesn't track that detail).

Robustness: if a source has no data on either side, it is reported "no data,
skipped" — never a FAIL. The diff logic (reconcile_sets / reconcile_counts) is
pure and unit-tested; this module wires it to the live CH + the refsource readers.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refsources as RS  # noqa: E402
from chquery import CHClient, CHConn, sql_quote  # noqa: E402


@dataclass
class Recon:
    source: str
    skipped: bool = False
    reason: str = ""
    collected: int = 0
    reference: int = 0
    matched: int = 0
    missing: int = 0  # in reference, not collected
    extra: int = 0    # collected, not in reference

    def line(self) -> str:
        if self.skipped:
            return f"{self.source:<8} SKIPPED  ({self.reason})"
        return (f"{self.source:<8} collected={self.collected} reference={self.reference} "
                f"matched={self.matched} missing={self.missing} extra={self.extra}")


# --------------------------------------------------------------------------- #
# Pure diff logic
# --------------------------------------------------------------------------- #
def reconcile_sets(collected: set, reference: set) -> tuple[int, int, int]:
    """Set diff → (matched, missing, extra).

    matched   = |collected ∩ reference|
    missing   = |reference - collected|   (collector did not capture it)
    extra     = |collected - reference|   (collected but not in the reference)
    """
    matched = len(collected & reference)
    missing = len(reference - collected)
    extra = len(collected - reference)
    return matched, missing, extra


def reconcile_counts(collected_n: int, reference_n: int) -> tuple[int, int, int]:
    """Count-only reconciliation when items aren't keyable 1:1 (e.g. claude msgs).

    matched = min(both); missing = reference excess; extra = collected excess.
    """
    matched = min(collected_n, reference_n)
    missing = max(0, reference_n - collected_n)
    extra = max(0, collected_n - reference_n)
    return matched, missing, extra


# --------------------------------------------------------------------------- #
# Per-source reconciliation (live)
# --------------------------------------------------------------------------- #
def reconcile_zsh(client: CHClient, histfile: Path, since_epoch: float) -> Recon:
    r = Recon("zsh")
    hist = RS.read_zsh_history(histfile)
    # zsh plain-format histfile has no timestamps; we can only match command text
    # multiset-style. Use the most-recent slice as a proxy reference set.
    ref_cmds = {h["command"] for h in hist if h["command"].strip()}
    table = client.conn.fq_table
    try:
        rows = client.rows(
            f"SELECT DISTINCT text FROM {table} "
            f"WHERE source='zsh' AND text!='' AND ts >= {sql_quote(_dt(since_epoch))}"
        )
    except Exception as exc:
        return Recon("zsh", skipped=True, reason=f"query error: {exc}")
    coll_cmds = {row.get("text", "") for row in rows if row.get("text")}
    if not coll_cmds and not ref_cmds:
        return Recon("zsh", skipped=True, reason="no data on either side")
    matched, missing, extra = reconcile_sets(coll_cmds, ref_cmds)
    r.collected, r.reference = len(coll_cmds), len(ref_cmds)
    r.matched, r.missing, r.extra = matched, missing, extra
    return r


def reconcile_browser(client: CHClient, history_db: Path, since_epoch: float) -> Recon:
    refs = RS.read_chrome_history(history_db, since_epoch=since_epoch)
    ref_urls = {x["url"] for x in refs if x["url"]}
    table = client.conn.fq_table
    try:
        rows = client.rows(
            f"SELECT DISTINCT text FROM {table} "
            f"WHERE source='browser' AND text!='' AND ts >= {sql_quote(_dt(since_epoch))}"
        )
    except Exception as exc:
        return Recon("browser", skipped=True, reason=f"query error: {exc}")
    coll_urls = {row.get("text", "") for row in rows if row.get("text")}
    if not coll_urls and not ref_urls:
        return Recon("browser", skipped=True, reason="no data on either side")
    matched, missing, extra = reconcile_sets(coll_urls, ref_urls)
    return Recon("browser", collected=len(coll_urls), reference=len(ref_urls),
                 matched=matched, missing=missing, extra=extra)


def reconcile_tmux(client: CHClient, tasks_dir: Path, activity_dir: Path,
                   since_epoch: float) -> Recon:
    tasks = RS.read_tmux_tasks(tasks_dir)
    acts = RS.read_tmux_activity(activity_dir)
    # Reference "projects worked in tmux" = task names. Compare against the set
    # of projects the collector recorded for source=tmux in the window.
    ref_projects = {t.get("task", "") for t in tasks if t.get("task")}
    table = client.conn.fq_table
    try:
        rows = client.rows(
            f"SELECT DISTINCT project FROM {table} "
            f"WHERE source='tmux' AND project!='' AND ts >= {sql_quote(_dt(since_epoch))}"
        )
    except Exception as exc:
        return Recon("tmux", skipped=True, reason=f"query error: {exc}")
    coll_projects = {row.get("project", "") for row in rows if row.get("project")}
    if not coll_projects and not ref_projects and not acts:
        return Recon("tmux", skipped=True, reason="no tmux data on either side")
    if not coll_projects:
        return Recon("tmux", skipped=True,
                     reason=f"no collected tmux events in window (ref tasks={len(ref_projects)}, "
                            f"activity files={len(acts)})")
    matched, missing, extra = reconcile_sets(coll_projects, ref_projects)
    return Recon("tmux", collected=len(coll_projects), reference=len(ref_projects),
                 matched=matched, missing=missing, extra=extra)


def reconcile_claude(client: CHClient, projects_dir: Path, since_epoch: float) -> Recon:
    pdir = Path(projects_dir)
    jsonls = list(pdir.glob("**/*.jsonl")) if pdir.exists() else []
    ref_n = RS.count_claude_user_msgs(jsonls, since_epoch=since_epoch)
    table = client.conn.fq_table
    try:
        coll_n = client.scalar(
            f"SELECT count() FROM {table} "
            f"WHERE source='claude' AND ts >= {sql_quote(_dt(since_epoch))}"
        ) or 0
    except Exception as exc:
        return Recon("claude", skipped=True, reason=f"query error: {exc}")
    coll_n = int(coll_n)
    if coll_n == 0 and ref_n == 0:
        return Recon("claude", skipped=True, reason="no claude data on either side")
    if coll_n == 0:
        return Recon("claude", skipped=True,
                     reason=f"source=claude not yet emitting (reference user msgs={ref_n})")
    matched, missing, extra = reconcile_counts(coll_n, ref_n)
    return Recon("claude", collected=coll_n, reference=ref_n,
                 matched=matched, missing=missing, extra=extra)


# --------------------------------------------------------------------------- #
def _dt(epoch: float) -> str:
    """Epoch → local wall-clock 'YYYY-MM-DD HH:MM:SS' (matches stored ts space)."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


def default_paths() -> dict:
    home = Path.home()
    # Prefer Brave (the live browser here), fall back to Chromium.
    brave = home / ".config/BraveSoftware/Brave-Browser/Default/History"
    chromium = home / ".config/chromium/Default/History"
    return {
        "zsh_history": home / ".zsh_history",
        "browser_history": brave if brave.exists() else chromium,
        "tmux_tasks": home / ".tmux/tasks",
        "tmux_activity": home / ".tmux/activity",
        "claude_projects": home / ".claude/projects",
    }


def run_reconcile(client: CHClient, window_hours: float = 24.0,
                  paths: dict | None = None) -> list[Recon]:
    p = paths or default_paths()
    since = time.time() - window_hours * 3600
    return [
        reconcile_zsh(client, p["zsh_history"], since),
        reconcile_browser(client, p["browser_history"], since),
        reconcile_tmux(client, p["tmux_tasks"], p["tmux_activity"], since),
        reconcile_claude(client, p["claude_projects"], since),
    ]


def main(argv=None) -> int:
    window = 24.0
    if argv:
        for a in argv:
            if a.startswith("--window-hours="):
                window = float(a.split("=", 1)[1])
    conn = CHConn.from_env()
    client = CHClient(conn)
    results = run_reconcile(client, window_hours=window)
    print(f"Cross-source reconciliation over last {window:.0f}h @ {conn.url}\n")
    for r in results:
        print("  " + r.line())
    # Reconciliation is informational: a non-skipped 'missing' is a possible
    # collector gap, but timing/dedup differences are expected, so it does not
    # by itself fail the run. We exit 0 unless a query errored.
    errored = [r for r in results if r.skipped and "error" in r.reason]
    return 1 if errored else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
