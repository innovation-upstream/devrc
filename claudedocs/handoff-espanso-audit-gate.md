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
- 🔴 **CARRIED FORWARD (the REPLACE would otherwise drop it): THE ENFORCEABLE CORRECTION THIS
  DOC'S GOAL ASKS FOR EXISTS — a LABEL edit can no longer shadow a snippet that DECLARES the
  term.** Shipped by `#1247` (`b9b2493d`, the `_AMBIGUOUS_TERM_OWNER` patch) and `#1252`
  (`de677683`, declared-interface precedence). 🔴 **That correction is CODE and SURVIVES #1265** —
  what #1265 removes is only the LIVE-CONFIG tests over it; `_attribute`'s declared-interface,
  name-tiebreak and owner-lookup branches all keep killing hermetic coverage (mutation-verified in
  the #1265 round-1 audit: 5, 5 and 4 survivors go red respectively). ⚠ But note the live value is
  now ZERO instances: `a451abc0` moved "recommend" out of `:acq`'s label, so the collision the
  precedence rule was built for no longer exists on the real config. The rule is not wrong; it is
  currently unexercised live, and nothing measures that any more.

- 🔴 **THE LIVE-CONFIG GUARDS ARE BEING RETIRED — operator decision 2026-09-03, "drop the
  espanso guards/tests, they keep getting in the way".** In flight as devrc **#1265**
  (`fix/drop-espanso-live-guards`, head `a319056e`), NOT merged.
  Removes all **10** live-config-coupled tests from `test_espanso_detect.py` (each read
  `nix/home.nix` at test time), the dead `_live_base()`/`_live_det()` scrapers, `HOME_NIX`, and
  four unconsumed tables. **KEEPS the 60 hermetic tests** + all 11 in `test_espanso_triggers.py`
  + all 76 in `test_espanso_usage.py`. 180 pass.
- 🔴 **#1262 (`df02571f`) RETIRED ONE OF THE SAME GUARDS INDEPENDENTLY, ~3 MINUTES AFTER this
  branch's first fix commit** — `test_recommend_terms_resolve_on_the_live_config`, because
  `a451abc0` removed the collision it read. #1265 is a SUPERSET, not a contradiction; the merge
  commit `b8ea45df` reconciles them and carries #1262's prose forward with its "nothing was lost /
  `_EXISTING_RESOLUTIONS` is still green" accounting explicitly marked SUPERSEDED — #1265 deletes
  that table too.
- ✅ **`main` IS GREEN.** Re-measured at `79338677`: `test_espanso_detect.py` **69 passed**. #1265's
  original framing ("main is red and this unbreaks it") was true when written and is WITHDRAWN
  publicly on the PR. There is no urgency left; the PR stands on whether dropping the remaining
  nine guards is wanted.
- 🔴 **WHAT IS LOST, stated rather than hidden.** Nothing enforces the live coupling after #1265:
  a snippet edit that costs attribution ships silently. The `/espanso-audit` skill was still
  naming the deleted pytest run as its backstop (the one documented as catching the 2026-08-19
  `'dispatch'` case `--replay` missed) — corrected in the PR to say the backstop is GONE.
- **Audit ladder on #1265: 3 rounds, all with findings, none clean yet.** Round 1: 141 dead lines
  left behind + the skill backstop + 5 dangling refs + a drifted fixture. Round 2: the fix
  re-mirrored ONE fixture and not its two siblings, and orphaned a pointer in payload code.
  Round 3: the "these are synthetic" correction swept in `CIVIT_BASE`, which IS an exact mirror;
  "4 of 7" is 4 of 10; a "no dangling pointer remains" claim that was false.

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
6. **Decide #1265: merge or close.** 🔴 The urgency is GONE — `main` is green via #1262, so this
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
8. **Finish #1265's audit ladder or stop it on the stated criterion.** Three rounds, three sets of
   findings, all now comment-accuracy in scaffolding rather than behaviour. If round 4 returns only
   prose nits, stop on the criterion and record what is left open rather than grinding.
   CLOSES WHEN: a round returns no findings, or a stop is recorded on the PR with its rationale.
   forcing: none

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
