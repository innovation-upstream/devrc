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
- Branch / PR: `devrc` `main` at `5273d71b` (was `de2e1087` when this line was written) — `scripts/ship.sh` run rc=0 this session: BOTH hosts converged + switched at `de2e1087`, cross-host sha agreement verified. (Handoff's "workbench 3 behind at `a2f45567`" was STALE — git was already converged; only the stable-path helper lagged.)
- Workbench helper refresh BLOCKED on sudo: `ssh zach@10.42.0.30 'sudo -n install ...'` → `sudo: a password is required`. Its `/etc/nixos/i3blocks-scripts/airvpn-updown` is still the Jul 21 copy (11,306 B, 0 `uidrange` lines) vs laptop's synced 16,099 B (3 uidrange lines). Laptop helper verified IN SYNC. Operator deferred the sudo step. ⚠ **Not urgent**, measured 2026-09-23: no user timer, no system timer and no enabled unit can bring the workbench tunnel up — it takes a deliberate pill click, and the tunnel is down. Do it on the LAN, with the killswitch re-test (next-step 2).
- Laptop tunnel DOWN right now (`ip link show airvpn` → absent); killswitch helpers converged.
- ✅ The 2026-09-23 leak recurrence is CLOSED: #1861 squash-merged as `5834b4c5`, and `test_no_public_ips` is green on `origin/main` (15 passed, measured at `5273d71b`). Details in the leak investigation below.
- **Nebula flap arc — the `ip rule` pin IS APPLIED (2026-09-23 ~20:35 CDT) and evaluated.** It fixed the identity-roaming and did NOT close the flap; the residual is measured to be outside nebula and outside this host. See the investigation block. ⚠ **Not persistent — a reboot reverts it**; the cost of making it permanent is stated once, with the measurement, in that block.
- Untracked in laptop tree: `nix/system/apply-networkmanager-openvpn.sh` (unrelated, not nix-read).

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
as-of: 2026-09-23
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
- **Next probe:** 🔴 **REOPENED 2026-09-23 — this block said "it is closed" and that was
  wrong within 24 hours.** #1853 merged as `1c7ad1b9` and the gate went green; the very
  next commit to this file, `a6e98a3d`, put the SAME value back plus two more, and `main`
  went red again on the same test. Hash-compared across all three revisions of this file.
  The ACTION is now `devrc#1861`. 🔴 **The diagnosis is not "someone was careless": it is
  that the prohibition lives in PROSE inside the very document people append to, and prose
  does not gate.** The open question is whether anything cheaper than the test can make the
  rule reachable at APPEND time — the gate catches it only after a push, on CI. Until that
  is answered, treat this block as OPEN, not as history.

<!-- as-of: 2026-09-23 -->
### Nebula mesh flaps: "connection keeps hanging and recovering" — TWO mechanisms, one FIXED, one NOT NEBULA'S
- as-of: 2026-09-23
- 🔴 **THIS BLOCK'S EARLIER VERDICT WAS HALF RIGHT, AND THE HALF IT GOT WRONG IS THE HALF
  THAT STILL BITES.** It read `ROOT CAUSE MEASURED, fix not applied` and attributed the
  whole symptom to nebula identity-roaming. The pin was applied 2026-09-23 ~20:35 CDT and
  **the roaming stopped while the flap did not**, so the symptom had TWO causes and this
  block described one. Do not resume by re-chasing nebula: three measurement rounds below,
  each with a control, put the residual flap outside nebula, outside this laptop and
  outside devrc entirely. ⚠ **ONE NEBULA SYMPTOM IS EXPLICITLY NOT COVERED BY THAT
  SENTENCE, AND IS STILL LIVE:** both lighthouses (`10.42.0.1`, `10.42.0.2`) remain
  **100% loss from BOTH hosts**, re-measured 2026-09-23 after the pin. The two-cause split
  does not account for it, and an older `homelab-talos` handoff recorded lighthouse ICMP
  flapping independently of any of this. Keep it on the queue.
- **FIXED — nebula identity-roaming (the mechanism this block originally described).**
  `sudo ip rule add to 192.168.50.94 priority 5150 lookup main` moved the homelab node off
  the tailscale subnet route (`ip route get 192.168.50.94` → `via 192.168.1.1 dev
  wlp170s0`, was `dev tailscale0 table 52`), and the ssh burst that used to be five
  consecutive `rc=255` is **5/5 `rc=0`** — re-run and re-confirmed. `via: measurement`
  🔴 **BUT "THE ROAMING STOPPED" IS UNPROVEN, AND AN EARLIER WORDING OF THIS BULLET
  COMPARED TWO DIFFERENT INSTRUMENTS.** It read "`Host roamed|header is too short` =
  1 event in 40 min (was a storm)". The **1** was counted in the LAPTOP's
  `journalctl -u nebula@mesh`; **"a storm" described the GATEWAY POD's log**, read via
  `kubectl`. Those are different sources, so the pair was never a before/after.
  Re-measured on the laptop journal for 2026-09-23: 11:00→1, 12:00→1, 14:00→2, 20:00→1,
  21:00→1 — the pin went in ~20:35, so **post-pin is indistinguishable from pre-pin**,
  14 events in 7 days; the workbench's journal shows 9 in 17 days and never storms.
  And the pod-log side **cannot now be re-read, because the pin itself breaks `kubectl`
  against homelab** (rc 124, measured) — so this doc can no longer reach its own
  before-number. Treat the roaming as UNMEASURED either way; the ssh burst is the only
  re-derivable half. ⚠ **The pin is live kernel state and is NOT persistent — a reboot reverts
  it, and the roaming comes back.** It also breaks `kubectl` against homelab while in place
  (`$KC_HOMELAB` targets that node); `sudo ip rule del priority 5150` restores it.
- 🔴 **NOT FIXED, AND NOT NEBULA'S — periodic ~6.5 s blackouts of the path TO HOME.**
  Measured concurrently, one probe per transport, 0.5 s spacing:
  | target | loss | blackout |
  |---|---|---|
  | nebula → workbench (home) | 15/140 | epoch 1790215400, **+6.5 s** |
  | tailscale → workbench (home) | 15/140 | epoch 1790215400, **+6.5 s — same second** |
  | a far endpoint of ours NOT at home | **0/140** | NONE |
  | nearby anycast resolver | 1/140 | NONE |
  | local wifi gateway | 0/140 | NONE |
  Tailscale knows nothing about nebula's hostmap, so no nebula mechanism can blank both in
  the same second. Loss is **dominated by one contiguous ~13-packet (6.5 s) run** —
  blackout-shaped, not lossy-path-shaped. ⚠ An earlier wording said "clustered, **never**
  scattered: zero isolated drops", and the table's own arithmetic refutes it: 15 lost
  against a 13-packet run leaves 2 isolated. An independent re-probe an hour later gave
  14/140 = one run of 13 plus one isolated drop. The run is the signal; the absolute was
  overstated. ⚠ **Window: ONE ~70 s sample per transport** (140 packets at 0.5 s), so
  "only traffic to HOME dies" rests on that single window. `via: measurement`
- **Ruled out — the laptop's uplink, its ISP, and the local router's NAT.** The local wifi
  gateway is 0 % loss at 1–2 ms, signal −43 dBm, 650/866 Mbit. A local-router conntrack
  flush would blank both overlays at once while sparing ICMP — **the far non-home endpoint
  is the control that kills it: 0/140, no blackouts, same wifi, same router, same ISP, same
  long-haul distance.** Only traffic to HOME dies. `via: measurement`
- **Ruled out — the home uplink being down.** From the workbench (which is AT home), its own
  outbound internet was **1 lost packet in 280** across a window that fully contains two
  laptop-side blackouts — probe epochs `…563`–`…703` vs blackouts `…614`→`…621` and
  `…681`→`…688`, and its single loss at `…566` falls outside both. Home egress works
  throughout. `via: measurement` ⚠ An earlier run of this same check was thrown away
  because its epoch range was not recorded, so overlap could not be proven — record both
  clocks or the comparison is worthless.
- **Ruled out — a clean period to plan around.** Observed gaps are **141 s, 67 s, 143 s** —
  irregular, roughly one to two and a half minutes. An earlier draft of this block said
  "~every 67 s" off two points; that is RETRACTED. `via: measurement`
- **Leading hypothesis (home side, NOT high confidence):** the home router is periodically
  losing NAT/conntrack state, so established long-lived UDP flows (both overlays) stall
  until they re-punch, while ICMP and fresh outbound flows rebuild state per packet and
  survive. That fits every observation above but has **not** been tested at the router.
  `mtr` to the home endpoint shows the last responding hop at 0.0 % over 70 probes (~129 ms)
  — intermediate hops at 40–70 % are ICMP rate-limiting, since later hops are clean — so the
  failure is at or beyond the home edge. One sample; the window may simply have missed a
  blackout.
- **Next probe — AT THE HOME ROUTER, not here.** Its uptime and logs around the epochs
  above, its conntrack table size and eviction counters, and whether the ISP link is
  renegotiating. If the onset is recent, a firmware update or a reboot is the cheap first
  move. 🔴 **Nothing in devrc, nothing in nebula's config and nothing on this laptop can
  cure this** — the levers here only shorten each stall: nebula already has
  `use_relays: true`. 🔴 **Do NOT read the 0/140 row as "the relay works" — an earlier
  wording of this bullet did, and it conflated two addresses.** That row probed the relay
  host's UNDERLAY endpoint, which shows only that the host is reachable. The relay's MESH
  address — `10.42.0.2`, the one `relays:` actually names — is **100% loss from both
  hosts**, measured again 2026-09-23 beside the underlay at 0% / 109 ms. So relaying is
  NOT known to work and any lever resting on it is unproven.
  For a human working across it, `mosh` should ride a 6.5 s blackout where `ssh` hangs —
  ⚠ `via: inference`, NOT measurement: it is a long-lived-UDP tool proposed against a
  hypothesised long-lived-UDP-state fault, and it is installed on neither host, so nothing
  here has demonstrated it. `devrc#1865` stages it.
- **Symptom + exact repro:** laptop↔workbench over nebula drops for ~30–60 s windows, then recovers. Repro: `ping -c 4 -i 0.3 10.42.0.30` loops — measured at 17:15:34–17:15:52 CDT a 100%-loss window of ~7 samples between clean stretches; `ssh zach@10.42.0.30` times out during banner exchange for minutes at a time (17:58–18:03, five consecutive rc=255) then succeeds.
- **Observed (with values):** failing set is exactly the LIGHTHOUSES: `ping 10.42.0.1` (homelab lighthouse) and `10.42.0.2` (Hetzner lighthouse) 100% loss from BOTH laptop and workbench; `10.42.0.30` (workbench) and `10.42.0.20` (prod-gw) reachable with 0% loss (~137 ms / ~109 ms). Gateway pod (`nebula-gateway-5fpfv`, kubectl `-n nebula`, node `talos-jkj-deb` 192.168.50.94) log: `Tunnel status certName=zach-laptop tunnelCheck="map[method:active state:dead]"` (19:03:22Z) and `Host roamed ... newAddr="10.244.0.220:50519"` (19:05:09Z). Workbench log: my handshakes arrive `from="100.71.230.83:41232"` (my TAILSCALE addr) and lighthouse parsed garbage `from 10.244.0.220:54949: header is too short` (22:15:25Z) — `10.244.0.220` = `tailscale-subnet-router-5f4658c69f-2g99j` pod (kubectl field-selector lookup, 15d old, 0 restarts). Laptop routing: `ip route get 192.168.50.94` → `dev tailscale0 table 52`; `ip route show table 52` → `192.168.50.0/24 dev tailscale0` (rule 5270).
- **Ruled out:** lighthouse pods down — kubectl `-n nebula` shows both `nebula-lighthouse-xl58z` and `nebula-gateway-5fpfv` Running 0 restarts; via: measurement. Gateway node dead — workbench pings `192.168.50.94` at 0.1 ms and `<home-public-ip>` at 1.0 ms; via: measurement. Tailscale broken — `tailscale status` shows subnet-router `active; direct`; via: measurement. talosctl route to node internals — `talosctl -n 192.168.50.94 netstat` fails `tls: expired certificate` (client cert expiry, separate defect); via: command.
- **Leading hypothesis (high confidence, measured)** — ⚠ **SUPERSEDED, kept for the record.**
  Correct about the roaming, which the pin fixed; it does not explain the residual blackouts,
  and "high confidence" was asserted over a symptom that turned out to have two causes: laptop's tailscale subnet route `192.168.50.0/24 → tailscale0` intercepts nebula's UDP to `192.168.50.94:4242`, so stage-1s ride the subnet-router POD and arrive at nebula pods sourced from bogus addrs (`100.71.230.83`, `10.244.0.220`). Peers "roam" my identity onto those paths; when the tailscale path churns the roamed paths die → hang; a fresh handshake over a live path (this host's WAN address, `<laptop-wan-ip>`, seen in lighthouse log at 19:02:55Z) → recover. Lighthouse ICMP failing is likely the same arrival-path corruption, and every node's hostmap resolution degrades with the lighthouse tunnels.
- **Next probe** — ⚠ **DONE 2026-09-23; the verdict is the two-mechanism split above.** The recipe it carried is deleted rather than preserved: `claude/skills/handoff/SKILL.md` protects a superseded *reading* verbatim but says to delete a now-wrong **instruction** in the same delta, and re-running that `ip rule add` is exactly the wrong instruction — the pin is already applied. Its one still-live idea (a tailscale route exclusion for UDP:4242, rather than a `to <node>` pin) is carried in next-step 1, where it can be acted on.

## Next steps (ranked)
1. Decide the PERMANENT form of the `ip rule` pin, in whichever repo owns the laptop's nebula unit — it is kernel-state-only today, so a reboot silently reverts it. Costs and shape options are stated once, with the measurement, in the investigation's FIXED bullet; a tailscale route exclusion for UDP:4242 may be the better shape than a `to <node>` pin. **Do not treat this as the flap fix — it is not** (see the investigation).
   `forcing: incident`
2. Operator, ON THE LAN: refresh the workbench stable-path helper — `sudo install -m0755 ~/workspace/devrc/scripts/airvpn-updown /etc/nixos/i3blocks-scripts/airvpn-updown`. Verify: `cmp -s /etc/nixos/i3blocks-scripts/airvpn-updown ~/workspace/devrc/scripts/airvpn-updown && echo SYNC`. ⚠ Not urgent — nothing automatic can fire it (verified: no timer, no enabled unit) — but pair it with the killswitch re-test, because toggling that tunnel with the stale copy while off-LAN is a plausible lockout.
   `forcing: regression`
3. Silence the gateway v6-remote noise — nebula config in `homelab-talos` (advertise v4 only from the gateway, or v6-listen on the laptop).
   `forcing: none`
4. Re-run `scripts/data/refresh-airvpn-servers` from a host with qBit-pod access and bake country_code the supported way (its `_from_github` fallback is DEAD).
   `forcing: none`
5. `i3status-airvpn`'s no-country-code fallback abbreviates the full country NAME (`"United States"[:2]` → `UN`). One-line fix if it ever shows again; with cc baked it should be unreachable.
   `forcing: none`

## Defects (batched)
- talosctl client cert EXPIRED (`tls: expired certificate` against 192.168.50.94) — blocks all node-level debugging from this host.
- Workbench's stable-path `airvpn-updown` is stale (no roaming mode, no nebula pin) — closed by next-step 2's one command. General hazard: `/etc/nixos/i3blocks-scripts/` copies are NOT ship-managed nor covered by `drift-check.sh` rc 17 — stable-path helpers can rot silently on any host.
- `refresh-airvpn-servers --from-github` fallback rotted (gluetun moved to `servers.go`); the kube source still works from the workbench.
- The mockbin `ip` stub accepts any arg shape — any future `ip rule`/`nft` change needs a live unprivileged syntax probe before merge.

## Gotchas / decisions / dead-ends
- 🔴 The laptop has NO systemd-resolved: NetworkManager `dns=none` + local dnsmasq (`127.0.0.1` → public resolver). wg-quick ABORTS on the conf's `DNS =` line (`resolvconf` fails, interface torn down in the same invocation — measured on first connect). The apply script now strips that line when resolved is absent. Do NOT re-add DNS to the laptop conf.
- 🔴 The laptop's `configuration.nix` splits `imports =` and `[` across two lines — the apply script's awk keys on the `];` CLOSER (POSIX classes), not the opener (#1845), and parse-validates the edit.
- The laptop's wg conf endpoint is a HOSTNAME (`america3.vpn.airdns.org:1637`); AirVPN America servers use distinct entry (.10) vs exit (.16) IPs — that is why the verdict needed baked `country_code` (the workbench's Canada server happens to exit on its entry IP and verified without it).
- `sudo` from the units logs `PWD=/` — a root-privileged `airvpn-sudo down` with `PWD=/` in the journal was the OPERATOR's pill-menu Disconnect (session-3.scope), not a service or test. Don't misread that log line again.
- The pill's `?` on `US?` is the UNVERIFIED marker (exit IP ≠ entry IP and no server cc), NOT the stale marker — two different `?`s in one block's grammar.
- `--block` mirror machinery from #1839 was REMOVED, not left dead; `i3status-airvpn` stays in RELAY_BLOCKS so the `wb` rollup still carries the workbench tunnel's alarms on the laptop.

- 🔴 **NO ROUTABLE ADDRESS OF OURS GOES IN THIS DOC — AND ONE OF THEM CAME BACK 24 HOURS
  AFTER IT WAS SCRUBBED.** Measured by hash-compare across the three revisions of this
  file: #1853 removed the home public IP at `1c7ad1b9` (2026-09-22 17:27), and at
  `a6e98a3d` (2026-09-23 17:43) the mesh-flap investigation put **that same value** back,
  alongside two genuinely new ones — this laptop's WAN as the lighthouse saw it, and the
  Hetzner lighthouse. So the shape is not "three new literals": it is **one returning value
  plus two new**, one day apart, with the prohibition already written in this file. The
  rule is **any endpoint of ours, in any section, however it arrives** (a journal quote, a
  `static_host_map` excerpt, a ping result): write `<home-public-ip>` / `<laptop-wan-ip>` /
  `<hetzner-lighthouse-ip>`, and put the value in a shell variable at run time if a command
  must be copy-pasteable. 🔴 **A GOTCHA IS NOT A GATE, AND THE RETURNING VALUE IS THE
  PROOF.** This bullet was already here, in this file, naming that exact value, and it was
  read past inside a day; `scripts/tests/test_no_public_ips.py` is what caught it, both
  times. Widening the sentence does not make it enforcement — the only reason the recurrence
  was visible at all is that the gate is red until someone fixes it.
- ⚠ **A PLACEHOLDER IN THIS DOC IS GATE COMPLIANCE, NOT REVOCATION — and for one of the
  three it is not even a removal from HEAD.** The value written `<hetzner-lighthouse-ip>`
  is still committed at HEAD in `scripts/airvpn-updown` and
  `scripts/claude-hooks/tests/test_guard_core.py`, deliberately, as tracked `PENDING_SCRUB`
  debt — the killswitch one is split into its own PR because moving it is a runtime change.
  The other two are genuinely gone from HEAD. None of the three is revoked: the gate reads
  `git ls-files` and is blind to history.
- ⚠ **The Cloudflare resolver's ALLOWLIST pin for this doc is DELETED** — the literal left
  the file when the mesh-flap rewrite replaced `How to verify` wholesale, and the gate's
  second leg fails a pin that matches nothing rather than leaving a rubber stamp. Note what
  that rewrite cost: **seven command lines and the killswitch escape hatch**, not just one
  probe. `How to verify` below is the pre-rewrite block restored, with the resolver reached
  through `getent` at run time so no literal is needed — 🔴 `ip route get one.one.one.one`
  does NOT work (`Error: any valid prefix is expected`); that argument must be an address,
  which is exactly why the runtime-variable remedy exists.
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

- `kubectl get pods -A` on `$KC_HOMELAB` TIMES OUT (rc 124, >90 s) — use `kubectl get pods -n <ns>` or a `--field-selector` instead; `kubectl get ds/deploy/cm -A` works fine.
- `env KUBECONFIG=... kubectl ...` — bare `KUBECONFIG=... kubectl` in a compound command parses as a command name (timeout: 'failed to run command'); must use `env`.
- The gateway/lighthouse nebula pods are distroless — `kubectl exec ... -- ping/ls/nebula` all fail (`executable file not found`); logs are the only window.
- The two pods share one node (`talos-jkj-deb` / 192.168.50.94) and the lighthouse config's static_host_map only carries `10.42.0.2 → <hetzner-lighthouse-ip>:4242`; the gateway config's comment says LAN IP is used deliberately ("NAT hairpinning won't work") — consistent with the hairpin half of the flap.
- `sudo -n` over ssh to the workbench fails (password required) — sudo-touching steps are operator-run, hand over the exact command.
- (carried) Laptop has NO systemd-resolved — do NOT re-add `DNS` to the laptop wg conf.
- (carried) The pill's `?` on `US?` is the UNVERIFIED marker, not the stale marker.
- (carried, WIDENED) DO NOT put ANY routable address of ours back in this doc — not just the home public IP. Use `<home-public-ip>` / `<laptop-wan-ip>` / `<hetzner-lighthouse-ip>` or a runtime shell variable. On 2026-09-23 it recurred with three literals, one of them the value scrubbed 24 h earlier.

## How to verify
```bash
# mesh stability after the fix (want 0% loss over 60 samples):
ping -c 60 -i 1 10.42.0.30 | tail -1
# ssh burst (want rc=0, no banner-exchange timeouts):
for i in 1 2 3 4 5; do ssh -o ConnectTimeout=10 zach@10.42.0.30 'true'; echo rc=$?; sleep 2; done
# lighthouse tunnels (want 0% loss — currently 100% from BOTH hosts):
ping -c 5 -i 0.3 10.42.0.1; ping -c 5 -i 0.3 10.42.0.2
# ship state (want both hosts at one sha):
scripts/ship.sh
```
🔴 **THE BLOCK BELOW IS THE ARC'S OWN CLOSING CONDITION and was DELETED WHOLESALE by the
mesh-flap rewrite** — **seven** command lines plus the killswitch escape hatch. This is that
block restored from `1c7ad1b9`, **plus two additions** (the `HOME_PUB` assignment, and the
`journalctl` line that makes closing-condition item 4 runnable, and the `HOME_PUB`
placeholder guard) — a restoration, not a transcription. ⚠ An earlier wording said "plus
two additions" and undercounted by one: the guard is executable and was not in the
original, so a reader auditing this fence against `1c7ad1b9` would find a line the
manifest did not account for. One address literal is replaced by a runtime lookup; `<home-public-ip>` was
ALREADY a placeholder at `1c7ad1b9`, which is what #1853 did.
🔴 **RUN IT ALL WITH THE TUNNEL UP, and substitute `HOME_PUB` first.** With the tunnel down
*several* lines go vacuously green — but not all: `ip link show airvpn` fails outright, the
split-tunnel probe returns `dev wlp170s0` instead of `dev airvpn table 51820`, and the pill
read returns `up`=false. Those three are the ones that catch you.
```bash
# tunnel + split-tunnel, with the tunnel UP:
ip link show airvpn && ip rule | rg 500          # pin present: uidrange 991-991 lookup main
HOME_PUB='<home-public-ip>'                      # set at run time; NEVER inline the value
# Guard the mistake you will ACTUALLY make -- pasting this fence WITHOUT substituting.
# 🔴 An earlier version of this guard was `: "${HOME_PUB:?...}"`, which fires only when the
# variable is EMPTY. The block above never produces empty, so that guard was UNREACHABLE:
# the unsubstituted paste walked straight past it into `ip route get '<home-public-ip>'`,
# whose error is the SAME "any valid prefix is expected" the doc teaches means "you passed
# a hostname". Match the placeholder, not the empty string.
case "$HOME_PUB" in *'<'*) echo "substitute HOME_PUB first" >&2; return 2>/dev/null || exit 2;; esac
ip route get "$HOME_PUB" uid 991                 # → via <gw> dev wlp170s0 (NOT airvpn)
# the other half of the SPLIT: a non-LAN target must leave via the tunnel. `ip route get`
# needs an ADDRESS, so resolve the resolver's NAME at run time rather than pinning a literal:
ip route get "$(getent ahostsv4 one.one.one.one | awk '{print $1; exit}')" | head -1
                                                 # → dev airvpn table 51820
curl -s https://ipinfo.io/json | jq -r .country   # → US
ssh zach@10.42.0.30 'echo nebula-ok'              # nebula path alive with tunnel up
# pill (after ~60s post-connect) — this is closing-condition item 3:
python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.cache/bar-status/airvpn.json'))); print(d['up'], d['verdict'], d['server'], d['country_code'])"
# writer + timer:
systemctl --user list-timers airvpn-status-poll.timer --no-pager | head -3
# closing-condition item 4 — ONLY the IPv6-remote noise, no MTU/routing churn:
SINCE='<up-time>'                                 # e.g. '2026-09-23 20:35'
journalctl -u 'nebula@mesh' --since "$SINCE" | rg -c 'Failed to write outgoing packet|Failed to send handshake'
```
🔴 **Killswitch re-test protocol: `claude/skills/bar/reference/airvpn.md` (laptop section).**
It is FAIL-CLOSED on this laptop's ONLY uplink. Instant bail that KEEPS the tunnel:
`sudo nft delete table inet airvpn_ks`; full teardown:
`sudo /etc/nixos/i3blocks-scripts/airvpn-sudo down`. ⚠ The original block's `python3 -c`
passed a literal `~` to `open()`, which does not expand it — that line always raised
`FileNotFoundError`. Fixed above with `os.path.expanduser`; it is a repair, not a
transcription.
