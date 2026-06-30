#!/usr/bin/env python3
"""initiative-scan — durable, cross-session tracker of in-flight *initiatives*.

The gap this fills: Zach runs many multi-session work threads ("initiatives") —
"App Blocks soft-launch", "sysRedis HA", "mail-automation", "dp-prod 500-floor".
Each is anchored by a handoff doc (`claudedocs/handoff-<slug>.md`) and spans many
Claude Code sessions over days. The existing `tmux-initiatives.sh` (Alt+i) shows
only LIVE tmux sessions — ephemeral, lost on reboot. This is the durable analogue:
a re-runnable, deterministic, read-only report of every initiative + its momentum +
its next step, so you can stop searching for your own in-flight work.

It FUSES three existing data sources (all already present, nothing new written):

  1. HANDOFF DOCS = the initiative registry.
     Glob `claudedocs/handoff-*.md` across the active repos under ~/workspace (and
     one nested level, e.g. ~/workspace/civit/*). Each doc is one initiative; per
     doc we parse the slug (filename), title (first `# ` heading), the doc's own
     date (filename date suffix/prefix, else file mtime), the first ranked item
     under "## Next steps", and any "## Open investigations" sub-headings.
     Multiple dated docs sharing a base slug = the SAME initiative (clustered;
     newest doc is current state).

  2. GIT per repo — attributes progress events by fuzzy-matching the slug against
     branch names + recent commit/PR activity. Per initiative (best-effort, within
     the window): # commits on matching branch(es), merged PRs, OPEN PRs whose head
     branch matches. Linking is HEURISTIC — when a commit/PR can't be attributed,
     it is NOT force-fit.

  3. ACTIVITY TELEMETRY (ClickHouse `activity.events`, source='claude') — gives
     recency / momentum / effort. Each event carries `payload.gitBranch`, plus
     top-level `ts`, `cwd`, `kind`. Per initiative (branch slug <-> repo): event
     count + last-touched ts in the window. OPTIONAL: if CLICKHOUSE_* is unset or
     the endpoint is unreachable, the report degrades gracefully (telemetry columns
     blank) and is still produced from handoff + git.

  Plus Claude transcripts (~/.claude/projects/**/*.jsonl) — a session whose genesis
  message names `handoff-<slug>.md` is counted toward that initiative (# sessions +
  last-session-touched), reusing the genesis-parsing approach of extract_genesis.py.

Credentials (read-only reader, from env — NEVER hardcoded). Same block as
activity-scan.py:
  export CLICKHOUSE_URL=http://192.168.50.94:30123    # workbench LAN endpoint
  export CLICKHOUSE_USER=activity_reader
  export CLICKHOUSE_PASSWORD=<reader-password>
Populate the password from SOPS (homelab-talos trunk):
  git -C ~/workspace/homelab-talos fetch origin trunk -q
  git -C ~/workspace/homelab-talos show origin/trunk:clusters/homelab/apps/activity/secrets.enc.yaml > /tmp/s.yaml
  export CLICKHOUSE_PASSWORD=$(SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key \
      sops -d --extract '["stringData"]["reader-password"]' /tmp/s.yaml); rm -f /tmp/s.yaml

Usage:
  initiative-scan.py [--days N] [--json] [--repo PATH]
  --days   trailing window in days (default 14)
  --json   machine-readable output (the raw per-initiative data)
  --repo   restrict to a single repo path (default: auto-discover under ~/workspace)

HONESTY NOTE: this measures ACTIVITY, RECENCY, and EFFORT (commits / sessions /
telemetry events / handoff freshness) plus the human-written "Next steps" line —
it does NOT estimate semantic % completion. "Momentum" is recency of touch, not
progress toward done. Initiative<->commit/PR/session linking is HEURISTIC slug
matching against branch names and genesis text; unattributable activity is surfaced
honestly as "unsegmented trunk/main work" per repo rather than dropped or
mis-credited. Read it as a descriptive instrument, not a project-management verdict.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Reuse the shared ClickHouse client + creds-from-env (no new deps).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "validation"))
import chquery as Q  # noqa: E402


# --------------------------------------------------------------------------- #
# Constants / tuning
# --------------------------------------------------------------------------- #
WORKSPACE = os.path.expanduser("~/workspace")
PROJECTS_ROOT = os.path.expanduser("~/.claude/projects")
DAY = 86400

# Momentum buckets (seconds since most-recent touch).
ACTIVE_MAX = 2 * DAY   # < 2d  -> active
SLOWING_MAX = 7 * DAY  # 2-7d  -> slowing; >= 7d -> stalled
MOMENTUM_RANK = {"active": 0, "slowing": 1, "stalled": 2, "unknown": 3}

# Branches that carry work NOT attributable to any single initiative.
TRUNK_BRANCHES = {"main", "master", "trunk", "develop", "HEAD"}

# Reused transcript-cleaning regexes (mirrors extract_genesis.py).
SYS_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_STDOUT = re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S)
COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
HARNESS = ("<task-notification>", "<task-reminder>", "<post-tool-use", "<bash-",
           "<user-prompt-submit", "<local-command-stdout>")

# A YYYY-MM-DD date anywhere in a handoff filename stem.
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# "## Next steps" with any trailing decoration (the template varies a little).
NEXT_STEPS_RE = re.compile(r"^##+\s+next steps\b", re.I)
OPEN_INV_RE = re.compile(r"^##+\s+open investigations\b", re.I)
ANY_H2_RE = re.compile(r"^##\s+")
# A leading list marker: "1. ", "1) ", "- ", "* ".
LIST_ITEM_RE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.*\S)\s*$")
H3_RE = re.compile(r"^###\s+(.*\S)\s*$")


# --------------------------------------------------------------------------- #
# Pure logic (unit-tested without live infra)
# --------------------------------------------------------------------------- #
def parse_handoff_filename(name: str) -> tuple[str, str | None]:
    """Split a handoff filename into (base_slug, date_or_None).

    Handles the real-world variants seen in the corpus:
      handoff-activity-telemetry-2026-06-27.md -> ("activity-telemetry", "2026-06-27")
      handoff-2026-06-25-clawgate-tasks.md     -> ("clawgate-tasks", "2026-06-25")
      handoff-app-blocks-launch.md             -> ("app-blocks-launch", None)
      handoff-2026-05-25.md                    -> ("2026-05-25", "2026-05-25")  # date IS the slug
    The base_slug is what clusters dated variants into one initiative.
    """
    stem = name
    if stem.endswith(".md"):
        stem = stem[:-3]
    if stem.startswith("handoff-"):
        stem = stem[len("handoff-"):]
    elif stem.startswith("handoff"):
        stem = stem[len("handoff"):].lstrip("-")

    m = DATE_RE.search(stem)
    date = m.group(1) if m else None
    if not date:
        return (stem.strip("-") or stem, None)

    # Strip the date token (and an adjacent separator) to get the base slug.
    base = (stem[:m.start()] + stem[m.end():]).strip("-")
    base = re.sub(r"-{2,}", "-", base)
    if not base:
        # The whole slug was the date (e.g. handoff-2026-05-25.md).
        base = date
    return (base, date)


def parse_handoff_title(text: str) -> str | None:
    """First top-level `# ` heading, sans a trailing ` — YYYY-MM-DD` date tail."""
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            # Drop a trailing date tail the template appends ("— 2026-06-27").
            title = re.sub(r"\s*[—-]\s*\d{4}-\d{2}-\d{2}\s*$", "", title).strip()
            return title or None
    return None


def parse_next_step(text: str) -> str | None:
    """First ranked item under the `## Next steps` section (template-aware).

    Returns the item's text with leading list marker stripped and inline markdown
    bold (`**...**`) flattened. None if there's no Next-steps section or no items.
    """
    lines = text.splitlines()
    in_section = False
    for line in lines:
        if NEXT_STEPS_RE.match(line):
            in_section = True
            continue
        if in_section:
            # A new H2 ends the section.
            if ANY_H2_RE.match(line):
                break
            m = LIST_ITEM_RE.match(line)
            if m:
                return _flatten_md(m.group(1))
    return None


def parse_open_investigations(text: str) -> list[str]:
    """The `### ` sub-headings under `## Open investigations` (each = one open bug)."""
    lines = text.splitlines()
    in_section = False
    out: list[str] = []
    for line in lines:
        if OPEN_INV_RE.match(line):
            in_section = True
            continue
        if in_section:
            if ANY_H2_RE.match(line):
                break
            m = H3_RE.match(line)
            if m:
                out.append(_flatten_md(m.group(1)))
    return out


def _flatten_md(s: str) -> str:
    """Strip inline bold/italic/code markers; collapse whitespace."""
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"\1", s)
    return " ".join(s.split())


def slug_tokens(slug: str) -> list[str]:
    """Meaningful tokens of a slug for fuzzy branch/genesis matching.

    Drops date tokens and ultra-short/stop tokens that would over-match.
    """
    stop = {"the", "and", "for", "wip", "tmp", "fix", "feat", "v2", "ha"}
    toks = []
    for t in re.split(r"[-_/]", slug.lower()):
        if not t or DATE_RE.fullmatch(t) or t.isdigit():
            continue
        if len(t) < 3 or t in stop:
            continue
        toks.append(t)
    return toks


def branch_matches_slug(branch: str, slug: str) -> bool:
    """Heuristic: does a git branch / telemetry gitBranch belong to this slug?

    A branch matches if, after stripping a `type/` prefix (feat/, fix/, chore/...),
    it shares a strong token overlap with the slug. We require that EITHER the
    branch tail contains the full base slug as a substring, OR >=2 slug tokens (or
    all of them, if the slug has <2 tokens) appear in the branch. Trunk/main never
    match a specific initiative.
    """
    if not branch:
        return False
    b = branch.lower()
    if b in {x.lower() for x in TRUNK_BRANCHES}:
        return False
    tail = b.split("/", 1)[1] if "/" in b else b
    base = slug.lower()
    if base and base in tail:
        return True
    toks = slug_tokens(slug)
    if not toks:
        return False
    hit = sum(1 for t in toks if t in b)
    need = 2 if len(toks) >= 2 else len(toks)
    return hit >= need


def classify_momentum(last_ts: float | None, now: float | None = None) -> str:
    """Momentum bucket from the most-recent touch (epoch seconds).

    active  : touched < 2d ago
    slowing : 2d <= age < 7d
    stalled : age >= 7d
    unknown : no touch evidence at all
    Boundaries: exactly 2d -> slowing, exactly 7d -> stalled.
    """
    if last_ts is None:
        return "unknown"
    now = time.time() if now is None else now
    age = now - last_ts
    if age < ACTIVE_MAX:
        return "active"
    if age < SLOWING_MAX:
        return "slowing"
    return "stalled"


def newest_touch(*timestamps) -> float | None:
    """Max of several optional epoch-second timestamps (Nones ignored)."""
    vals = [t for t in timestamps if t is not None]
    return max(vals) if vals else None


def rel_age(ts: float | None, now: float | None = None) -> str:
    """Human relative age: '5h', '3d', '2w' — or '-' if unknown."""
    if ts is None:
        return "-"
    now = time.time() if now is None else now
    s = max(0, now - ts)
    if s < 3600:
        return f"{int(s // 60)}m"
    if s < DAY:
        return f"{int(s // 3600)}h"
    if s < 14 * DAY:
        return f"{int(s // DAY)}d"
    return f"{int(s // (7 * DAY))}w"


def cluster_handoffs(docs: list[dict]) -> list[dict]:
    """Group parsed handoff docs by (repo, base_slug); newest doc = current state.

    `docs` items have: repo, slug, date (str|None), mtime (float), title, next_step,
    open_investigations, path. Returns one initiative dict per cluster, carrying the
    newest doc's parsed fields + a list of all member doc paths/dates.
    """
    groups: dict[tuple, list[dict]] = {}
    for d in docs:
        groups.setdefault((d["repo"], d["slug"]), []).append(d)

    initiatives = []
    for (repo, slug), members in groups.items():
        members.sort(key=_doc_sort_key, reverse=True)
        cur = members[0]
        initiatives.append({
            "repo": repo,
            "slug": slug,
            "title": cur["title"],
            "date": cur["date"],
            "doc_mtime": cur["mtime"],
            "next_step": cur["next_step"],
            "open_investigations": cur["open_investigations"],
            "current_doc": cur["path"],
            "docs": [{"path": m["path"], "date": m["date"]} for m in members],
        })
    return initiatives


def _doc_sort_key(m: dict) -> tuple:
    """Order a slug's docs newest-first: prefer filename date, fall back to mtime."""
    if m["date"]:
        try:
            d = datetime.strptime(m["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return (d.timestamp(), m["mtime"])
        except ValueError:
            pass
    return (m["mtime"], m["mtime"])


def sort_initiatives(initiatives: list[dict]) -> list[dict]:
    """Sort by momentum (active->stalled->unknown) then recency (newest first)."""
    return sorted(
        initiatives,
        key=lambda i: (MOMENTUM_RANK.get(i.get("momentum", "unknown"), 9),
                       -(i.get("last_touch") or 0)),
    )


# --------------------------------------------------------------------------- #
# Handoff discovery + parsing (I/O)
# --------------------------------------------------------------------------- #
def discover_repos(workspace: str = WORKSPACE) -> list[str]:
    """Dirs under ~/workspace (and one nested level) that hold handoff docs."""
    repos = set()
    patterns = [
        os.path.join(workspace, "*", "claudedocs", "handoff-*.md"),
        os.path.join(workspace, "*", "*", "claudedocs", "handoff-*.md"),
    ]
    for pat in patterns:
        for p in glob.glob(pat):
            repos.add(os.path.dirname(os.path.dirname(p)))
    return sorted(repos)


def read_handoff(path: str) -> dict:
    """Parse one handoff doc into its initiative-registry fields."""
    name = os.path.basename(path)
    slug, date = parse_handoff_filename(name)
    try:
        text = Path(path).read_text(errors="replace")
    except Exception:
        text = ""
    return {
        "path": path,
        "repo": os.path.dirname(os.path.dirname(path)),
        "slug": slug,
        "date": date,
        "mtime": _safe_mtime(path),
        "title": parse_handoff_title(text) or slug,
        "next_step": parse_next_step(text),
        "open_investigations": parse_open_investigations(text),
    }


def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def load_initiatives(repos: list[str]) -> list[dict]:
    """Discover + parse + cluster handoffs across the given repos."""
    docs = []
    for repo in repos:
        for path in sorted(glob.glob(os.path.join(repo, "claudedocs", "handoff-*.md"))):
            docs.append(read_handoff(path))
    return cluster_handoffs(docs)


# --------------------------------------------------------------------------- #
# git / gh (I/O) — best-effort, read-only
# --------------------------------------------------------------------------- #
def _run(cmd: list[str], timeout: float = 20.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def git_branches(repo: str) -> list[str]:
    out = _run(["git", "-C", repo, "branch", "-a", "--format=%(refname:short)"])
    names = []
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("origin/"):
            ln = ln[len("origin/"):]
        names.append(ln)
    return names


def git_default_branch(repo: str) -> str | None:
    """The repo's default branch short name (origin/HEAD target), e.g. 'main'/'trunk'."""
    out = _run(["git", "-C", repo, "symbolic-ref", "--quiet",
                "refs/remotes/origin/HEAD"]).strip()
    if out:
        # refs/remotes/origin/trunk -> trunk
        return out.rsplit("/", 1)[-1]
    for cand in ("main", "trunk", "master"):
        if _run(["git", "-C", repo, "rev-parse", "--verify", "--quiet",
                 f"origin/{cand}"]).strip():
            return cand
    return None


def git_commits_in_window(repo: str, branch: str, since_days: int,
                          default_branch: str | None = None) -> tuple[int, float | None]:
    """(# commits UNIQUE to `branch` within window, last-commit epoch | None).

    Critically excludes commits reachable from the default branch (`--not <default>`)
    so a feature branch is credited only with ITS OWN work — otherwise every branch
    counts the entire trunk history in the window (thousands of commits), grossly
    inflating + double-counting attribution. If `branch` IS the default, return (0, None)
    (default-branch work is the unsegmented catch-all, not an initiative).
    """
    if default_branch and branch.lower() == default_branch.lower():
        return (0, None)
    cmd = ["git", "-C", repo, "log", branch, "--no-merges",
           f"--since={since_days} days ago", "--format=%ct"]
    if default_branch and default_branch.lower() != branch.lower():
        # Only commits NOT already on the default branch.
        cmd += ["--not", default_branch, f"origin/{default_branch}"]
    out = _run(cmd)
    epochs = [int(x) for x in out.split() if x.strip().isdigit()]
    if not epochs:
        return (0, None)
    return (len(epochs), float(max(epochs)))


def gh_open_prs(repo: str) -> list[dict]:
    """OPEN PRs as [{number, title, headRefName}] — empty on any failure."""
    out = _run([
        "gh", "pr", "list", "-R", _repo_slug(repo) or repo, "--state", "open",
        "--json", "number,title,headRefName", "--limit", "100",
    ], timeout=30.0)
    if not out.strip():
        out = _run([
            "gh", "pr", "list", "--state", "open",
            "--json", "number,title,headRefName", "--limit", "100",
        ], timeout=30.0)
    try:
        return json.loads(out) if out.strip() else []
    except json.JSONDecodeError:
        return []


def gh_merged_prs(repo: str, since_days: int) -> list[dict]:
    """Merged PRs as [{number, title, headRefName, mergedAt}] within the window."""
    out = _run([
        "gh", "pr", "list", "-R", _repo_slug(repo) or repo, "--state", "merged",
        "--json", "number,title,headRefName,mergedAt", "--limit", "200",
    ], timeout=30.0)
    if not out.strip():
        out = _run([
            "gh", "pr", "list", "--state", "merged",
            "--json", "number,title,headRefName,mergedAt", "--limit", "200",
        ], timeout=30.0)
    try:
        prs = json.loads(out) if out.strip() else []
    except json.JSONDecodeError:
        return []
    cutoff = time.time() - since_days * DAY
    keep = []
    for pr in prs:
        ts = _iso_to_epoch(pr.get("mergedAt"))
        if ts is not None and ts >= cutoff:
            pr["_mergedEpoch"] = ts
            keep.append(pr)
    return keep


def _repo_slug(repo: str) -> str | None:
    """owner/name from the origin remote, for `gh -R` (None if undeterminable)."""
    url = _run(["git", "-C", repo, "config", "--get", "remote.origin.url"]).strip()
    if not url:
        return None
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def _iso_to_epoch(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


# --------------------------------------------------------------------------- #
# Claude transcripts (I/O) — sessions referencing a handoff
# --------------------------------------------------------------------------- #
def _clean_turn(raw: str) -> str:
    cmd = COMMAND_NAME.search(raw)
    if cmd:
        cargs = COMMAND_ARGS.search(raw)
        return ("/" + cmd.group(1).strip() + " " + (cargs.group(1).strip() if cargs else "")).strip()
    t = SYS_REMINDER.sub("", raw)
    t = COMMAND_STDOUT.sub("", t).strip()
    return t


def session_genesis_refs(projects_root: str, since_days: int) -> list[dict]:
    """For each transcript touched in the window, return its genesis text + mtime.

    [{text, mtime}] over the FIRST genuine user turn per session. Used to attribute
    a session to an initiative when the genesis names `handoff-<slug>.md`.
    """
    cutoff = time.time() - since_days * DAY
    out = []
    for path in glob.glob(os.path.join(projects_root, "**", "*.jsonl"), recursive=True):
        if "/subagents/" in path or "/wf_" in path:
            continue
        mt = _safe_mtime(path)
        if mt < cutoff:
            continue
        text = _first_user_turn(path)
        if text:
            out.append({"text": text, "mtime": mt})
    return out


def _first_user_turn(path: str) -> str | None:
    try:
        with open(path, errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isSidechain"):
            continue
        msg = obj.get("message") or {}
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        raw = None
        if isinstance(content, str):
            raw = content
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    raw = b.get("text", "")
                    break
        if not raw:
            continue
        txt = _clean_turn(raw)
        if not txt or txt.lstrip().startswith(HARNESS):
            continue
        if txt.startswith("[Request interrupted") or txt.startswith("Caveat: The messages below"):
            continue
        return txt
    return None


def attribute_sessions(initiatives: list[dict], genesis: list[dict]) -> None:
    """Mutate each initiative with session_count + last_session (epoch).

    A session belongs to an initiative when its genesis text mentions
    `handoff-<slug>` (slug or any dated variant filename of the cluster). Counted
    per initiative independently — a genesis naming two handoffs counts for both.
    """
    for ini in initiatives:
        names = {os.path.basename(d["path"]).lower() for d in ini["docs"]}
        names.add(f"handoff-{ini['slug'].lower()}")
        count = 0
        last = None
        for g in genesis:
            low = g["text"].lower()
            if any(n in low for n in names):
                count += 1
                last = newest_touch(last, g["mtime"])
        ini["session_count"] = count
        ini["last_session"] = last


# --------------------------------------------------------------------------- #
# Telemetry (I/O, optional)
# --------------------------------------------------------------------------- #
def q_branch_activity(win: int) -> str:
    """Per gitBranch (+cwd): claude-source event count + last ts, within window."""
    return (
        "SELECT JSONExtractString(toString(payload),'gitBranch') AS branch, "
        "any(cwd) AS cwd, count() AS n, max(ts) AS last_ts "
        "FROM activity.events "
        f"WHERE source='claude' AND ts>now()-{win} "
        "GROUP BY branch ORDER BY n DESC LIMIT 500"
    )


def fetch_telemetry(client, days: int) -> list[dict] | None:
    """Branch-keyed claude activity, or None if telemetry is unavailable."""
    try:
        return client.rows(q_branch_activity(days * DAY))
    except Exception as e:  # noqa: BLE001 — telemetry is strictly optional
        print(f"  (telemetry skipped: {e})", file=sys.stderr)
        return None


def ch_ts_to_epoch(s) -> float | None:
    """ClickHouse `max(ts)` (a wall-clock 'YYYY-MM-DD HH:MM:SS' string) -> epoch.

    `ts` is stored as host LOCAL wall-clock with NO column tz (see chquery header),
    so interpret it in the local zone for relative-age math.
    """
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    txt = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(txt, fmt).timestamp()
        except ValueError:
            continue
    return None


def attribute_telemetry(initiatives: list[dict], rows: list[dict] | None,
                        repos: list[str]) -> dict:
    """Attribute branch-activity rows to initiatives; return per-repo trunk catch-all.

    Mutates each initiative with telem_events + telem_last (epoch). Returns
    {repo: {"events": n, "last": epoch, "branches": set}} for trunk/main + any
    activity that didn't match a known initiative branch.
    """
    for ini in initiatives:
        ini.setdefault("telem_events", 0)
        ini.setdefault("telem_last", None)
    catchall: dict = {}
    if not rows:
        return catchall

    def cwd_repo(cwd: str | None) -> str | None:
        if not cwd:
            return None
        for r in sorted(repos, key=len, reverse=True):
            if cwd == r or cwd.startswith(r + "/"):
                return r
        return None

    for row in rows:
        branch = (row.get("branch") or "").strip()
        n = _num(row.get("n"))
        last = ch_ts_to_epoch(row.get("last_ts"))
        repo = cwd_repo(row.get("cwd"))

        if branch.lower() in {b.lower() for b in TRUNK_BRANCHES} or not branch:
            key = repo or "(unknown repo)"
            c = catchall.setdefault(key, {"events": 0, "last": None, "branches": set()})
            c["events"] += n
            c["last"] = newest_touch(c["last"], last)
            if branch:
                c["branches"].add(branch)
            continue

        matched = False
        for ini in initiatives:
            if branch_matches_slug(branch, ini["slug"]):
                ini["telem_events"] += n
                ini["telem_last"] = newest_touch(ini["telem_last"], last)
                matched = True
        if not matched:
            # Real branch but no handoff — surface as unsegmented work too.
            key = repo or "(unknown repo)"
            c = catchall.setdefault(key, {"events": 0, "last": None, "branches": set()})
            c["events"] += n
            c["last"] = newest_touch(c["last"], last)
            c["branches"].add(branch)
    return catchall


def _num(v, default=0):
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return v
    try:
        s = str(v).strip()
        return int(s) if s.lstrip("-").isdigit() else float(s)
    except (ValueError, TypeError):
        return default


# --------------------------------------------------------------------------- #
# git attribution per initiative
# --------------------------------------------------------------------------- #
def attribute_git(initiatives: list[dict], days: int) -> None:
    """Mutate each initiative with commit/PR fields. Caches per-repo gh calls."""
    branch_cache: dict[str, list[str]] = {}
    default_cache: dict[str, str | None] = {}
    open_pr_cache: dict[str, list[dict]] = {}
    merged_pr_cache: dict[str, list[dict]] = {}

    for ini in initiatives:
        repo = ini["repo"]
        if repo not in branch_cache:
            # Dedup branch names (origin/x + x normalize to the same tail).
            branch_cache[repo] = sorted(set(git_branches(repo)))
            default_cache[repo] = git_default_branch(repo)
            open_pr_cache[repo] = gh_open_prs(repo)
            merged_pr_cache[repo] = gh_merged_prs(repo, days)

        default_branch = default_cache[repo]
        matching_branches = [b for b in branch_cache[repo]
                             if branch_matches_slug(b, ini["slug"])]
        commits = 0
        last_commit = None
        for b in matching_branches:
            c, lc = git_commits_in_window(repo, b, days, default_branch)
            commits += c
            last_commit = newest_touch(last_commit, lc)

        open_prs = [{"number": p["number"], "title": p.get("title", "")}
                    for p in open_pr_cache[repo]
                    if branch_matches_slug(p.get("headRefName", ""), ini["slug"])]
        merged = [p for p in merged_pr_cache[repo]
                  if branch_matches_slug(p.get("headRefName", ""), ini["slug"])]

        ini["matching_branches"] = matching_branches
        ini["commits"] = commits
        ini["last_commit"] = last_commit
        ini["open_prs"] = open_prs
        ini["merged_prs"] = len(merged)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def build_report(days: int, repos: list[str] | None = None,
                 client=None, projects_root: str = PROJECTS_ROOT,
                 now: float | None = None) -> dict:
    """Fuse the three sources into a ranked, per-repo report dict.

    `client` may be None (telemetry skipped). `repos` None -> auto-discover.
    """
    repos = repos if repos is not None else discover_repos()
    initiatives = load_initiatives(repos)

    attribute_git(initiatives, days)
    genesis = session_genesis_refs(projects_root, days)
    attribute_sessions(initiatives, genesis)

    telem_rows = fetch_telemetry(client, days) if client is not None else None
    telemetry_available = telem_rows is not None
    catchall = attribute_telemetry(initiatives, telem_rows, repos)

    # Compute momentum from the MAX of every touch signal.
    for ini in initiatives:
        ini["last_touch"] = newest_touch(
            ini.get("last_commit"),
            ini.get("telem_last"),
            ini.get("last_session"),
            ini.get("doc_mtime"),
        )
        ini["momentum"] = classify_momentum(ini["last_touch"], now)

    initiatives = sort_initiatives(initiatives)

    by_repo: dict[str, list[dict]] = {}
    for ini in initiatives:
        by_repo.setdefault(ini["repo"], []).append(ini)

    return {
        "days": days,
        "telemetry_available": telemetry_available,
        "repos": repos,
        "by_repo": by_repo,
        "catchall": {k: {"events": v["events"], "last": v["last"],
                         "branches": sorted(v["branches"])}
                     for k, v in catchall.items()},
    }


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
MOMENTUM_TAG = {
    "active": "●ACTIVE ",
    "slowing": "◐slowing",
    "stalled": "○stalled",
    "unknown": "·unknown",
}


def render(report: dict, now: float | None = None) -> str:
    days = report["days"]
    out: list[str] = []
    out.append(f"=== initiative-scan: trailing {days}d "
               f"({'telemetry ON' if report['telemetry_available'] else 'telemetry OFF — handoff+git only'}) ===")
    out.append("   Look for: what's in flight (●active <2d / ◐slowing 2-7d / ○stalled >7d), "
               "its momentum + next step. Momentum = recency of touch, NOT % done.")

    repo_names = sorted(report["by_repo"].keys()) or report.get("repos", [])
    for repo in repo_names:
        inis = report["by_repo"].get(repo, [])
        short = _short_repo(repo)
        out.append(f"\n## {short}   ({len(inis)} initiative{'s' if len(inis) != 1 else ''})")
        if not inis:
            out.append("   (handoffs present but none parsed)")
        for ini in inis:
            tag = MOMENTUM_TAG.get(ini["momentum"], "?")
            head = (f"  {tag}  {ini['slug']}"
                    f"   touched {rel_age(ini.get('last_touch'), now)}"
                    f"   sess:{ini.get('session_count', 0)}"
                    f"   commits:{ini.get('commits', 0)}"
                    f"   merged-PR:{ini.get('merged_prs', 0)}")
            if report["telemetry_available"]:
                head += f"   ev:{ini.get('telem_events', 0)}"
            out.append(head)
            if ini.get("title") and ini["title"] != ini["slug"]:
                out.append(f"        “{ini['title']}”")
            for pr in ini.get("open_prs", []):
                out.append(f"        OPEN PR #{pr['number']}: {pr['title'][:80]}")
            ns = ini.get("next_step")
            out.append(f"        next: {ns[:160]}" if ns else "        next: (no Next-steps item parsed)")
            oi = ini.get("open_investigations") or []
            if oi:
                out.append(f"        open-investigations: {len(oi)} — {oi[0][:90]}")
            if len(ini.get("docs", [])) > 1:
                out.append(f"        ({len(ini['docs'])} dated handoffs; "
                           f"current: {os.path.basename(ini['current_doc'])})")

        ca = report["catchall"].get(repo)
        if ca and ca["events"]:
            out.append(f"  ·trunk·  unsegmented trunk/main work"
                       f"   touched {rel_age(ca['last'], now)}"
                       f"   ev:{ca['events']}")
            out.append("        (telemetry not attributable to any handoff — surfaced honestly, not dropped)")

    extra = [k for k in report["catchall"] if k not in repo_names]
    for k in sorted(extra):
        ca = report["catchall"][k]
        if not ca["events"]:
            continue
        out.append(f"\n## {_short_repo(k)}   (no handoffs)")
        out.append(f"  ·trunk·  unsegmented trunk/main work"
                   f"   touched {rel_age(ca['last'], now)}   ev:{ca['events']}")

    if not report["telemetry_available"]:
        out.append("\n(NOTE: telemetry OFF — CLICKHOUSE_* unset or unreachable. "
                   "Momentum/recency here come from commits + sessions + handoff mtime only.)")
    return "\n".join(out)


def _short_repo(repo: str) -> str:
    home = os.path.expanduser("~")
    if not repo:
        return repo
    return repo.replace(home + "/workspace/", "").replace(home, "~")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Durable cross-session initiative tracker (handoff docs + git + telemetry).")
    p.add_argument("--days", type=int, default=14, help="trailing window in days (default 14)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--repo", default=None, help="restrict to a single repo path")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    if a.days <= 0:
        print("error: --days must be positive", file=sys.stderr)
        return 2

    if a.repo:
        repo = os.path.abspath(os.path.expanduser(a.repo))
        if not os.path.isdir(os.path.join(repo, "claudedocs")):
            print(f"error: {repo} has no claudedocs/ dir", file=sys.stderr)
            return 2
        repos = [repo]
    else:
        repos = discover_repos()

    client = None
    try:
        conn = Q.CHConn.from_env()
        client = Q.CHClient(conn)
    except RuntimeError:
        client = None  # telemetry optional — degrade gracefully

    report = build_report(a.days, repos=repos, client=client)

    if a.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
