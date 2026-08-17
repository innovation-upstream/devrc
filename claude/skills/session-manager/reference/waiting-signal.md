# `waiting_probable` — how the signal is derived, and what it deliberately misses

Loaded only when someone is changing the detector. The SKILL body has what a consumer needs.

## Why it exists

`status: idle` merged four states that need four different actions:

| real state | what you'd do |
|---|---|
| finished cleanly, awaiting instruction | give it work |
| asked a direct question | answer it |
| hard-blocked on a modal | press a key |
| out of context (`ctx: 0%`) | `/clear` it |

Two blind dogfood runs, given only *"what is being worked on, is anything waiting on me?"*,
could only answer the last three by `tail`-ing windows and reading English prose — 13 of 40
windows, ~30% of the run's tokens. The answer was a sample, not a sweep.

## How the panes are captured

**One tmux invocation per host**, not one per pane — 40 panes over SSH would be 40 round
trips. tmux takes a `;`-separated command list in a single call and `display-message -p`
writes to the same stdout, so the batch is:

```
tmux display-message -p -t %11 'SMCAP<nonce>#{pane_id}SMCAP' \; capture-pane -p -t %11 \
     display-message -p -t %68 'SMCAP<nonce>#{pane_id}SMCAP' \; capture-pane -p -t %68 …
```

Three things about that line are load-bearing:

- 🔴 **The pane id arrives via `#{pane_id}`, never our own interpolation.** A tmux format
  string is strftime-expanded, so a literal `%68` in it is **not** a pane id — it is `%B`
  (full month name) padded to width 68. Verified on live tmux 2026-08-12:
  `tmux display-message -p 'A%68B'` prints `A`, 62 spaces, `August`. The marker would
  silently lose the very id it exists to carry. Pinned by
  `test_the_marker_never_interpolates_a_pane_id_into_a_tmux_format`.
- **A per-run hex nonce**, so a pane cannot forge a marker: its scrollback would have to
  contain a string generated after it was drawn.
- **Only Claude panes are captured.** The signals are Claude Code TUI shapes; scraping a bare
  zsh would only invent false positives out of whatever its scrollback happens to hold. Those
  rows are `not_claude`, never `false`. The gate is asserted on the *argv*, because reading it
  off the row cannot distinguish "not scraped" from "scraped and suppressed".
- **No `-e`**, so the capture carries no ANSI at all.

The argv is asserted against an **allowlist** of subcommands (`tmux`, `display-message`,
`capture-pane`), not a denylist — a denylist passes for every mutating verb nobody named.

## The three signals

Evaluated independently on the full capture; none short-circuits another. A pane really can
match two at once (an agent that asks a question and then puts up a modal).

**`selection_menu`** — a line matching `^\s*❯\s*\d+\.\s+\S`, **plus at least one other
numbered option**. The second option is what stops the signal firing on ordinary prose:
agents quote menus back at the operator all day, and a live modal always has ≥2 choices.

**`context_exhausted`** — `\bctx:\s*0%`. Anchored so `ctx: 10%`, `ctx: 20%`, `ctx: 100%` and
`ctx: 0.4%` do **not** match; a substring test on `0%` would recommend `/clear` to every
window whose context happens to end in a zero. Measured at the boundary and on both sides.

**`trailing_question`** — the **last assistant line** ends in `?`. Finding that line is the
whole difficulty, and it is done **structurally**:

1. Claude Code draws the input box between two box-drawing rules. Cut there — everything from
   the box down is chrome, including the `❯` input line, `ctx: NN%`, and `⏸ manual mode on`.
   (A modal *replaces* the box, so a lone trailing rule cuts only itself.)
2. Strip the between-turn status lines off the bottom: `✻ Baked for 13m 17s`,
   `* Calculating… (47s · …)`, `※ recap: …`, the braille spinner. The sparkle/spinner half
   reuses `busy_from_title`'s table, so a glyph cannot be a spinner to one function and prose
   to the other.

Keyword-matching the chrome instead would break the moment upstream restyles a footer, and
would let a footer that ends in `?` read as a question. Both traps are live: a real pane
shows `new task? /clear to save 342.1k tokens`, and the user's own submitted prompt is
echoed into the scrollback as `❯ …?`.

## 🔴 Text typed at the `❯` prompt is EXCLUDED — the evidence

A dogfood run saw four windows with dimmed text at the prompt (`merge 131`, `clean up the
worktrees`, …) and read them as unsent instructions one Enter away, flagging honestly that it
could not rule out placeholder text. Investigated against `claude-code 2.1.220` on
2026-08-12.

**Dimness is the wrong question.** What separates the readings is *position*, and
`capture-pane` reports position perfectly well. From the bundle:

- **A placeholder cannot coexist with typed text.** The hint hook returns early on a
  non-empty buffer (`if(e!=="")return`), and the renderer gates on `t.length===0 && …`. So
  "placeholder vs draft" is not a real ambiguity.
- **A queued message is not in the box either.** The submit handler clears the buffer on
  enqueue (`onInputChange("")`, `setCursorOffset(0)`, `clearBuffer()`), and queued messages
  render as their own 2-column-indented block **above** the input box's top border.

So in principle: text between the two rules ⇒ typed but unsent ⇒ genuinely waiting.

**It is still excluded, for three reasons:**

1. Both invariants are read off a **minified** bundle at **one** version. Upstream restyles
   this surface.
2. The case they rule out — a live queued message — was **never observed**: zero hits across
   29 Claude panes and ~250k lines of scrollback, on a scan whose positive control passed
   (89–130 column-0 chevrons per pane). Keying a signal on an unobserved negative means its
   failure mode is a **false `waiting` row**, arriving exactly when the operator most trusts
   the output.
3. Under that same source read, typing while an agent works has **three** outcomes, not one:
   queue, *interrupt the turn* (`hasInterruptibleToolInProgress` → `abort`), or drop as
   `mode_not_queueable`. "Text in the box" is not even a single-meaning observation.

Excluding costs **recall**, which is the safe direction, and the caveat says so in the
output. **To turn it on**, someone has to observe a live queued message and confirm it
renders outside the box — then the discriminator is: box line non-empty and not `ESC[2m`
(which needs `capture-pane -e`, i.e. dropping the plain capture).

## 🔴 2026-08-15 — still excluded from `waiting`, but NO LONGER DISCARDED

Every word above stands: this is **not** a `waiting` signal, and the three reasons are
unchanged. What was wrong was the **disposal**. A blind dogfood hand-verified this tool
against all 79 panes on both hosts and found **five** holding a draft one Enter from
running — five distinct multi-word instructions, 20–45 characters each, four of them
imperative follow-ups to work already on screen — and this tool reported none of them. The
exclusion was correct; the silence was not.

So the fact is now measured under its **own** name: row fields `unsent_prompt` (the text) and
`unsent_prompt_status`, roll-up `summary.unsent_prompt`, `caveats.unsent_prompt`, and a
`✎ UNSENT PROMPT` block in the table.

### 🔴 NEVER PASTE A CAPTURED DRAFT INTO A COMMITTED FILE

This signal's entire job is publishing **text the operator typed**, and it lands in an agent's
payload — from there into transcripts, commit messages, handoff notes and any `claudedocs/`
an agent writes. **This repo is PUBLIC.** So: report a draft as a **count, a length or a
shape**, never verbatim, in any file that gets committed — a doc, a test fixture, a comment,
a PR body. Every fixture in `test_session_manager.py` is invented for exactly this reason,
and `test_no_FIXTURE_DRAFT_string_appears_in_a_shipped_doc` fails if a string ever appears in
both places, which is the shape a copied-in real draft takes. This paragraph replaced four
real drafts that had been quoted here verbatim.

**It is never mixed into `waiting_probable` and never summed into its counts.** The same 79-pane
sweep measured `waiting_probable` at **11 flagged / 11 true positives / 0 false positives**,
and that precision is exactly what folding a noisier signal into it would destroy — a reader
who learns `waiting: YES` sometimes means "you typed something and wandered off" stops
walking to the terminal for the ones that are a real block. `test_the_existing_WAITING_SET_is_
byte_identical_after_the_fourth_signal` pins the whole waiting surface against values measured
at the pre-change sha, and the two "fold it in" mutants are killed by that test alone.

**What it reads, and why that is narrow on purpose.** Only the lines *between the two
box-drawing rules* — the pane's own input box, located by `_input_box_span`, the one
definition `last_assistant_line` also cuts at. Never "any `❯` line in the capture": a pane
**displaying another session's transcript** is a live false-positive shape that already bit
this scrape once, and every capture also contains the operator's own submitted prompts echoed
into scrollback. A modal replaces the box, and a draft taller than the box is not recognised;
both report `no_input_box` — **unmeasured**, never "nothing typed". Shell panes are never
scraped: a half-typed shell command is a different, noisier thing.

## Known misses, stated rather than implied away

- `Login successful. Press Enter to continue…` — a real hard block on 2 of 40 panes
  2026-08-12. None of the three signals catches it.
- An agent that offers rather than asks (*"Say the word and I'll open the PR"*) — no `?`.
- A `claude` under a wrapper shell reads as `shell`, so it is never scraped
  (`caveat[claude_detection]`).
- A parked draft is **not** a miss any more — it is a separate signal (`unsent_prompt`,
  above). It is still not `waiting`, deliberately.

### And what NEITHER signal can see

Both are screen-scrapes of tmux panes, so both are blind to everything that is not one: open
PRs and their conflict state, the mail queue, cluster alerts, the durable initiative board,
and GUI windows outside tmux. That list is not left implicit — `report["not_measured"]`
enumerates it with the owning skill for each, derived from the report's own keys so it stops
claiming a population the day that population gains a measurement.

## Calibration, 2026-08-12

Run against all 40 live panes on the workbench: **7 flagged of 29 Claude panes** — 3
`selection_menu` (two resume prompts, one option list), 4 `trailing_question` — and 11
`not_claude`. Every flagged row was independently confirmed by reading the pane. Zero false
positives.

Suite: **positive control 3 flagged of 5 realistic panes; negative control 0.** Twenty
mutants, each killed on its own named assertion.

## Calibration, 2026-08-15 (`unsent_prompt`)

Blind dogfood across **79 panes on both hosts**, hand-verified pane by pane:
`waiting_probable` **11 flagged / 11 true positives / 0 false positives**; **5** panes held a
parked draft that no roll-up reported.

Suite: **positive control 2 parked of 8 realistic panes** (including a pane whose body holds
five *other* windows' `❯` lines and whose own box is empty — it must read as a measured
zero); **negative control** a dict-key reorder, which survives. **21 mutants: 21 as expected,
0 mismatches**, run with `PYTHONDONTWRITEBYTECODE=1` and `__pycache__` cleared between
mutants, each proven applied by sha change **and** byte-equality and proven non-inert by an
AST diff.

### 🔴 Re-swept after an adversarial audit, 2026-08-17 — that sweep had blind spots

A differently-constructed sweep found **six survivors** the 21-mutant batch above scored
clean, which is the point of varying how the sweep is BUILT rather than only how many mutants
it holds. All six were shapes no fixture could reach:

| survivor | why it was invisible |
|---|---|
| the table's parked list widened with `or waiting_probable` | the whole-string pin used a **single-pane** fixture with `waiting_probable: False`, so the `or` had nobody to add |
| `_input_box_span`'s `<= 3` → `<= 4` | fixtures measured **3 apart** (in) and **5 apart** (out); 4 is the only value where the two constants disagree |
| `summary.unsent_prompt.unmeasured` computed off `waiting_status` | nothing asserted the number against the histogram printed beside it |
| deleting a whole `NOT_MEASURED_POPULATIONS` entry | only 3 of 5 populations were named by any test, and the count assertion read `1 + len(<the constant>)` — derived from what it guarded |
| `rules[-2], rules[-1]` → `rules[0], rules[1]` | the anti-false-positive fixture has five stray `❯` lines but **no stray box rules**, so only one rule pair existed |
| `match(interior[0])` → `any(...)` | the only unreadable-box fixture has a **one-line** interior, where the two are the same function |

Each now has a named guard, and the same audit found two live defects the sweep could not
have: `_waiting_rollup` kept the `None`-key `TypeError` its sibling had been fixed for (the
`cluster_row` fixture set one field and not the other, so no test could build the crash), and
a modal drawn with **two** rules read as a parked draft whose "text" was the option label.

Re-sweep after the fixes: **16 mutants, 16 as expected, 0 mismatches** — the six survivors
above now KILLED, each **by its own named guard** (attributed per mutant, not merely counted,
because a kill from a different test is green for the wrong reason). Positive control: a
mutant forcing `text: None` — **died**, 12 tests. Negative control: a dict-key reorder —
**survived**. Same hygiene as before, plus one mutant of my own that the AST diff caught as
**inert** before its verdict was believed.

### Live reproduction on the workbench, 2026-08-15 (counts only — this repo is PUBLIC)

The 79-pane figures above are the **dogfood's**, on both hosts. Independently reproduced on
the LOCAL host with the read-only scan (`--host workbench --no-ch --no-ledger`):

- **52 rows**, 41 Claude panes scraped, 11 `not_claude` (shells, never scraped).
- `waiting_probable` **7 flagged** — 5 `trailing_question`, 2 `context_exhausted`,
  0 `selection_menu`.
- `unsent_prompt` **9 parked**, draft lengths 10–87 chars.
- **Rows flagged BOTH: 0 on that run, 1 on the next.** 🔴 **That number is not the
  separation claim and must never be quoted as one.** Co-occurrence is CORRECT and expected —
  the agent asks a question and the operator half-types a reply — and a build that suppressed
  it would be strictly worse: it would have to drop one of the two true facts about that
  window. **Separation means the two signals are COUNTED separately and neither can raise the
  other**: `unsent_prompt` is absent from `WAITING_SIGNALS`, absent from
  `summary.waiting`'s key set, never summed into `waiting.probable`, and rendered in its own
  table block. That is a structural property, pinned by
  `test_the_existing_WAITING_SET_is_byte_identical_after_the_fourth_signal`,
  `test_the_fourth_signal_is_not_IN_the_waiting_signal_set_at_any_layer` and
  `test_the_unsent_TABLE_SECTION_excludes_a_WAITING_row_with_no_draft` — not a run-to-run
  observation, which is why the earlier version of this bullet was wrong twice over.
- **0 chrome mis-parses**: no parked value contained `ctx:`, a box rule, `esc to interrupt`
  or a token count, i.e. the box scope did not leak footer or rule text into a draft.

🔴 **Scope of that run, stated:** local host only, one point in time. `no_input_box` did NOT
occur on it, so that status is exercised by fixtures rather than by this observation — an
unmeasured status here, not a measured zero.
