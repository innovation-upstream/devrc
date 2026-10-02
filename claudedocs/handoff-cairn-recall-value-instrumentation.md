# Handoff: cairn-recall-value-instrumentation — 2026-10-02

## Run this first — the index, one command
```bash
$DEVRC/scripts/cairn-ops/read.sh recall --repo "/home/zach/workspace/devrc"
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Make "was this cairn bullet actually USED" a mechanically answerable question, by
INSTRUMENTING the read path rather than mining transcripts for proxies. Two pieces: a recall
**receipt** (one telemetry row per invocation) and an opaque **per-bullet citation id** that
exists nowhere else in the corpus, so a later appearance of that token is evidence rather
than an echo.

🔴 **A SUCCESSOR ARC, NARROWLY SCOPED, AND THE PARENT IS CLOSED.**
`claudedocs/handoff-handoff-corpus-to-cairn.md` is **CLOSED — verdict NOT ADDRESSED**
(operator, 2026-10-02): its condition required an evicted block's own TEXT to reach cairn and
the transport cannot carry it. This arc inherits **only** that arc's rank 2. It does **not**
inherit the eviction/`--prune` question (shipped: rule (r)/(s), `--archive-write`), the
automigration question (**DECIDED: no**, four independent reasons), or the corpus-overlap
measurements (done, and they held).

- **closing-condition:** `check` — both clauses, run in one session:
  ```bash
  # (a) THE RECEIPT: a recall writes a row that the recall itself can be seen in.
  $DEVRC/scripts/cairn-ops/read.sh recall --repo /home/zach/workspace/devrc >/dev/null
  # then: the activity store returns >=1 row with source='tool' naming that invocation
  # (status, scope, entries, bullets_printed, output_bytes, session), where the same
  # query returned 0 cairn rows at round 1.
  # (b) THE CITATION ID: a printed bullet carries an opaque token, and the token is unique.
  $DEVRC/scripts/cairn-ops/read.sh recall --repo /home/zach/workspace/devrc \
    | grep -oE '\[cb:[0-9a-f]{4,}\]' | head -3     # non-empty
  ```
  🔴 **AND A POSITIVE CONTROL ON EACH, because a zero here is the exact failure mode this arc
  exists to escape:** for (a) the query must be shown returning a NON-ZERO count for a run
  that definitely happened — a reassuring 0 is indistinguishable from a query wired to
  nothing; for (b) the token must be shown ABSENT from the corpus before the recall and
  PRESENT after, or it is measuring its own output. ADDRESSED ⇒ arc CLOSED.
- ⚠ **FROZEN AT ROUND 1 — this line is the whole arc.** It deliberately does NOT require that
  the instrumentation prove the value claim. Shipping the instrument is the deliverable;
  reading it is a later arc with its own `n`. The parent arc spent three rounds unclosable
  because its condition encoded an outcome nobody could reach.

## State now
- ✅ **RANK 1 IS MERGED AND VERIFIED BY CONTENT — devrc#1983, squash `82d859ce`.** Supersedes
  this section's previous "built and IN FLIGHT". Verified on `origin/main`, not by ancestry:
  `scripts/cairn-receipt.sh` present, `nix/home.nix` carries
  `source = cairnWithReceipt;`, `scripts/testlib/nix_home.py` present,
  `scripts/tests/test_cairn_receipt.py` present, and **0** env-shebang literals left in the
  test stubs. Final gate: `collected=25017 passed=25009 skipped=8 failed=0`, all four legs.
  Claim `cairn-recall-value-instrumentation-1` RELEASED.
- ✅ **SUPERSEDED 2026-10-02 — THE SWITCH HAS RUN ON THIS HOST, AND CLOSING-CONDITION CLAUSE (a)
  IS MET HERE.** The previous text of this bullet read *"MERGED IS NOT RUNNING, AND THIS IS THE ONE
  GAP THAT MATTERS"*. On **this** host it IS running: `~/.local/bin/cairn` → `readlink -f` →
  `/nix/store/pf3pm…-cairn-with-receipt`, and one `read.sh recall --repo $DEVRC` took the spool
  **0 → 1 lines**, the row decoding to `source=tool kind=invocation`, `text=cairn`,
  `payload={"tool":"cairn","outcome":"ok","verb":"recall","repo":"/home/zach/workspace/devrc"}`
  — i.e. a row naming THAT invocation, which is what clause (a) asks for. **Positive control
  carried:** a `cairn -verbs` that MUST produce a row produced one (`exit_code=0 duration_ms=5`),
  so the figure under test sits beside a non-zero that moved.
  ⚠ **Scope: THIS host only, and that is the whole of the claim.** The other host is unmeasured,
  which is what rank 3 still owns. "Deployed-and-verified" is claimable for this machine and for
  no other.
- ⚠ **WHAT THE SUPERSEDED BULLET GOT RIGHT, KEPT BECAUSE IT IS STILL THE MECHANISM.**
  `.local/bin/cairn` is a `home.file`, so a merge writes nothing until `scripts/ship.sh` runs —
  which is exactly why the host-by-host scope above is load-bearing rather than pedantic. The
  hand-verification of the BUILT artifact stands as recorded: real client, real
  `~/.config/activity-collector/emit`, rc **0**, stdout byte-identical (134 B on `cairn -verbs`),
  stderr byte-identical, one row landed, and a failing client recorded `outcome=error` with
  `exit_code=11` intact.
- ✅ **THE PARENT ARC IS CLOSED — `handoff-handoff-corpus-to-cairn.md`, verdict NOT ADDRESSED**
  (operator, 2026-10-02; devrc#1980 `dc410367`). Its one item: the evicted block's own text
  cannot reach cairn. Only rank 2 was re-homed here.
- ✅ **devrc#1985 `ed2637c5` MERGED — rank 2's design record.** ⚠ Merged **without a gate
  verdict**, at operator direction ("skip gate") after all four legs returned `NO CAPACITY`.
  What stands in its place: the four disclosure gates + `test_handoff_doc_size.py` run
  locally on that exact tree (**240 passed**), and the retrigger commit verified
  byte-identical to the tested one. Doc-only diff, one file.
- ✅ **THE PLACEMENT DECISION IS TAKEN — OPERATOR RULING, 2026-10-02: EVERY BULLET, EVERY READ.**
  This closes what was rank 1 and unblocks rank 2. Of the three options put to the operator —
  (a) every bullet on every read, (b) an opt-in flag, (c) abandon clause (b) and keep only the
  receipt — **(a) was chosen and (b) was explicitly REJECTED**, on the ground that a flag
  reinstates the measured ~6% selection bias rank 1 was moved to the bin seam to escape.
  🔴 **AND THE COST OBJECTION WAS RETRACTED BY THE SESSION THAT RAISED IT.** Re-measured: the ids
  add **~2,114 B to a 192,047 B read ≈ 1.1%** (~560 tokens). The `## Next steps` text that called
  this *"the single biggest cost surface in the system"* and the investigation block's
  *"~1,660 B … 97.6% of a ~50,000-token `/resume` read"* are the OVERSTATEMENT — they priced the
  ids against a single 151-bullet entry rather than against a whole read. Do not re-derive the
  tension from them.
- ⚠ **RANK 2 IS IN FLIGHT, NOT BLOCKED — `ZacxDev/cairn#174` IS ITS PR A, AND THE WORK IS SPLIT
  THREE WAYS.** PR A (**OPEN**, `feat/bullet-citation-id`, +708/−4, `mergeStateStatus CLEAN`):
  the id derivation and the bullet START-LINE that makes an id placeable —
  `internal/store/journal.go`, `lib/subsystem_resolver.py`, plus
  `internal/store/citationid_test.go`, `internal/store/testdata/citation_ids.json`,
  `tests/citation_ids.py`, `tests/test_citation_ids.py`, `flake.nix`. PR B: the two renderers.
  PR C: the devrc pin bump. Claim `cairn-recall-value-instrumentation-2` still HELD (rc 12).
  🔴 **PR A IS THE SESSION'S FIRST PUBLIC-REPO CHANGE AND A REAL LEAK WAS ALREADY SHIPPED INTO IT
  ONCE, CAUGHT ONLY BY `tests/leakscan.py`.** Its output also becomes a CONTRACT the moment ids
  print — changing the derivation afterwards invalidates every id ever emitted — so it gets a
  round 0 and the nine axes before merge, not a glance.
- 🔴 **8 HEX, NOT 4 — SETTLED ON A MEASUREMENT.** A 3,129-bullet corpus at 16 bits collides with
  probability ≈1 (birthday `p ≈ n²/2^(b+1)`); 32 bits gives ≈0.1% corpus-wide. The closing
  condition's own regex already permits it (`[0-9a-f]{4,}`), so this needs no change there.
- ⚠ **FIVE PRs LANDED THIS SESSION, AND TWO OF THEM WERE NOT THIS ARC'S WORK** — devrc#1974
  `36d06188` (the parent's session close), devrc#1979 `8f36cb82` (a routable public IP scrubbed
  out of a PUBLIC repo, landed by #1972 inside the very S1 finding whose claim is that
  publishing that origin bypasses Cloudflare), devrc#1980, devrc#1983, devrc#1985.
- ⚠ **MY WORKTREES AND LOCAL BRANCHES ARE CLEANED UP** (five of each removed); the base clone
  is on `main`, **3 behind `origin/main`** as of 2026-10-02T22:54Z (was 1 when this was written —
  other sessions keep landing) — `git -C $DEVRC merge --ff-only origin/main` before any work.
  The count is a snapshot, not a fact; re-read it rather than trusting this line.
- ⚠ **TWO UNTRACKED DOCS ARE STRANDED IN THE devrc BASE CLONE AND THEY ARE NOT THIS ARC'S** —
  `claudedocs/scope-chief-{model-selector,situational-awareness}-2026-09-19.md`. Flagged because
  unsaved docs in a shared tree are one routine `checkout` from silent, unreported deletion. Not
  acted on: committing another session's work is not this arc's call.
- **No clawgate task.** `clawgate_handoff.sh resolve` printed `NOTHING RESOLVED — 0 tasks`
  (rc 5), and `field <doc>` returned rc 1 (none present). An unknown session id answers 200
  with an EMPTY ARRAY, so that zero cannot separate "touched no task" from "wrong id". **No
  field written, none created.** Not a clean bill of health.
- **Carried forward — a REPLACE heading would drop these values.** `source='tool'` held **0**
  cairn rows against **1,425** transcript-proven invocations; `read.sh recall` is **93 of
  1,425 (~6%)** of real traffic and **746 of 1,425** carry no parseable status line because of
  `| head`/`| tail`; the naive echo proxy fires **451/454 = 99.3%**; the counterfactual needs
  **≈510/arm** (message count) and **≈10,751/arm** (duration) against a small arm of **68**.
  Transcript span measured **2026-10-01**: 2026-09-01 → 2026-10-01, rolling at 30 days.

## Open investigations — live diagnosis state

### 🔴 OPEN — every proxy available today is a SPELLED guard, and the naive one is saturated by a mandate rather than by use
- as-of: 2026-10-01
- **Observed (with values):** the naive proxy "a printed ref reappears later in the session"
  fires **451/454 = 99.3%** — because `/resume` step 5 *requires* the report to carry what
  step 4 recalled. It measures COMPLIANCE with the skill, not use of the bullet. `via: measurement`
- **Observed — the discriminating observable, and it is narrow:** a rare (`df<=3`) *path*
  absent from the transcript BEFORE the recall, later appearing in a **read-shaped** tool
  input. That is what yielded the 61 / 23.5% figure. `via: measurement`
- **Ruled out: the count as first computed (151).** Three artifact classes inflated it, each
  invisible to the count — recall output spilled to a `tool-results/` file the session then
  greps (re-reading recall itself); bare `SCREAMING_CONST` tokens matching unrelated Go
  strings; paths inside `Edit`/`Write` bodies, which are echo into an artifact rather than
  use. `via: measurement`
- 🔴 **Ruled out: that the counterfactual is reachable by sampling.** The `scope-absent`
  control arm is **n=1** (9 corpus-wide) — empty by SELECTION, not sampling. The only
  available comparator (344 ran recall / 68 skipped) needs **≈510/arm** to see a 10% shift in
  message count and **≈10,751/arm** for duration; the small arm has 68. The outcome variable
  is a 19-hour session while the effect is one work item. `via: measurement`
- 🔴 **Ruled out: the parent arc's own next probe.** *"`find-session --skill resume` then ask
  whether the bullet changed what the session did"* cannot falsify anything — that surface
  disagrees with the transcripts by 24%, returned a session that merely READ the skill as its
  top hit, and has no opencode attribution. Do not re-run it. `via: measurement`
- **Leading hypothesis:** a spelled proxy is walkable by rewording and a mandated echo
  saturates it, so no transcript-mined observable can carry this claim. An opaque token minted
  per printed bullet is STRUCTURAL: it cannot be reworded into existence, and its appearance
  anywhere else in the corpus has exactly one explanation.
- ✅ **ANSWERED 2026-10-02 — this block's "Next probe" is DONE, and the answer is YES.** It asked
  whether the activity path reachable from `scripts/cairn-ops/read.sh` can emit a `source='tool'`
  row at all, with a positive control. It can, measured on this host: spool **0 → 1** across one
  `read.sh recall`, the row naming `verb=recall repo=/home/zach/workspace/devrc`, beside a
  `cairn -verbs` positive control that also produced one. Clause (a)'s instrument is exercised;
  the parent arc never did it. **Nothing here bears on the SPELLED-guard finding above**, which is
  what the rest of this block is about and which remains open.
- **Next probe:** none standing for the instrument. The block's own open question is unchanged —
  no transcript-mined observable can carry the value claim, which is why the opaque token exists.

### 🔴 OPEN — the receipt's measurement surface has FOUR known traps now, and every one produced a confident wrong number before it was understood
- as-of: 2026-10-02
- **Observed:** `session-summary` rows all pin `ts` to the SESSION START, so
  `argMax(payload, ts)` is a **tie** and returns an arbitrary snapshot. Measured: a session
  read as 26 assistant messages / 2 minutes; its final snapshot says **1,968 / 2,098** — a 75×
  understatement that looked entirely plausible. Key on
  `argMax(payload, JSONExtractString(payload,'end_ts'))`. ⚠ This belongs in the `activity`
  skill and is recorded here because this arc will hit it first. `via: measurement`
- 🔴 **Observed — and the first version of this bullet was the OPPOSITE and WRONG.** The
  `activity` skill's SOPS reader-cred recipe **works**. A prior round recorded it as broken on
  this host; the real cause was `--input-type yaml` placed **after** the `<(git show …)`
  positional, which `sops` ignores before failing to parse ciphertext as JSON — and it prints
  the diagnosis in as many words (*"Flags must always be provided before the first positional
  argument!"*) into a `2>/dev/null`. A/B: flag AFTER ⇒ unmarshal error; flag BEFORE ⇒
  30-character secret. **Do NOT reach for the collector's `activity_writer` file as a
  workaround, and do not "fix" the skill — put the flag before the positional.**
  `via: measurement`
- 🔴 **Observed 2026-10-02 — TRAP 3, AND IT IS THE B64 GOTCHA'S MIRROR IMAGE ON THE READ SIDE.**
  This doc already records that `emit` base64-encodes `b64:` values itself, **so pass plaintext**.
  The read-side consequence was NOT recorded and it cost a wrong answer: `grep -c 'cairn'` over the
  spool returned **0** while a cairn row was sitting in it, because the field is
  `b64:text=Y2Fpcm4=`. The reassuring zero arrived beside a correct `source=tool` count of 1, which
  is what made it believable — one true number vouching for a false one.
  **Decode before you grep**, or match on `source=tool` and decode `b64:text=`/`b64:payload=` per
  line. One command that works:
  `grep 'source=tool' "$SPOOL" | grep -oP 'b64:payload=\K[A-Za-z0-9+/=]+' | base64 -d`.
  ⚠ Generalises past this arc: **every** `b64:` field in the spool is opaque to a literal grep, so
  any consumer written against the line format needs the decode and `adoption-scan.py`'s
  tool-name-in-`text` convention exists precisely so a consumer can group WITHOUT it. `via: measurement`
- 🔴 **Observed 2026-10-02 — TRAP 4, AND IT INVALIDATES THIS DOC'S OWN RANK-3 RECIPE BELOW.**
  `~/.local/state/activity/spool/current.log` is a **TRANSIENT BUFFER, NOT A LEDGER** — the
  collector drains it. Measured across three consecutive commands in one session: the file
  existed (317 B, 1 `source=tool` row), then `cat` reported **no such file**, then it existed
  again at 1,024 B. Nothing was wrong; it had been drained and recreated.
  🔴 **So a `grep -c source=tool` on the spool is a reading about the last few seconds, and a zero
  read a minute after the run is indistinguishable from a receipt wired to nothing** — which is
  the exact failure this whole arc exists to escape, reproduced by the arc's own verification
  recipe. **Measure as a DELTA across the run, in one command**: count lines, run the client,
  count again. Or query the activity store downstream, where rows are durable. The `## How to
  verify` block below is corrected accordingly. `via: measurement`
- **Leading hypothesis:** all four traps are read-path only; none blocks emitting a row, and the
  receipt itself is sound. They are recorded so the arc's first query is not the one that gets
  believed — and traps 3 and 4 are both the same shape as traps 1 and 2: **a plausible number
  produced by an instrument nobody validated first.**
- **Next probe:** none standing — apply all four when writing clause (a)'s query.

### 🔴 OPEN — rank 2 cannot be built as specified: there is NO per-bullet render site, and the obvious workaround converts a validator warning into read-surface DATA LOSS
- as-of: 2026-10-02
- **Observed (with values) — the structural fact.** Both renderers emit an entry's section
  bodies **line-by-line verbatim**, never per bullet. Go:
  `internal/report/text.go` — `for _, line := range pytext.SplitLines(body)` under
  `for _, heading := range SurfacedHeadings`. The body is never parsed into bullets on the
  read path; `store.ParseJournalBullets` is called only to COUNT populations for the index
  row. cairn's `AGENTS.md` states the same property in its own words — *"the CLI/pod renderer
  prints section bodies verbatim"*. So there is no site at which a per-bullet token could be
  appended by the existing code shape. `via: code`
- 🔴 **Ruled out — re-emitting the body FROM `ParseJournalBullets`, which is the obvious
  implementation and is the dangerous one.** That converts "prints verbatim" into
  "reconstructs from a parse", and this repo already records that parser's `dropped-lines`
  defect: text preceding the first bullet is DROPPED, and a bullet whose opening line was lost
  or indented is absorbed into the bullet above it. Today those are an advisory validator
  finding (`dropped-line`, exit 0). Under a reconstruction they become **silent deletion on
  the read surface** — measured history: 7 historical versions across two `datapacket-talos`
  entries carried dropped lines for 2–8 days with `--validate` green throughout. `via: code`
- **The design that survives, and why it is safe:** parse for **IDs ONLY**, then emit the body
  verbatim exactly as now, appending ` [cb:xxxx]` to each line that is a matched bullet's
  OPENING line. Every other byte is untouched, including text the parser cannot reach — such
  text simply gets **no id**, which is a degradation in coverage rather than in content. State
  that explicitly wherever it lands: a missing id is not evidence a bullet was not printed.
- **Observed — the id must be 8 hex, not the 4 this doc's example used.** 3,129 store bullets
  at 16 bits collide with probability ≈1 (birthday: p ≈ n²/2^(b+1), so 4 hex is ~50% by
  ~300 bullets); 32 bits gives ≈0.1% over the whole corpus. The closing condition's regex
  already permits it (`[0-9a-f]{4,}`). Derive from `sha256` over the bullet's normalised text
  — available and identical in both languages, which is what makes byte-for-byte agreement
  reachable. `via: measurement`
- **Observed — the blast radius, counted rather than estimated:** `tests/conformance/golden`
  holds **128 files / 592 KB**, of which **25** carry rendered bullets and would move;
  `internal/report/testdata/reader_fixtures.json` is **325,085 B** and must be REGENERATED and
  diffed (hand-editing it is a declared failure). Then five gates must agree — `go test`,
  `tests/conformance/run_go.sh`, `tests/conformance/suite.py run` (the oracle, 0 failures),
  `tests/parity/harness.py`, `tests/dualrun/` — and only THEN the devrc pin bump plus every
  devrc guard that pins the pinned client's bytes. `via: measurement`
- ⚠ **The cost this doc never priced, and it points the other way from the whole arc.** A
  printed id is ~11 B per bullet: **~1,660 B on the 151-bullet entry**, which the parent arc
  measured as **97.6% of a ~50,000-token `/resume` read**. Rank 2 therefore ENLARGES the
  single biggest cost surface in the system in order to measure whether that surface earns its
  keep — and the parent arc's own shipped remedy (cairn#168's `⚠ OVER 30 nuance` badge) exists
  to warn about exactly that size. Not a reason to abandon it; a reason the operator should see
  the number before it ships. `via: measurement`
- **Next probe:** decide the id's PLACEMENT against that cost — every bullet on every read, or
  only under an opt-in flag (which reintroduces the ~6% selection problem rank 1 was moved to
  the bin seam to escape). That is an operator call, not a measurement, and it is upstream of
  writing any renderer code.

### 🔴 OPEN — the Tekton gate produced THREE distinct non-code failure modes in one day, and only one is documented
- as-of: 2026-10-02
- **Symptom + exact repro:** a devrc PR's four legs report `fail`, and nothing is wrong with
  the code. Read the raw statuses, never `gh pr checks` alone:
  ```bash
  H=$(gh pr view <n> --repo innovation-upstream/devrc --json headRefOid --jq .headRefOid)
  gh api repos/innovation-upstream/devrc/commits/$H/statuses \
    --jq '.[] | "\(.context) \(.state) created=\(.created_at) — \(.description)"'
  ```
- **Observed (with values) — mode 1, CAPACITY STARVATION.** All four legs:
  *"NO CAPACITY: <leg> — the gate never started (queued past its deadline). Not a code
  failure."* Seen on devrc#1974 after **65 minutes** pending, and again on devrc#1985. The
  gate SAYS it is not a code failure; that sentence is the discriminator. `via: measurement`
- **Observed — mode 2, A DUPLICATE-TRIGGER RACE THAT LEAVES NO VERDICT.** devrc#1983's head
  `dcdf33c8` received **two** pending status sets three seconds apart (17:03:29 and 17:03:32,
  contexts duplicated), then at 17:04:08–11 all four were marked
  `error — superseded by a newer run or a closed pull request — this commit was not
  validated`. Two runs for one commit superseded each other and the commit ended with **no
  verdict at all**. 🔴 I read those four `fail` rows as a code failure first; the timestamps
  are what refuted it. `via: measurement`
- **Observed — mode 3, A MERGED COMMIT THAT WAS NEVER VALIDATED.** `2a3168ae` and `82b4c6e3`
  both carry `superseded … this commit was not validated` on all four legs. **A superseded
  commit has no verdict**, so "it merged, therefore it was green" is unfounded for those.
  `via: measurement`
- 🔴 **Ruled out: that any of the three is a code signal.** In every case a byte-identical
  re-push went green — devrc#1974 first try, devrc#1983 first try. Tree identity was proven
  with `git diff --stat <old> HEAD` empty, not assumed. `via: measurement`
- ✅ **RESOLVED 2026-10-02 — THE SKILL NOW COVERS ALL THREE; devrc#1989, squash `ae1e3b6e`.**
  Verified by content on `origin/main`: `claude/skills/tekton/SKILL.md` **gotcha 12** carries the
  duplicate-trigger race with its own timeline (*"17:03:29–32, every context duplicated"*, then
  all four overwritten at 17:04:08–11 with `superseded by a newer run or a closed pull request —
  this commit was not validated"`) and the superseded-no-verdict state naming `2a3168ae` and
  `82b4c6e3`, with the ruling that **"it merged, therefore it was green" is unfounded for them**.
  This supersedes the bullet's previous text, which read *"It documents **neither** the
  duplicate-trigger race **nor** the superseded-no-verdict state"* — true when written, false now.
  ⚠ What still stands from it: the skill already documented the congestion mode (one node, no
  concurrency control, *"it heals when the queue drains"*, the `ExceededNodeResources` tell), and
  **I re-derived that from scratch before reading the skill** — the avoidable half, and the same
  shape as this doc's own closing finding about not recalling before working. `via: doc`
- **Leading hypothesis:** modes 2 and 3 are one mechanism — whatever marks a run superseded
  fires on a duplicate trigger for the SAME sha, not only on a newer sha. If so the fix is in
  the EventListener/dedup, and the operator-facing remedy is unchanged (re-push).
- **Next probe:** `tekton` skill, then the EventListener logs for `dcdf33c8` — did two
  PipelineRuns get created for one push event, and does the supersede check compare SHAs or
  run ids? That is the one question that separates "duplicate webhook" from "supersede logic
  too eager".

### 🔴 OPEN — three guards in this branch were tripped by PROSE ABOUT THEM, which is one rule in three costumes
- as-of: 2026-10-02
- **Observed — site 1, the deploy placeholder (the one that would have shipped broken).**
  `cairn-receipt.sh`'s guard spelled `@CAIRN_REAL@` in its own `case` pattern. `substitute`
  rewrites EVERY occurrence, so the DEPLOYED file compared the real store path against the
  real store path, matched, and refused **every** `cairn` invocation at exit 70. The checkout
  copy passed its own smoke test throughout — there the placeholder is still a placeholder.
  Only the BUILT artifact shows it. Matrix: **16 failed → 19 passed**. `via: measurement`
- **Observed — site 2, the hazardous-binary ledger.** `test_no_real_launchers.py` is a TEXT
  scan and says so: *"naming a binary in order to promise you never call it is
  indistinguishable from calling it."* `cairn-receipt.sh` tripped it on a binary it never
  invokes, named in ONE error string; the reworded remedy then tripped it on a SECOND binary,
  named in the comment explaining the trap. `via: measurement`
- **Observed — site 3, the runtime shebang.** `test_runtime_shebangs.py` is a SOURCE scan;
  the helper written to strip a shebang tripped it on its own `startswith("#!")` while
  writing no shebang at all. `via: measurement`
- 🔴 **Ruled out, AND THIS CORRECTS A CLAIM I PUBLISHED: that site 3 was two-tier blindness.**
  I first reported *"local passed because this dev host HAS `env`"*. **False** — it is a source
  scan, and the full local `scripts/tests` target DID catch it, naming all three sites at
  once (`collected=16101 passed=16094 skipped=6 failed=1`). The real cause is the same as the
  two rounds before it: I ran a SUBSET. `via: measurement`
- **The rule, stated once:** *a guard that must RECOGNISE a token cannot SPELL that token in a
  file something scans or rewrites.* All three sites now assemble the token from adjacent
  literals and say why.
- **Next probe:** none for the sites — all three are closed and guarded. The open question is
  whether this class deserves a meta-guard (a scan for test/script files that spell a token
  their own scanner matches), which is a devrc question and nobody's rank yet.

## Next steps (ranked)

✅ **THE FORMER RANK 1 — "OPERATOR DECISION: WHERE THE CITATION ID IS PRINTED" — IS ANSWERED AND
IS GONE FROM THIS LIST.** Ruling: **every bullet, every read**; the opt-in flag REJECTED. The
cost figure it was argued on (*"~1,660 B … 97.6% of a ~50,000-token read"*, *"the system's biggest
cost surface"*) was **retracted** — re-measured at ~2,114 B on a 192,047 B read ≈ **1.1%**. Record
in `## State now`. Do not reinstate the tension.

1. **AUDIT AND MERGE `ZacxDev/cairn#174` — PR A of rank 2, and the arc's first PUBLIC-repo change.**
   Round 0 first (it may legitimately challenge the A/B/C split), then the nine axes, then merge on
   green. It ships the id derivation and the bullet START-LINE that makes an id placeable.
   🔴 **Two reasons it earns a full ladder rather than a read-through:** its output becomes a
   **CONTRACT** — once ids print, changing the derivation invalidates every id ever emitted — and a
   real leak was already shipped into this branch once, caught only by `tests/leakscan.py`. Run that
   gate **unpiped** and read its exit code directly; **2 means "could not vouch", never "passed"**.
   🔴 The normalisation is the highest-value thing to attack: nobody has ruled on what the hash
   covers, so reflow, trailing whitespace, a `RESOLVED:` prefix, the `[cairn: zach/<uuid>]` suffix
   stored bullets already carry in their TEXT, and an edited bullet each need a stated answer.
   forcing: gate — a contract-forming derivation is cheap to change before it prints and
   impossible after, so the audit has to precede the merge rather than follow it.
2. **BUILD THE RENDERER — PR B.** Repo: **cairn** (public). Design settled — do NOT re-derive it,
   and do NOT implement the obvious version: parse for IDs ONLY, emit the body verbatim as now,
   append the id to each matched bullet's OPENING line. `sha256`-derived, **8 hex not 4** (3,129
   bullets at 16 bits collide with probability ≈1). Files: `internal/report/text.go` +
   `lib/subsystem_recall.py` (the `SurfacedHeadings` loop and its Python counterpart at ~:3145),
   then regenerate `internal/report/testdata/reader_fixtures.json` (**325,085 B**, regenerate and
   diff — hand-editing is a declared failure) and the **25 of 128** conformance goldens that
   carry rendered bullets; then five gates must agree — `go test ./... -count=1`
   (🔴 **`-count=1` is load-bearing: `ok (cached)` is not a run**), `tests/conformance/run_go.sh`,
   `tests/conformance/suite.py run`, `tests/parity/harness.py`, `tests/dualrun/`.
   forcing: deadline — the transcript corpus rolls at 30 days (measured 2026-10-01:
   2026-09-01 → 2026-10-01, nothing older, no `cleanupPeriodDays`), so the evidence any
   attribution study would use is deleted daily and waiting does not grow `n`.
3. **PR C — THE devrc PIN BUMP, plus every devrc guard that pins the pinned client's BYTES.**
   Expect devrc `main` to redden: that is exactly how cairn#162's `tasks`→`refs` rename did it.
   🔴 **ENUMERATE the readers, never guess which suites are adjacent** —
   `find scripts/tests -name '*.py' -print0 | xargs -0 grep -ln '<thing>'`, with **plain `grep`**
   after `xargs` (`command grep` is a builtin `xargs` cannot exec: exit **127**, no output,
   indistinguishable from a clean zero). Guessing adjacency cost three CI rounds on devrc#1983.
   And devrc's suite runs only via `scripts/run-tests.sh --targets "<exact target>" <root>` — a
   hand-rolled `pytest -p testlib.…` dies on `sys.path` and a `| tail` hides it as rc 0.
   forcing: gate — the pin is what makes the ids reach the surface `/resume` actually reads, so
   until it lands the renderer is merged and inert.
4. **VERIFY THE RECEIPT ON THE *OTHER* HOST — the only remaining half of that gap.**
   ⚠ **Re-scoped 2026-10-02: this host is DONE** (see `## State now`), so what is left is the
   second machine and nothing else. `scripts/ship.sh` there, then confirm a real `cairn recall`
   leaves a row: `ACTIVITY_SPOOL_DIR` default `~/.local/state/activity/spool/current.log`,
   `source=tool` + `kind=invocation`.
   🔴 **Measure it as a DELTA, not as a grep — the spool is DRAINED by the collector** (trap 4).
   Count lines, run the client, count again, in ONE command; a count taken a minute later is a
   reading about the last few seconds. 🔴 **And decode before matching: `b64:text=`/`b64:payload=`
   are base64, so a literal `grep 'cairn'` returns a confident 0 over a row that is right there**
   (trap 3). Carry the positive control either way: a run that MUST produce a row, counted beside
   the figure under test — a zero is otherwise indistinguishable from a reader wired to nothing,
   which is the whole failure this arc exists to escape.
   forcing: gate — the arc's headline claim ("cairn invocations are now observable") is verified on
   ONE host, and a one-host measurement is not the fleet claim the sentence reads as.

## Gotchas / decisions / dead-ends
- 🔴 **DECISION (operator, 2026-10-01): DO NOT AUTO-MIGRATE HANDOFF CONTENT INTO CAIRN.** Four
  measured reasons, each fatal alone: (a) the `devrc` scope README forbids it — *"Pointers, not
  copies"*; (b) there is no derivable address — at `DEFAULT_MIN_PATHS=2` **95.8% of closed
  blocks resolve to ZERO entries**, 69.9% name no existing repo path, and where derivation
  fires it lands on the `scripts`/`tests` catch-alls that README disowns; (c) the transport
  cannot carry it — `write.sh` refuses a newline or `>2000` chars at rc **21 before the
  network** and **288 of 289 closed blocks are multi-line**; (d) the value evidence covers
  `## Pointers` paths, not prose. **This arc must not quietly re-open it** — a receipt and a
  citation id are instrumentation, not intake.
- 🔴 **DECISION: the narrow aligned version is a POINTER on eviction, and it is NOT this arc's
  item.** When a run both evicts and already has a resolved cairn entry, emit one line naming
  the archive location. Recorded in the parent doc; listed here only so nobody re-derives it as
  new.
- ⚠ **THE ENFORCED 2000-RUNE BULLET CEILING IS WALKED AND `put` IS THE HOLE.** 83 bullets
  exceed it, largest 4,794 chars; `_put_entry` has no per-bullet check while the append path
  does. Any bounded rule that must be ENFORCED rather than requested has to cover `put`. A
  citation id added per bullet inherits this surface.
- ⚠ **A devrc gate reads the PINNED cairn client, and `cairn_pin` route 2 resolves the
  DEPLOYED one.** `shutil.which("cairn"|"cairn-py")` → `realpath` → `<store>/libexec/cairn/lib`
  reads whatever home-manager last switched to, which can be many commits behind
  `flake.lock`. To reproduce a CI failure locally, set `CAIRN_LIB=<locked lib>` (route 1) or
  build the locked rev — otherwise a passing local run reads as "CI is wrong". Measured
  2026-10-02: deployed `cairn-cdf6fae` vs locked `5c96ffda`, nine commits apart.
- ⚠ **Run devrc's suite via its own dev shell, not an ad-hoc `nix-shell -p`.**
  `scripts/run-tests.sh` GUARD 1 aborts (exit 3, **zero tests run**) when any of
  `REQUIRED_TOOLS` is missing, because ~55 `skipif`s would otherwise silently skip. Correct
  invocation, which it prints itself:
  `nix develop <repo> --command bash <repo>/scripts/run-tests.sh <repo>`.

- 🔴 **`emit` BASE64-ENCODES `b64:` VALUES ITSELF — PASS PLAINTEXT.** Verified by decoding a
  real spool line (`b64:text=Y2Fpcm4tcmVjYWxs` → `cairn-recall`). My first test helper
  b64-decoded what it read back from a stub emit and failed on invalid UTF-8; the stub receives
  plaintext because the encoding is emit's job. **The right test boundary is what the CALLER
  hands emit**, not emit's own line format, which emit's existing consumers already pin.
- 🔴 **`source='tool' kind='invocation'` IS AN EXISTING SIGNAL WITH AN EXISTING CONSUMER — do
  not mint a new class.** `scripts/session-analysis/adoption-scan.py` reads it as the adoption
  signal for shipped TOOLS, and `scripts/collector/invocation.py::build_fields` is the shape
  (tool name in `text` AS WELL AS the payload, so a consumer can group without parsing JSON).
  ⚠ `invocation.py` must NOT be imported: it is not deployed on this host, which is exactly why
  `scripts/claude-hooks/hook_telemetry.py` reuses the PATTERN and not the MODULE. Reusing a
  pattern across a deployment boundary is the honest form of "one rule, one place" here.
  ⚠ And `hook_telemetry` deliberately chose `source='hook'` over `'tool'` because a Stop hook
  is not a tool the operator chose to run — folding it in would change what every adoption
  number means. A `cairn` invocation IS such a tool, so `'tool'` is correct here.
- ⚠ **`$EPOCHREALTIME` IS LOCALE-FORMATTED.** Its decimal separator follows `LC_NUMERIC`, so a
  `.`-only parse yields a wrong duration under e.g. `de_DE`. Handle both separators rather than
  forcing `LC_ALL=C`: this process's locale is INHERITED BY THE CLIENT, and changing it could
  change the client's own output — which the byte-for-byte contract forbids.
- 🔴 **A NEW FILE THAT IS NOT `git add`ed IS INVISIBLE TO A FLAKE BUILD.** `nix build` failed
  with `path '…/scripts/cairn-receipt.sh' does not exist` while the file sat in the worktree.
  devrc's own `nix/home.nix` comments warn about this class ("a new extension file that is not
  `git add`ed is silently omitted"); here it was LOUD, which is the better direction, and
  staging the file fixed it. Stage new files before the first `nix build`, not after it fails.
- ⚠ **`--subst-var-by` DOES NOT FAIL ON A MISSING PLACEHOLDER.** So a renamed placeholder would
  ship a wrapper that refuses at runtime on every call with the build green. The derivation
  therefore asserts the placeholder EXISTS before substituting and that NONE survives after —
  two greps, both load-bearing. ⚠ The second one passed while the guard-tautology bug was live,
  so it is necessary and NOT sufficient: it proves substitution happened, never that the
  resulting script works. Only running the built artifact proves that.

- 🔴 **THE STORE ALREADY HELD ALL THREE OF THIS SESSION'S HEADLINE LESSONS, AND I RE-DERIVED
  EVERY ONE AT FULL PRICE. THAT IS THE FINDING, AND IT IS ABOUT THIS ARC'S OWN SUBJECT.**
  Measured at `/handoff` step 4, by reading the entries the probe surfaced:
  (a) *"🔴 A GUARD THAT MATCHES A STRING AGAINST A FILE IS WALKABLE BY PROSE **ABOUT** THE
  GUARD — and the round that documented it did the walking"* — `devrc/scripts.md`, dated
  **2026-09-20**. I hit that in three different costumes in one branch;
  (b) *"SIGTERM from `timeout`, not an assertion"* — `devrc/tests.md`, i.e. the exit-143
  lesson, already written down;
  (c) *"A SUBSET RUN, AND `failed=0` IS ITS SIGNATURE … subset run bypasses guards"* —
  `devrc/tests.md`, i.e. the three-CI-round method failure, already written down.
  **The index was therefore UNCHANGED this session: a fourth copy of a recorded lesson is the
  accumulation the `already there` check exists to stop.** ⚠ And the uncomfortable half, which
  this arc of all arcs should carry: the arc is ABOUT whether recalled bullets get used, and
  its own session did not read them. The cost was not a missing record — it was a missing
  **recall at the start of the work**, which is `/resume` step 4 and takes one command.
- ✅ **ROUTE TAKEN — devrc#1989 `ae1e3b6e`, merged 2026-10-02T20:39Z.** This item previously read
  *"ROUTE IDENTIFIED BUT NOT TAKEN"* and said the evidence *"lives ONLY in this doc's investigation
  block"*. It no longer does: the Tekton duplicate-trigger race and the superseded-no-verdict
  state are now `claude/skills/tekton/SKILL.md` **gotcha 12**, verified by content. The routing
  ruling it carried is the durable half and is unchanged: **an ops-gotcha routes to its owning
  skill, not to a scope entry or a handoff doc** — the skill loads deterministically on trigger,
  which is why neither the subsystem index nor this doc was the right home.
- 🔴 **THE METHOD FAILURE THAT COST THREE CI ROUNDS, STATED AS A METHOD AND NOT AS BAD LUCK.**
  devrc#1983 went red three times, every one a REAL finding, and the root cause was identical
  each time: **I ran the suites I guessed were adjacent** (561 tests, then 99) instead of
  enumerating what reads the thing I changed. Round 3 is when I finally did it —
  `find scripts/tests -name '*.py' -print0 | xargs -0 grep -ln 'local/bin/cairn'` → eight
  files → all eight run → exactly **2 failed of 1309**, both found in one pass.
  **Enumerate the readers of what you are changing; do not reason about adjacency.**
- 🔴 **AND THE FIRST ATTEMPT AT THAT ENUMERATION RETURNED EMPTY, WHICH I NEARLY BELIEVED.** It
  piped into `xargs -0 command grep` — `command` is a shell BUILTIN `xargs` cannot exec, so it
  exits **127** with no output, indistinguishable from a clean zero. `claude/RULES.md` names
  this trap and I had read it the same day. Plain `grep` after `xargs` is the fix.
- 🔴 **A HAND-ROLLED `pytest` INVOCATION OF THIS SUITE PROVES NOTHING, AND IT REPORTS SUCCESS.**
  I ran `python3 -m pytest scripts/tests -p testlib.nolaunch_plugin …` directly; it died at
  startup with `ImportError: No module named 'testlib'` (the suites need a per-directory
  `sys.path`, which is why `scripts/run-tests.sh` exists and says so in its header), my
  `| tail` swallowed the status, and the wrapper printed `DONE rc=0`. **Zero tests ran and it
  looked clean.** Use `scripts/run-tests.sh --targets "<exact target>" <root>`; it also owns
  GUARD 1, which aborts naming a missing tool rather than letting ~55 `skipif`s silently skip.
- ⚠ **`run-tests.sh` EXIT 143 IS A KILL, NOT A FAILURE.** 128+15 = SIGTERM, mine from a
  `timeout` landing in the tenth target group: the runner printed `RESULT: FAIL (exit=143)`
  with **zero** `FAILED` lines and nine groups at 18,005 passed. Count the per-target summary
  lines; never read the runner's exit code as a verdict. Budget ≥50 min for a full run —
  `scripts/tests` alone is ~40.
- 🔴 **`emit` BASE64-ENCODES `b64:` VALUES ITSELF — pass plaintext.** Verified by decoding a
  real spool line (`b64:text=Y2Fpcm4tcmVjYWxs` → `cairn-recall`). A test stub therefore
  receives plaintext, and the right test boundary is **what the caller hands emit**, not
  emit's line format, which emit's existing consumers already pin.
- 🔴 **`source='tool' kind='invocation'` IS AN EXISTING SIGNAL WITH AN EXISTING CONSUMER.**
  `session-analysis/adoption-scan.py` reads it as the adoption signal for shipped TOOLS;
  `collector/invocation.py::build_fields` is the shape (tool name in `text` AS WELL AS the
  payload). ⚠ Do not import that module — it is **not deployed** on this host, which is why
  `claude-hooks/hook_telemetry.py` reuses the PATTERN and not the MODULE. ⚠ And
  `hook_telemetry` deliberately chose `source='hook'` over `'tool'` because a Stop hook is not
  a tool the operator CHOSE to run; a `cairn` invocation is, so `'tool'` is right here.
- ⚠ **`$EPOCHREALTIME` IS LOCALE-FORMATTED** — its decimal separator follows `LC_NUMERIC`, so
  a `.`-only parse is wrong under e.g. `de_DE`. Handle both separators rather than forcing
  `LC_ALL=C`: this process's locale is INHERITED BY THE CLIENT and changing it could change
  the client's own output, which the byte-for-byte contract forbids.
- 🔴 **A NEW FILE THAT IS NOT `git add`ed IS INVISIBLE TO A FLAKE BUILD** — `nix build` failed
  with `path '…/scripts/cairn-receipt.sh' does not exist` while the file sat in the worktree.
  Loud here, which is the good direction; `nix/home.nix`'s own comments warn the extension
  case is SILENT. Stage new files before the first build.
- ⚠ **`--subst-var-by` DOES NOT FAIL ON A MISSING PLACEHOLDER**, so the derivation asserts the
  placeholder exists BEFORE substituting and that none survives after. ⚠ The second assertion
  passed while the tautology bug was live — it proves substitution HAPPENED, never that the
  result WORKS. Only running the built artifact proves that.
- 🔴 **NO CI LEG BUILDS `nix/home.nix`.** devrc's four Tekton legs are pytests, gotests,
  nodetests and cairn-client-runs — none evaluates or builds the home-manager config, so the
  `cairnWithReceipt` derivation is gated by **nothing in CI**. Its verification is a hand-run
  `nix build` plus source-level assertions. Named rather than assumed away, because it is the
  same shape as the tautology bug: the checkout passes, the artifact is what ships.
- **Decision: the receipt's payload is NARROWER than the ranked item asked for.** `entries`,
  `bullets_printed` and `output_bytes` are all properties of the client's STDOUT, so getting
  them means capturing and re-emitting it — against `read.sh`'s *"STDOUT IS THE CLIENT'S, BYTE
  FOR BYTE"* pin, and changing `isatty()` and SIGPIPE under the common `| head` form. Traded
  on an operator decision for **100% of traffic instead of ~6%**.
- **Decision: a reword, not a ledger entry, for the hazardous-binary hit.** The house
  precedent is to KEEP the word and justify it where deleting it would delete a GUARANTEE;
  that mention carried operator GUIDANCE, and the shared justification it would have joined is
  a dense string the repo has already recorded as drifting on its own counts.
- ✅ **devrc#1927 IS NOW CLOSED** (unmerged), which is the right outcome and resolves this item.
  It previously read *"IS AN OPEN PR WHOSE WORK ALREADY LANDED"* via devrc#1928 `7f84306c` —
  verified then by content (zero two-index-slice hits in `handoff-audit-pr-operator-asks.md` at
  `origin/main`). Somebody else closed it; this arc did not, and deliberately did not.
  ⚠ **Read the closure as reconciliation, not as this arc's work** — a `CLOSED without merge`
  on a reconciler's drift line looks identical to an abandoned plan, and here it is the opposite.
- 🔴 **THE PUBLIC-IP SCRUB GUARDS HEAD ONLY.** devrc#1979 removed the literal from the tip;
  the value remains in reachable history on a PUBLIC repo, and `test_no_public_ips.py`'s own
  docstring says rewriting history would not unpublish what is already cloned or forked.
  Whether the long-lived cluster-wide-read bearer token that S1 describes needs ROTATING is an
  open operator decision, deliberately not bundled.

## How to verify
```bash
# rank 1 landed — by CONTENT, never by ancestry (a squash is never an ancestor)
git -C $DEVRC fetch origin
git -C $DEVRC cat-file -e origin/main:scripts/cairn-receipt.sh && echo "wrapper present"
git -C $DEVRC show origin/main:nix/home.nix | grep -c 'source = cairnWithReceipt;'   # 1

# the wrapper's own suite, and the guards that read the entry it changed
nix develop $DEVRC --command bash -c 'cd '"$DEVRC"' && python3 -m pytest \
  scripts/tests/test_cairn_receipt.py scripts/tests/test_cairn_cli.py \
  scripts/tests/test_cairn_flake_pin.py scripts/tests/test_no_real_launchers.py \
  scripts/tests/test_runtime_shebangs.py -q'          # 24 + the four guards

# the full target, THE ONLY WAY THAT WORKS (a hand-rolled pytest dies on sys.path)
nix develop $DEVRC --command bash $DEVRC/scripts/run-tests.sh --targets "scripts/tests" $DEVRC
# read the per-target summary LINES, not the exit code: 143 is a SIGTERM kill, not a failure.

# the built artifact — the only thing that shows a substitution bug
nix build --impure "$DEVRC"'#homeConfigurations.zach.config.home.file.".local/bin/cairn".source' \
  --no-link --print-out-paths

# is the receipt actually LIVE on this host? (the arbiter — a `diff` proves nothing here)
readlink -f "$(command -v cairn)"      # …-cairn-with-receipt ⇒ switched; a bare client ⇒ not

# is a row actually written? 🔴 A DELTA, because the collector DRAINS the spool (trap 4),
# and DECODED, because b64:text=/b64:payload= are base64 (trap 3). Never a bare grep.
SPOOL=~/.local/state/activity/spool/current.log
before=$(wc -l < "$SPOOL" 2>/dev/null || echo 0)
$DEVRC/scripts/cairn-ops/read.sh recall --repo "$DEVRC" >/dev/null 2>&1
after=$(wc -l < "$SPOOL" 2>/dev/null || echo 0); echo "$before -> $after"
grep 'source=tool' "$SPOOL" | grep -oP 'b64:payload=\K[A-Za-z0-9+/=]+' | base64 -d; echo

# 🔴 THE POSITIVE CONTROL, not optional: a run that MUST produce a row, counted beside the
# figure under test — otherwise a zero cannot be told from a reader wired to nothing.
cairn -verbs >/dev/null 2>&1 && wc -l < "$SPOOL"
```
