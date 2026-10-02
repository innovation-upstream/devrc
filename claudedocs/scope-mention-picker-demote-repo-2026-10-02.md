# SCOPE — a demote key for the mention picker's repo-select list

**Status: SCOPE ONLY.** No production code written, no PR opened, no clawgate/muster task
created, nothing deployed, no file in any repo modified. Everything below is a proposal plus
the measurements that justify it.

- **Date:** 2026-10-02
- **Repo:** `innovation-upstream/devrc` (`/home/zach/workspace/devrc`) — read at
  `origin/main` = `c4adcd34`.
- **Scoped against the POST-MERGE shape of PR #1829**, head
  `origin/fix-mention-picker-staleness-margin` = `47cbab82`, merge base `26d853f2`. Stated
  explicitly because the brief required it, and because the two trees differ in the exact
  functions this work touches. See §1.1 for the test-merge.
- **fzf version every fzf claim below was measured at:** 0.74.4 — which is also the version
  the live click path uses (§1.2c).
- **Trigger:** operator's words — *"adding a key in the repo select list to demote the repo
  (make it always appear at the bottom) so i can demote inactive repos and reduce clutter"*.

---

## 0. TL;DR — what changed in my understanding of the problem

Six findings reframe the ask. Each is sourced below. **The first one is the reason this
document does not simply specify the feature.**

1. 🔴 **"Always appear at the bottom" is NOT ACHIEVABLE through the ordering, and the
   ordering is the only lever the handler has.** The pre-computed order reaches fzf as
   INPUT ORDER only, and `--tiebreak=end` consults input order only on an exact tie — a
   fact `mention-open.py` already records about Tier A/Tier B and which applies identically
   to a demote term. MEASURED (§2.4): a row placed **LAST** in a 30-row input came back
   **FIRST** for all four of `widget`, `wid`, `acme/widget`, `w`, beating a better-ranked
   row placed first; the empty-query control returned input order, so the instrument does
   see input order when there is nothing typed. **So a sort-key demote is "at the bottom"
   only while the operator types nothing** — i.e. on the first screen, which is
   **19 of 405 rows** (`PICKER_LINES = 22` minus header/info). The operator's own reported
   habit is *"I end up typing the repo name anyway"*, which is the path a demote cannot
   reach. Delivering the sentence as written requires **filtering**, which the module
   refuses on a stated argument (§3.4).

2. 🔴 **The clutter is 170 rows, not 405 — and the plausibility class does NOT subsume the
   ask, but a field the generator already pays for very nearly does.** MEASURED on this
   host's live state: 405 universe rows, **235 (58.0%) `IMPOSSIBLE`** and therefore already
   last, leaving **170 above that block for every clicked `#N`** (constant — `#1` through
   `#1829` all give 170). An inactivity proxy (`pushed_at` > 1y ago **or** `archived`) names
   **219 of 405**, of which **152 are already IMPOSSIBLE** and **67 are in the 170-row top
   block**. Demoting those 67 cuts the top block **170 → 103 (−39.4%)**. That is real. But
   `pushed_at` and `archived` come free on an API call `regen-known-repos.py` **already
   makes** and currently projects away (§1.2a) — so the mechanical 67 are reachable with
   **zero keypresses, no fzf change, no undo problem, and self-correction**.

3. 🔴 **A demote term has to be the FIRST element of the sort key — and widening
   `measured_rank_key` to carry it silently breaks the promotion gate, with a green suite.**
   The key is `(klass, distance, -score)`; the gate reads `top_key[0] == CLASS_PLAUSIBLE`
   (`mention-open.py:4139`). `CLASS_PLAUSIBLE` is `0` and a non-demoted flag is also `0`, so
   after a naive widening the comparison **still passes for every non-demoted row whatever
   its class** — an `IMPOSSIBLE` row gets promoted above the pane guess and captioned
   *"this host's best-ranked repository"*. Nothing in the key's shape makes that visible.
   §4 gives two shapes and which one I recommend.

4. ✅ **`--expect` is the right carrier, and it is measured rather than argued.** At 0.74.4,
   `--print-query --expect=ctrl-x` produces `<query>\n<key>\n<row>\n` — key line **empty**
   on Enter, `ctrl-x` on the demote key, **and the row is still printed** (§2.3 rows C/D).
   The esc bind is untouched (row E: still `<query>\n`) and the uncovered aborts still write
   0 bytes (row F). `PICKER_OUT_LINES` is **already derived from the flag string**, so 2→3
   is a mechanical consequence rather than a new literal. No `{}` substitution, therefore
   **no private repository name in any child process's argv** — which is the invariant
   `execute-silent` would break (§3.2).

5. 🔴 **The dim budget is a REF-DEPENDENT fact, and this scope is on the side of it with
   ZERO free slots.** `CLICK_DIM_FIELDS` is **14 at `origin/main`** and **16 post-#1829**
   (which adds `margin` and `plausibility_premargin`) against `_MAX_DIMS = 16`. **I scoped
   against #1829, so the honest answer is 0 free slots: a `demoted` dim needs an eviction or
   a cap raise, and §5.5 argues a specific eviction rather than assuming a slot.** Two
   corrections to how this is usually stated: the cap is **PER-EVENT, not a shared pool**
   (`sanitize_dims` is called from `build_fields`, which builds one invocation event), so a
   demote dim rides the *same click row* and genuinely competes — but a *different* sink's
   event gets its own 16 and never competes. And overflow is **no longer silent**: §1.2e.

6. ⚠ **The handler is LIVE out of the working tree. MEASURED, not inferred** — the deployed
   Alacritty hint command `exec`s
   `/home/zach/workspace/devrc/scripts/mention-open.py` directly (§1.2c). An edit to that
   file is in the operator's click path on the next click with **no `home-manager switch`**.
   The wrapper's pinned `PATH` is a nix-store list, so a design needing a **new binary**
   would need a switch — which is a second reason the recommendation spawns nothing.

---

## 1. Measured facts — verification results

### 1.1 The PR #1829 reconciliation

| claim | result |
|---|---|
| #1829 is OPEN and rewrites this area | **CONFIRMED.** `gh pr view 1829 --json state,additions,deletions,changedFiles` → `OPEN`, +1516/−54 over 3 files: `scripts/mention-open.py`, `scripts/tests/test_mention_open.py`, `scripts/tests/mutation_battery_mentions.py`. |
| it conflicts with `main` | **NO.** `gh pr view 1829 --json mergeable,mergeStateStatus` → `MERGEABLE` / `CLEAN` — the only authority on this. Cross-checked by **exit code**, never by a marker grep: `git merge-tree --write-tree origin/main origin/fix-mention-picker-staleness-margin` → **exit 0**. |
| the merge is semantically safe for these files | **YES, and this is the stronger check.** A clean git merge is not a clean merge, so: `git log --oneline 26d853f2..origin/main -- scripts/mention-open.py scripts/tests/test_mention_open.py` → **zero commits**. The branch is 4 ahead / **143 behind**, and none of those 143 touches either file. Nothing on `main` can semantically collide here. |

**So this scope targets the post-#1829 shape**, and the difference from `main` is not
cosmetic:

| symbol | at `main` `c4adcd34` | post-#1829 |
|---|---|---|
| `plausibility_class` | `(num, max_ref)` | `(num, max_ref, margin=0)` |
| `measured_rank_key` | `(full, num, ranges) -> (klass, distance)`, `distance = max_ref - target` | `(full, num, ranges, margin=0)`, `distance = abs(max_ref - target)` |
| `order_universe` | `(universe, num, ranges, scores=None)` | `+ margin=0` |
| `plausible_margin` | — | **new**, `(age_days) -> int`, clamped ≥ 0 |
| `PLAUSIBLE_MARGIN_MAX` | — | **deleted**, replaced by an interval-derived cap |
| `CLICK_DIM_FIELDS` | **14** | **16** (`+ plausibility_premargin`, `+ margin`) |

### 1.2 Things I found to be WRONG, or materially incomplete, in the brief or the tree

**(a) `regen-known-repos.py` already calls the API that answers "is this repo inactive",
and throws the answer away.** `scripts/regen-known-repos.py:671-672`:

```
["gh", "api", "user/repos", "--paginate", "--jq",
 ".[] | {full_name, has_issues} | tostring"]
```

Two fields. `pushed_at` and `archived` are in the same response objects at **zero extra
request cost** — I retrieved them with one call (`gh api user/repos --paginate --jq '.[] |
{full_name, has_issues, pushed_at, archived} | tostring'`, 401 rows, rc 0) and the
distribution is §2.2. This changes the shape of the decision: the brief frames the feature as
a picker key, and the cheapest route to **the majority of the measured clutter** is a
generator projection plus one sort term, with no key, no file to undo, and no fzf change.
It is a *question-the-requirement* finding, not an objection.

**(b) The demote list's FILE SHAPE decides whether the existing leak gate covers a committed
copy — and three of the four natural shapes are NOT covered.**
`scripts/tests/test_regen_known_repos.py` runs **four** detectors on one tracked-file sweep:
`looks_like_a_repo_mapping` (dict `name → name`), `looks_like_a_repo_universe` (a JSON **or**
Python **list literal** of `owner/repo`, and it requires the document to start with `[`),
`looks_like_a_repo_range_table` (dict `name → number`), `looks_like_a_pick_log` (JSONL rows
whose key set is **exactly** `{n, repo, t}` plus optionally `{via}`).

| candidate shape for the demote file | covered today? |
|---|---|
| JSON **list** of `owner/repo` (the `known_universe.json` shape) | ✅ `looks_like_a_repo_universe`, threshold 20 distinct names |
| dict `owner/repo → epoch` | ✅ `looks_like_a_repo_range_table` (value is numeric) |
| dict `owner/repo → {"t": …, "why": …}` | ❌ value is neither a name nor a number |
| JSONL `{"t","repo","op"}` | ❌ the pick-log fingerprint is an **exact** key set; `op` is in neither the required nor the optional set, so every row scores 0 |

So: **reuse the list shape, or ship a fifth detector in the same commit.** ⚠ Residual, and
it is the universe file's own: a list under the 20-name threshold slips, and the detector's
docstring already says a universe "split across several smaller literals … is NOT counted".
A short demote list is therefore *not* protected by this gate whatever shape it takes.

**(c) "Live with no switch" — measured, and the measurement also fixes the fzf-version
provenance.** `~/.config/alacritty/alacritty.toml` is a store symlink
(`readlink -f` → `/nix/store/q6ij…-alacritty.toml`) whose hint `command` is
`/nix/store/93i8…-alacritty-mention-open`. Reading that wrapper:

```
export PATH=…/python3-3.12.14/bin:…:/nix/store/6sw3…-fzf-0.74.4/bin:…
exec …/python3-3.12.14/bin/python3 \
  /home/zach/workspace/devrc/scripts/mention-open.py "$@"
```

Two facts fall out. The **script is the working tree** — an edit is live on the next click.
The **fzf is pinned at 0.74.4**, which is the version every measurement in §2.3/§2.4 was
taken at (`fzf --version` → `0.74.4 (refs/tags/v0.74.4)`), so those measurements describe the
production binary rather than a coincidentally-matching one. But the **PATH is a store
list**, so a design that spawns a new binary needs a `home-manager switch` *and* an entry in
`test_the_alacritty_wrapper_PATH_covers_every_executable_the_handler_spawns`.

**(e) 🔴 `mention-open.py`'s own comment about the dim cap is STALE — overflow is NOT silent
any more, and the stale sentence is the one a reader would act on.**
`mention-open.py:1466-1471` says a ledger past the cap *"loses its LAST entries … with no
error and a row that still parses"*. That was true when written. `sanitize_dims` now emits a
**`dropped` counter** (`scripts/collector/invocation.py:122-143`): it computes
`dropped = max(0, len(items) - _MAX_DIMS)`, adds the key-collision losses inside the loop, and
writes `out["dropped"] = dropped` **after** the `[:_MAX_DIMS]` slice — so `dropped` does not
itself consume a slot, and a caller-supplied `dropped` is **filtered out first**, so it can
be neither forged nor masked. `_MAX_DIMS`' own block says it: *"a drop is now visible whatever
it is set to"*, and *"now against a `dropped` counter that makes the old silent failure
impossible either way"*.

So the real consequence of a 17th field is **a red test, not a lost column** —
`test_the_click_DIM_ledger_FITS_the_collectors_own_dim_CAP` asserts
`len(CLICK_DIM_FIELDS) <= _MAX_DIMS` and runs its own positive control (feeds 17 dims, watches
the collector drop the 17th). ⚠ **Two bounds are counted and two deliberately are not:**
`_MAX_DIMS` and `_MAX_KEY_LEN` (two long keys truncating to the same prefix) increment
`dropped`; `_MAX_VALUE_LEN` and `_MAX_LIST_ITEMS` **shorten a value the consumer still
receives** and are not counted. A demote dim is a boolean, so only the first bound can reach
it. Correcting the stale comment belongs in whichever PR touches that block.

**(d) The brief says `picks.jsonl` is absent here. CONFIRMED, and so is the consequence.**
`ls -la ~/.config/mention-open/` → three files, all `0600`, directory `0700`:
`known_repos.json` (18,692 B), `known_universe.json` (11,970 B), `known_ranges.json`
(13,299 B), all written 2026-10-02 16:11. **No `picks.jsonl` and no `target`.** So every
Tier-B claim in §5.3 is reasoning about the code plus the numbers already recorded in the
module's own docstrings — **not** a fresh measurement — and the one question that needs real
pick data (§11 Q3) is a declared gap. Nothing was written to the laptop.

### 1.3 A correction to the brief's framing of `--nth`

> **`--nth` excludes the row marker from MATCHING but NOT from RANKING.**

True, and the mechanism is narrower than it reads: what reaches the ranking is the marker's
**WIDTH**, not its text. `mention-open.py:463-472` records the measurement (two rows with
byte-identical field-2.. text and field-1 widths of 3 vs 6 come back in *opposite* order
under `--tiebreak=end --nth=2..`), and `:474-492` records that a query matching only the
marker digits returns **zero** rows under both alignments, with a positive control of 1 row
once the flag is dropped.

The consequence for this work is sharp and easy to miss: **a demote glyph must not change the
marker's width, and must not add a FIELD.** Adding a leading field would require `--nth=3..`,
which is a silent ranking change on every row; keeping the field count but varying its width
between demoted and non-demoted rows re-opens the width defect `PICKER_MARKER_RANK_W` exists
to close. §5.2 therefore recommends **no demote glyph at all**.

---

## 2. Measurements taken for this scope

All read-only. Nothing was written outside the scratchpad.

### 2.1 The universe, by plausibility class (live host state, 405 rows)

Source: `python3` over `~/.config/mention-open/{known_universe.json,known_ranges.json}`,
reproducing `plausibility_class`'s own branches. **Aggregates only — no row is named.**

| fact | value |
|---|---|
| universe rows | **405** |
| range-table rows | 405 — every universe row has an entry, so `UNKNOWN` is **0** today |
| `max_ref == 0` → `IMPOSSIBLE` | **235 (58.0%)** |
| `max_ref > 0` | 170 — median **9**, p90 **286**, max **5,313** |
| buckets of `max_ref` | `0`: 235 · `1-9`: 89 · `10-99`: 53 · `100-999`: 23 · `1000+`: 5 |

Class split per clicked `#N`, and the number that matters:

| `#N` | plausible | below | unknown | impossible | **rows ABOVE the impossible block** |
|---:|---:|---:|---:|---:|---:|
| 1 | 170 | 0 | 0 | 235 | **170** |
| 7 | 95 | 75 | 0 | 235 | **170** |
| 12 | 75 | 95 | 0 | 235 | **170** |
| 42 | 41 | 129 | 0 | 235 | **170** |
| 100 | 28 | 142 | 0 | 235 | **170** |
| 400 | 10 | 160 | 0 | 235 | **170** |
| 1291 | 5 | 165 | 0 | 235 | **170** |
| 1829 | 4 | 166 | 0 | 235 | **170** |

🔴 **The last column is constant.** `#N` reshuffles `plausible`/`below` *within* the 170 and
never changes its size, because the only thing that moves a row out of it is `max_ref == 0`.
So "the clutter above the already-last block" is **170 rows for every click this host can
receive**, and that is the quantity a demote feature is competing for.

⚠ `UNKNOWN == 0` is a property of **a freshly-converged table on this host today**, not of
the design. A host whose refresh has missed runs, or one with new repos, has a non-empty
`UNKNOWN` class sitting *above* `IMPOSSIBLE`.

### 2.2 The activity dimension — which the host state does not carry at all

Source: one `gh api user/repos --paginate --jq '.[] | {full_name, has_issues, pushed_at,
archived} | tostring'` (401 rows, rc 0), joined to the universe by lowercased `owner/repo`.
401 of 405 universe rows are named by the API; **4 are not** (a local checkout of somebody
else's repository is exactly the case `repo_universe`'s union exists for).

| group | n | `<90d` | `90d–1y` | `1–2y` | `>2y` | archived | no timestamp |
|---|---:|---:|---:|---:|---:|---:|---:|
| whole universe | 405 | 97 | 87 | 53 | **164** | 11 | 4 |
| **above the impossible block** | **170** | **78** | 23 | 11 | **55** | 8 | 3 |
| already `IMPOSSIBLE` | 235 | 19 | 64 | 42 | 109 | 3 | 1 |

Derived, and this is the sizing of the feature:

| quantity | value |
|---|---:|
| inactivity proxy (`pushed_at > 365d` **or** `archived`) | **219 of 405 (54.1%)** |
| …of which already `IMPOSSIBLE` (no help needed) | **152** |
| …of which in the 170-row top block (**the actual prize**) | **67** |
| top block after demoting those 67 | **170 → 103 (−39.4%)** |
| rows pushed `<90d` and not archived | **95**, of which 77 non-impossible |
| ranks a demoted row would occupy, demote term first, 219 demoted | **187..405 of 405** |

Per-`#N` effect of demoting that set, demote term **first** in the key:

| `#N` | top block today | with the demote set demoted | plausible | below |
|---:|---:|---:|---|---|
| 1 | 170 | **103** | 170 → 103 | 0 → 0 |
| 12 | 170 | **103** | 75 → 57 | 95 → 46 |
| 42 | 170 | **103** | 41 → 33 | 129 → 70 |
| 1291 | 170 | **103** | 5 → 5 | 165 → 98 |

🔴 **Two conclusions, and they point in opposite directions.**
The plausibility class does **not** subsume the ask: it removes 152 of the 219 inactive rows
and leaves **67 sitting in the block the operator scrolls**, so the feature is not
redundant. But the same 67 are **mechanically identifiable from a field the generator
already fetches** — so the *manual* demote key's unique territory is only the residue:
a repository that is recently pushed and that the operator still never wants. **That
residue is not measurable here** (§11 Q3), because the one artefact that would answer it —
`picks.jsonl` — lives on the laptop.

⚠ **`pushed_at` is a proxy, and I am naming its failure mode rather than asserting it is
"inactive".** A repo can be dormant in commits and live in issues (which is exactly what a
`#N` click is about), and a repo can be force-pushed by a bot. The direction of the error is
the dangerous one: auto-demoting a repo the operator is actively *filing issues* in.
`max_ref` is the issue-activity signal and it is already in Tier A; a combined predicate is
§6 W2's business, not a one-liner.

### 2.3 fzf's stdout contract under `--print-query` + `--expect` (0.74.4)

Method: a pty probe of the shape `test_mention_open.py` uses for its own interactive
measurements — rows on stdin (a pipe), stdout to a pipe, keys written to the pty master,
`TIOCSWINSZ` set to 40×120 because **`pty.fork()` leaves the terminal 0×0 and fzf then draws
nothing** (the first run of this probe failed for exactly that reason and reported
*"fzf never drew a prompt"* on all six rows — recorded here because a probe that measures
nothing looks like a result). Readiness gated on fzf's own `<matched>/<total>` counter
settling, never on the prompt. Flags identical to `PICKER_SH` except the prompt string.
Corpus: 5 synthetic `acme/wN` rows.

| ending | flags | raw bytes | lines |
|---|---|---|---|
| **A** Enter (selection) | today's | `\n<row>\n` | `['', '<row>', '']` — **2** |
| **B** Esc | today's | `\n` | `['', '']` — **1** |
| **C** Enter (selection) | `+ --expect=ctrl-x` | `\n\n<row>\n` | `['', '', '<row>', '']` — **3**, key line EMPTY |
| **D** Ctrl-X | `+ --expect=ctrl-x` | `\nctrl-x\n<row>\n` | `['', 'ctrl-x', '<row>', '']` — **3**, **row still printed** |
| **E** Esc | `+ --expect=ctrl-x` | `\n` | **1** — the esc bind still wins |
| **F** Ctrl-C | `+ --expect=ctrl-x` | *(empty)* | **0 bytes** — unchanged |
| **J** Ctrl-X on a **no-match** query | `+ --expect=ctrl-x` | `<query>\nctrl-x\n` | **2** — key present, **row ABSENT** |
| **K** Enter on a **no-match** query | `+ --expect=ctrl-x` | `<query>\n\n` | **2** — empty key, empty row |
| **L** Ctrl-R, two expect keys | `--expect=ctrl-x,ctrl-r` | `\nctrl-r\n<row>\n` | **3** — the key is named correctly |

A and B reproduce `PICKER_SH`'s existing table exactly, which is this probe's positive
control: an instrument that could not reproduce the four known rows would not be evidence
about the two new ones.

🔴 **Row J is the one that will be got wrong.** A demote press with **no matching row** still
writes the key line and still terminates fzf, with *no* row line. A parser that reads
`lines[PICKER_ROW_LINE]` and acts on the key alone would demote whatever `lines[2]` happens
to be — `""` today, which is harmless, but the handling must be an **explicit third outcome**
("demote requested, no target → do nothing"), not an accident of an empty string.

Action-vocabulary availability at 0.74.4, with its negative control:

| probe | result |
|---|---|
| `--bind=ctrl-x:zzznosuchaction` | **rc 2, `unknown action: zzznosuchaction`** ← the negative control; the instrument can go red |
| `--bind=zzznosuchkey:accept` | rc 2, `unsupported key: zzznosuchkey` |
| `--bind=ctrl-x:print(DEMOTE)+accept` | rc 0 — `print(…)` **exists** |
| `--bind=ctrl-x:execute-silent(true {})+reload(cat /dev/null)` | rc 0 — both **exist** |
| `become(…)`, `transform(…)`, `print-query+accept`, `refresh-preview` | rc 0 — all exist |

### 2.4 🔴 Does the demote survive a typed query? No.

The load-bearing measurement, and the one that reframes the feature. Corpus: 30 rows under
the live flags (`-i --tiebreak=end --nth=2..`). Row 1 is `acme/widget-platform-legacy` at the
**front** of the input (marker `1`); the last row is `acme/widget` at the **back** (marker
`405`) — i.e. the position a demote would put it in. `acme/widget` is the better fzf match
for every query used. Run through `fzf -f <query>`, which applies the same scorer and
tiebreaks.

| query | matched | first row back |
|---|---:|---|
| `widget` | 2 | **`405  … acme/widget …`** |
| `wid` | 2 | **`405  … acme/widget …`** |
| `acme/widget` | 2 | **`405  … acme/widget …`** |
| `w` | 2 | **`405  … acme/widget …`** |
| *(empty — the control)* | 30 | `1  … widget-platform-legacy …` **first**, `405  … widget …` **last** |

The empty-query control is what makes the four rows above evidence: it shows the instrument
*does* return input order when nothing is typed, so the inversion on every typed query is
fzf's score overruling input order and not a broken probe.

**So the honest statement of what a sort-key demote buys:** it clears the un-typed first
screen (**19 visible rows** at `PICKER_LINES = 22`) and the un-typed scroll. It does
**nothing** once a character is typed. `mention-open.py:2527-2534` already says this about
the ordering generally — *"the first keystroke hands ordering to fzf's own score and the
ranking becomes invisible to anyone who TYPES"* — and `pick()` records the operator's own
complaint *"I end up typing the repo name anyway"*. **The feature as literally worded
("always appear at the bottom") is not deliverable without filtering.**

---

## 3. The mechanism — how a keypress is distinguished from a pick and from an abort

This is the centre of the brief. The constraint set is unusually tight, so the options are
enumerated against it rather than against fzf's manual.

**The constraints a mechanism must satisfy, each with its source:**

1. **It must coexist with `esc:print-query+abort`**, not displace it — operator decision
   2026-09-20 (`mention-open.py:2614`).
2. **The action order inside any bind is load-bearing and a reorder is SILENT**:
   `esc:abort+print-query` is *accepted* by fzf and writes 0 bytes at rc 130
   (`:2590-2600`). Three guards catch it today, two of them by accident.
3. **No private repository name may reach argv, an env var, a file, a log or stderr** — the
   whole reason the rows travel by FIFO (`:2462-2493`), and `run_picker`'s own comment: *"THE
   ROWS NEVER TOUCH argv"*.
4. **The picker is ONE-SHOT and FIFO-fed.** `run_picker` writes the payload into a FIFO once
   and the parent drains the choice FIFO for `PICKER_OUT_LINES` lines. There is no command
   fzf could re-run to re-read the list.
5. **No new binary** without a `home-manager switch` and a wrapper-PATH ledger entry (§1.2c).
6. **`PICKER_SH` is pinned as a WHOLE STRING in two files** — the module constant and
   `EXPECTED_PICKER_SH` in `test_mention_open.py:2944`, plus the `_ESC_PRINTS_QUERY`
   substring pin at `:1861`. Any flag change moves both in the same commit.

### 3.1 `--expect=<key>` — **RECOMMENDED**

fzf completes on the key and names it on its own output line. Measured (§2.3 C/D/E/J/L).

**Why it fits:** it introduces no subprocess, no `{}` substitution and therefore no argv
exposure (constraint 3); it does not touch the esc bind (1) and has no action order to get
wrong (2); it needs no reload (4) and no new binary (5); and `PICKER_OUT_LINES` /
`PICKER_ROW_LINE` are **already derived from the flag string**, so 2→3 plus a new
`PICKER_KEY_LINE = 1` is a mechanical consequence rather than a hand-written literal — the
same derivation `test_the_picker_OUTPUT_LINE_count_is_derived_from_the_FLAG` already guards.

**Failure modes, named:**
- 🔴 **It TERMINATES the picker.** One demote per invocation; demoting five repos is five
  clicks. That is the honest cost and it is the main argument against this option.
- 🔴 **Row J**: key with no row. Must be an explicit "no target" outcome (§2.3).
- ⚠ **`PICKER_OUT_LINES` 2→3 lengthens the short-ending path.** Enter-with-no-match now
  writes 2 lines where the loop waits for 3, so it falls through to the `proc.poll()` arm
  instead of satisfying the newline count. The outcome is identical (`row == ""` →
  dismissal) and there is no new hang, because fzf has already exited — but the code path
  that executes for the commonest non-selection ending **changes**, and the three existing
  short-ending tests are the ones that must be watched.
- ⚠ **It burns a keybinding on the live click path.** Like the esc bind, that is an operator
  decision, not an implementation detail (§8).

### 3.2 `bind` + `execute-silent` (+ `reload`) — **REJECTED**

Keeps the picker open, which is the one thing `--expect` cannot do. Rejected on two
independent grounds, either of which is sufficient:

- 🔴 **It breaks constraint 3.** Any use of `{}` substitutes the selected row — carrying a
  private `owner/repo` — into the command string, which becomes the argv of the spawned
  shell and is visible in `/proc/<pid>/cmdline`. The module's disclosure contract enumerates
  where a universe row may go and argv is not on the list; the FIFO machinery exists
  *because* of this. There is no `{}`-free spelling that still knows which row to demote.
- 🔴 **`reload` has nothing to reload (constraint 4).** The list arrived on a FIFO that was
  drained once; `reload(<cmd>)` needs a command that re-emits the rows, and the only
  candidate would re-render the universe in a child process — i.e. constraint 3 again, one
  level out. Without a reload the demoted row **stays where it is on screen**, so the
  operator gets no feedback that anything happened.

### 3.3 `bind` + `print(…)` or a second `print-query+abort` variant — **REJECTED, with a note**

`print(DEMOTE)` exists at 0.74.4 (§2.3) and `ctrl-x:print(DEMOTE)+accept` would emit a
sentinel plus the row. It is strictly worse than `--expect` for this job: it is a **bind**,
so it inherits constraint 2's silent-reorder hazard (`accept+print` would terminate first and
print nothing), it spells its own vocabulary where `--expect` reports the key fzf actually
saw, and it adds a sentinel string that has to be kept out of the row namespace. It buys
nothing `--expect` does not already give.

A `ctrl-x:print-query+abort` variant is worse still: it is **byte-indistinguishable from the
esc ending** (both `<query>\n`), and `mention-open.py:2564` records that those two are
already deliberately indistinguishable and that nothing reads the exit status.

### 3.4 Filtering (a two-list picker) — the only shape that delivers the sentence as written

Given §2.4, the only way a demoted repo is *always* at the bottom — or rather, out of the
way — under a typed query is for it **not to be in the list**. The module refuses filtering
on a stated argument (`:168-173`, `:648-655`): *"Hiding a row on a day-old snapshot is
unrecoverable from inside the picker — the operator cannot type at a row that is not
there"*.

🔴 **That argument is about a SNAPSHOT-DERIVED filter, and a demote list is not one.** The
refusal's premise is that the table is a day-old measurement which may be wrong about the
repo; a demote list is the **operator's own standing instruction**, and it is wrong only if
they changed their mind. And the unrecoverability — the load-bearing half — is **closable
here in a way it is not for `IMPOSSIBLE`**: a second `--expect` key, or a toggle, makes the
hidden rows one keypress away, which is strictly better than "dismiss and type a URL by
hand".

I am **not** recommending this for the first PR, because it is the larger change and because
it reverses a written refusal that deserves its own review. But it is the option that
actually matches the operator's sentence, and §8 asks about it rather than quietly shipping
the half that does not.

---

## 4. Where the term has to go in the sort key — and the trap

**Verified key, post-#1829** (`order_universe`, the `key()` closure):

```
(klass, distance, -scores.get(full.lower(), 0.0))
```
with `klass, distance = measured_rank_key(full, num, ranges, margin)`.

🔴 **A demote term must be element 0.** Anywhere else and it cannot reach the bottom: a
demoted `PLAUSIBLE` repo (`klass == 0`) still outranks a non-demoted `BELOW` one
(`klass == 1`) for any key of the form `(klass, …, demoted, …)`. The brief's statement is
confirmed by reading the key, not inferred.

So the key becomes `(demoted, klass, distance, -score)` with `demoted ∈ {0, 1}`.

**Two consequences to state out loud rather than discover:**

- **A demoted row sinks below the 235-row `IMPOSSIBLE` block too.** That *is* "the bottom",
  and it is what was asked for — but it means a demoted-and-plausible repo ranks below a
  repo that demonstrably cannot hold the number. Correct by the request; worth saying.
- **A fifth plausibility class is the WRONG shape, and it is the obvious wrong turn.**
  `PLAUSIBILITY_CLASSES` is pinned two-way and a `CLASS_DEMOTED = 4` would slot in neatly
  — and it would conflate the two kinds of evidence the module is emphatic about keeping
  apart: Tier A is evidence about the **repository**, Tier B about the **operator**
  (`order_universe`: *"no number of past picks makes a repo with zero references able to
  answer `#1291`"`). It would also corrupt the `plausibility` telemetry dim, which is the
  entire evidence base #1829 rests on (`below` 54 vs `plausible` 17) — the exact mistake
  #1829 spent a whole extra dim (`plausibility_premargin`) avoiding. **A separate first
  term, not a fifth class.**

### 🔴 The trap: `measured_rank_key` and the promotion gate

`measured_rank_key` was **extracted specifically so the sort and the promotion gate cannot
disagree** (its own docstring). The gate, at `mention-open.py:4134-4143`:

```python
top_key = (measured_rank_key(repo_of_github_url(ordered_rows[0]["url"]),
                             num, order_ranges)
           if ordered_rows else None)
top_is_separated = bool(
    top_key is not None
    and top_key[0] == CLASS_PLAUSIBLE
    and not any(measured_rank_key(…) == top_key for c in ordered_rows[1:]))
```

**Widen `measured_rank_key` to `(demoted, klass, distance)` and `top_key[0]` stops being the
class.** `CLASS_PLAUSIBLE` is `0` and a non-demoted flag is also `0`, so the comparison
**still passes** — for every non-demoted row, *whatever its class*. An `IMPOSSIBLE` row then
gets promoted above the pane guess and `guessed_note` captions it *"this host's best-ranked
repository"*. Nothing about the tuple's shape makes that visible, and the module already
records (`:4187-4199`) that deleting the adjacent `ORDER_APPLIED` clause leaves the whole
suite green — so the suite's ability to see a defect in this gate is already measured as
weak.

**Two shapes, and I recommend the second:**

| shape | cost | risk |
|---|---|---|
| **(i)** widen `measured_rank_key` to 3 elements and **convert its return to a `NamedTuple`** so the gate reads `top_key.klass`, in the same commit | touches the gate, the sort, and every call site/test that index-unpacks the pair | the conversion is mechanical and a missed site is a `TypeError`, not a silent wrong answer — but there are several call sites |
| **(ii)** leave `measured_rank_key` at `(klass, distance)` and add the demote term **only** in `order_universe`'s `key()` | one function, one line | the sort and the gate now *do* disagree — **but in the conservative direction**, which is the posture the module already takes for Tier B's absence from the gate (`measured_rank_key`: *"it under-promotes rather than making a false claim"*). The uniqueness half compares demote-free keys, so a demoted row tying a non-demoted one reads as "not separated" and the promotion is **skipped**. The one divergent state — row 0 is demoted — can only arise when **every** ranked row is demoted, in which case row 0 genuinely is the best-ranked row. |

Shape (ii) needs a comment in `measured_rank_key` saying the demote term is deliberately
absent *and why*, in the same register as the existing Tier-B paragraph — otherwise the next
reader "fixes" the asymmetry and re-opens the `top_key[0]` hole.

---

## 5. The design questions, answered

### 5.1 Where the demote list lives — and can `regen-known-repos.py` clobber it?

**Answer: no, and the mechanism is not "we were careful", it is that the generator cannot
name the file.**

`regen-known-repos.py` writes exactly three paths, each a tmp-then-`os.replace` of a
*specific* file: `DEFAULT_PATH` (`known_repos.json`, `:728-730`), `DEFAULT_UNIVERSE_PATH`
(`known_universe.json`, `:660-662`), `DEFAULT_RANGES_PATH` (`known_ranges.json`,
`:644-646`), each overridable by `--path` / `--universe-path` / `--ranges-path`
(`:735-737`). There is **no glob, no `rmtree`, no directory rewrite** — `grep -n
'write_text\|os.replace\|replace(\|rmtree\|unlink\|glob'` over the script returns only those
three tmp-write triples plus three `mkdir(mode=0o700, exist_ok=True)` calls. `picks.jsonl`
has coexisted in that directory across every run on both hosts and is the existing proof.

So a fourth file is safe **provided it is a fourth file**. Two hard requirements:

- 🔴 **It must NOT be a value inside one of the three generated files.** Those are
  replaced wholesale on a 4-hourly/daily timer, so a demote flag written into
  `known_universe.json` would be silently erased by the next refresh — a data-loss bug whose
  symptom is "my demotions keep coming back" and whose cause is 4 hours away from the
  action.
- 🔴 **It must be added to `HOST_STATE_CONSTANTS`** in `test_mention_open.py:537-551`, with a
  `MENTION_OPEN_DEMOTED` env door, **in the same commit**. That ledger is pinned two-way and
  its discovery walk resolves to a **fixpoint** over derived constants, so a path constant
  with no ledger entry fails the suite loudly. Without the env door, a subprocess test reads
  — and `record_pick`-style, *writes* — the operator's real 0600 data; the module records
  nine tests having done exactly that.

**Shape: a JSON list of `owner/repo`**, `0600`, parent `0700` via the existing `narrow_dir`,
at `~/.config/mention-open/demoted.json`. Chosen because it is the **one shape an existing
leak detector already fires on** (§1.2b) and because the loader can be a near-copy of
`load_known_universe` — same every-failure-is-`[]` posture, same `_OWNER_REPO_RE` filter,
same lowercased comparison. The cost is that it stores no timestamp, so "when did I demote
this" is unanswerable; §5.2 argues that is acceptable because the undo surface is the picker
itself, not a log.

⚠ **Writing it is a new write path on the click path**, and `record_pick`'s hard-won
properties are the spec: it must **never raise** (an unwritable file costs the demote, never
the click), it must chmod `0600` after write, and it must be tmp-then-`replace` since a list
rewrite is not append-atomic the way `picks.jsonl`'s `O_APPEND` line is.

### 5.2 Undo — the trap the brief is right to name

A demote with no un-demote is a one-way ratchet on a 405-row list. Three sub-questions:

**How does the operator SEE what they demoted?** 🔴 **By the list still containing it.** The
module's "rank, never filter" posture (§3.4) means a demoted repo is still there, still
typeable, still carrying a rank marker in the 187–405 range (§2.2). So "what have I
demoted?" is answered by typing the name and reading the rank. **No new surface, no new
disclosure sink.** Add a cardinality to `universe_note` — *"N demoted"* — which is exactly
the category that function already emits (a count and a date, never a row).

**How do they reverse it?** A **second `--expect` key** on the same picker, measured to work
(§2.3 row L: `--expect=ctrl-x,ctrl-r` reports the key correctly and still prints the row).
Press it on a demoted row and the entry is removed. This is why `--expect` taking a
comma-separated list matters and why it was measured.

**What NOT to do, and why:** do not put the demote list behind `--print` or a new CLI
subcommand. `--print` writes to stdout, and while the operator's own terminal is arguably
the same category as the picker, the module's disclosure enumeration does not list stdout and
`notify()` prints — so the refusal paths deliberately name only the clicked text. Opening a
second route for universe rows to leave the process needs its own argument, and it buys
nothing the in-picker view does not.

⚠ **The undo key is where the "one demote per invocation" cost bites hardest.** If the
operator over-demotes, each restore is its own click. That asymmetry should be said to them
before they adopt it (§8).

### 5.3 Interaction with Tier B learning — suppress, or display only?

**Recommendation: display position only. A demote must NOT suppress learning, and it must
not delete pick history.**

The argument, from the module's own measurements:

- The documented degradation — top-1 `82.5% → 43.1%` across a warming log, fixed by
  reordering the key so Tier B sits *under* Tier A rather than over it — was caused by
  **every repo ever picked earning a non-zero score**, letting more and more rows outrank
  Tier A's correct first choice. A demote term as element **0** is *immune* to that by
  construction: it sits above Tier A, which sits above Tier B, so no accumulated score can
  lift a demoted row. The fix that cured the degradation is the same structure that makes a
  demote safe.
- **Suppressing learning would be a silent second behaviour on one keypress.** The operator
  asked for a display change ("always appear at the bottom"). Making the key also erase or
  freeze pick history is scope the words do not contain, and it is *irreversible* in a way
  the ordering is not — `picks.jsonl` is append-only, and a demote that filtered it on read
  would permanently discard the evidence that the demote was a mistake.
- **A demoted repo that gets picked anyway is the single most valuable signal the feature can
  produce.** If Tier B keeps scoring it, the pick log records "demoted, and chosen anyway",
  which is a mechanical test for a wrong demote. Suppress the learning and that signal is
  destroyed — the same mistake #1829 avoided by keeping `plausibility_premargin`.

⚠ **Stated as a gap, not a finding:** I could not re-measure Tier B's behaviour against real
data, because `picks.jsonl` is absent on this host (§1.2d). The reasoning above is from the
code and from the numbers already recorded in `order_universe`'s docstring.

### 5.4 Does this subsume or conflict with the plausibility class?

**Neither subsumes the other, and the overlap is measured (§2.1, §2.2).**

- `IMPOSSIBLE` already removes **235 of 405 (58.0%)**, and it catches **152 of the 219**
  inactive-looking rows for free.
- It leaves **67 inactive rows in the 170-row top block**, because the class is a statement
  about *numeric reachability*, not activity: a repo whose `max_ref` is 9 and whose last push
  was three years ago is `PLAUSIBLE` for `#7` forever. **That orthogonality is the feature's
  actual territory**, and it is worth 170 → 103 (**−39.4%**).
- **No conflict**, provided the demote term is a separate first element rather than a fifth
  class (§4).

🔴 **But the feature is smaller than it looks, in two independent ways, and I am willing to
say so:**

1. **The visible surface is 19 rows, not 170.** `PICKER_LINES = 22`, so the operator sees
   ~19 of 405 rows at a time. Moving a repo from rank 90 to rank 300 is invisible — it was
   already off-screen. The real benefit is confined to the first screen and to the un-typed
   scroll.
2. **It evaporates the moment they type** (§2.4), which is their reported habit.

So the measured benefit is: **a cleaner first screen, and nothing else.** Whether that is
worth a keybinding on the live click path plus a new host-state file is an operator
judgement, and §8 asks it as one. The cheaper way to buy most of the same first screen is
§6 W2, which needs no key at all.

### 5.5 Telemetry — 0 free slots on the ref I scoped against, so this argues an eviction

**The budget, stated with its ref, because the number is ref-dependent and quoting it
without one is how the brief I was given came to be half wrong:**

| ref | `CLICK_DIM_FIELDS` | `_MAX_DIMS` | free |
|---|---:|---:|---:|
| `origin/main` `c4adcd34` | **14** | 16 | 2 |
| `origin/fix-mention-picker-staleness-margin` `47cbab82` (**what this scope targets**) | **16** | 16 | **0** |

🔴 **The cap is PER-EVENT, not a shared pool.** `sanitize_dims` is called from
`build_fields`, whose docstring says it builds the spool fields *"for one invocation event"*.
So a demote dim rides the **same click row** as the existing ledger and genuinely competes
with it — while a *different* sink emitting its own event gets its own 16 and competes with
nothing here. **This section therefore stands alone and is not blocked on any other
telemetry work.** (An earlier framing of this scope had it waiting on a sibling decision;
that was wrong, and per-event is why.)

And the failure mode is a **red test, not a lost column** — §1.2e, which also records that
`mention-open.py`'s own "with no error" comment is now stale.

**So: shipping `demoted` against #1829's shape needs an eviction or a cap raise. Argued, in
the order I would take them:**

1. ✅ **Ship the demote with NO new dim first.** `rank` and `ordered` are already emitted, so
   "did anyone pick a demoted repo" is answerable today: a demoted row shows as
   `ordered = true` with a `rank` above the non-demoted count. Weaker than a boolean — the
   threshold moves as the demote list grows, so a query written in week one is wrong in week
   four — but it costs **zero** budget and it answers the one question that decides whether
   the demote was a mistake. **This is what PR 3 should do.**
2. 🔴 **If a boolean is wanted, evict `tier_a` — and the argument is the repo's own.**
   `click_dims`' ledger prose says of it: *"`tier_a` is the same shape but **NEAR-CONSTANT** —
   read it as a partial-table detector"*. Its unique signal is a **partially populated**
   range table, and on this host that state is currently **unobservable**: MEASURED (§2.1)
   405 of 405 universe rows have a ranges entry, so `UNKNOWN` is 0 and `tier_a` is the count
   of ordered rows — a restatement of `offered_total` minus the pinned rows. What a reader
   would lose is covered from two directions already: `ordering` reports `no-table`/`stale`
   (the whole-table failures), and `plausibility` plus `plausibility_premargin` report the
   chosen row's class. ⚠ **The honest cost:** the *partial*-table case — a table that exists,
   is fresh, and answers for only some rows — loses its only detector. That state is real
   (a host with new repos since the last refresh) even though it is empty here, so the
   eviction is a trade and must be argued in the PR, not asserted. An evicted dim is also a
   **consumer contract change**, which is exactly what the ledger exists to make loud.
3. ⚠ **Raising `_MAX_DIMS` 16 → 17 is possible but needs its own argument, and the repo has
   already rejected a bigger version of it.** `invocation.py:73-78`: *"WHICH IS WHY IT IS 16
   AND NOT HIGHER. The cap is this module's ceiling on how much an ACCIDENT can carry —
   `_MAX_DIMS` × `_MAX_VALUE_LEN` — and it applies to EVERY caller … An earlier version of
   this change took it to 24 for headroom; that doubled the leak ceiling for a second,
   uninvolved caller to buy room nobody had asked for."* So a raise is a **leak-ceiling**
   decision for every caller, not a local one. 16 → 17 is the minimum such raise and is
   defensible on those terms; it is not free, and it is not this feature's call to make
   alone.
4. ❌ **Do not evict `plausibility_premargin` or `margin`.** They are #1829's instrument for
   the measurement that justified it, and removing either destroys the ability to repeat that
   measurement — the specific failure that PR spent a whole dim avoiding. Likewise not
   `queried`: it is the newest instrument for a live operator complaint.

---

## 6. Work items

Dependencies: **W1 → none.** **W2 → none** (and it is the one that may make W3/W4
unnecessary). **W3 → W1.** **W4 → W3.** **W5 → W3, and it is OPTIONAL** — nothing outside
this doc gates it (§5.5). **W6 → W3** (can ship narrower).

### W1 — the ordering term and the gate, with no UI
**Files:** `scripts/mention-open.py`, `scripts/tests/test_mention_open.py` ·
**Deps:** none · **Behaviourally inert without W3**

Add `load_demoted()` (a near-copy of `load_known_universe`: every failure `[]`,
`_OWNER_REPO_RE` filter, lowercased set), the `DEMOTED_PATH` constant with its
`MENTION_OPEN_DEMOTED` env door, the `HOST_STATE_CONSTANTS` ledger row, and the autouse
fixture redirect. Add the demote term as element **0** of `order_universe`'s key via shape
(ii) of §4, with the "deliberately absent from `measured_rank_key`" comment.

**Why first:** it is the whole ranking change, it touches no fzf flag, no keybinding and no
write path, and with an absent demote file it is a **provable no-op** (the demote set is
empty, every `demoted` is 0, `sorted` is stable — the same cold-start argument
`order_universe` already makes for an empty range table). Independently revertible.

### W2 — 🔴 the cheaper alternative: an activity term derived from data already fetched
**Files:** `scripts/regen-known-repos.py`, `scripts/mention-open.py`, both test files ·
**Deps:** none

Project `pushed_at` and `archived` in the existing `gh api user/repos` `--jq` (§1.2a) and
emit them into a new generated file beside the other three. Then the ordering gets a
*measured* staleness/archived term instead of a curated one.

**Why this is in the work items and not in "alternatives considered":** it reaches **67 of
the 67** rows a hand-curated demote list would be built to reach (§2.2), with **zero
keypresses**, **no keybinding on the live click path**, **no undo problem** (it self-corrects
the moment the operator pushes), and **no fzf change at all**. It is the
question-the-requirement answer, and the-algorithm says to ask it before adding the key.

**Why it is NOT a drop-in replacement, stated honestly:**
- `pushed_at` is a **proxy** and its error direction is the bad one — a repo dormant in
  commits but live in issues is exactly what a `#N` click is about (§2.2's warning). A
  combined predicate with `max_ref` needs its own measurement.
- It cannot express "active, and I still never want it", which is the residue only a manual
  demote reaches — and that residue is **unmeasured** (§11 Q3).
- It adds a fourth generated file (or a fourth value shape) and therefore a fifth leak
  detector or a shape that reuses an existing one (§1.2b).
- ⚠ It changes what the **generator** emits, which is a timer-driven unit; a bad projection
  is 4 hours of silence away from being noticed.

**It is complementary, not exclusive.** W2 handles the mechanical 67; W3 handles the residue.

### W3 — the demote key on the picker
**Files:** `scripts/mention-open.py`, `scripts/tests/test_mention_open.py` ·
**Deps:** W1 · **🔴 Needs an operator decision — §8**

Add `--expect=<demote-key>,<restore-key>` to `PICKER_SH`; let `PICKER_OUT_LINES` /
`PICKER_ROW_LINE` re-derive and add `PICKER_KEY_LINE`; extend `run_picker` to return the key
alongside the row; add two outcomes to the `PICKED_*` vocabulary (demote-requested,
restore-requested) and a **third** for "key with no target" (§2.3 row J); write the demote
file with `record_pick`'s never-raise posture; move `EXPECTED_PICKER_SH` in the same commit.

🔴 **Both key strings must be chosen so they are not already `abort` keys.** fzf binds
`abort` to `ctrl-c ctrl-g ctrl-q esc`, plus `ctrl-d` on an empty query — all five measured to
write 0 bytes (`mention-open.py:2571-2588`). Claiming one of them would turn a cancel into a
demote.

### W4 — the "N demoted" cardinality in `universe_note`
**Deps:** W3 · Trivial, and it is what makes W3 discoverable. A count and a key hint; never
a row (§5.2).

### W5 — the `demoted` click dim, **and only if a boolean is actually wanted**
**Files:** `scripts/mention-open.py`, `scripts/tests/test_mention_open.py` · **Deps:** W3

🔴 **0 free slots against #1829 (§5.5).** This item is "evict `tier_a`, add `demoted`", or it
is "raise `_MAX_DIMS` to 17 on a leak-ceiling argument" — **not** "add a field". It is
**optional**, because §5.5 option 1 answers the only question that matters with dims that
already exist. Fold the stale-comment correction (§1.2e) in here. **Do not** evict
`plausibility_premargin`, `margin` or `queried`.

### W6 — 🔴 NOT PROPOSED: suppressing Tier B for demoted repos
**Deliberately out of scope**, with the argument in §5.3. A demote changes display position
only.

---

## 7. Sequencing

| PR | item | independently revertible of | why here |
|---|---|---|---|
| **1** | **W1** | everything | The whole ranking change. Provable no-op with an absent demote file, so it can land and sit inert while the operator decides about the key. Touches no flag, no key, no write path. |
| **2** | **W2** | W1, W3 | The cheaper 39.4%. If the operator takes this and finds the first screen clean enough, **PR 3 may never be needed** — which is the outcome the-algorithm is asking for. |
| **3** | **W3 + W4** | W2 | The key itself. Rebinds the live click path and adds a write path, so it is the one that needs the go-ahead and the one to ship alone. |
| **4** | **W5** | all | **Optional.** Needs an eviction or a cap raise (§5.5), and §5.5 option 1 means PR 3 is already instrumented without it. Ship only if the boolean is wanted on its own merits. |

**PR 2 should be measured before PR 3 is built.** The whole case for the manual key rests on
a residue nobody has sized (§11 Q3), and PR 2 is what makes that residue observable: after it
lands, "which repos are still cluttering the first screen" is a question with an answer.

---

## 8. The FORK — one question for the operator, before PR 3

Three coupled decisions, presented as one, because answering them separately produces an
incoherent feature.

1. 🔴 **"Always at the bottom" cannot be delivered by ordering — do you want the ordering
   version anyway, or the filtering version?** MEASURED (§2.4): a demoted row placed last
   came back **first** for all four typed queries. So an ordering demote cleans the un-typed
   first screen (19 rows) and does nothing once you type — which your own reported habit is.
   The version that actually matches your sentence **hides** demoted rows with a key to bring
   them back, which reverses a written refusal in the module and is the larger change.
   **My recommendation: take the ordering version first (PR 1 + PR 3) because it is small and
   reversible, and treat filtering as a second decision once you have used it** — but you
   should know before you adopt it that it will not survive your typing.
2. 🔴 **Would the automatic version do instead?** `pushed_at` / `archived` are free on a call
   the generator already makes, and demoting the >1y-or-archived set reaches **67 of the 67**
   rows (§2.2) with no key, no file to curate, no undo, and self-correction when you push.
   The manual key's unique value is "active, and I still never want it", which **nobody has
   measured** — `picks.jsonl` is on the laptop. **My recommendation: PR 2 first, then decide
   whether PR 3 is still worth a keybinding.**
3. **Which two keys, and is a keybinding on the live click path acceptable?** This is the
   same category as the 2026-09-20 `esc:print-query+abort` decision, which was held back for
   you specifically. The demote key terminates the picker, so demoting five repos is five
   clicks, and each restore is its own click too.

**Blast radius if all of §6 ships:** the picker's row order changes; two keys on the live
click path are claimed; one new `0600` file in `~/.config/mention-open/` is written from the
click path. Nothing leaves the host, no row reaches a log or a toast, no telemetry field is
added at all unless W5 is taken and its eviction argued (§5.5), and nothing is auto-opened
that was not selected.
**Your call to proceed.**

---

## 9. Test coverage, specified

For each item: the guard, the base it must be **red** at, the mutation that must kill it
**with that guard's own message**, and the fixture states required. Base for every "red at"
claim: `origin/fix-mention-picker-staleness-margin` = `47cbab82` merged onto `origin/main`
= `c4adcd34` (clean, §1.1).

### W1 — the ordering term

- **Guard:** `test_a_DEMOTED_repo_ranks_below_every_NON_demoted_row_INCLUDING_impossible_ones`
  — a fixture with one demoted `PLAUSIBLE` row and one non-demoted `IMPOSSIBLE` row,
  asserting the demoted one is **last**.
- 🔴 **This is the test that proves the term is element 0**, and the fixture must contain
  exactly that cross-class pair. A fixture whose demoted row is also its worst-class row
  cannot see a term placed anywhere else in the key, and would pass for a demote term at
  position 3.
- **Red at base:** `load_demoted` does not exist ⇒ `AttributeError`. Report the matrix.
- **Mutation that must die with this guard's own message:** move the demote term from
  position 0 to position 3 (`(klass, distance, -score, demoted)`). The failure must name
  *which* class outranked the demoted row, not merely "order differs".
- 🔴 **Isolate the mutation.** Do not mutate "the whole key"; mutate only the term's
  **index**. And pick fixture `max_ref` values that are **not** equal across rows and not
  equal to any constant the assertion names, so the guard cannot be satisfied by rows that
  land on their own class boundary.
- **No-op guard:** `test_an_ABSENT_demote_file_leaves_the_order_BYTE_IDENTICAL` — the same
  universe through `order_universe` with and without the feature, asserting list equality.
  Mutant: make `load_demoted` return a non-empty default ⇒ must go red.
- **Fixture states required:** demote file **absent**; present but **unparseable**; present
  with **non-`owner/repo`** entries (must be filtered, not crash); present naming a repo
  **not in the universe** (must be a no-op, not an insertion); a **case-mismatched** entry
  (`acme/Widget` vs `acme/widget` — must demote, one repository).
- 🔴 **The gate guard, and it is the one that would otherwise be missed:**
  `test_the_promotion_gate_still_reads_the_CLASS_and_not_the_demote_flag` — a fixture whose
  top ranked row is `IMPOSSIBLE` and non-demoted, asserting **no promotion** and that
  `guessed_note` does not claim "best-ranked". **Mutation:** widen `measured_rank_key` to
  return `(demoted, klass, distance)` without touching the gate ⇒ this test must go red with
  its own message. Without this guard, §4's trap ships green.

### W2 — the activity term

- **Guard (generator):** `test_the_user_repos_projection_carries_the_ACTIVITY_fields` — a two-way
  pin on the `--jq` field set, so a field added to the file without the projection, or
  removed from the projection while a reader still consults it, fails.
- **Guard (ordering):** an archived repo and a >1y repo each rank below their same-class
  non-stale peers. **Red at base** by construction (no such file).
- 🔴 **The leak sweep is a required co-guard**, not an afterthought: the new generated file
  must be shown to make `test_the_incident_guard_CAN_GO_RED` fire when planted as a tracked
  file. If its shape is covered by none of the four detectors (§1.2b), the fifth detector and
  its own negative control ship in the same commit.
- **Fixture states:** `pushed_at` absent; `pushed_at` malformed; `archived` absent; a repo in
  the activity file but not the universe, and vice versa.

### W3 — the key

- 🔴 **Guard (the contract, and it must be INTERACTIVE):**
  `test_the_DEMOTE_key_is_reported_on_its_own_output_line` — drive the real `PICKER_SH`
  through the existing pty harness, send the demote key, assert the three-line shape with the
  key on `PICKER_KEY_LINE`. **The existing harness is the instrument**: it gates on fzf's own
  `<matched>/<total>` counter settling, and `pty.fork()` leaving the terminal 0×0 is a
  measured way to make this test pass while observing nothing (§2.3's first run).
- **Positive control, reported as a pair:** the same harness with `--expect` dropped must
  return **two** lines. "3 lines with the flag, 2 without" is the claim; "3 lines" alone is
  not.
- **Guard (row J):** `test_a_DEMOTE_press_with_NO_MATCHING_ROW_demotes_NOTHING` — typed
  no-match query plus the demote key ⇒ the demote file is **unchanged** and the outcome is
  the explicit no-target one. **Mutation:** act on the key alone ⇒ must red.
- **Guard (the esc ending is unchanged):** the three existing short-ending tests must stay
  green, and one must be re-asserted at the **new** `PICKER_OUT_LINES` — because §3.1's third
  failure mode changes which code path the commonest non-selection ending takes.
- **Guard (the whole-string pin moves):** `EXPECTED_PICKER_SH` updated in the same commit.
  🔴 Do **not** weaken `_ESC_PRINTS_QUERY` to a `"print-query"` substring test while adding
  the flag — the module records that this would pass on a reordered bind while production
  silently wrote nothing.
- **Guard (the key is not an abort key):**
  `test_neither_DEMOTE_key_is_one_of_fzfs_ABORT_keys` — pin against the enumerated set
  `{ctrl-c, ctrl-g, ctrl-q, esc, ctrl-d}`, as an **enumeration** so an unknown key is a
  finding by default.
- **Guard (never raises):** `test_a_DEMOTE_CANNOT_RAISE_when_the_demote_file_is_UNWRITABLE`
  — modelled on `test_record_pick_CANNOT_RAISE_when_the_log_is_UNWRITABLE`, and 🔴 with the
  same caution its sibling records: a flat `chmod(0o700)` on the parent *removes the very
  condition the test exercises*, so the fixture must narrow by **stripping** bits, not setting
  a mode.
- **Guard (disclosure):** the demote path must be shown to put **no** repository name in
  `notify()`, in the spool payload, or in any child argv. The existing argv/disclosure tests
  are the model; the new assertion is that `PICKER_SH` contains **no `{}`**.
- **Mutation sweep caution:** run it under `PYTHONDONTWRITEBYTECODE=1` (or clear
  `__pycache__` between mutants) and keep one known-fatal mutant in the batch as a positive
  control. A same-length edit landing in the same second as the last import is scored
  SURVIVED without ever executing, and this file is edited in place.

### W4 / W5

- W4: whole-normalised-string pin on the note, per the module's existing note pins — a guard
  on **words** here is walkable by rewording.
- W5: the ledger is already two-way and already has a cap guard with its own positive
  control. The only new work is the row.

---

## 10. Risks

1. 🔴 **The handler is LIVE out of the working tree (§1.2c, MEASURED).** A half-finished edit
   to `scripts/mention-open.py` is in the operator's click path on the next Alacritty hint
   click, with **no `home-manager switch` and no deploy step to forget**. There is no staging
   between "saved" and "in production" for this file. Work in a throwaway worktree off
   `origin/main` per this repo's `CLAUDE.md`, and treat a broken intermediate state as an
   operator-facing outage rather than a local inconvenience. **Corollary that cuts the other
   way:** a live probe of a demote against a dirty tree is evidence about the deployed copy
   and **not** about `main` — both claims have to be made separately.
2. 🔴 **A new keybinding on the click path can shadow a cancel.** Five keys abort today
   (§W3). Claiming one makes a cancel into a destructive-ish action, and the ending is
   measured as byte-silent, so it would be invisible.
3. 🔴 **`--nth=2..` must not move.** A demote glyph as a new leading field forces `--nth=3..`
   and silently re-ranks every row (§1.3). Recommendation: no glyph.
4. ⚠ **The write path is new and it is on the click path.** `record_pick`'s properties —
   never raise, `0600`, atomic — are the spec, and a list rewrite is not append-atomic.
5. ⚠ **A short demote list is not covered by the leak gate** (§1.2b residual, threshold 20).
   The file is `0600` and outside every checkout, so this is a *committed-copy* gap, not a
   disclosure one — but it means the gate is not the thing protecting it; the path is.
6. ⚠ **`picks.jsonl` is absent here (§1.2d)**, so §5.3's Tier-B reasoning is from code and
   from the module's recorded numbers, not from a measurement I took.
7. ⚠ **PR #1829 is still open.** If it is abandoned or materially rewritten, §1.1's signature
   table is the thing to re-read; the `margin` parameter and the `abs()` on distance are
   both in functions W1 edits.

---

## 11. Open questions

1. **Ordering or filtering?** §2.4 shows ordering cannot deliver the sentence. Operator
   decision (§8.1). *Closes when:* the operator answers.
2. **Automatic (W2) or manual (W3), or both?** §2.2 shows W2 reaches all 67 measured rows.
   Operator decision (§8.2). *Closes when:* the operator answers.
3. 🔴 **How large is the residue only a manual demote can reach — "active, and I still never
   want it"?** Unanswerable here: it needs `picks.jsonl`, which lives on the laptop, joined
   against the `<90d`-pushed set (**95 rows**, 77 non-impossible). **This is the single number
   that decides whether W3 is worth a keybinding**, and I did not invent it. *Closes when:*
   someone runs the §12 command **on the laptop** and reports the aggregate.
4. **If a `demoted` boolean is wanted, is `tier_a` evictable?** §5.5 argues yes on the
   module's own "NEAR-CONSTANT" description plus the measured `UNKNOWN == 0`, and names the
   cost (the *partial*-table case loses its only detector). The alternative is a 16 → 17 cap
   raise, which `invocation.py:73-78` frames as a **leak-ceiling** decision for every caller.
   🔴 **This is not a dependency on anyone else** — the cap is per-event, so no other sink's
   ledger competes with the click row. *Closes when:* either the eviction is argued in a PR
   and reviewed, or option 1 is taken and the question is retired as unnecessary.
5. **Should `archived` be `IMPOSSIBLE` rather than demoted?** An archived repository cannot
   gain a new issue, which is the exact predicate `CLASS_IMPOSSIBLE` encodes — and **8 of the
   170** top-block rows are archived (§2.2). That is a smaller, better-founded change than
   either W2 or W3 and it belongs to Tier A proper. *Closes when:* someone decides whether
   `archived` is evidence about the repository (it is) and files it as its own item.
6. **Should the demote term be shared with `mention-review`?** The TUI target is the other
   consumer of a resolved mention and nothing here looked at whether it has its own list.
   *Closes when:* someone greps `mention-review`'s repo-selection path, or declares it out of
   scope in writing.

---

## 12. Closing condition

This document is closed by **either** of the following, whichever comes first:

**(a) A mechanical check — the sizing question (Q3) gets an answer.** On the **laptop**, with
`picks.jsonl` present, read-only, aggregate output only:

```bash
# On the LAPTOP. Read-only. Prints counts, never a repository name.
nix develop ~/workspace/devrc -c python3 - <<'PY'
import json, pathlib, collections, datetime as dt, sys
sys.path.insert(0, str(pathlib.Path.home()/"workspace/devrc/scripts"))
d = pathlib.Path.home()/".config/mention-open"
uni = {r.lower() for r in json.loads((d/"known_universe.json").read_text())}
rng = {k.lower(): v for k, v in json.loads((d/"known_ranges.json").read_text()).items()}
picked = set()
for line in (d/"picks.jsonl").read_text().splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        row = json.loads(line)
    except ValueError:
        continue
    if isinstance(row, dict) and isinstance(row.get("repo"), str):
        picked.add(row["repo"].lower())
top = {r for r in uni if rng.get(r, 0) > 0}          # the block above IMPOSSIBLE
print("universe", len(uni), "| top block", len(top),
      "| ever picked", len(picked & uni),
      "| TOP BLOCK, NEVER PICKED =", len(top - picked))
PY
```

The number after `TOP BLOCK, NEVER PICKED` is the manual-demote residue. **If W2's automatic
term already covers most of it, PR 3 should not be built.** Report the counts only — no row.

**(b) A named human judgement over named evidence.** **Zach** reads §2.4 (input order does
not survive typing), §2.2 (170 → 103 for 67 keypresses, versus the same 67 for free from a
field already fetched) and §5.4 (the visible surface is 19 rows), and answers the three-part
question in §8. His answer sets which of PR 1–3 get built, and that answer closes this doc.

Until **(a)** or **(b)**, this scope is a proposal and nothing in it should be implemented.

---

### Appendix — provenance of every number

| number | command / source |
|---|---|
| 405 universe rows · 235 impossible · 170 top block · class splits per `#N` · `max_ref` buckets | `python3` over `~/.config/mention-open/{known_universe.json,known_ranges.json}`, re-implementing `plausibility_class`'s branches. §2.1. |
| 401 API rows · activity distributions · 219 / 152 / 67 · 170→103 · ranks 187..405 · 95 active | `gh api user/repos --paginate --jq '.[] \| {full_name, has_issues, pushed_at, archived} \| tostring'` (rc 0), joined to the universe on a lowercased key. §2.2. |
| fzf 0.74.4 output contract, rows A–L | pty probe, `TIOCSWINSZ` 40×120, readiness gated on fzf's `<matched>/<total>`; rows A/B as the positive control against `PICKER_SH`'s existing table. §2.3. |
| `print()` / `execute-silent` / `reload` / `become` / `transform` exist | `fzf --bind=<action> --version`, with `zzznosuchaction` → **rc 2 `unknown action`** as the negative control. §2.3. |
| input order does **not** survive a typed query | `fzf -i --tiebreak=end --nth=2.. -f <query>` over a 30-row synthetic corpus, 4 queries, with an empty-query control returning input order. §2.4. |
| the handler is live out of the working tree; fzf pinned at 0.74.4 | `readlink -f ~/.config/alacritty/alacritty.toml`, then reading the `/nix/store/…-alacritty-mention-open` wrapper it names. §1.2c. |
| `picks.jsonl` absent · modes `0600`/`0700` | `ls -la ~/.config/mention-open/` + `stat -c '%a %s %y'` per file. §1.2d. |
| #1829 mergeable, 143 behind, **0** overlapping commits | `gh pr view 1829 --json mergeable,mergeStateStatus`; `git merge-tree --write-tree` **exit code** (never a marker grep — it prints only a tree OID on success); `git log --oneline 26d853f2..origin/main -- scripts/mention-open.py scripts/tests/test_mention_open.py` → 0. 🔴 **Positive control for that zero:** the same command over the same range for `nix/home.nix` → **10 commits**, so the command can return rows and the 0 is real. §1.1. |
| `CLICK_DIM_FIELDS` **14 at `main` / 16 post-#1829**, `_MAX_DIMS` 16, cap is **per-event** | `gh pr diff 1829` (the ledger hunk) + `scripts/collector/invocation.py:81` + `sanitize_dims`'s caller `build_fields` (*"for one invocation event"*). §5.5. |
| `dropped` counter exists, is unforgeable, and does not consume a slot | read `scripts/collector/invocation.py:119-143` — `items` filters any caller `dropped` first, `out["dropped"]` is written **after** the `[:_MAX_DIMS]` slice. §1.2e. |
| the generator writes exactly three files, no glob / rmtree / unlink | `grep -n 'write_text\|os.replace\|chmod\|unlink\|rmtree\|mkdir\|glob' scripts/regen-known-repos.py` (escaped `\|` BRE alternation, which demonstrably matched — 7 of the alternatives returned hits, which is the pattern's own internal positive control). 🔴 **Cross-checked with `-E` on the explicit file** (no `-r`, so no `.gitignore` blindness): `command grep -nE 'glob\|rmtree\|unlink\|shutil\|os\.remove'` → **rc 1, no match**, with `command grep -cE 'os\.replace\|write_text'` → **3** as the positive control. §5.1. |
| the four leak detectors and their thresholds | `grep -n '^def looks_like\|THRESHOLD = ' scripts/tests/test_regen_known_repos.py`, then reading each. §1.2b. |

⚠ Every repository name in this document is synthetic (`acme/…`). No real repository name,
owner, path or captured text appears, and none was written to any file outside the
scratchpad. ⚠ Where a recursive scan was needed, `command grep -r` / explicit file reads
were used rather than the host's `grep` function (ugrep, `.gitignore`-honouring); no number
here rests on a recursive `grep` zero.
