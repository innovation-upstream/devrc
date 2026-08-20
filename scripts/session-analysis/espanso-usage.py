#!/usr/bin/env python3
"""Espanso usage audit — fires, demand, and a prune/keep VERDICT per snippet.

Espanso, on firing, BACKSPACES the trigger away and pastes the replacement via
the CLIPBOARD, so BOTH the trigger and its expansion are ERASED from any stored
text. That breaks naive counting two different ways, hence two signals:

  1. FIRES (primary) — the X11 keylogger's `EspansoDetector`
     (scripts/collector/keylog/espanso_detect.py) detects espanso usage AT
     CAPTURE TIME from the raw keystroke stream, BEFORE espanso reacts, and
     emits one `source=keys, kind=espanso` row per fire into ClickHouse. This is
     the real per-trigger fire count, split direct vs Ctrl+Space-search. It is
     FORWARD-ONLY: there is no data before the detector was deployed.

  2. DEMAND (secondary) — transcript occurrences of each expansion's text.
     Because the expansion IS what Claude sees, this CANNOT distinguish a fire
     from hand-typing or a clipboard paste — which is exactly why it is useful
     next to the fire count: *0 fires + high demand* means the snippet exists,
     is wanted, and is UNDISCOVERABLE, not dead. A clipboard paste produces no
     keystrokes at all, so this is the only signal that can see one.

The VERDICT matrix crosses the two (plus unattributed-search-term evidence) into
one action per snippet — see `classify()`.

🔴 Two silent zeros this tool refuses to produce:
  * an unreachable/rejecting ClickHouse is NOT "0 fires" (exit 3, loud banner);
  * an unparseable espanso config (e.g. PyYAML missing from the interpreter) is
    NOT "0 snippets" (exit 3, loud banner). Run under
    `nix-shell -p python3Packages.pyyaml` if the banner says PyYAML is missing.

Credentials for the fires signal (read-only reader, from env — NEVER hardcoded;
same pattern as activity-scan.py / validation/chquery.py):
  export CLICKHOUSE_URL=... CLICKHOUSE_USER=... CLICKHOUSE_PASSWORD=<from SOPS>

Usage:
  espanso-usage.py [--since YYYY-MM-DD] [--source keys|transcript|both]
                   [--root PATH] [--host LABEL]
  espanso-usage.py --terms [--since ...]        raw search-term breakdown
  espanso-usage.py --replay [--config PATH]     do observed terms resolve?
  espanso-usage.py --lint [--config PATH]       offline ambiguity check (no creds)
  espanso-usage.py --verify-deploy [--remote T] post-ship check, both hosts
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
for _rel in ("validation", "collector/keylog", "collector/claude"):
    _dir = str(_SCRIPTS / _rel)
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

import chquery as Q                                    # noqa: E402
import espanso_triggers as ET                          # noqa: E402
# `_WORD_RE` is the detector's OWN label tokenizer. Re-spelling it here (with a
# regex that kept hyphens) invented "self-miss" findings for every hyphenated
# label word, so import the one the matcher actually uses.
from espanso_detect import EspansoDetector, _WORD_RE as LABEL_WORD_RE  # noqa: E402
# ONE definition of the harness-boilerplate prefixes, shared with the claude
# activity source. The local copy this file used to carry had drifted to the
# WRONG CASING ("[request interrupted"), letting ~85% of interruption markers
# through into the ADD-CANDIDATE counts. Import, never re-spell.
from tailer import _BOILERPLATE_PREFIXES as _SHARED_BOILERPLATE  # noqa: E402
# Same reason: ONE definition of "which files are real transcripts" (it skips
# the synthetic `subagents/` + `wf_*` dirs, where a dispatched prompt is COPIED
# and would otherwise be counted again as demand).
from _shared import iter_transcripts                    # noqa: E402

try:  # PyYAML is not in the ambient python3 on this host — measured, guarded.
    import yaml as _yaml
except Exception:  # pragma: no cover - exercised only where PyYAML is missing
    _yaml = None


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
ESPANSO_BASE_REL = ".config/espanso/match/base.yml"
KEYLOG_UNIT = "keylog.service"
ESPANSO_UNIT = "espanso"
# The laptop, over nebula (same target the ship.sh/`:sshln` snippet uses).
DEFAULT_REMOTE = "zach@10.42.0.100"

# Boilerplate matched case-INSENSITIVELY: the shared tuple is spelled the way
# the collector sees it, but transcripts carry both casings (measured 2026-08-05:
# 389 "[Request interrupted" vs 47 lowercase). `[Image: original …` is a
# transcript-render artefact the collector never sees, so it is ADDED here
# rather than forked — the shared tuple stays the source of truth for the rest.
BOILERPLATE_PREFIXES = tuple(p.lower() for p in _SHARED_BOILERPLATE) + (
    "[image: original",
)

# DEMAND detection substrings: a distinctive fragment of each snippet's
# expansion, used to count transcript occurrences. Hand-maintained — a snippet
# whose expansion is text-detectable but MISSING here is reported UNPROBED, not
# DEAD, so a forgotten entry can never read as "no demand".
DEMAND_TEXTS = {
    # paths
    ":hlt":   ["homelab-talos"],
    ":kuc":   ["workspace/kubeclaw"],
    ":cc":    ["civit/civitai "],
    ":cdp":   ["civit/datapacket-talos"],
    ":cgf":   ["civitai-gpu-fleet"],
    ":cmo":   ["civitai-orchestration"],
    ":csc":   ["civitai-spine-controller"],
    ":cpk":   ["datapacket-talos/prod-kubeconfig"],
    ":subk":  ["submodel-dc-03-a-kubeconfig"],
    # workflow prompts. :eos was rewritten 2026-08-04 (#325) — match BOTH the
    # pre-rewrite text and the eviction-half rewrite, because transcripts from
    # earlier in any window still carry the old wording.
    ":eos":     ["may need updating", "write the handoff first"],
    # :acq's probe was "recommend anything you think would be useful to
    # include", which matched NEITHER the pre- nor the post-2026-08-19
    # expansion — a silent UNPROBED on the config's second-busiest snippet.
    # Match the opener instead: it is the half that has never been reworded.
    ":acq":     ["dispatch subagent to process feedback"],
    ":kickoff": ["kickoff message to copy paste to next session"],
    ":rna":     ["recommend next actions"],
    ":lr":      ["limit restored, resume agent"],
    ":mt":      ["tee up what we can do in the meantime"],
    # Added 2026-08-19 with the snippets themselves. Without these the next
    # /espanso-audit reports them UNPROBED and cannot re-measure the transcript
    # demand that justified adding them in the first place.
    ":alo":     ["left open or unaddressed from this session"],
    ":pdt":     ["proceed, dispatch, include complete test coverage"],
    ":cgt":     ["clawgate task to pick up the issues"],
}
# Substrings that DISQUALIFY a demand hit (one expansion containing another).
DEMAND_EXCLUDE = {":cdp": ["prod-kubeconfig"]}

# An unattributed search term counts as evidence FOR a snippet only if it is
# long enough to be a real query and specific enough to mean something. A 1-2
# char term ("c", "f") matches half the snippet set through `search_terms`
# substrings and is noise, not evidence.
UNATTR_EVIDENCE_MIN_LEN = 3
UNATTR_EVIDENCE_MAX_MATCHES = 4

UNATTRIBUTED = ""            # `text` column value for an unattributed search
UNATTRIBUTED_LABEL = "(unattributed search)"

# Verdicts
HEALTHY = "HEALTHY"
UNFINDABLE = "UNFINDABLE"
UNATTRIBUTABLE = "UNATTRIBUTABLE"
KEYLOG_ONLY = "KEYLOG-ONLY"
UNPROBED = "UNPROBED"
DEAD = "DEAD"
RETIRED = "RETIRED"

VERDICT_ACTION = {
    HEALTHY:        "keep",
    UNFINDABLE:     "RETUNE label/search_terms — do NOT prune (demand is proven)",
    UNATTRIBUTABLE: "do NOT prune — real searches match it but resolve ambiguously",
    KEYLOG_ONLY:    "expansion is not text-detectable; fires are the ONLY signal",
    UNPROBED:       "no DEMAND_TEXTS entry — demand UNMEASURED, cannot judge",
    DEAD:           "prune",
    RETIRED:        "fired in-window but no longer in the config",
}


# --------------------------------------------------------------------------- #
# Loud failure types — a signal we could not MEASURE is never a zero
# --------------------------------------------------------------------------- #
class Unmeasured(RuntimeError):
    """A signal could not be measured. Distinct from a measured zero."""

    def __init__(self, message, *, reason=""):
        super().__init__(message)
        self.reason = reason


class FiresUnmeasured(Unmeasured):
    """ClickHouse was unreachable / rejected the query / is unconfigured."""


class ConfigUnavailable(Unmeasured):
    """The espanso config could not be read or parsed into a trigger set."""


# --------------------------------------------------------------------------- #
# Config loading (never degrades to an empty trigger set)
# --------------------------------------------------------------------------- #
def default_config_path() -> str:
    return os.path.join(os.path.expanduser("~"), ESPANSO_BASE_REL)


def parse_config_text(text, *, origin="config") -> ET.TriggerSet:
    """Parse a base.yml body into a TriggerSet, or raise ConfigUnavailable.

    `espanso_triggers.load_triggers` deliberately degrades to an EMPTY
    TriggerSet on any error, because the keylogger must never crash. For an
    AUDIT that degradation is the silent zero itself — 0 triggers would classify
    every snippet as missing — so this wrapper turns each failure mode into a
    loud exception instead.
    """
    if _yaml is None:
        raise ConfigUnavailable(
            f"cannot parse {origin}: PyYAML is not available in this interpreter. "
            "Re-run under: nix-shell -p python3Packages.pyyaml --run '...'",
            reason="pyyaml")
    try:
        data = _yaml.safe_load(text)
    except Exception as e:
        raise ConfigUnavailable(f"cannot parse {origin}: {e.__class__.__name__}: {e}",
                                reason="parse") from e
    if not isinstance(data, dict):
        raise ConfigUnavailable(f"cannot parse {origin}: not a YAML mapping",
                                reason="parse")
    ts = ET.load_triggers(data)
    if not ts.triggers:
        raise ConfigUnavailable(f"{origin} parsed but declares ZERO triggers",
                                reason="empty")
    return ts


def load_config(path=None) -> ET.TriggerSet:
    p = path or default_config_path()
    try:
        text = Path(p).read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigUnavailable(f"cannot read {p}: {e.__class__.__name__}: {e}",
                                reason="io") from e
    return parse_config_text(text, origin=p)


# --------------------------------------------------------------------------- #
# Pure classification
# --------------------------------------------------------------------------- #
_TEMPLATE_RE = re.compile(r"\{\{.*?\}\}")
_SHELL_RE = re.compile(r"^(ssh|scp|kubectl|curl|sudo)\s", re.I)


def expansion_kind(replace) -> str:
    """Classify an expansion by whether it can EVER appear in a transcript.

    'template' — espanso var ({{date}}, {{clip}}, {{uuid}}): the output varies
                 and is indistinguishable from ordinary text.
    'shell'    — a command pasted into a terminal, never into a chat message.
    'typo'     — a bare single-word correction (dashbaord -> dashboard): the
                 output is an ordinary English word, so counting it is noise.
    'text'     — a real phrase/path; transcript occurrences ARE demand evidence.
    """
    r = (replace or "").strip()
    if not r:
        return "empty"
    if _TEMPLATE_RE.search(r):
        return "template"
    if _SHELL_RE.match(r):
        return "shell"
    if " " not in r and "/" not in r:
        return "typo"
    return "text"


def is_text_detectable(replace) -> bool:
    return expansion_kind(replace) == "text"


def classify(*, fires, demand, term_evidence, text_detectable, in_config=True) -> str:
    """One snippet's verdict. Pure — no ClickHouse, no filesystem.

    ORDER IS LOAD-BEARING and each branch must stay reachable:
      RETIRED        not in the live config at all (only historical fires).
      HEALTHY        it fires. Nothing else can override that.
      UNFINDABLE     0 fires but proven demand -> the search UI can't find it.
      UNATTRIBUTABLE 0 fires, no/unmeasurable demand, but real search terms
                     match it (they resolved ambiguously, so `_attribute`
                     returned None by design). Checked BEFORE KEYLOG-ONLY so a
                     non-text-detectable snippet with term evidence (the :ssh*
                     case) is not filed under "no signal".
      KEYLOG-ONLY    the expansion cannot appear in text at all.
      UNPROBED       text-detectable but no DEMAND_TEXTS entry -> UNMEASURED.
      DEAD           measured zero on every signal. The only prune verdict.
    """
    if not in_config:
        return RETIRED
    if fires > 0:
        return HEALTHY
    if demand is not None and demand > 0:
        return UNFINDABLE
    if term_evidence > 0:
        return UNATTRIBUTABLE
    if not text_detectable:
        return KEYLOG_ONLY
    if demand is None:
        return UNPROBED
    return DEAD


def term_evidence(unattributed_terms, ts):
    """Unattributed search terms -> {trigger: weight} + per-trigger detail.

    `unattributed_terms` is [(term, count), ...]. Matching reuses the REAL
    `EspansoDetector._term_matches` (an instance method that only reads
    `self.ts`) rather than a reimplementation, so a rules change in the detector
    can never silently diverge from this report.
    """
    det = EspansoDetector(ts)
    weight = collections.Counter()
    detail = collections.defaultdict(list)
    for term, count in unattributed_terms:
        t = (term or "").strip().lower()
        if len(t) < UNATTR_EVIDENCE_MIN_LEN:
            continue
        matches = [trig for trig in ts.triggers if det._term_matches(t, trig)]
        if not matches or len(matches) > UNATTR_EVIDENCE_MAX_MATCHES:
            continue
        for trig in matches:
            weight[trig] += int(count or 0)
            detail[trig].append((t, int(count or 0), len(matches)))
    return weight, detail


def build_verdicts(ts, fires_per, demand, evidence):
    """One row per snippet (config order), plus RETIRED rows for triggers that
    only exist in the fire data. `demand` maps trigger -> int or None."""
    rows = []
    for trig in ts.triggers:
        meta = ts.meta.get(trig) or {}
        f = fires_per.get(trig) or {}
        total = int(f.get("direct", 0)) + int(f.get("search", 0))
        detectable = is_text_detectable(meta.get("replace"))
        d = demand.get(trig) if demand is not None else None
        ev = int(evidence.get(trig, 0))
        rows.append({
            "trigger": trig,
            "fires": total,
            "direct": int(f.get("direct", 0)),
            "search": int(f.get("search", 0)),
            "demand": d,
            "term_evidence": ev,
            "kind": expansion_kind(meta.get("replace")),
            "verdict": classify(fires=total, demand=d, term_evidence=ev,
                                text_detectable=detectable),
        })
    for trig, f in sorted(fires_per.items()):
        if trig == UNATTRIBUTED or trig in ts.meta:
            continue
        total = int(f.get("direct", 0)) + int(f.get("search", 0))
        rows.append({
            "trigger": trig, "fires": total,
            "direct": int(f.get("direct", 0)), "search": int(f.get("search", 0)),
            "demand": None, "term_evidence": 0, "kind": "retired",
            "verdict": classify(fires=total, demand=None, term_evidence=0,
                                text_detectable=False, in_config=False),
        })
    return rows


# --------------------------------------------------------------------------- #
# FIRES — keylog rows from ClickHouse
# --------------------------------------------------------------------------- #
def _host_filter(host):
    return f" AND host = {Q.sql_quote(host)}" if host else ""


def q_fires(since=None, host=None, table="activity.events") -> str:
    where = "source = 'keys' AND kind = 'espanso'"
    if since:
        where += f" AND ts >= {Q.sql_quote(since)}"
    where += _host_filter(host)
    return (
        "SELECT text AS trigger, "
        "JSONExtractString(payload, 'method') AS method, "
        "JSONExtractBool(payload, 'inferred') AS inferred, "
        "count() AS fires "
        f"FROM {table} WHERE {where} "
        "GROUP BY trigger, method, inferred ORDER BY fires DESC, trigger"
    )


def q_terms(since=None, host=None, table="activity.events") -> str:
    where = ("source = 'keys' AND kind = 'espanso' AND "
             "JSONExtractString(payload, 'method') = 'search'")
    if since:
        where += f" AND ts >= {Q.sql_quote(since)}"
    where += _host_filter(host)
    return (
        "SELECT text AS trigger, "
        "JSONExtractString(payload, 'search_term') AS term, "
        "count() AS n "
        f"FROM {table} WHERE {where} "
        "GROUP BY trigger, term ORDER BY n DESC, term"
    )


def aggregate_fires(rows):
    """Fire rows -> ({trigger: {direct, search, inferred}}, total)."""
    per = collections.defaultdict(lambda: {"direct": 0, "search": 0, "inferred": False})
    total = 0
    for r in rows:
        trig = r.get("trigger") or UNATTRIBUTED
        method = r.get("method") or "direct"
        fires = int(r.get("fires") or 0)
        total += fires
        bucket = per[trig]
        bucket["direct" if method == "direct" else "search"] += fires
        if r.get("inferred"):
            bucket["inferred"] = True
    return dict(per), total


def split_terms(rows):
    """Term rows -> (attributed [(term, trigger, n)], unattributed [(term, n)])."""
    attributed, unattributed = [], []
    for r in rows:
        term = r.get("term") or ""
        n = int(r.get("n") or 0)
        trig = r.get("trigger") or ""
        if trig:
            attributed.append((term, trig, n))
        else:
            unattributed.append((term, n))
    return attributed, unattributed


def open_client():
    """Build a CHClient from the env, or raise FiresUnmeasured."""
    try:
        conn = Q.CHConn.from_env()
    except RuntimeError as e:
        raise FiresUnmeasured(f"ClickHouse NOT CONFIGURED — {e}",
                              reason="unconfigured") from e
    return Q.CHClient(conn), conn


def gather_fires(client, since=None, host=None, table="activity.events"):
    """Run both keylog queries. Raises FiresUnmeasured — NEVER returns a zero
    for a server that was unreachable or that rejected the query.

    🔴 The predecessor wrapped this in `except Exception` and printed "(no keylog
    espanso events yet)", so a down ClickHouse read as 0 fires for EVERY snippet
    and the next audit pruned live ones. chquery's taxonomy exists precisely to
    tell "could not reach" from "rejected my query"; both are UNMEASURED here,
    and they are reported with different text.
    """
    try:
        fire_rows = client.rows(q_fires(since, host, table))
        term_rows = client.rows(q_terms(since, host, table))
    except Q.CHUnreachable as e:
        raise FiresUnmeasured(f"ClickHouse UNREACHABLE — {e}",
                              reason="unreachable") from e
    except Q.CHQueryError as e:
        raise FiresUnmeasured(f"ClickHouse REJECTED the query — {e}",
                              reason="query") from e
    per, total = aggregate_fires(fire_rows)
    attributed, unattributed = split_terms(term_rows)
    return {"per": per, "total": total, "fire_rows": fire_rows,
            "attributed": attributed, "unattributed": unattributed}


def unmeasured_banner(exc, what="FIRES"):
    return [
        "",
        "!" * 72,
        f"!! {what} UNMEASURED — {exc}",
        "!! This is NOT a measurement of zero. Do NOT prune anything from this run.",
        "!" * 72,
        "",
    ]


def render_fires(data):
    out = ["## FIRES — keylog TRUE fires (source=keys, kind=espanso)", ""]
    per, total = data["per"], data["total"]
    if not per:
        out.append("(0 espanso rows in this window — detection is forward-only, so a "
                   "window that predates the detector's deploy is EXPECTED to be empty)")
        out.append("")
        return out
    out.append(f"# total fires: {total}")
    out.append("")
    out.append(f"{'trigger':22} {'direct':>7} {'search':>7} {'total':>7}  note")
    order = sorted(per, key=lambda t: (-(per[t]["direct"] + per[t]["search"]), t))
    for t in order:
        b = per[t]
        tot = b["direct"] + b["search"]
        note = "search=inferred attribution" if b["search"] else ""
        name = UNATTRIBUTED_LABEL if t == UNATTRIBUTED else t
        out.append(f"{name:22} {b['direct']:>7} {b['search']:>7} {tot:>7}  {note}")
    out.append("")
    return out


# --------------------------------------------------------------------------- #
# DEMAND — transcript occurrences (the LOCAL filesystem only)
# --------------------------------------------------------------------------- #
def local_host_label(env=None, env_file=None) -> str:
    """This machine's ACTIVITY_HOST label, or "" when it cannot be resolved.

    `hostname` is "nixos" on BOTH hosts, so it is useless here; the collector's
    env file is the source of truth the telemetry itself is stamped with.
    """
    e = os.environ if env is None else env
    v = (e.get("ACTIVITY_HOST") or "").strip().lower()
    if v in ("workbench", "laptop"):
        return v
    path = env_file or os.path.expanduser("~/.config/activity-collector/env")
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("ACTIVITY_HOST="):
            val = line[len("ACTIVITY_HOST="):].strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            val = val.strip().lower()
            return val if val in ("workbench", "laptop") else ""
    return ""


def norm(s):
    return " ".join(s.lower().split())


def is_boilerplate(s) -> bool:
    """True when a message is harness boilerplate, not something the user typed."""
    low = (s or "").lower()
    return any(low.startswith(p) for p in BOILERPLATE_PREFIXES)


def human_text(o):
    """The user-typed text of one transcript line, or None if it is not one."""
    if o.get("type") != "user":
        return None
    # Harness-synthesised turns, and the sidechain copies of a dispatched
    # prompt: not something the user typed, and counting them inflated DEMAND.
    if o.get("isMeta") or o.get("isSidechain"):
        return None
    m = o.get("message", {})
    c = m.get("content")
    if isinstance(c, list):
        parts = []
        for b in c:
            if isinstance(b, dict):
                if b.get("type") == "tool_result":
                    return None
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
        txt = "\n".join(parts)
    elif isinstance(c, str):
        txt = c
    else:
        return None
    s = txt.strip()
    if not s:
        return None
    if s.startswith("<command-") or "<local-command-stdout>" in s \
       or s.startswith("<system-reminder") or "tool_use_id" in s \
       or is_boilerplate(s):
        return None
    return s


def scan_transcripts(root, since=None, demand_texts=None, exclude=None):
    """Count per-snippet demand + recurring short messages over local transcripts."""
    demand_texts = DEMAND_TEXTS if demand_texts is None else demand_texts
    exclude = DEMAND_EXCLUDE if exclude is None else exclude
    known = [sub for subs in demand_texts.values() for sub in subs]

    counts = {t: 0 for t in demand_texts}
    sessions_hit = {t: set() for t in demand_texts}
    last_seen = {t: None for t in demand_texts}
    short_msgs = collections.Counter()
    total_user_msgs = 0
    files_scanned = 0
    # Resuming/forking a session can write a NEW transcript file that REPLAYS
    # earlier messages, so dedupe on the message uuid exactly as tailer.py does.
    # Measured a no-op over the 2026-07-25..08-05 window (identical counts with
    # and without) — kept as a guard, not as a correction.
    seen = set()

    for fp, stem in iter_transcripts([root]):
        files_scanned += 1
        sid = stem[:8]
        try:
            with open(fp, errors="ignore") as fh:
                for line in fh:
                    if '"type":"user"' not in line and '"type": "user"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    ts = o.get("timestamp", "")[:10]
                    if since and ts and ts < since:
                        continue
                    s = human_text(o)
                    if s is None:
                        continue
                    uid = o.get("uuid")
                    if isinstance(uid, str) and uid:
                        if uid in seen:
                            continue
                        seen.add(uid)
                    total_user_msgs += 1
                    low = s.lower()
                    for t, subs in demand_texts.items():
                        excl = exclude.get(t) or []
                        if any(sub in low for sub in subs) and \
                           not any(e in low for e in excl):
                            counts[t] += 1
                            sessions_hit[t].add(sid)
                            if ts and (last_seen[t] is None or ts > last_seen[t]):
                                last_seen[t] = ts
                    if len(s) <= 140 and not s.startswith("/"):
                        n = norm(s)
                        if len(n) >= 3 and not any(e in n for e in known):
                            short_msgs[n] += 1
        except Exception:
            continue

    return {"files": files_scanned, "messages": total_user_msgs,
            "demand": counts, "sessions": {t: len(v) for t, v in sessions_hit.items()},
            "last_seen": last_seen, "short": short_msgs}


STOP_EXACT = {"yes", "ok", "okay", "y", "continue", "go", "proceed", "do it",
              "yes do it", "no", "thanks", "thank you", "good", "nice", "next",
              "stop", "wait", "k", "yep", "yes please", "sure", "perfect"}


def render_demand(scan, host_label):
    out = [f"# transcript files scanned: {scan['files']}   "
           f"human user messages: {scan['messages']}   "
           f"(LOCAL filesystem — host: {host_label or 'UNRESOLVED'})", ""]
    out.append("## ADD-CANDIDATES — recurring short messages that are NOT snippets")
    out.append("   keep/kill predictor is SHAPE, not length: a whole standalone")
    out.append("   message sticks, a mid-sentence fragment does not.")
    out.append("")
    for msg, n in scan["short"].most_common(120):
        if n < 3:
            break
        if msg in STOP_EXACT or len(msg) < 8:
            continue
        out.append(f"{n:4}  {msg[:110]}")
    out.append("")
    return out


# --------------------------------------------------------------------------- #
# VERDICT rendering
# --------------------------------------------------------------------------- #
def render_verdicts(rows):
    out = ["## VERDICT — prune/keep matrix", ""]
    out.append(f"{'trigger':12} {'fires':>6} {'demand':>7} {'term-ev':>8} "
               f"{'kind':9} {'verdict':15} action")
    order = {HEALTHY: 0, UNFINDABLE: 1, UNATTRIBUTABLE: 2, KEYLOG_ONLY: 3,
             UNPROBED: 4, DEAD: 5, RETIRED: 6}
    for r in sorted(rows, key=lambda r: (order.get(r["verdict"], 9),
                                         -r["fires"], r["trigger"])):
        d = "n/a" if r["demand"] is None else str(r["demand"])
        out.append(f"{r['trigger']:12} {r['fires']:>6} {d:>7} {r['term_evidence']:>8} "
                   f"{r['kind']:9} {r['verdict']:15} {VERDICT_ACTION[r['verdict']]}")
    out.append("")
    tally = collections.Counter(r["verdict"] for r in rows)
    out.append("  " + "  ".join(f"{k}={tally[k]}" for k in sorted(tally)))
    out.append("")
    return out


# --------------------------------------------------------------------------- #
# TERMS / REPLAY / LINT
# --------------------------------------------------------------------------- #
def _q(term, width=28):
    """Quote a search term so leading/trailing spaces are VISIBLE — 'ssh ' and
    'ssh' are different rows and must not render identically."""
    return f"{term!r}"[:width].ljust(width)


def render_terms(fires, ts):
    out = ["## TERMS — raw Ctrl+Space search-term breakdown", ""]
    out.append("### attributed")
    out.append(f"{'n':>5}  {'term':28} -> trigger")
    for term, trig, n in sorted(fires["attributed"], key=lambda r: (-r[2], r[0])):
        out.append(f"{n:>5}  {_q(term)} -> {trig}")
    unattr_total = sum(n for _, n in fires["unattributed"])
    out.append("")
    out.append(f"### unattributed ({unattr_total} fires over "
               f"{len(fires['unattributed'])} distinct terms)")
    out.append("   `_attribute` returns None whenever a term matches 0 or >=2 "
               "snippets — by design.")
    out.append(f"{'n':>5}  {'term':28} competing snippets (current config)")
    det = EspansoDetector(ts) if ts is not None else None
    for term, n in sorted(fires["unattributed"], key=lambda r: (-r[1], r[0])):
        if det is None:
            out.append(f"{n:>5}  {_q(term)} (config unavailable)")
            continue
        t = (term or "").strip().lower()
        matches = [x for x in ts.triggers if det._term_matches(t, x)] if t else []
        out.append(f"{n:>5}  {_q(term)} "
                   f"{len(matches)}: {' '.join(matches) if matches else '-'}")
    out.append("")
    return out


def replay_terms(term_counts, ts):
    """[(term, count)] -> replay rows through the REAL detector matching rules."""
    det = EspansoDetector(ts)
    rows = []
    for term, count in term_counts:
        t = (term or "").strip().lower()
        matches = [x for x in ts.triggers if det._term_matches(t, x)] if t else []
        rows.append({
            "term": term, "count": int(count or 0), "n_matches": len(matches),
            "matches": matches, "resolves_to": det._attribute(term),
        })
    return rows


def render_replay(rows, config_path):
    out = [f"## REPLAY — observed search terms vs {config_path}", "",
           "   Uses the REAL EspansoDetector matching rules (imported, not "
           "re-spelled).",
           "   A term that resolves to None fired NOTHING: 0 matches = "
           "undiscoverable,", "   >=2 = ambiguous.", ""]
    out.append(f"{'n':>5}  {'term':28} {'#':>3}  resolves-to   matches")
    for r in sorted(rows, key=lambda r: (-r["count"], r["term"])):
        res = r["resolves_to"] or "-NONE-"
        out.append(f"{r['count']:>5}  {_q(r['term'])} {r['n_matches']:>3}  "
                   f"{res:12}  {' '.join(r['matches'])}")
    unresolved = sum(r["count"] for r in rows if not r["resolves_to"])
    total = sum(r["count"] for r in rows)
    out.append("")
    out.append(f"  resolves: {total - unresolved}/{total} fires; "
               f"{unresolved} would still land nowhere.")
    out.append("")
    return out


def declared_terms(ts, trig):
    """Every term a snippet can be FOUND by: its search_terms verbatim, plus the
    words of its label as the DETECTOR tokenizes them."""
    meta = ts.meta.get(trig) or {}
    terms = set()
    for st in meta.get("search_terms") or []:
        t = (st or "").strip().lower()
        if t:
            terms.add(t)
    for w in LABEL_WORD_RE.findall((meta.get("label") or "").lower()):
        if len(w) >= UNATTR_EVIDENCE_MIN_LEN:
            terms.add(w)
    return terms


def lint(ts):
    """Offline findings, worst first.

    🔴 EVERY finding here is about ATTRIBUTION, never about reachability.
    espanso's search UI lists EVERY match as a row and the user picks one, so a
    term matching >=2 snippets shows two rows — it does NOT fail. What breaks is
    `_attribute`, which returns None on >=2 matches, so the fire is recorded
    UNATTRIBUTED (`_close_search` emits the row either way). A snippet with no
    uniquely-resolving term is therefore INVISIBLE TO THIS TOOL, not unreachable
    to the user.

    This docstring used to say such a snippet "cannot be reached through the
    search UI AT ALL". That is false, and on 2026-08-19 an audit acted on it:
    it stripped `label`+`search_terms` from the two nebula :ssh* snippets to
    force uniqueness, which took 'nebula'/'mesh'/'remote' from 2 picker rows to
    ZERO and made those rows render as their raw `ssh zach@...` expansion
    (espanso falls back to the replacement text when a label is absent).
    Fix ambiguity by changing which WORDS a snippet spells — never by removing
    its label. The old "no trigger is a prefix of another" check is kept, but it
    has never fired.
    """
    findings = []
    det = EspansoDetector(ts)
    for a in ts.triggers:
        for b in ts.triggers:
            if a != b and b.startswith(a):
                findings.append({"kind": "prefix", "trigger": a,
                                 "message": f"{a!r} is a prefix of {b!r} — espanso "
                                            f"fires the SHORTER one first"})

    matches_of = {}

    def _matches(term):
        if term not in matches_of:
            matches_of[term] = [x for x in ts.triggers if det._term_matches(term, x)]
        return matches_of[term]

    owners = collections.defaultdict(set)
    for trig in ts.triggers:
        terms = declared_terms(ts, trig)
        if not terms:
            findings.append({"kind": "undiscoverable", "trigger": trig,
                             "message": "no search_terms AND no label — reachable "
                                        "only by typing the trigger verbatim"})
            continue
        unique = []
        for t in sorted(terms):
            owners[t].add(trig)
            m = _matches(t)
            if trig not in m:
                findings.append({"kind": "self-miss", "trigger": trig,
                                 "message": f"own term {t!r} does NOT match itself"})
            elif len(m) == 1:
                unique.append(t)
        if terms and not unique:
            findings.append({"kind": "unreachable", "trigger": trig,
                             "message": "NO declared term resolves uniquely to it — "
                                        "every search that reaches it is ambiguous, so "
                                        "its fires are recorded UNATTRIBUTED. It is "
                                        "still reachable: espanso lists it as one row "
                                        "among several. Fix by changing which WORDS it "
                                        "spells — NEVER by removing its label"})
    for term in sorted(owners):
        m = _matches(term)
        if len(m) > 1:
            findings.append({"kind": "ambiguous", "trigger": ",".join(sorted(owners[term])),
                             "message": f"term {term!r} matches {len(m)} snippets: "
                                        f"{' '.join(m)}"})
    return findings


_LINT_ORDER = {"unreachable": 0, "self-miss": 1, "undiscoverable": 2,
               "prefix": 3, "ambiguous": 4}


def render_lint(findings, config_path):
    out = [f"## LINT — offline ATTRIBUTION check ({config_path})", "",
           "   🔴 AMBIGUOUS IS NOT DEAD. espanso lists EVERY match as a row and",
           "   the user picks one, so an ambiguous term still fires. What breaks",
           "   is `_attribute` (None on >=2 matches), so the fire is logged",
           "   UNATTRIBUTED — these findings are about TELEMETRY, not reach.",
           "   Fix one by changing which WORDS a snippet spells. NEVER by",
           "   removing its label: espanso then shows the raw expansion as the",
           "   row text, and the snippet loses the picker entirely.", ""]
    if not findings:
        out.append("  (no findings)")
        out.append("")
        return out
    for f in sorted(findings, key=lambda f: (_LINT_ORDER.get(f["kind"], 9),
                                             f["trigger"])):
        out.append(f"  [{f['kind']:14}] {f['trigger']:22} {f['message']}")
    out.append("")
    tally = collections.Counter(f["kind"] for f in findings)
    out.append("  " + "  ".join(f"{k}={tally[k]}" for k in sorted(tally)))
    out.append("")
    return out


# --------------------------------------------------------------------------- #
# --verify-deploy
# --------------------------------------------------------------------------- #
def make_local_runner(timeout=20):
    def run(argv):
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            return 127, ""
        except subprocess.TimeoutExpired:
            return 124, ""
        return p.returncode, (p.stdout or "").strip()
    return run


def make_ssh_runner(target, timeout=25):
    def run(argv):
        cmd = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", target,
               " ".join(shlex.quote(a) for a in argv)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            return 127, ""
        except subprocess.TimeoutExpired:
            return 124, ""
        return p.returncode, (p.stdout or "").strip()
    return run


def _unix_ts(value):
    """Parse systemd's `--timestamp=unix` value ('@1785986153') -> int or None."""
    s = (value or "").strip()
    if s.startswith("@"):
        s = s[1:]
    return int(s) if s.isdigit() else None


def check_host(label, run, cfg_path, *, expect=(), expect_absent=()):
    """Gather one host's deploy state. Pure w.r.t. I/O: everything goes through
    `run(argv) -> (rc, stdout)`, so tests inject a fake runner."""
    res = {"host": label, "problems": [], "notes": [], "triggers": None,
           "config_mtime": None, "keylog_start": None, "espanso": "unknown",
           "keylog_state": "unknown"}

    rc, out = run(["stat", "-c", "%Y", cfg_path])
    if rc == 0 and out.strip().isdigit():
        res["config_mtime"] = int(out.strip())
    else:
        res["problems"].append(f"cannot stat {cfg_path} (rc={rc})")

    rc, out = run(["systemctl", "--user", "is-active", ESPANSO_UNIT])
    res["espanso"] = out.strip() or "unknown"
    if res["espanso"] != "active":
        res["problems"].append(f"espanso.service is {res['espanso']!r}, not 'active'")

    rc, out = run(["systemctl", "--user", "show", "-P", "ActiveEnterTimestamp",
                   "--timestamp=unix", KEYLOG_UNIT])
    res["keylog_start"] = _unix_ts(out) if rc == 0 else None

    rc, out = run(["systemctl", "--user", "show", "-P", "SubState", KEYLOG_UNIT])
    res["keylog_state"] = out.strip() or "unknown"
    if res["keylog_state"] != "running":
        res["problems"].append(
            f"{KEYLOG_UNIT} SubState is {res['keylog_state']!r}, not 'running' — "
            "no fires are being recorded at all")

    rc, out = run(["cat", cfg_path])
    if rc != 0:
        res["problems"].append(f"cannot read {cfg_path} (rc={rc})")
    else:
        try:
            ts = parse_config_text(out, origin=f"{label}:{cfg_path}")
            res["triggers"] = list(ts.triggers)
        except ConfigUnavailable as e:
            # NOT "0 triggers" — an unparseable config is unknown, not empty.
            res["problems"].append(f"CONFIG UNPARSEABLE: {e}")

    if res["triggers"] is not None:
        missing = [t for t in expect if t not in res["triggers"]]
        present = [t for t in expect_absent if t in res["triggers"]]
        if missing:
            res["problems"].append(f"expected trigger(s) MISSING: {' '.join(missing)}")
        if present:
            res["problems"].append(
                f"trigger(s) expected to be GONE are still deployed: {' '.join(present)}")

    # 🔴 Staleness. keylog loads the trigger set ONCE at process init, so a
    # detector older than the config is matching against triggers that no longer
    # exist — new snippets then read 0 fires and the next audit prunes them.
    if res["config_mtime"] is not None and res["keylog_start"] is not None:
        drift = res["keylog_start"] - res["config_mtime"]
        if drift < 0:
            res["problems"].append(
                f"🔴 STALE DETECTOR: {KEYLOG_UNIT} started {-drift}s BEFORE the "
                f"espanso config was written. It is matching an OLD trigger set, so "
                f"new snippets will read 0 fires. Fix: systemctl --user restart keylog")
        else:
            res["notes"].append(
                f"detector started {drift}s after the config was written "
                "(timestamp proxy — there is no IPC to ask the running process "
                "which trigger set it loaded)")
    else:
        res["problems"].append(
            "cannot compare detector start vs config mtime — staleness UNKNOWN")
    return res


def render_verify(results):
    out = ["## VERIFY-DEPLOY", ""]
    for r in results:
        ok = "OK" if not r["problems"] else "PROBLEM"
        out.append(f"### {r['host']}  [{ok}]")
        ntrig = "UNKNOWN" if r["triggers"] is None else str(len(r["triggers"]))
        out.append(f"  espanso={r['espanso']}  keylog={r['keylog_state']}  "
                   f"triggers={ntrig}")
        for n in r["notes"]:
            out.append(f"  - {n}")
        for p in r["problems"]:
            out.append(f"  !! {p}")
        out.append("")
    known = [r for r in results if r["triggers"] is not None]
    if len(known) == 2:
        a, b = known
        only_a = sorted(set(a["triggers"]) - set(b["triggers"]))
        only_b = sorted(set(b["triggers"]) - set(a["triggers"]))
        if only_a or only_b:
            out.append("### host DIVERGENCE (ship.sh should make these identical)")
            if only_a:
                out.append(f"  only on {a['host']}: {' '.join(only_a)}")
            if only_b:
                out.append(f"  only on {b['host']}: {' '.join(only_b)}")
        else:
            out.append(f"### both hosts carry the SAME {len(a['triggers'])} triggers")
        out.append("")
    elif len(known) < 2:
        out.append("### host comparison SKIPPED — fewer than two configs parsed")
        out.append("")
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Espanso snippet usage audit — fires, demand, verdicts.")
    p.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                   help="window start (inclusive)")
    p.add_argument("--source", default="both", choices=("keys", "transcript", "both"),
                   help="which signal(s) to gather (default: both)")
    p.add_argument("--root", default="~/.claude/projects",
                   help="transcript root for the DEMAND signal")
    p.add_argument("--host", default=None,
                   help="restrict the FIRES query to one ACTIVITY_HOST label")
    p.add_argument("--terms", action="store_true",
                   help="raw search-term breakdown instead of the report")
    p.add_argument("--replay", action="store_true",
                   help="resolve observed search terms through the real detector")
    p.add_argument("--lint", action="store_true",
                   help="offline discoverability/ambiguity check (no creds needed)")
    p.add_argument("--config", default=None, metavar="PATH",
                   help="base.yml to load (default: the live one). With --replay "
                        "this is a PRE-SHIP gate on a candidate config.")
    p.add_argument("--verify-deploy", action="store_true",
                   help="post-ship check of both hosts, incl. detector staleness")
    p.add_argument("--remote", default=DEFAULT_REMOTE,
                   help=f"ssh target for the second host (default {DEFAULT_REMOTE})")
    p.add_argument("--no-remote", action="store_true",
                   help="--verify-deploy: check only this host")
    p.add_argument("--expect", default="", metavar="T,T",
                   help="--verify-deploy: triggers that MUST be deployed")
    p.add_argument("--expect-absent", default="", metavar="T,T",
                   help="--verify-deploy: triggers that must be GONE")
    return p.parse_args(argv)


def _csv(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def _run_verify(a, out):
    results = [check_host("local", make_local_runner(),
                          default_config_path(),
                          expect=_csv(a.expect), expect_absent=_csv(a.expect_absent))]
    if not a.no_remote and a.remote:
        results.append(check_host(a.remote, make_ssh_runner(a.remote),
                                  ESPANSO_BASE_REL,
                                  expect=_csv(a.expect),
                                  expect_absent=_csv(a.expect_absent)))
    out.extend(render_verify(results))
    return 4 if any(r["problems"] for r in results) else 0


def main(argv=None) -> int:
    a = parse_args(argv)
    out = []
    rc = 0

    # ---- modes that need no ClickHouse ----
    if a.verify_deploy:
        rc = _run_verify(a, out)
        print("\n".join(out))
        return rc

    if a.lint:
        try:
            ts = load_config(a.config)
        except ConfigUnavailable as e:
            print("\n".join(unmeasured_banner(e, what="CONFIG")))
            return 3
        out.extend(render_lint(lint(ts), a.config or default_config_path()))
        print("\n".join(out))
        return 0

    # ---- B2: --host is a REAL filter on the fires query only. The DEMAND
    # signal reads the LOCAL filesystem, so stamping another host's label on it
    # would mislabel the data; refuse rather than mislabel. ----
    wants_transcripts = a.source in ("transcript", "both") and not (a.terms or a.replay)
    local = local_host_label()
    if a.host and wants_transcripts and local and local.lower() != a.host.lower():
        print(f"espanso-usage: --host {a.host} filters the FIRES query, but the "
              f"DEMAND signal reads the LOCAL transcripts of {local!r}. Run this on "
              f"{a.host} for its transcripts, or add --source keys.", file=sys.stderr)
        return 2

    hdr = "# espanso usage"
    if a.since:
        hdr += f" — since {a.since}"
    if a.host:
        hdr += f" — fires filtered to host={a.host}"
    out.append(hdr)
    out.append("")

    # ---- config (needed for verdicts / terms / replay) ----
    ts, cfg_err = None, None
    try:
        ts = load_config(a.config)
    except ConfigUnavailable as e:
        cfg_err = e

    # ---- FIRES ----
    fires, fires_err = None, None
    if a.source in ("keys", "both") or a.terms or a.replay:
        try:
            client, conn = open_client()
            fires = gather_fires(client, a.since, a.host, conn.fq_table)
        except FiresUnmeasured as e:
            fires_err = e

    if a.terms or a.replay:
        if fires_err is not None:
            out.extend(unmeasured_banner(fires_err))
            print("\n".join(out))
            return 3
        if cfg_err is not None:
            out.extend(unmeasured_banner(cfg_err, what="CONFIG"))
            print("\n".join(out))
            return 3
        if a.terms:
            out.extend(render_terms(fires, ts))
        if a.replay:
            terms = [(t, n) for t, n in fires["unattributed"]]
            terms += [(t, n) for t, _trig, n in fires["attributed"]]
            out.extend(render_replay(replay_terms(terms, ts),
                                     a.config or default_config_path()))
        print("\n".join(out))
        return 0

    if a.source in ("keys", "both"):
        if fires_err is not None:
            out.extend(unmeasured_banner(fires_err))
            rc = 3
        else:
            out.extend(render_fires(fires))

    # ---- DEMAND ----
    scan = None
    if a.source in ("transcript", "both"):
        scan = scan_transcripts(os.path.expanduser(a.root), a.since)
        out.extend(render_demand(scan, local))

    # ---- VERDICT ----
    if a.source != "both":
        out.append("(VERDICT matrix needs BOTH signals — omit --source to get it)")
        out.append("")
    elif cfg_err is not None:
        out.extend(unmeasured_banner(cfg_err, what="CONFIG"))
        rc = 3
    elif fires_err is not None:
        out.append("(VERDICT matrix SKIPPED — fires were not measured; classifying "
                   "on an unmeasured signal is how live snippets get pruned)")
        out.append("")
    else:
        demand = {t: scan["demand"].get(t) for t in DEMAND_TEXTS}
        weight, _detail = term_evidence(fires["unattributed"], ts)
        out.extend(render_verdicts(build_verdicts(ts, fires["per"], demand, weight)))

    print("\n".join(out))
    return rc


if __name__ == "__main__":
    sys.exit(main())
