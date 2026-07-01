#!/usr/bin/env python3
"""DETERMINISTIC repo-exclusion layer — turn Zach's emailed reply into a HARD filter.

Why this exists (a real failure this fixes):
  repo-cos already feeds Zach's reply to the previous digest into synthesis as CONTEXT
  (`feedback.py` → `llm.build_feedback_block`). That is too weak — the model IGNORED a
  reply that said "1. this project is paused / … / 5. we are not the code owner for that
  repo" and re-proposed the exact paused repos. His replies are really REPO-LEVEL SCOPE
  EXCLUSIONS. This module makes them a HARD, DETERMINISTIC filter that DROPS those repos
  from the scan BEFORE the LLM ever sees them — so an excluded repo CANNOT reappear.

Design (mirrors the rest of repo-cos: deterministic, no LLM, best-effort, never raises):
  * `parse_reply(reply_text, emailed_proposals)` → {"exclude":[...], "resume":[...]} using
    POSITIONAL line mapping (`1.`, `2)`, `#3`, `4:` → proposal N's repo) + a fixed keyword
    set + explicit repo-name/alias mentions. NO model call.
  * State persists to ~/.config/repo-cos/exclusions.json (hand-editable by Zach).
  * `apply(state, parsed)` merges excludes / drops resumes. `filter_repos(repos, state)`
    drops excluded repos (match on realpath OR basename).
  * Position mapping resolves against the LAST EMAILED digest (`last_emailed.json`, else the
    most-recent history entry with emailed=true, else latest.json) — because proposals
    ROTATE run-to-run and every run overwrites latest.json, so "1./2./…" must map to the
    digest Zach ACTUALLY SAW, not whatever's current.

The context-injection path (feedback.py → llm) is KEPT for nuance; this ADDS a hard layer.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

PERSIST_DIR = Path("~/.config/repo-cos").expanduser()
EXCLUSIONS_FILE = PERSIST_DIR / "exclusions.json"
LAST_EMAILED_FILE = PERSIST_DIR / "last_emailed.json"
LATEST_FILE = PERSIST_DIR / "latest.json"
HISTORY_DIR = PERSIST_DIR / "history"


def _log(msg: str) -> None:
    print(f"  exclusions: {msg}", file=sys.stderr)


# ---- keyword sets (deterministic; case-insensitive) --------------------------------
#
# An EXCLUDE intent on a line drops that line's repo. A subset of phrasings mean the repo
# is PERMANENTLY out of scope (we don't own it / it's dead) vs. merely paused.

# Phrasings that mean "not ours / dead" → permanent exclusion.
_PERMANENT_RE = re.compile(
    r"""
      not \s+ (?:the|our|my|code)? \s* owner            # "not the code owner", "not owner"
    | (?:code[\s-]?owner)                                 # "not codeowner"
    | not \s+ (?:mine|ours)                               # "not mine", "not ours"
    | \b deprecated \b
    | \b archived \b
    | \b (?:dead|abandoned|retired|sunset) \b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Phrasings that mean "exclude" (paused / skip / ignore / drop / …). Non-permanent unless a
# _PERMANENT_RE phrase also matches. Ordering doesn't matter — any match = exclude intent.
_EXCLUDE_RE = re.compile(
    r"""
      \b paused? \b
    | \b skip (?:ping|ped)? \b
    | \b ignore \b
    | \b stop \b
    | \b drop \b
    | \b remove \b
    | \b exclude \b
    | \b won'? t \b                                       # won't, wont
    | \b do \s* n'? t \b                                  # don't, dont, do not
    | \b do \s+ not \b
    | leave \s+ (?:it|this|that)                          # "leave it", "leave this"
    | not \s+ (?:relevant|interested|mine|ours)
    | not \s+ (?:the|our|my|code)? \s* owner
    | (?:code[\s-]?owner)
    | \b deprecated \b
    | \b archived \b
    | \b (?:dead|abandoned|retired|sunset) \b
    | \b no \b                                            # bare "no"
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Phrasings that mean "resume" (un-exclude / bring back). A resume beats an exclude on the
# same line (Zach would only say "resume" about something already paused).
_RESUME_RE = re.compile(
    r"""
      \b resume \b
    | \b un[\s-]? pause \b                                # unpause, un-pause
    | \b un[\s-]? exclude \b
    | \b re[\s-]? enable \b
    | \b reactivate \b
    | bring \s+ back
    | \b restart \b
    | start \s+ .*? \b again \b
    | \b again \b                                         # "do X again"
    """,
    re.IGNORECASE | re.VERBOSE,
)

# A leading positional anchor: "1." "2)" "3 -" "#4" "5:" (optionally indented). Group 1 = N.
_POSITIONAL_RE = re.compile(r"^\s*(?:#\s*)?(\d{1,3})\s*(?:[.)\-:]|\s|$)")


# ---- state (exclusions.json) -------------------------------------------------------

def load_state(path: Path | None = None) -> dict:
    """Load exclusions.json → {"repos": {key: {...}}}. Robust: missing/corrupt/wrong-shape
    file → an empty state. Never raises."""
    p = path or EXCLUSIONS_FILE
    try:
        if not p.exists():
            return {"repos": {}}
        obj = json.loads(p.read_text())
        if not isinstance(obj, dict):
            return {"repos": {}}
        repos = obj.get("repos")
        if not isinstance(repos, dict):
            obj["repos"] = {}
        return obj
    except Exception as exc:  # noqa: BLE001
        _log(f"could not read {p} ({exc}); starting empty")
        return {"repos": {}}


def save_state(state: dict, path: Path | None = None) -> None:
    """Persist exclusions.json. Best-effort — never fails the run."""
    p = path or EXCLUSIONS_FILE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, indent=2, sort_keys=True))
    except Exception as exc:  # noqa: BLE001
        _log(f"could not save {p}: {exc}")


# ---- emitted-digest resolution (position mapping source) ---------------------------

def load_last_emailed(*, last_emailed: Path | None = None,
                      history_dir: Path | None = None,
                      latest: Path | None = None) -> dict | None:
    """Resolve the digest Zach ACTUALLY SAW, for positional mapping. Order:
        1. last_emailed.json (written on every --email send), else
        2. the most-recent history/*.json entry with "emailed": true, else
        3. latest.json (best-effort fallback).
    Returns the parsed digest dict (with a "proposals" list) or None. Never raises."""
    le = last_emailed or LAST_EMAILED_FILE
    hd = history_dir or HISTORY_DIR
    lt = latest or LATEST_FILE

    try:
        if le.exists():
            obj = json.loads(le.read_text())
            if isinstance(obj, dict) and obj.get("proposals") is not None:
                return obj
    except Exception as exc:  # noqa: BLE001
        _log(f"could not read {le}: {exc}")

    # most-recent history entry that was emailed
    try:
        if hd.is_dir():
            for f in sorted(hd.glob("*.json"), reverse=True):
                try:
                    obj = json.loads(f.read_text())
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(obj, dict) and obj.get("emailed") is True:
                    return obj
    except Exception as exc:  # noqa: BLE001
        _log(f"could not scan history {hd}: {exc}")

    try:
        if lt.exists():
            obj = json.loads(lt.read_text())
            if isinstance(obj, dict):
                return obj
    except Exception as exc:  # noqa: BLE001
        _log(f"could not read {lt}: {exc}")
    return None


def write_last_emailed(proposals, *, subject: str, generated_at: str,
                       path: Path | None = None) -> None:
    """Snapshot the EMAILED proposals so a later reply's positional refs map to what Zach
    saw (not the next run's rotated set). Best-effort. `proposals` are objects with
    `.as_dict()` OR plain dicts."""
    p = path or LAST_EMAILED_FILE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        out = []
        for pr in proposals:
            out.append(pr.as_dict() if hasattr(pr, "as_dict") else dict(pr))
        payload = {
            "emailed_at": generated_at,
            "subject": subject,
            "proposals": out,
        }
        p.write_text(json.dumps(payload, indent=2))
    except Exception as exc:  # noqa: BLE001
        _log(f"could not write {p}: {exc}")


# ---- alias map (repo-name mentions → repo key) -------------------------------------

def build_alias_map(default_repos: list[str]) -> dict[str, str]:
    """alias(lowercased) → repo basename. Built from each default repo's basename plus a
    few obvious short forms, so a reply naming 'kubeclaw' / 'civitai' / 'homelab' /
    'datapacket' maps to the right repo even without a position number."""
    amap: dict[str, str] = {}
    for r in default_repos:
        base = Path(r).expanduser().name
        if not base:
            continue
        amap[base.lower()] = base
        # a short form = the part before the first '-' (e.g. baseball-manitoba-pitch →
        # baseball, civitai-orchestration → civitai) — only when it's distinctive.
        head = base.split("-", 1)[0].lower()
        if head and head not in amap:
            amap[head] = base
    # explicit obvious short names (only added if the corresponding repo is present).
    def _has(sub: str) -> str | None:
        for r in default_repos:
            b = Path(r).expanduser().name
            if sub in b.lower():
                return b
        return None
    for short, needle in (("homelab", "homelab"), ("datapacket", "datapacket"),
                          ("civit", "civitai"), ("promptver", "promptver"),
                          ("baseball", "baseball"), ("kubeclaw", "kubeclaw")):
        b = _has(needle)
        if b and short not in amap:
            amap[short] = b
    return amap


def _find_named_repo(line: str, alias_map: dict[str, str]) -> str | None:
    """Return the repo basename named in `line` via the alias map (longest alias first, so
    'civitai-orchestration' beats 'civitai'), else None."""
    low = line.lower()
    for alias in sorted(alias_map, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", low):
            return alias_map[alias]
    return None


# ---- the deterministic parser ------------------------------------------------------

def parse_reply(reply_text: str, emailed_proposals: list[dict] | None,
                *, alias_map: dict[str, str] | None = None) -> dict:
    """Parse Zach's reply into {"exclude": [{repo, reason, permanent}], "resume": [repo]}.

    DETERMINISTIC — no LLM. Line-by-line:
      * a POSITIONAL line ('1.', '2)', '#3', '4:') → proposal N in `emailed_proposals`
        (1-indexed) → its repo. This is the primary mechanism (Zach replies positionally).
      * an EXCLUDE-intent keyword on the line → exclude that repo (permanent if a
        _PERMANENT_RE phrase matches, e.g. 'not the code owner', 'deprecated').
      * a RESUME-intent keyword + a repo ref (positional OR name) → add to `resume`.
      * an explicit repo-NAME/alias mention (non-positional) with intent → apply likewise.
    Unparseable lines are ignored (they still reach the LLM via context injection). Never
    raises."""
    parsed = {"exclude": [], "resume": []}
    if not reply_text:
        return parsed
    props = emailed_proposals or []

    # de-dupe: last write wins per repo, resume beats exclude.
    excl: dict[str, dict] = {}
    resume: set[str] = set()

    for raw in reply_text.splitlines():
        line = raw.strip()
        if not line:
            continue

        repo = _line_repo(line, props, alias_map)
        if not repo:
            continue

        is_resume = bool(_RESUME_RE.search(line))
        is_exclude = bool(_EXCLUDE_RE.search(line))

        if is_resume:
            resume.add(repo)
            excl.pop(repo, None)
            continue
        if is_exclude:
            permanent = bool(_PERMANENT_RE.search(line))
            excl[repo] = {"repo": repo, "reason": line, "permanent": permanent}

    # resume wins over exclude for the same repo
    for r in resume:
        excl.pop(r, None)

    parsed["exclude"] = list(excl.values())
    parsed["resume"] = sorted(resume)
    return parsed


def _line_repo(line: str, props: list[dict],
               alias_map: dict[str, str] | None) -> str | None:
    """Resolve the repo a line refers to: positional index first, else a named alias.

    Positional takes precedence (it's the primary channel), but if the positional index is
    out of range we still fall through to an alias mention on the same line — so "4. kubeclaw
    is paused" excludes kubeclaw even if position 4 didn't exist."""
    m = _POSITIONAL_RE.match(line)
    if m:
        n = int(m.group(1))
        if 1 <= n <= len(props):
            repo = str((props[n - 1] or {}).get("repo") or "").strip()
            if repo:
                return repo
    if alias_map:
        return _find_named_repo(line, alias_map)
    return None


# ---- apply + filter ----------------------------------------------------------------

def apply(state: dict, parsed: dict, *, source: str = "reply",
          now: str | None = None) -> dict:
    """Merge `parsed` into `state`: add/refresh excludes, remove resumes. Returns the same
    (mutated) state dict. Caller persists via save_state."""
    repos = state.setdefault("repos", {})
    ts = now or datetime.now().astimezone().isoformat(timespec="seconds")

    for entry in parsed.get("exclude", []):
        key = _canon_key(entry.get("repo"))
        if not key:
            continue
        prev = repos.get(key) or {}
        repos[key] = {
            "reason": entry.get("reason") or prev.get("reason") or "",
            # once permanent, stays permanent even if a later paused-line re-touches it
            "permanent": bool(entry.get("permanent") or prev.get("permanent")),
            "excluded_at": prev.get("excluded_at") or ts,
            "refreshed_at": ts,
            "source": source,
        }

    for key in parsed.get("resume", []):
        repos.pop(_canon_key(key), None)

    return state


def _canon_key(ref) -> str:
    """Canonical exclusion key = the repo BASENAME, so the same repo referenced as a bare
    name, a `~/…` path, or an absolute path all collapse to ONE entry (was creating dup
    keys like both `kubeclaw-cloud` and `~/workspace/kubeclaw-cloud`). Basename is unique
    across ~/workspace and is what `resume <name>` / --show-exclusions use."""
    ref = str(ref or "").strip()
    if not ref:
        return ""
    if "/" in ref or ref.startswith("~"):
        return os.path.basename(os.path.normpath(os.path.expanduser(ref)))
    return ref


def filter_repos(repos: list[str], state: dict) -> tuple[list[str], list[str]]:
    """Split `repos` (paths or names) into (kept, excluded). A repo is excluded if its
    realpath OR basename matches an excluded key (also compared by realpath/basename), so
    '~/workspace/civit/civitai-orchestration' and a bare 'civitai-orchestration' both hit."""
    keys = (state or {}).get("repos") or {}
    if not keys:
        return list(repos), []

    # normalize excluded keys to a set of {realpath, basename} tokens.
    excl_tokens: set[str] = set()
    for k in keys:
        excl_tokens.update(_tokens(k))

    kept, excluded = [], []
    for r in repos:
        if _tokens(r) & excl_tokens:
            excluded.append(r)
        else:
            kept.append(r)
    return kept, excluded


def _tokens(ref: str) -> set[str]:
    """Comparison tokens for a repo ref: its basename and (if it looks like a path) its
    expanded realpath. A bare name yields just the basename; a path yields both."""
    ref = (ref or "").strip()
    if not ref:
        return set()
    toks = set()
    base = Path(ref).name
    if base:
        toks.add(base)
    # only realpath-ify things that look like paths (contain a separator or ~)
    if os.sep in ref or ref.startswith("~"):
        try:
            toks.add(os.path.realpath(Path(ref).expanduser()))
        except Exception:  # noqa: BLE001
            pass
    return toks


# ---- human-readable state (for --show-exclusions and the digest footer) ------------

def excluded_names(state: dict) -> list[str]:
    """Sorted list of excluded repo keys."""
    return sorted((state or {}).get("repos") or {})


def format_state(state: dict) -> str:
    """Pretty one-block summary of the current exclusion state for --show-exclusions."""
    repos = (state or {}).get("repos") or {}
    if not repos:
        return "repo-cos exclusions: none.\n(Excluded repos come from your emailed reply; " \
               "hand-edit ~/.config/repo-cos/exclusions.json to adjust.)"
    lines = [f"repo-cos exclusions: {len(repos)} repo(s) currently dropped from the scan.",
             ""]
    for key in sorted(repos):
        e = repos[key] or {}
        perm = "permanent" if e.get("permanent") else "paused"
        src = e.get("source") or "?"
        when = e.get("excluded_at") or "?"
        reason = (e.get("reason") or "").strip()
        lines.append(f"  {key}  [{perm}, {src}, since {when}]")
        if reason:
            lines.append(f"      reason: {reason}")
    lines.append("")
    lines.append('Reply "resume <repo>" to re-enable one, or edit '
                 "~/.config/repo-cos/exclusions.json.")
    return "\n".join(lines)
