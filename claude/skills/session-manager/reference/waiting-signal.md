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

## Known misses, stated rather than implied away

- `Login successful. Press Enter to continue…` — a real hard block on 2 of 40 panes
  2026-08-12. None of the three signals catches it.
- An agent that offers rather than asks (*"Say the word and I'll open the PR"*) — no `?`.
- A `claude` under a wrapper shell reads as `shell`, so it is never scraped
  (`caveat[claude_detection]`).

## Calibration, 2026-08-12

Run against all 40 live panes on the workbench: **7 flagged of 29 Claude panes** — 3
`selection_menu` (two resume prompts, one option list), 4 `trailing_question` — and 11
`not_claude`. Every flagged row was independently confirmed by reading the pane. Zero false
positives.

Suite: **positive control 3 flagged of 5 realistic panes; negative control 0.** Twenty
mutants, each killed on its own named assertion.
