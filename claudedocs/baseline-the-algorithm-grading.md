# t=0 baseline — grading `the-algorithm` (2026-09-15)

Measurement record for deciding, later, whether the `the-algorithm` skill
(merged `37b3bd07`, PR #1699, 2026-09-14) actually reduced guard
over-engineering — or whether it merely shipped.

🔴 **This is a RECORD, not machinery.** No timer, no test, no gate reads it.
That is deliberate: the skill's own step 5 says the fix for over-guarding is
never another guard, and a scheduled job watching guard growth **is** a
guard-audit guard. Re-run the method below BY HAND at a chosen date.

---

## 🔴 Read this first: the naive before/after design does NOT work here

The metric was **already improving before the intervention**. Same frozen
method, consecutive windows, all pre-merge:

| window | days | non-merge commits | guard-touching | guard-only | guard files added |
|---|---|---|---|---|---|
| 2026-05-19 → 06-09 | 21 | 16 | 0.0% | 0.0% | 0 |
| 2026-06-09 → 06-30 | 21 | 59 | 35.6% | 0.0% | 31 |
| 2026-06-30 → 07-21 | 21 | 101 | 49.5% | 0.0% | 38 |
| 2026-07-21 → 08-11 | 21 | 342 | **59.9%** ← peak | 3.2% | 164 |
| 2026-08-11 → 09-01 | 21 | 742 | 52.0% | 9.8% | 215 |
| 2026-09-01 → 09-15 | **14** | 511 | **37.6%** | 7.2% | 82 |

**Guard share peaked at 59.9% and fell for two consecutive windows — 59.9 →
52.0 → 37.6 — with the skill not yet in existence.** So a post-merge reading of
~37% or lower is **indistinguishable from the trend already in motion**, and
crediting the skill for it would be a straightforward misattribution.

⚠ Two things that make the ratios the only usable figures: commit volume grew
16 → 742 across this span, so absolute counts say more about repo activity than
about guarding; and the last window is **14 days, not 21**, so compare its
percentages, never its counts.

**What follows from this:** do not grade on the aggregate alone. The aggregate
can only *falsify* — a rise back toward 50%+ would be evidence the skill is not
working. It cannot confirm.

## The claims this baseline supersedes

🔴 **The figures in PR #1699's body and commit message are NOT reproducible and
should not be re-quoted.** They came from the handoff doc, were taken on trust,
and no method was recorded with them. Measuring the same corpus with a stated
method disagrees materially, in BOTH directions:

| metric (6w to 2026-09-15) | #1699 quoted | this method | direction |
|---|---|---|---|
| non-merge commits | 1,326 | 1,329 | same corpus ✅ |
| guard-touching commits | 407 (31%) | 622 (**46.8%**) | worse than claimed |
| commits touching no product file | 287 | 114 (**8.6%**) | far fewer |
| test : product LOC | 414k : 126k = **3.3:1** | 445k : 253k = **1.76:1** | better than claimed |
| Bash calls (14d) | 20,913 | 20,900 | same corpus ✅ |
| …that are test/gate runs | **41.2%** | **19.7%** | half the claimed rate |

The commit and Bash-call totals match to within 0.3%, so this is the **same
corpus classified differently** — not a different window. The prior numbers are
not wrong so much as **undefined**: without a recorded classifier, "31% of
commits are guard work" is not a measurement anyone can repeat. The
disagreement runs in both directions, so it is not a correctable bias either.

**Use the table below as t=0. Treat the #1699 figures as retracted.**

## t=0 values — 2026-08-04 → 2026-09-15, frozen method

```
A  non-merge commits          1329
A  guard-touching commits     622  (46.8%)
B  guard-only (no product)    114  (8.6%)
B  guard-file churn           +392092 / -34369  (net +357723)
C  tree LOC guard/product     445447 / 253067   ratio 1.76:1
C  tree files guard/product   491 / 537
D  guard files ADDED          324
E  Bash calls (14d)           20900
E  ...that are test/gate runs 4111  (19.7%)
```
⚠ **C is a property of the TREE, not the window** — it does not vary by window
and is only comparable across re-runs at different dates.

## Instrument for "did it fire" — validated

`find-session --skill` is the authoritative surface. Two others are **wrong and
will mislead**: `adoption-scan` sees only the 9 `invocation.py` emitters and
cannot see a skill at all; the ClickHouse `skills_used` map undercounts ~40% and
its usual predicate `JSONExtractString(...) IS NOT NULL` is **always true**,
silently returning whole-population statistics wearing a skill's name (see the
`activity` skill).

Validated 2026-09-15 with a positive control, because a zero from an instrument
wired to nothing looks identical to a real zero:

| | result |
|---|---|
| positive control — `--skill activity` | ≥10 sessions — the instrument observes |
| **under test — `--skill the-algorithm`** | **`No sessions matched` — a trustworthy 0 at t=0** |

```bash
python3 $DEVRC/scripts/find-session.py --skill the-algorithm
```
⚠ **Blind spot it names itself: `--skill` does NOT search the opencode corpus.**
The skill also ships to opencode as `/the-algorithm`, so firings there are
invisible to this measurement and a zero is a claim about Claude Code only.

## Grade two things, separately

1. **Does it fire?** `find-session --skill the-algorithm`. A zero makes question
   2 unanswerable — it does **not** mean "no effect".
2. **When it fires, does it change the outcome?** Read the sessions where it
   fired and ask whether the change that followed **deleted or declined**
   something. The skill predicts ~10% add-back, so an effective firing shows a
   net deletion or a guard argued down. **Ten legible cases beat one
   percentage** — and given the trend above, the percentage cannot confirm
   anyway.

## Kill criteria — set NOW so the result is falsifiable

The skill was not free: admitting it cost **nine other skills their mechanism
prose** against a 0-headroom listing ceiling. It should have to earn that.

- **Fires 0 times unprompted by 2026-10-27 (6 weeks)** → the description is not
  routing. Fix routing ONCE, or retire the skill and reclaim the listing budget.
- **Fires but no firing produces a deletion or a declined guard** → it is
  ceremony. Retire it.
- **Guard-touching share returns above ~50% for two consecutive 21-day windows**
  → evidence against, regardless of firing count.

⚠ **Confound, recorded now rather than rationalised later:** on the SAME DAY the
skill merged, the tier ledger was applied to both hosts, making 13 skills
name-only. Routing changed for everything at once, so any adoption shift after
2026-09-14 has two plausible causes.

## The frozen method

🔴 **Classification is frozen. Changing it invalidates every comparison above.**
A file is `guard` if its path contains `/tests/` or its basename matches
`test_*.py` / `*_test.py` / `*.test.mjs` / `mutation_*.py`; `docs` if it ends
`.md` or sits under `claudedocs/` or `docs/`; `product` otherwise. A commit is
*guard-touching* if it changes ≥1 guard file, and *guard-only* if it changes ≥1
guard file and 0 product files.

```python
#!/usr/bin/env python3
"""t=0 baseline for the-algorithm. RE-RUN VERBATIM to compare.
Usage: baseline.py <repo> <since-iso> <until-iso> [claude-transcript-dir]"""
import json, re, subprocess, sys, time
from pathlib import Path

REPO, SINCE, UNTIL = sys.argv[1], sys.argv[2], sys.argv[3]
TDIR = Path(sys.argv[4]) if len(sys.argv) > 4 else None

def sh(*a):
    return subprocess.run(a, cwd=REPO, capture_output=True, text=True).stdout

def kind(p):                                          # FROZEN
    if "/tests/" in p or re.search(
            r"(^|/)(test_[^/]+\.py|[^/]+_test\.py|[^/]+\.test\.mjs|mutation_[^/]+\.py)$", p):
        return "guard"
    if p.endswith(".md") or p.startswith("claudedocs/") or p.startswith("docs/"):
        return "docs"
    return "product"

log = sh("git", "log", "--no-merges", f"--since={SINCE}", f"--until={UNTIL}",
         "--pretty=format:%H", "--name-only")
commits, cur, files = [], None, []
for line in log.splitlines():
    if re.fullmatch(r"[0-9a-f]{40}", line):
        if cur: commits.append((cur, files))
        cur, files = line, []
    elif line.strip():
        files.append(line.strip())
if cur: commits.append((cur, files))

total = len(commits)
guard_c = sum(1 for _, f in commits if any(kind(x) == "guard" for x in f))
guard_only = sum(1 for _, f in commits if any(kind(x) == "guard" for x in f)
                 and not any(kind(x) == "product" for x in f))

ns = sh("git", "log", "--no-merges", f"--since={SINCE}", f"--until={UNTIL}",
        "--numstat", "--pretty=format:")
add = dele = 0
for line in ns.splitlines():
    p = line.split("\t")
    if len(p) == 3 and p[0].isdigit() and p[1].isdigit() and kind(p[2]) == "guard":
        add += int(p[0]); dele += int(p[1])

loc = {"guard": 0, "product": 0, "docs": 0}
nfiles = {"guard": 0, "product": 0, "docs": 0}
for p in sh("git", "ls-files").splitlines():
    k = kind(p)
    try:
        n = sum(1 for _ in (Path(REPO) / p).open("rb"))
    except Exception:
        continue
    loc[k] += n; nfiles[k] += 1

added = sh("git", "log", "--no-merges", f"--since={SINCE}", f"--until={UNTIL}",
           "--diff-filter=A", "--name-only", "--pretty=format:").splitlines()
added_guard = sum(1 for p in added if p.strip() and kind(p.strip()) == "guard")

print(f"WINDOW {SINCE} .. {UNTIL}")
print(f"A  non-merge commits          {total}")
print(f"A  guard-touching commits     {guard_c}  ({100*guard_c/total:.1f}%)")
print(f"B  guard-only (no product)    {guard_only}  ({100*guard_only/total:.1f}%)")
print(f"B  guard-file churn           +{add} / -{dele}  (net {add-dele:+})")
print(f"C  tree LOC guard/product     {loc['guard']} / {loc['product']}"
      f"  ratio {loc['guard']/max(loc['product'],1):.2f}:1")
print(f"C  tree files guard/product   {nfiles['guard']} / {nfiles['product']}")
print(f"D  guard files ADDED          {added_guard}")

if TDIR and TDIR.is_dir():
    cutoff = time.time() - 14 * 86400
    TESTRE = re.compile(r"pytest|run-tests\.sh|run-node-tests\.sh|gate\.sh|"
                        r"scoped-tests\.sh|node --test|nix build .*#checks|mutation_")
    nbash = ntest = 0
    for f in TDIR.glob("*.jsonl"):
        if f.stat().st_mtime < cutoff:
            continue
        try:
            for line in f.open(errors="ignore"):
                if '"Bash"' not in line:
                    continue
                try: rec = json.loads(line)
                except Exception: continue
                for c in (rec.get("message") or {}).get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "tool_use" \
                            and c.get("name") == "Bash":
                        nbash += 1
                        if TESTRE.search((c.get("input") or {}).get("command") or ""):
                            ntest += 1
        except Exception:
            continue
    print(f"E  Bash calls (14d)           {nbash}")
    print(f"E  ...that are test/gate runs {ntest}  ({100*ntest/max(nbash,1):.1f}%)")
```

Run as:
```bash
python3 baseline.py ~/workspace/devrc 2026-08-04 2026-09-15 \
  /home/zach/.claude/projects/-home-zach-workspace-devrc
```

⚠ **Metric E reads Claude transcripts only** and selects files by mtime, so it
is a 14-day window regardless of the `--since`/`--until` arguments. It is also
blind to opencode, like the firing count.
