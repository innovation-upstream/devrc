#!/usr/bin/env python3
"""initiative-scan — durable, cross-session tracker of in-flight *initiatives*.

The gap this fills: Zach runs many multi-session work threads ("initiatives") —
"App Blocks soft-launch", "sysRedis HA", "mail-automation", "dp-prod 500-floor".
Each is anchored by a handoff doc (`claudedocs/handoff-<slug>.md`) and spans many
Claude Code sessions over days. The live tmux `agent-ops` dashboard ($mod+i) shows
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
  initiative-scan.py [--days N] [--json] [--repo PATH] [--tmux]
  --days   trailing window in days (default 14)
  --json   machine-readable output (the raw per-initiative data)
  --repo   restrict to a single repo path (default: auto-discover under ~/workspace)
  --tmux   link each initiative to the live tmux session(s) hosting it (matches the
           claude pane title against the initiative slug/title, scoped by the pane's
           cwd→repo); also lists live claude sessions with no matched initiative.
           Best-effort: no tmux server -> initiatives simply show [no session].

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

# Explicit "what this is" markers for parse_summary. Inline form
# ("**Goal:** …", "Objective: …", "- **Summary:** …") captures the trailing text;
# the section-heading form ("## Goal", "## Status") points at the paragraph beneath.
# Optional list marker + optional bold/italic wrappers around the keyword and value.
SUMMARY_INLINE_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?[*_]{0,3}\s*(?:goal|objective|summary|status|tl;?dr)"
    r"\s*[*_]{0,3}\s*:\s*[*_]{0,3}\s*(.*)$",
    re.I,
)
SUMMARY_HEADING_RE = re.compile(
    r"^#{1,6}\s+(?:goal|objective|summary|status|tl;?dr)\b", re.I)
# A markdown horizontal rule / thematic break (---, ***, ___).
HRULE_RE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
# Cap on the extracted summary length (chars), so a card stays legible.
SUMMARY_MAX = 200


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


def parse_all_next_steps(text: str) -> list[str]:
    """EVERY ranked item under the `## Next steps` section (not just the first).

    Companion to `parse_next_step` (which returns only the lead item) — the live
    viewer's expanded card wants the full list. Same template rules: collect every
    list item (`1.` / `-` / `*`) until the next H2, leading marker stripped and inline
    markdown flattened. Empty list if there's no Next-steps section or no items.
    """
    lines = text.splitlines()
    in_section = False
    out: list[str] = []
    for line in lines:
        if NEXT_STEPS_RE.match(line):
            in_section = True
            continue
        if in_section:
            if ANY_H2_RE.match(line):
                break
            m = LIST_ITEM_RE.match(line)
            if m:
                out.append(_flatten_md(m.group(1)))
    return out


def _cap_summary(s: str) -> str | None:
    """Trim + collapse; cap at SUMMARY_MAX chars on a word boundary (adds '…'). None if blank."""
    s = s.strip()
    if not s:
        return None
    if len(s) <= SUMMARY_MAX:
        return s
    cut = s[:SUMMARY_MAX].rstrip()
    sp = cut.rfind(" ")
    if sp >= int(SUMMARY_MAX * 0.6):
        cut = cut[:sp].rstrip()
    return cut + "…"


def _strip_list_marker(s: str) -> str:
    m = LIST_ITEM_RE.match(s)
    return m.group(1) if m else s


def _first_prose_paragraph(lines: list[str], start: int) -> str | None:
    """First prose block at/after `start`: skip blanks/headings/rules (and a leading
    list block), then join consecutive non-blank prose lines until a blank/heading/rule.
    A leading list marker on the block's first line is stripped. None if no prose."""
    i, n = start, len(lines)
    while i < n:
        s = lines[i].strip()
        if s and not s.startswith("#") and not HRULE_RE.match(lines[i]):
            break
        i += 1
    if i >= n:
        return None
    buf: list[str] = []
    while i < n:
        s = lines[i].strip()
        if not s or s.startswith("#") or HRULE_RE.match(lines[i]):
            break
        buf.append(_strip_list_marker(s) if not buf else s)
        i += 1
    return " ".join(buf) if buf else None


def parse_summary(text: str) -> str | None:
    """A deterministic 1-2 line "what this is" for a handoff (NO LLM).

    Preference order:
      1. An explicit inline marker — `**Goal:** …`, `Objective: …`, `Summary: …`,
         `Status: …`, `TL;DR: …` (optionally a list item / bold) — takes its trailing
         text; if the marker line has no trailing text (e.g. `**Goal:**` alone) or is a
         `## Goal` / `## Status` section heading, takes the first prose paragraph beneath.
      2. Fallback: the first non-heading, non-blank prose paragraph after the leading
         `# ` title (or the top of the doc if there's no title).
    Markdown is flattened, whitespace collapsed, and the result capped at ~200 chars.
    None only when the doc has no prose at all.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = SUMMARY_INLINE_RE.match(line)
        if m:
            val = m.group(1).strip()
            if val:
                return _cap_summary(_flatten_md(val))
            para = _first_prose_paragraph(lines, i + 1)
            if para:
                return _cap_summary(_flatten_md(para))
        elif SUMMARY_HEADING_RE.match(line):
            para = _first_prose_paragraph(lines, i + 1)
            if para:
                return _cap_summary(_flatten_md(para))
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            start = i + 1
            break
    para = _first_prose_paragraph(lines, start)
    return _cap_summary(_flatten_md(para)) if para else None


# Ultra-common tokens that would over-match if used as an initiative fingerprint.
STOP_TOKENS = {"the", "and", "for", "wip", "tmp", "fix", "feat", "v2", "ha"}


def slug_tokens(slug: str) -> list[str]:
    """Meaningful tokens of a slug for fuzzy branch/genesis matching.

    Drops date tokens and ultra-short/stop tokens that would over-match.
    """
    toks = []
    for t in re.split(r"[-_/]", slug.lower()):
        if not t or DATE_RE.fullmatch(t) or t.isdigit():
            continue
        if len(t) < 3 or t in STOP_TOKENS:
            continue
        toks.append(t)
    return toks


# Generic session-summary / action verbs that lead tmux pane titles ("Resume …",
# "Monitor …", "Continue …") and prose handoff titles — never a TOPIC identifier, so
# they must not carry a pane→initiative match (a "Resume session <id>" pane wrongly
# hitting a `…-resume` slug). Applied ONLY to free text (`text_tokens`), NOT to
# `slug_tokens`, so branch/git/telemetry attribution stays byte-identical.
TITLE_STOP = {
    "resume", "continue", "monitor", "review", "audit", "update", "check",
    "implement", "assess", "revive", "identify", "finish", "run", "get", "wire",
    "build", "work", "session", "task", "tasks", "add", "use",
}


def text_tokens(s: str) -> list[str]:
    """Meaningful tokens of FREE TEXT (a handoff title, a tmux pane title).

    Same filters as `slug_tokens` (drop dates / digits / short / stop tokens) but
    splits on any non-alphanumeric run instead of only slug separators, so a prose
    string like a live tmux pane title ("Continue clawgate agent loop soak testing")
    tokenizes the same way a slug does and the two can be compared on word equality.
    Additionally drops `TITLE_STOP` action verbs — noise in a session summary that
    would otherwise link a pane on a generic word (see TITLE_STOP).
    """
    toks = []
    for t in re.split(r"[^a-z0-9]+", s.lower()):
        if not t or DATE_RE.fullmatch(t) or t.isdigit():
            continue
        if len(t) < 3 or t in STOP_TOKENS or t in TITLE_STOP:
            continue
        toks.append(t)
    return toks


def resolve_cwd_repo(cwd: str | None, repos: list[str],
                     wt_map: dict[str, str] | None = None) -> str | None:
    """Map a working directory to its CANONICAL repo, or None.

    A cwd inside a discovered repo (realpath-prefix containment) resolves to that
    repo; a cwd inside a linked worktree resolves via `wt_map` to the worktree's
    canonical repo (shrinking the `(unknown repo)` bucket). Longest-prefix-first so
    the most specific repo / worktree wins. Shared by telemetry cwd attribution and
    tmux pane→initiative matching so both agree on what repo a dir belongs to.
    """
    if not cwd:
        return None
    wt_map = wt_map or {}
    rp = os.path.realpath(cwd)
    for r in sorted(repos, key=len, reverse=True):
        rr = os.path.realpath(r)
        if rp == rr or rp.startswith(rr + "/"):
            return r
    for wt in sorted(wt_map.keys(), key=len, reverse=True):
        if rp == wt or rp.startswith(wt + "/"):
            return wt_map[wt]
    return None


def branch_tokens(branch: str) -> list[str]:
    """Tokenize a branch name the SAME way slugs are tokenized.

    Strips an `origin/` remote prefix and a leading `type/` segment (feat/, fix/,
    chore/, zach/, …) then tokenizes the remainder with `slug_tokens`. Tokenizing
    both sides identically is what lets us compare on WORD equality instead of
    substrings (the source of the `app-blocks`⊂`app-blocks-followups` and
    `mail-actions`⊂`email-fractions-redesign` false matches).
    """
    if not branch:
        return []
    b = branch.lower()
    if b.startswith("origin/"):
        b = b[len("origin/"):]
    # Drop a single leading type/owner segment so feat/x and x tokenize alike.
    tail = b.split("/", 1)[1] if "/" in b else b
    return slug_tokens(tail)


def branch_matches_slug(branch: str, slug: str) -> bool:
    """Does a git branch / telemetry gitBranch belong to this slug?

    Rule (WORD equality, not substring): tokenize both the branch and the slug
    with the SAME tokenizer, then require EVERY slug token to equal a branch
    token — i.e. the slug's token set is a subset of the branch's token set. A
    branch may carry extra tokens (a `feat/` prefix, a `-collector` suffix) and
    still match, but a token must match a WHOLE word, so:
      - `app-blocks` does NOT match `app-blocks-followups` (extra token on the
        BRANCH is fine, but here `followups` is unrelated and the SIBLING slug
        `app-blocks-followups` is the better, more-specific match — see
        best_matching_initiative);
      - `mail-actions` does NOT match `email-fractions-redesign`
        (`mail`≠`email`, `actions`≠`fractions`);
      - `app-api` does NOT match `mapper-rapid`.
    Trunk/main never match a specific initiative.

    NOTE: this is the LOW-LEVEL predicate (does the slug fit inside the branch?).
    When several initiatives' slugs all fit one branch (siblings sharing a common
    prefix), use `best_matching_initiative` to award credit to the single
    longest / most-specific slug instead of every sibling.
    """
    if not branch:
        return False
    if branch.lower() in {x.lower() for x in TRUNK_BRANCHES}:
        return False
    slug_toks = slug_tokens(slug)
    if not slug_toks:
        return False
    btoks = set(branch_tokens(branch))
    if not btoks:
        return False
    return set(slug_toks).issubset(btoks)


def best_matching_initiative(branch: str, initiatives: list[dict]) -> dict | None:
    """Pick the single most-specific initiative whose slug matches `branch`.

    Among all initiatives whose slug fits the branch (`branch_matches_slug`),
    return the one with the MOST slug tokens (longest / most-specific), so a
    branch like `app-blocks-followups` is credited to the `app-blocks-followups`
    initiative, NOT also to the broader `app-blocks` sibling. Ties (equal token
    count) are broken by the longer raw slug, then lexically, for determinism.
    None if nothing matches.
    """
    candidates = [ini for ini in initiatives
                  if branch_matches_slug(branch, ini.get("slug", ""))]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda ini: (len(slug_tokens(ini.get("slug", ""))),
                         len(ini.get("slug", "")),
                         ini.get("slug", "")),
    )


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


def _date_to_epoch(date_str: str | None) -> float | None:
    """'YYYY-MM-DD' -> epoch at UTC midnight, or None if absent/unparseable."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def doc_touch_epoch(ini: dict) -> float | None:
    """Freshness of a handoff, for momentum — its AUTHORED date, NOT filesystem mtime.

    The handoffs live in an untracked `claudedocs/` (no git history), and a bulk
    `git checkout`/`pull`/`clone` rewrites EVERY working-tree file's mtime to the same
    instant — so filesystem mtime routinely reports a batch of months-old, done
    handoffs as all "touched" on the checkout day, and they masquerade as in-flight.
    The filename's `YYYY-MM-DD` (parsed into `date`) is the real authoring date and is
    immune to that. Fall back to filesystem mtime ONLY for a dateless handoff, where
    there is no better signal.
    """
    return _date_to_epoch(ini.get("date")) or ini.get("doc_mtime")


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
            "summary": cur["summary"],
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
def _git_common_dir(path: str) -> str | None:
    """Absolute `git rev-parse --git-common-dir` for `path`, or None if not a repo.

    Two dirs that are worktrees of the SAME repository resolve to the SAME
    common-dir (the main worktree's `.git`), which is exactly the key we dedup on.
    `--path-format=absolute` makes the result stable regardless of cwd; we realpath
    it so symlinked workspace roots compare equal too.
    """
    out = _run(["git", "-C", path, "rev-parse", "--path-format=absolute",
                "--git-common-dir"]).strip()
    if not out:
        # Older git without --path-format: fall back and absolutize ourselves.
        out = _run(["git", "-C", path, "rev-parse", "--git-common-dir"]).strip()
        if not out:
            return None
        if not os.path.isabs(out):
            out = os.path.join(path, out)
    try:
        return os.path.realpath(out)
    except OSError:
        return out


def _is_main_worktree(path: str, common_dir: str) -> bool:
    """True iff `path` is the MAIN worktree of its repo (not a linked worktree).

    The main worktree's `.git` is a real directory and its common-dir resolves to
    `<path>/.git`; a linked worktree has a `.git` FILE pointing elsewhere, so its
    common-dir lives under a different toplevel. We compare the common-dir's parent
    (the toplevel that owns `.git`) against `path` by realpath.
    """
    try:
        owner = os.path.realpath(os.path.dirname(common_dir))
        return owner == os.path.realpath(path)
    except OSError:
        return False


def _canonical_repo_for_group(candidates: list[str], common_dir: str) -> str:
    """Pick ONE canonical repo for a set of dirs sharing `common_dir`.

    Prefer a candidate that IS the main worktree. Else fall back to the main
    worktree's toplevel (common-dir's parent) if it exists on disk, else the first
    candidate deterministically (sorted) so the choice is reproducible.
    """
    for c in sorted(candidates):
        if _is_main_worktree(c, common_dir):
            return c
    main_toplevel = os.path.dirname(common_dir)
    if main_toplevel and os.path.isdir(main_toplevel):
        return os.path.realpath(main_toplevel)
    return sorted(candidates)[0]


def discover_repos(workspace: str = WORKSPACE) -> list[str]:
    """Dirs under ~/workspace (and one nested level) that hold handoff docs.

    Collapses git worktrees to their canonical repo: candidate dirs that are
    worktrees of the SAME repository (same `git rev-parse --git-common-dir`) are
    grouped, and ONE canonical repo per group is kept (the main worktree, or its
    toplevel, see `_canonical_repo_for_group`). A candidate that isn't a git repo
    at all (no common-dir) falls back to being its own repo, so a plain dir with a
    `claudedocs/` still surfaces.
    """
    return _dedup_worktrees(_candidate_repo_dirs(workspace))


def _candidate_repo_dirs(workspace: str) -> list[str]:
    """Raw candidate repo dirs (pre-dedup): parents of any handoff doc."""
    cands: set[str] = set()
    patterns = [
        os.path.join(workspace, "*", "claudedocs", "handoff-*.md"),
        os.path.join(workspace, "*", "*", "claudedocs", "handoff-*.md"),
    ]
    for pat in patterns:
        for p in glob.glob(pat):
            cands.add(os.path.dirname(os.path.dirname(p)))
    return sorted(cands)


def _dedup_worktrees(candidates: list[str]) -> list[str]:
    """Collapse candidate dirs that are worktrees of one repo to a canonical repo.

    Groups by `git rev-parse --git-common-dir`; non-git candidates (common-dir is
    None) are each their own group keyed by their own path (graceful fallback —
    never crash, never drop). Returns the sorted, deduped canonical repo list.
    """
    by_common: dict[str, list[str]] = {}
    for cand in candidates:
        common = _git_common_dir(cand)
        key = common if common is not None else f"\0nogit:{cand}"
        by_common.setdefault(key, []).append(cand)

    canonical: set[str] = set()
    for key, group in by_common.items():
        if key.startswith("\0nogit:"):
            # Not a git repo — keep the dir itself.
            canonical.update(group)
        else:
            canonical.add(_canonical_repo_for_group(group, key))
    return sorted(canonical)


def worktree_canonical_map(repos: list[str]) -> dict[str, str]:
    """Map every linked-worktree path of a canonical repo -> that canonical repo.

    For each canonical repo we ask git for ALL its worktree paths
    (`git worktree list`) and map each linked-worktree path back to the canonical.
    This lets cwd attribution resolve telemetry whose `cwd` lives in a linked
    worktree (a dir that is NOT itself a discovered repo) to the parent repo —
    shrinking the `(unknown repo)` bucket. Best-effort: a repo whose worktree list
    can't be read just contributes nothing (its own realpath-prefix still matches).
    """
    mapping: dict[str, str] = {}
    for repo in repos:
        for wt in _git_worktree_paths(repo):
            rp = os.path.realpath(wt)
            if rp != os.path.realpath(repo):
                mapping[rp] = repo
    return mapping


def _git_worktree_paths(repo: str) -> list[str]:
    """Absolute worktree paths for `repo` via `git worktree list --porcelain`."""
    out = _run(["git", "-C", repo, "worktree", "list", "--porcelain"])
    paths: list[str] = []
    for ln in out.splitlines():
        if ln.startswith("worktree "):
            paths.append(ln[len("worktree "):].strip())
    return paths


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
        "summary": parse_summary(text),
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


def _ref_exists(repo: str, ref: str) -> bool:
    """True iff `ref` resolves in `repo` (local or remote-tracking)."""
    if not ref:
        return False
    try:
        out = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--verify", "--quiet", ref],
            capture_output=True, text=True, timeout=20.0)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return out.returncode == 0


def _resolve_branch_ref(repo: str, branch: str) -> str | None:
    """Pick an existing ref for a branch token: prefer local, else origin/<branch>.

    `branch` may already carry an `origin/` prefix (from `git branch -a`). Returns
    the first ref that actually resolves, or None if neither the local branch nor
    its remote-tracking counterpart exists (so callers can report "unknown" rather
    than fataling git → a silent 0).
    """
    if not branch:
        return None
    bare = branch[len("origin/"):] if branch.startswith("origin/") else branch
    for cand in (bare, f"origin/{bare}", branch):
        if _ref_exists(repo, cand):
            return cand
    return None


def git_branches(repo: str) -> list[str]:
    """Branch SHORT names. Keeps the `origin/` prefix on remote-only branches so
    a branch existing solely as `origin/feat/x` stays resolvable (stripping it to
    `feat/x` would later fatal `git log feat/x`). Local branches (no prefix) are
    preferred — when both a local `x` and `origin/x` exist they dedup to `x`.
    """
    out = _run(["git", "-C", repo, "branch", "-a", "--format=%(refname:short)"])
    local: set[str] = set()
    remote: set[str] = set()
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("origin/"):
            tail = ln[len("origin/"):]
            if tail == "HEAD" or tail.startswith("HEAD"):
                continue  # the origin/HEAD -> origin/<default> alias, not a branch
            remote.add(ln)
        else:
            local.add(ln)
    names = set(local)
    local_tails = local
    for r in remote:
        tail = r[len("origin/"):]
        if tail not in local_tails:
            names.add(r)  # remote-only branch — keep the origin/ prefix
    return sorted(names)


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
                          default_branch: str | None = None
                          ) -> tuple[int | None, float | None]:
    """(# commits UNIQUE to `branch` within window, last-commit epoch | None).

    Count is None when the branch can't be resolved to ANY existing ref (neither
    local nor `origin/<branch>`) — a clearly-distinguished "unknown", NOT a silent
    0, so a fresh clone / remote-only branch doesn't masquerade as "no work".

    Critically excludes commits reachable from the default branch (`--not <ref>`)
    so a feature branch is credited only with ITS OWN work — otherwise every branch
    counts the entire trunk history in the window (thousands of commits), grossly
    inflating + double-counting attribution. Each `--not` ref is guarded with
    `git rev-parse --verify`, so a missing local `main` (repo on `trunk`, or a
    remote-only default) no longer fatals git rc=128 → a swallowed "" → false 0.
    If `branch` IS the default, return (0, None) (default-branch work is the
    unsegmented catch-all, not an initiative).
    """
    if default_branch and branch.lower() == default_branch.lower():
        return (0, None)

    target = _resolve_branch_ref(repo, branch)
    if target is None:
        # Neither the branch nor its remote exists — report unknown, not 0.
        return (None, None)

    cmd = ["git", "-C", repo, "log", target, "--no-merges",
           f"--since={since_days} days ago", "--format=%ct"]
    if default_branch and default_branch.lower() != branch.lower():
        # Only commits NOT already on the default branch — but ONLY include
        # exclusion refs that actually exist (else git fatals and we'd undercount).
        excludes = []
        for ref in (default_branch, f"origin/{default_branch}"):
            if _ref_exists(repo, ref) and ref not in excludes:
                excludes.append(ref)
        if excludes:
            cmd += ["--not", *excludes]
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
    """Per (gitBranch, cwd): claude-source event count + last ts, within window.

    Grouping by BOTH branch and cwd (not `GROUP BY branch` with `any(cwd)`) keeps
    each repo's activity tied to its own working dir — otherwise every repo's
    `main`/`trunk`, and any branch name reused across repos (`feat/api`,
    `fix/bug`), collapse into one row attributed to one arbitrary cwd, and
    attribution double-credits unrelated repos.
    """
    return (
        "SELECT JSONExtractString(toString(payload),'gitBranch') AS branch, "
        "cwd AS cwd, count() AS n, max(ts) AS last_ts "
        "FROM activity.events "
        f"WHERE source='claude' AND ts>now()-{win} "
        "GROUP BY branch, cwd ORDER BY n DESC LIMIT 500"
    )


def fetch_telemetry(client, days: int) -> list[dict] | None:
    """Branch-keyed claude activity, or None if telemetry is unavailable."""
    try:
        return client.rows(q_branch_activity(days * DAY))
    except Exception as e:  # noqa: BLE001 — telemetry is strictly optional
        print(f"  (telemetry skipped: {e})", file=sys.stderr)
        return None


def ch_ts_to_epoch(s) -> float | None:
    """ClickHouse `max(ts)` (a 'YYYY-MM-DD HH:MM:SS[.fff]' string) -> epoch.

    `ts` is stored in UTC — `scripts/collector/emit` stamps it with `date -u`
    (see scripts/collector/tests/test_collector.py::test_emit_ts_is_utc and the
    root CLAUDE.md "ts is UTC" note). The column is a bare DateTime64(3) with NO
    attached timezone, so the wall-clock string we read back IS the UTC instant.
    We therefore parse it as UTC — NOT local — or every relative-age/momentum
    computation is skewed by the host's UTC offset (~5h here), wrongly pushing
    initiatives toward slowing/stalled.
    """
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    txt = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(txt, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def attribute_telemetry(initiatives: list[dict], rows: list[dict] | None,
                        repos: list[str],
                        worktree_map: dict[str, str] | None = None) -> dict:
    """Attribute branch-activity rows to initiatives; return per-repo trunk catch-all.

    Each row carries (branch, cwd). The cwd is mapped to its CANONICAL repo by
    realpath-prefix containment — including linked-worktree paths via
    `worktree_map`, so telemetry whose cwd lives in a sibling worktree
    (`…/datapacket-talos-review-sandbox`) attributes to the parent repo instead of
    falling into `(unknown repo)`. A row is only credited to an initiative WHOSE
    repo matches that cwd's repo — so a branch token reused across repos
    (`feat/api` in repo A and repo B) never cross-credits, and an arbitrary
    `any(cwd)` no longer collapses every repo's `main` into one. Within the
    matching repo, credit goes to the SINGLE most-specific initiative
    (`best_matching_initiative`), so sibling slugs don't all share the same count.

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

    wt_map = worktree_map or {}

    def cwd_repo(cwd: str | None) -> str | None:
        return resolve_cwd_repo(cwd, repos, wt_map)

    # Initiatives indexed by their repo, so matching is scoped to the row's repo.
    inis_by_repo: dict[str, list[dict]] = {}
    for ini in initiatives:
        inis_by_repo.setdefault(ini["repo"], []).append(ini)

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

        # Only initiatives in THIS row's repo are eligible (no cross-repo credit).
        eligible = inis_by_repo.get(repo, []) if repo is not None else []
        ini = best_matching_initiative(branch, eligible)
        if ini is not None:
            ini["telem_events"] += n
            ini["telem_last"] = newest_touch(ini["telem_last"], last)
        else:
            # Real branch but no matching handoff in its repo — surface as
            # unsegmented work too (honest, not dropped, not mis-credited).
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
# Live tmux sessions (I/O, optional) — which scratch session hosts an initiative
# --------------------------------------------------------------------------- #
def collect_tmux_panes() -> list[dict]:
    """Every live tmux pane as [{session, window, cwd, command, title}] (empty if no
    server).

    Zach runs many named scratchpad tmux sessions in parallel (`8`, `scratch7`,
    `wheat`), each often with SEVERAL windows on DIFFERENT initiatives; a claude pane
    sets its title to that window's session-summary line. We capture the window index
    too so the ledger can point at `<session>-<window>` (e.g. `8-1` vs `8-3`), the
    exact unit `tmux select-window -t 8:1` navigates to — a bare session id can't
    disambiguate two initiatives in one session. Best-effort: `_run` returns "" when
    the tmux binary is missing or no server is running, so this yields [] and callers
    degrade to [no session].
    """
    out = _run(["tmux", "list-panes", "-a", "-F",
                "#{session_name}\t#{window_index}\t#{pane_current_path}"
                "\t#{pane_current_command}\t#{pane_title}"])
    panes: list[dict] = []
    for ln in out.splitlines():
        if not ln.strip():
            continue
        parts = ln.split("\t")
        while len(parts) < 5:
            parts.append("")
        panes.append({"session": parts[0], "window": parts[1], "cwd": parts[2],
                      "command": parts[3], "title": parts[4]})
    return panes


# The scratchpad codename map lives in ONE place — the `SCRATCH_SLOTS` table of
# scripts/tmux-scratch-slots.sh, the single source of truth also sourced by the tmux
# HUD / dashboard / status-left, mirroring the tmux/i3 hotkey bindings ($mod+Shift+V
# → session `scratch4` → codename `Vapor`). We parse it at runtime so the report
# speaks the SAME names Zach navigates by, and never drifts from a copy.
SCRATCH_SLOTS_FILE = Path(__file__).resolve().parent.parent / "tmux-scratch-slots.sh"
# A SCRATCH_SLOTS entry: "scratch4:V:#83a598:Vapor" (session:key:hex-color:codename).
_SLOT_RE = re.compile(r'"([^":]+):([^":]+):(#[0-9a-fA-F]{6}):([^":]+)"')


def load_scratch_codenames(path: str | os.PathLike | None = None) -> dict[str, str]:
    """Parse `{session: codename}` from tmux-scratch-slots.sh's SCRATCH_SLOTS table.

    Returns e.g. {"scratch4": "Vapor", "scratch11": "wheat", …}. Empty dict on any
    failure (file missing / unreadable) so callers degrade to raw session names — the
    codename layer is a display nicety, never a hard dependency.
    """
    p = Path(path) if path is not None else SCRATCH_SLOTS_FILE
    try:
        text = p.read_text()
    except OSError:
        return {}
    mapping: dict[str, str] = {}
    for session, _key, _color, name in _SLOT_RE.findall(text):
        mapping[session] = name
    return mapping


def pane_id(pane: dict, codenames: dict[str, str] | None = None) -> str:
    """`<session>-<window>` display id for a pane, e.g. `Vapor-2`, `main:8-3`.

    A hotkey-bound scratchpad shows its CODENAME (`scratch4` → `Vapor-2`), matching
    what Zach navigates by. A session with NO codename is the persistent "main tmux"
    (auto-numbered `8`/`2`, host-dependent), marked `main:` so it reads distinctly
    from a scratchpad — `main:8-3`. Falls back to the bare (marked) session name if a
    window index is somehow absent, so the id is never a dangling `session-`.
    """
    raw = pane.get("session", "")
    codename = (codenames or {}).get(raw)
    if codename is not None:
        session = codename
    elif raw:
        session = f"main:{raw}"
    else:
        session = raw
    window = str(pane.get("window", "")).strip()
    return f"{session}-{window}" if window else session


def best_title_match(pane_toks: set[str], initiatives: list[dict]) -> dict | None:
    """Pick the initiative whose slug/title best overlaps a pane title's tokens.

    Scored as (# slug-token overlaps, # extra title-token overlaps). Slug tokens are
    the initiative fingerprint (clawgate, sysredis, wedge, tekton, bitdex); the pane
    title is first stripped of generic action verbs (`TITLE_STOP` in `text_tokens`),
    so a pane can't be linked on "resume"/"review"/"monitor" alone. A candidate
    qualifies with ≥2 slug-token overlaps, OR ≥1 slug-token overlap on a token UNIQUE
    to that initiative among the eligible set, OR ≥2 title-token overlaps. The
    uniqueness gate is what stops a pane linking on ONE token SHARED across siblings
    (e.g. "grafana" in both grafana-alert-drift and alert-chaos-grafana-sqlite) while
    still allowing a single distinctive token (faro, tekton) to match. Best by
    (slug_overlap, title_overlap), then longer slug, then lexically, for determinism.

    LIMITATION: a genuinely multi-topic pane title is attributed to its single
    strongest match and may miss a co-hosted sibling; bag-of-words can't disambiguate.
    Heuristic — read it as "which session is likely on this", not a proof.
    """
    # Document frequency of each token across the eligible set, so a single-token
    # match can require that token to be UNIQUE (df == 1) to this initiative.
    df: dict[str, int] = {}
    for ini in initiatives:
        toks = set(slug_tokens(ini.get("slug", ""))) | set(text_tokens(ini.get("title") or ""))
        for t in toks:
            df[t] = df.get(t, 0) + 1

    best: dict | None = None
    best_key: tuple = (0, 0, 0, "")
    for ini in initiatives:
        slug_t = set(slug_tokens(ini.get("slug", "")))
        title_t = set(text_tokens(ini.get("title") or ""))
        slug_hits = pane_toks & slug_t
        slug_overlap = len(slug_hits)
        title_overlap = len(pane_toks & (title_t - slug_t))
        unique_single = slug_overlap == 1 and df.get(next(iter(slug_hits)), 1) == 1
        if not (slug_overlap >= 2 or unique_single or title_overlap >= 2):
            continue
        key = (slug_overlap, title_overlap,
               len(ini.get("slug", "")), ini.get("slug", ""))
        if key > best_key:
            best_key = key
            best = ini
    return best


def match_tmux_to_initiatives(initiatives: list[dict], panes: list[dict],
                              repos: list[str],
                              wt_map: dict[str, str] | None = None,
                              codenames: dict[str, str] | None = None) -> list[dict]:
    """Attach live tmux sessions to initiatives; return unmatched claude panes.

    Each pane's cwd is resolved to its repo (`resolve_cwd_repo`), and its title is
    matched ONLY against initiatives in that repo (`best_title_match`) — so a pane in
    devrc can't match a civit initiative that happens to share a word. Mutates each
    initiative's `tmux_sessions` (a set of `<session>-<window>` ids, e.g. `8-1`). A
    claude pane that resolves to no initiative is returned as unmatched (live work the
    ledger doesn't cover), with its id/title/repo — the honest mirror of the "no
    session" gap.
    """
    for ini in initiatives:
        ini.setdefault("tmux_sessions", set())
    by_repo: dict[str, list[dict]] = {}
    for ini in initiatives:
        by_repo.setdefault(ini["repo"], []).append(ini)

    unmatched: list[dict] = []
    for pane in panes:
        ptoks = set(text_tokens(pane.get("title", "")))
        repo = resolve_cwd_repo(pane.get("cwd"), repos, wt_map)
        eligible = by_repo.get(repo, []) if repo is not None else []
        ini = best_title_match(ptoks, eligible) if ptoks else None
        if ini is not None:
            ini["tmux_sessions"].add(pane_id(pane, codenames))
        elif pane.get("command", "") == "claude":
            unmatched.append({"id": pane_id(pane, codenames),
                              "title": pane.get("title", ""),
                              "repo": repo})
    return unmatched


# --------------------------------------------------------------------------- #
# git attribution per initiative
# --------------------------------------------------------------------------- #
def attribute_git(initiatives: list[dict], days: int) -> None:
    """Mutate each initiative with commit/PR fields. Caches per-repo gh calls.

    A branch / PR head is awarded to the SINGLE most-specific initiative in its
    repo (`best_matching_initiative`), so sibling slugs sharing a common prefix
    (`app-blocks` vs `app-blocks-followups`) no longer all claim the same branch
    and end up with identical commit/PR counts.
    """
    branch_cache: dict[str, list[str]] = {}
    default_cache: dict[str, str | None] = {}
    open_pr_cache: dict[str, list[dict]] = {}
    merged_pr_cache: dict[str, list[dict]] = {}

    # Group initiatives by repo so "best match" is decided within a repo's own slugs.
    by_repo: dict[str, list[dict]] = {}
    for ini in initiatives:
        by_repo.setdefault(ini["repo"], []).append(ini)
        ini["matching_branches"] = []
        ini["commits"] = 0
        ini["commits_unknown"] = False
        ini["last_commit"] = None
        ini["open_prs"] = []
        ini["merged_prs"] = 0

    for repo, repo_inis in by_repo.items():
        if repo not in branch_cache:
            # Dedup branch names (origin/x + x normalize to the same tail).
            branch_cache[repo] = sorted(set(git_branches(repo)))
            default_cache[repo] = git_default_branch(repo)
            open_pr_cache[repo] = gh_open_prs(repo)
            merged_pr_cache[repo] = gh_merged_prs(repo, days)
        default_branch = default_cache[repo]

        # Each branch → its single best initiative (most-specific slug).
        for b in branch_cache[repo]:
            ini = best_matching_initiative(b, repo_inis)
            if ini is None:
                continue
            ini["matching_branches"].append(b)
            c, lc = git_commits_in_window(repo, b, days, default_branch)
            if c is None:
                ini["commits_unknown"] = True  # unresolvable ref — don't fake 0
                continue
            ini["commits"] += c
            ini["last_commit"] = newest_touch(ini["last_commit"], lc)

        # Each PR head → its single best initiative, same rule.
        for p in open_pr_cache[repo]:
            ini = best_matching_initiative(p.get("headRefName", ""), repo_inis)
            if ini is not None:
                ini["open_prs"].append({"number": p["number"], "title": p.get("title", "")})
        merged_counts: dict[int, int] = {}
        for p in merged_pr_cache[repo]:
            ini = best_matching_initiative(p.get("headRefName", ""), repo_inis)
            if ini is not None:
                merged_counts[id(ini)] = merged_counts.get(id(ini), 0) + 1
        for ini in repo_inis:
            ini["merged_prs"] = merged_counts.get(id(ini), 0)

    for ini in initiatives:
        # "commits:?" only when we have NO confident count at all (every matching
        # branch was unresolvable) — a partial count stays a number.
        ini["commits_unknown"] = ini["commits_unknown"] and ini["commits"] == 0


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def build_report(days: int, repos: list[str] | None = None,
                 client=None, projects_root: str = PROJECTS_ROOT,
                 now: float | None = None, include_tmux: bool = False,
                 panes: list[dict] | None = None) -> dict:
    """Fuse the three sources into a ranked, per-repo report dict.

    `client` may be None (telemetry skipped). `repos` None -> auto-discover.
    `include_tmux` links live tmux sessions to initiatives; `panes` overrides the
    live `collect_tmux_panes()` read (for tests / reproducibility).
    """
    repos = repos if repos is not None else discover_repos()
    initiatives = load_initiatives(repos)

    attribute_git(initiatives, days)
    genesis = session_genesis_refs(projects_root, days)
    attribute_sessions(initiatives, genesis)

    telem_rows = fetch_telemetry(client, days) if client is not None else None
    telemetry_available = telem_rows is not None
    # Resolve cwds living in linked worktrees back to their canonical repo so
    # worktree telemetry attributes to the parent and `(unknown repo)` shrinks.
    wt_map = worktree_canonical_map(repos) if (telem_rows or include_tmux) else {}
    catchall = attribute_telemetry(initiatives, telem_rows, repos, wt_map)

    tmux_unmatched: list[dict] = []
    tmux_active = False
    if include_tmux:
        live_panes = panes if panes is not None else collect_tmux_panes()
        # A live read that finds NO panes = no tmux server on this host -> suppress the
        # column entirely (annotating every initiative "[no session]" would be noise).
        # An explicitly-injected pane list (tests) always activates, even when empty.
        if panes is not None or live_panes:
            tmux_active = True
            codenames = load_scratch_codenames()
            tmux_unmatched = match_tmux_to_initiatives(initiatives, live_panes, repos,
                                                       wt_map, codenames)

    # Compute momentum from the MAX of every touch signal. Note doc freshness comes
    # from the handoff's AUTHORED date (doc_touch_epoch), not filesystem mtime, which
    # a bulk git checkout clobbers. A LIVE tmux session on the initiative counts as
    # touched-now — open work is active regardless of how old its handoff is.
    now_epoch = now if now is not None else time.time()
    for ini in initiatives:
        live_session = now_epoch if ini.get("tmux_sessions") else None
        ini["last_touch"] = newest_touch(
            ini.get("last_commit"),
            ini.get("telem_last"),
            ini.get("last_session"),
            doc_touch_epoch(ini),
            live_session,
        )
        ini["momentum"] = classify_momentum(ini["last_touch"], now)

    # Window-filter: `--days N` now means "in flight within the last N days" — keep
    # only initiatives whose newest touch is inside the window (a live tmux session
    # keeps it regardless). Widen `--days` to resurface older / stalled work. This is
    # what stops months-old done handoffs from surfacing in a short-window view.
    cutoff = now_epoch - days * DAY
    initiatives = [i for i in initiatives
                   if i.get("last_touch") is not None and i["last_touch"] >= cutoff]

    initiatives = sort_initiatives(initiatives)

    # Sets aren't JSON-serializable and the renderer wants a stable order.
    if tmux_active:
        for ini in initiatives:
            ini["tmux_sessions"] = sorted(ini.get("tmux_sessions", set()),
                                          key=_tmux_session_sort_key)

    by_repo: dict[str, list[dict]] = {}
    for ini in initiatives:
        by_repo.setdefault(ini["repo"], []).append(ini)

    return {
        "days": days,
        "telemetry_available": telemetry_available,
        "tmux_enabled": tmux_active,
        "tmux_unmatched": tmux_unmatched,
        "repos": repos,
        "by_repo": by_repo,
        "catchall": {k: {"events": v["events"], "last": v["last"],
                         "branches": sorted(v["branches"])}
                     for k, v in catchall.items()},
    }


def _tmux_session_sort_key(name: str) -> tuple:
    """Order `<session>-<window>` ids naturally: '1','8-1','8-3','scratch2','scratch10'.

    Peels a trailing `-<window>` (greedy, so a dashed session like
    `scratch-1774833757-1` splits at the LAST dash), then sorts the session part by
    (non-digit prefix, numeric suffix) so a numeric tail sorts by VALUE
    ('scratch2' < 'scratch10') and pure-numeric sessions ('8') sort ahead of prefixed
    ones — with the window index as the final tiebreak ('8-1' before '8-3').
    """
    win = -1
    session = name
    mw = re.match(r"^(.*)-(\d+)$", name)
    if mw:
        session, win = mw.group(1), int(mw.group(2))
    m = re.match(r"^(.*?)(\d*)$", session)
    prefix = m.group(1) if m else session
    num = int(m.group(2)) if (m and m.group(2)) else -1
    return (prefix, num, win, name)


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
    out.append(f"   Showing only initiatives touched in the last {days}d "
               "(or with a live tmux session); widen --days to resurface older/stalled work.")

    repo_names = sorted(report["by_repo"].keys()) or report.get("repos", [])
    for repo in repo_names:
        inis = report["by_repo"].get(repo, [])
        short = _short_repo(repo)
        out.append(f"\n## {short}   ({len(inis)} initiative{'s' if len(inis) != 1 else ''})")
        if not inis:
            out.append("   (handoffs present but none parsed)")
        for ini in inis:
            tag = MOMENTUM_TAG.get(ini["momentum"], "?")
            commits_str = "?" if ini.get("commits_unknown") else str(ini.get("commits", 0))
            head = (f"  {tag}  {ini['slug']}"
                    f"   touched {rel_age(ini.get('last_touch'), now)}"
                    f"   sess:{ini.get('session_count', 0)}"
                    f"   commits:{commits_str}"
                    f"   merged-PR:{ini.get('merged_prs', 0)}")
            if report["telemetry_available"]:
                head += f"   ev:{ini.get('telem_events', 0)}"
            if report.get("tmux_enabled"):
                sess = ini.get("tmux_sessions") or []
                head += f"   [tmux:{','.join(sess)}]" if sess else "   [no session]"
            out.append(head)
            if ini.get("title") and ini["title"] != ini["slug"]:
                out.append(f"        “{ini['title']}”")
            if ini.get("summary"):
                out.append(f"        » {ini['summary'][:160]}")
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

    if report.get("tmux_enabled"):
        unmatched = report.get("tmux_unmatched") or []
        if unmatched:
            out.append("\n## live claude sessions — no matched initiative")
            out.append("   (open work the ledger doesn't cover — a new thread, or a "
                       "handoff not yet written)")
            seen = set()
            for u in sorted(unmatched, key=lambda u: _tmux_session_sort_key(u.get("id", ""))):
                key = (u.get("id"), u.get("title"))
                if key in seen:
                    continue
                seen.add(key)
                repo = _short_repo(u["repo"]) if u.get("repo") else "?"
                title = (u.get("title") or "").strip() or "(untitled)"
                out.append(f"  [{u.get('id', '?')}]  {title[:80]}   ({repo})")

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
    p.add_argument("--tmux", action="store_true",
                   help="link each initiative to the live tmux session(s) hosting it")
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

    report = build_report(a.days, repos=repos, client=client, include_tmux=a.tmux)

    if a.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
