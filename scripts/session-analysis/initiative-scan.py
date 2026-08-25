#!/usr/bin/env python3
"""initiative-scan — durable, cross-session tracker of in-flight *initiatives*.

The gap this fills: Zach runs many multi-session work threads ("initiatives") —
"App Blocks soft-launch", "sysRedis HA", "mail-automation", "dp-prod 500-floor".
Each is anchored by a handoff doc (`claudedocs/handoff-<slug>.md`) and spans many
Claude Code sessions over days. The bar's live Claude-runs pill shows
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
  initiative-scan.py [--days N] [--json] [--repo PATH] [--tmux] [--exclude-slugs S1,S2,...]
  --days   trailing window in days (default 5)
  --json   machine-readable output (the raw per-initiative data)
  --repo   restrict to a single repo path (default: auto-discover under ~/workspace)
  --tmux   link each initiative to the live tmux session(s) hosting it (matches the
           claude pane title against the initiative slug/title, scoped by the pane's
           cwd→repo); also lists live claude sessions with no matched initiative.
           Best-effort: no tmux server -> initiatives simply show [no session].
  --exclude-slugs  comma-separated initiative slugs to suppress from the report, e.g.
           "observability-gaps-audit,evaluate-script-then-validate". Matches the base
           slug derived from the handoff filename — the `[slug]` shown on each row.
           EXPLICIT ONLY: the scan never infers that an initiative is finished, so
           nothing disappears from this report unless you name it here.

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
import shutil
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

# Recent-user-message + recent-commit surfacing (Phase A card legibility). Zach works
# entirely via agents, so his own recent prompts are the highest-signal "what is this"
# for an initiative. These are collected DETERMINISTICALLY (no LLM) — a later Phase B
# may add an LLM recap on top, so the raw fields are kept cleanly reusable.
RECENT_TURNS_PER_SESSION = 5        # last-N genuine user turns kept per attributed session
RECENT_MESSAGES_PER_INITIATIVE = 5  # top-N (newest) messages surfaced per initiative
RECENT_MESSAGE_MAX_CHARS = 200      # per-message truncation so a card stays legible
RECENT_COMMITS_PER_INITIATIVE = 5   # most-recent commit subjects surfaced per initiative

# Session-first initiative discovery (the flip from handoff-doc-only discovery): an
# active Claude SESSION, grouped by (repo, topic), is what makes an initiative EXIST; a
# handoff doc (any filename) is an OPTIONAL title-matched anchor, not a prerequisite. A
# session seeds/joins a group only when it clears this deterministic noise floor — it has
# an ai-title AND >= MIN_TOPIC_TOKENS meaningful topic tokens AND (>= MIN_SESSION_TURNS
# genuine user turns OR git corroboration — see below). Tunable module constants so the
# flood-vs-recall trade-off is one edit away (see the on-real-data validation).
#
# NOISE-FLOOR TUNING (validated on 14d of real transcripts, 206 sessions): the design's
# repo-level telemetry corroboration was measured to NULLIFY the turn floor in the
# firehose repo (datapacket-talos — where nearly every session runs, so the repo is always
# "corroborated"), letting ephemeral one-off sessions ("Yes, dispatch", "commit and push")
# mint initiatives. So MIN_SESSION_TURNS was raised 3->8 AND corroboration was NARROWED
# from repo-level to a PER-SESSION signal: the session did work on a real (non-trunk)
# feature branch. That is topic-specific git corroboration — it rescues a short session
# that is clearly tracked work without blanket-admitting every short session in an active
# repo. A wrong initiative costs more than a missed one (the scan's precision stance).
MIN_TOPIC_TOKENS = 2   # a session's ai-title must yield at least this many topic tokens
# EVERY SESSION IS A FIRST-CLASS CARD (owner decision, 2026-07-27): the turn floor is
# dropped 8->1. The flood the floor once guarded — repo-level telemetry corroboration
# admitting every ephemeral session in a firehose repo — was ALREADY retired in favour of
# per-session branch corroboration (`session_corroborated`), so the floor no longer
# defends against that. Combined with the sessions-only window (5d) and doc-noise removal
# the admitted set stays bounded (~63 cards at 5d). A session is now eligible on an
# ai-title + >=MIN_TOPIC_TOKENS topic tokens + a resolved repo, regardless of turn count;
# MIN_TOPIC_TOKENS STAYS 2 (a card still needs a >=2-token title to be meaningful).
MIN_SESSION_TURNS = 1  # every titled session with >=MIN_TOPIC_TOKENS tokens is a card

# Title-drift MERGE — Pass-2 of build_session_groups (union-find folding same-repo exact
# groups that share a repo-distinctive token; `_should_merge_groups`/`_merge_group_indices`).
# DISABLED by default. On real transcripts the TRANSITIVE union (a drift CHAIN A~B~C folds
# all three) produced token-SALAD "Emerging" cards: several unrelated sessions that each
# shared a single distinctive token pairwise fused into one unrecognizable mega-slug
# (e.g. `both-connect-delete-dispatch-drive-flow-offsite-security-web`), and a session whose
# ai-title drifted got absorbed instead of standing on its own. With it OFF each Pass-1 EXACT
# (repo, topic-token) group becomes its own initiative, and the surfaced `opening_message`
# (the genesis prompt) keeps each one legible regardless of title drift. Flip to True to
# restore the old drift-merge behaviour — the Pass-2 helpers are kept intact, just gated.
ENABLE_TITLE_DRIFT_MERGE = False

# Cap on a surfaced opening (genesis) prompt (chars) so a card's `start ›` line stays legible.
OPENING_MAX = 300

# Cap on a session's `search_text` (chars) — the FULL concatenation of the user's own turn
# texts across the WHOLE session, kept SEARCH-ONLY (never rendered) so a keyword the user typed
# in a MIDDLE turn is findable even though the card only displays the opening + last-N turns.
# Hard-truncated at this cap (no ellipsis — it's an index blob, not display prose).
SEARCH_TEXT_MAX = 6000

# A YYYY-MM-DD date anywhere in a handoff filename stem.
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# "## Next steps" with any trailing decoration (the template varies a little).
NEXT_STEPS_RE = re.compile(r"^##+\s+next steps\b", re.I)
OPEN_INV_RE = re.compile(r"^##+\s+open investigations\b", re.I)
ANY_H2_RE = re.compile(r"^##\s+")
# A heading naming this doc as a handoff/session-handoff — the structural marker that
# lets a NON-`handoff-*.md` file (e.g. `SESSION-HANDOFF.md`) still stand alone as a
# documented-but-dormant initiative even when no live session anchors it. Kept narrow
# (the word "handoff") so 38 misc `claudedocs/*.md` don't flood the ledger.
HANDOFF_HEADING_RE = re.compile(r"^#{1,6}\s+.*\bhandoff\b", re.I)
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


def _cap_opening(s: str | None) -> str:
    """Trim + collapse a genesis prompt; cap at OPENING_MAX chars on a word boundary (adds
    '…'). Returns "" for blank/None (the `opening_message` contract default) — NEVER None, so
    the field is always a string that flows cleanly through the store + viewer."""
    s = (s or "").strip()
    if not s:
        return ""
    if len(s) <= OPENING_MAX:
        return s
    cut = s[:OPENING_MAX].rstrip()
    sp = cut.rfind(" ")
    if sp >= int(OPENING_MAX * 0.6):
        cut = cut[:sp].rstrip()
    return cut + "…"


def _merge_search_texts(texts) -> str:
    """Join several sessions' `search_text` blobs into one, DEDUPING identical lines
    (order-preserving) and HARD-capping at SEARCH_TEXT_MAX. Search-only prose — the raw
    user-turn texts of a group's sessions, never synthesized. "" when nothing survives.

    Line-level dedup keeps a merged/anchored initiative's index from ballooning with the
    same turn repeated across sessions that share history, and keeps concat idempotent (a
    doc adopting the same group twice can't double it)."""
    seen: set[str] = set()
    out: list[str] = []
    for t in texts:
        for line in (t or "").split("\n"):
            if line and line not in seen:
                seen.add(line)
                out.append(line)
    return "\n".join(out)[:SEARCH_TEXT_MAX]


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


def initiative_fingerprint(ini: dict) -> list[str]:
    """The token fingerprint used to attach FREE TEXT (a tmux pane title / router
    signal) to an initiative: its SLUG tokens, or — when the slug is date-only /
    degenerate and yields NO slug tokens — the handoff TITLE tokens as a fallback.

    Why: a bare-date slug (e.g. `handoff-2026-07-21.md` → slug `2026-07-21`) has ZERO
    `slug_tokens` (dates are dropped), so it can never match its own live panes on the
    slug fingerprint — it is structurally invisible to `best_title_match`. Falling back
    to TITLE tokens gives such an initiative *some* fingerprint.

    Precision: STRICTLY ADDITIVE — it only grants matching ability to initiatives that
    currently match NOTHING (a non-empty slug is returned unchanged, so every existing
    match is byte-identical). The caller's df/uniqueness gate still applies to the
    fallback tokens, so a date-only initiative can only claim a pane on a token NO
    eligible sibling shares; and `best_title_match` ranks a real-slug fingerprint ABOVE
    a title fallback, so a fallback can never steal a pane from a real-slug match.
    """
    toks = slug_tokens(ini.get("slug", ""))
    if toks:
        return toks
    return text_tokens(ini.get("title") or "")


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


def _specificity_key(ini: dict) -> tuple:
    """Ranking key for "most-specific initiative": most slug tokens, then longest raw
    slug, then lexical. This is the EXACT tie-break `best_matching_initiative` awards
    branch/PR/telemetry credit by; factored out so message single-crediting
    (attribute_recent_messages) ranks siblings identically instead of inventing a
    second rule."""
    slug = ini.get("slug", "")
    return (len(slug_tokens(slug)), len(slug), slug)


def best_matching_initiative(branch: str, initiatives: list[dict]) -> dict | None:
    """Pick the single most-specific initiative whose slug matches `branch`.

    Among all initiatives whose slug fits the branch (`branch_matches_slug`),
    return the one with the MOST slug tokens (longest / most-specific), so a
    branch like `app-blocks-followups` is credited to the `app-blocks-followups`
    initiative, NOT also to the broader `app-blocks` sibling. Ties (equal token
    count) are broken by the longer raw slug, then lexically, for determinism
    (`_specificity_key`). None if nothing matches.
    """
    candidates = [ini for ini in initiatives
                  if branch_matches_slug(branch, ini.get("slug", ""))]
    if not candidates:
        return None
    return max(candidates, key=_specificity_key)


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


# The CONTRACT: every initiative dict — doc-derived OR session-derived — MUST carry
# this exact key set with these types, because sync.write_snapshot does a positional
# `r[c]` over ROW_COLUMNS and a missing key crashes the store insert. Building EVERY
# initiative through this one function is what guarantees no key can be silently missed;
# doc-only fields degrade to safe defaults for a session-only initiative. `source`,
# `undocumented`, `session_ids`, `_topic_tokens`, `handoff_structured` are discovery
# metadata (NOT stored — sync's report_to_rows only reads the named contract keys).
def _new_initiative(repo: str, slug: str, title: str | None, *,
                    summary: str | None = None, date: str | None = None,
                    doc_mtime: float | None = None, next_step: str | None = None,
                    open_investigations: list | None = None,
                    current_doc: str | None = None, docs: list | None = None,
                    source: str = "doc", undocumented: bool = False,
                    session_ids: set | None = None,
                    opening_message: str = "", search_text: str = "") -> dict:
    return {
        # --- stored contract keys -------------------------------------------- #
        "repo": repo,
        "slug": slug,
        "title": title,
        "summary": summary,
        "date": date,
        "next_step": next_step,
        # The thread's ORIGIN prompt (a real genesis, never invented; trimmed to OPENING_MAX
        # chars) — surfaced on the card's `start ›` line so titling accuracy stops mattering.
        # "" for doc/extra initiatives (their handoff summary already describes them); set
        # from the earliest session's genesis for a session-derived group.
        "opening_message": opening_message,
        # SEARCH-ONLY full-text index: the concatenation of the user's OWN turn texts across
        # the WHOLE session(s) (capped at SEARCH_TEXT_MAX). NEVER rendered — it exists so a
        # keyword typed in a MIDDLE turn (not in the opening or last-N surfaced turns) still
        # matches the card. "" for doc/extra initiatives that anchor no session of their own.
        "search_text": search_text,
        "open_investigations": list(open_investigations or []),
        "current_doc": current_doc,
        "docs": list(docs or []),
        # attribution defaults — overwritten unconditionally by attribute_* / momentum,
        # but pre-set so an initiative that skips a pass never lacks a contract key.
        "momentum": "unknown",
        "last_touch": None,
        "commits": 0,
        "commits_unknown": False,
        "merged_prs": 0,
        "open_prs": [],
        "session_count": 0,
        "telem_events": 0,
        "telem_last": None,
        "recent_messages": [],
        "recent_commits": [],
        # --- internal / freshness (already emitted today; sync ignores) ------- #
        "doc_mtime": doc_mtime,
        # --- discovery metadata (Commit A: emitted in --json, NOT stored) ----- #
        "source": source,          # "doc" | "session" | "both"
        "undocumented": undocumented,
        "session_ids": set(session_ids or ()),
    }


def cluster_handoffs(docs: list[dict]) -> list[dict]:
    """Group parsed handoff docs by (repo, base_slug); newest doc = current state.

    `docs` items have: repo, slug, date (str|None), mtime (float), title, next_step,
    open_investigations, path. Returns one initiative dict per cluster, carrying the
    newest doc's parsed fields + a list of all member doc paths/dates. Slug is the
    filename base slug (byte-identical across runs) — this is the hard stability
    guarantee the store/recaps depend on.
    """
    groups: dict[tuple, list[dict]] = {}
    for d in docs:
        groups.setdefault((d["repo"], d["slug"]), []).append(d)

    initiatives = []
    for (repo, slug), members in groups.items():
        members.sort(key=_doc_sort_key, reverse=True)
        cur = members[0]
        initiatives.append(_new_initiative(
            repo=repo, slug=slug, title=cur["title"], summary=cur["summary"],
            date=cur["date"], doc_mtime=cur["mtime"], next_step=cur["next_step"],
            open_investigations=cur["open_investigations"], current_doc=cur["path"],
            docs=[{"path": m["path"], "date": m["date"]} for m in members],
            source="doc"))
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
# Session-first discovery — group active Claude sessions into initiatives
# (deterministic, no LLM; reuses text_tokens + the best_title_match df bar).
# --------------------------------------------------------------------------- #
def topic_tokens(rec: dict) -> frozenset:
    """The TOPIC fingerprint of a session: `text_tokens` of its ai-title (Claude Code's
    auto-title), falling back to its last-prompt then genesis.

    ai-title is the human-readable "what is this session about" that Claude Code writes
    into the transcript (`{"type":"ai-title","aiTitle":…}`) — the highest-signal topic
    identifier with NO LLM call. The same `text_tokens` used for pane/title matching, so
    a session groups with a handoff/pane on word equality (dates/verbs/stop/short dropped).
    """
    src = rec.get("ai_title") or rec.get("last_prompt") or rec.get("genesis") or ""
    return frozenset(text_tokens(src))


def session_corroborated(rec: dict) -> bool:
    """Per-session git corroboration: did this session do work on a real (non-trunk)
    feature branch? A tracked feature branch is strong evidence of a real initiative
    (vs a throwaway on trunk/main). Narrower than repo-level telemetry corroboration,
    which floods a firehose repo (see the noise-floor tuning note)."""
    b = (rec.get("branch") or "").strip()
    if not b:
        return False
    return b.lower() not in {x.lower() for x in TRUNK_BRANCHES}


def session_eligible(rec: dict, corroborated: bool) -> bool:
    """Does a session clear the noise floor to seed/join a group? (deterministic).

    Requires an ai-title AND >= MIN_TOPIC_TOKENS topic tokens AND (>= MIN_SESSION_TURNS
    genuine user turns OR `corroborated`). `corroborated` is the caller's git-corroboration
    verdict (`session_corroborated`) — a short but branch-tracked session still qualifies,
    while a short throwaway on trunk needs the full turn count.
    """
    if not (rec.get("ai_title") or "").strip():
        return False
    if len(topic_tokens(rec)) < MIN_TOPIC_TOKENS:
        return False
    if int(rec.get("n_turns") or 0) >= MIN_SESSION_TURNS:
        return True
    return corroborated


def _should_merge_groups(a: frozenset, b: frozenset, df: dict[str, int]) -> bool:
    """Merge two same-repo session groups (title drift) iff they share a repo-DISTINCTIVE
    token — one present in <= 2 of the repo's exact groups (i.e. essentially only this
    pair). A shared token always has df >= 2, so `df <= 2` means "unique to this pair".

    This is the CONSERVATIVE reading of best_title_match's bar: a family of siblings that
    all share a generic prefix (`app-blocks`, `app-blocks-followups`, `app-blocks-ux` →
    {app,blocks} shared with df=3) does NOT fuse, because {app,blocks} appear in >2 groups;
    but genuine title drift (`ComfyUI realism pipeline` vs `ComfyUI NSFW pipeline`, sharing
    {comfyui,pipeline} present only in those two) does. Deliberately stricter than a raw
    ">=2 shared tokens" bar — a wrong fuse silently merges two distinct initiatives, which
    costs more than leaving near-duplicates split (matches the scan's precision stance).
    """
    shared = a & b
    return any(df.get(t, 0) <= 2 for t in shared)


def _merge_group_indices(tok_sets: list[frozenset], df: dict[str, int]) -> list[list[int]]:
    """Union-find over a repo's exact-group token sets; returns lists of member indices.

    Pairs that clear `_should_merge_groups` are unioned (transitively — a drift CHAIN
    A~B~C folds all three). Deterministic: `tok_sets` is pre-sorted by the caller and the
    O(n^2) pass is order-independent given fixed df."""
    parent = list(range(len(tok_sets)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(tok_sets)):
        for j in range(i + 1, len(tok_sets)):
            if _should_merge_groups(tok_sets[i], tok_sets[j], df):
                parent[find(i)] = find(j)

    clusters: dict[int, list[int]] = {}
    for i in range(len(tok_sets)):
        clusters.setdefault(find(i), []).append(i)
    return list(clusters.values())


def _make_group_initiative(repo: str, records: list[dict],
                           union_tokens: frozenset) -> dict:
    """One merged session group -> a session-derived initiative (the FULL contract set).

    slug = the sorted merged token set ("-".join) — stable across runs and session order.
    title/summary come from the group's MOST-RECENT session's raw ai-title (so the card
    face isn't blank). `session_ids` credits the group's sessions in attribution even
    though no genesis/handoff names its token-slug. source="session", undocumented=True —
    a later doc anchor may flip these to "both"."""
    def _rec_ts(r: dict) -> float:
        return r.get("last_user_ts") or r.get("mtime") or 0.0

    newest = max(records, key=_rec_ts)
    # The EARLIEST session's genesis is the original ask that started the thread — surfaced
    # as `opening_message` so a drifted ai-title never makes the card unrecognizable.
    earliest = min(records, key=_rec_ts)
    opening = _cap_opening(earliest.get("genesis"))
    slug = "-".join(sorted(union_tokens))
    ai_title = (newest.get("ai_title") or "").strip()
    session_ids = {r["session_id"] for r in records if r.get("session_id")}
    # SEARCH-ONLY full text: pool the FULL user-turn text of EVERY session in the group
    # (chronological, so the merged index reads oldest→newest), dedup lines, re-cap.
    search_text = _merge_search_texts(
        r.get("search_text") or "" for r in sorted(records, key=_rec_ts))
    ini = _new_initiative(
        repo=repo, slug=slug, title=ai_title or slug,
        summary=_cap_summary(ai_title) if ai_title else None,
        source="session", undocumented=True, session_ids=session_ids,
        opening_message=opening, search_text=search_text)
    ini["_topic_tokens"] = frozenset(union_tokens)
    # Internal (dropped before emit): the genesis ts, so a doc anchor folding SEVERAL groups
    # can adopt the EARLIEST opening deterministically (combine_docs_and_groups).
    ini["_opening_ts"] = _rec_ts(earliest)
    return ini


def build_session_groups(records: list[dict],
                         corroborated_repos: set[str] | None = None) -> list[dict]:
    """Group in-window Claude sessions into session-derived initiatives (per repo).

    Two deterministic passes (per the discovery design):
      1. EXACT — eligible sessions with an identical topic-token set (same repo) group.
      2. MERGE — same-repo exact-groups sharing a repo-distinctive token fold together
         (title drift; `_should_merge_groups`), via union-find. GATED behind
         `ENABLE_TITLE_DRIFT_MERGE` (default False): the transitive union produced
         token-salad Emerging cards, so by default each Pass-1 exact group stands alone.
    Each record must already carry a resolved `repo` (None → skipped) plus ai_title /
    last_prompt / genesis / n_turns / session_id / last_user_ts / mtime / branch. Corroboration
    is PER-SESSION (`session_corroborated` — a real feature branch), NOT repo-level (which was
    measured to flood the firehose repo); `corroborated_repos` is accepted for signature
    compatibility but no longer widens eligibility. Returns session-derived initiative dicts
    (source="session"); anchoring to docs happens later.
    """
    eligible = [r for r in records
                if r.get("repo")
                and session_eligible(r, session_corroborated(r))]

    # Pass 1: exact groups keyed (repo, topic_tokens).
    exact: dict[tuple, list[dict]] = {}
    for r in eligible:
        exact.setdefault((r["repo"], topic_tokens(r)), []).append(r)

    # Pass 2: per-repo union-find merge on repo-distinctive shared tokens.
    keys_by_repo: dict[str, list[frozenset]] = {}
    for (repo, toks) in exact:
        keys_by_repo.setdefault(repo, []).append(toks)

    groups: list[dict] = []
    for repo, tok_sets in keys_by_repo.items():
        tok_sets = sorted(tok_sets, key=lambda s: sorted(s))  # deterministic order
        if not ENABLE_TITLE_DRIFT_MERGE:
            # Pass-2 OFF (default): each EXACT group is its own initiative — no drift-merge
            # union, so distinct sessions can't fuse into a token-salad card.
            for toks in tok_sets:
                groups.append(_make_group_initiative(repo, exact[(repo, toks)], toks))
            continue
        df: dict[str, int] = {}
        for s in tok_sets:
            for t in s:
                df[t] = df.get(t, 0) + 1
        for member_idxs in _merge_group_indices(tok_sets, df):
            member_records: list[dict] = []
            union_tokens: set = set()
            for i in member_idxs:
                member_records.extend(exact[(repo, tok_sets[i])])
                union_tokens |= tok_sets[i]
            groups.append(_make_group_initiative(repo, member_records,
                                                 frozenset(union_tokens)))
    return groups


def _doc_is_handoff_structured(text: str, is_handoff: bool) -> bool:
    """Does a doc STRUCTURALLY look like a handoff (so a NON-`handoff-*.md` file may stand
    alone as a documented-dormant initiative)? True for any `handoff-*.md`, or a doc with a
    parseable Next-steps item, or a heading naming it a handoff. Narrow on purpose — it
    gates the ~38 misc `claudedocs/*.md` out of the ledger unless a live session anchors
    them (an anchor overrides this gate)."""
    if is_handoff:
        return True
    if parse_next_step(text) is not None:
        return True
    for line in text.splitlines():
        if HANDOFF_HEADING_RE.match(line):
            return True
    return False


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


def git_toplevel(cwd: str | None) -> str | None:
    """`git -C cwd rev-parse --show-toplevel` (realpath'd), or None if `cwd` is not inside
    a git worktree. A cwd of a bare `~` / non-repo dir yields no toplevel → None → dropped,
    so session/telemetry activity that isn't in a repo can't mint a phantom repo."""
    if not cwd:
        return None
    out = _run(["git", "-C", cwd, "rev-parse", "--show-toplevel"]).strip()
    if not out:
        return None
    try:
        return os.path.realpath(out)
    except OSError:
        return out


def discover_repos(workspace: str = WORKSPACE,
                   session_cwds: list[str] | None = None,
                   telem_cwds: list[str] | None = None) -> list[str]:
    """The repo universe = UNION of three sources, worktree-collapsed to canonical repos:

      1. dirs with any TOP-LEVEL `claudedocs/*.md` (top + one nested level under
         ~/workspace) — the historical doc-glob set, now ANY `*.md` not only `handoff-*`;
      2. `git_toplevel` of every in-window Claude-session cwd (session-first discovery —
         this is what surfaces a repo like `civit/civitai-manager` whose handoff doc is
         named `SESSION-HANDOFF.md`, invisible to the doc glob);
      3. `git_toplevel` of every telemetry cwd.

    Worktrees of the SAME repository (same `git rev-parse --git-common-dir`) collapse to
    ONE canonical repo (`_dedup_worktrees`); a non-git candidate survives as its own repo.
    Backward-compatible: called with no cwds (viewer/route) it returns the doc-glob set."""
    cands: set[str] = set(_candidate_repo_dirs(workspace))
    for cwd in set(session_cwds or ()) | set(telem_cwds or ()):
        top = git_toplevel(cwd)
        if top:
            cands.add(top)
    return _dedup_worktrees(sorted(cands))


def _candidate_repo_dirs(workspace: str) -> list[str]:
    """Raw candidate repo dirs (pre-dedup): parents of any top-level `claudedocs/*.md`
    (top + one nested level; subdir files under claudedocs/ are ignored)."""
    cands: set[str] = set()
    patterns = [
        os.path.join(workspace, "*", "claudedocs", "*.md"),
        os.path.join(workspace, "*", "*", "claudedocs", "*.md"),
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
    """Parse one doc (ANY top-level `claudedocs/*.md`, not just `handoff-*.md`) into its
    initiative-registry fields. `is_handoff` / `handoff_structured` let the caller decide
    whether a NON-handoff-named doc may stand alone (see `_doc_is_handoff_structured`)."""
    name = os.path.basename(path)
    slug, date = parse_handoff_filename(name)
    is_handoff = name.startswith("handoff-") or name == "handoff.md"
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
        "is_handoff": is_handoff,
        "handoff_structured": _doc_is_handoff_structured(text, is_handoff),
    }


def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def load_initiatives(repos: list[str]) -> list[dict]:
    """Discover + parse + cluster `handoff-*.md` docs across the given repos.

    Deliberately still `handoff-*.md` ONLY (not every `*.md`): these are the canonical
    handoff registry whose base-slug clustering must stay BYTE-IDENTICAL for the store /
    recaps. Non-handoff `claudedocs/*.md` are handled by `load_extra_doc_initiatives` so
    they only surface when a session anchors them (or they're handoff-structured)."""
    docs = []
    for repo in repos:
        for path in sorted(glob.glob(os.path.join(repo, "claudedocs", "handoff-*.md"))):
            docs.append(read_handoff(path))
    return cluster_handoffs(docs)


def _doc_to_initiative(d: dict, source: str = "doc") -> dict:
    """One parsed non-handoff doc -> a single-doc initiative (full contract set)."""
    ini = _new_initiative(
        repo=d["repo"], slug=d["slug"], title=d["title"], summary=d["summary"],
        date=d["date"], doc_mtime=d["mtime"], next_step=d["next_step"],
        open_investigations=d["open_investigations"], current_doc=d["path"],
        docs=[{"path": d["path"], "date": d["date"]}], source=source)
    ini["handoff_structured"] = bool(d.get("handoff_structured"))
    return ini


def load_extra_doc_initiatives(repos: list[str]) -> list[dict]:
    """Non-`handoff-*.md` top-level `claudedocs/*.md` as one initiative per doc.

    These are ANCHOR CANDIDATES for session groups (a matching group flips one to
    source="both" and enriches its card) and, when handoff-structured, can stand alone as
    documented-dormant initiatives. Kept separate from the handoff cluster so existing
    handoff slugs never change. Skips the `handoff-*` files owned by `load_initiatives`."""
    out: list[dict] = []
    for repo in repos:
        for path in sorted(glob.glob(os.path.join(repo, "claudedocs", "*.md"))):
            name = os.path.basename(path)
            if name.startswith("handoff-") or name == "handoff.md":
                continue
            out.append(_doc_to_initiative(read_handoff(path)))
    return out


def combine_docs_and_groups(doc_inis: list[dict], extra_inis: list[dict],
                            groups: list[dict]) -> list[dict]:
    """Fuse doc initiatives with session-derived groups — SESSIONS ONLY (docs ENRICH,
    never create/float).

    For each session group, find its single best-overlapping doc/extra initiative in the
    SAME repo (`best_title_match` with the group's topic tokens as the query — the inverse
    indexing of the design's phrasing, chosen so a group adopts exactly one anchor slug
    unambiguously; the qualification bar is identical). On a match the group folds into
    that doc initiative (session_ids merged, source="both", adopting the doc's
    slug/summary/next_step/open_investigations for the expanded detail); with no match the
    group stands alone (source="session", undocumented).

    SESSIONS-ONLY EMIT (owner decision): a card is EMITTED only when a session backs it.
    Every doc/extra initiative that did NOT get anchored by a session (i.e. stayed
    source="doc") is DROPPED here — a handoff with no live session behind it must never
    become a card, and (handled in `main`) a doc's mtime must never time a card. Docs
    still ANCHOR + ENRICH (a matched handoff flips its group to source="both" and lends
    its summary/next_step), but a standalone doc no longer floats to the board. Net: the
    returned `source` set is exactly {session, both} — zero pure `doc`.
    """
    docs_by_repo: dict[str, list[dict]] = {}
    for d in doc_inis + extra_inis:
        docs_by_repo.setdefault(d["repo"], []).append(d)

    standalone: list[dict] = []
    for grp in groups:
        cands = docs_by_repo.get(grp["repo"], [])
        anchor = best_title_match(set(grp.get("_topic_tokens") or ()), cands) if cands else None
        if anchor is not None:
            anchor["session_ids"] |= grp.get("session_ids") or set()
            anchor["source"] = "both"
            anchor["undocumented"] = False
            # An anchored doc adopts the folding session group's ORIGIN prompt IF the doc
            # carries none of its own — so the card still shows where the thread began. When
            # several groups fold into one doc, the EARLIEST genesis (min `_opening_ts`) wins,
            # so the result is independent of group iteration order (deterministic). The temp
            # `_adopt_*` keys are applied after the loop and never emitted.
            if not (anchor.get("opening_message") or "").strip():
                grp_open = grp.get("opening_message") or ""
                if grp_open.strip():
                    grp_ts = grp.get("_opening_ts") or 0.0
                    if anchor.get("_adopt_ts") is None or grp_ts < anchor["_adopt_ts"]:
                        anchor["_adopt_open"] = grp_open
                        anchor["_adopt_ts"] = grp_ts
            # SEARCH-ONLY: every folding group contributes its full-text index to the doc.
            # Accumulated (not earliest-wins like opening) as (ts, text) so the merge reads
            # oldest→newest deterministically regardless of group iteration order.
            grp_search = grp.get("search_text") or ""
            if grp_search.strip():
                anchor.setdefault("_adopt_search", []).append(
                    (grp.get("_opening_ts") or 0.0, grp_search))
        else:
            standalone.append(grp)

    # Apply the earliest-wins adopted opening to each doc that had none of its own.
    for d in doc_inis + extra_inis:
        adopted = d.pop("_adopt_open", None)
        d.pop("_adopt_ts", None)
        if adopted and not (d.get("opening_message") or "").strip():
            d["opening_message"] = adopted
        # Append the folded groups' full text onto the doc's own search_text (doc default ""),
        # ordered oldest→newest, dedup + re-cap via _merge_search_texts.
        adopt_search = d.pop("_adopt_search", None)
        if adopt_search:
            ordered = [t for _ts, t in sorted(adopt_search, key=lambda x: x[0])]
            d["search_text"] = _merge_search_texts([d.get("search_text") or ""] + ordered)

    # SESSIONS-ONLY: keep a doc/extra initiative ONLY when a session anchored it
    # (source flipped to "both"). Un-anchored handoff clusters (source="doc") and
    # standalone / handoff-structured extras that no session touched are DROPPED — a
    # doc never creates a card. `standalone` are the un-anchored session groups
    # (source="session"). Result: source ∈ {both, session} only.
    kept_docs = [d for d in doc_inis if d["source"] == "both"]
    kept_extras = [d for d in extra_inis if d["source"] == "both"]
    return kept_docs + kept_extras + standalone


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


def git_recent_commit_subjects(repo: str, branch: str, since_days: int,
                               default_branch: str | None = None,
                               limit: int = RECENT_COMMITS_PER_INITIATIVE
                               ) -> list[tuple[float, str]]:
    """[(epoch, subject)] for the most-recent commits UNIQUE to `branch` in the window,
    newest-first, capped at `limit`.

    Mirrors git_commits_in_window's ref resolution + default-branch exclusion EXACTLY
    (so a feature branch is credited only with ITS OWN subjects, not the whole trunk),
    just capturing `%s` subjects instead of counting. The default branch (unsegmented
    catch-all) and an unresolvable ref both yield []. A NUL (`%x00`) field separator so a
    subject containing whitespace/tabs can't split the parse."""
    if default_branch and branch.lower() == default_branch.lower():
        return []
    target = _resolve_branch_ref(repo, branch)
    if target is None:
        return []
    cmd = ["git", "-C", repo, "log", target, "--no-merges",
           f"--since={since_days} days ago", "--format=%ct%x00%s"]
    if default_branch and default_branch.lower() != branch.lower():
        excludes = []
        for ref in (default_branch, f"origin/{default_branch}"):
            if _ref_exists(repo, ref) and ref not in excludes:
                excludes.append(ref)
        if excludes:
            cmd += ["--not", *excludes]
    out = _run(cmd)
    res: list[tuple[float, str]] = []
    for ln in out.splitlines():
        if "\x00" not in ln:
            continue
        cts, subj = ln.split("\x00", 1)
        cts, subj = cts.strip(), subj.strip()
        if cts.isdigit() and subj:
            res.append((float(cts), subj))
    return res[:limit]


def gh_available() -> bool:
    """Can we read PRs at all?

    `gh_open_prs`/`gh_merged_prs` return `[]` on ANY failure, so a repo with no
    open PRs and a host where `gh` is absent or unauthenticated produce the
    IDENTICAL `open_prs: []`. That ambiguity is reported (not resolved) via the
    report's `gh_available` flag: with it False, every PR field in the report is
    "not wired up here", not "measured zero". One probe per report.
    """
    if not shutil.which("gh"):
        return False
    try:
        return subprocess.run(["gh", "auth", "status"], capture_output=True,
                              timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


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


def _extract_user_text(content) -> str | None:
    """The text of a user message's `content` (str form, or the first text block of a
    list form). None when there's no text block. Shared by every transcript reader so
    genesis + recent-turn extraction agree on what a user turn's text IS."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                return b.get("text", "")
    return None


def _truncate(s: str, n: int) -> str:
    """Trim/ellipsize a string to at most `n` chars (with a trailing … when cut)."""
    s = (s or "").strip()
    return s if len(s) <= n else s[: max(0, n - 1)].rstrip() + "…"


def _read_session_turns(path: str, n: int) -> dict | None:
    """Parse ONE transcript into {genesis, turns, last_user_ts, cwd, branch, ai_title,
    last_prompt, n_turns}, or None if it has no genuine user turn.

    Session-first discovery additions (all deterministic, no LLM):
    - ai_title: the LAST `{"type":"ai-title","aiTitle":…}` line — Claude Code's
      human-readable auto-title, the topic signal groups form on (`topic_tokens`).
    - last_prompt: the LAST `{"type":"last-prompt","lastPrompt":…}` line (topic fallback).
    - n_turns: the TOTAL count of genuine user turns (independent of the `n`-turn window),
      so the MIN_SESSION_TURNS noise floor can gate short throwaway sessions.

    - genesis: the FIRST genuine user turn text (the SAME rule _first_user_turn used —
      skips harness/system-reminder/command/interrupt/caveat noise via _clean_turn).
    - turns: [{text, ts}] for the LAST `n` genuine user turns (oldest→newest), each
      cleaned; `ts` is the turn's UTC epoch (from its ISO `timestamp`), or None.
    - last_user_ts: the MAX UTC epoch across ALL genuine user turns (independent of the
      `n`-turn window) — the "last real interaction" time. This is the momentum-safe
      touch signal: unlike the transcript's filesystem mtime it is NOT bumped when
      Claude Code rewrites the .jsonl in place for metadata maintenance (title/mode/
      permission), which otherwise makes an idle session read as freshly touched. None
      only when NO genuine turn carried a parseable `timestamp` (rare/edge). Mirrors the
      `doc_touch_epoch` rule that avoids mtime for the same class of bulk-rewrite reason.
    - cwd / branch: from the MOST-RECENT turn that carries them (each user turn records
      `cwd` + `gitBranch`) — used by the branch/cwd attribution fallback.
    Reads the file ONCE (transcript reads are the costly part of the scan)."""
    try:
        with open(path, errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    genesis: str | None = None
    turns: list[dict] = []
    search_parts: list[str] = []   # EVERY genuine user turn's text (search_text, not last-n)
    last_user_ts: float | None = None
    cwd: str | None = None
    branch: str | None = None
    ai_title: str | None = None
    last_prompt: str | None = None
    n_turns = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        otype = obj.get("type")
        if otype == "ai-title":
            at = obj.get("aiTitle")
            if at:
                ai_title = at  # LAST ai-title wins (the title as it currently stands)
            continue
        if otype == "last-prompt":
            lp = obj.get("lastPrompt")
            if lp:
                last_prompt = lp
            continue
        if otype != "user" or obj.get("isMeta") or obj.get("isSidechain"):
            continue
        msg = obj.get("message") or {}
        if msg.get("role") != "user":
            continue
        raw = _extract_user_text(msg.get("content"))
        if not raw:
            continue
        txt = _clean_turn(raw)
        if not txt or txt.lstrip().startswith(HARNESS):
            continue
        if txt.startswith("[Request interrupted") or txt.startswith("Caveat: The messages below"):
            continue
        if genesis is None:
            genesis = txt
        n_turns += 1
        # search_text: the user's OWN words across the WHOLE session (every genuine turn,
        # not just the last-n window kept for display) — search-only, capped on return.
        search_parts.append(txt)
        ts = _iso_to_epoch(obj.get("timestamp"))
        turns.append({"text": txt, "ts": ts})
        if ts is not None:
            last_user_ts = ts if last_user_ts is None else max(last_user_ts, ts)
        c = obj.get("cwd")
        if c:
            cwd = c
        b = obj.get("gitBranch")
        if b:
            branch = b
    if genesis is None:
        return None
    return {"genesis": genesis, "turns": turns[-n:] if n > 0 else [],
            "search_text": "\n".join(search_parts)[:SEARCH_TEXT_MAX],
            "last_user_ts": last_user_ts, "cwd": cwd, "branch": branch,
            "ai_title": ai_title, "last_prompt": last_prompt, "n_turns": n_turns}


def _first_user_turn(path: str) -> str | None:
    """The first genuine user turn of a transcript (back-compat thin wrapper)."""
    rec = _read_session_turns(path, 1)
    return rec["genesis"] if rec else None


def collect_session_records(projects_root: str, since_days: int,
                            n: int = RECENT_TURNS_PER_SESSION) -> list[dict]:
    """Walk every transcript touched in the window ONCE; per session return
    {genesis, mtime, cwd, branch, turns}.

    The SUPERSET of session_genesis_refs: it additionally carries the last-`n` user
    turns (recent-message attribution) and the session's cwd/branch (the branch/cwd
    attribution fallback). One walk feeds BOTH attribute_sessions and
    attribute_recent_messages so transcripts aren't read twice."""
    cutoff = time.time() - since_days * DAY
    out: list[dict] = []
    for path in glob.glob(os.path.join(projects_root, "**", "*.jsonl"), recursive=True):
        if "/subagents/" in path or "/wf_" in path:
            continue
        mt = _safe_mtime(path)
        if mt < cutoff:
            continue
        rec = _read_session_turns(path, n)
        if rec is None:
            continue
        rec["mtime"] = mt
        # The transcript filename stem IS the sessionId — the stable per-session key that
        # credits a session-derived group's sessions in attribution (no genesis names a
        # session-only token-slug).
        rec["session_id"] = Path(path).stem
        out.append(rec)
    return out


def session_genesis_refs(projects_root: str, since_days: int) -> list[dict]:
    """[{text, mtime, last_user_ts}] over each in-window session's genesis (first genuine
    user turn).

    A thin adapter over collect_session_records so the transcript walk is single-sourced
    (attribute_sessions still consumes exactly this shape). `last_user_ts` (the last
    genuine user-turn epoch) is carried alongside `mtime` so attribute_sessions can time
    `last_session` from real interaction, not the mtime that in-place .jsonl rewrites bump."""
    return [{"text": r["genesis"], "mtime": r["mtime"],
             "last_user_ts": r.get("last_user_ts"),
             "session_id": r.get("session_id")}
            for r in collect_session_records(projects_root, since_days, n=1)]


def attribute_recent_messages(initiatives: list[dict], records: list[dict],
                              repos: list[str], wt_map: dict[str, str] | None = None,
                              keep: int = RECENT_MESSAGES_PER_INITIATIVE,
                              max_chars: int = RECENT_MESSAGE_MAX_CHARS) -> None:
    """Mutate each initiative with `recent_messages` = [{text, ts}], newest-first, top-N.

    Bring the conversation onto the card: surface the user's OWN recent prompts. A
    session is a candidate for an initiative when EITHER:
      • genesis  — its genesis text names a handoff of the initiative (the SAME rule as
        attribute_sessions), OR
      • branch+cwd — its gitBranch is the initiative's most-specific match
        (best_matching_initiative) AND its cwd resolves to the initiative's repo
        (resolve_cwd_repo) — catching feature-branch sessions the genesis missed.
    Both predicates are the SAME ones the scan already uses for session/telemetry
    attribution (no new scheme).

    SINGLE-BEST-CREDIT: unlike session_count (attribute_sessions), a session's MESSAGES
    are credited to exactly ONE initiative — its MOST-SPECIFIC candidate (`_specificity_key`,
    the same ranking `best_matching_initiative` uses for branch/PR/telemetry credit). This
    is what stops a session whose genesis names `handoff-app-blocks-comfy-cloud-scaffold.md`
    from ALSO crediting the generic `app-blocks` sibling (whose `handoff-app-blocks` name is
    a prefix substring) — the prompt would otherwise show on every prefix-sharing sibling.
    Turns for the winning initiative are pooled across its attributed sessions, sorted by
    timestamp DESC (a None ts sorts last), de-duplicated, truncated, and the top-N kept."""
    wt_map = wt_map or {}
    inis_by_repo: dict[str, list[dict]] = {}
    names_by_id: dict[int, set[str]] = {}
    sid_index: dict[str, dict] = {}
    acc: dict[int, list[dict]] = {}
    for ini in initiatives:
        ini["recent_messages"] = []
        inis_by_repo.setdefault(ini["repo"], []).append(ini)
        names = {os.path.basename(d["path"]).lower() for d in ini.get("docs", [])}
        names.add(f"handoff-{ini.get('slug', '').lower()}")
        names_by_id[id(ini)] = names
        for sid in (ini.get("session_ids") or ()):
            sid_index[sid] = ini  # a sessionId belongs to exactly one group -> one ini
        acc[id(ini)] = []

    for r in records:
        turns = r.get("turns") or []
        if not turns:
            continue
        candidates: list[dict] = []
        genesis = (r.get("genesis") or "").lower()
        if genesis:
            for ini in initiatives:
                if any(nm in genesis for nm in names_by_id[id(ini)]):
                    candidates.append(ini)
        # Session-membership: a session-derived initiative's own sessions (their turns are
        # its recent_messages) — the genesis/branch predicates can't see a token-slug.
        si = sid_index.get(r.get("session_id"))
        if si is not None:
            candidates.append(si)
        branch = (r.get("branch") or "").strip()
        repo = resolve_cwd_repo(r.get("cwd"), repos, wt_map)
        if branch and repo is not None:
            best = best_matching_initiative(branch, inis_by_repo.get(repo, []))
            if best is not None:
                candidates.append(best)  # a repeat of a genesis candidate is harmless to max
        if not candidates:
            continue
        # Single-best-credit: the session's messages go to its MOST-SPECIFIC candidate
        # only (same ranking as best_matching_initiative), so sibling slugs sharing a
        # prefix don't each surface the same prompt.
        winner = max(candidates, key=_specificity_key)
        acc[id(winner)].extend(turns)

    for ini in initiatives:
        # Newest-first; a None ts sorts after any real ts (present-flag as primary key).
        pooled = sorted(acc[id(ini)],
                        key=lambda m: (m.get("ts") is not None, m.get("ts") or 0.0),
                        reverse=True)
        seen: set = set()
        msgs: list[dict] = []
        for m in pooled:
            text = (m.get("text") or "").strip()
            if not text:
                continue
            # De-dupe on the DISPLAYED (truncated) text so identical card lines collapse
            # to one — automated agent sessions (e.g. the task-spec drafter) re-inject the
            # same boilerplate prompt across many sessions; showing it once is enough. The
            # newest occurrence wins (pooled is already sorted DESC).
            disp = _truncate(text, max_chars)
            if disp in seen:
                continue
            seen.add(disp)
            msgs.append({"text": disp, "ts": m.get("ts")})
            if len(msgs) >= keep:
                break
        ini["recent_messages"] = msgs


def attribute_sessions(initiatives: list[dict], genesis: list[dict]) -> None:
    """Mutate each initiative with session_count + last_session (epoch).

    A session belongs to an initiative when its genesis text mentions
    `handoff-<slug>` (slug or any dated variant filename of the cluster). Counted
    per initiative independently — a genesis naming two handoffs counts for both.

    `last_session` is timed from each session's LAST genuine user-turn epoch
    (`last_user_ts`), NOT its transcript filesystem mtime: Claude Code rewrites the
    .jsonl in place for metadata maintenance (title/mode/permission) with zero new work,
    bumping mtime and making an idle session masquerade as freshly touched → false
    `active` momentum. This mirrors `doc_touch_epoch`, which avoids mtime for the same
    bulk-rewrite reason. FALLBACK: a session with no parseable user-turn ts (rare — a
    genuine turn whose `timestamp` was absent/unparseable) contributes its `mtime`, the
    only remaining signal; `session_count` is unaffected either way.
    """
    for ini in initiatives:
        names = {os.path.basename(d["path"]).lower() for d in ini.get("docs", [])}
        names.add(f"handoff-{ini['slug'].lower()}")
        # Session-derived (and doc-anchored "both") initiatives credit sessions by
        # membership too: a session-only token-slug appears in NO genesis, so genesis-name
        # matching alone would leave it with session_count=0. `session_ids` closes that.
        sids = ini.get("session_ids") or set()
        count = 0
        last = None
        for g in genesis:
            low = g["text"].lower()
            if any(n in low for n in names) or g.get("session_id") in sids:
                count += 1
                touch = g.get("last_user_ts")
                if touch is None:
                    touch = g.get("mtime")
                last = newest_touch(last, touch)
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
    """Every live tmux pane as [{session, window, cwd, command, activity_ts, title}]
    (empty if no server).

    Zach runs many named scratchpad tmux sessions in parallel (`8`, `scratch7`,
    `wheat`), each often with SEVERAL windows on DIFFERENT initiatives; a claude pane
    sets its title to that window's session-summary line. We capture the window index
    too so the ledger can point at `<session>-<window>` (e.g. `8-1` vs `8-3`), the
    exact unit `tmux select-window -t 8:1` navigates to — a bare session id can't
    disambiguate two initiatives in one session. Best-effort: `_run` returns "" when
    the tmux binary is missing or no server is running, so this yields [] and callers
    degrade to [no session].

    `activity_ts` is `#{window_activity}` — the epoch (seconds) of the pane's window's
    last activity — parsed to an int so the Live-now strip can sort most-recently-active
    first and show a per-row freshness age. It is deliberately the LAST fixed field
    (before the free-text title) so a title containing a literal tab can't shift it; a
    missing/blank/non-integer value (older tmux without `#{window_activity}`, or a parse
    failure) degrades to `None` so the sort key is simply absent — never a crash.
    """
    out = _run(["tmux", "list-panes", "-a", "-F",
                "#{session_name}\t#{window_index}\t#{pane_current_path}"
                "\t#{pane_current_command}\t#{window_activity}\t#{pane_title}"])
    panes: list[dict] = []
    for ln in out.splitlines():
        if not ln.strip():
            continue
        parts = ln.split("\t")
        while len(parts) < 6:
            parts.append("")
        # Fields 0-4 are tab-free (session/window/cwd/command/window_activity); the title
        # is last and may itself contain tabs, so re-join the remainder rather than truncate.
        session, window, cwd, command, act_raw = parts[0], parts[1], parts[2], parts[3], parts[4]
        title = "\t".join(parts[5:])
        act = (act_raw or "").strip()
        try:
            activity_ts: int | None = int(act) if act else None
        except ValueError:
            activity_ts = None
        panes.append({"session": session, "window": window, "cwd": cwd,
                      "command": command, "activity_ts": activity_ts, "title": title})
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

    Scored as (# fingerprint overlaps, # extra title-token overlaps). The fingerprint
    is the initiative's SLUG tokens (clawgate, sysredis, wedge, tekton, bitdex) — or,
    for a date-only/degenerate slug with NO slug tokens, its TITLE tokens
    (`initiative_fingerprint`, so a bare-date initiative like `2026-07-21` isn't
    structurally unmatchable). The pane title is first stripped of generic action verbs
    (`TITLE_STOP` in `text_tokens`), so a pane can't be linked on "resume"/"review"/
    "monitor" alone. A candidate qualifies with ≥2 fingerprint overlaps, OR ≥1
    fingerprint overlap on a token UNIQUE to that initiative among the eligible set, OR
    ≥2 title-token overlaps. The uniqueness gate is what stops a pane linking on ONE
    token SHARED across siblings (e.g. "grafana" in both grafana-alert-drift and
    alert-chaos-grafana-sqlite) while still allowing a single distinctive token (faro,
    tekton) to match.

    Ranked by (real-slug-fingerprint?, fingerprint_overlap, title_overlap, longer slug,
    lexical). The leading real-slug flag guarantees a real SLUG match always outranks a
    date-only TITLE-fallback match, so the fallback can only fill a gap — never steal a
    pane a real-slug initiative would have won. Among real-slug initiatives the flag is
    constant, so their ordering is byte-identical to before.

    LIMITATION: a genuinely multi-topic pane title is attributed to its single
    strongest match and may miss a co-hosted sibling; bag-of-words can't disambiguate.
    A pane whose ONLY overlap is a token SHARED across a sibling family (df>1) is
    deliberately left UNMATCHED by the uniqueness gate — attaching it to one sibling by
    recency was evaluated and REJECTED: on live data it is structurally indistinguishable
    from a false match on a generic client/domain token (see the handoff), and a wrong
    tag costs more than a miss. Heuristic — read it as "which session is likely on this",
    not a proof.
    """
    # Document frequency of each token across the eligible set, so a single-token
    # match can require that token to be UNIQUE (df == 1) to this initiative.
    df: dict[str, int] = {}
    for ini in initiatives:
        toks = set(slug_tokens(ini.get("slug", ""))) | set(text_tokens(ini.get("title") or ""))
        for t in toks:
            df[t] = df.get(t, 0) + 1

    best: dict | None = None
    best_key: tuple = (0, 0, 0, 0, "")
    for ini in initiatives:
        fp_t = set(initiative_fingerprint(ini))
        title_t = set(text_tokens(ini.get("title") or ""))
        fp_hits = pane_toks & fp_t
        fp_overlap = len(fp_hits)
        title_overlap = len(pane_toks & (title_t - fp_t))
        unique_single = fp_overlap == 1 and df.get(next(iter(fp_hits)), 1) == 1
        if not (fp_overlap >= 2 or unique_single or title_overlap >= 2):
            continue
        is_real_slug = 1 if slug_tokens(ini.get("slug", "")) else 0
        key = (is_real_slug, fp_overlap, title_overlap,
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
        ini.setdefault("tmux_tasks", [])
        # Parallel `{task: activity_ts}` overlay (first-write-wins, aligned with the
        # tmux_tasks dedup) so a viewer can sort each live task by its pane's freshness
        # WITHOUT changing the back-compat `tmux_tasks` string list. Missing → absent.
        ini.setdefault("tmux_task_activity", {})
    by_repo: dict[str, list[dict]] = {}
    for ini in initiatives:
        by_repo.setdefault(ini["repo"], []).append(ini)

    unmatched: list[dict] = []
    for pane in panes:
        ptoks = set(text_tokens(pane.get("title", "")))
        repo = resolve_cwd_repo(pane.get("cwd"), repos, wt_map)
        # The repo-NAME tokens (civitai-manager -> {civitai, manager}) are the GROUPING key, not
        # a distinguishing topic: every session AND initiative in the repo carries them. A generic
        # pane title that merely names the repo ("Continue civitai-manager development work") would
        # otherwise clear best_title_match's `title_overlap >= 2` gate against ANY same-repo
        # initiative on the repo-name tokens alone and falsely badge it live (this mis-tagged the
        # SECURITY-AUDIT card onto a session doing unrelated packaging work). Strip them so a pane
        # must share a DISTINGUISHING word to attach; a pane left with no distinguishing token
        # falls through to `unmatched` (an honest "live but not tied to an initiative" miss).
        if repo is not None:
            ptoks -= set(text_tokens(os.path.basename(repo)))
        eligible = by_repo.get(repo, []) if repo is not None else []
        ini = best_title_match(ptoks, eligible) if ptoks else None
        if ini is not None:
            ini["tmux_sessions"].add(pane_id(pane, codenames))
            # Surface the matched pane's title (the live session's task summary) so a
            # viewer can render a `live: <task>` line. De-duped, insertion-ordered.
            task = (pane.get("title") or "").strip()
            if task and task not in ini["tmux_tasks"]:
                ini["tmux_tasks"].append(task)
                ini["tmux_task_activity"][task] = pane.get("activity_ts")
        elif pane.get("command", "") == "claude":
            unmatched.append({"id": pane_id(pane, codenames),
                              "title": pane.get("title", ""),
                              "repo": repo,
                              "activity_ts": pane.get("activity_ts")})
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
    # (epoch, subject) pairs per initiative, pooled across its matching branches; the
    # top-N newest become `recent_commits` after the per-repo pass.
    commit_subs: dict[int, list[tuple[float, str]]] = {}

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
        ini["recent_commits"] = []

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
            subs = git_recent_commit_subjects(repo, b, days, default_branch)
            if subs:
                commit_subs.setdefault(id(ini), []).extend(subs)
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
        # The newest commit subjects across all matching branches (deterministic hint).
        pooled = sorted(commit_subs.get(id(ini), []), key=lambda t: t[0], reverse=True)
        ini["recent_commits"] = [s for _ts, s in pooled[:RECENT_COMMITS_PER_INITIATIVE]]


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def build_report(days: int, repos: list[str] | None = None,
                 client=None, projects_root: str = PROJECTS_ROOT,
                 now: float | None = None, include_tmux: bool = False,
                 panes: list[dict] | None = None,
                 gh_ok: bool | None = None,
                 exclude_slugs: set[str] | None = None) -> dict:
    """Fuse the three sources into a ranked, per-repo report dict.

    `client` may be None (telemetry skipped). `repos` None -> auto-discover from the
    session/telemetry cwds + doc globs (session-first discovery). `include_tmux` links
    live tmux sessions to initiatives; `panes` overrides the live `collect_tmux_panes()`
    read (for tests / reproducibility). `gh_ok` overrides the `gh_available()` probe
    the same way — a test that stubs `gh_open_prs` must state which world it is in,
    rather than letting the report's honesty flag depend on the host running it.
    """
    now_epoch = now if now is not None else time.time()

    # SESSIONS FIRST — discovery consumes their cwds. A transcript-read failure must NOT
    # crash discovery: degrade to the old doc-glob repo set (records treated as empty).
    try:
        records = collect_session_records(projects_root, days)
    except Exception as e:  # noqa: BLE001 — degrade, never crash discovery
        print(f"  (session read failed, discovery degrades to docs: {e})", file=sys.stderr)
        records = []

    # Telemetry rows also feed discovery (a repo touched only via telemetry still surfaces).
    telem_rows = fetch_telemetry(client, days) if client is not None else None
    telemetry_available = telem_rows is not None

    if repos is None:
        session_cwds = [r.get("cwd") for r in records if r.get("cwd")]
        telem_cwds = [row.get("cwd") for row in (telem_rows or []) if row.get("cwd")]
        repos = discover_repos(session_cwds=session_cwds, telem_cwds=telem_cwds)

    # Resolve cwds living in linked worktrees back to their canonical repo so worktree
    # sessions/telemetry attribute to the parent and `(unknown repo)` shrinks.
    wt_map = worktree_canonical_map(repos)

    # Resolve each session's repo (corroboration is now per-session, from the branch).
    for r in records:
        r["repo"] = resolve_cwd_repo(r.get("cwd"), repos, wt_map)

    # Build initiatives: session groups (the driver) + doc anchors/dormant docs.
    groups = build_session_groups(records)
    doc_inis = load_initiatives(repos)                 # handoff-*.md clusters (slugs frozen)
    extra_inis = load_extra_doc_initiatives(repos)     # non-handoff docs (anchor candidates)
    initiatives = combine_docs_and_groups(doc_inis, extra_inis, groups)

    attribute_git(initiatives, days)

    # ONE transcript walk feeds session-count attribution AND recent-message surfacing.
    genesis = [{"text": r["genesis"], "mtime": r["mtime"],
                "last_user_ts": r.get("last_user_ts"),
                "session_id": r.get("session_id")} for r in records]
    attribute_sessions(initiatives, genesis)
    attribute_recent_messages(initiatives, records, repos, wt_map)

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

    # Compute momentum from the MAX of every SESSION-DERIVED touch signal. The board is
    # sessions-only, so timing is sessions-only too: a doc's mtime/authored-date
    # (doc_touch_epoch) must NEVER set last_touch — a `both` card whose handoff was just
    # rewritten but whose last session turn was 3 days ago has to read ~3 days, not
    # "minutes". So doc freshness is deliberately OMITTED here (it only ever anchored/
    # enriched a card; it never times one). A LIVE tmux session on the initiative counts
    # as touched-now — open work is active regardless of how old its handoff is.
    for ini in initiatives:
        live_session = now_epoch if ini.get("tmux_sessions") else None
        ini["last_touch"] = newest_touch(
            ini.get("last_commit"),
            ini.get("telem_last"),
            ini.get("last_session"),
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

    # Operator-supplied suppression. Deliberately the ONLY way a row leaves this report
    # for a reason other than the `--days` window: an earlier WIP (PR #778) inferred
    # "resolved" from markers in the handoff text and, measured over the real corpus,
    # flagged 11 of 62 docs — 8 of them on section headings like `### DONE this session`
    # inside handoffs that carried live next-steps. A scan whose whole job is answering
    # "what am I working on" must not hide a row on a guess, so this list is explicit.
    # A suppressed row must stay AUDIBLE: report the slugs asked for and the number
    # actually removed separately, so `--exclude-slugs typo` reads as "0 suppressed"
    # rather than looking identical to a run that had nothing to suppress.
    excluded_slugs = sorted(exclude_slugs) if exclude_slugs else []
    excluded_count = 0
    if exclude_slugs:
        kept = [i for i in initiatives if i.get("slug") not in exclude_slugs]
        excluded_count = len(initiatives) - len(kept)
        initiatives = kept

    initiatives = sort_initiatives(initiatives)

    # Sets aren't JSON-serializable and the renderer wants a stable order.
    if tmux_active:
        for ini in initiatives:
            ini["tmux_sessions"] = sorted(ini.get("tmux_sessions", set()),
                                          key=_tmux_session_sort_key)
    # `session_ids` is a set through attribution; emit a stable sorted list. `_topic_tokens`
    # is an internal grouping/anchoring artifact — drop it from the emitted payload.
    for ini in initiatives:
        ini.pop("_topic_tokens", None)
        ini.pop("_opening_ts", None)  # internal genesis-ts (earliest-wins adoption); not stored
        sids = ini.get("session_ids")
        if isinstance(sids, (set, frozenset)):
            ini["session_ids"] = sorted(sids)

    by_repo: dict[str, list[dict]] = {}
    for ini in initiatives:
        by_repo.setdefault(ini["repo"], []).append(ini)

    return {
        "days": days,
        "telemetry_available": telemetry_available,
        # What the operator asked to hide, and how many rows that actually removed.
        # Both, because they can disagree — a misspelled slug suppresses nothing.
        "excluded_slugs": excluded_slugs,
        "excluded_count": excluded_count,
        # Says which zeroes are MEASURED and which are "not wired up here":
        # False means every `open_prs: []` / `merged_prs: 0` in this report is
        # an absence of data, not an absence of PRs.
        "gh_available": gh_available() if gh_ok is None else bool(gh_ok),
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
    # Say it out loud. A hidden row that nobody knows is hidden is how this report
    # starts lying about what is in flight.
    if report.get("excluded_slugs"):
        out.append(f"   --exclude-slugs SUPPRESSED {report.get('excluded_count', 0)} "
                   f"initiative(s); asked for: {', '.join(report['excluded_slugs'])}")

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
            msgs = ini.get("recent_messages") or []
            if msgs:
                out.append(f"        you › {msgs[0].get('text', '')[:160]}")
            rc = ini.get("recent_commits") or []
            if rc:
                out.append(f"        commit › {rc[0][:100]}")
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
    if report.get("gh_available") is False:
        out.append("\n(NOTE: gh UNAVAILABLE — every 'open PR' / 'merged PR' figure above is "
                   "NOT MEASURED, not zero. Authenticate `gh` to get real PR counts.)")
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
    p.add_argument("--days", type=int, default=5, help="trailing window in days (default 5)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--repo", default=None, help="restrict to a single repo path")
    p.add_argument("--tmux", action="store_true",
                   help="link each initiative to the live tmux session(s) hosting it")
    p.add_argument("--exclude-slugs", default=None,
                   help="comma-separated initiative slugs to suppress from the report")
    return p.parse_args(argv)


def parse_exclude_slugs(raw: str | None) -> set[str] | None:
    """Split a `--exclude-slugs` value into a slug set, or None if it names nothing.

    Strips whitespace and drops empty tokens, so `"a, b"`, `"a,b,"` and `"a , b"` all
    give `{"a", "b"}` — a bare `.split(",")` would yield `" b"` and `""`, which match
    no slug and would silently suppress nothing while looking like they did. A value
    that is all separators (`","`, `"  "`) returns None: nothing was named.
    """
    if not raw:
        return None
    slugs = {tok.strip() for tok in raw.split(",")}
    slugs.discard("")
    return slugs or None


def main(argv=None) -> int:
    a = parse_args(argv)
    if a.days <= 0:
        print("error: --days must be positive", file=sys.stderr)
        return 2

    repos: list[str] | None
    if a.repo:
        repo = os.path.abspath(os.path.expanduser(a.repo))
        top = git_toplevel(repo)
        if top is None:
            print(f"error: {repo} is not inside a git repo", file=sys.stderr)
            return 2
        repos = [top]
    else:
        # None -> build_report auto-discovers from the session/telemetry cwds + doc globs
        # (session-first), which it can only do AFTER it has fetched those cwds.
        repos = None

    client = None
    try:
        conn = Q.CHConn.from_env()
        client = Q.CHClient(conn)
    except RuntimeError:
        client = None  # telemetry optional — degrade gracefully

    report = build_report(a.days, repos=repos, client=client, include_tmux=a.tmux,
                          exclude_slugs=parse_exclude_slugs(a.exclude_slugs))

    if a.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
