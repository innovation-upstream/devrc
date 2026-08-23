#!/usr/bin/env python3
"""Deterministic audit of Claude Code SKILL.md bodies (the /prune-skill auditor).

WHY: a SKILL.md costs 0 tokens until its trigger fires, then ALL of it at once.
Nothing applies per-session pressure, so skills accrete silently. Measured
2026-08-02 in datapacket-talos/.claude/skills/: 67 skills = 3.13 MB, up from
2.79 MB six days earlier while the skill COUNT went 65 -> 66 — i.e. the growth
was accretion into existing files. Worst case `app-blocks/SKILL.md` = 726 KB
~= 182k tokens, which does not fit a 200k context at all.

Reports, per skill: size vs budget, per-section (H2, then H3 inside the fattest
H2) byte weights, work-status history blocks (the top eviction candidate) with a
projected saving, dated LESSONS reported separately because they are NOT
evictable, fat lines (>500 B), and reference-file
integrity in BOTH directions (a referenced file that is missing, and a file on
disk that nothing routes to — an orphan reference file is invisible, therefore
dead). Pure measurement — makes NO edits. The /prune-skill command runs this,
then an agent applies the judgment cut.

Usage:
  skill-audit.py [PATH ...]        PATH = a SKILL.md, a skills dir, or a repo
                                   root; with no arg, $PWD/.claude/skills
  --all           list every skill in the size table (default: only over-budget
                  skills are listed individually)
  --sections N    how many sections to show per detail block (default 10)
  --detail N      how many skills to render a detail block for (default 3)

Sibling of memory-audit.py (which audits the per-session MEMORY.md index). The
byte ceiling is the same one enforced on the `browser` skill by
scripts/browser-bridge/tests/test_skill_size.py — that skill is the existence
proof that 12 KB is achievable for a genuinely complex tool.
"""
import argparse
import importlib.machinery
import importlib.util
import os
import re
import sys
from pathlib import Path

# --- budgets -------------------------------------------------------------------
# 🔴 THE BUDGET IS NOT RE-DECLARED HERE. scripts/browser-bridge/tests/test_skill_size.py
# OWNS both numbers, each with the measurement that sized it, and it is the gate
# that actually blocks a merge. This module used to restate the ceiling as a
# literal and check ONLY against it -- so it printed
#
#     ✓ all 1 skill(s) within budget -- no prune needed (stop; do not churn the files)
#
# for scripts/browser-bridge/SKILL.md on a day that gate was RED (12,259 B: inside
# the 12,288 B ceiling, 221 B inside the 250 B working margin). The documented
# audit tool told a maintainer to stop about the exact file the authoritative test
# was rejecting. Reading the constants out of the gate is what makes that
# disagreement impossible; a fallback literal here would be the duplicate this
# exists to prevent, so a missing/unreadable gate is a hard failure, not a default.
_BUDGET_SOURCE = (Path(__file__).resolve().parent
                  / "browser-bridge" / "tests" / "test_skill_size.py")


def _load_budget(path=_BUDGET_SOURCE):
    """(ceiling, working margin) read from the gate module itself.

    That module imports nothing but pathlib and runs no code at import time, so
    loading it here is cheap and side-effect free.
    """
    if not path.is_file():
        raise SystemExit(
            f"skill-audit: cannot read the budget -- {path} is missing.\n"
            "That file OWNS the ceiling and the working-margin floor. This tool "
            "deliberately keeps no copy of them (a second hand-maintained number "
            "is how the audit and the gate came to disagree), so it cannot run "
            "without it."
        )
    loader = importlib.machinery.SourceFileLoader("_skill_size_gate", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    try:
        return int(mod.MAX_BYTES), int(mod.MIN_HEADROOM_BYTES)
    except AttributeError as e:  # the gate renamed a constant
        raise SystemExit(
            f"skill-audit: {path} no longer exports the budget constants "
            f"({e}). Re-point _load_budget at their new names -- do NOT paste "
            "the numbers back in here."
        )


# TARGET is the browser skill's PROVEN ceiling; MIN_HEADROOM is the working margin
# that must stay below it, sized off a MEASURED mean table row so the warning
# precedes the breach. BUDGET is what is actually ENFORCED -- a body between
# BUDGET and TARGET is inside the ceiling and still fails the gate.
TARGET, MIN_HEADROOM = _load_budget()
BUDGET = TARGET - MIN_HEADROOM
# HARD is where a skill stops being a skill. ~40 KB is ~10k tokens — already a
# 5% bite out of a 200k context before any work starts. Past this the body
# routinely displaces the task it was loaded for.
HARD = 40_960
# A line this long is a paragraph wearing a bullet's clothes: it is prose that
# belongs in a reference file, not a routing line. 500 B is ~3 dense lines of
# terminal output.
FAT_LINE = 500

HEADING = re.compile(r"^(#{1,6})\s+(.+)$")

# A code-fence marker: up to 3 leading spaces, then a run of >=3 backticks or
# tildes, then the info string. Length and info are BOTH load-bearing when
# deciding whether a marker opens or closes — see _fence_map.
FENCE = re.compile(r"^ {0,3}(?P<f>`{3,}|~{3,})(?P<info>.*)$")

# 🔴 TWO DIFFERENT THINGS, and conflating them costs you your best content.
#
# WORK_STATUS names WORK DONE — `### Session 2026-07-29→30 — dogfood + audit`.
# It is narrative about a past session and belongs in a claudedocs/ handoff, so
# it is the genuine EVICT_HISTORY bucket.
#
# DATED_LESSON is durable guidance whose heading merely CITES a date —
# `## Common silent-failure modes (from the 2026-05-22 audit)`, `## 🔴 The
# INERT-ALERT defect class … (2026-08-01)`. Evicting it guts the skill. It is at
# most a DEMOTE_TO_REFERENCE candidate.
#
# These were one bucket keyed on the ISO date, which made the date the dominant
# signal. MEASURED 2026-08-04 across the 67-skill datapacket-talos corpus:
# app-blocks carries 45 work-status headings, and EVERY OTHER SKILL CARRIES
# ZERO — while manage-alerts has 17 dated-but-topical headings, manage-redis 15,
# profile-dp 10. So the merged metric reported 59% "evictable history" for
# manage-alerts whose two largest blocks were its most valuable operational
# content. The single bucket was a true signal on 1 skill of 67 and a false one
# on the other 66.
WORK_STATUS_HEADING = re.compile(
    r"\bsessions?\b"
    r"|\bchangelog\b"
    r"|\bwork\s*log\b"
    r"|\bwhat\s+(?:we\s+)?shipped\b"
    r"|\brelease\s+notes\b"
    r"|\bhistory\b",
    re.I,
)
# Only consulted for a heading that is NOT work-status; a bare date is the
# weakest signal, so it downgrades to "review", never to "evict".
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

# A reference path mentioned in the core. Placeholders (`reference/<file>.md`)
# deliberately do NOT match — `<` is outside the character class — so a doc that
# writes its paths with the <var> convention is not reported as broken.
REF_PATH = re.compile(r"[\w./~$@-]*reference/[\w.-]+\.md")

# Does the core state an ABSOLUTE base for its reference dir? If it does, the
# relative paths in its routing table resolve for a reader who followed it, so
# the deployment caveat below does not apply.
ABS_REF_BASE = re.compile(r"(?:~|/)[\w./-]*/reference/")

# --- numbered-corpus integrity --------------------------------------------------
# A corpus of numbered items ("11. **ModelMetric is declared...**") that has been
# DEMOTED across several reference files is cited BY NUMBER — from the core, from
# sibling reference files, and from OTHER skills ("gotcha #178"). The numbers are
# therefore an API, and a demote that renumbers per-file silently breaks every
# citation: nothing in a repo-relative path gate can see it, because the paths are
# all still fine. Measured on datapacket-talos app-blocks 2026-08-17: 200 items
# spread over 9 files, numbered 1-200 with ZERO gaps, 150 distinct citations, zero
# dangling — i.e. the scheme survives a split only because whoever split it froze
# the numbers and said so in a banner on every file.
CORPUS_ITEM = re.compile(r"^(\d+)\. ", re.M)
# Only the EXPLICIT form. A bare `#N` is unusable here: across app-blocks it
# matches 313 distinct numbers of which 189 exceed the corpus entirely (civitai
# PRs, issues, line numbers), whereas `gotcha #N` matches 61, max 188 — every one
# a real index. Bounding bare `#N` by the highest defined index was tried first
# and is WORSE THAN USELESS: a demote that renumbers every shard to 1..n drops the
# ceiling with it, so the citations that just broke fall below the bound and the
# check goes silent at exactly the moment it should fire.
CORPUS_CITE = re.compile(r"gotchas?\s+#(\d+)")
# A SHARD of a split corpus vs an ordinary numbered list, decided by DENSITY: a
# shard holds a scattered slice of one global sequence, a procedure holds 1..n.
# Measured over app-blocks' 13 numbered files — the 9 shards score span/n 4.90 to
# 12.00, and all 4 non-shards (a W-table, two step lists, the core) score exactly
# 1.00. A 1.5 cut sits in a 3.4x-wide empty gap, so this is not a tuned constant.
CORPUS_SPARSE = 1.5
# Guards the degenerate end: 2 items numbered 1 and 9 are sparse by ratio and are
# still just a list.
CORPUS_FILE_MIN = 5
# Below this many items across all shards, there is no corpus worth policing.
CORPUS_MIN = 10


def _bytes(lines):
    return sum(len(ln.encode()) for ln in lines)


def _fence_map(lines):
    """(inside_fence_per_line, unclosed_at_eof) using CommonMark fence semantics.

    🔴 A PARITY COUNT OF ``` MARKERS IS NOT A FENCE CHECK. Under CommonMark a
    fence opened with N of a char closes only on >= N of the SAME char followed
    by nothing but whitespace — a marker carrying an INFO STRING (```action)
    can never close one. So the ordinary pattern of a literal ```lang example
    nested inside an outer fence is well-formed but has ODD marker parity.

    MEASURED 2026-08-03, and this replaced a heuristic that got it exactly
    backwards: parity called datapacket-talos app-blocks/SKILL.md (47 markers)
    and manage-support-stack/SKILL.md (57) "unclosed"; a CommonMark read shows
    BOTH well-formed. The "250 KB phantom H1" parity produced by re-partitioning
    app-blocks at a `# 3. SQL state:` shell COMMENT did not exist, and the
    dated-history share it derived (75.4%) was inflated from a true 43%. Two
    independent readers reproduced that false positive because they used the
    same wrong instrument — so the tell is the FIX, not the agreement.
    """
    inside = []
    open_char, open_len = "", 0
    for raw in lines:
        m = FENCE.match(raw.rstrip("\n"))
        is_marker = False
        if m:
            char, run, info = m.group("f")[0], len(m.group("f")), m.group("info")
            if open_len:
                if char == open_char and run >= open_len and not info.strip():
                    open_len, is_marker = 0, True
            elif not (char == "`" and "`" in info):
                # a backtick opener's info string may not contain a backtick
                open_char, open_len, is_marker = char, run, True
        inside.append(True if is_marker else open_len > 0)
    return inside, open_len > 0


def _headings(lines):
    """(line_index, level, title) for every ATX heading OUTSIDE a code fence.

    Fence tracking is load-bearing: a `# comment` inside a ```bash block is not
    a heading, and counting it as one silently re-partitions the whole file.
    """
    inside, _ = _fence_map(lines)
    out = []
    for i, raw in enumerate(lines):
        if inside[i]:
            continue
        m = HEADING.match(raw.rstrip("\n"))
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def fence_balanced(lines):
    """False when a fence is never closed.

    Not a nicety: an unclosed fence makes every heading after it invisible to
    _headings(), so the last real section silently swallows the rest of the file
    and every byte-weight below is wrong. The audit must say so rather than
    quietly reporting the skewed numbers — but it must not cry wolf either, so
    the check is a real fence walk, not a marker count (see _fence_map).
    """
    return not _fence_map(lines)[1]


def _extent(headings, i, level, n_lines):
    """End line (exclusive) of the block opened by the heading at line i."""
    for j, lv, _ in headings:
        if j > i and lv <= level:
            return j
    return n_lines


def sections(lines, headings, level, lo=0, hi=None):
    """Blocks at `level` within [lo, hi): (title, start, end, bytes)."""
    hi = len(lines) if hi is None else hi
    out = []
    for i, lv, title in headings:
        if lv != level or not (lo <= i < hi):
            continue
        end = min(_extent(headings, i, level, len(lines)), hi)
        out.append((title, i, end, _bytes(lines[i:end])))
    return out


def dated_blocks(lines, headings, kind="work_status"):
    """Work-status OR dated-lesson blocks, outermost-only (a nested one is
    already inside its parent).

    kind="work_status" -> the EVICT_HISTORY bucket: narrative about a past
    session, which belongs in a claudedocs/ handoff.
    kind="dated_lesson" -> durable guidance that merely cites a date. NOT
    evictable; reported separately so a reader cannot mistake one for the other.

    Returns (title, start, end, bytes), in file order.
    """
    out = []
    covered_until = -1
    for i, lv, title in headings:
        if i < covered_until:
            continue
        ws = bool(WORK_STATUS_HEADING.search(title))
        hit = ws if kind == "work_status" else (not ws and bool(ISO_DATE.search(title)))
        if not hit:
            continue
        end = _extent(headings, i, lv, len(lines))
        out.append((title, i, end, _bytes(lines[i:end])))
        covered_until = end
    return out


def fat_lines(lines, limit=FAT_LINE):
    return [(len(ln.encode()), ln.rstrip("\n")) for ln in lines if len(ln.encode()) > limit]


def _sidecar_tail(skill_dir, ref):
    """This ref expressed RELATIVE TO the skill dir, or None if it is not a sidecar.

    Three accepted spellings, because a core may legitimately use any of them:
      1. bare              `reference/x.md`
      2. absolute          `~/workspace/.../<name>/reference/x.md`
      3. repo-root-relative `.claude/skills/<name>/reference/x.md`
      4. deployed          `~/.claude/skills/<name>/reference/x.md`

    🔴 (3) used to return False, so a repo-local skill written the way the
    prune-skill guidance RECOMMENDS was scored as having no sidecars at all —
    "no skill routes to a reference/ sidecar yet" on a freshly-split skill with
    five routing lines, and a missing topic reported by nobody. Measured on
    datapacket-talos image-cacher 2026-08-17, right after its 70 KB -> 8 KB split.

    A relative path rooted elsewhere (`apps/reference/manifest.md` — a page on a
    CLIENT's public developer-docs site, seen in two real datapacket skills) is
    still NOT a sidecar: reporting it as broken is a false alarm about another
    repo's docs site. (3) is distinguished from it by naming THIS skill's own
    directory immediately before `reference/`.
    """
    if ref.startswith("./"):
        ref = ref[2:]
    if ref.startswith("reference/"):
        return ref
    p = Path(os.path.expanduser(ref))
    if p.is_absolute():
        try:
            return p.relative_to(skill_dir).as_posix()
        except ValueError:
            # 🔴 (4) THE DEPLOYED SPELLING. `~/.claude/skills/<name>/reference/x.md`
            # expands to an absolute path under the HOME deploy root, not under
            # the SOURCE skill_dir, so relative_to() raises -- and returning None
            # here scored a skill with three sidecars and eleven routing lines as
            # "no skill routes to a reference/ sidecar yet". Measured on devrc
            # prune-skill 2026-08-19.
            #
            # This is not a niche spelling: devrc skills are READ from
            # ~/.claude/skills/ by an agent whose cwd is some unrelated project,
            # so it is the only one of the four a reader can actually open. Fall
            # through to the marker test, which is the same disambiguation (3)
            # uses -- it names THIS skill's own directory immediately before
            # `reference/`, so another repo's `apps/reference/manifest.md` still
            # does not match.
            pass
    # 🔴 SEGMENT boundary, not substring. `find()` alone matches a skill whose
    # name merely ENDS with this one: looking for `mailbox/reference/` hits
    # `vetr-mailbox/reference/`, and BOTH are real skills here — so a legitimate
    # cross-reference between them was claimed as `mailbox`'s own sidecar and then
    # reported as a phantom `missing_refs`. Widening the resolver above newly
    # exposed absolute/deployed refs to this, so the collision got bigger.
    # The marker must start the string or be preceded by "/".
    name = skill_dir.name
    marker = f"{name}/reference/"
    i = ref.find(marker)
    while i != -1:
        if i == 0 or ref[i - 1] == "/":
            return ref[i + len(name) + 1:]
        i = ref.find(marker, i + 1)
    return None


def resolve_ref(skill_dir, ref):
    tail = _sidecar_tail(skill_dir, ref)
    if tail is None:
        p = Path(os.path.expanduser(ref))
        return p if p.is_absolute() else skill_dir / ref
    return skill_dir / tail


def referenced_refs(text, skill_dir):
    """This skill's own reference/*.md sidecars, deduped, in first-seen order.

    Deduped by RESOLVED TARGET, not by the string as written: a core may name the
    same topic twice in different accepted spellings (app-blocks routes to
    legacy-hackathon-ops.md from its table as `reference/…` and from its Repo
    layout as `.claude/skills/app-blocks/reference/…`). Counting the spellings
    reported 16 "reference file(s)" for 15 files.
    """
    seen, out = set(), []
    for m in REF_PATH.findall(text):
        tail = _sidecar_tail(skill_dir, m)
        if tail is None or tail in seen:
            continue
        seen.add(tail)
        out.append(m)
    return out


def reference_integrity(skill_md, text):
    """(referenced, missing, orphans, relative_count, has_abs_base).

    BOTH directions, mirroring test_eviction_playbook_lists_every_reference_topic:
    a referenced file that does not exist routes the reader nowhere, and a file
    on disk that nothing routes to is unreachable and therefore dead.

    🔴 THE THIRD ROUTING SHAPE: a DIRECTORY routed with a VARIABLE segment.
    Naming every reference file in the body is what keeps a skill honest, but it
    also makes the body grow linearly with the corpus — which is unaffordable for
    a set that is expected to keep growing (browser's per-site docs). A row
    written with a placeholder segment,

        | `reference/sites/<host>.md` | you are driving a site that has one … |

    routes to EVERY member of that directory without naming one, because
    something at RUN TIME resolves the placeholder (for browser, the bridge puts
    the exact filename in the result envelope). The reachability claim the orphan
    check exists to defend is therefore satisfied, so a member of such a
    directory is NOT dead. Recognised structurally — `<dir>/<` in the body, the
    repo's own documented placeholder convention — never by naming a directory
    here. A file under a directory with NO placeholder route is still an orphan.
    """
    skill_dir = skill_md.parent
    refs = referenced_refs(text, skill_dir)
    missing = [r for r in refs if not resolve_ref(skill_dir, r).is_file()]
    ref_dir = skill_dir / "reference"
    orphans = []
    if ref_dir.is_dir():
        for f in sorted(ref_dir.rglob("*.md")):
            rel = f.relative_to(skill_dir).as_posix()
            if rel in text or f.name in text:
                continue
            parent = f.parent.relative_to(skill_dir).as_posix()
            if parent != "reference" and f"{parent}/<" in text:
                continue  # routed by a variable segment — see the docstring
            orphans.append(rel)
    relative = [r for r in refs if not (r.startswith("/") or r.startswith("~"))]
    return refs, missing, orphans, len(relative), bool(ABS_REF_BASE.search(text))


def _corpus_items(body):
    """Top-level numbered items in one file, nested/restarting lists dropped.

    Real corpus items ascend monotonically within a file (they are a slice of one
    global sequence). A nested "1./2." list inside an item's body restarts, so
    keeping only the strictly-ascending run is what separates the two — verified
    against app-blocks, where it reproduced the core's own per-file counts on 8 of
    9 files and exposed the 9th as genuinely stale (labelled 18, holds 20).
    """
    out, last = [], 0
    for n in (int(m) for m in CORPUS_ITEM.findall(body)):
        if n > last:
            out.append(n)
            last = n
    return out


def corpus_integrity(skill_md):
    """(n_defined, dangling, dupes) over the skill's whole directory.

    Reports ONLY on a corpus that is actually cited: a skill whose numbered lists
    are procedures produces no citations, hence no findings, hence no noise.
    """
    skill_dir = skill_md.parent
    ref_dir = skill_dir / "reference"
    if not ref_dir.is_dir():
        return 0, [], []  # nothing has been split, so nothing can have desynced
    files = [skill_md] + sorted(ref_dir.rglob("*.md"))
    bodies = {f: f.read_text(errors="replace") for f in files}

    # DEFINED is every top-level numbered item, density irrelevant. Keeping the
    # dense files in is what makes a total renumber detectable: after one, every
    # shard reads 1..n, so a density-filtered view would hold nothing at all.
    per_file = {f: _corpus_items(b) for f, b in bodies.items()}
    defined = {}
    for f, items in per_file.items():
        for n in items:
            defined.setdefault(n, f.name)
    if len(defined) < CORPUS_MIN or sum(1 for i in per_file.values() if i) < 2:
        return 0, [], []

    cited = {int(n) for b in bodies.values() for n in CORPUS_CITE.findall(b)}
    if not cited:
        return 0, [], []
    # Several skills cite ANOTHER skill's corpus ("gotchas #137" from
    # manage-design-system, one in gitops-gate). Requiring a citation to land in
    # this skill's own numbers was tried as the guard against policing those, and
    # it REINTRODUCED the silent-renumber hole: when every shard is rewritten,
    # nothing overlaps and the check disappears. The size gate above already
    # excludes every borrowed case in the real corpus — measured across all 67
    # datapacket skills, dropping this guard adds zero findings.

    # DUPES are scored over sparse shards only: a dense 1..n list legitimately
    # reuses low numbers that a shard also holds, and pairing those reported 15
    # bogus collisions on a corpus that is provably intact.
    seen, dupes = {}, []
    for f, items in per_file.items():
        if len(items) < CORPUS_FILE_MIN:
            continue
        if (max(items) - min(items) + 1) / len(items) <= CORPUS_SPARSE:
            continue
        for n in items:
            if n in seen:
                dupes.append((n, seen[n], f.name))
            else:
                seen[n] = f.name
    return len(defined), sorted(cited - set(defined)), dupes


def audit_one(skill_md):
    text = skill_md.read_text(errors="replace")
    lines = text.splitlines(keepends=True)
    heads = _headings(lines)
    size = len(text.encode())
    h2 = sections(lines, heads, 2)
    dated = dated_blocks(lines, heads, "work_status")
    lessons = dated_blocks(lines, heads, "dated_lesson")
    fat = fat_lines(lines)
    refs, missing, orphans, n_rel, abs_base = reference_integrity(skill_md, text)
    corpus_n, corpus_dangling, corpus_dupes = corpus_integrity(skill_md)
    first_h2 = h2[0][1] if h2 else len(lines)
    fattest = max(h2, key=lambda s: s[3]) if h2 else None
    return {
        "path": skill_md,
        # A skill is identified by its DIRECTORY (.claude/skills/<name>/SKILL.md).
        # Auditing a loose file — a scratch copy, a candidate rewrite — must not
        # inherit whatever directory it happens to be sitting in: that reported a
        # copy under scratchpad/ as the skill "scratchpad".
        "name": skill_md.parent.name if skill_md.name == "SKILL.md" else skill_md.stem,
        "size": size,
        "status": status_for(size),
        "preamble_bytes": _bytes(lines[:first_h2]),
        "h2": h2,
        "fattest_h2": fattest,
        "h3_of_fattest": sections(lines, heads, 3, fattest[1], fattest[2]) if fattest else [],
        "dated": dated,
        "dated_bytes": sum(b for *_, b in dated),
        "lessons": lessons,
        "lesson_bytes": sum(b for *_, b in lessons),
        "fat": fat,
        "fat_bytes": sum(b for b, _ in fat),
        "refs": refs,
        "missing_refs": missing,
        "orphan_refs": orphans,
        "relative_refs": n_rel,
        "abs_ref_base": abs_base,
        "corpus_n": corpus_n,
        "corpus_dangling": corpus_dangling,
        "corpus_dupes": corpus_dupes,
        "fence_ok": fence_balanced(lines),
    }


def resolve_targets(args):
    """Every SKILL.md named by `args` (a file, a skills dir, or a repo root)."""
    out = []
    for a in args:
        p = Path(os.path.expanduser(a))
        if p.is_file():
            out.append(p.resolve())
            continue
        if not p.is_dir():
            print(f"skill-audit: no such path: {p}", file=sys.stderr)
            continue
        if (p / "SKILL.md").is_file():
            out.append((p / "SKILL.md").resolve())
            continue
        found = sorted(p.glob("*/SKILL.md")) or sorted(p.rglob("SKILL.md"))
        out.extend(f.resolve() for f in found)
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def status_for(size):
    """The four bands, widest-first in severity.

    NO HEADROOM is the band the ceiling-only check could not see: under TARGET,
    so the old comparison called it OK, while the gate rejects it. It is a
    FINDING, not a note -- everything downstream that asks "is this skill over"
    tests `status != "OK"`, so naming it here is what makes the verdict move.
    """
    if size <= BUDGET:
        return "OK"
    if size <= TARGET:
        return "NO HEADROOM"
    if size <= HARD:
        return "OVER TARGET"
    return "OVER HARD CAP"


def _mark(status):
    return {"OK": "✓", "NO HEADROOM": "⚠", "OVER TARGET": "⚠",
            "OVER HARD CAP": "✗"}[status]


def _overage(a):
    """(what it is over, by how many bytes) for a non-OK skill."""
    if a["status"] == "OVER HARD CAP":
        return "hard cap", a["size"] - HARD
    if a["status"] == "OVER TARGET":
        return "target", a["size"] - TARGET
    return "enforced budget", a["size"] - BUDGET


def render(audits, show_all, n_sections, n_detail, out=sys.stdout):
    p = lambda *a: print(*a, file=out)
    audits = sorted(audits, key=lambda a: -a["size"])
    over = [a for a in audits if a["status"] != "OK"]
    p(f"# SKILL.md audit — {len(audits)} skill(s)")
    p(f"\nbudget {BUDGET:,} B ENFORCED   = ceiling {TARGET:,} B − {MIN_HEADROOM:,} B "
      f"working margin   ·   hard cap {HARD:,} B")
    p(f"  ({BUDGET:,} is the number the gate rejects at — a body between it and the "
      f"{TARGET:,} B\n   ceiling is 'under the ceiling' and still RED. Both constants are read "
      f"from\n   {_BUDGET_SOURCE.relative_to(Path(__file__).resolve().parent.parent)}, "
      f"which owns them.)")

    p("\n## sizes (worst first)")
    listed = audits if show_all else over
    for a in listed:
        if a["status"] == "OK":
            note = ""
        elif a["status"] == "NO HEADROOM":
            note = (f"  {a['size'] - BUDGET:,} B over the enforced budget "
                    f"(only {TARGET - a['size']:,} B of the {MIN_HEADROOM:,} B margin left)")
        else:
            note = f"  {a['size'] / TARGET:.1f}x target"
        p(f"  {a['size']:>9,} B  {_mark(a['status'])} {a['status']:<13} {a['name']}{note}")
    hidden = len(audits) - len(listed)
    if hidden:
        p(f"  ({hidden} skill(s) within budget — not listed; --all to see them)")
    if not listed:
        p("  (none over budget)")

    for a in over[:n_detail]:
        what, by = _overage(a)
        p(f"\n## {a['name']} — {a['size']:,} B (over {what} by {by:,} B)")
        p(f"  {a['path']}")
        if not a["fence_ok"]:
            p("  🔴 UNCLOSED ``` FENCE — every heading after it is invisible to this "
              "parser, so the section/dated numbers below UNDERSTATE the count and "
              "OVERSTATE the last block. Fix the fence, then re-run.")

        p(f"\n  sections (H2, worst first — {len(a['h2'])} total)")
        if a["preamble_bytes"]:
            p(f"    {a['preamble_bytes']:>8,} B  (preamble / frontmatter)")
        for title, _s, _e, b in sorted(a["h2"], key=lambda s: -s[3])[:n_sections]:
            p(f"    {b:>8,} B  {title[:64]}")
        if len(a["h2"]) > n_sections:
            p(f"    ... {len(a['h2']) - n_sections} more H2 section(s)")

        if a["fattest_h2"] and a["h3_of_fattest"]:
            p(f"\n  H3 inside the fattest H2 \"{a['fattest_h2'][0][:50]}\"")
            for title, _s, _e, b in sorted(a["h3_of_fattest"], key=lambda s: -s[3])[:n_sections]:
                p(f"    {b:>8,} B  {title[:64]}")
            if len(a["h3_of_fattest"]) > n_sections:
                p(f"    ... {len(a['h3_of_fattest']) - n_sections} more H3 section(s)")

        p("\n  work-status history (EVICT_HISTORY — genuinely evictable)")
        if a["dated"]:
            pct = 100.0 * a["dated_bytes"] / a["size"]
            after = a["size"] - a["dated_bytes"]
            verdict = {
                "OK": "under the enforced budget",
                "NO HEADROOM": "still inside the working margin — the gate stays RED",
                "OVER TARGET": "still over target",
                "OVER HARD CAP": "still over the HARD CAP",
            }[status_for(after)]
            p(f"    {len(a['dated'])} block(s), {a['dated_bytes']:,} B ({pct:.1f}% of the file)")
            p(f"    → evicting them to a claudedocs/ doc leaves {after:,} B — {verdict}")
            for title, _s, _e, b in sorted(a["dated"], key=lambda s: -s[3])[:5]:
                p(f"      {b:>8,} B  {title[:64]}")
        else:
            p("    (none detected)")

        # Reported SEPARATELY and never added to the eviction total. These were
        # one bucket with the above, keyed on the ISO date; that read a skill's
        # best operational content as evictable narrative on 66 of 67 skills.
        p("\n  dated lessons (NOT evictable — durable guidance that cites a date)")
        if a["lessons"]:
            lpct = 100.0 * a["lesson_bytes"] / a["size"]
            p(f"    {len(a['lessons'])} block(s), {a['lesson_bytes']:,} B ({lpct:.1f}% of the file)")
            p("    → DEMOTE_TO_REFERENCE at most. Do NOT evict to claudedocs/: the")
            p("      date is a citation, not a work-status marker. Read each before moving it.")
            for title, _s, _e, b in sorted(a["lessons"], key=lambda s: -s[3])[:5]:
                p(f"      {b:>8,} B  {title[:64]}")
        else:
            p("    (none detected)")

        p(f"\n  fat lines (> {FAT_LINE} B — trim targets / DEMOTE_TO_REFERENCE)")
        if a["fat"]:
            p(f"    {len(a['fat'])} line(s), {a['fat_bytes']:,} B "
              f"({100.0 * a['fat_bytes'] / a['size']:.1f}% of the file)")
            for b, ln in sorted(a["fat"], key=lambda f: -f[0])[:3]:
                p(f"      {b:>8,} B  {ln[:60]}…")
        else:
            p("    (none)")

    p("\n## reference-file integrity")
    any_ref_issue = False
    for a in audits:
        bits = []
        if a["missing_refs"]:
            bits.append("MISSING (routes nowhere): " + ", ".join(a["missing_refs"]))
        if a["orphan_refs"]:
            bits.append("ORPHANED (on disk, nothing routes to it): " + ", ".join(a["orphan_refs"]))
        if bits:
            any_ref_issue = True
            p(f"  {a['name']}: " + "; ".join(bits))
        if a["relative_refs"] and not a["abs_ref_base"]:
            p(f"  {a['name']}: {a['relative_refs']} RELATIVE reference path(s) and "
              "no absolute base stated. 🔴 NOT 'usually fine' — this line said so "
              "until 2026-08-19 and that framing PRODUCED 47 unopenable refs "
              "across 13 devrc skills. SHIPPING and RESOLVING are separate: "
              "nix/home.nix maps "
              "devrc/claude/skills with `recursive = true`, so `reference/` DOES "
              "ship to ~/.claude/skills/<name>/reference/ (verified live on "
              "activity, clawgate, initiatives, mailbox, tekton, auditloop, bar, "
              "devrc-dx, i3). It does NOT ship for the two mkOutOfStoreSymlink "
              "exceptions — `browser` (scripts/browser-bridge/) and `dl-router` "
              "(scripts/dl-router/) link only SKILL.md plus their CLI — so THOSE "
              "cores must use repo-absolute paths. State the absolute source next "
              "to the deployed path either way, so a reader can find it to edit.\n"
              "    🔴 SHIPPING IS NOT RESOLVING, AND A DEVRC SKILL IS NOT EXEMPT. "
              "A devrc skill is READ from ~/.claude/skills/<name>/ by an agent "
              "whose cwd is some unrelated project, so a bare `reference/x.md` "
              "resolves against THAT cwd and the repo-relative "
              "`claude/skills/<name>/reference/x.md` resolves nowhere either — "
              "only `~/.claude/skills/<name>/reference/<topic>.md` opens. Gated "
              "since 2026-08-19 by scripts/tests/test_doc_path_rot.py (rule 1c), "
              "which fires EVEN WHEN THE FILE EXISTS.\n"
              "    🔴 That paragraph is about whether the file SHIPS. A REPO-LOCAL "
              "skill (a project's own .claude/skills/<name>/) always ships its "
              "reference/ dir, and still breaks here for a different reason: a "
              "bare `reference/<topic>.md` is resolved by the reader against the "
              "CWD, not against the skill's directory, so it is simply not found "
              "(measured on datapacket-talos app-blocks 2026-08-04). Write the "
              "repo-root-relative form `.claude/skills/<name>/reference/<topic>.md`, "
              "or keep short table entries and state the expansion once in a "
              "preamble — which is what app-blocks does, and why it does not warn "
              "here. All 3 repo-local skills with a reference/ dir use one of "
              "those two forms; none relies on a bare relative path.")
    if not any_ref_issue:
        n_refd = sum(len(a["refs"]) for a in audits)
        if n_refd:
            p(f"  {n_refd} referenced reference file(s), all resolve; no orphans ✓")
        else:
            p("  no skill routes to a reference/ sidecar yet — DEMOTE_TO_REFERENCE is "
              "unused here, which is why the cores carry everything")

    corpora = [a for a in audits if a["corpus_n"]]
    if corpora:
        p("\n## numbered-corpus integrity (the numbers are an API — never renumber)")
        for a in corpora:
            if a["corpus_dangling"]:
                p(f"  🔴 {a['name']}: {len(a['corpus_dangling'])} CITED BUT UNDEFINED "
                  f"— {', '.join('#' + str(n) for n in a['corpus_dangling'][:12])}")
            if a["corpus_dupes"]:
                p(f"  🔴 {a['name']}: {len(a['corpus_dupes'])} DUPLICATE number(s) "
                  "— the hallmark of a demote that renumbered per file: "
                  + ", ".join(f"#{n} in {x} and {y}" for n, x, y in a["corpus_dupes"][:6]))
            if not a["corpus_dangling"] and not a["corpus_dupes"]:
                p(f"  {a['name']}: {a['corpus_n']} numbered items, every citation "
                  "resolves, no duplicates ✓")

    broken_fence = [a["name"] for a in audits if not a["fence_ok"]]
    if broken_fence:
        p("\n## unclosed code fences (these skew every number above for that file)")
        for n in broken_fence:
            p(f"  🔴 {n}")

    p("\n## verdict")
    n_hard = sum(1 for a in audits if a["status"] == "OVER HARD CAP")
    n_over = sum(1 for a in audits if a["status"] == "OVER TARGET")
    n_tight = sum(1 for a in audits if a["status"] == "NO HEADROOM")
    if not over and not any_ref_issue and not broken_fence:
        p(f"  ✓ all {len(audits)} skill(s) within budget ({BUDGET:,} B enforced), "
          f"references intact — no prune needed (stop; do not churn the files)")
    else:
        need = []
        if n_hard:
            need.append(f"{n_hard} over the {HARD:,} B hard cap")
        if n_over:
            need.append(f"{n_over} over the {TARGET:,} B target")
        if n_tight:
            need.append(f"{n_tight} inside the {TARGET:,} B ceiling but past the "
                        f"{MIN_HEADROOM:,} B working margin — the gate REJECTS these")
        # Measured against the ENFORCED budget, not the ceiling: against the
        # ceiling a NO HEADROOM skill contributes a NEGATIVE number, which
        # silently cancelled out real overage elsewhere in the same run.
        excess = sum(a["size"] - BUDGET for a in over)
        if excess > 0:
            need.append(f"cut ~{excess:,} B total")
        if any_ref_issue:
            need.append("broken/orphaned reference routing")
        if broken_fence:
            need.append(f"{len(broken_fence)} unclosed code fence(s)")
        p("  ⚠ prune needed — " + "; ".join(need))


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True, description="audit SKILL.md byte budgets")
    ap.add_argument("paths", nargs="*", help="a SKILL.md, a skills dir, or a repo root")
    ap.add_argument("--all", action="store_true", help="list every skill, not just over-budget ones")
    ap.add_argument("--sections", type=int, default=10, help="sections shown per detail block")
    ap.add_argument("--detail", type=int, default=3, help="skills to render a detail block for")
    args = ap.parse_args(argv)

    paths = args.paths or [str(Path.cwd() / ".claude" / "skills")]
    targets = resolve_targets(paths)
    if not targets:
        sys.exit(f"no SKILL.md found under: {', '.join(paths)}\n"
                 "(pass a SKILL.md, a skills dir, or a repo root explicitly)")
    render([audit_one(t) for t in targets], args.all, args.sections, args.detail)


if __name__ == "__main__":
    main()
