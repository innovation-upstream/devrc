# Handoff: laptop-airvpn-tunnel — 2026-09-21

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading. Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
The laptop runs its OWN host AirVPN WireGuard tunnel (replacing PR #1839's mirror of the
workbench's pill), default-OFF, click-to-toggle from its own bar pill, with a ROAMING
killswitch whose derived LAN is correct on any network — and nebula's transport must be
unaffected while the tunnel is up.
- **closing-condition:** `check` — with the tunnel up: `curl -s https://ipinfo.io/json` shows the
  AirVPN exit; `ssh zach@10.42.0.30 'true'` works (nebula intact); the pill renders `US`
  (verified, not `US?`) after one post-connect writer poll; and
  `journalctl -u 'nebula@mesh' --since <up-time> | rg -c 'Failed to write outgoing packet|Failed to send handshake'`
  counts ONLY `listener is IPv4, but writing to IPv6 remote` lines (see open investigation)
  — no MTU/routing churn.

## State now
- Branch / PR: `devrc` `main` at `c55c401d` (#1849); ALL session PRs merged: #1839 (mirror, now REPLACED), #1840 (real laptop tunnel), #1844/#1845 (apply-script fixes), #1846 (writer PATH), #1847 (manifest country_code), #1848/#1849 (nebula split-tunnel pin). No open PRs.
- Laptop (this host): tunnel **UP** (reconnect 22:18:33) with the nebula pin LIVE (`ip rule`: `500: from all uidrange 991-991 lookup main`; nebula uid 991), killswitch armed roaming (`LAN allowed = 192.168.1.0/24 derived from wlp170s0`), exit IP US through tunnel, nebula ssh + LAN direct both work while up. Helpers at `/etc/nixos/i3blocks-scripts/` refreshed 22:05 (dash-form uidrange). `/etc/wireguard/airvpn.conf`: roaming PostUp/PreDown hooks present, `DNS` line REMOVED (host has no systemd-resolved; dnsmasq→public resolver rides the tunnel).
- Verified during up windows: exit IP via ipinfo (US, AS62744), `ip route get 24.79.61.66 uid 991` → `via 192.168.1.1 dev wlp170s0` while `ip route get 1.1.1.1` → `dev airvpn table 51820` (the split is exact), ping workbench 0% loss ~134–147ms (= no-tunnel baseline), writer/pill grammar honest (`off` dim icon, `US?` up-unverified, `airvpn ?` named).
- Workbench: primary clone is BEHIND — last `ship.sh` ran at #1846 (`a2f45567`); #1847/#1848/#1849 are ff-merged locally on the laptop only. Its stable-path `/etc/nixos/i3blocks-scripts/airvpn-updown` predates BOTH the roaming port AND the nebula pin (latent re-degradation, see Defects).
- Deploy honesty: every piece live-verified against the real path on the laptop (not inferred); the workbench half is UNVERIFIED since #1847 (needs `scripts/ship.sh`).
- 🔴 `/etc/nixos/i3blocks-scripts/` copies are NOT ship-managed — every `airvpn-updown` change needs an operator `sudo install -m0755 ~/workspace/devrc/scripts/airvpn-updown /etc/nixos/i3blocks-scripts/airvpn-updown` (or an apply-script re-run) PER HOST. Today this bit twice (stale helper → no nebula pin).

## Open investigations — live diagnosis state
<!-- as-of: 2026-09-21 -->
### Gateway IPv6-remote noise — "listener is IPv4, but writing to IPv6 remote" (homelab-gateway)
- as-of: 2026-09-21
- **Symptom + exact repro:** `journalctl -u 'nebula@mesh'` on the laptop shows, on first contact with peer `homelab-gateway` (vpnAddrs 10.42.0.10), `level=error msg="Failed to write outgoing packet" error="listener is IPv4, but writing to IPv6 remote" udpAddr="[fda3:e207:b5c1:…]:51774"` (and fded:984d:43ce:…). Harmless in effect: nebula continues on the v4 remotes (handshakes complete; ssh to 10.42.0.30 works).
- **Observed (with values):** counts by hour 2026-09-21: 18:00–20:00 = **0**; 20–21 = 4; 21–22 = 10. After the #1848/#1849 fix, the up window at 22:18 produced exactly 4 more lines, ALL of this IPv6 shape for homelab-gateway — the routing-regression errors are gone.
- **Ruled out:** the AirVPN tunnel/killswitch as the CAUSE of these lines — `via: measurement` (the lines are v6-listener vs v6-remote, fired on first contact with a peer, and persist with the tunnel down; the tunnel-only errors were a SEPARATE mechanism, fixed by the uid pin).
- **Leading hypothesis:** the laptop's nebula listener is IPv4-only while the gateway advertises IPv6 remotes (its LAN ULA / k8s-node v6 addrs); nebula logs per attempt and falls through to v4. Config-side cleanup on the GATEWAY (advertise v4 only, or give the laptop a v6 listener) — lives in the `homelab-talos` repo, not devrc.
- **Next probe:** `rg -i 'listen|preferred_ranges|advertised' ~/workspace/homelab-talos -g '*.yml' -g '*.yaml' -g '*.conf'` for the gateway's nebula config (find where its `listen`/`advertised_addrs` are set) and check whether other peers (workbench) log the same lines.
### Pill verdict path — `US?` (up, unverified) vs `US` (verified)
- as-of: 2026-09-21
- **Symptom + exact repro:** with the tunnel up pre-#1847, `~/.cache/bar-status/airvpn.json` carried `verdict=unknown server=None cc=None` → pill `US?`. Root cause: the manifest stored no `country_code` (0/255 rows) and the poller reads only the manifest. FIXED (#1847): 254/255 rows now carry `country_code`.
- **Observed (with values):** `server=Chamaeleon cc=None verdict=unknown handshake_age=21` before the fix; post-fix lookup returns Chamaeleon with `country_code: "us"`.
- **Ruled out:** writer env PATH (was a REAL second bug, fixed in #1846 — `sudo -n airvpn-sudo status` died rc 127 `env: bash: not found` under the unit's PATH) — `via: measurement`.
- **Next probe:** on the next tunnel-up writer poll (~60s after Connect): `python3 -c "import json; d=json.load(open('/home/zach/.cache/bar-status/airvpn.json')); print(d['verdict'], d['server'], d['country_code'])"` — expect `verified Chamaeleon us`; pill `US`. **Not yet observed while up** (the 22:18 reconnect predates no manifest issue — it simply wasn't re-read).

## Next steps (ranked)
1. Converge the WORKBENCH: `scripts/ship.sh` (its tree is at `a2f45567`, three behind), then refresh its stable-path helper — `sudo install -m0755 ~/workspace/devrc/scripts/airvpn-updown /etc/nixos/i3blocks-scripts/airvpn-updown` (workbench) — else its next tunnel-up window re-measures today's nebula regression.
   `forcing: regression` — the same measured degradation mechanism (b5fb6a0c), one tunnel-up window away on the second host.
2. `sudo install -m0755 ~/workspace/devrc/scripts/airvpn-updown /etc/nixos/i3blocks-scripts/airvpn-updown` on the LAPTOP was done 22:05 — re-run after any future `airvpn-updown` change (this file is operator-managed at the stable path by design).
   `forcing: none` — standing rule, nothing pending.
3. Silence the gateway v6-remote noise (open investigation above) — nebula config in `homelab-talos` (advertise v4 only from the gateway, or v6-listen on the laptop).
   `forcing: none`
4. Re-run `scripts/data/refresh-airvpn-servers` from a host with qBit-pod access and bake country_code the supported way — its `_from_github` fallback is DEAD (gluetun ships `servers.go` now; raw URLs 404) and the pod moved off `media-stack` (ns absent on the workbench k3s and the talos homelab; `minio-thc-media-ssd` exists but no qbit/gluetun pods found there). 1 of 255 servers has no `country_code`.
   `forcing: none`
5. `i3status-airvpn`'s no-country-code fallback abbreviates the full country NAME (`"United States"[:2]` → `UN` — observed as pill `UN?` before #1847). One-line fix if it ever shows again; with cc baked it should be unreachable.
   `forcing: none`

## Defects (batched)
- Workbench's stable-path `airvpn-updown` is stale (no roaming mode, no nebula pin) — closed by next-step 1's two commands. The general hazard: `/etc/nixos/i3blocks-scripts/` copies are NOT ship-managed nor covered by `drift-check.sh` rc 17 (that gate covers `nix/pkgs` srcDirs only) — stable-path helpers can rot silently on any host.
- `refresh-airvpn-servers --from-github` fallback rotted (gluetun moved to `servers.go`); the kube source still works from the workbench.
- The mockbin `ip` stub accepts any arg shape — it validated the uidrange intent but could NOT catch the real-syntax bug (#1849). Any future `ip rule`/`nft` change needs a live unprivileged syntax probe (valid syntax → `Operation not permitted`; invalid → parse error) before merge.

## Gotchas / decisions / dead-ends
- 🔴 The laptop has NO systemd-resolved: NetworkManager `dns=none` + local dnsmasq (`127.0.0.1` → public resolver). wg-quick ABORTS on the conf's `DNS =` line (`resolvconf` fails, interface torn down in the same invocation — measured on first connect). The apply script now strips that line when resolved is absent. Do NOT re-add DNS to the laptop conf.
- 🔴 The laptop's `configuration.nix` splits `imports =` and `[` across two lines — the apply script's awk keys on the `];` CLOSER (POSIX classes), not the opener (#1845), and parse-validates the edit.
- The laptop's wg conf endpoint is a HOSTNAME (`america3.vpn.airdns.org:1637`); AirVPN America servers use distinct entry (.10) vs exit (.16) IPs — that is why the verdict needed baked `country_code` (the workbench's Canada server happens to exit on its entry IP and verified without it).
- `sudo` from the units logs `PWD=/` — a root-privileged `airvpn-sudo down` with `PWD=/` in the journal was the OPERATOR's pill-menu Disconnect (session-3.scope), not a service or test. Don't misread that log line again.
- The pill's `?` on `US?` is the UNVERIFIED marker (exit IP ≠ entry IP and no server cc), NOT the stale marker — two different `?`s in one block's grammar.
- `--block` mirror machinery from #1839 was REMOVED, not left dead; `i3status-airvpn` stays in RELAY_BLOCKS so the `wb` rollup still carries the workbench tunnel's alarms on the laptop.

## How to verify
```bash
# tunnel + split-tunnel, with the tunnel UP:
ip link show airvpn && ip rule | rg 500          # pin present: uidrange 991-991 lookup main
ip route get 24.79.61.66 uid 991                  # → via <gw> dev wlp170s0 (NOT airvpn)
ip route get 1.1.1.1 | head -1                    # → dev airvpn table 51820
curl -s https://ipinfo.io/json | jq -r .country   # → US
ssh zach@10.42.0.30 'echo nebula-ok'              # nebula path alive with tunnel up
# pill (after ~60s post-connect):
python3 -c "import json; d=json.load(open('~/.cache/bar-status/airvpn.json')); print(d['up'], d['verdict'], d['server'], d['country_code'])"
# writer + timer:
systemctl --user list-timers airvpn-status-poll.timer --no-pager | head -3
```
Killswitch re-test protocol: `claude/skills/bar/reference/airvpn.md` (laptop section). Instant bail that KEEPS the tunnel: `sudo nft delete table inet airvpn_ks`; full teardown: `sudo /etc/nixos/i3blocks-scripts/airvpn-sudo down`.
