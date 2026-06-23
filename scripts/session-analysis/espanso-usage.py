#!/usr/bin/env python3
"""Mine Claude transcripts for espanso snippet usage + snippet candidates.

espanso expands a trigger to its replacement BEFORE Claude sees it, so
transcripts contain the replacement text, never the trigger. We detect usage
by matching each snippet's distinctive expansion substring in human-typed
user messages, and surface recurring phrases that are not yet snippets.
"""
import json, os, re, glob, collections

ROOT = os.path.expanduser("~/.claude/projects")

# trigger -> (kind, distinctive lowercased match substrings; None = not text-detectable)
SNIPPETS = {
    ":date":     ("date", None),
    ":time":     ("date", None),
    ":datetime": ("date", None),
    ":iso":      ("date", None),
    ":hlt":      ("path", ["homelab-talos"]),
    ":kuc":      ("path", ["workspace/kubeclaw"]),
    ":nixos":    ("path", ["/etc/nixos/configuration.nix"]),
    ":cc":       ("path", ["civit/civitai "]),
    ":cdp":      ("path", ["civit/datapacket-talos"]),
    ":cgf":      ("path", ["civitai-gpu-fleet"]),
    ":cmo":      ("path", ["civitai-orchestration"]),
    ":csc":      ("path", ["civitai-spine-controller"]),
    ":cpk":      ("path", ["datapacket-talos/prod-kubeconfig"]),
    ":subk":     ("path", ["submodel-dc-03-a-kubeconfig"]),
    ":whn":      ("prompt", ["write the handoff to continue in next session"]),
    ":rau":      ("prompt", ["audit the pr for risks, regressions"]),
    ":rns":      ("prompt", ["recommend next steps, improvements, extension"]),
    ":pst":      ("prompt", ["proceed, use subagent, ensure test coverage"]),
    ":kickoff":  ("prompt", ["kickoff message to copy paste to next session"]),
    ":usd":      ("prompt", ["update the skills and project docs then write the handoff"]),
    ":nday":     ("prompt", ["it's the next day, check"]),
    ":uuid":     ("util", None),
    ":clip":     ("util", None),
    "dashbaord": ("typo", None),
}

counts = {t: 0 for t in SNIPPETS}
sessions_hit = {t: set() for t in SNIPPETS}
last_seen = {t: None for t in SNIPPETS}

# snippet-candidate mining
short_msgs = collections.Counter()        # exact normalized short messages
phrase_counter = collections.Counter()    # recurring 3-6 word phrases
total_user_msgs = 0

KNOWN_EXPANSIONS = [m for _, subs in SNIPPETS.values() if subs for m in subs]

def human_text(o):
    """Return human-typed text from a user record, or None if it's a tool
    result / slash-command wrapper / interrupt / system reminder."""
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
                s = human_text(o)
                if s is None:
                    continue
                total_user_msgs += 1
                low = s.lower()
                ts = o.get("timestamp", "")[:10]
                # snippet usage
                for t, (kind, subs) in SNIPPETS.items():
                    if not subs:
                        continue
                    if any(sub in low for sub in subs):
                        counts[t] += 1
                        sessions_hit[t].add(sid)
                        if ts and (last_seen[t] is None or ts > last_seen[t]):
                            last_seen[t] = ts
                # candidate mining: only on shortish human messages
                if len(s) <= 140 and not s.startswith("/"):
                    n = norm(s)
                    # skip if it's a known expansion
                    if not any(e in n for e in KNOWN_EXPANSIONS):
                        if len(n) >= 3:
                            short_msgs[n] += 1
                        words = WORD.findall(n)
                        for k in (4, 5):
                            for i in range(len(words) - k + 1):
                                phrase_counter[" ".join(words[i:i+k])] += 1
    except Exception:
        continue

print(f"# Total human user messages scanned: {total_user_msgs}\n")

print("## Espanso snippet usage (text-detectable)\n")
print(f"{'trigger':12} {'kind':7} {'hits':>6} {'sessions':>9}  last-seen")
order = sorted(SNIPPETS, key=lambda t: (-counts[t], t))
for t in order:
    kind, subs = SNIPPETS[t]
    if subs is None:
        print(f"{t:12} {kind:7} {'   n/a':>6} {'      n/a':>9}  (not text-detectable)")
    else:
        print(f"{t:12} {kind:7} {counts[t]:>6} {len(sessions_hit[t]):>9}  {last_seen[t] or '-'}")

print("\n## Recurring short user messages NOT already snippets (top 40)\n")
STOP_EXACT = {"yes", "ok", "okay", "y", "continue", "go", "proceed", "do it",
              "yes do it", "no", "thanks", "thank you", "good", "nice", "next",
              "stop", "wait", "k", "yep", "yes please", "sure", "perfect"}
for msg, n in short_msgs.most_common(120):
    if n < 4:
        break
    if msg in STOP_EXACT:
        continue
    if len(msg) < 8:
        continue
    print(f"{n:4}  {msg[:110]}")

print("\n## Recurring 4-5 word phrases (top 40, candidate building blocks)\n")
GENERIC = re.compile(r"^(the|a|to|of|in|and|for|is|it|that|this|you|i|we|on|with)\b")
shown = 0
for ph, n in phrase_counter.most_common(400):
    if n < 8:
        break
    # skip phrases that are mostly filler
    if any(e.startswith(ph[:15]) for e in KNOWN_EXPANSIONS):
        continue
    print(f"{n:4}  {ph}")
    shown += 1
    if shown >= 40:
        break
