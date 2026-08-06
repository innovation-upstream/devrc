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

🔴 **`/etc/airvpn-updown.env` (root-owned, 0600, untracked) is a PREREQUISITE, not an
afterthought** — one line, `NEBULA_LIGHTHOUSE=<lighthouse public IP>`. This repo is PUBLIC,
so the IP is not committed (`scripts/tests/test_no_public_ips.py` enforces that). Without it
the killswitch still arms, but the lighthouse's direct bypass route + nft accept are omitted
and the arm line reads `lighthouse=UNSET`. The script **parses** this file (it never sources
it) and **ignores it** if it is not root/self-owned, if it is group/other-writable, or if the
value is not an IP — each of those logs a reason. **Check `lighthouse=` after any re-arm**;
`UNSET` on a host that should have it means the file is missing or was rejected, NOT that the
change worked.
`sudo /etc/nixos/i3blocks-scripts/airvpn-updown check-env` prints the resolved value without
touching a single rule or route.

## Procedures
🔴 **These are in dependency order. Do not reorder them** — the site config has to exist
before the helper that reads it is installed, or you install and re-arm a degraded killswitch.

1. **Create the site config (FIRST, once per host):**
   `printf 'NEBULA_LIGHTHOUSE=%s\n' <lighthouse ip> | sudo install -m 0600 -o root -g root /dev/stdin /etc/airvpn-updown.env`
2. **Apply / re-apply Phase 2:** `NEBULA_LIGHTHOUSE=<ip> sudo -E bash ~/workspace/devrc/nix/system/apply-airvpn-host.sh`
   (secret conf `/etc/wireguard/airvpn.conf` must exist first; **refuses** unless the site
   config exists or you pass `NEBULA_LIGHTHOUSE=` / `ALLOW_NO_LIGHTHOUSE=1`; installs helpers →
   `/etc/nixos/i3blocks-scripts/`, copies the module, wires `configuration.nix` `imports`,
   `nixos-rebuild`). Default-OFF — does NOT bring the tunnel up.
3. **Ship a helper edit (no rebuild):** after editing `scripts/airvpn-{updown,sudo}`, re-install
   with plain `sudo install -m 0755 -o root -g root ~/workspace/devrc/scripts/airvpn-updown /etc/nixos/i3blocks-scripts/airvpn-updown`
   (same for `airvpn-sudo`). They're plain scripts and the NOPASSWD sudoers rule points at that
   path — **no `nixos-rebuild` needed**. Step 1 must already be done on this host.
4. **Apply a killswitch edit WITHOUT dropping the tunnel:**
   `sudo /etc/nixos/i3blocks-scripts/airvpn-updown up airvpn` re-arms in place (flush+reload the
   `airvpn_ks` table) — no reconnect. 🔴 Re-arming **deletes the table first**, so a re-arm that
   fails to load leaves you with the blanket fallback, and a re-arm with a bad site config
   leaves you with `lighthouse=UNSET`. Read `journalctl -t airvpn-updown -n5` every time.

## 🔴 Mandatory re-test protocol for ANY killswitch change
A LAN-only test CANNOT reach the nebula direct-punch lockout mode, so it is NOT sufficient.

🔴 **PRECONDITION — THE TUNNEL MUST ALREADY BE UP.** `airvpn-updown up` arms the killswitch
against whatever it can see *now*. With the tunnel DOWN, `wg show … fwmark` fails (no
`meta mark` accept) and there is no endpoint accept, so the chain reduces to
*allow LAN + gateway + nebula-by-skuid + lighthouse, then **drop***, and **every other
host-originated packet on a physical NIC is dropped**. It is bounded — the output hook does
not see forwarded pod traffic, and LAN + nebula survive — but you will blackhole the host's own
egress. Check first:

```
ip -br link show airvpn 2>/dev/null | grep -qw UP \
  && sudo wg show airvpn latest-handshakes | grep -qv '\b0$' \
  && echo TUNNEL-UP || echo TUNNEL-NOT-READY-DO-NOT-ARM
```

🔴 **EXISTENCE IS NOT UP-NESS.** `ip link show airvpn` alone succeeds for an interface left
behind by a half-failed `wg-quick up` — a state in which `wg show … fwmark` still fails and
arming still blackholes you. The check above requires the link to be **UP** *and* wg to report
a **non-zero handshake**, i.e. a peer that has actually answered.

**Instant bail, from the LAN session, at any point:** `sudo nft delete table inet airvpn_ks`.

1. Run it from a **LAN session** (192.168.50.x — always allowed = your recovery path) held open.
2. Site config exists: `sudo test -f /etc/airvpn-updown.env && sudo stat -c '%U:%G %a' /etc/airvpn-updown.env`
   → expect `root:root 600`. Create it first if not (Procedures step 1).
3. **Install the edited helper** — without this you are testing the OLD script.
   🔴 **Confirm the right branch is checked out FIRST** — the whole reason this step exists is
   that the deployed helper was found byte-identical to `main` while the PR was open:
   `git -C ~/workspace/devrc status -sb | head -1` → expect the branch under test, clean.
   Then:
   `sudo install -m 0755 -o root -g root ~/workspace/devrc/scripts/airvpn-updown /etc/nixos/i3blocks-scripts/airvpn-updown`
4. `sudo /etc/nixos/i3blocks-scripts/airvpn-updown check-env` → **`lighthouse=<ip> state=ok`**.
   `state=unreadable-by-uid-…` means you dropped the `sudo`, not that the file is wrong.
5. Re-arm: `sudo /etc/nixos/i3blocks-scripts/airvpn-updown up airvpn`
6. `journalctl -t airvpn-updown -n5` → **`up: killswitch ARMED(primary) … lighthouse=<ip>`**.
   Anything else is a STOP:
   - 🔴 `ARMED(fallback)` → **BAIL NOW** (`sudo nft delete table inet airvpn_ks`). The blanket
     fallback has **no `meta mark` accept**, so wg's own encrypted packets are dropped and the
     tunnel dies. Do not continue to step 10 — you will get a confusing `curl` failure instead
     of a diagnosed one.
   - 🔴 `STALE(previous)` → **BAIL NOW.** Both loads failed and an OLDER table survived; it was
     built for a different endpoint/fwmark and does not match this tunnel.
   - 🔴 `NOT-ARMED` → the uplink is **unfiltered**. Fix the load error before going further.
   - `lighthouse=UNSET` → the site config was missing, rejected or malformed (step 2/4).
7. 🔴 **`ip route get <lighthouse-ip>`** — the ONLY step that observes the `/32` bypass, i.e.
   the thing A-1 is about. `via <lan-gw> dev <phys>` = the bypass is present;
   `dev airvpn` = it is GONE and lighthouse traffic is inside the tunnel.
   **Step 10 cannot substitute for this**: with a healthy tunnel, lighthouse traffic routed
   *into* the tunnel still arrives, so the ssh check passes either way.
8. Workbench k3s healthy:
   `KUBECONFIG=/home/zach/workspace/homelab-talos/workbench-kubeconfig kubectl get pods -A | grep -vE 'Running|Completed'`
   (expect nothing bad).
9. Nebula overlay intact: `KUBECONFIG=$KC_HOMELAB kubectl get nodes` → **4 nodes**.
10. Exit IP is the tunnel: `curl -s https://ipinfo.io/json` → **CA** and an AirVPN `org`,
    NOT your home ISP's IP/org. (This repo is PUBLIC — the home IP is deliberately not
    written down here; compare against the same command with the tunnel DOWN.)
11. 🔴 **Confirm OFF-LAN — and this step is worthless unless you run it FROM somewhere else.**

    `10.42.0.30` is the **workbench's own** nebula address. Steps 1–10 put you *on the
    workbench, on the LAN*, so running `ssh zach@10.42.0.30` there is a **self-ssh**; from
    another machine on the same LAN it is a same-LAN hop. Neither touches the nebula
    **direct-punch** path — the one this section's opening line says a LAN-only test cannot
    reach, and the one that locked the host out in #118.

    Run it **from a genuinely off-LAN host** — the laptop on a phone hotspot / any network that
    is not `192.168.50.0/24`, reaching the workbench over nebula:

    ```
    # ON THE LAPTOP (off-LAN), toward the workbench:
    ip -br addr | grep -q '192\.168\.50\.' && echo "STILL ON THE LAN — this proves nothing"
    ssh -o ConnectTimeout=10 zach@10.42.0.30 'echo off-lan-ok; hostname'
    ```

    * **PASS** — `off-lan-ok` plus the workbench's hostname within the timeout. The overlay
      survived the killswitch, including the direct-punch path.
    * **FAIL** — timeout, `No route to host`, or a hang. The killswitch is eating nebula
      transport. **Bail from the LAN session you held open in step 1**:
      `sudo nft delete table inet airvpn_ks`. That is what step 1 is for.
    * **INVALID** — the guard line above printed anything. You are on the LAN; the result tells
      you nothing either way, so do not record it as a pass.

    If no off-LAN host is available, this protocol **cannot be completed** — say so rather than
    substituting the self-ssh, which passes unconditionally.

## Debug / bail
- arm line: `journalctl -t airvpn-updown -n5` →
  `up: killswitch ARMED(primary) (fwmark=…) policing [<nics>], gw=… ep=… lighthouse=<ip>`.
  **FOUR states.** The kernel is asked with `nft list table`; the line is never written from an
  exit status. Only the first is a pass:

  | state | meaning | what to do |
  |---|---|---|
  | `ARMED(primary)` | the intended ruleset is installed | continue |
  | `ARMED(fallback)` | blanket fail-closed ruleset — **no `meta mark` accept, so wg's own encrypted packets are dropped and the tunnel dies** | 🔴 **BAIL** |
  | `STALE(previous)` | both loads failed; an OLDER table survived the atomic load. Built for a different endpoint/fwmark — it can read as filtering while the tunnel is dead | 🔴 **BAIL** |
  | `NOT-ARMED` | no table at all — **the uplink is UNFILTERED** | 🔴 fix the load error |

  `STALE(previous)` only became reachable when the load was made atomic: before that, a failed
  load had already flushed the table, so "both failed" always meant NOT-ARMED.
- instant bail that KEEPS the tunnel up (drops the killswitch only):
  `sudo nft delete table inet airvpn_ks`.
- connect / disconnect (NOPASSWD): `sudo /etc/nixos/i3blocks-scripts/airvpn-sudo {up,down}`.
  `down` also force-deletes `airvpn_ks` (orphan guard for when `wg-quick down` no-ops on an
  already-gone iface).
