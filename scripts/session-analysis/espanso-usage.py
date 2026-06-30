#!/usr/bin/env python3
"""Mine Claude transcripts for espanso snippet usage + snippet candidates.

espanso expands a trigger to its replacement BEFORE Claude sees it, so
transcripts contain the replacement text, never the trigger. We detect usage
by matching each snippet's distinctive expansion substring in human-typed
user messages, and surface recurring phrases that are not yet snippets.

SNIPPETS is synced to the live config in nix/home.nix (PRs #4/#5, 2026-06-23:
:rns shortened, :rau/:mdc/:nday/... steer-extended, :aep/:cont/:pec/:wn/:rnx/
:fhrs/:fdays added, :usd removed).

Caveats baked in:
- Some expansions are now IDENTICAL to phrases you hand-type (:rns="recommend
  next steps", :wn="what's next", :pec="push an empty commit"). Their counts
  CONFLATE snippet-expansion with manual typing and are flagged ambiguous.
- :rns vs :rnx overlap ("recommend next steps" is a prefix of the :rnx text);
  handled via an exclude substring so :rns doesn't double-count :rnx.

Usage: espanso-usage.py [--since YYYY-MM-DD] [--root PATH] [--host LABEL]
"""
import json, os, re, glob, collections, sys

# --- args ---
SINCE = None
ROOT = os.path.expanduser("~/.claude/projects")
HOST = ""
_a = sys.argv[1:]
while _a:
    k = _a.pop(0)
    if k == "--since":  SINCE = _a.pop(0)
    elif k == "--root": ROOT = os.path.expanduser(_a.pop(0))
    elif k == "--host": HOST = _a.pop(0)

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
    ":whn":     ("prompt", ["write the handoff to continue in next session"], [], False),
    ":rau":     ("prompt", ["adversarially audit the pr for bugs"], [], False),
    ":aep":     ("prompt", ["adversarially audit each pr"], [], False),
    ":rns":     ("prompt", ["recommend next steps"], ["ranked by leverage"], True),
    ":rnx":     ("prompt", ["recommend next steps ranked by leverage"], [], False),
    # NOTE: PR (espanso-shorten-unfired-snippets, 2026-06-30) reverted these to
    # their short hand-typed forms — option (a) — so their counts now CONFLATE
    # snippet-expansion with manual typing (ambiguous=True), the accepted trade.
    ":pst":     ("prompt", ["proceed, use subagent, ensure test coverage"], [], True),
    ":kickoff": ("prompt", ["kickoff message to copy paste to next session"], [], False),
    ":nday":    ("prompt", ["it's the next day, check"], [], True),
    ":fhrs":    ("prompt", ["it's been a few hours, check"], [], True),
    ":fdays":   ("prompt", ["it's been a few days, check"], [], True),
    ":mdc":     ("prompt", ["merged and deployed, check"], [], True),
    ":wn":      ("prompt", ["what's next"], [], True),
    ":cont":    ("prompt", ["continue from where you left off"], [], True),
    ":pec":     ("prompt", ["push an empty commit"], [], True),
    # utilities / typo-correction — output not distinguishable
    ":uuid":     ("util", None, [], False),
    ":clip":     ("util", None, [], False),
    "dashbaord": ("typo", None, [], False),
    "reocmmend": ("typo", None, [], False),
}

counts = {t: 0 for t in SNIPPETS}
sessions_hit = {t: set() for t in SNIPPETS}
last_seen = {t: None for t in SNIPPETS}

short_msgs = collections.Counter()
phrase_counter = collections.Counter()
total_user_msgs = 0
files_scanned = 0

KNOWN_EXPANSIONS = [m for _, subs, _, _ in SNIPPETS.values() if subs for m in subs]

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

WORD = re.compile(r"[a-z][a-z'\-]+")
def norm(s):
    return " ".join(s.lower().split())

for fp in glob.glob(os.path.join(ROOT, "**", "*.jsonl"), recursive=True):
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
                if SINCE and ts and ts < SINCE:
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

hdr = f"# espanso usage"
if HOST:  hdr += f" — host={HOST}"
if SINCE: hdr += f" — since {SINCE}"
print(hdr)
print(f"# files scanned: {files_scanned}   human user messages: {total_user_msgs}\n")

print("## Espanso snippet usage (text-detectable)\n")
print(f"{'trigger':12} {'kind':7} {'hits':>6} {'sessions':>9}  {'last-seen':10} note")
order = sorted(SNIPPETS, key=lambda t: (-counts[t], t))
for t in order:
    kind, subs, excl, amb = SNIPPETS[t]
    if subs is None:
        print(f"{t:12} {kind:7} {'   n/a':>6} {'      n/a':>9}  {'-':10} not text-detectable")
    else:
        note = "AMBIGUOUS (conflates hand-typing)" if amb else ""
        print(f"{t:12} {kind:7} {counts[t]:>6} {len(sessions_hit[t]):>9}  {(last_seen[t] or '-'):10} {note}")

print("\n## Recurring short user messages NOT already snippets (>=3 hits)\n")
STOP_EXACT = {"yes", "ok", "okay", "y", "continue", "go", "proceed", "do it",
              "yes do it", "no", "thanks", "thank you", "good", "nice", "next",
              "stop", "wait", "k", "yep", "yes please", "sure", "perfect"}
for msg, n in short_msgs.most_common(120):
    if n < 3:
        break
    if msg in STOP_EXACT or len(msg) < 8:
        continue
    print(f"{n:4}  {msg[:110]}")

print("\n## Recurring 4-5 word phrases (candidate building blocks, >=6 hits)\n")
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
