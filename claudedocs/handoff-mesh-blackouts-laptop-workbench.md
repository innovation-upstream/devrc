# Handoff: mesh-blackouts-laptop-workbench — 2026-09-30

## Run this first — the index, one command
```bash
$DEVRC/scripts/cairn-ops/read.sh recall --repo "$DEVRC"
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. Non-blocking: if it exits non-zero,
print the stderr line and carry on. See also the `nebula-mesh` entry in that store.

## Goal
Explain and fix the episodic UDP blackouts between the laptop and the workbench: both
overlays (nebula + tailscale) lose IDENTICAL packets in the same second, while every ICMP
control on the same paths stays clean. **Split out of `handoff-laptop-airvpn-tunnel.md` on
2026-09-30** — that arc CLOSED (its tunnel closing-condition was met) while this one did not;
they had accumulated in one document and pushed it to its 65,536 B ceiling.
- **closing-condition:** `check` — a mechanism that PREDICTS the lockstep, confirmed by a
  measurement that would have come out differently had the mechanism been false; plus
  `bash <scratchpad>/probe2.sh` run twice showing `home-workbench` at 0% alongside
  `home-gateway`. 🔴 Every mechanism proposed so far has been eliminated; an explanation that
  merely coexists with the lockstep does NOT satisfy this line.

## State now
- Split out of the tunnel arc and merged to `main` as `ad437b54` (PR #1941). The tunnel arc is CLOSED; this one is not.
- Watcher: **flap-watch v3, pid `2309463`** (`ppid=1`). Resolve from `/proc/<pid>/cmdline`, never `pgrep -f`, never the pid file alone.
- 🔴 **Every candidate mechanism is ELIMINATED** — the `ip rule` pin, nebula riding the tailscale subnet route, the homelab node as a shared hop, the workbench host, the laptop's uplink, and the home site. The lockstep (170 of 172 episodes identical) survived all of them.
- 🔴 **The `ip rule` 5150 pin is NOT APPLIED** since the 2026-09-27 15:33 reboot, and is not the lever: it targeted `192.168.50.94`, which measures 0% in every round WITHOUT it. An `ip rule` is kernel state a reboot reverts silently.
- 🔴 Sudoers: on the laptop NOPASSWD covers `airvpn-sudo`, the whole `tailscale` binary, `systemctl restart|start|stop tailscaled` / `restart nebula@mesh`. **`ip`, `nft`, `tcpdump` are NOT passwordless**; `sudo -n` fails entirely on the workbench. This bounds every remedy an agent can apply.
- **Arrival probe built and controlled** at `<scratchpad>/agent-arrival-probe/`; `wb-sensor.sh` is STAGED at `/var/tmp/wb-sensor.sh` on the workbench and needs one operator `sudo` to run.
- ⚠ Leftover root-owned probe files on the workbench: `sudo rm -rf /var/tmp/arrival-probe /var/tmp/wb-sensor.sh` when done.

## Open investigations — live diagnosis state
### 🔴 The "two internet sources, one router" comparison is STRUCTURALLY CONFOUNDED — do not spend a session on it
- as-of: 2026-09-30
- 🔴 **This retires the previous rank 1 of this document.** It read "capture at the workbench with the laptop and the far box driving concurrently and compare". That comparison cannot be made.
- **Observed (with values):** the far box (`10.42.0.20`, `diffsona`) has **no tailscale installed at all**, and its only route to the workbench's LAN address is `via 172.31.1.1 dev eth0` — i.e. out to the internet, where the workbench is behind home NAT and unreachable. Its sole working path to the workbench is **nebula**, and it is simultaneously the nebula **relay** (`10.42.0.2`, on `nebula0`, same machine). `via: measurement`
- **Ruled out — that a different probe design fixes it.** The confound is that the only available second vantage IS the relay; no re-run separates them. A genuinely independent arm needs a FOURTH host, or tailscale installed on the far box. `via: measurement`
- **Leading hypothesis:** none. Superseded by the next-probe below, which needs no second vantage.
- **Next probe:** the inbound-vs-return question in rank 1 — it uses one source and is unconfounded.
## Open investigations — live diagnosis state
<!-- as-of: 2026-09-21 -->
### Gateway IPv6-remote noise — "listener is IPv4, but writing to IPv6 remote" (homelab-gateway)
- as-of: 2026-09-21
- **Symptom + exact repro:** `journalctl -u 'nebula@mesh'` on the laptop shows, on first contact with peer `homelab-gateway` (vpnAddrs 10.42.0.10), `level=error msg="Failed to write outgoing packet" error="listener is IPv4, but writing to IPv6 remote" udpAddr="[fda3:e207:b5c1:…]:51774"` (and fded:984d:43ce:…). Harmless in effect: nebula continues on the v4 remotes (handshakes complete; ssh to 10.42.0.30 works).
- **Observed (with values):** counts by hour 2026-09-21: 18:00–20:00 = **0**; 20–21 = 4; 21–22 = 10. After the #1848/#1849 fix, the up window at 22:18 produced exactly 4 more lines, ALL of this IPv6 shape for homelab-gateway — the routing-regression errors are gone.
- **Ruled out:** the AirVPN tunnel/killswitch as the CAUSE of these lines — `via: measurement` (the lines are v6-listener vs v6-remote, fired on first contact with a peer, and persist with the tunnel down; the tunnel-only errors were a SEPARATE mechanism, fixed by the uid pin).
- **Leading hypothesis:** the laptop's nebula listener is IPv4-only while the gateway advertises IPv6 remotes (its LAN ULA / k8s-node v6 addrs); nebula logs per attempt and falls through to v4. Config-side cleanup on the GATEWAY (advertise v4 only, or give the laptop a v6 listener) — lives in the `homelab-talos` repo, not devrc.
- **Next probe:** `rg -i 'listen|preferred_ranges|advertised' ~/workspace/homelab-talos -g '*.yml' -g '*.yaml' -g '*.conf'` for the gateway's nebula config (find where its `listen`/`advertised_addrs` are set) and check whether other peers (workbench) log the same lines.

### Nebula mesh flaps: "connection keeps hanging and recovering" — TWO mechanisms, one FIXED, one NOT NEBULA'S
- 🔴 **EVICTED 2026-09-30 to `claudedocs/archive/handoff-laptop-airvpn-tunnel-2026-09-30.md`** — superseded 2026-09-30: the `ip rule` pin it turns on is not the lever (the peer it targeted measures 0% without it), and its residual-flap evidence is replaced by the 9-hour dataset block below. Read it there before re-deriving anything; the eliminations it records still stand.


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


### Mesh blackouts laptop↔home (UDP-only) — RECURRENCE CONFIRMED 2026-09-30
- 🔴 **EVICTED 2026-09-30 to `claudedocs/archive/handoff-laptop-airvpn-tunnel-2026-09-30.md`** — superseded 2026-09-30 by the 9-hour dataset block below, which measures the same fault at 173 episodes instead of one. Read it there before re-deriving anything; the eliminations it records still stand.


### 🔴 SUPERSEDES "only traffic to HOME dies" — the fault is ONE WIRE FLOW (laptop↔workbench), not the path to home
- as-of: 2026-09-30
- 🔴 **Retires the framing in "Mesh blackouts laptop↔home (UDP-only) — RECURRENCE CONFIRMED 2026-09-30" and in "THREE vantages put the fault on the PATH, not at either end".** Their *eliminations* stand (laptop link, home router/NAT, forward+return IP path — all still ruled out). What is refuted is the conclusion drawn from them: "only traffic to HOME dies" and "the fault is on the PATH". 🔴 **Their shared `Next probe` — "decide the permanent `ip rule` pin form vs a tailscale route exclusion" — is now a WRONG INSTRUCTION as written**, because it presumes the pin's target address is the right one. It is not; see below.
- **Symptom + exact repro:** `bash <scratchpad>/probe2.sh` — warm-up 6 pkt to each target (discarded), then 100 pkt @0.5 s **concurrently** to four targets: workbench `10.42.0.30` (home), homelab gateway `10.42.0.10` (**also home**), Hetzner prod-gw `10.42.0.20` (not home), local default gw (control).
- **Observed (with values), two rounds:**

  | target | round A | round B |
  |---|---|---|
  | home-workbench `10.42.0.30` | 0% | **16%, one contiguous 16-pkt run (8 s)** |
  | home-gateway `10.42.0.10` | 0% | **0%** |
  | non-home Hetzner `10.42.0.20` | 0% | 0% |
  | control local gw | 0% | 0% |

  Two hosts on the **same home LAN**, reached over the **same overlay** across the **same internet path**, in the **same 50 s window**: one loses a contiguous 8-second run, the other is flawless. An earlier un-warmed run showed the same asymmetry (workbench 11.67%/run 14, gateway 0%). `via: measurement`
- **Ruled out — "only traffic to HOME dies" / a bad middle segment on the path to home.** The homelab gateway IS at home and is 0% in the same window that the workbench loses 16%. A path fault common to the home location cannot be selective between two hosts behind it. `via: measurement`
- **Observed — the mechanism, confirmed on the WORKBENCH, not inferred here.** `journalctl -u nebula@mesh` on `10.42.0.30`, last 24 h, counting the source address of `certName=zach-laptop` handshakes: **5 arrive from the laptop's TAILSCALE address (100.64/10), 1 from a public address.** So nebula-to-workbench is being routed into `tailscale0` and encapsulated inside the tailscale flow. `ip route get` confirms **both** `192.168.50.94` and `192.168.50.250` resolve `dev tailscale0 table 52`. `tailscale ping` to the workbench reports **`direct <endpoint>:41641`, 138 ms** — a single public UDP flow. `via: measurement`
- **Leading hypothesis (fits every observation, one step still untested):** nebula's packets to the workbench ride inside the laptop's **direct tailscale UDP:41641 flow**; when that one flow stalls, both overlays stall *in the same second* because they are literally the same packets on the wire — which is what the watcher has been recording all along (identical loss %, identical run length, same second, on both overlays). The homelab gateway escaped because nebula **roamed it onto the public endpoint** on its own (journal: `Host roamed … newAddr=<home-public-ip>:38552`, certName=homelab-gateway), so it never traverses tailscale. ⚠ **Untested:** that removing the tailscale path for nebula actually clears the loss. That is next-step 1.
- **Next probe:** apply a route exclusion for nebula's UDP:4242 only (fwmark → separate rule, so `kubectl`/LAN-over-tailscale survive), then re-run `probe2.sh` twice and confirm `home-workbench` joins `home-gateway` at 0%.


### Lighthouse mesh addresses still 100% loss — unchanged, and NOT explained by the above
- as-of: 2026-09-30
- **Observed (with values):** `10.42.0.1` and `10.42.0.2` → **100% loss**; `10.42.0.10`, `10.42.0.20`, `10.42.0.30` → 0% in the same sweep. Unchanged from 2026-09-23. `via: measurement`
- **Ruled out — that the wire-flow finding above accounts for it.** That block explains selective loss to the *workbench*; the lighthouses are 100%, permanently, and one of them is at Hetzner on the path measured clean. `via: measurement`
- **Leading hypothesis:** unchanged and untested — likely ICMP handling on the lighthouse side rather than reachability, since discovery demonstrably works throughout (every data peer resolves and connects).
- **Next probe:** from the far box (`ssh root@10.42.0.20`, works with `StrictHostKeyChecking=accept-new`), ping `10.42.0.1` and `10.42.0.2` — if they also fail from a host on a clean path, it is the lighthouses, not the laptop.


### 🔴 RETRACTION — "nebula rides the tailscale subnet route, and that is the fault" is REFUTED (it was asserted earlier TODAY, in this same doc)
- as-of: 2026-09-30
- 🔴 **This retracts the causal claim in the block "SUPERSEDES 'only traffic to HOME dies' — the fault is ONE WIRE FLOW", committed in `a9bcf992` a few hours earlier.** That block's *measurements* stand. Its **mechanism does not**, and its `Next probe` — apply a UDP:4242 route exclusion and expect `home-workbench` to reach 0% — is now a **wrong instruction**: the experiment was run and the loss did not move.
- **Symptom + exact repro:** `bash <scratchpad>/experiment.sh` — capture `tailscale debug prefs`, `sudo tailscale set --accept-routes=false`, settle 20 s, `probe2.sh` ×2, restore via an `EXIT` trap.
- **Observed (with values):** with the subnet route **gone** (`ip route get 192.168.50.250` → `via 192.168.1.1 dev wlp170s0`, no `tailscale0`): round A all four targets 0%; **round B `home-workbench` 13%, one contiguous 13-pkt run**, `home-gateway` 0%, Hetzner 0%, control 0%. Identical in shape to the 16%/run-16 measured *with* the route present. `via: measurement`
- **Ruled out — that the tailscale subnet route causes the loss.** Removing it changed nothing. `via: measurement`
- 🔴 **Ruled out — that nebula's DATA path was ever using the LAN address.** The disproof is in the same run: nebula logged **no handshakes at all** during the window, yet stayed at 0–13% instead of 100%. Had its data path been sending to `192.168.50.250:4242`, deleting that route would have blackholed it entirely. It was already on the workbench's public endpoint. `via: measurement`
- 🔴 **Ruled out — that the `from=` arrival address reports the data path.** It reports **handshake** packets only. "5 of 6 from `100.64/10`" was a true reading promoted to a claim it does not support; that promotion is what produced the refuted hypothesis. `via: measurement`
- **Leading hypothesis:** NONE that is load-bearing. What survives is the *observation set*, which is now well constrained and still unexplained: (a) the workbench flaps and the homelab gateway does not, though both sit behind one home public IP — so it is **per-flow or per-port, not per-site and not per-destination-host**; (b) nebula and tailscale to the workbench lose **identically, to the second**, though they are separate flows to that same home IP; (c) every ICMP control (local gw, last hop before home, far box) is 0% throughout; (d) a third vantage reaches the workbench at 0% while the laptop cannot. CGNAT per-flow state eviction still fits (a), (c) and (d) — but (b) is what it does not explain, and (b) is the observation that keeps surviving.
- **Next probe:** stop proposing remedies and characterise (b). The question is whether the nebula and tailscale flows to the workbench share a NAT binding — capture the laptop's actual source ports for both (needs root: `ss -unp` or `conntrack -L`, neither available to an agent here) and check whether the two flows die together because they share one mapping, or because something upstream drops both. Until (b) is explained, any "route around it" proposal is a guess — this session already shipped one and had to retract it inside an hour.


### 🔴 THE CENTRAL FACT, now on a 9-hour dataset: the two overlays fail in LOCKSTEP, packet-for-packet, while ICMP on the same paths is clean
- as-of: 2026-09-30
- 🔴 **This is the observation every remedy proposed in this arc has failed to engage with, and it is now the best-evidenced thing in the document.** Both remedies proposed so far (the `ip rule` pin; the tailscale route exclusion) were aimed at mechanisms that would break the coupling — and the coupling survived both. **Do not propose another remedy before explaining this block.**
- **Symptom + exact repro:** v3 watcher, 9 h unattended (`flap-watch3.sh`, pid `2309463`). On each ≥15% poll it fires a 60 s 5-way concurrent probe: nebula(UDP), tailscale(UDP), ICMP-local-gw, ICMP-last-hop-before-home, ICMP-far-box. Re-derive with the analysis one-liner in `How to verify`.
- **Observed (with values), 2026-09-30 06:09Z→15:14Z:**
  - **610 polls, 173 triggers (28.4% of polls).** `BADPARSE`: **0** — so the v2 defect that silently swallowed the worst polls is not operating.
  - **169 episodes measured both overlays. In 167 (98.8%) the loss% AND the longest run were IDENTICAL.** The only two exceptions differ by a single packet with the *same* run length (`14.1667%` vs `15.0%`, run 15; `7.5%` vs `8.33333%`, run 7).
  - **ICMP controls: 344 readings at 0%, 2 non-zero**, across the same episodes.
  - **Worst episode: `2026-09-30T10:16:16Z` — 99.1667% loss, a contiguous 118-packet run = 59 SECONDS**, both overlays identical. Next worst 65% / 40 pkt, then 63.3% / 37 pkt.
  - `via: measurement`
- 🔴 **Ruled out — the "6.5–20 s blackout" size this doc has carried since 09-24.** The real distribution reaches **59 s**, and episodes fire on 28% of polls. Any plan sized against 6.5–20 s is sized against the wrong fault. `via: measurement`
- **Ruled out — that the coupling is nebula riding tailscale.** See the retraction block: the subnet route was removed and the loss was unchanged. `via: measurement`
- **Leading hypothesis:** NONE load-bearing, and the lockstep is why. Two *separate* UDP flows — different protocols, different local ports, different remote ports — to one destination host lose the *same packets* for the *same duration*, while ICMP to that host's own last hop stays at 0%. That is very hard to explain by anything per-flow, which is what the CGNAT story requires. It points instead at something that drops UDP to that destination wholesale for seconds at a time, and at nothing this laptop or this repo controls. ⚠ Explicitly untested.
- **Next probe:** the discriminating question is whether the two flows share one NAT binding or are being dropped independently. Needs root on the laptop **during** an episode (the watcher's timestamps say when): `ss -unp` / `conntrack -L` for the two flows' source ports, and whether the mappings change across a blackout. Neither command is available to an agent here — operator-run.


### Both nebula peers egress to ONE shared underlay endpoint — so the differing segment is NOT on the internet path
- as-of: 2026-09-30
- 🔴 **Reframes the arc: it puts the 09-24 third-vantage exoneration of the home side back in play.** Everything here assumed the two home peers are reached independently. They are not — both are dominated by the same remote endpoint, so the segment where they differ lies BEYOND it, inside the home network, not on the carrier path probed all week.
- **Symptom + exact repro:** capture nebula's own LOCAL socket while driving exactly one peer; count OUTBOUND remote ports. 🔴 Filter on the local ephemeral port, NOT `:4242` (see gotcha).
  ```bash
  TD=$(nix-shell -p tcpdump --run 'command -v tcpdump'); P=51711   # re-derive: listen.port is 0
  sudo sh -c "timeout 12 $TD -i wlp170s0 -n -l 'udp port $P' > /tmp/c.txt 2>/dev/null & \
    sleep 1; ping -c 20 -i 0.4 -W 2 <peer> | tail -2; wait; \
    grep -oP '\.$P > [0-9.]+\.\K[0-9]+(?=:)' /tmp/c.txt | sort | uniq -c | sort -rn"
  ```
- **Observed, 2026-09-30 ~15:50Z (both peers 0% loss at the time):** driving workbench `10.42.0.30` → **`:38552` ×27**, `:4242` ×4, `:40155` ×2. Driving gateway `10.42.0.10` → **`:38552` ×80**, `:49527` ×7, `:4242` ×4, `:40155` ×2. `:38552` dominates for BOTH. `via: measurement`
- ⚠ **Not airtight:** the runs were not background-controlled (93 vs 33 outbound packets), so it is not *proven* the workbench's 20 pings rode `:38552` — only that it is the sole destination with the volume to contain them. Quiesce and subtract a no-ping baseline for a clean version.
- **Ruled out — that nebula reaches peers on `:4242`.** `listen.port: 0` ⇒ ONE ephemeral local socket; each peer is reached at the address:port the lighthouse observed (NAT-mapped for a peer behind NAT). The only `:4242` destinations are the **two lighthouses** — the config's only public `static_host_map` entries (verified: 2, both port 4242). `via: measurement`
- **Leading hypothesis — NOT adopted:** consistent with the workbench being reached *through* the gateway, making the failing segment a home-network hop. ⚠ NOT established: `:38552` was tied to the gateway by a 2026-09-29 21:53 journal line and NAT mappings change; that attribution was never re-derived.
- 🔴 **What it still does not explain, and what has outlived every hypothesis here:** tailscale reaches the workbench **directly** (`tailscale ping` → `direct …:41641`), so a nebula-only relay hop cannot account for both overlays losing identical packets in the same second in 167 of 169 episodes. **Any next mechanism must predict the lockstep or it is not the mechanism.**
- **Next probe, in order:** (1) **re-run the 09-24 third-vantage measurement** (far box → `10.42.0.30` concurrent with laptop → same, epochs recorded) — it is the only result exonerating the home side and it is six days old; (2) re-derive which peer `:38552` is *now*, from live state not a stale journal line; (3) only then capture during a live episode.


### ✅ THE 09-24 THIRD-VANTAGE RESULT REPRODUCES under proper controls — the home side stays exonerated, and the fault is isolated to the laptop↔workbench PAIRING
- as-of: 2026-09-30
- 🔴 **This was the single result keeping the home network out of scope, it was six days old, and it now holds under a re-run with the two controls the original lacked.** Three suspects die on one round, each on a CONCURRENT control rather than a separate run.
- **Symptom + exact repro:** `bash <scratchpad>/third-vantage.sh` — warm-up, then the far box (`root@10.42.0.20`) pings `10.42.0.30` for 140 pkt while the laptop pings `10.42.0.30` AND `10.42.0.10` for 100 pkt each, all concurrent; both clocks recorded. Up to 6 rounds, stopping on the first round where the laptop sees ≥10% loss — a quiet round proves nothing.
- **Observed (with values), round 1, 2026-09-30:**

  | vantage | result |
  |---|---|
  | laptop → workbench `10.42.0.30` | **13 lost / 100**, one contiguous 13-pkt run |
  | laptop → gateway `10.42.0.10` (control) | **0 / 100** |
  | far box → workbench `10.42.0.30` | **0 lost / 140** |

  Windows from the packets' own timestamps: far `1790784753.6..1790784823.3` **contains** laptop `1790784759.8..1790784809.5`. The laptop's blackout was **seqs 40–52, epoch 1790784778.9 → 1790784785.9 (7.1 s)**; inside that exact window the far box delivered **14 of 14**. `via: measurement`
- **Ruled out — the workbench HOST.** It answered a third vantage flawlessly during the precise 7.1 s it was dropping the laptop's packets. `via: measurement`
- **Ruled out — the laptop's uplink / its link generally.** The gateway control was 0% in the same window, on the same wifi, same router, same ISP. `via: measurement`
- **Ruled out — the home site as a whole.** The gateway is at that site and shares the dominant underlay endpoint; untouched. `via: measurement`
- **Leading hypothesis:** the fault belongs to the laptop↔workbench pairing specifically — not to either endpoint, not to the site, not to the laptop's access network. Combined with the shared-underlay-endpoint block, the failing segment is whatever is unique to that pairing beyond the shared endpoint. ⚠ Untested.
- 🔴 **STILL does not explain the lockstep**, which remains the fact no hypothesis in this arc has predicted: both overlays lose identical packets in the same second in 167 of 169 episodes. A candidate — that both overlays funnel through the homelab node to reach the workbench, since that node also hosts the tailscale subnet router — is contradicted by `tailscale ping` reporting the workbench as `direct`, and is recorded as a question, NOT a finding. Three hypotheses were promoted on this kind of resemblance today and all three were retracted.
- **Next probe:** determine whether the laptop's nebula path to the workbench traverses the homelab node. From the far box, `ssh root@10.42.0.20` and re-run the pairing matrix with the gateway as the *source* if possible; or on the workbench, compare the arrival source address of laptop traffic against far-box traffic during an episode.


### 🔴 QUALIFIES the third-vantage block above — the "far box" is the laptop's own nebula RELAY, so it is NOT an independent vantage for nebula
- as-of: 2026-09-30
- 🔴 **Landed and corrected the same day.** The block above concludes "the fault is isolated to the laptop↔workbench PAIRING". That conclusion is **not supported**, because it assumed `10.42.0.20` observes the workbench over a path independent of the laptop's. It does not.
- **Observed (with values):** `ssh root@10.42.0.20` → `hostname: diffsona`, and `ip -4 -br addr` shows **both** `nebula1 10.42.0.20/24` **and** `nebula0 10.42.0.2/24` — one machine, two nebula interfaces. The laptop's RUNNING config (`/nix/store/…-nebula-config-mesh.yml`) carries `use_relays: true` with `10.42.0.2` in `relays:`. So the laptop's nebula traffic may transit the very box being used as the control. `via: measurement`
- **What SURVIVES from the block above — unchanged:**
  - **The workbench HOST is innocent.** It delivered 14/14 to the far box during the laptop's exact 7.1 s blackout. That is a fact about the host, independent of which path the far box took.
  - **The laptop's uplink is innocent.** The `laptop → gateway` control read 0% in the same window and is measured wholly on the laptop side.
- **What is WITHDRAWN:** "isolated to the laptop↔workbench pairing". If the laptop relays via `10.42.0.2`, then "far box → workbench is clean" localises the fault to the **laptop → far-box leg**, which is a different claim. The measurement as run cannot separate the two.
- 🔴 **Ruled out — that this can be fixed by re-running the same probe.** The confound is structural: for nebula there is no third vantage, because the only candidate IS the relay. A genuinely independent arm must be **tailscale-only** (the far box has no tailscale installed at all) or a fourth host. `via: measurement`
- **Leading hypothesis:** unchanged and still none. The lockstep remains unexplained and is now the only thing that has survived every round.
- **Next probe:** run the arrival probe (below) — its workbench-side `(srcMAC, srcIP)` discriminator does not depend on any vantage being independent, which is exactly why it is the right instrument now.


### Arrival probe — BUILT and controlled, NOT yet run to conclusion
- as-of: 2026-09-30
- **What it does:** each arm pings over its overlay with a distinct inner ICMP size, so each lands on a distinct *outer* UDP length; a workbench-side root sensor captures outer headers with `-e`, and the verdict is the **(srcMAC, srcIP)** each arm arrives with. Arm→length is **learned** by calibration, never assumed. Episode windows come from the packets' own `ping -D` stamps. Episode-triggered: a quiet run reports `NO EPISODE OBSERVED` and suppresses the table.
- **Where:** `<scratchpad>/agent-arrival-probe/arrival-probe.sh` (laptop controller: `preflight|controls|sensor-cmd|calibrate|watch|analyze`) and `wb-sensor.sh` (workbench root sensor, operator-run). `./arrival-probe.sh sensor-cmd` prints the operator sequence.
- **Controls that PASSED (with numbers):** capture positive — **60 packets at outer length 1141**, 0 dropped by kernel; capture negative — **0** hits for a never-sent size *in the same pcap* (the pair is the evidence, not either alone); a deliberately-wrong BPF → `captured=0`, labelled as the shape of an instrument failure; address-scrubber positive/negative and an IPv6 arm; `analyze` exercised on a real capture, on no-episodes, and on an empty pcap.
- 🔴 **NOT validated, and this is the gap that matters:** `wb-sensor.sh` **has never run as root on the workbench** (no root there). It was validated as root on the far box with port overrides, plus an unprivileged dry-run of its discovery logic on the workbench. **No laptop-arm packet has ever been observed arriving at the workbench**, so the verdict path is unexercised. `TS_PORTS_OVERRIDE=41641` is the escape hatch if the `/proc/<pid>/fd` inode→port mapping fails.
- **Reading it — outcomes that mean INSTRUMENT BROKEN, not a finding:** sensor reports nebula port `4242` (you are watching lighthouses); rotation self-check FAIL or a single pcap (capture overwriting itself); nonzero "packets dropped by kernel" (counts unreliable); `total captured packets: 0`; any arm `UNLEARNED` (that vantage never saw it — row skipped, never fabricated); `distinct (srcMAC,srcIP) tokens < 2` ("all arms identical" is unproven). `NO EPISODE OBSERVED` is a null about the run, not the network.
- **The finding, if it comes:** a *learned* arm with ≥2 distinct sources present, showing the laptop arms on the homelab node's MAC while the far-box arm shows the router's.


### ✅ REFUTED — "both overlays funnel through the homelab node" was the last standing candidate for the lockstep, and it is measured FALSE
- as-of: 2026-09-30
- 🔴 **Read this before proposing the homelab node / tailscale subnet-router as a shared hop. It was the only mechanism left that predicted the lockstep, and packet capture at the workbench kills it.**
- **Symptom + exact repro:** run the arrival probe to capture at the workbench, then attribute INBOUND packets by source MAC directly from the pcap — **no privileges needed to read a pcap**:
  ```bash
  tcpdump -r <wb.pcap> -n -e "ip dst 192.168.50.250 and udp port <port>" \
    | grep -oP '^\S+ \K[0-9a-f:]{17}' | sort | uniq -c | sort -rn
  ```
  MAC legend: sensor's own `sensor-meta-*.txt` (`ip neigh`) — `192.168.50.1` router, `192.168.50.94` homelab node.
- **Observed (with values), 2026-09-30, capture of 94,788 packets, 0 dropped by kernel, 3 episodes inside it:**

  | overlay, inbound to workbench | via router | via homelab node |
  |---|---|---|
  | tailscale `:41641` | **14,021** | **0** |
  | nebula `:49527` | 628 | 61 |

  Not ONE tailscale packet arrived via the homelab node. The 61 nebula packets from it are the gateway peer's own on-LAN traffic (`10.42.0.10` shares that LAN with the workbench). `via: measurement`
- **Ruled out — that the laptop's two overlays share the homelab node as a hop.** Both arrive from the router, i.e. off the internet. `via: measurement`
- **Leading hypothesis:** none. What survives is that both overlays share the ROUTER hop into the workbench — but the far box reaches the workbench through that same router and was clean during a laptop blackout, so "the router drops inbound UDP" does not fit either. **The lockstep is now unexplained with every candidate mechanism eliminated.**
- **Next probe:** the surviving asymmetry is between two INTERNET SOURCES arriving via the same router — the laptop (loses) and the far box (clean). Capture at the workbench with both driving concurrently and compare their inbound source addresses and arrival gaps inside one episode window. The pcap tooling for this now exists and is controlled.



## Next steps (ranked)
1. **Does the laptop's traffic ARRIVE at the workbench during a blackout?** Run the workbench sensor (operator `sudo`, one command — `ssh -t zach@10.42.0.30 'sudo TCPDUMP=<store-path>/bin/tcpdump /var/tmp/wb-sensor.sh'`), drive the laptop over tailscale with `ping -D`, then count inbound packets from the laptop bucketed per second and aligned to the laptop's blackout seconds taken from its OWN packet timestamps. 🔴 Analyse with `tcpdump -r` on the pulled pcap — that needs NO privileges; only `-i` capture does. **Arrivals continue ⇒ the loss is on the RETURN leg and every hypothesis so far has been looking the wrong way. Arrivals stop ⇒ inbound.** Neither answer has ever been measured; every mechanism proposed to date silently assumed inbound.
   forcing: incident — 173 episodes in 9 hours on 2026-09-30, worst 99.2% loss over a contiguous 59 s, 170 of 172 episodes with both overlays identical
2. **Check the workbench's own NIC/driver state**, which no round has examined: `ethtool -S eth0` rx drops/overruns across an episode, and any offload or power-saving setting the gateway lacks.
   forcing: incident — the same recurrence; the workbench is the only host that loses

## Gotchas / decisions / dead-ends
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
- `kubectl get pods -A` on `$KC_HOMELAB` TIMES OUT (rc 124, >90 s) — use `kubectl get pods -n <ns>` or a `--field-selector` instead; `kubectl get ds/deploy/cm -A` works fine.
- `env KUBECONFIG=... kubectl ...` — bare `KUBECONFIG=... kubectl` in a compound command parses as a command name (timeout: 'failed to run command'); must use `env`.
- The gateway/lighthouse nebula pods are distroless — `kubectl exec ... -- ping/ls/nebula` all fail (`executable file not found`); logs are the only window.
- The two pods share one node (`talos-jkj-deb` / 192.168.50.94) and the lighthouse config's static_host_map only carries `10.42.0.2 → <hetzner-lighthouse-ip>:4242`; the gateway config's comment says LAN IP is used deliberately ("NAT hairpinning won't work") — consistent with the hairpin half of the flap.
- `sudo -n` over ssh to the workbench fails (password required) — sudo-touching steps are operator-run, hand over the exact command.
- (carried, WIDENED) DO NOT put ANY routable address of ours back in this doc — not just the home public IP. Use `<home-public-ip>` / `<laptop-wan-ip>` / `<hetzner-lighthouse-ip>` or a runtime shell variable. On 2026-09-23 it recurred with three literals, one of them the value scrubbed 24 h earlier.

- 🔴 **A commit pushed to a branch AFTER its PR squash-merged is stranded and invisible to every gate.** Measured this session: `7511cbb8` landed on #1861's branch six minutes after #1861 merged as `5834b4c5`, so it was never in `main` and never in the merged PR. It was recovered as #1866 only because someone looked. **Verify a squash landed by CONTENT, never by ancestry** — `git merge-base --is-ancestor` is false after every squash, forever.
- 🔴 **A line-based grep over a wrapped sentence returns a confident zero.** Verifying #1866's merge, one sentinel read ABSENT because the sentence wraps; normalising whitespace found it. Control the two forms against each other before believing an absence.
- 🔴 **A 20-second ping sample cannot size an episodic fault.** A 40-packet run read 65% loss; a concurrent 120-packet run minutes later read 16%, same mechanism. The first reading nearly got reported as a degradation.
- ⚠ **`pgrep -c -f <pattern>` counts ITSELF.** It reported two `flap-watch.sh` processes; resolving PIDs and reading `/proc/<pid>/cwd` found one. An audit round filed the phantom as a leaked process. Resolve PIDs; never trust the count.
- ⚠ **An auditor's concrete numbers need the same re-derivation as anyone's.** Round 0 on #1869 reported the workbench's nebula inbound as two groups; measured, it has three (`admin`, `homelab`, `lighthouse`) — the laptop also has three, differing only in the third (`workbench`). Its *point* was right and its *number* was not; the comment now carries no count at all.

- (carried) 🔴 NO routable address of ours in this doc — `<home-public-ip>` / `<laptop-wan-ip>` / `<hetzner-lighthouse-ip>` placeholders or runtime vars only; it recurred once already within 24 h.
- (carried) The flap watcher is a plain background process, NOT a unit — suspend/reboot kills it; the old one died silently after 3.5 days and lost 09-27→09-30 coverage. Re-check `ps -p $(cat flap-watch.pid)` at every resume.
- The old watcher's loss% fields are GARBAGE (parse bug) — read only its `longest_run` and trigger lines; v2 is fixed.
- 🔴 **The v2 watcher under-reported the WORST episodes.** `loss_pct()` had no `tail -1`, so any ping output matching `% packet loss` twice returned a MULTI-LINE value; the caller's `[ "$p" -ge N ]` then errored and fell through to the else branch, logging it as **quiet**. Observed at `2026-09-30T06:00:29Z` as `poll: 100\n100% quiet` — a 100%-loss poll that never fired the discriminator. Reproduced with a `ping` shim (v2 → "quiet", v3 → TRIGGER).
- 🔴 **The first version of that fix contained an unreachable guard.** `grep -oP '(\d+(?:\.\d+)?)(?=% packet loss)'` on `1.2.3% packet loss` matches the **tail** `2.3` — a plausible-but-wrong number, so the `BADPARSE` branch could never run. The lookbehind `(?<![\d.])` is load-bearing; `BADPARSE` was then proved reachable. Also: the caller now compares with `awk`, because `[ -ge ]` is integer-only and the log already contains `10.8333%` readings it could never have evaluated.
- 🔴 **A 4-way ping probe needs a WARM-UP or its first seconds read as a blackout.** An un-warmed run lost exactly seqs 1–8 — tunnel re-establishment, not an episode. `probe2.sh` sends 6 discarded packets to each target first. Any probe that starts cold will manufacture a leading blackout.
- 🔴 **`pgrep -f 'flap-watch3.sh'` matched the Claude Code wrapper shell, not the watcher** — it wrote the wrong PID into `flap-watch.pid`. Resolve by scanning `pgrep -x bash` and reading `/proc/<pid>/cmdline`. This is the third time a `-f` pattern has misfired in this arc.
- **The homelab gateway is clean because nebula ROAMED it to the public endpoint, not because of the pin** — the pin has been absent for three days and `10.42.0.10` measured 0% twice. Do not read its health as evidence the pin works.
- ⚠ **A 120 s probe does not fit a 2-minute foreground command budget** — it gets truncated and silently yields short files. Keep concurrent probes at 100 pkt @0.5 s (50 s), or background them properly.

- 🔴 **THIS SESSION SHIPPED A CONFIDENT MECHANISM AND RETRACTED IT WITHIN THE HOUR — the tell was there at write time and was read past.** The evidence was `from=` addresses on **handshake** log lines; the claim was about the **data path**. Those are different packet types, and nothing measured connected them. **A log field names the packets that produced it and nothing else.** The cheap disproof existed before the commit and cost one command: had nebula's data path used the LAN address, removing that route would have given 100% loss, not 13%.
- 🔴 **`sudo -n -l` is worth reading BEFORE designing a remedy.** Two rounds of this arc specified `ip rule`/fwmark fixes that no agent on this host can apply — `ip` is not in the sudoers list, `tailscale` (entirely) is. Design to the privileges that exist, or hand the step over explicitly.
- **A routing experiment must carry its own restore.** `experiment.sh` puts the revert in an `EXIT`/`INT`/`TERM` trap, so an interrupt or a probe failure still restores `accept-routes`. Verify the restore by re-reading `tailscale debug prefs` (`RouteAll`) **and** `ip route get`, not by assuming the command worked.
- **`tailscale set` changes only the pref you name; `tailscale up` re-asserts a whole prefs set.** Use `set` for a single-pref experiment.
- 🔴 **The v2 watcher under-reported the WORST episodes.** `loss_pct()` had no `tail -1`, so ping output matching `% packet loss` twice returned a MULTI-LINE value; `[ "$p" -ge N ]` then errored and fell to the else branch, logging it **quiet**. Seen at `2026-09-30T06:00:29Z` as `poll: 100\n100% quiet`. Reproduced with a `ping` shim. Old log "quiet" lines are not trustworthy at the high end.
- ⚠ **The ping output producing that double match was NOT reproducible** — v3 fixes the CLASS (shape-validate, fail LOUD via `BADPARSE`) rather than a guessed cause. Do not write up a mechanism for it.
- 🔴 **The first version of that fix contained an unreachable guard** — `1.2.3% packet loss` matched the tail `2.3`, so `BADPARSE` could never fire. The lookbehind `(?<![\d.])` is load-bearing; reachability was then proved.
- 🔴 **`claim-work --release` is per-WORKTREE**: claiming from the base clone and releasing from elsewhere is refused. Release from the same checkout you claimed in.

- 🔴 **A PID resolved immediately after `setsid` can be a transient — this arc recorded a dead one twice in one day.** `2309522` was written into two commits as "the running watcher"; the real process was `2309463` (`ppid=1`). Re-resolve from `/proc/<pid>/cmdline` at the moment you need it, and treat both the pid file and any number in this doc as a hint.
- 🔴 **LET THE INSTRUMENT RUN BEFORE THEORISING — 9 hours of it changed the question.** Every hypothesis in this arc was built on single ad-hoc probes of 100–300 packets. The unattended dataset showed the blackouts reach **59 s** (not 6.5–20 s), fire on **28%** of polls, and couple the two overlays in **98.8%** of episodes. None of that was visible in the hand-run samples the remedies were designed against.
- ⚠ **`grep -c TRIGGER` over the whole watcher log double-counts across watcher generations** — the log is appended across the v2→v3 handover. Slice from the `WATCHER HANDOVER` marker (`awk '/WATCHER HANDOVER/{f=1} f'`) before quoting any count.

- 🔴 **NEBULA DOES NOT TALK TO PEERS ON `:4242` — filtering on it watches the LIGHTHOUSES.** `listen.port: 0` ⇒ one ephemeral local socket (`51711`); NATed peers are reached on their NAT-mapped port. A `udp port 4242` capture returns lighthouse keepalives while looking exactly like peer traffic. It produced a confident wrong reading: a conntrack flow `[ASSURED]` with a stable source port through a 75% episode was reported as "the peer's flow survived, the local stack is innocent" — it was a **lighthouse** flow; the peer's data path was never observed. **Filter on the LOCAL port.**
- 🔴 **FIVE INSTRUMENT FAILURES IN ONE SESSION, EACH PRODUCING OUTPUT THAT LOOKED LIKE A FINDING.** (1) `pgrep -f` matched the agent's own wrapper shell → a dead PID in two commits. (2) A conntrack-timeout differential assumed idle flows decay; keepalives pin them near the 120 s max, so every peer read `NONE` — i.e. "all peers relayed". (3) `tcpdump -G` without `-w` is fatal, and with stderr to `/dev/null` it reported a clean-looking 0 packets. (4) A positive control tested `wc -l > 0` instead of "a packet matched", passing on a line tcpdump called `0 packets captured`. (5) The `:4242` filter. 🔴 **The pattern: every one was a ZERO or a UNIFORM result — a uniform result across all arms is the tell that the INSTRUMENT failed, not the system.** Never silence stderr on a capture tool; make a positive control assert the THING, never a proxy.
- ⚠ **`tcpdump` arg errors fire BEFORE the permission check** — reaching "You don't have permission" proves the flags parsed. The FILTER cannot be validated that way (a malformed filter gives the same error), so filter correctness rests on the run's own positive control.
- 🔴 **VERIFY WINDOW CONTAINMENT FROM THE PACKETS' OWN TIMESTAMPS, not from a computed end time.** `third-vantage.sh` computed `F_END = F_START + count/2` and declared `NOT CONTAINED` on windows that plainly nest (`759..809` inside `753..823`) — instrument failure #6 of this session. The reliable derivation is `min`/`max` of the received packets' `ping -D` stamps on each side, which also lets you align the far box's packets against the laptop's blackout SECONDS rather than against the whole run. ⚠ This one failed SAFE — it refused a real result rather than manufacturing one — which is the direction a guard should fail.
- ⚠ **A `nohup … &` launched from an agent Bash call dies when the call returns; `setsid` survives.** Cost one silently truncated measurement run here and one earlier in the session (a 120 s probe that produced short files).

- 🔴 **`/etc/nebula/config.yaml` ON THE LAPTOP IS A DECOY — it says `port: 4242` and the running process does not use it.** The live config is a nix-store path with `listen.port: 0`, `use_relays: true`, `relays: [10.42.0.2]`; laptop mesh addr is `10.42.0.100`, local port currently `51711` (uid 991). Read the config the RUNNING unit was given (`systemctl cat nebula@mesh`), never the conventional path. This is the same family as the `:4242` filter error: the obvious answer is wrong and looks right.
- 🔴 **`sudo -n -l <cmd>` IS A BROKEN INSTRUMENT ON THIS LAPTOP.** A `(ALL : ALL) SETENV: ALL` line makes it report EVERY command as permitted, so it cannot tell you what is passwordless. Read the NOPASSWD lines in bare `sudo -n -l` output, or actually invoke the command and see whether it prompts.
- ⚠ **`pgrep -x tailscaled` finds nothing on NixOS** — the wrapper makes `comm` = `.tailscaled-wra`. Use the unit's `MainPID`.
- ⚠ **`tcpdump -G` makes `-w` a strftime TEMPLATE**: `%03d` expands to day-of-month, so every rotation overwrites one file while tcpdump still reports packets captured. `-G`+`-W` also does not wrap — it STOPS, so the capture is bounded, not a ring. And tcpdump drops privileges after the first rotation file (`-Z root` segfaulted on 4.99.4 under `-G`; a mode-1777 output dir is the workaround).
- ⚠ **The homelab node is Talos — no ssh, no node-side capture.** The workbench-side `(srcMAC, srcIP)` is the only available discriminator for which path laptop traffic took.

- 🔴 **READING A PCAP NEEDS NO PRIVILEGES — capture is the only privileged half.** Three rounds were spent handing capture-and-analyse scripts to the operator when only the `tcpdump -i` step needed root; `tcpdump -r` on the pulled file is an ordinary user operation. Pull the pcap, analyse it locally, iterate freely.
- 🔴 **An arrival analysis MUST filter by direction, or it counts the capture host's OWN EGRESS as arrivals.** The probe's table reported ~1,300–1,900 "arrivals" per episode whose source was `192.168.50.250` — the workbench itself. Add `ip dst <capture host>` to the filter.
- 🔴 **A MAC address looks like an IPv6 address to an address scrubber.** The probe's own scrubber rewrote every `srcMAC` in its analysis output to an `ip6x:` token, destroying the primary discriminator, while the legend in the sensor meta file survived. If a scrubber is in the path, verify the field you are about to reason over is still readable.
- ⚠ **Size-tagging arms by inner ICMP size worked for tailscale and NOT for nebula** (`LAP_NEB`/`FAR_NEB` both `UNLEARNED` — no distinct outer length appeared above the control slab). Do not assume a 1:1 inner→outer length mapping per overlay. The probe correctly refused to print rows for the unlearned arms rather than fabricate them.

- 🔴 **AN AD-HOC LEAK CHECK THAT ALLOWLISTS ITS OWN CANONICAL EXAMPLE SCANS CLEAN WHILE THE REAL GATE FAILS.** The throwaway scanner used all session excluded `1.1.1.1` as "benign", so it reported NONE on a doc that contained it, and `tekton/devrc-pytests` failed on exactly that literal (`test_no_unallowlisted_public_ip_literal_is_committed`). The repo's allowlist is keyed on `(relpath, value)`, so `1.1.1.1` being present-and-pinned in another file does NOT cover yours. **Run `scripts/tests/test_no_public_ips.py` itself; never a hand-rolled substitute.** Fixed by removing the literal, not by adding allowlist debt.
- ⚠ **`test_tmux_reply_agent.py` fails on a PRISTINE `origin/main` worktree (93 failures) on this host** — it touches live tmux. A full local suite therefore reports ~200 reds that are environmental. Control any local red against a pristine main worktree before attributing it to your branch; measured twice on 2026-09-30, once in each direction.
- 🔴 **Bringing the AirVPN tunnel UP does NOT change either overlay's egress** — `ip rule 500 uidrange 991-991` exempts nebula and `ip rule 5210 fwmark 0x80000` exempts tailscale, by design. Verified `uid 991 → wlp170s0`, `uid 1000 → airvpn`. A session proposed the tunnel as a way to test the laptop's outbound path; it cannot.


## How to verify
```bash
S=/tmp/claude-1000/-home-zach-workspace-devrc/765865a3-5a34-4368-9c27-c442fd52106c/scratchpad
for p in $(pgrep -x bash); do [ -r /proc/$p/cmdline ] && tr '\0' ' ' < /proc/$p/cmdline \
  | grep -q flap-watch3 && ps -o pid=,etime= -p $p; done        # watcher alive
awk '/WATCHER HANDOVER/{f=1} f' $S/flap-watch2.log > /tmp/v3.log  # v3's OWN record only
grep -c 'poll:' /tmp/v3.log; grep -c TRIGGER /tmp/v3.log; grep -c BADPARSE /tmp/v3.log
bash <scratchpad>/probe2.sh        # home-gateway must be 0%; home-workbench is the one to watch
sudo -n -l                         # `ip`/`nft`/`tcpdump` NOT granted — design remedies to this
```
Lockstep check (the central fact): parse `/tmp/v3.log` for paired `nebula(`/`tailscale(` loss+run
per episode and count exact matches — 167/169 on 2026-09-30, 170/172 including the probe run.
