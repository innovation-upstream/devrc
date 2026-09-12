# Handoff: espanso-audit-gate — 2026-08-21

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Run `/espanso-audit`, tune the snippets from measured usage — and, after the audit's own
advice turned out to be built on a false premise, make the correction enforceable rather
than written down.

## State now
- 🔴 **CARRIED FORWARD (a REPLACE would drop it): THE ENFORCEABLE CORRECTION THIS DOC'S GOAL ASKS
  FOR EXISTS — a LABEL edit can no longer shadow a snippet that DECLARES the term.** Shipped by
  `#1247` (`b9b2493d`) and `#1252` (`de677683`). 🔴 **It is CODE and SURVIVED #1265** — only the
  live-config TESTS over it were removed. ⚠ Its live instance count is now **ZERO**: `a451abc0`
  moved "recommend" out of `:acq`'s label, so the collision it was built for no longer exists on the
  real config. The rule is not wrong; it is unexercised live, and nothing measures that.
- 🔴 **CARRIED FORWARD — the operator decision that authorised all of this, verbatim, because the
  rationale outlives the PR:** *"intended, drop the espanso guards/tests. they keep getting in the
  way."* (2026-09-03). The guards re-broke on every snippet edit; that is what was being bought off.
- 🔴 **CARRIED FORWARD — the #1262 reconciliation, which a future reader will otherwise re-derive.**
  `#1262` (`df02571f`, another session) retired ONE of the same guards ~3 minutes after this arc's
  first fix commit, because `a451abc0` removed the collision it read. #1265 is a SUPERSET, not a
  contradiction. The merge commit `b8ea45df` carries #1262's prose forward with its *"nothing was
  lost — `_EXISTING_RESOLUTIONS` is still green"* accounting explicitly marked **SUPERSEDED**,
  because #1265 deletes that table too. A clean textual merge would have imported that false claim.

- ✅ **THE LIVE-CONFIG GUARDS ARE GONE. `#1265` MERGED as `68d10b19`** (2026-09-04), verified by
  content on `origin/main`. `test_espanso_detect.py` is **60 tests, 0 live-config guards**; the
  dead `_live_base()`/`_live_det()` scrapers, `HOME_NIX` and four unconsumed tables went with them.
  `test_espanso_triggers.py` (11) and `test_espanso_usage.py` (76) untouched. Merged through a RED
  gate on an explicit operator decision — the discriminator and the unattributed second failure are
  recorded in rank 6.
- ✅ **`#1270` MERGED `1cc08ff8`** (this doc's previous update) and ✅ **`#1275` MERGED `aba48864`**
  (the retraction below). Both content-verified.
- 🔴 **WHAT IS NOW UNGUARDED, stated plainly:** nothing checks that a snippet edit preserves
  attribution. A future label reword that costs a search route ships silently. The
  `/espanso-audit` skill previously named the deleted pytest run as its backstop — corrected in
  #1265 to say the backstop is GONE and attribution must be checked by hand.
- 🔴 **THE DECIDING EVIDENCE, because it inverts the intuition and is worth not re-deriving:** the
  live guards were **GREEN THROUGH the regression they existed to catch.**
  `_AUDIT_2026_08_19_RESOLUTIONS` pins `rig`/`portable` — the words the rename INTRODUCED — so it
  passed while `'ssh work nebula'` went from one picker row to zero. Cost: four reddenings of `main`
  on legitimate config edits (#1060, #1247, #1252, `a451abc0`→#1262). Benefit: did not fire.
- ⚠ **`_attribute`'s LOGIC keeps full hermetic coverage** — mutation-verified in #1265's round-1
  audit: killing the declared-interface, name-tiebreak and owner-lookup branches reddens 5, 5 and 4
  survivors respectively. What was dropped is config-data integrity only.
- **This session's clawgate link: NONE.** `clawgate_handoff.sh resolve` exited **5** — 0 tasks. An
  unknown session id answers 200 with an EMPTY ARRAY, so that cannot distinguish "touched no task"
  from "wrong id". No field written, no task created; not a clean bill of health.

## Open investigations — live diagnosis state
None. Everything opened this session was closed or merged. The single unverified claim is the
keystroke check below, which is not an investigation — it needs a human at the keyboard.

### The `clar` prefix blind spot is UNCHANGED, and precedence cannot close it
- **Symptom + exact repro:** typing `clar` into `Ctrl+Space` resolves to **`None`**, not
  `:acq`. Reproduce with the live-config scraper the tests use (`_live_base()` in
  `scripts/collector/keylog/tests/test_espanso_detect.py`).
- **Observed (with values):** `:acq` declares `["ask" "clarify" "clarifying" "questions"]`
  and `:dacq` declares `["ask" "clarifying" "feedback" "dispatch" "process" "elicit" "scope"
  "include"]`. **Both DECLARE a `clar*` term**, so `#1252`'s declared-interface narrowing
  leaves two candidates and changes nothing. The owner table keys on whole terms (`clarify`),
  not prefixes, so `clar` misses it.
- **Ruled out:** *"the precedence rule should have fixed it"* — it only removes LABEL-only
  spellers from the candidate set; this collision is between two DECLARED terms. Measured:
  `clar` stays `None` across the 735-term before/after sweep. via: measurement
- **Ruled out:** *"it is the same bug as `recom`"* — `recom` was label-vs-declared and is now
  auto-resolved; `clar` is declared-vs-declared. Different mechanism, different fix.
  via: measurement
- **Leading hypothesis:** a prefix-aware owner lookup (walk the term back to the longest
  declared owner key it prefixes) would close it, but that is a **separate mechanism change**
  to the table's matching, not to precedence.
- **Next probe, verbatim:** decide first whether it MATTERS — does Zach ever type `clar`?
  ```bash
  # real keylog evidence, not speculation — search terms actually typed
  KUBECONFIG=$KC_HOMELAB kubectl exec -n activity deploy/clickhouse -- \
    clickhouse-client -q "SELECT search_term, count() FROM activity.events \
    WHERE source='keys' AND kind='espanso' AND search_term LIKE 'clar%' GROUP BY search_term ORDER BY 2 DESC"
  ```

### Whether the 41 GAINED attributions are desirable is UNJUDGED
- **Symptom + exact repro:** not a bug — an unreviewed semantics change. Terms that recorded
  "no snippet" now name one, in a telemetry pipeline you later reason from.
- **Observed (with values):** 41 terms moved `None` → a snippet, 0 lost, 0 repointed. The
  headline gains are the prefix chain `rec`/`reco`/`recomm`/`recomme`/`recommen` → `:rna`,
  which the deleted table rows explicitly could NOT close. Two are near-noise short probes
  (`the`, `id` → `:mt`).
- **Ruled out:** *"a gain could be a silent regression"* — every one of the 41 was `None`
  before, so nothing that resolved was moved. via: measurement
- **Leading hypothesis:** the prefix-chain gains are strictly good (they attribute keystrokes
  that were previously dropped); the two near-noise ones are cosmetic and may slightly
  inflate `:mt`'s counts in `/espanso-audit`.
- **Next probe, verbatim:** after a week of new data, check whether `:mt`'s attributed count
  diverges from its direct-trigger count more than other snippets — that is the signal that
  near-noise probes are inflating it.

### ⚠ CORRECTED 2026-09-04 — NOT a telemetry gap. A muscle-memory regression, and smaller
🔴 **The diagnosis below was WRONG on its mechanism and its fix, and it was merged before anyone
re-derived it. Read this correction first; the block under it is kept as the retracted reading.**
- **What is actually true, re-measured on `origin/main`:** `nebula` and `mesh` ARE in both
  snippets' `search_terms` (`:sshwn` and `:sshln` each declare `['nebula','mesh','remote']`). So
  `_attribute('nebula')` → `None` is **CORRECT BEHAVIOUR, not a defect**: two snippets legitimately
  spell the term, the picker shows BOTH rows — which the design calls the feature — and attribution
  declines to guess. I mistook the documented picker/attribution split for a regression.
- **The real, smaller issue:** the rename took "workbench" → **rig** and "laptop" → **portable**
  and dropped `ssh`/`workbench`/`wb` from those two snippets' terms. Measured:
  `'ssh work nebula'` matches **[]** — zero picker rows, not an ambiguous result — and
  `'ssh work'` matches `[':sshwl']` alone, so it silently yields the LAN shortcut where it used to
  offer both workbench variants. The nebula snippets remain reachable by `rig`, `portable`,
  `nebula`, `mesh`, `remote`.
- **So the harm is muscle memory, not telemetry.** It bites only someone still typing "work"/"lap".
  Nothing is unattributed that should be attributed.
- 🔴 **The FIX named below is a NO-OP** — it says to add `nebula`/`mesh` to `search_terms`, and they
  are already there. If the old words are wanted back, the edit is to re-add `ssh`/`workbench`/`wb`
  (and `laptop`) — which would re-create the `'ssh work'` two-row ambiguity the rename removed. That
  is a trade, not a repair. via: measurement

### ~~🔴 OPEN — a LIVE telemetry gap: the `:ssh*` nebula routes attribute to None~~ (RETRACTED — see above)
- **Symptom + exact repro:** searching `ssh work nebula` or `ssh lap nebula` in the Ctrl+Space
  picker records an UNATTRIBUTED fire. Reproduce by building a detector from the real config:
  scrape `nix/home.nix` for `{ trigger = …; replace = …; label = …; search_terms = […] }` records,
  then `EspansoDetector(espanso_triggers.load_triggers({"matches": recs}, {"search_shortcut": "CTRL+SPACE"}))`.
- **Observed (with values):** `_attribute('ssh work nebula')` → **None**; `_attribute('ssh lap
  nebula')` → **None**; `_attribute('ssh work')` → `':sshwl'`; `_attribute('rig')` → `':sshwn'`;
  `_attribute('portable')` → `':sshln'`. The labels became *"SSH rig via nebula mesh"* /
  *"SSH portable via nebula mesh"*, so `rig`/`portable` are the live words that reach those rows
  and ~~`nebula`/`mesh` are not in either snippet's `search_terms`~~ 🔴 **FALSE — they are in BOTH;
  that is why the term is ambiguous rather than missing. See the correction above.**
- **Ruled out:** *"the fixture is right and the probe is wrong"* — reproduced on two independent
  instruments (a scrape of `nix/home.nix`, and the rendered `~/.config/espanso/match/base.yml`
  the daemon actually reads), by two different sessions. via: measurement
- **Ruled out:** *"a guard would have caught this"* — the guard that pinned exactly
  `('rig', ':sshwn')` and `('portable', ':sshln')` is `test_live_audit_2026_08_19_resolutions`,
  one of the ten #1265 removes. via: code
- **Leading hypothesis:** an ordinary label reword took the routing words with it — the
  2026-08-19 defect class, live. It is NOT caused by #1265; #1265 removes the detector for it.
- **Next probe:** none needed to confirm. The FIX is one line: add `"nebula"` and `"mesh"` to
  `:sshwn`'s and `:sshln`'s `search_terms` in `nix/home.nix`, then re-run the probe above and
  confirm both terms resolve. OPERATOR'S CALL — it is snippet config, not code.

### ✅ CLOSED 2026-09-04 — I merged a WRONG diagnosis, and the shape is the reusable part
- **Symptom + exact repro:** this doc (merged via #1270) asserted a *"LIVE TELEMETRY GAP"* —
  *"`nebula`/`mesh` are not in either snippet's `search_terms`"* — with a one-line fix. Retracted by
  **#1275 (`aba48864`)**, which strikes the block through in place rather than deleting it.
- **Observed (with values):** on `origin/main`, `:sshwn` and `:sshln` each declare
  `['nebula','mesh','remote']`. `'nebula'` matches `[':sshln', ':sshwn']` → `_attribute` **None**;
  same for `'mesh'`. So `None` is the CORRECT two-row outcome — the documented picker/attribution
  split — not a defect, and the published fix was a **no-op**. What is real is smaller:
  `'ssh work nebula'` matches **[]** and `'ssh work'` matches `[':sshwl']` alone, because the rename
  dropped `ssh`/`workbench`/`wb`. Muscle memory, not telemetry.
- **Ruled out:** *"the probe was wrong"* — the `None` values were correct on both instruments and
  by two sessions; it was the SENTENCE EXPLAINING them that was never measured. via: measurement
- **Ruled out:** *"the audit ladder should have caught it"* — three rounds re-derived those `None`
  values and confirmed them; none re-derived the explanation, because it read as a restatement of
  the measurement rather than a separate claim. via: measurement
- 🔴 **The reusable shape: A MEASURED VALUE WITH AN UNMEASURED EXPLANATION ATTACHED.** It passed
  three adversarial rounds built specifically to catch claims that certify rather than omit, and
  then a merge. When a bullet reports a value AND says why, those are TWO claims — re-derive both.
- **Next probe:** none — closed. The correction is on `main`.

## Next steps (ranked)
🔴 Numbering is STABLE — `claim-work --slug-for <this doc> <rank>` derives from it.

1. **Type the three triggers** (30 seconds, see "How to verify"). If `Ctrl+Space` → `lap`
   still shows two rows, the central claim of #592 is wrong. **Still the only check no
   command can perform** — espanso erases trigger and expansion when it fires.
   forcing: user — the operator is the only instrument that can reach the keystroke path.
2. **Re-run `/espanso-audit`** — it acted on **3 of 42** ADD-CANDIDATES. The remaining 39 are
   the audit you actually asked for. It now runs against `--gate`.
   forcing: none — the original ask; no external signal is waiting on it.
3. Consider whether `--replay`'s ranking should weight *characters saved* × occurrences —
   `"merge it"` (28×, 8 chars) currently outranks a 48-char phrase at 3×, and that ranking is
   what led to dismissing the short candidates without measuring.
   forcing: none.
4. **Decide whether the `clar` prefix blind spot is worth closing** — devrc,
   `scripts/collector/keylog/espanso_detect.py`, `_AMBIGUOUS_TERM_OWNER` lookup. 🔴 **Measure
   demand BEFORE building**: the ClickHouse probe in the Open-investigations block says
   whether `clar*` is ever typed. If it is never typed, the answer is to write the blind spot
   down, not to close it.
   forcing: none — no signal says anyone hits it; the probe exists to find out.
5. **Optional: retro-audit `#1252`** — `/audit-pr 1252`. Merged unaudited by explicit
   operator call. The part worth a fresh reader is the **test-fixture edit in
   `scripts/session-analysis/tests/test_espanso_usage.py`** (`:bb` now also declares
   `"thing"`), made to resolve a red the change itself caused. The reasoning was sound and it
   was controlled — the file is green against the BASE detector too, so the edit encodes no
   assumption about the change — but "edited a fixture so the test passes" is the shape that
   earns a second reader.
   forcing: none — merged and green; this is discretionary assurance, not a live signal.
6. ✅ **DONE 2026-09-04 — MERGED as `68d10b19`** (squash), verified by content on `origin/main`,
   never by ancestry. `main` now has **60 tests and ZERO live-config guards**. Merged through a RED
   `tekton/devrc-pytests` on an explicit operator decision, with the two-arm discriminator recorded
   on the PR: the named failure `TestARefusedWriteIsIndistinguishableFromAnAbsentOne...` passed on
   BOTH arms (2.47 s branch / 2.76 s main) and lives in `test_subsystem_store_api.py`, which an
   espanso-test deletion cannot reach — the store-api contention family. 🔴 `failed=2` named ONE
   test, so the second is unattributed (devrc#943); one of two accounted for is not the gate
   accounted for. ~~Decide #1265: merge or close.~~ 🔴 The urgency is GONE — `main` is green via #1262, so this
   is now purely "do you want the remaining nine live guards dropped". Merging accepts that no
   automated check will ever again catch a snippet edit that costs attribution. Closing keeps the
   guards and accepts they will redden `main` on the next label reword. Files:
   `scripts/collector/keylog/tests/test_espanso_detect.py`, `claude/skills/espanso-audit/SKILL.md`.
   IN FLIGHT: devrc#1265. CLOSES WHEN: the PR is merged or closed with a reason in writing.
   forcing: user — the operator asked for the guards to be dropped; what changed is only the urgency
7. ⚠ **CORRECTED 2026-09-04 — the premise was wrong and this item shrinks to a judgement call.**
   `nebula`/`mesh` are ALREADY in both snippets' `search_terms`, so the "one line" fix was a no-op
   and `nebula` → None is correct two-row behaviour, not a gap. What actually changed is that
   `'ssh work nebula'` now matches NOTHING and `'ssh work'` yields the LAN snippet alone, because
   the rename dropped `ssh`/`workbench`/`wb`. DECIDE: re-add those words (restoring the old muscle
   memory, and with it the `'ssh work'` ambiguity the rename removed), or leave it and use
   `rig`/`portable`. CLOSES WHEN: either is recorded here in writing.
   forcing: none — nothing external is broken; this is a preference about search words
8. ✅ **DONE 2026-09-04 — ladder STOPPED DELIBERATELY at round 3, not because it went clean**, and
   the rationale is on the PR so a reader can tell the two apart. Criterion met: no 🔴; no blast
   radius past "a comment is inaccurate" (round 3 confirmed the PR changed **0 executable lines**);
   recurring shape swept at every site found, including two my own sweeps missed by being scoped to
   `scripts/` and to test NAMES. ~~Finish #1265's audit ladder or stop it on the stated criterion.~~ Three rounds, three sets of
   findings, all now comment-accuracy in scaffolding rather than behaviour. If round 4 returns only
   prose nits, stop on the criterion and record what is left open rather than grinding.
   CLOSES WHEN: a round returns no findings, or a stop is recorded on the PR with its rationale.
   forcing: none
9. 🔴 **#1256 IS OPEN AND `main`'s SKILL-CHAIN DOC IS STALE — this is the arc's own defect, live.**
   `claudedocs/handoff-skill-chain-usage-audit.md` on `origin/main` still says #1219 is
   *"OPEN as of 2026-09-01"* (it merged 09-02) and still carries the pre-#1219 partition
   (123 of 256 disk-backed; the truth is 25 of 270). The correction exists ONLY in unmerged
   **devrc#1256** (head `10a01687`, branch `docs/handoff-skill-chain-post-1219`). Verify with
   `git -C ~/workspace/devrc show origin/main:claudedocs/handoff-skill-chain-usage-audit.md | grep -c 'RE-MEASURED 2026-09-02'`
   → **0** today. NEEDS: forward-merge `main` (it is well behind), re-read the gate — its red was
   diagnosed as INHERITED from a then-red `main`, and since `main`'s espanso failure is fixed that
   theory is now TESTABLE rather than assumed — and round 2 of its ladder, which never ran.
   IN FLIGHT: devrc#1256. CLOSES WHEN: #1256 merges and that grep returns non-zero.
   forcing: regression — a doc read as authoritative at session start asserts a superseded state
10. **Decide the `:ssh*` search words** — the corrected version of old rank 7, kept separate because
   it is a preference, not a repair. `'ssh work nebula'` matches NOTHING and `'ssh work'` yields the
   LAN snippet alone, because the rename dropped `ssh`/`workbench`/`wb`. Re-adding them restores the
   old muscle memory AND re-creates the `'ssh work'` two-row ambiguity the rename removed. File:
   `nix/home.nix`, the four `:ssh*` records. CLOSES WHEN: either choice is recorded in this doc.
   forcing: none — nothing external is broken

## Gotchas / decisions / dead-ends

🔴 **`--lint`'s "can never fire from the search UI" is about TELEMETRY, not espanso.** espanso
lists every match as a picker row; the user arrows to one. `_attribute` returning `None` on
≥2 matches only means the fire is logged with `trigger=None` (`_close_search`,
`espanso_detect.py:228`, emits a row either way). **This premise being read literally is what
caused everything else in this session.** The tool's own strings now say so.

🔴 **A snippet with NO label is worse off, not better** — espanso falls back to showing its
raw `replace` text as the picker row.

🔴 **FOUR fatal-rule designs for `--gate` were each walked by a one-line edit**, and the
pattern is the lesson: every one tried to infer *intent* from the config, and intent is not
in the config. Removing `nebula` from a label is byte-identical whether you retired the word
or destroyed the only way to find that snippet.
| rule | walked by |
|---|---|
| a query reaches nothing | pruning — red on the skill's own primary action |
| snippet reaches nothing at all | keeping **one** word |
| lost with no gain | adding **one junk** word |
| any magnitude threshold | staying under it |
The shipped rule stops guessing: every lost query is fatal, deliberate ones are stated with
`--accept word,word`. A prune needs none — the word went with the snippet.

🔴 **`--replay` "0 regressions" is a claim about the OBSERVED search stream**, not the config.
It replays only terms actually typed in the window. It reported 0 while a real regression was
live. Always also run `pytest scripts/collector/keylog/tests/test_espanso_detect.py`.

🔴 **`'ask'` ⊂ `'task'`.** A `:cgt` labelled "task" silently takes `:acq`'s 58-fire term.
`_token_matches` is a **substring** test over trigger + label words + search_terms, so
`'bench'` ⊂ `'workbench'` and `'la'` ⊂ `'nebula'` too. Pinned now, but the class is live.

🔴 **`.git/hooks` cannot tell you whether the pre-push gate is installed.** `githooks/` ships
a real blocking one; it installs by pointing `core.hooksPath` elsewhere. Use
`git config --get core.hooksPath`. Also: several workspace clones carry a **repo-local**
`core.hooksPath` (workbench `devrc` and `homelab-talos` do, the laptop's `devrc` does not) —
local beats global, so `githooks/install.sh` alone can be inert. Per-clone, environmental,
not a devrc setting.

**Dead ends, so they are not re-tried:** collapsing the four `:ssh*` to nebula-only (all four
endpoints are in live use, LAN ahead of nebula — `activity.events`, 13-day window: laptop-LAN
4, workbench-LAN 3, workbench-nebula 1, laptop-nebula 0); grading attribution loss as fatal
(permanently-red gate); `blinded`/all-or-nothing and lost-with-no-gain as fatal axes (both
walked, above).

**Process, measured this session:** a `[] or [...]` mutant evaluates to the non-empty list and
mutates nothing — written **three times**, scoring phantom mutants SURVIVED each time. Declare
each mutant's expected verdict and make setup failure loud.

- 🔴 **A `label` IS ROUTING, NOT DOCUMENTATION — editing prose changed search behaviour and
  reddened `main`.** `_token_matches` read three sources (trigger, `search_terms`, label), so
  adding the word "recommend" to `:acq`'s label silently stole `:rna`'s two declared terms.
  **The fix was to rank the sources, not to forbid the edit:** a snippet that DECLARES a term
  outbids one that merely spells it in prose. Generalises past espanso — **when a
  human-readable field feeds a matcher, a cosmetic edit is a behavioural change**, and the
  remedy is precedence between declared and incidental, not asking humans to write carefully.
- 🔴 **THE TABLE ROWS THAT FIXED IT BECAME DEAD CODE, AND WERE MEASURED AS SUCH BEFORE
  DELETION.** `#1247`'s `recom`/`recommend` entries are unreachable once precedence lands —
  `_attribute` returns before the owner lookup — so they were deleted, not kept as
  belt-and-braces. **The control that licensed it: remove the rows, re-run, and watch both
  terms still resolve to `:rna`.** Keeping an unreachable entry "just in case" is how this
  repo accumulates guards that read as coverage and provide none. The re-add condition is
  written into the source.
- 🔴 **`ask`/`clarify` STAYED in the table on purpose — it is a DIFFERENT collision class.**
  `:acq` and `:dacq` both DECLARE `ask`, so precedence narrows nothing and the owner table is
  still the only arbiter. **Precedence removed a class of FALSE collisions from reaching the
  table; it did not replace the table.** Do not "finish the job" by deleting the rest.
- ⚠ **A fixture edit made to fix a red the change caused needs its own control.** `#1252`'s
  new sensitivity legitimately reddened `test_espanso_usage.py`; the fix edited the FIXTURE's
  isolation. The control that makes that honest: **the file must be green against the BASE
  detector too**, proving the edit encodes no assumption about the change. A first attempt
  using a `search_terms`-only word was wrong and the suite caught it.
- ⚠ **The live guard did exactly its job, on the first commit that tripped it.** The comment
  above the snippets says `test_live_existing_resolutions_not_made_ambiguous` "is what tells
  you, and it is not optional to read." It caught a real routing regression the same day it
  was introduced. **Do not weaken it** — `#1252` narrows what reaches it, never what it
  asserts.

## How to verify
```bash
# the precedence rule is live, and #1247's two rows are gone (both required)
git -C $DEVRC show origin/main:scripts/collector/keylog/espanso_detect.py \
  | command grep -c "_term_matches_declared"                       # >0
git -C $DEVRC show origin/main:scripts/collector/keylog/espanso_detect.py \
  | command grep -A4 "^_AMBIGUOUS_TERM_OWNER"                      # ask + clarify ONLY

# the operator's label is untouched by every PR in this chain
git -C $DEVRC show origin/main:nix/home.nix \
  | command grep -o 'trigger = ":acq".*label = "[^"]*"'            # "…and recommend improvements…"

# the guard that reddened main is green WITHOUT the owner rows it was patched with
nix develop $DEVRC -c env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  $DEVRC/scripts/collector/keylog/tests \
  $DEVRC/scripts/session-analysis/tests/test_espanso_usage.py -q -p no:cacheprovider   # 190 passed

# the tool, from main, against the live config — must be PASS
nix-shell -p python3Packages.pyyaml --run \
  "python3 ~/workspace/devrc/scripts/session-analysis/espanso-usage.py --gate \
   \$(readlink -f ~/.config/espanso/match/base.yml)"
```
**The part no command can do — type these** (espanso erases trigger and expansion when it
fires, so nothing in the toolchain can reach the keystroke path):
- `:sshll` → `ssh zach@192.168.50.155`; then `Ctrl+Space` → `lap` must show **ONE** row
- `Ctrl+Space` → `nebula` must still show **TWO** rows (correct, not a bug)
- `:alo` → your wording, unchanged by the reconcile
- 🆕 `Ctrl+Space` → `recommend` must show **`:rna`** (this is what `#1247`+`#1252` restored);
  `clar` still shows nothing — known, rank 4.
