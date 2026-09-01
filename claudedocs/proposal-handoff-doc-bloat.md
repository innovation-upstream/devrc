# `/handoff` output bloat — audit & proposed remedy (2026-08-29)

READ-ONLY audit. No repo file was edited; this report is the only file written.

---

## 1. Population sampled

**Intended method worked — no fallback used.** `find-session.py --skill handoff --limit 30` returned
**472 sessions** that invoked the handoff skill. Instrument controls, both run: positive
`--skill prune-skill` → **13** sessions (≠ 472, so it discriminates); negative
`--skill no-such-skill-xyz` → refused, `names no skill`, 0 sessions.

From the **30 most recent** of those 472 sessions I extracted the actual
`handoff_doc.py --repo … --topic <t>` invocation (the only step that lands a doc). That gave
**19 distinct topics**; 18 resolved to a doc I could measure. **So: sampled 18 of 413 committed
handoff docs (4.4%), selected by "a recent session actually wrote this doc", not by `ls -t`.**

Corpus denominator, enumerated (not sampled): `find <repo>/claudedocs -maxdepth 1 -name 'handoff-*.md'`
across **devrc (87) + homelab-talos (52) + datapacket-talos (274) = 413 docs**. Note the brief named
devrc + datapacket-talos as the two biggest corpora; **homelab-talos is the second-heaviest source in
the session sample** (6 of 18 docs) and is included.

🔴 4 docs were absent from the datapacket-talos primary clone's working tree but present on
`origin/trunk` (CLAUDE.md rule 10 — a stale clone makes a file look missing); measured from the ref.

---

## 2. Size distribution

**Sampled 18 docs** — total **654,499 B ≈ 163,600 tokens**, mean 36,361 B (~9,090 tokens/doc).
min 175 L / 12,740 B · **median 317 L / 32,911 B (~8,228 tok)** · max 1,129 L / 87,673 B (~21,918 tok).

**Whole corpus (413 docs, enumerated):** 7,731,823 B ≈ **1.93 M tokens**.
p50 12,159 B · p90 41,612 B · max **192,625 B (~48 k tokens)**. **45 docs > 40 KB; 103 > 20 KB.**

Worst offenders in the sample:

| bytes | lines | path |
|---|---|---|
| 87,673 | 1,129 | `/home/zach/workspace/devrc/claudedocs/handoff-tmux-webapp.md` |
| 68,268 | 453 | `/home/zach/workspace/homelab-talos/claudedocs/handoff-clickup-mirror.md` |
| 63,196 | 899 | `/home/zach/workspace/civit/datapacket-talos/claudedocs/handoff-cocry-tiering-2026-08-12.md` |
| 60,122 | 663 | `/home/zach/workspace/homelab-talos/claudedocs/handoff-session-makework-audit.md` |
| 50,794 | 442 | `/home/zach/workspace/homelab-talos/claudedocs/handoff-media-autoremixer.md` |
| 47,204 | 643 | `/home/zach/workspace/civit/datapacket-talos/claudedocs/handoff-etcd-cp-disk.md` |

A `/resume` on `handoff-tmux-webapp.md` spends ~22 k tokens before any work starts.

---

## 3. Growth over time — **monotonic; this is the load-bearing finding**

All 18 docs, every revision, line count at each (`git log --follow --reverse` + `git show <sha>:<path> | wc -l`):

- **123 revisions total. 121 grew or held; 2 shrank (1.6%).**
- First revision → latest: **3,033 → 7,748 lines, ×2.55**, +4,715 lines added after first write.
- Only 2 of 18 docs ever recorded a single decrease (`session-makework-audit`, `cairn`); **16 of 18 never shrank once.**

Two worked traces (12 and 13 revisions, zero decreases):
`tmux-webapp` 203→327→338→365→367→560→622→669→752→865→963→**1129 L**;
`cocry-tiering` 226→361→410→501→623→642→650→686→690→700→777→881→**899 L**.

`cocry-tiering` is dated **2026-08-12** and was still being appended to on **2026-08-27** — 15 days and
+673 lines after the doc's own topic date. Growth continues long after the work ships.

**Mechanism, written into the skill on purpose.** `SKILL.md:144`: *"Status header REPLACED, findings
APPENDED … `Open investigations`/`Findings`/`Gotchas` append and the earlier text survives **verbatim**,
even when your block supersedes an old one."* And the ranked-queue rule (`SKILL.md:95-103` + its
`shared-queue.md`) makes rank numbers a claim identity, so a completed item **must stay in place**
(`handoff-tmux-webapp.md`: *"A finished item stays in place marked ✅ DONE"*). Both rules are correct.
**There is simply no counterweight — nothing in the skill ever removes a byte.**

---

## 4. LIVE / DEAD-BUT-DATED / DURABLE-BUT-MISPLACED split

Method: H2 byte weights (exact), then per-bullet/per-item classification — a **regex heuristic,
spot-verified by reading headlines** (all 148 bullets of three `## Gotchas` sections were printed and
read). Floor/ceiling pairs given where matchers disagreed. **`<details>` blocks: 0 in all five docs** —
the bloat is plain prose, not collapsed.

### Per-doc H2 weights (exact bytes)

| doc | total | Gotchas | Next steps | Open investigations | State now + Goal + How-to-verify |
|---|---|---|---|---|---|
| tmux-webapp | 86,692 | 28,659 | 29,186 | 2,938 | ~17,000 |
| cocry-tiering | 62,552 | 13,535 | 24,746 | 12,119 | 11,900 |
| clickup-mirror | 67,379 | 26,132 | 6,464 | 24,123 | 10,400 |
| session-makework-audit | 59,430 | 30,847 | 3,197 | 12,110 | 12,700 (incl. a 6,434 B dated `## Session 2026-08-28` H2) |
| etcd-cp-disk | 46,676 | 11,010 | 14,813 | 12,857 | 7,500 |

**`## Gotchas` + `## Next steps` alone are 58–68% of each doc.**

### Category split (bytes, % of whole doc)

| doc | DEAD-BUT-DATED | DURABLE-BUT-MISPLACED | remainder = LIVE |
|---|---|---|---|
| tmux-webapp | 27,344 (6 of 9 ranked items ✅DONE, kept with full verification narrative) + 1,517 retracted gotchas = **28,861 B / 33%** | 8,515–18,845 B (16–33 of 56 gotcha bullets are generic zsh/git/mutation/audit-method lessons) = **10–22%** | **45–56%** |
| cocry-tiering | 6,542 (5 of 6 ranked items done) + 7,091 (4 of 8 investigation blocks resolved) + 1,085 = **14,718 B / 24%** | 2,045–3,802 B = **3–6%** | **70–73%** |
| clickup-mirror | 4,800 (3 of 13 items) + 1,710 + 2,944 = **9,454 B / 14%** | 10,334–12,622 B (28–30 of 62 bullets) = **15–19%**. 🔴 **By reading, far worse than the regex says: the 62 gotchas are Loki retention / Alloy config / Talos logging / cert-manager / Harbor / CI — essentially none are about "clickup-mirror".** ~55 of 62 belong in a homelab skill. Read-based estimate **~22,000 B / 33%** | **~35–50%** |
| session-makework-audit | 554 + 2,747 + 5,450 (13 RETRACTED gotcha bullets) + 6,434 (dated `## Session` H2) = **15,185 B / 26%** | 8,859–21,053 B (17–38 of 58 bullets: mutation batteries, audit ladders, `git rev-parse`, `pgrep -f`, worktrees) = **15–35%** | **39–59%** |
| etcd-cp-disk | 7,141 (4 of 16 items) + 2,035 + 3,015 = **12,191 B / 26%** | 1,440–4,129 B = **3–9%** | **65–71%** |

**Aggregate over the 5 largest (322,729 B):** DEAD-BUT-DATED **≈ 80,400 B (25%)**;
DURABLE-BUT-MISPLACED **≈ 31,200–60,400 B (10–19%)**; LIVE **≈ 56–65%**.
So **roughly 35–44% of the bytes a `/resume` pays for change nothing about what the next session does.**

Instrument honesty — a zero I positive-controlled: matching the generic-tooling regex against **bullet
first lines only** returned **0 misplaced bullets for `etcd-cp-disk`**; over whole bullets it returns
**8 (4,129 B)**. The zero was an artifact of my matcher. Both are reported above as the floor/ceiling pair.

Near-duplication is **not** the lever: fuzzy-matched repeated gotcha bullets are only **2–4%** of each
Gotchas section (`alloy validate enforces stability on COMPONENTS` appears twice verbatim in
`clickup-mirror`). Worth a MERGE_DUP pass, not worth a mechanism.

---

## 5. Does a remedy already exist? **No — and adoption of the nearest analogue is ~0.**

Searched `claude/skills/handoff/SKILL.md`, all 4 of its `reference/*.md`, `scripts/lib/handoff_doc.py`,
and `claude/skills/resume/SKILL.md` for `size|KB|byte|archive|prune|evict|shrink|budget|line count`:

- **No byte budget, no line budget, no archive instruction, no eviction step, anywhere.**
- The only size-shaped code in `handoff_doc.py` is the *wrong-base* tell (base vs mainline section/line
  counts) — a correctness check, not a budget.
- The entire counterweight is one prose sentence at `SKILL.md:166`: *"Keep the doc tight and
  high-signal … every line must earn its place"* — immediately followed by an explicit **exemption** for
  `Open investigations`. No number, no trigger, no gate; and prune-skill's own closing line says prose
  budgets do not hold.
- Deployed `~/.claude/skills/handoff/SKILL.md` is **byte-identical** to the devrc source (23,097 B,
  `diff` rc=0; a `/nix/store` copy, so a source edit needs `home-manager switch`).

**Adoption rate of the archive pattern in handoff docs: 9 of 413 = 2.2%** (positive control on the same
matcher: 252 of 413 contain `## Goal`). And **all 9 are false positives** — they *reference* someone
else's archive (`RULES-ARCHIVE.md`, `app-blocks-workstream-history-archive.md`, `MEMORY.md`'s
`ARCHIVE.md`). **Zero handoff docs have an archive sink of their own**
(`find claudedocs -iname 'handoff-*archive*'` → 0 files, in all three repos).

**The more interesting finding — the pattern is proven and simply never applied here.**
`~/workspace/devrc/claudedocs/close-the-loop/ARCHIVE.md` (171 L / 82,262 B) opens with:

> *"**This file is NOT auto-read.** `STATE.md` is the live ledger the skill reads first and updates
> last; this is where its shipped-and-verified narrative, superseded decisions and build history were
> moved (2026-08-01) so the live ledger stops costing ~105 KB of context on every run."*

That is a live ledger of **36,042 B** beside an archive of **82,262 B** — a 69% cut of a
read-every-run surface, done once, by hand, for one initiative, and never generalised.

🔴 **One conflict to name, because it decides the design.** `prune-skill` says: *"Never prune an
eviction SINK (`claude/RULES-ARCHIVE.md`, a `claudedocs/` doc) — ungated and demand-loaded."* Its
rationale is *demand-loaded*. **A handoff doc is the one `claudedocs/` file that is NOT demand-loaded —
`/resume` reads it in full, first thing, every time.** So it falls outside that exemption's reason, and
any remedy must send evicted bytes to a **new, non-handoff** `claudedocs/` file that then stays an
untouchable sink.

---

## 6. Proposed remedy — `prune-skill`'s actual method, one level down

`prune-skill`'s method, stated accurately: budgets (target 12 KB / hard cap 40 KB, owned by a test file
not by prose) → §0 staleness pass → §1 deterministic auditor (`skill-audit.py`: size vs budget,
per-section byte weights, dated-history blocks, fat lines, reference integrity both directions) →
§2 backup → §3 classify every over-budget block into EVICT_HISTORY / DEMOTE_TO_REFERENCE /
DROP_REDUNDANT / MERGE_DUP / KEEP_HOT, biased hard toward evicting → §4 routing-path correctness
(a bare `reference/x.md` resolves against the reader's CWD, unopenable) → §5 **one atomic rewrite built
by verbatim line-range slicing**, never retyping → §7 re-measure + a byte-cap test, because *"prose
budgets do not hold."* The adaptation below keeps that spine.

### (a) What changes in `claude/skills/handoff/SKILL.md`

1. **A budget section, mirroring prune-skill's "Budgets (the contract)".** Target **12 KB**, hard cap
   **40 KB** for a handoff doc — the same numbers, chosen because they already govern the other
   read-first surface and because corpus p50 is 12,159 B (i.e. **half the corpus already meets the
   target**; the cap bites 45 docs). Numbers owned by a caps file, not restated in prose.
2. **A new step 4.5, "Evict before you append"**, run *before* step 5's merge, with prune-skill's
   verdicts renamed to this domain:
   - **EVICT_HISTORY** → a ranked item marked ✅DONE/SHIPPED, a resolved `### ` investigation block, a
     RETRACTED gotcha, a dated `## Session <date>` H2 → move **verbatim** (line-range slice, never
     retyped) to `claudedocs/handoff-<topic>-ARCHIVE.md`, leaving **the numbered rank in place with a
     ≤200-char résumé + a pointer**. 🔴 The rank number must survive — it is half a `claim-work` slug
     identity, so deleting the line breaks live claims. This is the one adaptation prune-skill does not
     have to make.
   - **RELOCATE_DURABLE** (the handoff-specific verdict) → a gotcha that is a generic tooling/method
     lesson, not a fact about this initiative, goes to `claude/RULES.md`/`RULES-ARCHIVE.md` or the owning
     `.claude/skills/<name>/SKILL.md`, leaving one line. This is already the routing law in
     `datapacket-talos/CLAUDE.md` ("a new domain gotcha goes in the owning skill") — it is simply not
     wired into `/handoff`, which is where the gotchas are written.
   - **KEEP_HOT** → Goal, State now, open ranked items, unresolved investigations, How to verify.
3. **Delete the blanket exemption at `SKILL.md:166`.** Keep verbatim evidence for **unresolved**
   investigations (that exemption is right); a *resolved* one is history and gets evicted.
4. **Routing-path rule, inherited from prune-skill §4:** the pointer left behind must be written
   repo-root-relative (`claudedocs/handoff-<topic>-ARCHIVE.md`), never bare — datapacket-talos gate 0
   checks exactly this class.

### (b) Eviction trigger

Two, both mechanical, both evaluated at step 4.5 — no judgement call about "is this doc bloated":

- **Per-item (the primary):** an item becomes evictable the moment the *thing it describes* reaches a
  terminal state — a merged PR, a ✅DONE rank, a resolved investigation, a RETRACTED bullet. That is the
  same closing-condition test CLAUDE.md rule 11 already mandates for filed objects; it just fires here
  too. This is what stops accretion **after the work ships**, which section 3 shows is where all the
  growth is.
- **Per-doc (the backstop):** the merge is over target. `handoff_doc.py` already computes the post-merge
  line/section counts for its wrong-base tell — it can print `size: 47,204 B (target 12,288, cap 40,960)
  — N sections over budget` on the *proposal* run, in the same block as the existing
  `This replace DROPS N line(s) that look DURABLE` warning. **A warning, never a refusal**, exactly like
  its two siblings: refusing would block a legitimate mid-incident append.

### (c) Deterministic gate / ratchet — feasible, with a caveat

**Feasible, and there is a working template to copy: `scripts/skill-size-caps.txt` + gate 11.** That
gate ratchets a `SKILL.md` already over 12,288 B with a +8,192 B/push allowance, plus an **opt-in
per-path hard cap ledger** for files freshly pruned under target (the one shape the ratchet is blind to).

Proposed: extend gate 11's ledger to accept `claudedocs/handoff-*.md` paths. It would measure
**file bytes at the merge-base vs at HEAD for handoff docs named in the ledger**, delta-only like every
gate here — a doc already over budget warns, a *newly*-crossed cap blocks.

🔴 **Do not make it repo-wide.** Gate 11's own caps file records that the general rule ("a file under
target may not cross it") was **replayed over 365 days / 901 commits / 1,095 growth pairs and REFUTED**
— 42 fires, mostly trivial crossings, one commit blocked on four skills at once, i.e. a
train-people-to-click-through gate. **Rerun that replay against `claudedocs/handoff-*.md` before
widening** — I did not, and this is the one part of the proposal that is unverified.

A cheaper first move, no gate: a `handoff-audit.py` modelled on `skill-audit.py`, printing size vs
budget, per-H2 byte weights, evictable-item counts (✅DONE ranks, resolved `### ` blocks, RETRACTED
bullets) and dated-history H2s. Section 4 was produced with ~6 throwaway commands — that is the
auditor. Ship it, run it over all 413 docs, and let the distribution decide whether a gate is warranted.

**Expected saving, from section 4's split:** 35–44% of the read cost of the largest docs — on the
sampled 18 that is ~57–72 k tokens of the 164 k; corpus-wide, on the order of **0.7–0.85 M tokens**
across 413 docs, paid back on every `/resume`.

---

## 7. What I did NOT measure

1. **Whether the evicted bytes are ever re-read.** No transcript check for a session opening an
   archived/DONE section. If DONE narrative is regularly re-consulted, eviction-to-a-sink (still one
   `Read` away) is still right but the saving is smaller than stated.
2. **Actual `/resume` token cost.** Bytes/4, not tokenized; and I did not verify `/resume` reads the doc
   in *full* — the brief asserts it and I took that as given.
3. **The other 395 docs.** Category splits are from **5 of 413** (1.2%). The size distribution is
   enumerated over all 413; the LIVE/DEAD/MISPLACED split is **not**.
4. **The ratchet replay** — the 365-day backtest saying whether a handoff byte-cap fires usefully or
   trains click-through. The single highest-value unrun check.
5. **Item classification was regex + headline reading, not full reading.** Floor/ceiling pairs given
   where matchers disagreed; `clickup-mirror`'s ~33% is a read-based estimate and the softest number here.
6. **opencode-authored handoffs** — `find-session --skill` cannot see that corpus (it says so itself).
7. **Whether `handoff_doc.py` can cleanly slice-and-move**, i.e. whether step 4.5 is mechanisable inside
   it rather than by hand.
8. **Cross-repo sinks** — whether an evicted gotcha's true home (a skill, RULES.md) then blows *its*
   budget. The relocation could just move the cost.
