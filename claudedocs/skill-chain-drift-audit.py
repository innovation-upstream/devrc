#!/usr/bin/env python3
"""Rank 3: do the TOPIC-DRIFT sessions leave the ABANDONED doc claiming in-flight work?

A drift session resumed handoff doc X and recorded its work under a DIFFERENT doc Y.
The 91.3% measurement scored those as RECORDED, and that is right -- the work IS on
disk. The open question this answers is the SECOND-ORDER one: X is now unmaintained
while still advertising open work, which is exactly the staleness `/resume` walks into.

Reads the same three artifacts as `skill-chain-loss-classifier.py` (chain2.out,
docs.idx, allnames.txt) from $CHAIN_WORKDIR, so it shares that script's population and
its corrections (topic-keyed matching, --all commit index, untruncated first-message
fallback).

🔴 The headline is a CONTRASTED rate, never a bare count: the same predicate is run on
the docs that were NOT abandoned. A predicate that fires on every handoff doc alike
measures the corpus, not drift.
"""
import re, os, json, collections, sys, subprocess

S = os.environ.get("CHAIN_WORKDIR", os.getcwd())
STOP = {"the", "a", "an", "and", "of", "to", "for", "work"}
TODAY = "2026-08-30"


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


# ---- commit index: basename -> [dates], and basename -> repo ----------------
commits = collections.defaultdict(list)
doc_repo = {}
date = None
for line in open(f"{S}/docs.idx", encoding="utf-8", errors="replace"):
    p = line.rstrip("\n").split("\t")
    if len(p) >= 4 and p[1] == "C":
        date = p[3]
    elif len(p) == 2 and p[1].startswith("claudedocs/"):
        b = p[1].rsplit("/", 1)[-1]
        commits[b].append(date)
        doc_repo.setdefault(b, p[0])

# ---- population ------------------------------------------------------------
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


# ---- which doc did the session WRITE? ---------------------------------------
# Three routes, unioned, mirroring how the write guard observes satisfaction:
#   1. a `handoff_doc.py ... --topic <t>` command  -> handoff-<slug(t)>.md
#   2. a Write/Edit whose file_path is a handoff doc
#   3. a `handoff_doc.py` run with no readable topic -> recorded, target UNKNOWN
TOPIC_RE = re.compile(r"--topic[= ]+['\"]?([^'\"\s]+)")


_WD_CACHE_PATH = f"{S}/written.cache.json"
try:
    _WD_CACHE = json.load(open(_WD_CACHE_PATH))
except Exception:
    _WD_CACHE = {}


def written_docs(fp):
    """Return (set_of_basenames, ran_handoff_doc_bool). Cached on (path, size)."""
    try:
        key = f"{fp}:{os.path.getsize(fp)}"
    except OSError:
        return None, None
    if key in _WD_CACHE:
        v = _WD_CACHE[key]
        return set(v[0]), v[1]
    out, ran = _written_docs_uncached(fp)
    if out is not None:
        _WD_CACHE[key] = [sorted(out), ran]
    return out, ran


def _written_docs_uncached(fp):
    out, ran = set(), False
    try:
        fh = open(fp, encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    with fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("type") != "assistant":
                continue
            c = (o.get("message") or {}).get("content")
            for b in (c if isinstance(c, list) else []):
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                inp = b.get("input") or {}
                blob = json.dumps(inp)
                if "handoff_doc.py" in blob:
                    ran = True
                    m = TOPIC_RE.search(blob.replace("\\", ""))
                    if m:
                        out.add("handoff-" + "-".join(slug(m.group(1))) + ".md")
                if b.get("name") in ("Write", "Edit", "MultiEdit"):
                    fpth = inp.get("file_path") or ""
                    mm = basere.search(fpth)
                    if mm:
                        out.add(mm.group(1))
    return out, ran


# ---- the predicate: does this doc DECLARE open work? ------------------------
# Scoped to headings, and each block clipped at the next heading -- the extent bug
# this arc hit three times. Case IS the discriminator (this corpus SHOUTS status).
DONE_RE = re.compile(r"^\s*\d+\.\s*(✅|\*\*?DONE|DONE\b|~~)")
RANK_RE = re.compile(r"^\s*(\d+)\.\s+(.*)$")


# One-time repo-basename -> path index. Built once; the per-doc os.walk it replaces
# was O(docs x tree) and did not finish.
REPO_PATH = {}
for _root, _dirs, _ in os.walk(os.path.expanduser("~/workspace")):
    if os.path.exists(os.path.join(_root, ".git")):
        REPO_PATH.setdefault(os.path.basename(_root), _root)
        _dirs[:] = []
    else:
        _dirs[:] = [d for d in _dirs if d not in ("node_modules", "target", ".direnv", ".venv")]

_TEXT_CACHE = {}


def doc_text(basename):
    """Resolve a doc's text: on disk in its repo, else from that repo's mainline.

    🔴 A stale clone makes a file LOOK ABSENT -- these clones run behind, so an
    on-disk miss is checked against the mainline ref before returning None.
    """
    if basename in _TEXT_CACHE:
        return _TEXT_CACHE[basename]
    repo = doc_repo.get(basename)
    root = REPO_PATH.get(repo) if repo else None
    res = (None, root)
    if root:
        p = os.path.join(root, "claudedocs", basename)
        if os.path.exists(p):
            res = (open(p, encoding="utf-8", errors="replace").read(), root)
        else:
            for ref in ("origin/main", "origin/trunk", "origin/master"):
                try:
                    t = subprocess.run(["git", "-C", root, "show", f"{ref}:claudedocs/{basename}"],
                                       capture_output=True, text=True, timeout=20)
                    if t.returncode == 0 and t.stdout:
                        res = (t.stdout, root)
                        break
                except Exception:
                    pass
    _TEXT_CACHE[basename] = res
    return res


def declares_open_work(text):
    """True iff the doc has a ranked next-step item that is NOT marked done,
    or an 'Open investigations' section with at least one sub-block."""
    if not text:
        return None, []
    lines = text.split("\n")
    open_items, in_next, in_openinv, fence = [], False, False, None
    openinv_blocks = 0
    for ln in lines:
        f = re.match(r"^\s*(`{3,}|~{3,})", ln)
        if f:
            d = f.group(1)
            if fence is None:
                fence = d[0] * len(d)
            elif ln.strip() == fence or ln.strip().startswith(fence):
                fence = None
            continue
        if fence:
            continue
        if ln.startswith("#"):
            h = ln.lstrip("#").strip().lower()
            in_next = h.startswith("next step")
            in_openinv = h.startswith("open investigation")
            continue
        if in_next:
            m = RANK_RE.match(ln)
            if m and not DONE_RE.match(ln):
                open_items.append(m.group(1) + ". " + m.group(2)[:70])
        if in_openinv and ln.startswith("### "):
            if not ln.lstrip("# ").startswith(("✅", "CLOSED")):
                openinv_blocks += 1
    return (bool(open_items) or openinv_blocks > 0), open_items


# ---- INSTRUMENT CONTROLS ----------------------------------------------------
# 🔴 A predicate never watched go BOTH ways is a claim about the predicate. Run with
# DRIFT_CONTROLS=1. Every case is built from a REAL doc in this corpus, not a fixture,
# because a synthetic case is exactly what a corpus-tuned matcher passes vacuously.
if os.environ.get("DRIFT_CONTROLS"):
    def show(label, name, expect):
        t, _ = doc_text(name)
        if t is None:
            print(f"  {label:11} {name[:46]:46} UNREADABLE  (control void)")
            return
        got, items = declares_open_work(t)
        ok = "OK " if got is expect else "\U0001f534 FAIL"
        print(f"  {ok} {label:11} {name[:46]:46} -> {got}  (expect {expect})"
              f"  items={len(items)}")

    print("INSTRUMENT CONTROLS for declares_open_work()")
    # POSITIVE: resolved from the corpus, not pinned by name -- a named pin rots into a
    # stale assertion the moment that doc's items get closed.
    # 🔴 The obvious pin (this arc's own doc) is VOID here: it lives only on an unmerged
    # branch, so doc_text cannot read it and the control reported UNREADABLE.
    pos = None
    for _b in sorted(allnames):
        _t, _ = doc_text(_b)
        if not _t:
            continue
        _d, _it = declares_open_work(_t)
        if _d and len(_it) >= 3:
            pos = (_b, _it)
            break
    if pos:
        show("positive", pos[0], True)
        for _line in pos[1][:3]:
            print(f"                 booked: {_line[:70]}")
    else:
        print("  \U0001f534 NO POSITIVE CASE FOUND — void.")
    # NEGATIVE: a doc whose every ranked item carries a DONE/✅ marker. Resolved by
    # scanning the corpus rather than named, so this cannot rot into a stale pin.
    neg = None
    for _b in sorted(allnames):
        _t, _ = doc_text(_b)
        if not _t or "Next steps" not in _t:
            continue
        _d, _it = declares_open_work(_t)
        if _d is False and re.search(r"^\s*\d+\.\s*(✅|\*\*?DONE)", _t, re.M):
            neg = _b
            break
    if neg:
        show("negative", neg, False)
    else:
        print("  \U0001f534 NO NEGATIVE CASE FOUND — the predicate has never been "
              "watched return False on a doc that HAS a Next-steps section. Void.")
    # 🔴 SENSITIVITY: a predicate that returns True and False on two different docs has
    # only been shown to vary WITH THE DOC. This shows it varies with the THING —
    # take the negative doc and inject one un-done ranked item; it must flip.
    _t = doc_text(neg)[0] if neg else None
    if _t:
        before, _ = declares_open_work(_t)
        mutated = _t + "\n\n## Next steps (ranked)\n\n1. **An item nobody has done.**\n"
        after_, items_ = declares_open_work(mutated)
        ok = "OK " if (before is False and after_ is True) else "\U0001f534 FAIL"
        print(f"  {ok} sensitivity  inject one un-done ranked item into {neg[:32]:32} "
              f"-> {before} => {after_}  (booked {len(items_)})")
    # Positive control on the WRITE detector: it must fire on a transcript we know
    # ran /handoff, or every zero below is a dead probe rather than an absence.
    fired = sum(1 for r in chain[:120]
                if r.get("file") and os.path.exists(r["file"])
                and (written_docs(r["file"])[1] or False))
    print(f"  {'OK ' if fired else '\U0001f534 FAIL'} write-detector fired on {fired}"
          f"/120 sampled transcripts (0 would mean a probe wired to nothing)")
    sys.exit(0)

# ---- resolve every session -------------------------------------------------
drift, aligned, unresolved, unreadable, norecord = [], [], 0, 0, 0
for _i, r in enumerate(chain):
    if _i % 25 == 0:
        print(f"  ... scanning transcript {_i}/{len(chain)}", file=sys.stderr, flush=True)
    b = match(r["topic"]) if r["topic"] else None
    if b is None:
        t = first_user_text(r["file"]) if r["file"] and os.path.exists(r["file"]) else None
        m = basere.search(t) if t else None
        b = m.group(1) if m else None
    if b is None:
        unresolved += 1
        continue
    r["doc"] = b
    if not r["file"] or not os.path.exists(r["file"]):
        unreadable += 1
        continue
    wrote, ran = written_docs(r["file"])
    if wrote is None:
        unreadable += 1
        continue
    r["wrote"], r["ran"] = wrote, ran
    if b in wrote:
        aligned.append(r)
    elif wrote:
        r["target"] = sorted(wrote)
        drift.append(r)
    elif ran:
        r["target"] = ["<topic unreadable>"]
        drift.append(r)
    else:
        norecord += 1

try:
    json.dump(_WD_CACHE, open(_WD_CACHE_PATH, "w"))
except Exception:
    pass

print(f"POPULATION  chain-genesis={len(chain)}  resolved-doc={len(chain)-unresolved}"
      f"  unresolved={unresolved}  unreadable={unreadable}")
print(f"            aligned(wrote the doc it resumed)={len(aligned)}"
      f"  DRIFT(wrote a different doc)={len(drift)}  no-record={norecord}")

# ---- the audit: abandoned docs ---------------------------------------------
ab = collections.defaultdict(list)
for r in drift:
    ab[r["doc"]].append(r)

print(f"\nDISTINCT ABANDONED DOCS: {len(ab)}\n")
rowsout = []
for b, rs in sorted(ab.items()):
    last_sess = max(x["date"] for x in rs)
    ds = [d for d in commits.get(b, []) if d]
    # 🔴 `>` here was WRONG and it inflated the headline. `docs.idx` carries DAY
    # granularity (`--date=short`), so a commit landing hours after the drift session,
    # on the same calendar day, scored as "never updated since". Every same-day case
    # -- which is most of them -- read as abandoned. `>=` is the conservative
    # direction: it can only REMOVE docs from the finding, never add one. The drift
    # session itself provably did not write this doc (that is what made it drift), so
    # a same-day commit to it came from somewhere else and the doc is not orphaned.
    after = [d for d in ds if d >= last_sess]
    text, repo = doc_text(b)
    dec, items = declares_open_work(text)
    rowsout.append(dict(doc=b, n=len(rs), last_sess=last_sess,
                        last_commit=(max(ds) if ds else None),
                        committed_after=len(after), repo=os.path.basename(repo or "?"),
                        readable=text is not None, declares=dec, items=items,
                        targets=sorted({t for x in rs for t in x["target"]})))

stale_and_open = [x for x in rowsout if x["committed_after"] == 0 and x["declares"]]
print(f"{'doc':52} {'n':>2} {'lastsess':10} {'lastcommit':10} {'after':>5} {'open?':>6}")
for x in sorted(rowsout, key=lambda z: (z["committed_after"] != 0, z["doc"])):
    print(f"{x['doc'][:52]:52} {x['n']:>2} {x['last_sess']:10} "
          f"{str(x['last_commit']):10} {x['committed_after']:>5} {str(x['declares']):>6}")

# ---- the SHARPER discriminator: does the successor point BACK? --------------
# 🔴 "X still declares open work" is the corpus norm (see the control below), so on its
# own it measures handoff docs, not drift. The harm specific to drift is that a reader
# arriving at X gets no pointer to Y -- X reads as the live front of that work forever.
# This is checkable: does Y's text name X?
def links_back(y_basename, x_basename):
    t, _ = doc_text(y_basename)
    if t is None:
        return None
    if x_basename in t:
        return True
    stem = x_basename[len("handoff-"):-3] if x_basename.startswith("handoff-") else x_basename[:-3]
    stem = re.sub(r"-20\d\d-\d\d-\d\d$", "", stem)
    return bool(stem) and stem in t


for x in rowsout:
    verdicts = [links_back(y, x["doc"]) for y in x["targets"] if y != "<topic unreadable>"]
    real = [v for v in verdicts if v is not None]
    x["linked"] = (any(real) if real else None)

orphaned = [x for x in rowsout if x["committed_after"] == 0 and x["declares"] and x["linked"] is False]

print(f"\n\U0001f534 ABANDONED **and** STILL DECLARING OPEN WORK: "
      f"{len(stale_and_open)} / {len(rowsout)} distinct docs")
print(f"\U0001f534 OF THOSE, the successor doc NAMES NEITHER the abandoned doc nor its topic "
      f"(a reader of X has no route to Y): {len(orphaned)}")
for x in orphaned:
    print(f"  - {x['doc']}  ->  {', '.join(x['targets'])[:90]}")
for x in stale_and_open:
    print(f"  - {x['doc']}  ({x['repo']}, last commit {x['last_commit']}, "
          f"{len(x['items'])} un-done ranked item(s))")
    for it in x["items"][:4]:
        print(f"      {it}")
    print(f"      drifted to: {', '.join(x['targets'])[:110]}")

# ---- CONTROL: the same predicate on docs that were NOT abandoned ------------
al = collections.defaultdict(list)
for r in aligned:
    al[r["doc"]].append(r)
ctl = []
for b, rs in sorted(al.items()):
    if b in ab:
        continue
    text, _ = doc_text(b)
    dec, _ = declares_open_work(text)
    if dec is not None:
        ctl.append(dec)
print(f"\nCONTROL — the SAME predicate on {len(ctl)} maintained (non-drift) resumed docs: "
      f"{sum(1 for c in ctl if c)} declare open work "
      f"({100*sum(1 for c in ctl if c)/len(ctl):.1f}%)" if ctl else "\nCONTROL: none")
dr = [x['declares'] for x in rowsout if x['declares'] is not None]
print(f"        drift-abandoned docs: {sum(1 for c in dr if c)}/{len(dr)} "
      f"({100*sum(1 for c in dr if c)/len(dr):.1f}%) declare open work")
print("        \U0001f534 The predicate does NOT discriminate on its own — nearly every handoff")
print("           doc declares open work. The DISCRIMINATOR is 'declares open work AND has")
print("           received no commit since the session that walked away from it'.")
