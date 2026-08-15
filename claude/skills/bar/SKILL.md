---
name: bar
description: "Fix or extend the i3status-rust status bar and its status-count poller — add/reorder blocks, tune the hide-at-zero count pills and their --red-above thresholds, wire block<->poller signals, debug the bar-status-poll timer and its dunst toasts. Use for: the status/i3 bar, a bar block/icon/threshold, the alert/mail/clawgate/civitai pills, bar-status-poll, ~/.cache/bar-status, the DND/agent-ops bar buttons, \"the bar is wrong/stale/red\"."
---

# i3status-rust status bar — operations

The workbench + laptop status bar. Migrated **i3blocks (/etc/nixos) → i3status-rust under
home-manager** (PR #74). All bar config, block scripts, and the poller live in the **devrc** repo
and deploy via home-manager. Point-in-time history: memory `i3-bar-i3status-rust-migration` +
`devrc/claudedocs/handoff-agent-facing-workbench-2026-07-11.md`.

**AirVPN host tunnel + its pill → `reference/airvpn.md`** (`~/.claude/skills/bar/reference/airvpn.md`,
source `~/workspace/devrc/claude/skills/bar/reference/airvpn.md`): the killswitch design, the
🔴 mandatory re-test protocol, apply/re-arm/bail procedures. Read it before touching anything AirVPN.

## Modernization stance (standing rule — applies whenever you touch this setup)
When editing the bar / i3 / any NixOS or home-manager config here, **prefer the current
best-practice idiom and opportunistically modernize outdated portions you encounter** — don't
just pattern-match the old code. Examples: retire a bespoke i3blocks-era script when
i3status-rust has a native block; use the maintained `programs.*`/`nix/pkgs` idiom over a
hand-rolled one; drop `/etc/nixos` remnants in favour of home-manager. Guardrail (from RULES):
**reversibility-aware** — a drop-in modernization goes ahead, but a risky/broad rewrite gets
**flagged before acting** ("here's the outdated bit, here's the modern replacement, blast radius
X — your call"). Leave the setup a little more modern than you found it; never more legacy.

## Where everything lives (all in `~/workspace/devrc`, `$DEVRC`)
| Thing | Path |
|---|---|
| Bar definition (blocks, theme, icons, thresholds) | `nix/graphical.nix` (`programs.i3status-rust.bars.top`) |
| i3 WM config (binds, bar buttons) | `nix/i3/config.nix` → `~/.config/i3/config` |
| Decoupled poller (workbench systemd-user timer) | `scripts/bar-status-poll` |
| Instant block scripts (read cache, never network) | `scripts/i3status-{clawgate,mail,alerts,civitai,dnd,media,airvpn,telemetry}` |
| Poller output (per-source JSON) | `~/.cache/bar-status/*.json` (0600) |
| Generated bar TOML + symlinked scripts | `~/.config/i3status-rust/` |
| **RETIRED — never edit** | `/etc/nixos/{i3config,i3blocks}.nix`, `/etc/nixos/i3blocks-scripts` (except `airvpn-sudo` + `airvpn-updown`, which MUST stay there for the NOPASSWD sudoers rule + the tunnel PostUp/PostDown; the old Mullvad `vpn-sudo` is dead) |

## Architecture (why it's shaped this way — the CALM bar)
- **Blocks render local files only.** The bar NEVER queries a remote per tick. Anything remote
  (alert counts, mail, clawgate tasks) is fetched out-of-band by `bar-status-poll` (~45s
  systemd-user timer, workbench-only) which writes tiny JSON to `~/.cache/bar-status/` — so a
  slow/down source can never hang or break the bar. A down source writes `{"state":"stale"}` →
  the block renders **empty/invisible** — **except `clawgate`**, whose reassuring answer is a
  ZERO, so "cannot tell" must not borrow the pixels that mean "nothing to tell": it renders a
  visible `?` (and `!N?`, keeping the last known stuck count — `carry_stuck_forward` copies it
  onto the stale marker, so a clawgate outage cannot silence a live wedge).
  ⚠ **`telemetry` does NOT** — `i3status-telemetry` maps a poller `stale`/`error` to the empty
  pill on purpose (the unit's `OnFailure` toast covers a fetch that raised). Its `tlm ?` comes
  only from `payload["unknown"]` **inside a readable payload** — the deadman said "cannot
  evaluate", grace-gated at 1800s in `parse_telemetry`. So telemetry is guarded against a dead
  *backend* but not against a dead *poller*; see the block table row.
- **Colour == "look at me."** Steady state is neutral/invisible (hide-at-zero count blocks). A
  block reddens only when something NEW crosses its `--red-above` line — standing backlogs read
  neutral.
- **Instant refresh:** after writing a source file the poller does `pkill -RTMIN+N i3status-rs`
  to refresh exactly that block. The block's `signal = N` in `graphical.nix` **must** match
  `SIGNALS` in `bar-status-poll`.
- **Edge-triggered toasts:** the poller also fires a dunst toast **once on the rising edge**
  (count crosses `--red-above`), latched via `~/.cache/bar-status/<src>.toast-state` so steady
  state stays silent. Thresholds are env-tunable and MUST match the block's `--red-above` (or
  they drift).
  🔴 **A `--mock-*` run does NOT touch the latches or fire toasts unless you pass `--toast`.**
  The latches live in the same cache dir as the status files, so a debug run with a
  sub-threshold fixture would clear a *live* latch and make the next real poll (≤45s) re-toast
  a steady-state condition. `telemetry` is the one source whose toast can ride an
  **unknown** verdict: a `presence-stalled` host with a measured death renders `tlm N` and
  toasts, not `tlm ?` — see `deadman.py`'s COST section.

## Block ↔ signal ↔ threshold map (workbench)
| Block | Script | signal | `--red-above` | Notes |
|---|---|---|---|---|
| homelab alerts | `i3status-alerts` | 13 | **34** (#97) | homelab backlog is noise; bumped 30→34 when it drifted to ~24-27 |
| civitai prod alerts | `i3status-civitai` | 14 | **340** | CLIENT prod; its own kubeconfig. **Red is CORRECT when real — do NOT tune it away.** (disk-space growth, 2026-07). 🔴 **left-click + its toast open the client Grafana via `scripts/bar-url --open civitai_grafana`, NOT a URL literal** — this repo is PUBLIC, so the host lives in `~/.config/bar/urls.env` (0600, untracked, `civitai_grafana=<url>`). A host missing that key gets `bar-url` exit 3 naming the key + file; the button is never silently dead. Gated by `scripts/tests/test_no_client_hostnames.py` |
| mail | `i3status-mail` | 12 | 0 (0→>0) | open `mail_actions` rows |
| clawgate | `i3status-clawgate` | 11 | 0 (0→>0) | Tasks needing the operator = `{open, ready_for_review}` **plus `in_progress` tasks whose agent looks dead**. 🔴 The predicate is NOT in the poller — it is `scripts/lib/clawgate_tasks.py`, shared with `agent-ops` and `session-manager`, and re-spelling it anywhere fails `scripts/tests/test_clawgate_predicate_single_source.py`. Excluding `in_progress` outright is what hid a four-hour-dead dispatch on all three surfaces at once. Stuck = no agent / agent `error` / never kicked off / silent >15m (`AGENT_IDLE_THRESHOLD_SECS`) / no readable timestamp; the payload always ships `agent_idle_secs` beside the flag. Fetch is `?summary=1` (~27x smaller). **Changing the shared module requires the `X-Restart-Triggers` entry in `graphical.nix` to re-arm the unit** — without a restart the pill keeps the OLD meaning across a green switch. 🔴 **Text grammar: `22` = count; `22!2` = 2 of them STUCK (Critical); a TRAILING `?` = "this is not a current measurement".** So `?` alone = the board could not be read at all (missing/corrupt/`stale`/`error` cache, or one the poller stopped refreshing — older than `MAX_CACHE_AGE_SECS`, 600s vs the **195s** worst healthy gap: `OnUnitActiveSec` 45 + systemd's default `AccuracyUSec` 60 + `TimeoutStartSec` 90) and `!2?` = 2 stuck as of the last readable poll, still Critical. 🔴 A measurement outage never makes the pill QUIETER, and that needed BOTH halves: the block's `ts` gate covers a frozen cache, and `bar-status-poll.carry_stuck_forward` copies the last known `stuck_count` onto a `stale`/`error` marker — without it a clawgate outage overwrote `24!2`/Critical with a bare `?`/Warning. The carry is not a ratchet (a recovered poll's own reading wins outright) and never reaches the toast gate. **This is the one count pill that does NOT go invisible when its cache is unusable**: a stuck dispatch is announced nowhere else that cannot be dismissed (the toast is one-shot and dies with dunst, the notif badge clears on `seen`, `session-manager` is on-demand), so an unreadable board must not look like an empty one |
| DND | `i3status-dnd` | 15 | — | dunst paused indicator; `$mod+Shift+n` toggles |
| media | `i3status-media` | 16 | n/a | qBit-POD AirVPN pill (net_down), **state-driven**: neutral=connected, **RED**=firewalled (tunnel/port-fwd broken), soft-yellow=stale. poller `parse_media`/`fetch_media` read creds from `~/.config/bar/media.env` (0600) — the same `media.env` also feeds `deep-search`, a media release search/grab CLI on PATH (workbench) after `home-manager switch`. **left → `media-menu`**; **right → qBit WebUI** |
| telemetry deadman | `i3status-telemetry` | 17 | n/a | `tlm N` = N activity-telemetry (host, source) pairs have STOPPED emitting into ClickHouse; `tlm ?` = the check itself has been unable to evaluate for >30 min. 🔴 **It does NOT render an unreachable BACKEND as empty** — its reassuring answer is a zero, so "cannot tell" must not look like "all healthy". ⚠ But that guard covers only a readable payload whose deadman verdict is `unknown`: a poller `stale`/`error` marker, and a cache the poller stopped refreshing, still render EMPTY here (unlike `clawgate`). The deadman block has no deadman of its own — known follow-up, see PR #490. Measures BOTH hosts from the workbench poller (it reads the shared table). Logic + the per-source budget table: `scripts/collector/deadman.py` (run it bare for the table; left-click floats it). See the `activity` skill. |
| airvpn (host) | `i3status-airvpn` | 10 | n/a | **HOST** AirVPN WireGuard pill (net_vpn), **state-driven**, default-OFF. **left → `airvpn-menu`**; **right → `airvpn-detail` float**. 🔴 Full detail + the killswitch re-test protocol: **`reference/airvpn.md`** |

Bars differ by host (`isLaptop` in `graphical.nix`): laptop gets `batteryBlock` and **omits** GPU
+ all count blocks + poller (nebula-only, no LAN path to homelab endpoints); workbench gets GPU
(RTX 5080), the count blocks, the state-driven `mediaBlock` (qBit/AirVPN), `agentOpsBlock` (▦),
`rigcontrolBlock` (⚙).

## Deploy / apply
- **Single host (validate an edit end-to-end):** `home-manager switch --flake ~/workspace/devrc --impure`.
  This DOES restart the poller on a script change (`X-Restart-Triggers`).
- **Both hosts (after merge):** `scripts/ship.sh`.
- **Syntax check first:** `nix-instantiate --parse nix/graphical.nix >/dev/null`.
- **Flake gotcha:** a NEW *block script* referenced from `graphical.nix` — and any new file in
  this skill dir — must be `git add`ed before switch (flakes only see tracked files). This skill
  ships from `devrc/claude/skills/bar/` via `home.file.".claude/skills"`; edit it there, never
  the read-only `~/.claude/skills/bar/`.

## 🔴 Cutover gotcha (the one that bit us)
A change that alters which i3 config file is authoritative (e.g. the /etc/nixos→HM cutover) must
finish with **`sudo systemctl restart display-manager`, NOT `i3-msg restart`** — a running i3 has
`-c /etc/i3/config` baked into argv, so deleting that file + in-place restart = dead session.
Routine HM block/threshold edits don't need this; only config-source cutovers do.

## Other gotchas
- **Icon overrides need the TABLE form.** `settings.icons = { icons = "material-nf"; overrides.gpu = "…"; }`.
  The `icons = "…"` string shortcut + an `[icons.overrides]` table **conflict and silently drop
  ALL icons to text** (#81). GPU is overridden to `nf-md-expansion_card` (material-nf maps
  `gpu`→a monitor glyph).
- **Poller runs from the working tree**, not a nix-store copy, so it can resolve sibling
  `scripts/mail-actions/_db.py` (loaded by explicit path — do NOT add `mail-actions/` to
  `sys.path`, its `llm.py` shadows things).
- **fuzzyclaw is UNTRUSTED** as a data source (`~/.tmux/tasks/*.json` is stale) — nothing
  bar-related should depend on it.
- Poller holds standing read-only port-forwards into TWO prod clusters (homelab + civitai) every
  ~45s — bounded (`TimeoutStartSec=90`), fail-safe, cgroup-killed. Accepted.

## Debugging
```
# is the poller healthy?
systemctl --user status bar-status-poll.timer bar-status-poll.service
journalctl --user -u bar-status-poll -n 50 --no-pager
# what does each source currently say?
for f in ~/.cache/bar-status/*.json; do echo "== $f"; cat "$f"; echo; done
# force a poll now (workbench)
systemctl --user start bar-status-poll.service
# run the poller by hand (see errors live)
python3 ~/workspace/devrc/scripts/bar-status-poll
# drive the write+signal path offline with fixtures (no live endpoint).
# SAFE by default: writes the real cache files, but fires NO toasts and does not
# touch the rising-edge latches. Add --toast only if you mean to exercise those.
~/workspace/devrc/scripts/bar-status-poll --mock-alerts alerts.json --mock-mail 3
# unit tests for the pure parse/edge functions
python3 -m pytest ~/workspace/devrc/scripts/tests/test_bar_status.py
# manually refresh one block (N = signal)
pkill -RTMIN+13 i3status-rs
```
- **Block stuck/empty:** check its `~/.cache/bar-status/<src>.json` — `state:"stale"` means the
  source (kubeconfig/port-forward/creds) failed; run the poller by hand to see the exception.
- **Block never refreshes on change:** the block's `signal` in `graphical.nix` ≠ `SIGNALS` in
  `bar-status-poll`.
- **All icons show as text:** the `[icons.overrides]` / `icons=` shortcut conflict (see gotcha).
- **Red pill you think is noise:** confirm it's actually noise before bumping `--red-above` —
  homelab is noise, **civitai is not**. Tune the *threshold* in BOTH `graphical.nix`
  (`--red-above`) AND the poller toast env (or they drift), never suppress a real signal.

## Common tasks
- **Add a block:** define `fooBlock` in `graphical.nix`, add it to the `blocks` list (order =
  left→right, gate with `lib.optional*` for host scoping), symlink any script via `home.file`,
  `git add` the script, switch.
- **Change a threshold:** edit the block's `--red-above N` in `graphical.nix` **and** the matching
  `*_TOAST_ABOVE` env in the poller's `_toast_specs()` so the toast fires on the same line; switch.
- **New remote count source:** add a `parse_*`/`fetch_*` to `bar-status-poll`, a `SIGNALS` entry,
  a fixture-tested parser, an instant block script that reads the cache, and the block in
  `graphical.nix` with the matching `signal`.
