#!/usr/bin/env python3
"""Espanso usage audit — TWO complementary signals.

Espanso, on firing, BACKSPACES the trigger away and pastes the replacement via
the CLIPBOARD, so BOTH the trigger and its expansion are ERASED from any stored
text. That breaks naive counting two different ways, hence two signals:

  1. PRIMARY — keylog (TRUE fires).  The X11 keylogger's `EspansoDetector`
     (scripts/collector/keylog/espanso_detect.py) detects espanso usage AT
     CAPTURE TIME from the raw keystroke stream, BEFORE espanso reacts, and
     emits one `source=keys, kind=espanso` row per fire into ClickHouse. This is
     the real per-trigger fire count, split direct vs Ctrl+Space-search. It is
     FORWARD-ONLY: there is no historical data before the detector was deployed.
     Honest caveat: direct fires are high-fidelity but can rarely over-count if a
     trigger string is assembled via a mouse-repositioned caret (key events only)
     or in a per-app espanso-disabled context; search rows are best-effort /
     inferred. Still far better than phrase-counting.

  2. SECONDARY — transcript miner (ADD-CANDIDATES).  Because the expansion IS
     what Claude sees, mining `~/.claude/projects/**/*.jsonl` cannot reliably
     tell a snippet fire from hand-typing (the two produce identical text). Its
     durable, UNIQUE value is the inverse view: recurring SHORT phrases you type
     that are NOT yet snippets — i.e. candidates to ADD. The per-trigger
     "hits" it reports are kept only as a rough, ambiguous cross-check.

Credentials for the keylog signal (read-only reader, from env — NEVER hardcoded;
same pattern as activity-scan.py / validation/chquery.py):
  export CLICKHOUSE_URL=http://192.168.50.94:30123
  export CLICKHOUSE_USER=activity_reader
  export CLICKHOUSE_PASSWORD=<reader-password>   # from SOPS

Usage: espanso-usage.py [--since YYYY-MM-DD] [--source keys|transcript|both]
                        [--root PATH] [--host LABEL]
  --source   which signal(s) to show (default: both; keylog shown first)
"""
import json, os, re, glob, collections, sys
from pathlib import Path

# --- args ---
SINCE = None
ROOT = os.path.expanduser("~/.claude/projects")
HOST = ""
SOURCE = "both"
_a = sys.argv[1:]
while _a:
    k = _a.pop(0)
    if k == "--since":    SINCE = _a.pop(0)
    elif k == "--root":   ROOT = os.path.expanduser(_a.pop(0))
    elif k == "--host":   HOST = _a.pop(0)
    elif k == "--source": SOURCE = _a.pop(0)
    else:
        sys.stderr.write(f"unknown arg: {k}\n"); sys.exit(2)
if SOURCE not in ("keys", "transcript", "both"):
    sys.stderr.write("--source must be keys|transcript|both\n"); sys.exit(2)


# --------------------------------------------------------------------------- #
# PRIMARY signal — keylog TRUE fires from ClickHouse (source=keys, kind=espanso)
# --------------------------------------------------------------------------- #
def keylog_section(since):
    """Print the per-trigger TRUE-fire table from the keylog espanso rows.

    Degrades gracefully: if ClickHouse is unreachable OR there are zero espanso
    rows yet (detection is forward-only), print a clear note and return — the
    transcript section still runs.
    """
    print("## PRIMARY — keylog TRUE fires (source=keys, kind=espanso)\n")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "validation"))
        import chquery as Q  # noqa: E402
        conn = Q.CHConn.from_env()
        client = Q.CHClient(conn)
    except Exception as e:
        print(f"(keylog signal unavailable — {e.__class__.__name__}: {e})")
        print("(set CLICKHOUSE_URL/USER/PASSWORD; detection is forward-only)\n")
        return
    where = "source = 'keys' AND kind = 'espanso'"
    if since:
        where += f" AND ts >= {Q.sql_quote(since)}"
    sql = (
        "SELECT text AS trigger, "
        "JSONExtractString(payload, 'method') AS method, "
        "JSONExtractBool(payload, 'inferred') AS inferred, "
        "count() AS fires "
        f"FROM {conn.fq_table} WHERE {where} "
        "GROUP BY trigger, method, inferred ORDER BY fires DESC, trigger"
    )
    try:
        rows = client.rows(sql)
    except Exception as e:
        print(f"(ClickHouse query failed — {e.__class__.__name__}: {e})")
        print("(no keylog espanso events yet — detection is forward-only; "
              "collecting since deploy)\n")
        return
    if not rows:
        print("(no keylog espanso events yet — detection is forward-only; "
              "collecting since deploy)\n")
        return
    # Aggregate per trigger with a direct/search split.
    per = collections.defaultdict(lambda: {"direct": 0, "search": 0, "inferred": False})
    total = 0
    for r in rows:
        trig = r.get("trigger") or "(unattributed search)"
        method = r.get("method") or "direct"
        fires = int(r.get("fires") or 0)
        total += fires
        bucket = per[trig]
        bucket["direct" if method == "direct" else "search"] += fires
        if r.get("inferred"):
            bucket["inferred"] = True
    print(f"# total fires: {total}\n")
    print(f"{'trigger':22} {'direct':>7} {'search':>7} {'total':>7}  note")
    order = sorted(per, key=lambda t: (-(per[t]['direct'] + per[t]['search']), t))
    for t in order:
        b = per[t]
        tot = b["direct"] + b["search"]
        note = "search=inferred attribution" if b["search"] else ""
        print(f"{t:22} {b['direct']:>7} {b['search']:>7} {tot:>7}  {note}")
    print()


# --------------------------------------------------------------------------- #
# SECONDARY signal — transcript miner (ADD-CANDIDATES + ambiguous cross-check)
# --------------------------------------------------------------------------- #
# trigger -> (kind, [include substrs] | None, [exclude substrs], ambiguous?)
# include=None => not text-detectable (date/clipboard/typo-correction).
SNIPPETS = {
    ":date":     ("date", None, [], False),
    ":time":     ("date", None, [], False),
    ":datetime": ("date", None, [], False),
    ":iso":      ("date", None, [], False),
    # paths
    ":hlt":   ("path", ["homelab-talos"], [], False),
    ":kuc":   ("path", ["workspace/kubeclaw"], [], False),
    ":nixos": ("path", ["/etc/nixos/configuration.nix"], [], False),
    ":cc":    ("path", ["civit/civitai "], [], False),
    ":cdp":   ("path", ["civit/datapacket-talos"], ["prod-kubeconfig"], False),
    ":cgf":   ("path", ["civitai-gpu-fleet"], [], False),
    ":cmo":   ("path", ["civitai-orchestration"], [], False),
    ":csc":   ("path", ["civitai-spine-controller"], [], False),
    ":cpk":   ("path", ["datapacket-talos/prod-kubeconfig"], [], False),
    ":subk":  ("path", ["submodel-dc-03-a-kubeconfig"], [], False),
    # workflow prompts (current expansions)
    ":eos":     ("prompt", ["identify skills that may need updating"], [], False),
    ":acq":     ("prompt", ["recommend anything you think would be useful to include"], [], False),
    ":ds":      ("prompt", ["dispatch subagent to"], ["adversarially audit the PR", "identify skills that may need updating"], True),
    ":aep":     ("prompt", None, [], False),
    ":rns":     ("prompt", ["recommend next steps"], ["ranked by leverage"], True),
    ":rnx":     ("prompt", ["recommend next steps ranked by leverage"], [], False),
    ":pst":     ("prompt", ["proceed, use subagent, ensure test coverage"], [], True),
    ":kickoff": ("prompt", ["kickoff message to copy paste to next session"], [], False),
    ":nday":    ("prompt", ["it's the next day, check"], [], True),
    ":fhrs":    ("prompt", ["it's been a few hours, check"], [], True),
    ":fdays":   ("prompt", ["it's been a few days, check"], [], True),
    # utilities / typo-correction — output not distinguishable
    ":uuid":     ("util", None, [], False),
    ":clip":     ("util", None, [], False),
    "dashbaord": ("typo", None, [], False),
    "reocmmend": ("typo", None, [], False),
}

WORD = re.compile(r"[a-z][a-z'\-]+")
KNOWN_EXPANSIONS = [m for _, subs, _, _ in SNIPPETS.values() if subs for m in subs]


def norm(s):
    return " ".join(s.lower().split())


def human_text(o):
    if o.get("type") != "user":
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
    low = s.lower()
    if s.startswith("<command-") or "<local-command-stdout>" in s \
       or s.startswith("[request interrupted") or low.startswith("caveat:") \
       or s.startswith("<system-reminder") or "tool_use_id" in s:
        return None
    return s


def transcript_section(since, root, host):
    counts = {t: 0 for t in SNIPPETS}
    sessions_hit = {t: set() for t in SNIPPETS}
    last_seen = {t: None for t in SNIPPETS}
    short_msgs = collections.Counter()
    phrase_counter = collections.Counter()
    total_user_msgs = 0
    files_scanned = 0

    for fp in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        files_scanned += 1
        sid = os.path.basename(fp)[:8]
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
                    total_user_msgs += 1
                    low = s.lower()
                    for t, (kind, subs, excl, _amb) in SNIPPETS.items():
                        if not subs:
                            continue
                        if any(sub in low for sub in subs) and not any(e in low for e in excl):
                            counts[t] += 1
                            sessions_hit[t].add(sid)
                            if ts and (last_seen[t] is None or ts > last_seen[t]):
                                last_seen[t] = ts
                    if len(s) <= 140 and not s.startswith("/"):
                        n = norm(s)
                        if not any(e in n for e in KNOWN_EXPANSIONS):
                            if len(n) >= 3:
                                short_msgs[n] += 1
                            words = WORD.findall(n)
                            for k in (4, 5):
                                for i in range(len(words) - k + 1):
                                    phrase_counter[" ".join(words[i:i+k])] += 1
        except Exception:
            continue

    print(f"# transcript files scanned: {files_scanned}   "
          f"human user messages: {total_user_msgs}\n")

    print("## SECONDARY — transcript ADD-CANDIDATES (short phrases, no snippet)\n")
    STOP_EXACT = {"yes", "ok", "okay", "y", "continue", "go", "proceed", "do it",
                  "yes do it", "no", "thanks", "thank you", "good", "nice", "next",
                  "stop", "wait", "k", "yep", "yes please", "sure", "perfect"}
    print("### Recurring short user messages NOT already snippets (>=3 hits)\n")
    for msg, n in short_msgs.most_common(120):
        if n < 3:
            break
        if msg in STOP_EXACT or len(msg) < 8:
            continue
        print(f"{n:4}  {msg[:110]}")

    print("\n### Recurring 4-5 word phrases (candidate building blocks, >=6 hits)\n")
    shown = 0
    for ph, n in phrase_counter.most_common(400):
        if n < 6:
            break
        if any(e.startswith(ph[:15]) for e in KNOWN_EXPANSIONS):
            continue
        print(f"{n:4}  {ph}")
        shown += 1
        if shown >= 40:
            break

    print("\n### Ambiguous transcript cross-check (CONFLATES fire + hand-typing)\n")
    print(f"{'trigger':12} {'kind':7} {'hits':>6} {'sessions':>9}  {'last-seen':10} note")
    order = sorted(SNIPPETS, key=lambda t: (-counts[t], t))
    for t in order:
        kind, subs, excl, amb = SNIPPETS[t]
        if subs is None:
            print(f"{t:12} {kind:7} {'   n/a':>6} {'      n/a':>9}  {'-':10} not text-detectable")
        else:
            note = "AMBIGUOUS (conflates hand-typing)" if amb else ""
            print(f"{t:12} {kind:7} {counts[t]:>6} {len(sessions_hit[t]):>9}  {(last_seen[t] or '-'):10} {note}")


# --------------------------------------------------------------------------- #
def main():
    hdr = "# espanso usage"
    if HOST:  hdr += f" — host={HOST}"
    if SINCE: hdr += f" — since {SINCE}"
    print(hdr + "\n")

    if SOURCE in ("keys", "both"):
        keylog_section(SINCE)
    if SOURCE in ("transcript", "both"):
        transcript_section(SINCE, ROOT, HOST)


if __name__ == "__main__":
    main()
