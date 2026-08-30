# Mention detection + click-to-open — AS BUILT (2026-08-29)

Shipped in #1011 (`0493e612`), deployed to both hosts via `ship.sh` at `ad5274b6`,
and **verified live** — including a real click. This supersedes the two design
drafts written on 2026-08-28; **three of their load-bearing premises measured
FALSE** and are corrected below, which is the main reason this doc exists.

## What it does

Detects clawgate task ids, GitHub issues/PRs and ClickUp task ids in agent output,
emits telemetry, and makes the reference clickable in the terminal exactly the way
a URL already is.

| piece | path |
|---|---|
| scanner (pure regex, no I/O) | `scripts/collector/mention_scan.py` |
| detection + telemetry | `scripts/collector/claude/session-tailer.py` |
| click handler + rofi picker | `scripts/mention-open.py` |
| the two hints | `nix/programs/alacritty/default.nix` |
| tests | `scripts/tests/test_mention_scan.py`, `test_mention_open.py`, `test_alacritty_hints.py` |

## 🔴 Three premises the drafts got wrong

**1. "clawgate has no browser UI, only an API."** False. `internal/api/server.go:384`
in `homelab-talos` registers `GET /tasks` → `handleIndex`, serving the full web UI;
task cards carry DOM ids shaped `task-<id>`. So a clawgate mention resolves to
`https://clawgate.zacx.dev/tasks#task-370` — a real page, not an API endpoint. The
drafts' proposed workaround (float `clawgatectl task get N` in a tmux popup) was
solving a problem that did not exist.

🔴 **But the `#task-<id>` FRAGMENT IS INERT — filed as clawgate task #440.** The
page is real and the DOM id is correct; the anchor still does not focus or scroll to
the task. `GET /tasks` serves only the document shell and the cards arrive in a
LATER htmx fetch, so the browser resolves the fragment when no card exists yet, and
nothing re-applies it afterwards (`grep` for `location.hash` / `hashchange` /
`scrollIntoView` across `internal/` + `static/` returns one hit, an unrelated
dropdown). Clicking a clawgate mention lands you on the board, not on the task.

**This is the exact shape of error the rest of this doc is about, committed by the
work that corrected the others.** #1011 verified that the URL *resolved* and that
the DOM id *existed*, then INFERRED navigation from those two facts — and its PR
body recorded "`#task-{id}` anchor: verified and used", citing `notes_test.go` as
the evidence. Those tests pin that the id is RENDERED. No test, and no human, ever
loaded the URL and watched the page move. **An id that exists is not an anchor that
works** — the same "a declaration is not a code path" trap the drafts fell into
three times.

**2. Detection did NOT need a new hook.** The drafts specified a `PostToolUse` +
`Stop` hook and then worried about ~50 ms of Python startup on every tool call.
`session-tailer.py` already walks the transcript JSONL, already extracts assistant
content blocks, and already emits to `activity.events` — on a settle timer, off the
hot path. Folding the scan in there deleted the hook, the `settings.json`
registration and the whole overhead question. (For scale: `PostToolUse` already
carried 6 hook groups, 2 of them unmatched Python.)

**3. tmux was not involved at all.** The drafts proposed `set -g mouse on` plus an
`M-o` binding, and flagged mouse mode as an open risk to `xdotool` automation. The
operator's actual requirement was *"for click i want it to function the same way
link clicking already does"* — and link clicking is **Alacritty's built-in hint**,
not tmux. Zero tmux changes shipped, and the mouse-mode risk evaporated.

## 🔴 The trap that made this dangerous

`hints.enabled` is an **array, and declaring it REPLACES alacritty's built-in
default entirely.** There is no merge. Adding the mention hint without
re-declaring the URL hint would have silently deleted URL clicking — the most-used
interaction in this terminal — with no error and nothing in the config looking
wrong.

So the URL hint is re-declared **verbatim** (regex verified byte-for-byte against
the compiled `alacritty-0.17.0` binary, with one documented substitution: literal
C0/C1 control chars spelled `\x00-\x1F` / `\x7F-\x9F`, since a Nix string cannot
hold a NUL). `test_alacritty_hints.py` pins its presence AND its regex — deleting
the hint reddens a test with its own message rather than being discovered by a
click that does nothing.

## Why the mention regex is deliberately LOOSER than the scanner

Rust's regex crate has **no lookaround**, so the trailing-digit guard that lets
`mention_scan.py` reject a six-digit hex colour cannot be expressed in the hint.
With a `{1,5}` bound the hint would match the first FIVE digits of `#282828` and
offer to open "task 28282". So the bound is `{1,6}`: the whole colour literal is
swallowed into one match, handed to the handler, and **rejected there**. The
handler is the authority; the regex only decides what gets underlined.

Measured live: `#282828` and `#ff00ff` both return *"no mention in the clicked
text"*. `#` must be followed by a digit, so `# Heading` and `#!/usr/bin/env` never
match at all.

## Verified live (not inferred)

- deployed config resolves via `readlink -f` to a store path carrying **both** hints
- hint mode labels all three shapes in a real terminal: `devrc#1011`, `#370`, `868abc123`
- activating a label **dispatches to the handler**; `#370` is ambiguous and raised the
  rofi picker showing both candidates — clawgate `#task-370` and the GitHub issue
  (the picker OFFERS the clawgate URL correctly; where that URL then LANDS is the
  separate, unfixed defect above — task #440)
- handler resolution: `devrc#1011` → GitHub issue, `868abc123` → ClickUp, hex colours rejected
- **`live_config_reload` DID pick up the new config in a 3-day-old window.** Predicted
  otherwise (home-manager swaps a symlink; inotify watchers commonly miss that) —
  the prediction was wrong, no restart was needed. Useful for any HM-managed dotfile.

**Not verified:** mouse hover-underline specifically (the keyboard binding
`Ctrl+Shift+M` was driven instead — same dispatch path, but `mouse.enabled` itself
is untested).

## 🔴 Harness lesson worth more than the feature

Two automated click tests reported **false negatives** before the third succeeded.
`xdotool key --window <id>` uses `XSendEvent`, which winit-based apps like Alacritty
ignore — it even echoed a stray `^A` into the shell. Only **XTEST** (`xdotool key`
against the *focused* window, no `--window`) delivers real input.

Had the run stopped at attempt two, a working feature would have been reported
broken. **Validate the instrument before believing its verdict** — and note the
positive control that made this legible: the same harness DID capture hint labels
in a screenshot, so "the hint works but dispatch doesn't" was distinguishable from
"nothing works".

## Deliberately not built

opencode plugin handler, clipboard monitoring, stale-reference detection, mention
notifications, and any tmux change. Only Claude Code output is scanned today.
