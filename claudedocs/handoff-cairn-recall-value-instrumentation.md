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
- 🔴 **RANK 1 IS BUILT AND IN FLIGHT — devrc#1983, `feat/cairn-invocation-receipt`.** The
  receipt lands at the **PATH bin seam** (`nix/home.nix`'s `.local/bin/cairn` becomes a
  fail-open wrapper) and **not** in `scripts/cairn-ops/read.sh`, which is a change from what
  this doc's rank 1 specified. Three measured reasons: `read.sh recall` ends in
  `exec cairn …` so it has no after-the-call moment; its header pins *"STDOUT IS THE CLIENT'S,
  BYTE FOR BYTE"* because `/resume`, `/handoff` and `/analyze-service` diff recall output
  against prior captures; and it is **93 of 1,425 (~6%)** of traffic. Every caller reaches the
  client through PATH, so one wrapper covers all of them.
- ⚠ **THE ROW'S PAYLOAD IS NARROWER THAN THIS DOC ASKED FOR, ON AN OPERATOR DECISION.**
  `entries`, `bullets_printed` and `output_bytes` are all properties of the client's STDOUT,
  so obtaining them means capturing and re-emitting it — the byte-for-byte contract, plus a
  changed `isatty()` and changed SIGPIPE under the very common `| head` form. Traded for 100%
  of traffic instead of ~6%. The row carries
  `{tool, outcome, verb, scope, repo}` + `exit_code` + `duration_ms` + `session`, as
  `source='tool' kind='invocation'` — the EXISTING adoption signal, not a new class.
- ✅ **CLAUSE (a)'s DESTINATION IS VALIDATED, WITH BOTH CONTROLS, AND IT WAS DONE BEFORE ANY
  CODE WAS WRITTEN** (this doc's own next probe): an isolated spool went **0 → 1** rows through
  the real `~/.config/activity-collector/emit`, and the row's fields decoded correctly.
  `emit` base64-encodes `b64:` values ITSELF — verified by decoding a real spool line — so a
  caller passes plaintext.
- 🔴 **A BUG SHIPPED AND WAS CAUGHT ONLY BY BUILDING THE ARTIFACT, AND THE CLASS GENERALISES.**
  The wrapper's placeholder guard spelled `@CAIRN_REAL@` in its own `case` pattern; `substitute`
  rewrites EVERY occurrence, so the deployed file compared the real store path against itself,
  matched, and refused **every** `cairn` call at exit 70. The checkout copy passed its own
  smoke test throughout, because there the placeholder is still a placeholder. **A guard that
  must RECOGNISE a token cannot SPELL that token in a file something rewrites.** Matrix:
  16 failed → 19 passed.
- ⚠ **NO CI LEG BUILDS `nix/home.nix`** — devrc's four Tekton legs are pytests, gotests,
  nodetests and cairn-client-runs. The wrapper derivation is therefore gated by **nothing in
  CI**; its only verification is a hand-run `nix build` plus source-level assertions. That is
  the same shape as the bug above, so it is named rather than assumed away.
- ⚠ **RANK 2 IS CLAIMED (`cairn-recall-value-instrumentation-2`) AND SCOPED BUT NOT STARTED.**
  Its design is now settled and is NOT what the ranked item implied — see the investigation
  block below before writing any code.
- 🔴 **THE DEADLINE IS STILL THE ONLY LIVE CLOCK, AND IT IS NOW DATED RATHER THAN RESTATED.**
  Measured **2026-10-01**: Claude Code transcripts spanned **2026-09-01 → 2026-10-01 exactly**
  — nothing older, no `cleanupPeriodDays` set — so the corpus **rolls at 30 days and does not
  accumulate**. ⚠ It has since rolled, which is the point rather than a caveat: a day of the
  only existing evidence was deleted between that measurement and this update, and re-reading
  the span is one command (`ls` the transcript dir's oldest entry) that a later session should
  run rather than trusting this line. Waiting does not grow `n`.
- **Carried forward — a REPLACE heading would drop these values.** `source='tool'` held **0**
  cairn rows against **1,425** transcript-proven invocations; the documented
  `read.sh recall` spelling is **93 of 1,425 (~6%)** of real traffic and **746 of 1,425** carry
  no parseable status line because of `| head`/`| tail`; the naive echo proxy fires
  **451/454 = 99.3%**; the counterfactual needs **≈510/arm** (message count) and
  **≈10,751/arm** (duration) against a small arm of **68**.

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
- **Next probe:** before building, confirm the receipt's destination can actually be written
  and read by the client — i.e. that the activity path reachable from
  `scripts/cairn-ops/read.sh` can emit a `source='tool'` row at all, with a positive control
  showing a non-zero count. That is clause (a)'s instrument, and the parent arc never
  exercised it.

### 🔴 OPEN — the receipt's own measurement surface has two known traps, both of which produced a confident wrong number already
- as-of: 2026-10-01
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
- **Leading hypothesis:** both traps are read-path only; neither blocks emitting a row. They
  are recorded so the arc's first query is not the one that gets believed.
- **Next probe:** none standing — apply both when writing clause (a)'s query.

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

## Next steps (ranked)

1. **SHIP THE RECALL RECEIPT (clause (a)) — one `source='tool'` row per invocation**, carrying
   `{status, scope, entries, bullets_printed, output_bytes, session}`. Repo: `devrc`; the
   emitting edge is `scripts/cairn-ops/read.sh` and whatever it calls to reach the activity
   store. 🔴 **Instrument the BARE spelling too, or it measures ~6% of traffic** — `read.sh
   recall` is 93 of 1,425 invocations and the rest is bare `cairn recall`. Land it with the
   positive control from the closing condition, not just a green test.
   forcing: deadline — the transcript corpus rolls at 30 days (2026-09-01 → 2026-10-01,
   nothing older, no `cleanupPeriodDays`), so the only evidence that could justify any further
   automation here is deleted daily and waiting does not grow `n`.
2. **STAMP EACH PRINTED BULLET WITH AN OPAQUE CITATION ID (clause (b))** — `[cb:7f3a]`-shaped,
   derived so it is stable per bullet and collides with nothing in the corpus. Both renderers
   must agree, which makes this a **cairn** change, not a devrc one:
   `internal/report` + `lib/subsystem_recall.py`, pinned equal by the cross-language fixture
   (`internal/report/testdata/reader_fixtures.json` — regenerate and diff, never hand-edit).
   ⚠ **This changes rendered bytes, so it will break every devrc guard that pins the pinned
   client's output** — exactly how cairn#162's `tasks`→`refs` rename reddened devrc `main`.
   Bump the devrc pin and update those expectations in the same round.
   forcing: deadline — same clock as item 1; and the citation id is the only observable that
   reaches the prose-lesson population at all, which is what the parent arc's evidence could
   not see.

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

## How to verify
```bash
# (a) the receipt — the row must exist AND the query must be shown able to return non-zero
$DEVRC/scripts/cairn-ops/read.sh recall --repo /home/zach/workspace/devrc >/dev/null
# then query the activity store for source='tool' cairn rows for this session;
# POSITIVE CONTROL: the same query over a window containing a known-present row type
# must return a non-zero count, or the zero means nothing.

# (b) the citation id — absent before, present after, and unique
$DEVRC/scripts/cairn-ops/read.sh recall --repo /home/zach/workspace/devrc \
  | grep -oE '\[cb:[0-9a-f]{4,}\]' | sort -u | head
# then grep the token across the corpus and confirm exactly one origin.

# the renderers must agree byte-for-byte (clause (b) touches both)
cd /home/zach/workspace/cairn && go test ./internal/report/... && python3 tests/parity/harness.py

# the parent arc, for context only — CLOSED, verdict NOT ADDRESSED
sed -n '/^## State now/,/^## Open/p' $DEVRC/claudedocs/handoff-handoff-corpus-to-cairn.md | head -20
```
