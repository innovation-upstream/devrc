#!/usr/bin/env python3
"""DETERMINISTIC repo-exclusion layer — turn Zach's emailed reply into a HARD filter.

Why this exists (a real failure this fixes):
  repo-cos already feeds Zach's reply to the previous digest into synthesis as CONTEXT
  (`feedback.py` → `llm.build_feedback_block`). That is too weak — the model IGNORED a
  reply that said "1. this project is paused / … / 5. we are not the code owner for that
  repo" and re-proposed the exact paused repos. His replies are really REPO-LEVEL SCOPE
  EXCLUSIONS. This module makes them a HARD, DETERMINISTIC filter that DROPS those repos
  from the scan BEFORE the LLM ever sees them — so an excluded repo CANNOT reappear.

Two distinct intents (a real conflation this fixes):
  * "pause this repo" — the WHOLE project is on hold → REPO-level exclusion (existing).
    Signals: pause/paused/on hold/hold off; and not-owner/deprecated/archived (permanent).
  * "skip this recommendation" — the repo is FINE, he just doesn't want THIS one proposal →
    the repo STAYS in scope, but that single proposal must never resurface. Signals:
    skip/not needed/not relevant/dismiss/nah/no/don't-propose WHEN no repo-pause/owner
    language is present. This is RECOMMENDATION-level DISMISSAL.
  Before this split, ANY skip keyword dropped the whole repo — so "we dont own the 3d model
  feature, skip" (about a FEATURE) wrongly excluded all of civitai. The parser now classifies
  per line with a strict precedence: repo-pause > repo-owner/dead > recommendation-dismiss.

Design (mirrors the rest of repo-cos: deterministic, no LLM, best-effort, never raises):
  * `parse_reply(reply_text, emailed_proposals)` →
      {"exclude":[...], "resume":[...], "dismiss":[{evidence, reason, repo}]}
    using POSITIONAL line mapping (`1.`, `2)`, `#3`, `4:` → proposal N) + a fixed keyword
    set + explicit repo-name/alias mentions. A dismiss line collects proposal N's `evidence`
    (repo/file:line refs) so the exact recommendation can be suppressed. NO model call.
  * State persists to ~/.config/repo-cos/exclusions.json (hand-editable by Zach) under two
    top-level keys: "repos" (repo-level exclusions) and "dismissed" (per-recommendation,
    keyed by the normalized evidence ref → {reason, dismissed_at, repo}).
  * `apply(state, parsed)` merges excludes / drops resumes / accumulates dismissals.
    `filter_repos(repos, state)` drops excluded repos (match on realpath OR basename).
    `filter_candidates(candidates, state)` drops dismissed candidates by evidence ref BEFORE
    the LLM — the guarantee a skipped proposal can't re-form from the same signal.
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
# Each numbered-reply line is classified into ONE intent with a strict precedence:
#   1. REPO-PAUSE     (pause/paused/on hold/hold off)        → repo exclusion, non-permanent
#   2. REPO-OWNER/DEAD (not owner / deprecated / archived)   → repo exclusion, PERMANENT
#   3. RECOMMENDATION-DISMISS (skip/not needed/dismiss/no…)  → drop THAT proposal, keep repo
# Higher tiers win: "kubeclaw is paused" is repo-pause even though it lacks skip words; a line
# with BOTH "paused" and "skip" is a repo-pause (the whole project is on hold). Only a line
# whose sole intent is skip/dismiss (no pause/owner language) dismisses a single proposal.

# Tier 1 — "the whole repo is on hold" → REPO-level, non-permanent.
_REPO_PAUSE_RE = re.compile(
    r"""
      \b paused? \b
    | on \s+ hold
    | \b hold \s+ off \b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Tier 2 — "not ours / dead" → REPO-level, PERMANENT. NOTE the owner alternative is
# `not (the|our|my|code) owner` — it does NOT fire on "dont own the 3d model FEATURE"
# (that's "dont own", not "not owner", and about a feature), which must fall to dismiss.
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

# Any REPO-level exclude intent = tier 1 OR tier 2 (plus a few legacy synonyms that clearly
# mean "drop the whole repo": ignore/stop/drop/remove/exclude/leave-it). Skip/no/don't were
# REMOVED from here — they are now recommendation-DISMISS signals (tier 3), not repo drops.
_EXCLUDE_RE = re.compile(
    r"""
      \b paused? \b
    | on \s+ hold
    | \b hold \s+ off \b
    | \b ignore \b
    | \b stop \b
    | \b remove \b
    | \b exclude \b
    | leave \s+ (?:it|this|that)                          # "leave it", "leave this"
    | not \s+ (?:interested|mine|ours)                    # "not relevant" is a DISMISS signal
    | not \s+ (?:the|our|my|code)? \s* owner
    | (?:code[\s-]?owner)
    | \b deprecated \b
    | \b archived \b
    | \b (?:dead|abandoned|retired|sunset) \b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Tier 3 — "skip THIS recommendation" → dismiss one proposal, repo STAYS in scope. Only
# considered when NO repo-pause/owner language is present (enforced by precedence in
# parse_reply). "not relevant"/"not interested" here mean the PROPOSAL, not the repo.
_DISMISS_RE = re.compile(
    r"""
      \b skip (?:ping|ped)? \b
    | \b dismiss (?:ed|ing)? \b
    | not \s+ needed
    | not \s+ relevant
    | \b nah \b
    | \b no \b                                            # bare "no"
    | do \s* n'? t \s+ (?:propose|want|need)              # don't propose/want/need
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

def _empty_state() -> dict:
    return {"repos": {}, "dismissed": {}}


def load_state(path: Path | None = None) -> dict:
    """Load exclusions.json → {"repos": {...}, "dismissed": {...}}. Robust:
    missing/corrupt/wrong-shape file → an empty state; an OLDER file with no "dismissed"
    key loads clean with an empty dismissed dict. Never raises."""
    p = path or EXCLUSIONS_FILE
    try:
        if not p.exists():
            return _empty_state()
        obj = json.loads(p.read_text())
        if not isinstance(obj, dict):
            return _empty_state()
        if not isinstance(obj.get("repos"), dict):
            obj["repos"] = {}
        # older files predate the dismiss feature → default to empty (robust to a
        # missing OR wrong-shaped "dismissed" key).
        if not isinstance(obj.get("dismissed"), dict):
            obj["dismissed"] = {}
        return obj
    except Exception as exc:  # noqa: BLE001
        _log(f"could not read {p} ({exc}); starting empty")
        return _empty_state()


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
    """Parse Zach's reply into
        {"exclude": [{repo, reason, permanent}], "resume": [repo],
         "dismiss": [{evidence: [refs], reason, repo}]}.

    DETERMINISTIC — no LLM. Line-by-line, with a STRICT intent precedence:
      * RESUME beats everything (Zach only says "resume" about something paused).
      * a POSITIONAL line ('1.', '2)', '#3', '4:') → proposal N in `emailed_proposals`
        (1-indexed) → its repo AND its evidence. This is the primary mechanism.
      * TIER 1 repo-pause (paused/on hold) → repo exclusion (non-permanent).
      * TIER 2 repo-owner/dead (not owner/deprecated/archived) → repo exclusion (permanent).
      * TIER 3 skip/dismiss (skip/not needed/dismiss/no…) WHEN no tier-1/2 language →
        DISMISS that one proposal: collect proposal N's `evidence` refs; the repo STAYS.
      * an explicit repo-NAME/alias mention (non-positional) with repo-level intent applies
        likewise (name-mentions cannot dismiss — there's no proposal to look up).
    Unparseable lines are ignored (they still reach the LLM via context injection). Never
    raises."""
    parsed = {"exclude": [], "resume": [], "dismiss": []}
    if not reply_text:
        return parsed
    props = emailed_proposals or []

    # de-dupe: last write wins per repo; resume beats exclude. Dismissals accumulate keyed
    # by evidence ref so re-touching the same proposal doesn't duplicate.
    excl: dict[str, dict] = {}
    resume: set[str] = set()
    dismiss: dict[str, dict] = {}  # ref -> {evidence:[ref], reason, repo}

    for raw in reply_text.splitlines():
        line = raw.strip()
        if not line:
            continue

        repo, prop = _line_repo(line, props, alias_map)
        if not repo:
            continue

        # RESUME first — un-pauses a repo regardless of any other keyword on the line.
        if _RESUME_RE.search(line):
            resume.add(repo)
            excl.pop(repo, None)
            continue

        # Precedence: repo-pause > repo-owner/dead > recommendation-dismiss.
        if _REPO_PAUSE_RE.search(line):
            excl[repo] = {"repo": repo, "reason": line, "permanent": False}
            continue
        if _PERMANENT_RE.search(line):
            excl[repo] = {"repo": repo, "reason": line, "permanent": True}
            continue
        if _EXCLUDE_RE.search(line):
            # a legacy repo-level synonym (ignore/stop/remove/exclude/leave-it/not-relevant)
            excl[repo] = {"repo": repo, "reason": line,
                          "permanent": bool(_PERMANENT_RE.search(line))}
            continue
        if _DISMISS_RE.search(line):
            # RECOMMENDATION-level: suppress THIS proposal, keep the repo. Needs the
            # proposal's evidence — only available via a positional match.
            refs = _proposal_evidence(prop)
            if not refs:
                continue  # no evidence to key on (e.g. a name-only line) → can't dismiss
            for ref in refs:
                dismiss[ref] = {"evidence": [ref], "reason": line, "repo": repo}

    # resume wins over exclude for the same repo
    for r in resume:
        excl.pop(r, None)

    parsed["exclude"] = list(excl.values())
    parsed["resume"] = sorted(resume)
    # collapse per-ref dismiss entries back to per-proposal {evidence:[...], reason, repo}
    # grouped by (reason, repo) so one skip line yields ONE dismiss with all its refs.
    grouped: dict[tuple, dict] = {}
    for ref, d in dismiss.items():
        k = (d["reason"], d["repo"])
        grouped.setdefault(k, {"evidence": [], "reason": d["reason"], "repo": d["repo"]})
        grouped[k]["evidence"].append(ref)
    for g in grouped.values():
        g["evidence"] = sorted(set(g["evidence"]))
    parsed["dismiss"] = list(grouped.values())
    return parsed


def _proposal_evidence(prop: dict | None) -> list[str]:
    """The normalized evidence refs of a proposal (its `evidence` list). Each ref is
    canonicalized to match how prescan emits candidate refs (repo/file:line)."""
    if not prop:
        return []
    ev = prop.get("evidence") or []
    out = []
    for r in ev:
        c = _canon_ref(r)
        if c:
            out.append(c)
    return out


def _line_repo(line: str, props: list[dict],
               alias_map: dict[str, str] | None) -> tuple[str | None, dict | None]:
    """Resolve (repo, proposal) a line refers to: positional index first, else a named alias.

    Positional takes precedence (it's the primary channel) and also yields the PROPOSAL dict
    (needed for evidence on a dismiss). If the positional index is out of range we still fall
    through to an alias mention on the same line — so "9. civitai is paused" excludes civitai
    even though position 9 didn't exist (name-mentions carry no proposal → dismiss can't fire
    on them)."""
    m = _POSITIONAL_RE.match(line)
    if m:
        n = int(m.group(1))
        if 1 <= n <= len(props):
            prop = props[n - 1] or {}
            repo = str(prop.get("repo") or "").strip()
            if repo:
                return repo, prop
    if alias_map:
        return _find_named_repo(line, alias_map), None
    return None, None


# ---- apply + filter ----------------------------------------------------------------

def apply(state: dict, parsed: dict, *, source: str = "reply",
          now: str | None = None) -> dict:
    """Merge `parsed` into `state`: add/refresh REPO excludes, remove resumes, and ACCUMULATE
    per-recommendation dismissals into state["dismissed"]. Returns the same (mutated) state
    dict. Caller persists via save_state."""
    repos = state.setdefault("repos", {})
    dismissed = state.setdefault("dismissed", {})
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

    # Accumulate dismissed recommendations, keyed by the normalized evidence ref so the
    # pre-scan filter can compare directly against a candidate's ref. Never removed here —
    # a dismissed proposal must stay suppressed (hand-edit the JSON to un-dismiss).
    for d in parsed.get("dismiss", []):
        reason = d.get("reason") or ""
        repo = d.get("repo") or ""
        for ref in d.get("evidence") or []:
            key = _canon_ref(ref)
            if not key:
                continue
            prev = dismissed.get(key) or {}
            dismissed[key] = {
                "reason": reason or prev.get("reason") or "",
                "repo": repo or prev.get("repo") or "",
                "dismissed_at": prev.get("dismissed_at") or ts,
            }

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


def _canon_ref(ref) -> str:
    """Canonical DISMISSED-evidence key = the `repo/file:line` reference, normalized to match
    how prescan emits a candidate's `.ref` (see prescan.Candidate.ref: `{repo}/{file}` with a
    `:{line}` suffix only when line>0). We only strip surrounding whitespace and collapse any
    accidental duplicate/leading slashes in the path segment — the format is otherwise already
    the comparison form, so a proposal's evidence ref and a fresh candidate's ref compare
    equal (e.g. 'civitai/docs/3d-models-followups.md:103')."""
    ref = str(ref or "").strip()
    if not ref:
        return ""
    # split the optional ':line' suffix (only the LAST colon that precedes a pure integer),
    # normalize the path part, re-attach. Windows-style drive colons don't occur here.
    line = ""
    if ":" in ref:
        head, _, tail = ref.rpartition(":")
        if head and tail.isdigit():
            ref, line = head, tail
    # collapse redundant separators in the path portion (e.g. 'repo//a.py' → 'repo/a.py')
    ref = re.sub(r"/{2,}", "/", ref).strip("/")
    return f"{ref}:{line}" if line else ref


def filter_candidates(candidates: list, state: dict) -> tuple[list, list]:
    """Split prescan candidates into (kept, dropped) — drop any whose `.ref` matches a
    dismissed recommendation in state["dismissed"]. Compared on the NORMALIZED repo/file:line
    ref, so a dismissed proposal's evidence and the fresh candidate produced by the same
    signal collapse to the same key. This is the GUARANTEE a dismissed proposal can't re-form:
    its signal never reaches the LLM. Candidates are prescan.Candidate objects (have `.ref`)
    OR dicts with a "ref" key. Never raises."""
    dismissed = (state or {}).get("dismissed") or {}
    if not dismissed:
        return list(candidates), []
    keys = {_canon_ref(k) for k in dismissed}
    kept, dropped = [], []
    for c in candidates:
        ref = c.ref if hasattr(c, "ref") else (c.get("ref") if isinstance(c, dict) else "")
        if _canon_ref(ref) in keys:
            dropped.append(c)
        else:
            kept.append(c)
    return kept, dropped


def dismissed_entries(state: dict) -> list[dict]:
    """Sorted list of dismissed recommendations for --show-exclusions / the digest footer:
    [{ref, reason, repo, dismissed_at}, …]."""
    d = (state or {}).get("dismissed") or {}
    out = []
    for ref in sorted(d):
        e = d[ref] or {}
        out.append({
            "ref": ref,
            "reason": (e.get("reason") or "").strip(),
            "repo": e.get("repo") or "",
            "dismissed_at": e.get("dismissed_at") or "",
        })
    return out


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
    """Pretty one-block summary of the current exclusion state for --show-exclusions —
    REPO-level exclusions AND per-recommendation dismissals."""
    repos = (state or {}).get("repos") or {}
    dismissed = dismissed_entries(state)
    if not repos and not dismissed:
        return ("repo-cos exclusions: none.\n(Excluded repos + dismissed recommendations come "
                "from your emailed reply; hand-edit ~/.config/repo-cos/exclusions.json to "
                "adjust.)")

    lines: list[str] = []
    if repos:
        lines.append(f"repo-cos exclusions: {len(repos)} repo(s) currently dropped from the "
                     "scan.")
        lines.append("")
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
    else:
        lines.append("repo-cos exclusions: no repos paused.")
        lines.append("")

    if dismissed:
        lines.append(f"repo-cos dismissed recommendations: {len(dismissed)} suppressed "
                     "proposal(s) (repo kept in scope).")
        lines.append("")
        for d in dismissed:
            when = d["dismissed_at"] or "?"
            lines.append(f"  {d['ref']}  [since {when}]")
            if d["reason"]:
                lines.append(f"      reason: {d['reason']}")
        lines.append("")

    lines.append('Reply "resume <repo>" to re-enable a repo; edit '
                 "~/.config/repo-cos/exclusions.json to un-dismiss a recommendation.")
    return "\n".join(lines)
