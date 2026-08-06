# Handoff — Host AirVPN tunnel: shipped live + killswitch hardened (2026-07-21)

## TL;DR
The workbench **host AirVPN WireGuard tunnel** (the bar's `airvpn` pill) went from
"the vpn control doesn't work" → **live, verified, leak-safe, cluster-safe,
lockout-safe**. Phase-2 is applied; the fail-closed killswitch survived two
self-inflicted outages and now uses a durable uplink-policing model. **Nothing is
outstanding** — this doc is a record + operating guide for the next session.

## What shipped (all merged to `main`)
| PR | what |
|---|---|
| #118 | Phase-2 apply script (`nix/system/apply-airvpn-host.sh`) + minimal icon-only pill + nebula-overlay killswitch exemption |
| #119 | Removed dead speech-to-text dictation section from README (unrelated rot) |
| #122 | Apply script handles a split multi-line `imports =` / `[` in `configuration.nix` |
| #126 | Killswitch → **uplink-only policing** + `skuid` nebula match + orphan-teardown (fixes the k3s outage) |
| #128 | Police **all physical NICs** (union w/ default-route egress iface) + fail-closed on nft error / no-NIC / bridge uplink + active-pill display fix (`???`→`CA?`) |

## Live state (verified end-to-end 2026-07-21)
- Tunnel **UP**, exit `CA` (an AirVPN egress IP), **not** the home ISP IP.
- Killswitch armed **policing all 3 physical NICs** — journal: `up: killswitch armed
  (fwmark=…) policing [enp18s0u2u2c2 eth1 wlp15s0]`.
- **k3s cluster healthy**, **nebula overlay** reachable (homelab 4 nodes), and
  **off-LAN `ssh zach@10.42.0.30` works** under the live killswitch (Zach confirmed).
- Pill display: **icon-only dim** when off · **`CA?`** when up.

## The killswitch model (why it's shaped this way — read before touching it)
`scripts/airvpn-updown` PostUp/PreDown. **Polices ONLY physical uplinks:**
- Rule 1: `oifname != { <all physical NICs> } accept` — set = `/sys/class/net/*/device`
  hardware NICs **UNION** the live default-route egress iface (catches a bridge/bond/
  VLAN uplink that has no `/sys` device). Everything non-physical (lo, airvpn tunnel,
  `nebula.mesh`, `cni0`/`flannel.1` CNI, docker0, veth) is always allowed → durable,
  no rule per new internal net.
- Uplink allows: wg `fwmark`, LAN `192.168.50.0/24`, **`meta skuid "nebula-mesh"`**
  (nebula runs `listen.port:0` → random source port, so match by SOCKET OWNER, not a
  port), lighthouse `/32`, endpoint `/32`, gateway `/32`, ip6 link-local, multicast →
  else **`drop`**.
- **Fail-closed** on: no physical NIC found, OR `nft -f` load failure → blanket
  fallback (internal ifaces + skuid + LAN + lighthouse → drop). Killswitch ALWAYS
  arms (the old fail-open `exit 0` is gone).
- `airvpn-sudo down` force-deletes the `airvpn_ks` table (orphan guard: wg-quick down
  no-ops PostDown when the iface is already gone → table would stay armed).

**History (why not simpler):** an "enumerate every internal network to ALLOW"
killswitch first dropped the nebula overlay (→ would lock Zach out of his own remote
host, #118) then dropped k3s apiserver→pod replies (→ whole cluster CrashLooped,
#126). Uplink-only policing is leak-equivalent AND doesn't need to know about internal
nets. **Do not revert to an allow-list model.**

## How to operate it (also in the `bar` skill, refined this session)
- **Connect / disconnect:** left-click the pill → menu, or `sudo /etc/nixos/i3blocks-scripts/airvpn-sudo {up,down}` (NOPASSWD). DEFAULT-OFF.
- **Apply a killswitch edit without a reconnect:** re-install then re-arm in place —
  `sudo install -m 0755 -o root -g root ~/workspace/devrc/scripts/airvpn-updown /etc/nixos/i3blocks-scripts/airvpn-updown`
  then `sudo /etc/nixos/i3blocks-scripts/airvpn-updown up airvpn` (flush+reload, tunnel stays up).
- **🔴 Re-test ANY killswitch change from an OFF-LAN nebula path** (a LAN test can't
  reach the direct-punch lockout mode): LAN session held open (recovery), then check
  k3s health + `$KC_HOMELAB kubectl get nodes` (4) + `curl ipinfo.io` (CA) + off-LAN
  `ssh zach@10.42.0.30`.
- **Bail (keeps tunnel up):** `sudo nft delete table inet airvpn_ks`.
- **Debug:** `journalctl -t airvpn-updown -n5` (arm line lists the policed NICs).

## Gotchas hit this session (so next session doesn't relearn them)
- **Installed helpers ≠ repo.** `/etc/nixos/i3blocks-scripts/airvpn-{updown,sudo}` are
  plain copies (not nix-store, for the NOPASSWD sudoers path). Editing the repo does
  nothing until you re-`install` them; a running killswitch keeps the OLD rules until
  a re-arm/reconnect.
- **`nft -c` can't validate rulesets in this sandbox** (rootless netlink init fails) —
  render the chain offline + reason; not machine-validated.
- **Concurrent `ship.sh` mangled the working tree mid-session** (a stale June-29
  autostash stash-pop left conflict markers in `CLAUDE.md` + reverted files). It
  self-resolved when ship.sh finished; the 2 stale autostashes were dropped. If the
  tree looks corrupted, check `git stash list` + whether a ship is running before
  touching anything.
- **My commit once landed on local `main`** (concurrent merges shifted refs under a
  `checkout -b`). Fixed with `git branch -f` surgery (no reset --hard). Watch the
  current branch after branch ops when merges are landing concurrently.

## Follow-ups / not done (all optional — nothing blocking)
- **Laptop applicability:** host AirVPN is **workbench-only** today. The multi-NIC +
  fail-closed killswitch was explicitly built with the multi-homed laptop in mind, but
  applying host AirVPN to the laptop is not done (would need its own conf + apply).
- Audit 🟢 items left by choice: broadcast-DHCP on a policed NIC is dropped (desirable);
  forwarded/pod/container egress is unpoliced by design (`hook output` only); stale
  endpoint `/32` left on `down` (harmless). See PR #128 comments.
- The poller doesn't populate the connected **server** country for a hostname endpoint
  (`ca3.vpn.airdns.org`) → pill shows `CA?` via the exit-country fallback. Fine as-is;
  could map the endpoint hostname → country if a clean `CA` (no `?`) is wanted.

## Kickoff message for next session
> Host AirVPN tunnel is live + verified on the workbench (PRs #118/#122/#126/#128
> merged). Killswitch polices all physical NICs, `skuid`-matches nebula, fail-closed.
> Operate via the `bar` skill's "AirVPN host tunnel — ops" section. Nothing
> outstanding. If asked to touch the killswitch: re-install to
> `/etc/nixos/i3blocks-scripts/`, re-arm in place (`airvpn-updown up airvpn`), and
> re-test from an OFF-LAN nebula path with a LAN recovery session — a LAN-only test
> can't reach the direct-punch lockout mode.
