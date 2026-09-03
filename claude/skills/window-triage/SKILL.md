---
name: window-triage
description: "Point at ONE tmux window by codename, hotkey or address, and rank the windows STRANDED past a threshold, longest-waited first. Use for: which window is Gold, what does Alt+p open, which window has been stranded / unanswered for hours or days, the oldest unanswered question, a codename with no hotkey, why a window shows no harness record, what the scan could not cover. The live inventory — is anything waiting on me, what's running where, tail a window, unsent prompts — is `session-manager`."
---

# window-triage — name one window, rank the stranded ones

Two read-only tools, one surface. **Neither writes.** Acting on a window is a command
you hand the operator, never something this skill does.

| ask | tool |
|---|---|
| "which window is `Gold` / `Alt+p` / `@81`?" | `scripts/session-resolve` |
| "what has been stranded longest?" | `scripts/waiting-windows` |

```bash
python3 $DEVRC/scripts/session-resolve show <selector> [--json]
python3 $DEVRC/scripts/waiting-windows [--json] [--top N] [--kind selection_menu,...]
```

🔴 **Read `--help` for flags and selector kinds — they are NOT restated here.** One
place for that, and it is the tool. This file carries only what `--help` cannot say.

## Why this exists

A question sat unanswered in a tmux window for **55 hours** while ~98 other prompts
were answered in the same 22. The signal existed the whole time; nothing ranked it.
These two tools consume it. Discovering them is the remaining half.

## 🔴 The eight things that bite

### 1. Two `waiting` states that disagree — never merge them
* `waiting_probable` (session-manager) — *the pane TEXT looks like it is asking a human.*
* `harness_status` (the `~/.claude/sessions/` registry) — *this harness process is waiting on a turn.*

Measured at one instant: **3 vs 1, zero overlap**, with the same window `idle` in one
and `waiting` in the other. **No mapping between them exists.** Do not invent one, do
not sum them, do not translate one into the other. `waiting-windows` fires its
threshold on `waiting_probable` **alone** and passes `harness_status` through beside it.

### 2. Presence is four-valued — and the two tools SPELL it differently
`present` · `absent` (read, no record — the **majority**, normal, not a zero) ·
`unmeasured` (could not look; carries a reason) · **structurally excluded**.

The registry is a directory in the **local** host's `$HOME` — there is no host
dimension in the path — so a **remote** window can *never* carry a record. That is
exclusion, not absence, and re-checking it will never help.

🔴 The two tools do not agree on the token:

| | fourth state spelled as |
|---|---|
| `session-resolve` | `harness_presence: "structurally_excluded"` |
| `waiting-windows` | `harness_presence: "unmeasured"` + `harness_presence_reason: "remote_host"` |

Both name the excluded hosts at `coverage.registry.hosts_not_covered`. **Read
`harness_presence` (plus the reason), never the nullness of `harness_status`.**

### 3. `--host` defaults to `all` — leave it there
`session-resolve --host` takes `all` (default) / `workbench` / `laptop`. Narrowing it
makes every window on the other host **UNMATCHED**. The report now says so loudly —

```
laptop  out-of-scope   <- contributed NOTHING
    why: not in scope for --host workbench
```

— so **read the `hosts consulted` block before believing an UNMATCHED.** tmux and the
registry are always read locally regardless of this flag. `waiting-windows` has no
`--host`; it takes whatever session-manager reports.

### 4. `age_secs` is not time-waited
`age_secs` is time since the window's last **ledger heartbeat** — a different question
in the same units. A row was measured flagged waiting while `busy` with an `age_secs`
of **2.5 seconds**. `waiting-windows` keeps its own clock and labels every row's
number with `waited_source`:

* `registry_status_updated` — the registry's `statusUpdatedAt`. **Preferred.**
* `ledger_age_fallback` — the ledger age observed when the stamp was taken.
* `first_seen` — elapsed since this tool first saw the window waiting (a **lower
  bound**; a row that first appears is not 6h old because the tool has run 6h).
* `unmeasured` — never coerced to 0.

**Quote `waited_source` whenever you quote a duration.** A `first_seen` row that reads
`6h10m` is saying "at least this long", not "exactly this long".

### 5. Visible ≠ uncovered — "open it" and "reveal it" are different acts
A window can be the **active window of its session, displayed on the base terminal,
and still invisible** under `display-popup` overlays.

* base terminal → `TERM=alacritty` (measured live: 312x64 and 103x64)
* popup → `TERM=tmux-256color`, smaller (measured live: 247x49)

The discriminator is the **TERM name**, not the size — a resized terminal must not be
reclassified. `visible: true, covered: true` means *it is already on screen underneath
a popup*: the fix is to dismiss the popup, not to switch to the window. Telling the
operator to "open it" there is the wrong instruction and looks like the right one.

### 6. Codename and hotkey come from ONE table — and case matters
`scripts/tmux-scratch-slots.sh`, entries `session:key:colour:codename`. The 20 tmux
`bind -n M-<key>` popup toggles are **generated from that file** at home-manager build
time (`nix/programs/tmux/default.nix`). Verified live against `tmux list-keys`:

| | |
|---|---|
| `Alt+p` | `scratch5` — **poppy** |
| `Alt+Shift+P` | `scratch6` — **Pool** |

Same letter, different session. **Never lowercase a hotkey.** A session with **no slot
entry** (a repo-named session such as `datapacket-talos`) has **no codename and no
hotkey** — its label falls back to the cwd, and none is invented. Read the table, never
a private copy; the file's own header comment mentions an `$mod+Shift` i3 binding that
does not exist — the tmux `Alt+<key>` bindings above are the live ones.

### 7. Ambiguity is refused, not guessed
A codename names a **session**, and a session has many windows: `Gold` resolved to
**10** targets on one live run. Exit codes:

| code | meaning | what to do |
|---|---|---|
| `0` | resolved | act |
| `2` | **AMBIGUOUS** | it prints every candidate — pick an address `session:@window_id` or `session:index` |
| `3` | **UNMATCHED** | check the `hosts consulted` block (see #3) before concluding it does not exist |

**Branch on the exit code.** Do not parse the human output for a verdict, and never
pick a candidate on the operator's behalf.

### 8. `idle_no_signal` cannot tell blocked from finished
session-manager does not read PRs. A window idle because its PR is blocked looks
**exactly** like a window idle because the work is done. Both land in
`idle_no_signal`, which is why `waiting-windows` ranks that band **last** and prints
the caveat rather than filtering it out. **Report the ACTIONABLE count**
(`trailing_question` + `selection_menu` + `context_exhausted`) separately from the
total: one live run was `ACTIONABLE: 7 of 43 over threshold`, the other 36 being
`idle_no_signal` whose titles mostly describe finished work. Sorting on time alone
buries the rows actually asking a human, and `--top` then truncates them away.

## Reading the output

* `waiting-windows` bands by kind first, then time within a band:
  `trailing_question`/`selection_menu` (a human is being asked, **now**) →
  `context_exhausted` (needs `/compact` or a resume — batchable) → `idle_no_signal`.
* `--top N` and `--kind` **always print what they dropped**. Say so when you use them.
* `COVERAGE:` and the `CAVEAT` lines are part of the answer. A count from a scan that
  walked nothing is `null`, never `0` — `counts.counts_are_partial` says whether any
  scope went unmeasured, and `counts.per_host[<host>].measured` says which.

## Handing it over

Both tools are read-only by construction (`session-resolve` cannot emit a tmux write
verb at all). So finish with the command, don't pretend to run it:

```
scratch2:@81 — Gold (G), selection_menu, waited 1d18h [ledger_age_fallback]
  reveal: press Alt+Shift+G      (window is covered by a popup, not hidden)
  or:     tmux select-window -t scratch2:@81
```
