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
- Branch / PR: `devrc` `main` at `4f0cbd48`. Base clone re-synced (`merge --ff-only`).
- ✅ **The mosh arc is CLOSED, and it closed by DELETING most of it.** #1865 was **closed unmerged** after five audit rounds; **#1869 merged as `4f0cbd48`** carrying one package line plus a 28-line comment. What was dropped: an **812-line** staged `/etc/nixos` editor and **1473** lines of tests, whose whole complexity budget went on making a sudo rewrite of a remote host's config safe on a machine the operator cannot reach — to mitigate a fault that had already stopped. `nix/pkgs/default.nix` now spells out the two-line server-side edit instead.
- 🔴 **mosh DOES NOT WORK YET, deliberately.** No host runs `mosh-server`; `mosh <host>` hangs at `Connecting...`. It needs a two-line edit at the console of the host you want to mosh INTO — the recipe is in the comment in `nix/pkgs/default.nix`, and `programs.mosh.openFirewall` **defaults to TRUE** and must be set false or it opens 1001 UDP ports on every interface including WAN.
- ✅ **The 2026-09-23 leak recurrence stays CLOSED** — #1861 (`5834b4c5`), corrections rescued in #1866 (`b7a30bc3`); `test_no_public_ips` green on `main`.
- **`main` is fully green** — all four Tekton legs pass. The `TestMutationKillMatrix.test_kills_the_readme_exclusion` red that blocked things was someone else's and was fixed by #1868 (`811fa910`).
- 🔴 **The `ip rule` pin (applied 2026-09-23 ~20:35 CDT) is STILL APPLIED and STILL NOT PERSISTENT.** `ip rule show | rg 5150` confirms it live. A reboot reverts it silently and the roaming returns with nothing to announce it. This is the only genuinely unfinished thing in the arc.
- **Workbench helper: still BLOCKED on sudo, by design.** `sudo -n` over ssh fails, so it is operator-run. `/etc/nixos/i3blocks-scripts/airvpn-updown` is still the Jul 21 copy (11,306 B, **0** `uidrange` lines) vs the laptop's synced 16,099 B (**3**). Laptop helper verified IN SYNC.
- **The mesh-flap arc is CLOSED on measurement, and NOT by anything we did.** Loss to home climbed **16% → 30% → 45%** between ~22:45 and ~00:03 local on 2026-09-23, then stopped dead with no config change. It has not recurred in ~14 h: watcher pid `2709742` alive at 14h20m, **1925+ polls, 0 triggers**; live four-path check 0/100 on every path.
- Untracked in the laptop tree: `nix/system/apply-networkmanager-openvpn.sh` (unrelated, pre-existing).

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

### 🔴 SUPERSEDES the "home router NAT/conntrack" hypothesis above — THREE vantages put the fault on the PATH, not at either end
- as-of: 2026-09-24
- 🔴 **The block above leads with "the home router is periodically losing NAT/conntrack state". That is REFUTED.** It rested on "home's outbound internet is clean during the blackouts", and that measurement **cannot test home's INBOUND path** — outbound ICMP builds its own state per packet. Do not resume by rebooting or reconfiguring the home router; it was measured innocent.
- **Symptom + exact repro:** laptop↔home over nebula AND tailscale loses 16–45% in contiguous 6.5–20 s blackouts. Repro: `ping -c 160 -i 0.5 -D 10.42.0.30` beside `ping -c 160 -i 0.5 -D <tailscale peer>`, concurrently.
- **Observed (with values):** the two overlays are not merely similar, they are **identical** — 160-packet concurrent run: nebula `49/160 (30.6%)`, tailscale `49/160 (30.6%)`, same gap count (4), same longest run (26), **same blackout start epochs to the second** (`1790225305`, `1790225319`). RTT 144.0 vs 143.3 ms.
- **Ruled out — the home end (router, NAT, conntrack, home ISP link).** A THIRD vantage outside both — the far box, probing the SAME destination `10.42.0.30` over the mesh — measured **0/300 loss** across window `1790225893..1790226042`, which **fully contains** the laptop's window `1790225901..1790226023` in which the laptop lost **127/280 (45%)** with blackouts at `…914`, `…989`, `…009`, `…023`. An external host reached the same machine flawlessly while the laptop could not. `via: measurement`
- **Ruled out — the laptop's own link and its ISP generally.** Local gateway `0/120` at 1–2 ms, signal −43 dBm; far non-home endpoint `0/140`. Only traffic to *home* dies. `via: measurement`
- **Ruled out — a broken forward or return path at the IP layer.** `mtr` laptop→home, 120 probes: last responding hop (16) **0.0%**. Reverse `mtr` from home→laptop's WAN, 60 probes: last responder (17) **1.7%**. Intermediate hops showing 15–75% are ICMP rate-limiting — later hops are clean, so that loss is not real. `via: measurement`
- **Leading hypothesis (UNTESTED — say so):** the overlay pings ride **UDP on the wire**; `mtr`'s probes do not. UDP dying while ICMP survives would point at per-flow state being evicted on the path. **This host sits behind carrier-grade NAT at its current location** — the forward `mtr` shows hops 2–6 all RFC1918 (`10.35.x`, `10.10.x`, `10.100.x`) — and CGNAT is a classic source of exactly this shape. It also explains why home↔far-box UDP is clean: different path, different CGNAT.
- **Next probe:** the discriminator is already built and RUNNING — `bash <scratchpad>/flap-watch.sh <log>`, pid recorded in `flap-watch.pid`. It polls 20 packets every 20 s and, on ≥15% loss, fires a 5-way 60 s concurrent probe (nebula, tailscale, ICMP-to-last-hop-before-home, ICMP-to-far-box, ICMP-to-local-gw) DURING the episode. **Its trigger and parser were positive-controlled** (a black-hole target returns 100% → TRIGGER; a synthetic gap parses to the right run length), so its zero is a real zero. ⚠ It is a plain background process, not a unit — a suspend or reboot ends it, and the fault has not recurred in 12.5 h, so it may need re-launching.

### mosh's stated benefit was WRONG, and the correction is smaller than the claim
- as-of: 2026-09-24
- **Symptom + exact repro:** not a bug — a false claim that shipped in three places (PR body, commit message, source comment) and would have been acted on. Read `nix/pkgs/default.nix`'s mosh comment on `main` for the corrected wording.
- **Observed (with values):** mosh does **not** open a UDP session first. It runs `ssh <host> mosh-server new`, parses the key and port out of **that ssh session**, and only then switches to UDP. So the TCP/SSH handshake — the exact thing described as failing with a banner-exchange timeout — is **unchanged** by installing mosh. `via: code`
- **Ruled out — that mosh helps you *initiate* a session over a link failing during connect.** It cannot; the bootstrap is ssh. What it buys is keeping an **already-established** session alive across a drop, which is real but smaller. `via: code`
- **Ruled out — that the benefit was ever demonstrated here.** mosh was never installed while a blackout was live, and no host runs `mosh-server`. The shipped comment labels it an inference for that reason. `via: measurement`
- **Leading hypothesis:** for the 6.5–20 s blackouts measured on this path, plain ssh over TCP mostly *survives* — you get a frozen terminal, not a dropped session — so mosh's practical gain here is comfort rather than continuity. Untested.
- **Next probe:** none needed for the claim; it is settled. If you want the benefit, do the two-line edit in `nix/pkgs/default.nix`'s comment on the target host and then `mosh zach@10.42.0.30` during a live episode.

## Next steps (ranked)
1. **Decide the PERMANENT form of the `ip rule` pin**, in whichever repo owns the laptop's nebula unit. Kernel-state-only today, so a reboot silently reverts it. 🔴 **Justify it on the ssh evidence (5/5 `rc=0` vs five consecutive `rc=255`), NOT on "it stopped the roaming"** — that claim did not survive re-measurement, and the instrument that could settle it is the one the pin disables. Costs: breaks `kubectl` against homelab while active; a tailscale route exclusion for UDP:4242 may be the better shape than a `to <node>` pin.
   `forcing: incident`
2. **Operator, ON THE LAN: refresh the workbench stable-path helper** — `sudo install -m0755 ~/workspace/devrc/scripts/airvpn-updown /etc/nixos/i3blocks-scripts/airvpn-updown`. Verify: `cmp -s /etc/nixos/i3blocks-scripts/airvpn-updown ~/workspace/devrc/scripts/airvpn-updown && echo SYNC`. ⚠ Not urgent — measured that no timer and no enabled unit can fire it — but pair it with the killswitch re-test, because toggling that tunnel with the stale copy while off-LAN is a plausible lockout.
   `forcing: regression`
3. Silence the gateway v6-remote noise — nebula config in `homelab-talos` (advertise v4 only from the gateway, or v6-listen on the laptop).
   `forcing: none`
4. Re-run `scripts/data/refresh-airvpn-servers` from a host with qBit-pod access and bake country_code the supported way (its `_from_github` fallback is DEAD).
   `forcing: none`
5. `i3status-airvpn`'s no-country-code fallback abbreviates the full country NAME (`"United States"[:2]` → `UN`). One-line fix if it ever shows again; with cc baked it should be unreachable.
   `forcing: none`

## Defects (batched)
- talosctl client cert EXPIRED (`tls: expired certificate` against 192.168.50.94) — blocks node-level debugging from this host.
- Workbench's stable-path `airvpn-updown` is stale (0 `uidrange` lines vs the repo's 3) — closed by next-step 2. General hazard: `/etc/nixos/i3blocks-scripts/` copies are NOT ship-managed nor covered by `drift-check.sh` rc 17.
- `refresh-airvpn-servers --from-github` fallback rotted (gluetun moved to `servers.go`); the kube source still works from the workbench.
- Nothing in the repo checks whether any fleet host has `programs.mosh.enable` set, so the shipped client stays inert with no signal. `drift-check.sh` is the natural home if that ever matters.

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

- 🔴 **A commit pushed to a branch AFTER its PR squash-merged is stranded and invisible to every gate.** Measured this session: `7511cbb8` landed on #1861's branch six minutes after #1861 merged as `5834b4c5`, so it was never in `main` and never in the merged PR. It was recovered as #1866 only because someone looked. **Verify a squash landed by CONTENT, never by ancestry** — `git merge-base --is-ancestor` is false after every squash, forever.
- 🔴 **A line-based grep over a wrapped sentence returns a confident zero.** Verifying #1866's merge, one sentinel read ABSENT because the sentence wraps; normalising whitespace found it. Control the two forms against each other before believing an absence.
- 🔴 **`gh pr view --json headRefOid` reporting an old sha is not always API lag — the PR may have MERGED at that sha.** This session diagnosed lag, was wrong, and nearly re-merged a merged PR. Read `state` before theorising about staleness.
- 🔴 **A 20-second ping sample cannot size an episodic fault.** A 40-packet run read 65% loss; a concurrent 120-packet run minutes later read 16%, same mechanism. The first reading nearly got reported as a degradation.
- 🔴 **Widening a guard's regex to admit one spelling admitted a WAN hole.** #1865 round 2 widened an idempotence check so `programs.mosh.enable` would match; round 4 found it then accepted a config with `openFirewall` ABSENT — which defaults TRUE and opens 1001 UDP ports on every interface — reported `ALREADY APPLIED`, printed an affirmative "openFirewall is set false" banner, and ran the switch. Fix the CLASS, not the spelling the audit named.
- ⚠ **`nixos-rebuild switch` on a host whose `/etc/nixos` has no flake pulls the ROOT CHANNEL.** Measured on the workbench: `dry-build` reported **444 to build / 1420 to fetch** against a system last built 5 days earlier on `nixpkgs-unstable`. A staged script that ends in a bare `switch` is a full system update, not its own delta — which is why #1865's script now stops before switching by default.
- ⚠ The `bash-guard` hook cannot resolve `git -C $VAR`; it judges the caller's cwd instead. Pass an absolute path, or assign the variable in the same command.

- 🔴 **A COMMENT THAT SPELLS AN UNGUARDED FACT IS A CLAIM THAT ROTS, AND THIS REPO ALREADY WROTE THAT DOWN.** `nix/pkgs/tools/default.nix` states it for the opencode entry (only the version is spelled, because only the version is guarded by a test). The mosh comment's first draft spelled four unguarded facts: a **PR number and its lifecycle state**, two line counts (**"~600" was 812; "~1000" was 1473** — understated 35% and 47%), a **per-host nebula group list** measured on one host and quoted where the other host's reader would act on it, and a pointer at a **handoff doc**, which is per-topic and deliberately overwritten. 🔴 Worse: the PREVIOUS version of that same block carried an explicit warning never to cite a PR's state (*"this comment carried two wrong ones"*), and the rewrite **deleted the warning and then did the thing it warned against.** Point durable comments at the subsystem-index entry, not at a handoff doc.
- 🔴 **`isLaptop` (`nix/home.nix:327-330`) is a BACKLIGHT PROBE THAT FAILS OPEN.** Gating a laptop-only package on it risks NOT shipping to the laptop — the wrong failure direction. That is why `mosh` is in the universal list rather than host-gated.
- 🔴 **Five audit rounds are sunk cost, not a reason to merge.** The right question is whether the artifact should exist, and it must be re-asked when the diagnosis changes — round 0 exists for exactly that and should have been re-run at round 2, not round 5. The rounds were not wasted: they caught a bare `nixos-rebuild switch` that would have pulled 444 builds / 1420 fetches onto an unreachable host, a converge path that could never succeed, and a WAN hole introduced by a fix specified in an earlier round.
- ⚠ **`pgrep -c -f <pattern>` counts ITSELF.** It reported two `flap-watch.sh` processes; resolving PIDs and reading `/proc/<pid>/cwd` found one. An audit round filed the phantom as a leaked process. Resolve PIDs; never trust the count.
- ⚠ **An auditor's concrete numbers need the same re-derivation as anyone's.** Round 0 on #1869 reported the workbench's nebula inbound as two groups; measured, it has three (`admin`, `homelab`, `lighthouse`) — the laptop also has three, differing only in the third (`workbench`). Its *point* was right and its *number* was not; the comment now carries no count at all.

## How to verify
🔴 **The arc's own closing-condition block is preserved verbatim below — do NOT replace it
with the watcher checks; those answer a different question.** New this session, first:
```bash
# the flap watcher — still running, and did it catch an episode?
ps -o pid=,etime= -p "$(cat <scratchpad>/flap-watch.pid)"
rg -c 'quiet|TRIGGER' <scratchpad>/flap-watch.log
rg -A8 'EPISODE' <scratchpad>/flap-watch.log      # empty = the fault has not recurred
# loss to home, WITH the controls that make it interpretable (a bare number is not a reading):
ping -c 120 -i 0.5 10.42.0.30   | tail -2         # home over nebula — UDP on the wire
ping -c 120 -i 0.5 192.168.1.1  | tail -2         # own link — must be 0%
# the pin (kernel state only; reverts on reboot):
ip rule show | rg 5150
# PR #1865 after taking main in (the red is INHERITED, fixed on main by 811fa910):
gh pr checks 1865 --repo innovation-upstream/devrc
```

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
