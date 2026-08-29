#!/usr/bin/env python3
"""Rank 1: split the never-run handoff losses by SESSION END-STATE.

Pipeline (all re-derived this session; no cached artifact from the prior session is used):
  chain2.out   -- find-session population, regenerated
  docs.idx     -- handoff-doc commit index, git log --all over 154 repos, regenerated
  allnames.txt -- every handoff doc basename, any branch, any repo

A "loss" = a /resume-genesis session whose resumed doc got NO commit on or after the
session date.  A "never-run" loss = a loss whose transcript contains no handoff_doc.py
tool_use anywhere.  Those are the ones this script classifies.
"""
import re, os, json, collections, sys, time

# Working directory holding chain2.out / docs.idx / allnames.txt. Override with
# CHAIN_WORKDIR; defaults to the cwd so the two scripts can be run from any scratch dir.
S = os.environ.get("CHAIN_WORKDIR", os.getcwd())
STOP = {"the", "a", "an", "and", "of", "to", "for", "work"}
NOW = time.time()


def slug(s):
    s = s.replace("→", " ").replace("—", " ")
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t and t not in STOP]


allnames = set()
for line in open(f"{S}/allnames.txt", encoding="utf-8", errors="replace"):
    p = line.strip()
    if p:
        allnames.add(p.rsplit("/", 1)[-1])
alltok = {b: set(slug(b.replace("handoff", "").replace(".md", ""))) for b in allnames}


def match(topic):
    ts = set(slug(topic))
    if not ts:
        return None
    ex = "handoff-" + "-".join(slug(topic)) + ".md"
    if ex in allnames:
        return ex
    best, sc = None, 0.0
    for b, bt in alltok.items():
        if not bt:
            continue
        j = len(ts & bt) / len(ts | bt)
        if j > sc:
            best, sc = b, j
    return best if sc >= 0.6 else None


commits = collections.defaultdict(list)
date = None
for line in open(f"{S}/docs.idx", encoding="utf-8", errors="replace"):
    p = line.rstrip("\n").split("\t")
    if len(p) >= 4 and p[1] == "C":
        date = p[3]
    elif len(p) == 2 and p[1].startswith("claudedocs/"):
        commits[p[1].rsplit("/", 1)[-1]].append(date)

# ---- parse the find-session population -------------------------------------
rows, cur = [], None
hdr = re.compile(r"^\s*\d+\.\s+\[(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\]\s+(\S+)(.*)$")
topre = re.compile(r"/resume\s*[—-]\s*continue the (.+?)\s+work[.,]", re.I)
for line in open(f"{S}/chain2.out", encoding="utf-8", errors="replace"):
    m = hdr.match(line)
    if m:
        cur = {"date": m.group(1), "project": m.group(2), "tail": m.group(3),
               "genesis": None, "file": None}
        rows.append(cur)
        continue
    if not cur:
        continue
    if "opened:" in line and cur["genesis"] is None:
        cur["genesis"] = line.split("opened:", 1)[1].strip().lstrip("'")
    if line.strip().startswith("file:") and cur["file"] is None:
        cur["file"] = line.strip().split(None, 1)[1]

chain = [r for r in rows if r["genesis"] and r["genesis"].startswith("/resume")]
for r in chain:
    m = topre.search(r["genesis"])
    r["topic"] = m.group(1).strip() if m else None
    r["remote"] = "[claude-remote]" in (r["tail"] or "") or "[opencode-remote]" in (r["tail"] or "")

basere = re.compile(r"\b(handoff-[\w.-]+\.md|[\w-]*HANDOFF[\w-]*\.md)\b")


def first_user_text(fp):
    try:
        with open(fp, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 400:
                    break
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") != "user":
                    continue
                c = (o.get("message") or {}).get("content")
                if isinstance(c, str) and c.strip():
                    return c
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                            return b["text"]
    except OSError:
        return None
    return None


# ---- resolve each session's resumed doc, then find the losses ---------------
losers, unresolved, missing_file = [], 0, 0
for r in chain:
    b = match(r["topic"]) if r["topic"] else None
    if b is None:
        t = first_user_text(r["file"]) if r["file"] and os.path.exists(r["file"]) else None
        m = basere.search(t) if t else None
        b = m.group(1) if m else None
    if b is None:
        unresolved += 1
        continue
    if not [d for d in commits.get(b, []) if d and d >= r["date"]]:
        losers.append(dict(r, doc=b))

# ---- did the session ever invoke handoff_doc.py? ----------------------------
TAIL = 25  # "at the end" = the final TAIL conversational rows


def scan(fp):
    """One pass. Returns end-state signals for a transcript.

    🔴 An interrupt ANYWHERE is not an interrupted END — a session can be interrupted at
    turn 40, carry on for 500 more and finish cleanly. Two of the first run's three
    'interrupted' rows were exactly that. So interrupts are recorded with their row
    index and only those inside the final TAIL rows count toward the end-state.
    """
    sig = dict(handoff_cmd=False, compact=False, int_any=False, int_at_end=False,
               last_type=None, last_stop=None, peak_in=0, assistants=0,
               stop_hook=0, open_tool=False, rows=0, conv_rows=0, model=None,
               ceiling=0, last_ts=None)
    pending = set()
    int_rows, conv_idx = [], 0
    try:
        fh = open(fp, encoding="utf-8", errors="replace")
    except OSError:
        return None

    def note_interrupt(txt):
        if txt and "Request interrupted" in txt:
            sig["int_any"] = True
            int_rows.append(conv_idx)

    with fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            sig["rows"] += 1
            t = o.get("type")
            if o.get("timestamp"):
                sig["last_ts"] = o["timestamp"]
            if o.get("isCompactSummary") or o.get("compactMetadata"):
                sig["compact"] = True
            if t == "system":
                st = o.get("subtype")
                if st == "compact_boundary":
                    sig["compact"] = True
                elif st == "stop_hook_summary":
                    sig["stop_hook"] += 1
            if t not in ("assistant", "user"):
                continue
            conv_idx += 1
            msg = o.get("message") or {}
            if t == "assistant":
                sig["assistants"] += 1
                sig["last_type"] = "assistant"
                sig["last_stop"] = msg.get("stop_reason")
                if msg.get("model"):
                    sig["model"] = msg["model"]
                u = msg.get("usage") or {}
                tot = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) \
                      + (u.get("cache_creation_input_tokens") or 0)
                sig["peak_in"] = max(sig["peak_in"], tot)
            else:
                sig["last_type"] = "user"
            c = msg.get("content")
            for b in (c if isinstance(c, list) else []):
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    pending.add(b.get("id"))
                    if "handoff_doc.py" in json.dumps(b.get("input") or {}):
                        sig["handoff_cmd"] = True
                elif b.get("type") == "tool_result":
                    pending.discard(b.get("tool_use_id"))
                    cc = b.get("content")
                    note_interrupt(cc if isinstance(cc, str) else json.dumps(cc))
                elif b.get("type") == "text":
                    note_interrupt(b.get("text"))
            if isinstance(c, str):
                note_interrupt(c)
    sig["conv_rows"] = conv_idx
    sig["open_tool"] = bool(pending)
    sig["int_at_end"] = any(i > conv_idx - TAIL for i in int_rows)
    # 🔴 The model STRING does not carry the context tier here: every session in this set
    # reports `claude-opus-5`, yet peaks reach 965,819 tokens. A 200k ceiling is therefore
    # empirically REFUTED for those sessions. v2 of this script assumed 200k and produced
    # ratios up to 4.83 -- impossible values that would have scored 11 of 16 as exhausted.
    # Infer the tier from evidence instead, and mark it AMBIGUOUS where evidence is absent.
    pk = sig["peak_in"]
    if pk > 200_000:
        sig["ceiling"], sig["tier"] = 1_000_000, "observed>200k"
    elif pk == 0:
        sig["ceiling"], sig["tier"] = 0, "no-turns"
    else:
        sig["ceiling"], sig["tier"] = 0, "AMBIGUOUS(200k or 1M)"
    return sig


never, toolfail, other, unreadable = [], [], [], []
for r in losers:
    fp = r["file"]
    if not fp or not os.path.exists(fp):
        unreadable.append(r)
        continue
    sig = scan(fp)
    if sig is None:
        unreadable.append(r)
        continue
    r["sig"] = sig
    if not sig["handoff_cmd"]:
        never.append(r)
    else:
        other.append(r)

# ---- classify the never-run set --------------------------------------------
FRESH = 6 * 3600   # a session touched this recently may simply still be running
NEAR = 0.90        # within 10% of the model's context ceiling


def bucket(r):
    s = r["sig"]
    age = NOW - os.path.getmtime(r["file"])
    ratio = (s["peak_in"] / s["ceiling"]) if s["ceiling"] else 0.0
    r["ratio"] = ratio
    # a session that never produced an assistant turn never started at all
    if s["assistants"] == 0:
        return "0 never-started"
    if s["compact"] or ratio >= NEAR:
        return "A context-exhausted"
    if s["int_at_end"] or s["open_tool"]:
        return "B interrupted-at-end"
    if age < FRESH:
        return "C still-live"
    if s["last_type"] == "assistant" and s["last_stop"] in ("end_turn", "stop_sequence"):
        return "D cleanly-ended"
    # last row is a human turn the agent never answered: abandoned mid-exchange
    if s["last_type"] == "user":
        return "B interrupted-at-end"
    return "E unclassified"


buckets = collections.defaultdict(list)
for r in never:
    buckets[bucket(r)].append(r)

print("=" * 72)
print("POPULATION")
print(f"  find-session rows                  : {len(rows)}")
print(f"  /resume-genesis sessions           : {len(chain)}")
print(f"  of those, remote-host sessions     : {sum(1 for r in chain if r['remote'])}")
print(f"  resumed doc UNRESOLVED (excluded)  : {unresolved}")
print(f"  graded                             : {len(chain) - unresolved}")
print(f"  LOSSES (doc got no commit >= date) : {len(losers)}")
print(f"    transcript unreadable on this host: {len(unreadable)}")
print(f"    ran handoff_doc.py (other causes): {len(other)}")
print(f"    NEVER ran handoff_doc.py         : {len(never)}   <-- classified below")
print()
print("END-STATE OF THE NEVER-RUN LOSSES")
tot = len(never) or 1
for k in sorted(buckets):
    v = buckets[k]
    print(f"  {k:22s} {len(v):3d}  ({100*len(v)/tot:.1f}%)")
print()
print("  collapsed to the doc's binary (0 never-started and C still-live are NOT losses):")
ex = len(buckets["A context-exhausted"])
cl = len(buckets["D cleanly-ended"])
oth = len(buckets["B interrupted-at-end"]) + len(buckets["E unclassified"])
print(f"    context-exhausted : {ex}")
print(f"    cleanly-ended     : {cl}")
print(f"    neither (B+E)     : {oth}")
print()
print("PER-SESSION DETAIL   (ratio = peak input tokens / this session's model ceiling)")
print(f"  {'bucket':<22} {'date':<11} {'peak_in':>10} {'ratio':>6} {'turns':>6} {'stopHk':>7}  doc")
for k in sorted(buckets):
    for r in sorted(buckets[k], key=lambda x: x["date"]):
        s = r["sig"]
        print(f"  {k:<22} {r['date']:<11} {s['peak_in']:>10,} {r.get('ratio',0):>6.2f} "
              f"{s['assistants']:>6} {s['stop_hook']:>7}  {r['doc'][:42]}")

print()
print("INSTRUMENT CONTROLS  (a detector that has never been watched fire proves nothing)")
print(f"  handoff_doc.py detector POSITIVE control: fired on {len(other)} of "
      f"{len(other)+len(never)} loser transcripts -> it is wired to something, so the "
      f"{len(never)} zeros are absences, not a dead probe.")
lateint = sum(1 for r in never if r["sig"]["int_any"] and not r["sig"]["int_at_end"])
print(f"  interrupt NEGATIVE control: {lateint} session(s) carry an interrupt EARLY and "
      f"still ended cleanly -> correctly NOT bucketed B. (v1 of this script counted them "
      f"as interrupted; that was a spelled guard, not a structural one.)")
models = collections.Counter(r["sig"]["model"] for r in never)
print(f"  model string as recorded: {dict(models)}  <-- carries NO context tier")
tiers = collections.Counter(r["sig"]["tier"] for r in never)
print(f"  ceiling inferred from evidence: {dict(tiers)}")
print(f"  compaction-marker detector fired on: "
      f"{sum(1 for r in never if r['sig']['compact'])} of {len(never)}")
with open(f"{S}/never.json", "w") as fh:
    json.dump([{k: v for k, v in r.items() if k != "sig"} | {"sig": r["sig"], "bucket": bucket(r)}
               for r in never], fh, indent=1)
with open(f"{S}/losers.json", "w") as fh:
    json.dump([{k: v for k, v in r.items() if k != "sig"} for r in losers], fh, indent=1)

print()
print("STOP-HOOK FEASIBILITY (the measurement rank 2 turns on)")
fired = sum(1 for r in never if r["sig"]["stop_hook"] > 0)
print(f"  never-run losses whose transcript shows a Stop hook fired: {fired} / {len(never)}")
for k in sorted(buckets):
    f = sum(1 for r in buckets[k] if r["sig"]["stop_hook"] > 0)
    print(f"    {k:<22} {f}/{len(buckets[k])}")
