# AirVPN host tunnel — ops (bar skill reference)

Loaded on demand from the `bar` skill. Covers the workbench's whole-host AirVPN WireGuard
tunnel and the `airvpn` bar pill (signal **10**, `scripts/i3status-airvpn`, net_vpn).

## The pill
**HOST** AirVPN WireGuard pill, **state-driven**, replaces the decommissioned host Mullvad.
Whole workbench routes through AirVPN; **default-OFF**, toggled from the menu. MINIMAL
icon-button:
- **icon-only** dim when down (was `VPN off`)
- neutral `CC` when up — falls back to the ipinfo EXIT country so a hostname endpoint
  (`ca3.vpn.airdns.org`, no manifest match → server cc null) shows `CA?` not a useless `??`/`???`
- **RED** `LEAK`
- yellow `CC!` on a down fwd-port
- **icon-only** soft-yellow when stale

Poller `parse_airvpn`/`fetch_airvpn` (sudo `airvpn-sudo status` for `wg show` + ipinfo exit-IP
verify + fwd-port check). **left → `airvpn-menu`** (Connect/Disconnect · 🌍 switch server · 🔎
verify exit-IP/leak · 📶 fwd-port · 📊 `airvpn-detail --watch` TUI); **right → `airvpn-detail`
float**. Switch data: committed `scripts/data/airvpn-servers.json` (gluetun-sourced WG endpoints
+ shared pubkey) + live load from `airvpn.org/api/status`; privileged ops via NOPASSWD
`airvpn-sudo` (switch = `wg syncconf`, instant, no rebuild). `~/.config/bar/airvpn.env` (0600):
`AIRVPN_WG_PORT`, `AIRVPN_FWD_PORT`.

## Where the code lives
- **`airvpn-sudo` + `airvpn-updown` stay in `/etc/nixos/i3blocks-scripts/`** — a nix-store path
  would break the NOPASSWD sudoers rule (and the conf PostUp/PostDown ref). Deliberately NOT
  symlinked from HM.
- The host AirVPN SYSTEM block (packages + sudoers) is in `nix/system/airvpn-host.nix`.
- **Phase 2 LANDED 2026-07-21**: tunnel is **LIVE + verified** (exit CA, k3s + nebula overlay
  intact, killswitch armed), still **default-OFF** (toggle from the menu; the conf is Zach's
  secret at `/etc/wireguard/airvpn.conf`).

## 🔴 The killswitch (why it is shaped this way)
Fail-closed killswitch (`scripts/airvpn-updown`) on a box that ALSO runs k3s AND is reached over
nebula — so it **polices only the physical uplink**
(`oifname != {hardware NICs ∪ default-route egress iface} accept`;
everything on lo/tunnel/nebula.mesh/CNI/docker is always allowed), matches
nebula transport by **socket owner** (`meta skuid nebula-mesh`, since nebula uses `listen.port:0`
→ random sport, so a port rule is dead), and FAILS CLOSED if no NIC is found or the nft load
errors. `airvpn-sudo down` also force-tears the `airvpn_ks` table (orphan guard).

⚠ It **POLICES ALL PHYSICAL NICs** (`oifname != {hardware NICs from /sys/class/net/*/device}`,
fail-CLOSED if none) — it is **NOT** an enumerate-internal allow-list. History (condensed): an
enumerate-internal-networks allow-list first dropped the nebula overlay (would have locked Zach
out of the remote host, #118), then dropped k3s apiserver→pod replies and CrashLooped the cluster
(#126); uplink-only + skuid is leak-equivalent and durable (#122/#128).

## Procedures
- **Apply / re-apply Phase 2:** `sudo bash ~/workspace/devrc/nix/system/apply-airvpn-host.sh`
  (secret conf `/etc/wireguard/airvpn.conf` must exist first; installs helpers →
  `/etc/nixos/i3blocks-scripts/`, copies the module, wires `configuration.nix` `imports`,
  `nixos-rebuild`). Default-OFF — does NOT bring the tunnel up.
- **Ship a helper edit (no rebuild):** after editing `scripts/airvpn-{updown,sudo}`, re-install
  with plain `sudo install -m 0755 -o root -g root ~/workspace/devrc/scripts/airvpn-updown /etc/nixos/i3blocks-scripts/airvpn-updown`
  (same for `airvpn-sudo`). They're plain scripts and the NOPASSWD sudoers rule points at that
  path — **no `nixos-rebuild` needed**.
- **Apply a killswitch edit WITHOUT dropping the tunnel:**
  `sudo /etc/nixos/i3blocks-scripts/airvpn-updown up airvpn` re-arms in place (flush+reload the
  `airvpn_ks` table) — no reconnect.

## 🔴 Mandatory re-test protocol for ANY killswitch change
A LAN-only test CANNOT reach the nebula direct-punch lockout mode, so it is NOT sufficient:
1. Run it from a **LAN session** (192.168.50.x — always allowed = your recovery path) held open.
2. Workbench k3s healthy:
   `KUBECONFIG=/home/zach/workspace/homelab-talos/workbench-kubeconfig kubectl get pods -A | grep -vE 'Running|Completed'`
   (expect nothing bad).
3. Nebula overlay intact: `KUBECONFIG=$KC_HOMELAB kubectl get nodes` → **4 nodes**.
4. Exit IP is the tunnel: `curl -s https://ipinfo.io/json` → **CA**, NOT the home IP
   `24.79.61.66`.
5. **Confirm off-LAN:** `ssh zach@10.42.0.30` still connects (proves the nebula path survived —
   the LAN test can't cover this).

## Debug / bail
- arm line: `journalctl -t airvpn-updown -n5` →
  `up: killswitch armed (fwmark=…) policing [<nics>] …`.
- instant bail that KEEPS the tunnel up (drops the killswitch only):
  `sudo nft delete table inet airvpn_ks`.
- connect / disconnect (NOPASSWD): `sudo /etc/nixos/i3blocks-scripts/airvpn-sudo {up,down}`.
  `down` also force-deletes `airvpn_ks` (orphan guard for when `wg-quick down` no-ops on an
  already-gone iface).
