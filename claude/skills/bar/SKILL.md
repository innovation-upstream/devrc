---
name: bar
description: Operate the workbench/laptop i3status-rust status bar and its decoupled status-count poller. Add/edit/reorder bar blocks, tune the hide-at-zero count blocks (clawgate/mail/homelab-alerts/civitai) and their --red-above thresholds, wire block↔poller signals, run/debug the bar-status-poll timer + edge-triggered dunst toasts, and do the cutover correctly. Use when the user mentions the (status/i3/i3status) bar, a bar block/icon/threshold, the alert/mail/clawgate/civitai pills, bar-status-poll, ~/.cache/bar-status, the DND/agent-ops bar buttons, or "the bar is wrong/stale/red".
---

# i3status-rust status bar — operations

The workbench + laptop status bar. Migrated **i3blocks (/etc/nixos) → i3status-rust under home-manager** (PR #74). All bar config, block scripts, and the poller live in the **devrc** repo and deploy via home-manager. Point-in-time history: memory `i3-bar-i3status-rust-migration` + `devrc/claudedocs/handoff-agent-facing-workbench-2026-07-11.md`.

## Modernization stance (standing rule — applies whenever you touch this setup)
When editing the bar / i3 / any NixOS or home-manager config here, **prefer the current best-practice idiom and opportunistically modernize outdated portions you encounter** — don't just pattern-match the old code. Examples: retire a bespoke i3blocks-era script when i3status-rust has a native block for it; use the maintained `programs.*`/`nix/pkgs` idiom over a hand-rolled one; drop `/etc/nixos` remnants in favour of home-manager. Guardrails (from RULES): **reversibility-aware** — a drop-in modernization goes ahead, but a risky/broad rewrite gets **flagged before acting** ("here's the outdated bit, here's the modern replacement, blast radius X — your call"). Leave the setup a little more modern than you found it; never leave it more legacy.

## Where everything lives (all in `~/workspace/devrc`, `$DEVRC`)
| Thing | Path |
|---|---|
| Bar definition (blocks, theme, icons, thresholds) | `nix/graphical.nix` (`programs.i3status-rust.bars.top`) |
| i3 WM config (binds, bar buttons) | `nix/i3/config.nix` → `~/.config/i3/config` |
| Decoupled poller (workbench systemd-user timer) | `scripts/bar-status-poll` |
| Instant block scripts (read cache, never network) | `scripts/i3status-{clawgate,mail,alerts,civitai,dnd,media,airvpn}` |
| Poller output (per-source JSON) | `~/.cache/bar-status/*.json` (0600) |
| Generated bar TOML + symlinked scripts | `~/.config/i3status-rust/` |
| **RETIRED — never edit** | `/etc/nixos/{i3config,i3blocks}.nix`, `/etc/nixos/i3blocks-scripts` (except `airvpn-sudo` + `airvpn-updown`, which MUST stay there for the NOPASSWD sudoers rule + the tunnel PostUp/PostDown; the old Mullvad `vpn-sudo` is dead) |

## Architecture (why it's shaped this way — the CALM bar)
- **Blocks render local files only.** The bar NEVER queries a remote per tick. Anything remote (alert counts, mail, clawgate tasks) is fetched out-of-band by `bar-status-poll` (~45s systemd-user timer, workbench-only) which writes tiny JSON to `~/.cache/bar-status/` — so a slow/down source can never hang or break the bar. A down source writes `{"state":"stale"}` → the block renders **empty/invisible**.
- **Colour == "look at me."** Steady state is neutral/invisible (hide-at-zero count blocks). A block reddens only when something NEW crosses its `--red-above` line — the standing backlogs read neutral.
- **Instant refresh:** after writing a source file the poller does `pkill -RTMIN+N i3status-rs` to refresh exactly that block. The block's `signal = N` in `graphical.nix` **must** match `SIGNALS` in `bar-status-poll`.
- **Edge-triggered toasts:** the poller also fires a dunst toast **once on the rising edge** (count crosses `--red-above`), latched via `~/.cache/bar-status/<src>.toast-state` so steady state stays silent. Thresholds are env-tunable and MUST match the block's `--red-above` (or they drift).

## Block ↔ signal ↔ threshold map (workbench)
| Block | Script | signal | `--red-above` | Notes |
|---|---|---|---|---|
| homelab alerts | `i3status-alerts` | 13 | **34** (#97) | homelab backlog is noise; bumped 30→34 when it drifted to ~24-27 |
| civitai prod alerts | `i3status-civitai` | 14 | **340** | CLIENT prod; its own kubeconfig. **Red is CORRECT when real — do NOT tune it away.** (disk-space growth, 2026-07) |
| mail | `i3status-mail` | 12 | 0 (0→>0) | open `mail_actions` rows |
| clawgate | `i3status-clawgate` | 11 | 0 (0→>0) | operator-pending Tasks |
| DND | `i3status-dnd` | 15 | — | dunst paused indicator; `$mod+Shift+n` toggles |
| media | `i3status-media` | 16 | n/a | qBit-POD AirVPN pill (net_down), **state-driven**: neutral=connected, **RED**=firewalled (tunnel/port-fwd broken), soft-yellow=stale. poller `parse_media`/`fetch_media` read creds from `~/.config/bar/media.env` (0600) — the same `media.env` also feeds `deep-search`, a media release search/grab CLI on PATH (workbench) after `home-manager switch`. **left → `media-menu`**; **right → qBit WebUI** |
| airvpn (host) | `i3status-airvpn` | 10 | n/a | **HOST** AirVPN WireGuard pill (net_vpn), **state-driven**, replaces the decommissioned host Mullvad. Whole workbench routes through AirVPN; **default-OFF**, toggled from the menu. MINIMAL icon-button: **icon-only** dim when down (was `VPN off`) / neutral `CC` when up — falls back to the ipinfo EXIT country so a hostname endpoint (`ca3.vpn.airdns.org`, no manifest match → server cc null) shows `CA?` not a useless `??`/`???` / **RED** `LEAK` / yellow `CC!` on a down fwd-port / **icon-only** soft-yellow when stale. poller `parse_airvpn`/`fetch_airvpn` (sudo `airvpn-sudo status` for `wg show` + ipinfo exit-IP verify + fwd-port check). **left → `airvpn-menu`** (Connect/Disconnect · 🌍 switch server · 🔎 verify exit-IP/leak · 📶 fwd-port · 📊 `airvpn-detail --watch` TUI); **right → `airvpn-detail` float**. Switch data: committed `scripts/data/airvpn-servers.json` (gluetun-sourced WG endpoints + shared pubkey) + live load from `airvpn.org/api/status`; privileged ops via NOPASSWD `airvpn-sudo` (switch = `wg syncconf`, instant, no rebuild). Killswitch + split-tunnel bypasses in the conf's PostUp/PostDown → `airvpn-updown`. ⚠ The killswitch **POLICES ALL PHYSICAL NICs** (`oifname != {hardware NICs from /sys/class/net/*/device}`, fail-CLOSED if none) — NOT an enumerate-internal allow-list (that dropped the nebula overlay then the entire k3s cluster — #118/#126 audits); nebula transport is matched by `meta skuid nebula-mesh` (nebula runs `listen.port:0` → random sport, so a port rule is dead); `airvpn-sudo down` also force-tears the `airvpn_ks` table (orphan guard). `~/.config/bar/airvpn.env` (0600): `AIRVPN_WG_PORT`, `AIRVPN_FWD_PORT`. |

Bars differ by host (`isLaptop` in `graphical.nix`): laptop gets `batteryBlock` and **omits** GPU + all count blocks + poller (nebula-only, no LAN path to homelab endpoints); workbench gets GPU (RTX 5080), the count blocks, the state-driven `mediaBlock` (qBit/AirVPN), `agentOpsBlock` (▦), `rigcontrolBlock` (⚙).

## Deploy / apply
- **Single host (validate an edit end-to-end):** `home-manager switch --flake ~/workspace/devrc --impure`. This DOES restart the poller on a script change (`X-Restart-Triggers`).
- **Both hosts (after merge):** `scripts/ship.sh`.
- **Syntax check first:** `nix-instantiate --parse nix/graphical.nix >/dev/null`.
- **Skills are per-host** (this file lives at `~/.claude/skills/bar/`, NOT in the flake) — no git-add needed. But a NEW *block script* referenced from `graphical.nix` must be `git add`ed before switch (flakes only see tracked files).

## 🔴 Cutover gotcha (the one that bit us)
A change that alters which i3 config file is authoritative (e.g. the /etc/nixos→HM cutover) must finish with **`sudo systemctl restart display-manager`, NOT `i3-msg restart`** — a running i3 has `-c /etc/i3/config` baked into argv, so deleting that file + in-place restart = dead session. Routine HM block/threshold edits don't need this; only config-source cutovers do.

## Other gotchas
- **Icon overrides need the TABLE form.** `settings.icons = { icons = "material-nf"; overrides.gpu = "…"; }`. The `icons = "…"` string shortcut + an `[icons.overrides]` table **conflict and silently drop ALL icons to text** (#81). GPU is overridden to `nf-md-expansion_card` (material-nf maps `gpu`→a monitor glyph).
- **`airvpn-sudo` + `airvpn-updown` stay in `/etc/nixos/i3blocks-scripts/`** — a nix-store path would break the NOPASSWD sudoers rule (and the conf PostUp/PostDown ref). Deliberately NOT symlinked from HM. The host AirVPN SYSTEM block (packages + sudoers) is in `nix/system/airvpn-host.nix`. **Phase 2 LANDED 2026-07-21**: tunnel is **LIVE + verified** (exit CA, k3s + nebula overlay intact, killswitch armed), still **default-OFF** (toggle from the menu; the conf is Zach's secret at `/etc/wireguard/airvpn.conf`). Full apply / re-arm / mandatory re-test procedures → the **AirVPN host tunnel — ops** section below.
- **Poller runs from the working tree**, not a nix-store copy, so it can resolve sibling `scripts/mail-actions/_db.py` (loaded by explicit path — do NOT add `mail-actions/` to `sys.path`, its `llm.py` shadows things).
- **fuzzyclaw is UNTRUSTED** as a data source (`~/.tmux/tasks/*.json` is stale) — nothing bar-related should depend on it.
- Poller holds standing read-only port-forwards into TWO prod clusters (homelab + civitai) every ~45s — bounded (`TimeoutStartSec=90`), fail-safe, cgroup-killed. Accepted.

## AirVPN host tunnel — ops
The workbench's whole-host AirVPN WireGuard tunnel (the `airvpn` pill). Fail-closed killswitch (`scripts/airvpn-updown`) on a box that ALSO runs k3s AND is reached over nebula — so the killswitch **polices only the physical uplink** (`oifname != {hardware NICs ∪ default-route egress iface} accept`; everything on lo/tunnel/nebula.mesh/CNI/docker is always allowed), matches nebula transport by **socket owner** (`meta skuid nebula-mesh`, since nebula uses `listen.port:0` → random sport), and FAILS CLOSED if no NIC is found or the nft load errors. History (condensed): an enumerate-internal-networks allow-list first dropped the nebula overlay (would have locked Zach out of the remote host, #118) then dropped k3s apiserver→pod replies and CrashLooped the cluster (#126); uplink-only + skuid is leak-equivalent and durable (#122/#128).

- **Apply / re-apply Phase 2:** `sudo bash ~/workspace/devrc/nix/system/apply-airvpn-host.sh` (secret conf `/etc/wireguard/airvpn.conf` must exist first; installs helpers → `/etc/nixos/i3blocks-scripts/`, copies the module, wires `configuration.nix` `imports`, `nixos-rebuild`). Default-OFF — does NOT bring the tunnel up.
- **Ship a helper edit (no rebuild):** after editing `scripts/airvpn-{updown,sudo}`, re-install with plain `sudo install -m 0755 -o root -g root ~/workspace/devrc/scripts/airvpn-updown /etc/nixos/i3blocks-scripts/airvpn-updown` (same for `airvpn-sudo`). They're plain scripts and the NOPASSWD sudoers rule points at that path — **no `nixos-rebuild` needed**.
- **Apply a killswitch edit WITHOUT dropping the tunnel:** `sudo /etc/nixos/i3blocks-scripts/airvpn-updown up airvpn` re-arms in place (flush+reload the `airvpn_ks` table) — no reconnect.
- **🔴 Mandatory re-test protocol for ANY killswitch change** — a LAN-only test CANNOT reach the nebula direct-punch lockout mode, so it is NOT sufficient:
  1. Run it from a **LAN session** (192.168.50.x — always allowed = your recovery path) held open.
  2. Workbench k3s healthy: `KUBECONFIG=/home/zach/workspace/homelab-talos/workbench-kubeconfig kubectl get pods -A | grep -vE 'Running|Completed'` (expect nothing bad).
  3. Nebula overlay intact: `KUBECONFIG=$KC_HOMELAB kubectl get nodes` → **4 nodes**.
  4. Exit IP is the tunnel: `curl -s https://ipinfo.io/json` → **CA**, NOT the home IP `24.79.61.66`.
  5. **Confirm off-LAN:** `ssh zach@10.42.0.30` still connects (proves the nebula path survived — the LAN test can't cover this).
- **Debug / bail:**
  - arm line: `journalctl -t airvpn-updown -n5` → `up: killswitch armed (fwmark=…) policing [<nics>] …`.
  - instant bail that KEEPS the tunnel up (drops the killswitch only): `sudo nft delete table inet airvpn_ks`.
  - connect / disconnect (NOPASSWD): `sudo /etc/nixos/i3blocks-scripts/airvpn-sudo {up,down}`. `down` also force-deletes `airvpn_ks` (orphan guard for when `wg-quick down` no-ops on an already-gone iface).

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
# drive the write+signal path offline with fixtures (no live endpoint)
~/workspace/devrc/scripts/bar-status-poll --mock-alerts alerts.json --mock-mail 3
# unit tests for the pure parse/edge functions
python3 -m pytest ~/workspace/devrc/scripts/tests/test_bar_status.py
# manually refresh one block (N = signal)
pkill -RTMIN+13 i3status-rs
```
- **Block stuck/empty:** check its `~/.cache/bar-status/<src>.json` — `state:"stale"` means the source (kubeconfig/port-forward/creds) failed; run the poller by hand to see the exception.
- **Block never refreshes on change:** the block's `signal` in `graphical.nix` ≠ `SIGNALS` in `bar-status-poll`.
- **All icons show as text:** the `[icons.overrides]` / `icons=` shortcut conflict (see gotcha).
- **Red pill you think is noise:** confirm it's actually noise before bumping `--red-above` — homelab is noise, **civitai is not**. Tune the *threshold* in BOTH `graphical.nix` (`--red-above`) AND the poller toast env (or they drift), never suppress a real signal.

## Common tasks
- **Add a block:** define `fooBlock` in `graphical.nix`, add it to the `blocks` list (order = left→right, gate with `lib.optional*` for host scoping), symlink any script via `home.file`, `git add` the script, switch.
- **Change a threshold:** edit the block's `--red-above N` in `graphical.nix` **and** the matching `*_TOAST_ABOVE` env in the poller's `_toast_specs()` so the toast fires on the same line; switch.
- **New remote count source:** add a `parse_*`/`fetch_*` to `bar-status-poll`, a `SIGNALS` entry, a fixture-tested parser, an instant block script that reads the cache, and the block in `graphical.nix` with the matching `signal`.
