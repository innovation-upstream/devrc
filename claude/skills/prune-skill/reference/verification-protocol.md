# Verification protocol — content survival, gap audit, and control design

Routed from the prune-skill core (§7) — `~/.claude/skills/prune-skill/SKILL.md`, source `~/workspace/devrc/claude/skills/prune-skill/SKILL.md`. Load this when you are about to verify a prune. The core
carries the rules; this carries the protocol, the measured failures behind each rule, and a
working checker.

## Why the structural bar is not enough

A prune can pass **every** structural check — under target, all routing paths resolve, no
orphans, fences balanced, numbered corpus intact — while having silently destroyed content.
Both losses below did exactly that, in one campaign:

- `gitops-gate`: a block on why a PR can sit on one CI tier forever (`#859`/`#867`, the
  9-char-vs-40-char `head_sha` control, `refs/pull/<n>/merge`) was **summarised into the core and
  sliced into no sidecar**. Surfaced only as 7 lost backticked spans, 6 lost numbers, 2 lost refs.
- `heap-snapshot`: lines 150–153 (`# Heap Snapshot Capture` plus its intro) **fell between two
  slice ranges** and landed nowhere.

`skill-audit.py` cannot see either: its checks are `fence_balanced`, `reference_integrity`,
`corpus_integrity`, `dated_blocks`, `fat_lines`. None of them is a survival check.

## 1. Un-sliced gap audit (cheapest, catches the whole class)

Union your slice ranges, subtract from `1..EOF`, read what is left. Only frontmatter/H1/intro
that you deliberately carried into the core may be there.

```python
EOF_LINE=580                                       # wc -l of the ORIGINAL
sliced=[(14,120),(121,293),(294,441),(442,580)]     # your ranges
cov=set()
for a,b in sliced: cov.update(range(a,b+1))
gaps=[]; run=None
for i in range(1, EOF_LINE+1):
    if i not in cov: run = run or i
    elif run: gaps.append((run,i-1)); run=None
if run: gaps.append((run,EOF_LINE))
print("un-sliced:", gaps)
```

Ran clean on three consecutive prunes after it was introduced (`[(1,13)]`, `[(1,28)]`, `[(1,35)]`).

## 2. Enumerated-population survival check

Extract every concrete token from the **original**, assert each survives in the union of
(new core + every sidecar + anything you pointed to). **≥5 populations, one of which MUST be
numbers.**

🔴 **A coverage test is only as good as its populations.** A 4-population check (backticked,
paths, refs, names) reported *"65 of 66 covered"* and licensed a deletion. Adding a numbers
population surfaced **5 more real losses**.

Useful populations: backticked spans · path-like tokens · numeric constants · issue/sha refs ·
table row keys · whole non-blank lines.

## 3. Two instruments, because one is structurally blind

**Whole-line survival is load-bearing; token membership is not sufficient.** A substring-anywhere
membership test cannot see a row deleted from a sidecar when its tokens are also quoted in the
core. Measured, twice:

| Mutation | token membership caught | line survival caught |
|---|---|---|
| drop 6 table rows (`heap-snapshot`) | 2 | 5 |
| drop 8 IP-table rows (`add-node`) | 0 (row-keys) | 10 |

## 4. 🔴 Control design — the error that produced three false PASSes

**Validate the checker against a DESTINATION, never against your new text.** Dropping rows from
the freshly-written core exercises nothing and PASSES — it contains no original content. This
invalid control was produced by **three different actors in one session**, each initially
reading its PASS as a clean result.

A second invalid shape: deleting a token from one destination when the same token legitimately
lives in another. It passes because nothing was lost. Only removal across every copy goes red.

**Valid controls** — run several, report the number each produced:
- hide each sidecar in turn (expect losses proportional to its size)
- drop table rows **from a sidecar**
- mutate numeric constants **in a sidecar**
- for a dedup, hide the destination skill entirely

A control that PASSES is not a clean result — it is an invalid control. Say so and rebuild it.

## 5. Byte accounting

Take before/after from the **pushed blob**, not the working tree:
`git cat-file -s <ref>:<path>`. On a concurrently-edited file (`CLAUDE.md`) a rebase invalidates
your "before" — one pass reported `92,181 → 88,343` when the real parent blob was **93,692 B**,
because another session added 1,511 B between the measurement and the commit. Skill bodies are
rarely concurrently edited, so their figures are usually safe; always-loaded files are not.

## 6. What no check here covers

🔴 **None of this proves the pruned core is USABLE.** Structural integrity and content survival
are both about what is present, not about whether a reader can still do the task from the core
alone. After a campaign of 9 prunes, not one core had been exercised end-to-end on a real task.
If the skill matters, drive it once afterwards.

### What driving one actually found (2026-08-19, n=2)

Two pruned cores (`manage-postgres` 69,810→7,736 B; `image-cacher` 70,034→8,300 B) were each
given a real read-only production diagnostic by a fresh agent. **The structure held**: routing
was 4-for-4 and 3-of-5, neither agent listed the directory or grepped to find a file, and both
correctly skipped sidecars they did not need. Keep the design. Two things to carry forward:

🔴 **THE FAILURE MODE SLICING CAUSES — a warning demoted AWAY from the instruction it guards.**
Content survives, the path resolves, every gate passes; the **PAIRING** broke, and nothing in
this protocol measures pairings. `image-cacher`'s core (`SKILL.md:31`) prescribes gating on
bucket fractions `<=5s / >10s / >25s` and states the buckets as `0,5,10,25`. The warning that
Prometheus's labels are **`"30.0"`, not `"30"`** — and that a mistyped boundary "returns an
empty result, which reads exactly like a passing gate" — sits in
`reference/serve-original-transcode-and-deploys.md:37`, routed for *"serve-original,
transcode/VFE, or deploy-time 502s"*. Nothing about a duration query sends you there. The
agent wrote `le="5"`, got silence, and the rows vanished from a unioned table. **In one fat
body you scroll past the warning and are immune.** Milder sibling in the same skill: the core's
Redis hot-command prints saturation numbers with no interpretation, while the sidecar saying
*"it is NOT a 503 lever, r = 0.029"* is a routing hop away — the agent nearly filed it as an
availability contributor. **So: when you demote a block, ask what it was PROTECTING. If the
trigger stays in the core, the guard goes with it or leaves a cross-reference.**

🔴 **A lean core AMPLIFIES stale text.** Both agents said it independently: a wrong line in a
7.7 KB curated core reads as deliberate in a way it never did inside 70 KB of everything. The
prune does not cause the rot — a fat body carries it identically — but it removes the noise
that made you doubt it. This is the argument for §0 being a **pass you EXERCISE, not one you
read**: across the two runs, the defects that mattered (a role filter that misses the roles
that matter, a rate 40× off, a container limit 25% low, a cluster count, a `le` label) were
**every one of them findable only by measuring live**. Four rounds of adversarial doc audit on
this very skill never found its own dead ceiling pointer; using it did.

## 7. The report — say it, do not leave it in the transcript

The core's §7 requires a report, not just a verdict. Four things, all of which exist only in
your own scrollback otherwise:

- **before/after bytes** of the core, from the pushed blob (§5 above).
- **What moved where** — each demoted block and the sidecar it landed in. This is what makes the
  slicing auditable by someone who was not there, and it is the input to the next prune.
- 🔴 **§2's backup path** (`/tmp/skill-prune-<epoch>`). §2 builds the rollback and echoes the
  path once; if the report omits it, rolling a bad prune back means scrolling a transcript for a
  timestamped directory name. State it even when nothing went wrong.
- **The number each control produced** (§4 above) — the count, not the verdict. "The control
  passed" is the sentence that hides an invalid control.
