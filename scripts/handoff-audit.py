#!/usr/bin/env python3
"""Deterministic audit of claudedocs/handoff-*.md bodies — the /handoff analogue
of skill-audit.py.

WHY: a SKILL.md costs 0 tokens until its trigger fires. A HANDOFF doc is worse —
`/resume` reads it in full, first thing, every time, so it is the one
`claudedocs/` file that is *not* demand-loaded. And nothing removes a byte from
it: `/handoff` mandates append-verbatim for Gotchas and Open investigations, and
the stable-rank rule makes a completed item stay in place. Both rules are
correct; there is simply no counterweight.

MEASURED 2026-08-29 over 18 docs picked by `find-session --skill handoff` (i.e.
docs a recent session actually wrote), every revision of each:

    123 revisions — 121 grew or held, 2 shrank. 16 of 18 never shrank once.
    First revision -> latest: 3,033 -> 7,748 lines, x2.55.

One doc dated 2026-08-12 was still being appended to on 08-27, +673 lines after
its own topic date. Growth continues long after the work ships, which is exactly
the part that cannot still be earning its place.

This tool is PURE MEASUREMENT and makes no edits. It exists so the eviction
decision is taken against numbers instead of a feeling, and so the question "is a
byte-cap gate worth building?" can be answered from the corpus distribution
rather than from the five docs a human happened to read.

Usage:
  handoff-audit.py [PATH ...]   PATH = a handoff doc, a claudedocs dir, or a repo
                                root; with no arg, $PWD/claudedocs
  --all            list every doc, not just over-budget ones
  --detail N       docs to render a detail block for (default 3)
  --sections N     sections shown per detail block (default 8)
  --csv            emit one row per doc instead of the report (for distributions)
"""
import argparse
import importlib.machinery
import importlib.util
import os
import re
import sys
from pathlib import Path

# --- borrow the parser, do not re-implement it ---------------------------------
# 🔴 skill-audit.py's fence walk is the single most load-bearing thing here and it
# was got WRONG twice before it was got right (a ``` parity count called two
# well-formed files unclosed and inflated a dated-history share from 43% to 75%).
# Copying it would fork that fix. `_headings` and `sections` come from there, so a
# correction to the fence semantics lands in both tools at once.
_SIBLING = Path(__file__).resolve().parent / "skill-audit.py"


def _load_sibling(path=_SIBLING):
    if not path.is_file():
        raise SystemExit(
            f"handoff-audit: cannot import the shared parser — {path} is missing.\n"
            "This tool deliberately keeps no copy of the fence/heading walk (a "
            "second implementation is how the CommonMark bug survived two "
            "readers), so it cannot run without it."
        )
    loader = importlib.machinery.SourceFileLoader("_skill_audit", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


SA = _load_sibling()

# 🔴 BORROWED, NOT RE-DECLARED, AND NOT ENFORCED ANYWHERE.
# skill-audit reads these from the gate that owns them. A handoff doc has NO gate:
# nothing in this repo measures one, so these are a REFERENCE the reader may argue
# with, never a verdict. The target is defensible rather than arbitrary — the
# corpus p50 measured 12,159 B on 2026-08-29, i.e. half the corpus already meets
# it — but every line this tool prints must keep saying it binds nothing.
TARGET = SA.TARGET
HARD = SA.HARD
FAT_LINE = SA.FAT_LINE

# Evicting a completed rank must LEAVE THE NUMBER: the rank is half a `claim-work`
# slug identity (`claim-work --slug-for <doc> <rank>`), so deleting the line
# silently re-points every live claim on the queue. The remedy is a resume line
# plus a pointer, which is not free — so the projected saving is charged for it.
# This is the one adaptation prune-skill does not have to make.
RESUME_COST = 200

# --- section families ----------------------------------------------------------
# Measured over all 413 docs: `Next steps (ranked)` 246, `Gotchas / decisions /
# dead-ends` 237, `State now` 231, `Open investigations …` 198. The families below
# are deliberately loose on the tail of the heading and anchored on its head.
NEXT_STEPS = re.compile(r"^next steps\b|^do next\b|^what'?s next\b", re.I)
INVESTIGATIONS = re.compile(r"^open investigations\b|^live diagnosis\b", re.I)
GOTCHAS = re.compile(r"^gotchas\b|dead-ends?\b|^decisions\b", re.I)

# A DONE marker on a ranked item. Measured shapes in the first 60 chars of the
# 2,804 numbered items in the corpus: ✅ 163, DONE 136, **DONE 115, ~~ 106,
# CLOSED 27, MERGED 14, SHIPPED 10. `~~` is markdown strikethrough, which is how
# several docs retire an item without deleting it.
DONE_MARK = re.compile(r"✅|~~|\bDONE\b|\bSHIPPED\b|\bMERGED\b|\bCLOSED\b|\bLANDED\b")
# 🔴 Bounded to the item's OWN FIRST PHYSICAL LINE. Unbounded, an item whose BODY
# merely mentions that some other PR merged is scored as complete — and the body is
# where that word almost always appears, so the unbounded version marks nearly
# everything done. It is the same trap as skill-audit's WORK_STATUS/DATED_LESSON
# split: the strong-looking signal is in the wrong place.
#
# 🔴 A CHARACTER BUDGET IS NOT THAT BOUND, and this shipped as `DONE_SCAN = 160`
# before test_done_marker_is_bounded_to_the_item_head caught it. A ranked item's
# first line is ~60-80 chars, so 160 reaches two lines into the body and picks up
# exactly the mention the bound exists to exclude. Measured on the fixture: item 1
# is open and was scored done. Use the LINE, which is the structure the convention
# actually follows — every one of the corpus's 193 markers sits on it.
DONE_FIRST_LINE_ONLY = True

# A resolved investigation block, judged on its HEADING only, same reason.
RESOLVED_HEAD = re.compile(r"✅|\bCLOSED\b|\bRESOLVED\b|\bANSWERED\b|~~")

# A bullet recording something that was retracted or refuted. This is history by
# construction: the value is the correction, and the correction is one line.
RETRACTED = re.compile(
    r"\bRETRACTED\b|\bREFUTED\b|\bREJECTED\b|\bWITHDRAWN\b"
    r"|\bdead[- ]end\b|\bdo not re-?derive\b",
    re.I,
)

# 🔴 ADVISORY ONLY — NEVER ADDED TO AN EVICTION TOTAL. A gotcha that is a generic
# tooling lesson belongs in RULES.md or the owning skill, not in a per-initiative
# handoff. But deciding that requires READING the bullet: the 2026-08-29 audit's
# regex said 28-30 of one doc's 62 bullets and a human read said ~55, so the
# matcher's floor and the truth differed by ~2x IN THE SAME DIRECTION on the one
# doc anybody checked. Reported as a count to go read, with no byte estimate
# attached, because a byte estimate would get quoted.
GENERIC_LESSON = re.compile(
    r"\bzsh\b|\bbash\b|\bgit (?:stash|worktree|log|merge|rev-parse)\b"
    r"|\bgrep\b|\bpgrep\b|\bawk\b|\bmutation[- ]test|\baudit ladder\b"
    r"|\bexit code\b|\bpositive control\b|\bnegative control\b",
    re.I,
)

BULLET = re.compile(r"^\s{0,3}[-*+] ")
NUMBERED = re.compile(r"^(\d+)\. ")

# 🔴 DO NOT REUSE skill-audit's WORK_STATUS_HEADING HERE. It was validated on
# SKILL.md bodies and it is WRONG on this corpus, in the most expensive direction:
# it keys on a bare `\bsessions?\b`, and a handoff doc's H1 is
# `# Handoff: session-makework-audit — 2026-08-26`. The topic SLUG contains
# "session", the H1's extent is the whole document, and the block was therefore
# scored as evictable history in full. Measured 2026-08-29 before this fix: the
# bucket read 1,026,330 B over 343 blocks, its top entries being entire documents
# (60,122 B of a 60,122 B file; `find-session-live-first` matched for the same
# reason). It also swept up `Kickoff message for next session` and `Quick state
# checks (next session)`, which are live instructions, not history.
#
# Two structural corrections, both required:
#   - level >= 2 only. An H1 is the doc's title and is never an evictable block.
#   - a COMPLETED-WORK phrasing, not the word "session". Forward-looking headings
#     are excluded explicitly rather than left to the absence of a match.
WORK_STATUS = re.compile(
    r"^what shipped\b"
    r"|\b(?:shipped|merged|done|landed|completed)\s+this\s+session\b"
    r"|^what this session\b"
    r"|^session\s+\d{4}-\d{2}-\d{2}"
    r"|^changelog\b|^work\s*log\b|^release notes\b"
    r"|\bhistory\b",
    re.I,
)
# Checked FIRST. A heading that plans the next session is the live half of the
# doc; matching it would evict the instructions the reader came for.
FORWARD_LOOKING = re.compile(
    r"\bkickoff\b|\bnext session\b|\bfirst action\b|\bstate checks\b|\bpaste to resume\b",
    re.I,
)


def work_status_blocks(lines, heads):
    """Completed-work blocks at H2 or deeper, outermost-first.

    Mirrors skill-audit.dated_blocks' shape (a nested block is already inside its
    parent) but with this corpus's matcher and an H1 exclusion.
    """
    out, covered = [], -1
    for i, lv, title in heads:
        if lv < 2 or i < covered:
            continue
        if FORWARD_LOOKING.search(title) or not WORK_STATUS.search(title):
            continue
        end = SA._extent(heads, i, lv, len(lines))
        out.append((title, i, end, SA._bytes(lines[i:end])))
        covered = end
    return out


def _bucket(lines, lo, hi):
    return SA._bytes(lines[lo:hi])


def _blocks_by_bullet(lines, lo, hi, pred):
    """(start, end, bytes) for each top-level bullet in [lo,hi) matching `pred`.

    A bullet's extent runs to the next top-level bullet, so a continuation line
    and any nested sub-bullet travel with it.
    """
    starts = [i for i in range(lo, hi) if BULLET.match(lines[i])]
    out = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else hi
        if pred("".join(lines[s:e])):
            out.append((s, e, _bucket(lines, s, e)))
    return out


def _ranked_items(lines, lo, hi):
    """(number, start, end, bytes, done) per top-level numbered item in [lo,hi).

    Only a strictly-ascending run counts, mirroring skill-audit._corpus_items: a
    nested "1./2." list inside an item restarts, and treating that restart as a
    new rank both inflates the count and mis-attributes the bytes.
    """
    starts, last = [], 0
    for i in range(lo, hi):
        m = NUMBERED.match(lines[i])
        if m and int(m.group(1)) > last:
            last = int(m.group(1))
            starts.append((last, i))
    out = []
    for k, (n, s) in enumerate(starts):
        e = starts[k + 1][1] if k + 1 < len(starts) else hi
        head = lines[s] if DONE_FIRST_LINE_ONLY else "".join(lines[s:e])
        out.append((n, s, e, _bucket(lines, s, e), bool(DONE_MARK.search(head))))
    return out


def status_for(size):
    if size <= TARGET:
        return "OK"
    if size <= HARD:
        return "OVER TARGET"
    return "OVER HARD CAP"


def audit_one(doc):
    text = doc.read_text(errors="replace")
    lines = text.splitlines(keepends=True)
    heads = SA._headings(lines)
    size = len(text.encode())
    h2 = SA.sections(lines, heads, 2)

    ranked, resolved, retracted, generic = [], [], [], 0
    for title, s, e, _b in h2:
        if NEXT_STEPS.search(title):
            ranked += _ranked_items(lines, s, e)
        if INVESTIGATIONS.search(title):
            for t3, s3, e3, b3 in SA.sections(lines, heads, 3, s, e):
                if RESOLVED_HEAD.search(t3):
                    resolved.append((t3, s3, e3, b3))
        if GOTCHAS.search(title):
            retracted += _blocks_by_bullet(lines, s, e, lambda t: RETRACTED.search(t))
            generic += len(_blocks_by_bullet(lines, s, e, lambda t: GENERIC_LESSON.search(t)))

    # Work-status headings ("## Session 2026-08-28", "### Shipped this session") —
    # skill-audit's EVICT_HISTORY bucket, on THIS corpus's matcher. See the comment
    # on WORK_STATUS for why its matcher cannot be borrowed.
    dated = work_status_blocks(lines, heads)

    done = [r for r in ranked if r[4]]
    done_b = sum(r[3] for r in done)
    resolved_b = sum(b for *_, b in resolved)
    retracted_b = sum(b for *_, b in retracted)
    dated_b = sum(b for *_, b in dated)
    gross = done_b + resolved_b + retracted_b + dated_b
    # Charged only for the ranked items, which are the ones whose number must
    # survive. A resolved investigation or a retracted bullet leaves nothing.
    net = max(0, gross - RESUME_COST * len(done))
    return {
        "path": doc,
        "name": doc.name,
        "size": size,
        "lines": len(lines),
        "status": status_for(size),
        "h2": h2,
        "ranked": ranked,
        "done": done,
        "done_b": done_b,
        "resolved": resolved,
        "resolved_b": resolved_b,
        "retracted": retracted,
        "retracted_b": retracted_b,
        "dated": dated,
        "dated_b": dated_b,
        "gross": gross,
        "net": net,
        "generic": generic,
        "fat": SA.fat_lines(lines),
        "fence_ok": SA.fence_balanced(lines),
    }


def resolve_targets(args):
    out = []
    for a in args:
        p = Path(os.path.expanduser(a))
        if p.is_file():
            out.append(p.resolve())
            continue
        if not p.is_dir():
            print(f"handoff-audit: no such path: {p}", file=sys.stderr)
            continue
        for base in (p, p / "claudedocs"):
            if base.is_dir():
                out += [f.resolve() for f in sorted(base.glob("handoff-*.md"))]
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _pct(a, b):
    return (100.0 * a / b) if b else 0.0


def render(audits, show_all, n_detail, n_sections, out=sys.stdout):
    p = lambda *a: print(*a, file=out)
    audits = sorted(audits, key=lambda a: -a["size"])
    over = [a for a in audits if a["status"] != "OK"]
    total = sum(a["size"] for a in audits)
    p(f"# handoff-doc audit — {len(audits)} doc(s)")
    p(f"\ntarget {TARGET:,} B   ·   hard cap {HARD:,} B")
    p("  🔴 NOT ENFORCED. No gate in this repo measures a handoff doc, so every")
    p("     verdict below is a REFERENCE you may argue with, not a rejection. The")
    p("     numbers are borrowed from the SKILL.md gate via skill-audit.py rather")
    p("     than re-declared here, so there is only ever one copy of them.")

    sizes = sorted(a["size"] for a in audits)
    if sizes:
        med = sizes[len(sizes) // 2]
        p90 = sizes[min(len(sizes) - 1, int(0.9 * len(sizes)))]
        p(f"\n## corpus  {total:,} B  (~{total // 4:,} tokens)")
        p(f"  min {sizes[0]:,} · p50 {med:,} · p90 {p90:,} · max {sizes[-1]:,}")
        p(f"  over target: {sum(1 for s in sizes if s > TARGET)} of {len(sizes)}"
          f"   ·   over hard cap: {sum(1 for s in sizes if s > HARD)}")

    g = sum(a["gross"] for a in audits)
    n = sum(a["net"] for a in audits)
    p(f"\n## evictable, corpus-wide")
    p(f"  gross {g:,} B ({_pct(g, total):.1f}% of the corpus)"
      f"  ·  net of resume lines {n:,} B ({_pct(n, total):.1f}%)")
    p(f"    completed ranked items : {sum(a['done_b'] for a in audits):>9,} B "
      f"({sum(len(a['done']) for a in audits)} items of "
      f"{sum(len(a['ranked']) for a in audits)})")
    p(f"    resolved investigations: {sum(a['resolved_b'] for a in audits):>9,} B "
      f"({sum(len(a['resolved']) for a in audits)} blocks)")
    p(f"    retracted/dead-end     : {sum(a['retracted_b'] for a in audits):>9,} B "
      f"({sum(len(a['retracted']) for a in audits)} bullets)")
    p(f"    work-status headings   : {sum(a['dated_b'] for a in audits):>9,} B "
      f"({sum(len(a['dated']) for a in audits)} blocks)")
    p(f"  net charges {RESUME_COST} B per evicted rank, because the NUMBER must stay: it is")
    p( "  half a claim-work slug identity and deleting it re-points every live claim.")

    gen = sum(a["generic"] for a in audits)
    p(f"\n## RELOCATE_DURABLE candidates: {gen} gotcha bullet(s) — ADVISORY, NOT A MEASUREMENT")
    p( "  A generic tooling lesson belongs in RULES.md or the owning skill. Deciding")
    p( "  that needs READING the bullet: on the one doc a human checked, the regex")
    p( "  said 28-30 and the reading said ~55. No byte estimate is printed here on")
    p( "  purpose — an estimate that wrong in that direction would get quoted.")

    p("\n## sizes (worst first)")
    listed = audits if show_all else over
    for a in listed:
        note = "" if a["status"] == "OK" else f"  {a['size'] / TARGET:.1f}x target"
        p(f"  {a['size']:>9,} B {a['lines']:>6} L  {a['status']:<13} {a['name'][:52]}{note}")
    hidden = len(audits) - len(listed)
    if hidden:
        p(f"  ({hidden} doc(s) within target — not listed; --all to see them)")
    if not listed:
        p("  (none over target)")

    for a in over[:n_detail]:
        p(f"\n## {a['name']} — {a['size']:,} B ({a['size'] / TARGET:.1f}x target)")
        p(f"  {a['path']}")
        if not a["fence_ok"]:
            p("  🔴 UNCLOSED ``` FENCE — every heading after it is invisible to this")
            p("     parser, so every number for this doc is wrong. Fix it, then re-run.")
        p(f"\n  sections (H2, worst first — {len(a['h2'])} total)")
        for title, _s, _e, b in sorted(a["h2"], key=lambda s: -s[3])[:n_sections]:
            p(f"    {b:>8,} B  {title[:60]}")
        p(f"\n  evictable: gross {a['gross']:,} B ({_pct(a['gross'], a['size']):.1f}%)"
          f" · net {a['net']:,} B → {a['size'] - a['net']:,} B "
          f"({status_for(a['size'] - a['net'])})")
        p(f"    {len(a['done'])}/{len(a['ranked'])} ranked items done  {a['done_b']:,} B"
          f"  ·  {len(a['resolved'])} resolved investigations  {a['resolved_b']:,} B")
        p(f"    {len(a['retracted'])} retracted bullets  {a['retracted_b']:,} B"
          f"  ·  {len(a['dated'])} work-status blocks  {a['dated_b']:,} B")
        if a["fat"]:
            fb = sum(b for b, _ in a["fat"])
            p(f"    {len(a['fat'])} fat line(s) > {FAT_LINE} B  {fb:,} B "
              f"({_pct(fb, a['size']):.1f}%)")

    broken = [a["name"] for a in audits if not a["fence_ok"]]
    if broken:
        p("\n## unclosed code fences (every number above is wrong for these)")
        for b in broken:
            p(f"  🔴 {b}")

    p("\n## verdict")
    if not over:
        p(f"  ✓ all {len(audits)} doc(s) within the {TARGET:,} B reference target")
    else:
        p(f"  {len(over)} of {len(audits)} doc(s) over the {TARGET:,} B reference target; "
          f"{sum(1 for a in audits if a['status'] == 'OVER HARD CAP')} over the hard cap.")
        p(f"  Evicting terminal content would return ~{n:,} B ({_pct(n, total):.1f}%) "
          "of the corpus,")
        p("  every byte of it paid for again on each /resume of that doc.")


def render_csv(audits, out=sys.stdout):
    print("path,bytes,lines,status,ranked,done,done_b,resolved_b,retracted_b,"
          "dated_b,gross,net,generic,fence_ok", file=out)
    for a in sorted(audits, key=lambda a: -a["size"]):
        print(f"{a['path']},{a['size']},{a['lines']},{a['status']},{len(a['ranked'])},"
              f"{len(a['done'])},{a['done_b']},{a['resolved_b']},{a['retracted_b']},"
              f"{a['dated_b']},{a['gross']},{a['net']},{a['generic']},{a['fence_ok']}",
              file=out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="audit claudedocs/handoff-*.md sizes")
    ap.add_argument("paths", nargs="*", help="a handoff doc, a claudedocs dir, or a repo root")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--detail", type=int, default=3)
    ap.add_argument("--sections", type=int, default=8)
    ap.add_argument("--csv", action="store_true")
    args = ap.parse_args(argv)

    targets = resolve_targets(args.paths or [str(Path.cwd())])
    if not targets:
        sys.exit("no handoff-*.md found under: "
                 + ", ".join(args.paths or [str(Path.cwd())])
                 + "\n(pass a doc, a claudedocs dir, or a repo root explicitly)")
    audits = [audit_one(t) for t in targets]
    if args.csv:
        render_csv(audits)
    else:
        render(audits, args.all, args.detail, args.sections)


if __name__ == "__main__":
    main()
