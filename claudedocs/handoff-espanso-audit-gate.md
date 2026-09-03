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
- devrc `main`, HEAD == `origin/main`. **Nothing in flight.** Claim
  `espanso-label-only-match-precedence` released. ⚠ Another session's dirty
  `nix/programs/alacritty/default.nix`, `nix/system/apply-tmp-churn-retention.sh` and three
  untracked `diagnose-*`/`output.txt` files sit in the shared checkout — untouched.

🔴 **THE ENFORCEABLE CORRECTION THIS DOC'S GOAL ASKS FOR NOW EXISTS: a LABEL edit can no
longer shadow a snippet that DECLARES the term.** Two PRs, one day, by two different sessions:

- **`#1247` → `b9b2493d`** (another session) — `main` had gone RED. The operator edited
  `:acq`'s **`label`** to *"ask clarifying questions and recommend improvements and anything
  useful to include"*; `replace` and `search_terms` were untouched. `_token_matches` reads the
  label, so the new word "recommend" made `:acq` match `recom`/`recommend` — `:rna`'s declared
  terms — and both went ambiguous → `None`. Patched by adding `"recom": ":rna"` and
  `"recommend": ":rna"` to `_AMBIGUOUS_TERM_OWNER`. **`nix/home.nix` untouched: the operator's
  label stands as written.**
- **`#1252` → `de677683`** (this session) — the structural fix. In `_attribute`, on the
  **already-ambiguous branch only**, candidates are first narrowed to those reaching the term
  through their **declared** interface (trigger or `search_terms`) before the naming tie-break
  and the owner table. `_term_matches_declared` is `_term_matches` with the label switched off
  — one keyword-only flag, not a second copy of the substring logic. **The picker is
  untouched**: `_term_matches` still reads labels, so snippets stay findable by description.

🔴 **`#1247`'s two table rows were DELETED by `#1252`, because they became structurally
unreachable** — for `recom`/`recommend` the declared-narrowed set is a single snippet, so
`_attribute` returns *before* the owner lookup. `_AMBIGUOUS_TERM_OWNER` is now `ask` and
`clarify` only; those stay because `:acq` **and** `:dacq` both DECLARE `ask` in
`search_terms`, so precedence narrows nothing and the table is still what decides. The re-add
condition is written into the source (`:acq` declaring `recom` in its own `search_terms`).

**Measured, not asserted** — 26 snippets, **735 terms** (every `search_term`, label word,
trigger name ±`:`, the four pinned test tables, and every prefix) resolved before and after:
**523 → 564, LOST 0 · REPOINTED 0 · picker-row diffs 0 · GAINED 41.** Instrument validated
both ways: identical modules → 0 on every counter; a deliberately broken module → exactly 4
diffs (`ask`/`clarify`/`recom`/`recommend`, the owner table's entire live effect).

**Independently reproduced (not taken from the agent's report):** with the two rows deleted,
`nix develop $DEVRC -c env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest <branch>/scripts/collector/keylog/tests
<branch>/scripts/session-analysis/tests/test_espanso_usage.py -q -p no:cacheprovider` → **190
passed**, including `test_live_existing_resolutions_not_made_ambiguous` — the test that had
reddened `main`. Owner table and `nix/home.nix` diff also verified by content on `origin/main`.

**Gate:** all three tiers green on the merged tree at base `2c6b2ac9` (which had not moved at
merge time) — sandbox `nix build …#pytests` `RESULT: PASS` 20,599 passed / 0 failed; sandbox
`…#nodetests` `RESULT: PASS` 1,449; dev-host `gate.sh --tier both` `GATE: RESULT=PASS`. Built
one at a time, redirected, never piped.

⚠ **`#1252` merged WITHOUT an adversarial audit — operator's explicit call.** Recorded so it
is not later mistaken for audited work. See rank 5.

**No clawgate task recorded** — `clawgate_handoff.sh resolve` exited **5** (0 tasks). Its
positive control answered 3 links for another session, so the board was reachable and the 0
is a real reading; it still cannot prove the session id under test is right. No field written.

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
