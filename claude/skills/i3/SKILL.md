---
name: i3
description: "Drive the i3 window manager — query windows/workspaces/layouts, focus/move/resize/arrange, screenshot the desktop and analyze it, type/click/scroll and read the clipboard via xdotool. Use for: what's on my screen, which workspace, focus or move a window, arrange a layout, screenshot my desktop, type or click into a GUI app, send a desktop notification. Editing the i3 config -> `devrc-dx`; the bar -> `bar`."
argument-hint: "<action> [args] — e.g. 'status', 'windows 2', 'focus Brave-browser', 'screenshot', 'type hello', 'key ctrl+s', 'arrange dev'"
allowed-tools: Bash, Read
---

# i3 — window manager interaction

Action + args come from `$ARGUMENTS`.

| Window management | |
|---|---|
| `status` | current workspace, focused window, layout overview |
| `tree [filter]` | container tree (filter: workspace num, window class, or `focused`) |
| `workspaces` | all workspaces with window counts and focused state |
| `windows [ws]` | all windows, optionally filtered to a workspace |
| `focus <target>` | focus by class, title substring, mark, or con_id |
| `move <target> <ws>` | move a window to a workspace |
| `workspace <n>` | switch workspace — 🔴 **this is the operator's screen.** Only on an explicit request naming a workspace; never as a step inside another task, and never in a loop |
| `layout <type>` | split / tabbed / stacking on the focused container |
| `exec <cmd>` · `mark <name>` · `goto <mark>` · `resize <dir> <px>` | |
| `scratch [show\|hide\|toggle]` · `config` · `find <query>` | |
| `arrange <recipe>` | apply a layout recipe (`dev`, `monitor`, `pair`, `present`) |
| `notify <msg>` | desktop notification via dunst |

| Keyboard / mouse / screen | |
|---|---|
| `screenshot [class]` | capture full screen (or a window) and display it for analysis |
| `observe` | screenshot + analyze what is visible + suggest next actions |
| `type <text>` · `key <combo>` | xdotool text / key combo into the focused window |
| `click <x> <y>` · `click-on <class> <x> <y>` · `rclick <x> <y>` | |
| `scroll <up\|down> [n]` · `drag <x1> <y1> <x2> <y2>` · `mouse` | |
| `clipboard [get\|set <text>]` | read or write the X11 clipboard |

**Reference files** (`~/.claude/skills/i3/reference/`, source
`~/workspace/devrc/claude/skills/i3/reference/`) — read on demand:
- `ipc.md` — every `i3-msg -t` query type, tree node fields, the full action-command
  vocabulary, criteria selectors, and the `arrange` recipes.
- `interaction.md` — xdotool keyboard/mouse recipes, clipboard, notifications, and the
  compound patterns (read text out of a GUI, fill a field, navigate a browser,
  screenshot→verify→act).
- `environment.md` — this host's display/theme/tooling, and the **reliability tiers**
  that decide which method to reach for.

## 1. Query
For `status`, `tree`, `workspaces`, `windows`, `find`: `i3-msg -t get_tree` (or
`get_workspaces`), parse with `jq`, present readably.

🔴 **The Bash tool escapes `!` in every string — never use `!=` in a jq filter.**
Use `| not`, `| type == "number"`, `| . > 0`, or `select(X) | select(Y)` instead of
`select(X and Y)`.

```bash
# Workspaces with a focused indicator
i3-msg -t get_workspaces | jq -r '.[] | "\(if .focused then ">" else " " end) [\(.name)] on \(.output)\(if .urgent then " URGENT" else "" end)"'

# Focused window (double-select instead of "and ... != null")
i3-msg -t get_tree | jq '.. | select(.focused? == true) | select(.window? | type == "number") | {class: .window_properties.class, title: .name, id: .id}'

# All windows per workspace ("startswith | not" instead of "!=")
i3-msg -t get_tree | jq '[recurse(.nodes[]?, .floating_nodes[]?) | select(.type == "workspace") | select(.name | startswith("__") | not) | {workspace: .name, windows: [recurse(.nodes[]?, .floating_nodes[]?) | select(.window | type == "number") | {class: .window_properties.class, title: (.name | if length > 60 then .[:57] + "..." else . end), focused: .focused}]}]'
```

## 2. Act
For `focus`, `move`, `workspace`, `layout`, `exec`, `mark`, `resize`, `scratch`:
validate the target exists (query the tree first for focus/move), prefer criteria
selectors (`i3-msg '[class="Firefox"] focus'`), and report the result — `i3-msg`
returns `[{"success": true|false}]`. Command vocabulary → `~/.claude/skills/i3/reference/ipc.md`.

## 3. See
Claude is multimodal, so a screenshot gives real visual awareness of the desktop.

```bash
flameshot full -p /tmp/claude-i3-screenshot.png          # full screen

# A specific window: focus it, then use its geometry
WID=$(xdotool search --class "Brave-browser" | head -1)
GEOM=$(xdotool getwindowgeometry --shell "$WID")         # X, Y, WIDTH, HEIGHT
```

`observe` = capture → `Read` the PNG → describe what is visible → suggest actions.
**Always `rm /tmp/claude-i3-screenshot.png` once you have read it.**

## 4. Type / click / clipboard
Full recipes and the safety protocols are in `~/.claude/skills/i3/reference/interaction.md`. The
non-negotiables:
- Confirm which window is focused **before** sending any keystroke; focus via i3-msg
  criteria and let X11 settle (`… && sleep 0.1 && …`) before typing.
- Use `xdotool key` for anything with a modifier; `xdotool type` sends raw characters.
- Screenshot **before and after** any coordinate-based click.
- 🔴 Read the clipboard only when explicitly asked — it may hold passwords or tokens —
  and never log its contents to a file or a commit message.

## Boundaries
**Will:** query and display i3 state · manage windows/workspaces/layouts · apply layout
recipes · target precisely with criteria selectors · notify via dunst · mark windows ·
capture and analyze screenshots · type and send key combos · click when nothing better
exists (with screenshot verification) · read/write the clipboard on request · combine
layers into compound workflows.

**Will not:** modify i3 config files (that is `devrc-dx`; the bar is `bar`) · reload or
restart i3 unasked · `exit` i3 without explicit confirmation · override keybindings at
runtime · run a destructive window operation without saying what it will do first · read
the clipboard unasked · send keystrokes before confirming focus · chain more than 3 blind
actions without a screenshot check · use xdotool mouse for browser work when the `browser`
skill or Playwright is available · leave screenshots in `/tmp`.

🔴 **Will not: take the operator's screen as a side effect of another task.** Switching a
workspace, raising or focusing a window changes what a human is looking at right now — only
on explicit request, once, never alternately. **Record the prior state first and restore it
when done, including on failure**: `PREV_WIN=$(xdotool getactivewindow)`,
`PREV_WS=$(i3-msg -t get_workspaces | jq -r '.[]|select(.focused).num')`, restore with
`i3-msg "workspace number $PREV_WS"` then `i3-msg "[id=$PREV_WIN] focus"`. If a task needs a
browser window in front, that is `browser activate`'s job — **it runs the host-side raise
itself, so driving `i3-msg` alongside it is a second theft, not a fix.** Measured 2026-08-19:
a capture subagent issued 42 `i3-msg` calls and 14 workspace switches in one run, restoring
nothing, against a skill that contains no `i3-msg` invocation at all. Note this list had ten
prohibitions and none about the operator's screen — that absence is what read as permission.
