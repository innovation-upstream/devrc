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
# 🔴 ANCHORED ON `^` THESE MISSED REAL SECTIONS, ALWAYS UNDERSTATING. Re-measured
# over the 414-doc corpus AT H2, which is the only level the tool reads
# (`SA.sections(lines, heads, 2)`): the widened GOTCHAS covers **52 H2 headings /
# 77,937 B** the anchored form missed (`Durable gotchas (this session)`,
# `🔴🔴 CRITICAL GOTCHAS (…)`, `Key gotchas (…)`) — and in those sections NEITHER
# the retracted scan NOR the RELOCATE_DURABLE scan ever ran. Widened INVESTIGATIONS
# covers **10 H2 / 51,182 B** more, two of them fully terminal.
# ⚠ Round 1 wrote these as 70/102,153 and 16/63,894. Those counted H1–H6; the tool
# consults H2 only, so they were ~1.4x high. Round 2 caught it — and the pair was
# the JUSTIFICATION for the highest-risk change in that round, which is exactly the
# kind of number that gets re-quoted instead of re-derived.
# 🔴 An OPEN section is the LIVE half of the doc, and widening GOTCHAS to a
# word-boundary search pulled such sections in — `Open decisions (yours, not the
# platform's)`, `🔴 PENDING — HUMAN decisions/actions`, `🔴 Parked on YOU —
# product/ops decisions`. The file already owned this guard shape for WORK_STATUS
# and simply did not apply it to the families.
#
# 🔴 THIS GUARD IS PURELY PREVENTIVE — IT REMEDIES NOTHING TODAY. Measured
# 2026-08-30 over the 414-doc corpus: it suppresses **10 H2 sections / 14,466 B**,
# and those sections contain **0 retracted bullets and 0 advisory bullets**. The
# `retracted` and `RELOCATE_DURABLE` totals are byte-identical with and without it
# (117,706 B / 232 bullets; 568 advisory). Positive control, so that pair of zeros
# is an absence and not a dead probe: the same scan finds 232 retracted bullets in
# the GOTCHAS sections that are NOT suppressed.
# ⚠ Round 2 wrote this as "13 of them" and "0 B of retracted bytes, 2 advisory
# bullets". Both are wrong — 10, and 0 advisory. Round 3 caught it, which makes it
# the second consecutive round whose fix shipped an unreproducible number
# JUSTIFYING that same fix; round 2's own claim 4 was the first. The value of the
# guard is entirely forward-looking: a future `- … dead-end …` bullet under
# `## Open decisions` would otherwise be booked as evictable history.
#
# Applied to GOTCHAS ONLY: `Open investigations` is the INVESTIGATIONS family's
# canonical heading and legitimately contains resolved sub-blocks. ⚠ That rationale
# is narrower than it reads — all three families apply a positive per-item
# predicate inside the section, so the asymmetry is a judgement, not a derivation.
# It is measured inert either way: 0 headings match both GOTCHAS∧OPEN and
# INVESTIGATIONS.
OPEN_SECTION = re.compile(r"\bopen\b|\bpending\b|\bparked\b|\bwaiting\b|\bunresolved\b", re.I)

NEXT_STEPS = re.compile(r"\bnext steps\b|\bdo next\b|\bwhat'?s next\b", re.I)
INVESTIGATIONS = re.compile(r"\binvestigations?\b|\blive diagnosis\b", re.I)
GOTCHAS = re.compile(r"\bgotchas?\b|\bdead-ends?\b|\bdecisions\b", re.I)

# A DONE marker on a ranked item. Measured shapes in the first 60 chars of the
# 2,804 numbered items in the corpus: ✅ 163, DONE 136, **DONE 115, ~~ 106,
# CLOSED 27, MERGED 14, SHIPPED 10. `~~` is markdown strikethrough, which is how
# several docs retire an item without deleting it.
DONE_MARK = re.compile(r"✅|~~|\bDONE\b|\bSHIPPED\b|\bMERGED\b|\bCLOSED\b|\bLANDED\b")
# 🔴 Bounded to the item's OWN FIRST PHYSICAL LINE. Unbounded, an item whose BODY
# merely mentions that some other PR merged is scored as complete. It is the same
# trap as skill-audit's WORK_STATUS/DATED_LESSON split: the strong-looking signal
# is in the wrong place.
# ⚠ This comment used to end "so the unbounded version marks nearly everything
# done", which is FALSE and was caught by the round-1 audit. Measured: unbounded
# scores 214 of 1,417 ranked items (15.1%), first-line 194 (13.7%) — the bound
# removes 20 items, not "nearly everything". Keeping the wrong figure would have
# had the next reader over-trust the bound and under-look at the rest.
#
# 🔴 A CHARACTER BUDGET IS NOT THAT BOUND, and this shipped as `DONE_SCAN = 160`
# before test_done_marker_is_bounded_to_the_item_head caught it. A ranked item's
# first line is ~60-80 chars, so 160 reaches two lines into the body and picks up
# exactly the mention the bound exists to exclude. Measured on the fixture: item 1
# is open and was scored done. Use the LINE, which is the structure the convention
# actually follows — every one of the corpus's 193 markers sits on it.
DONE_FIRST_LINE_ONLY = True

# A resolved investigation block, judged on its HEADING only, same reason.
#
# 🔴 CASE-SENSITIVE ON PURPOSE — DO NOT ADD `re.I`. Round 1 added it, reasoning
# that RETRACTED carries `re.I` so the inconsistency must be the defect. That was
# backwards, and round 2 caught it: THIS CORPUS SHOUTS A TERMINAL STATUS
# (✅ / CLOSED / RESOLVED / ANSWERED), so the capitals ARE the discriminator, not
# an accident. Lower case is how the words appear in ordinary prose.
#
# Measured: `re.I` added 12,902 B over 10 blocks, and 10 of 10 are inversions —
# `bounded, not closed` · `unresolved, and deliberately not resolved` · `two open
# items (arc is closed, these are new)` · `Why #4174 was closed — the reusable
# lesson`, plus three where `fail-closed` is a TERM OF ART, not a status. A reader
# quoting the resolved bucket would have evicted a block whose own title says it is
# not resolved. Three mechanisms — term of art, negation, and "something else was
# closed" — none of which a case-insensitive word match can separate.
#
# The control that settles it: handoff-object-leak-guard.md has BOTH shapes — a
# `fail-closed backstop is unexercised` heading (open; the false positive `re.I`
# added) and `Everything else … is **CLOSED** — verified` (terminal; the
# case-sensitive matcher already caught it).
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


# 🔴 THE EXTENT CLASS, THIRD INSTANCE. Two were already fixed here — an H1 whose
# extent was the whole file, and a 160-character DONE scan that reached into an
# item's body. This is the same defect in the list walkers: a bullet's or a ranked
# item's extent ran "to the next one of its kind, else the end of the H2", with no
# HEADING boundary. The last bullet in a section therefore swallowed every H3 that
# followed it, and the predicate was then applied to the swallowed text.
#
# MEASURED on the real 413-doc corpus before this fix:
#   16 of 237 "retracted" bullets crossed a heading; 44,057 B lay past the first
#   swallowed heading = 26.5% of that bucket. TEN of them (45,309 B) matched
#   RETRACTED *only* via swallowed content — they are not retractions at all. The
#   worst single case booked 16,380 B for a bullet containing no retraction
#   (datapacket-talos handoff-pr-preview-observability-and-gate15.md:226, whose
#   extent swallowed 15 headings). 3 ranked items over-reached by 9,928 B.
#   Corrected: retracted 166,390 -> 116,282 B (-30.1%); gross 14.3% -> 13.4%.
# The headline survived; the per-bucket line the report prints did not — it was
# 1.43x high. Clip at the next heading, and test the predicate on the CLIPPED text.
def _clip(heads, s, e):
    """`e`, brought back to the first heading strictly after `s`.

    A list item cannot span a heading: the heading ends the block it is in.
    """
    for i, _lv, _t in heads:
        if s < i < e:
            return i
    return e


def _blocks_by_bullet(lines, heads, inside, lo, hi, pred):
    """(start, end, bytes) for each top-level bullet in [lo,hi) matching `pred`.

    A bullet's extent runs to the next top-level bullet OR the next heading,
    whichever comes first, so a continuation line and any nested sub-bullet travel
    with it and nothing else does. Bullets inside a code fence are not bullets.
    """
    starts = [i for i in range(lo, hi) if not inside[i] and BULLET.match(lines[i])]
    out = []
    for k, s in enumerate(starts):
        e = _clip(heads, s, starts[k + 1] if k + 1 < len(starts) else hi)
        # 🔴 The BYTES include a fenced example — it is genuinely part of the
        # bullet — but the PREDICATE must not read it. A bullet followed by a code
        # block containing the word REFUTED is not a retraction, and matching on
        # fenced content is the same "matched on text that is not the bullet's own
        # claim" defect as the swallowed-heading case above, one layer in.
        probe = "".join(ln for j, ln in enumerate(lines[s:e], start=s) if not inside[j])
        if pred(probe):
            out.append((s, e, _bucket(lines, s, e)))
    return out


def _ranked_items(lines, heads, inside, lo, hi):
    """(number, start, end, bytes, done) per top-level numbered item in [lo,hi).

    Only a strictly-ascending run counts, mirroring skill-audit._corpus_items: a
    nested "1./2." list inside an item restarts, and treating that restart as a
    new rank both inflates the count and mis-attributes the bytes.

    🔴 The ascending-run rule is NOT fence awareness, and the two were confused:
    a fenced `2. echo x` after a real item 1 ascends, so it was booked as rank 2.
    The corpus happens to contain no such line today — measured with a positive
    control proving the detector is wired — but the guard that was supposed to
    hold this was passing for the ascending-run reason instead.
    """
    starts, last = [], 0
    for i in range(lo, hi):
        if inside[i]:
            continue
        m = NUMBERED.match(lines[i])
        if m and int(m.group(1)) > last:
            last = int(m.group(1))
            starts.append((last, i))
    out = []
    for k, (n, s) in enumerate(starts):
        e = _clip(heads, s, starts[k + 1][1] if k + 1 < len(starts) else hi)
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

    inside, _unclosed = SA._fence_map(lines)

    ranked, resolved, retracted, generic = [], [], [], 0
    for title, s, e, _b in h2:
        if NEXT_STEPS.search(title):
            ranked += _ranked_items(lines, heads, inside, s, e)
        if INVESTIGATIONS.search(title):
            for t3, s3, e3, b3 in SA.sections(lines, heads, 3, s, e):
                if RESOLVED_HEAD.search(t3):
                    resolved.append((t3, s3, e3, b3))
        if GOTCHAS.search(title) and not OPEN_SECTION.search(title):
            retracted += _blocks_by_bullet(lines, heads, inside, s, e,
                                           lambda t: RETRACTED.search(t))
            generic += len(_blocks_by_bullet(lines, heads, inside, s, e,
                                             lambda t: GENERIC_LESSON.search(t)))

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
    """(docs, per_root) — the resolved docs, and what each ARGUMENT contributed.

    🔴 The per-root tally is not decoration. This tool's output is meant to be
    quoted, and without it a run naming two roots where one holds no docs prints a
    single total with nothing to say so — an existing-but-empty root contributes
    silently, because only a NONEXISTENT path warns, and that warning goes to
    stderr where a redirect loses it. A quoted headline has to be traceable to the
    population it came from.
    """
    # 🔴 TALLIED AFTER DEDUP, one root at a time. Round 1 counted what each root
    # MATCHED and deduped afterwards, so two overlapping roots (`<repo>` and
    # `<repo>/claudedocs`, which the usage line explicitly invites) each reported
    # the full 87 under a header reading 87 — a traceability feature contradicting
    # its own total. Counting only the NEWLY-UNIQUE docs makes the column sum to
    # the header by construction, and gives the second root an honest 0.
    seen, uniq, per_root = set(), [], []
    for a in args:
        p = Path(os.path.expanduser(a))
        if p.is_file():
            found = [p.resolve()]
        elif not p.is_dir():
            print(f"handoff-audit: no such path: {p}", file=sys.stderr)
            per_root.append((str(p), None, 0))
            continue
        else:
            found = []
            for base in (p, p / "claudedocs"):
                if base.is_dir():
                    found += [f.resolve() for f in sorted(base.glob("handoff-*.md"))]
        added = 0
        for f in found:
            if f not in seen:
                seen.add(f)
                uniq.append(f)
                added += 1
        # 🔴 `matched` is carried so a 0 can say WHY. Tallying after dedup fixed the
        # column-does-not-sum-to-the-header defect and re-created, for a different
        # cause, the very silence this tally exists to break: a fully-overlapping
        # root and a genuinely empty one both printed a bare `0`. An operator then
        # hunts for a typo in a path that was in fact wholly covered by an earlier
        # root. Same shape as the empty-root case in this function's docstring.
        per_root.append((str(p), added, len(found)))
    return uniq, per_root


def _pct(a, b):
    return (100.0 * a / b) if b else 0.0


def render(audits, show_all, n_detail, n_sections, out=sys.stdout, per_root=()):
    p = lambda *a: print(*a, file=out)
    audits = sorted(audits, key=lambda a: -a["size"])
    over = [a for a in audits if a["status"] != "OK"]
    total = sum(a["size"] for a in audits)
    p(f"# handoff-doc audit — {len(audits)} doc(s)")
    if per_root:
        p("\n## population (each root as given, and what it contributed)")
        for root, n, matched in per_root:
            if n is None:
                cell = "NO SUCH PATH"
            elif n == 0 and matched:
                cell = f"0 (dup)"          # every doc here was already counted
            else:
                cell = format(n, ">5")
            p(f"  {cell:>12}  {root}")
        p("  Counted after dedup, so the column SUMS to the header even when two")
        p("  roots overlap. `0 (dup)` = every doc here was already counted under an")
        p("  earlier root; a bare `0` = this path genuinely holds no handoff docs.")
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

    targets, per_root = resolve_targets(args.paths or [str(Path.cwd())])
    if not targets:
        sys.exit("no handoff-*.md found under: "
                 + ", ".join(args.paths or [str(Path.cwd())])
                 + "\n(pass a doc, a claudedocs dir, or a repo root explicitly)")
    audits = [audit_one(t) for t in targets]
    if args.csv:
        render_csv(audits)
    else:
        render(audits, args.all, args.detail, args.sections, per_root=per_root)


if __name__ == "__main__":
    main()
