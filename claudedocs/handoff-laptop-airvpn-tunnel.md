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
- Verified during up windows: exit IP via ipinfo (US, AS62744), `ip route get <home-public-ip> uid 991` → `via 192.168.1.1 dev wlp170s0` while `ip route get 1.1.1.1` → `dev airvpn table 51820` (the split is exact), ping workbench 0% loss ~134–147ms (= no-tunnel baseline), writer/pill grammar honest (`off` dim icon, `US?` up-unverified, `airvpn ?` named).
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

### This doc leaked the home public IP to a PUBLIC repo; scrubbed at HEAD, NOT revoked
as-of: 2026-09-22
🔴 **WRITTEN BY A DIFFERENT SESSION than the one that authored this doc**, and deliberately
appended here rather than in a new doc: the leak came out of THIS effort, so the next
person working the tunnel is the one who must not put the value back. Only
append-bucket sections are touched — this arc's `State now`, `Next steps` and
`How to verify` are untouched and still belong to its author.

- **Symptom + exact repro:** `main`'s `tekton/devrc-pytests` leg had been RED since
  `3bd6fc22` (the commit that landed this doc), on
  `test_no_unallowlisted_public_ip_literal_is_committed`. Reproduce with
  `nix develop $DEVRC -c python3 -m pytest scripts/tests/test_no_public_ips.py -q`
  against any tree at or after that commit.
- **Observed (with values):** the repo is `visibility: PUBLIC`, `isPrivate=false`; the
  HEAD copy of this file answered **200** unauthenticated from
  `raw.githubusercontent.com`; **9** commits carry the value per `git log -S … --all`,
  the oldest well before this doc and two of them `untracked files on …` (stash-shaped),
  so it reached history by more than one route. The file was **NOT** in `PENDING_SCRUB`,
  so this was a NEW leak the gate blocked rather than tracked debt. `via: measurement`
- **Ruled out — that it was CI noise.** The measured red rate on this repo is ~42%, and
  that is exactly the trap. A control run on a PRISTINE `origin/main` worktree failed the
  SAME test identically, which is what proved it real and not caused by the PR under
  test. 🔴 On that same PR an EARLIER red was genuinely the PR's fault — two reds,
  opposite verdicts, only the control separated them. `via: measurement`
- **Ruled out — that scrubbing HEAD revokes the disclosure.** It does not. The four
  content gates read `git ls-files` and are blind to git history (devrc `CLAUDE.md` →
  `SECRETS.md`, "Dead credentials in reachable history"). `via: doc`
- **Ruled out — a history rewrite as the remedy.** Considered and DECLINED by the
  operator: it force-pushes a public repo, breaks every clone and fork, and does not
  un-index an address that is already public. `via: change`
- **Leading hypothesis:** rotating the address is the only action that actually revokes
  it. Whether that is worth doing is a judgement about the operator's ISP and threat
  model, not a measurement.
- **Next probe:** none for the diagnosis — it is closed. The ACTION is `devrc#1853`
  (branch `fix/scrub-public-ip-airvpn-handoff`), OPEN and MERGEABLE at the time of
  writing. Merge it, then re-run the gate on `origin/main` (not on the branch) and expect
  green. 🔴 It is the only PR from that session where `/audit-pr` round 0 is still
  actionable.

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

- 🔴 **DO NOT PUT THE REAL ADDRESS BACK IN THIS DOC.** The split-tunnel verification needs
  two probe targets and they are NOT the same kind of thing, which is why the fix is
  asymmetric: the HOME public IP is a real endpoint and is now `<home-public-ip>` (the
  gate's own remedy: *"if you are tempted to pin a real endpoint, the answer is an env
  var, not a pin"*), while the Cloudflare resolver is not an endpoint of ours, is not a
  disclosure, and carries a path-scoped ALLOWLIST entry instead. If you need the commands
  to be copy-pasteable, put the address in a shell variable at run time — do not inline it.
- 🔴 **THE EXPLANATION OF A LEAK IS ONE OF THE PLACES THE LEAK SPREADS TO.** The first PR
  body for #1853 quoted the address and was REFUSED by the `bash-guard` PreToolUse hook;
  the first commit message had the same defect and had already been PUSHED, and was
  amended + force-pushed (`--force-with-lease`, unmerged branch, no PR yet). The gate's
  own `PENDING_SCRUB` header warns about this for the tracking file — it is equally true
  of a commit message or a PR body on a public repo. The guard was the only thing that
  caught it.
- 🔴 **The ALLOWLIST is keyed on `(relpath, value)`, NEVER the value alone** — the gate's
  header records that a value-only exemption is repo-wide by construction and once let an
  audit mutant through. And never write the offending literal into the gate or its ledger:
  `PENDING_SCRUB` is pinned by COUNT precisely so the tracking file does not become a
  fresh copy of the leak.
- ⚠ **A green run of that gate is a claim about `git ls-files` only** — blind to history
  and to anything gitignored. Not a clean repo.
- ⚠ **The same line also carries a private `192.168.1.1`.** RFC1918, correctly not
  flagged, not a disclosure — noted so nobody "fixes" it and widens the diff.
- ⚠ **A permanently-red gate is the real defect here, not the IP.** It had been red for
  days; the `main-green-check` deadman REPORTS and never fixes, so nothing forced it to be
  cleared, and the standing cost is that every session learns to click through a red.

## How to verify
```bash
# tunnel + split-tunnel, with the tunnel UP:
ip link show airvpn && ip rule | rg 500          # pin present: uidrange 991-991 lookup main
ip route get <home-public-ip> uid 991                  # → via <gw> dev wlp170s0 (NOT airvpn)
ip route get 1.1.1.1 | head -1                    # → dev airvpn table 51820
curl -s https://ipinfo.io/json | jq -r .country   # → US
ssh zach@10.42.0.30 'echo nebula-ok'              # nebula path alive with tunnel up
# pill (after ~60s post-connect):
python3 -c "import json; d=json.load(open('~/.cache/bar-status/airvpn.json')); print(d['up'], d['verdict'], d['server'], d['country_code'])"
# writer + timer:
systemctl --user list-timers airvpn-status-poll.timer --no-pager | head -3
```
Killswitch re-test protocol: `claude/skills/bar/reference/airvpn.md` (laptop section). Instant bail that KEEPS the tunnel: `sudo nft delete table inet airvpn_ks`; full teardown: `sudo /etc/nixos/i3blocks-scripts/airvpn-sudo down`.
