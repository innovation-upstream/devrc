# Per-session tab ownership & multiple Brave instances

**Load this when:** you got `409 ambiguous_instance`, `404 unknown_instance`,
`409 superseded`, `409 no_owned_tab`, or `owned_tab_gone` · two drivers appear to be
fighting over one tab / you read a page another session navigated · you are designing a
multi-session, multi-subagent or multi-profile workflow · you want to know whether
`open` leaks tabs, or what `close` vs `release` do.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.

## Per-session tab isolation (use `open` for multi-step work)

Two Claude sessions driving one browser used to interleave on the ONE shared
active tab (session A `nav`s, session B `nav`s in between, A reads B's page).
Now each session can own its own tab:

- **Multi-step workflow → `browser open <url>` first.** The server records this
  session's owned tab (keyed by a stable per-session id sent automatically on
  every request) and routes your subsequent `html`/`eval`/`nav`/`screenshot`/
  `close` to it — a parallel session doing the same on its own tab can't clobber
  you. Finish with `browser close` (closes the tab) or `browser release` (keeps
  the tab, drops ownership).
- **One-shot read → just `browser html` (no `open`).** With no owned tab, ops
  fall back to the active tab — the historical "read the tab the user has open"
  behaviour, which is a single read and inherently safe.
- **`--tab <id>`** targets a specific tab explicitly (overrides owned/active).
- **Double `open` is safe (idempotent).** Calling `browser open` again in a
  session that already owns a **live** tab returns that SAME tabId (it does not
  leak a second tab). If the owned tab was closed, `open` makes a fresh one.
- **Self-heal.** If the user manually closes your owned tab, the next op fails
  with `owned_tab_gone` and the bridge drops your ownership — your NEXT command
  automatically falls back to the active tab. `browser close` always clears the
  mapping, even if the tab was already gone.
- **Session id source (precedence):** `CLAUDE_CODE_SESSION_ID` → `CLAUDE_SESSION_ID`
  → `$TMUX_PANE` → the **POSIX session id** (`sid:<sid>:<leader-starttime>`, from
  `/proc/self/stat`) → (no procfs only) a PPID-keyed cached token. It is
  routing-only, never trusted for auth. If two drivers ever resolve the same id
  they share a tab (degrades to the old behaviour — no worse).
  `browser --print-session-id` prints the derived id.
- **⚠ The subshell hazard (fixed 2026-08-01 — read this if you script `open`).**
  Capturing a tab id is naturally written inside a command substitution:

  ```bash
  T=$(browser open https://example.com | grep -oE '"tabId": *[0-9]+' | grep -oE '[0-9]+')
  browser --tab "$T" emulate iphone-15
  ```

  Until 2026-08-01 the last-resort fallback was keyed on `$PPID`, and a `$( … )`
  that forks gives `browser` a different parent pid — so the `open` registered
  ownership under one session id and the later call presented another. The
  refusal (`op 'emulate' may only run on a tab THIS session owns`) points at
  `--tab`, which is the wrong place to look. **Measured over ssh on the
  workbench**, where `CLAUDE_CODE_SESSION_ID` is unset:

  ```
  $ echo "in subst: $(browser --print-session-id)"
  in subst:  ppid:2484606:c4b88b1d9de41681
  $ browser --print-session-id
             ppid:2484584:3216410da01ef5e2      # DIFFERENT → not_owned_tab
  ```

  Invisible under normal Claude Code use (`CLAUDE_CODE_SESSION_ID` is set, so the
  fallback never runs); it bites over **ssh, cron, and any non-Claude shell**.
  The POSIX session id fixes it: a subshell cannot leave its session (only
  `setsid(2)` can), so every process in one login/pane/unit agrees — while two
  ssh logins, two tmux panes and two systemd units still get distinct ids, so
  per-session tab isolation is preserved. **If you hit `not_owned_tab` anyway**,
  the CLI now prints the session id THIS call presented; run
  `browser --print-session-id` both inside the same `$( … )`/pipeline you used
  for the `open` and directly, and compare. **No migration needed** across the
  id change: ownership lives in the server's memory with a 900s idle TTL reaped
  on every touch, so a tab still mapped to an old `ppid:` id self-releases
  within 15 minutes (and the real Brave tab is never closed by a reclaim).
- **⚠ Concurrent drivers that may share a session id (subagents): pass explicit
  `--tab`.** Sibling subagents of ONE parent share identity — a subagent inherits
  the parent's `CLAUDE_CODE_SESSION_ID` and the same `$TMUX_PANE`, and there is NO
  subagent-unique env var — so two parallel subagents derive the SAME session id
  and would own the SAME tab (re-introducing the clobber). Per-session isolation
  only separates concurrent **top-level** sessions. When you spawn concurrent
  drivers that each drive the browser, have EACH one `browser open` (capture the
  returned `tabId`) and then pass its OWN `--tab <id>` on **every** subsequent op
  (`browser --tab <id> nav …`, `--tab <id> html`, …). An explicit `--tab`
  overrides owned-tab routing entirely, so indistinguishable drivers never
  collide. (Each `close`s its own `--tab <id>` at the end.)
- **Contention → FIFO, not failure.** If two sessions DO target the same tab
  (both active, or one `--tab`s another's tab), the commands queue in arrival
  order (bounded by `cmd_timeout`) rather than fail.
- **Lifecycle:** idle ownership is reclaimed after a TTL, which RELEASES it but
  does NOT close the real Brave tab (only `browser close` closes it).

## Don't do this: a bare high-rate `eval` loop

Do **NOT** run a bare high-rate `eval` loop (no `--instance`/`open`). It shares
the one active tab AND saturates the single serial extension connection; the
server **rate-limits** it and returns **HTTP 429** (`rate_limited` /
`queue_full`) — the `browser` CLI prints a back-off message and exits non-zero.
Batch what you need into fewer `eval`s, or space them out. Knobs are in
`~/workspace/devrc/scripts/browser-bridge/reference/errors.md`.

## Multiple instances (per host)

Several Brave profiles can each run the extension and be driven independently —
each has its own command queue (routing key = the profile's **label** if set,
else a stable auto-id; labels must be unique per host).

- **One instance connected** → no `--instance` needed (back-compat).
- **More than one and no `--instance`** → the command **ERRORS** and lists the
  connected instances. Do NOT retry blindly — run `browser instances`, pick the
  right key, and re-issue with `--instance <key>`. (The bridge never guesses.)
- **`browser --instance <key> <op>`** targets one (key = label or auto-id); an
  unknown key errors.
- **⚠ A bare `health` can mask ONE dead instance.** The extension can drop
  mid-session per profile, and `brave://extensions` is per-profile — reloading ↻ in
  profile A leaves B disconnected while `health`'s connected count (from A) still
  looks healthy. Confirm with `browser --instance <key> health`, and name the
  profile when asking the user to reload.
  → `~/workspace/devrc/scripts/browser-bridge/reference/errors.md`
- **Newest supersedes:** a fresh connection for an already-held key drops the old
  one; an in-flight command on the dropped connection returns a `superseded`
  error — just retry. The displaced connection's own `/poll` gets a distinct
  `409 superseded` (not the idle `204`) and the extension **backs off ~30s** (and
  shows a "superseded — set a unique label" state) instead of re-registering
  instantly, so two profiles sharing a label can't mutual-supersede in a tight
  loop. If you see `superseded` steadily, two profiles share a label → fix it.
