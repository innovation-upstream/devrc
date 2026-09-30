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
- Branch / PR: devrc **PR #1937** (`handoff-laptop-airvpn-tunnel`), OPEN. Commits: `a9bcf992`, `9ab5dfd1`, `a58d97ec`, + this.
- No `clawgate-task:` field: `clawgate_handoff.sh resolve` → **5 (NOTHING RESOLVED)**; cannot distinguish "no task" from "wrong id".
- Watcher: **v3, pid `2309463`** (`ppid=1`; earlier commits recorded `2309522`, which was a transient and is dead). Resolve it from `/proc/<pid>/cmdline`, never `pgrep -f`, never the pid file alone.
- 🔴 `ip rule` 5150 pin **NOT applied** since the 2026-09-27 15:33 reboot — and not the lever: `10.42.0.10` measures 0% in every round without it.
- 🔴 Sudoers: NOPASSWD covers `airvpn-sudo`, the whole `tailscale` binary, `systemctl restart|start|stop tailscaled` / `restart nebula@mesh`. **`ip`/`nft` are NOT granted**, so no agent here can apply an `ip rule`/fwmark remedy. `tcpdump` is absent but `nix-shell -p tcpdump` supplies it.
- Deploy/verify: nothing deployed, nothing left changed. The one routing experiment (`accept-routes=false`) was reverted in-run via a trap; `RouteAll = True` re-confirmed.
- 🔴 CARRIED: mosh DOES NOT WORK YET — no host runs `mosh-server`; `programs.mosh.openFirewall` DEFAULTS TRUE (must be false or it opens 1001 UDP ports on every interface incl. WAN).

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

### mosh's stated benefit was WRONG, and the correction is smaller than the claim
- 🔴 **EVICTED 2026-09-30 to `claudedocs/archive/handoff-laptop-airvpn-tunnel-2026-09-30.md`** — CLOSED: the claim was settled and corrected; the corrected wording lives in `nix/pkgs/default.nix`. Read it there before re-deriving anything; the eliminations it records still stand.

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

## Next steps (ranked)
1. **Capture the two flows' NAT state during a live episode** (open investigation above) — operator-run, root, on the laptop; use the v3 watcher's episode timestamps to time it. This is DIAGNOSIS, not a remedy: this arc has now proposed two remedies and measurement killed both, and the lockstep observation engages neither.
   forcing: incident — 173 episodes in 9 hours on 2026-09-30, worst 99.2% loss over a contiguous 59 s, both overlays identical in 98.8% of 169 episodes
2. **Merge #1937 once its Tekton checks settle** — it carries the correction, the retraction and this dataset.
   forcing: incident — the same live recurrence; leaving it open leaves a refuted mechanism standing as the doc's leading hypothesis

## Defects (batched)
- Workbench stale `airvpn-updown` copy — operator, ON THE LAN: `sudo install -m0755 ~/workspace/devrc/scripts/airvpn-updown /etc/nixos/i3blocks-scripts/airvpn-updown`, pair with killswitch re-test per `claude/skills/bar/reference/airvpn.md`.
- Nebula gateway v6-remote noise (config in homelab-talos).
- `refresh-airvpn-servers --from-github` fallback rotted; re-run from a qBit-pod host and bake country_code.
- `i3status-airvpn` no-country-code fallback abbreviates full country NAME — one-line fix if ever seen again.
- The flap watcher is still a plain background process, not a unit — v3 is pid 2309522 and a suspend/reboot ends it silently, as v2's predecessor did (3 days of coverage lost).

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

- (carried) 🔴 NO routable address of ours in this doc — `<home-public-ip>` / `<laptop-wan-ip>` / `<hetzner-lighthouse-ip>` placeholders or runtime vars only; it recurred once already within 24 h.
- (carried) The flap watcher is a plain background process, NOT a unit — suspend/reboot kills it; the old one died silently after 3.5 days and lost 09-27→09-30 coverage. Re-check `ps -p $(cat flap-watch.pid)` at every resume.
- The old watcher's loss% fields are GARBAGE (parse bug) — read only its `longest_run` and trigger lines; v2 is fixed.
- (carried) A 20-s poll cannot size an episodic fault; the discriminator (120 pkt @0.5 s concurrent) is the reading.
- (carried) Laptop has NO systemd-resolved — never re-add `DNS` to the laptop wg conf.

- 🔴 **The v2 watcher under-reported the WORST episodes.** `loss_pct()` had no `tail -1`, so any ping output matching `% packet loss` twice returned a MULTI-LINE value; the caller's `[ "$p" -ge N ]` then errored and fell through to the else branch, logging it as **quiet**. Observed at `2026-09-30T06:00:29Z` as `poll: 100\n100% quiet` — a 100%-loss poll that never fired the discriminator. Reproduced with a `ping` shim (v2 → "quiet", v3 → TRIGGER).
- ⚠ **The ping output that produced the double match was NOT reproducible** — black-hole, unreachable-network and no-stats shapes all match exactly once. v3 therefore fixes the CLASS (validate the value's shape, fail LOUD) rather than a guessed cause, and says so in its own comment. Do not write up a mechanism for it; none was established.
- 🔴 **The first version of that fix contained an unreachable guard.** `grep -oP '(\d+(?:\.\d+)?)(?=% packet loss)'` on `1.2.3% packet loss` matches the **tail** `2.3` — a plausible-but-wrong number, so the `BADPARSE` branch could never run. The lookbehind `(?<![\d.])` is load-bearing; `BADPARSE` was then proved reachable. Also: the caller now compares with `awk`, because `[ -ge ]` is integer-only and the log already contains `10.8333%` readings it could never have evaluated.
- 🔴 **A 4-way ping probe needs a WARM-UP or its first seconds read as a blackout.** An un-warmed run lost exactly seqs 1–8 — tunnel re-establishment, not an episode. `probe2.sh` sends 6 discarded packets to each target first. Any probe that starts cold will manufacture a leading blackout.
- 🔴 **`pgrep -f 'flap-watch3.sh'` matched the Claude Code wrapper shell, not the watcher** — it wrote the wrong PID into `flap-watch.pid`. Resolve by scanning `pgrep -x bash` and reading `/proc/<pid>/cmdline`. This is the third time a `-f` pattern has misfired in this arc.
- **The homelab gateway is clean because nebula ROAMED it to the public endpoint, not because of the pin** — the pin has been absent for three days and `10.42.0.10` measured 0% twice. Do not read its health as evidence the pin works.
- **`ssh root@10.42.0.20` (far box, `diffsona`) works from the laptop over the mesh** — needs `-o StrictHostKeyChecking=accept-new` on first use. That is the third vantage; use it rather than re-deriving one.
- ⚠ **A 120 s probe does not fit a 2-minute foreground command budget** — it gets truncated and silently yields short files. Keep concurrent probes at 100 pkt @0.5 s (50 s), or background them properly.

- 🔴 **THIS SESSION SHIPPED A CONFIDENT MECHANISM AND RETRACTED IT WITHIN THE HOUR — the tell was there at write time and was read past.** The evidence was `from=` addresses on **handshake** log lines; the claim was about the **data path**. Those are different packet types, and nothing measured connected them. **A log field names the packets that produced it and nothing else.** The cheap disproof existed before the commit and cost one command: had nebula's data path used the LAN address, removing that route would have given 100% loss, not 13%.
- 🔴 **`sudo -n -l` is worth reading BEFORE designing a remedy.** Two rounds of this arc specified `ip rule`/fwmark fixes that no agent on this host can apply — `ip` is not in the sudoers list, `tailscale` (entirely) is. Design to the privileges that exist, or hand the step over explicitly.
- **A routing experiment must carry its own restore.** `experiment.sh` puts the revert in an `EXIT`/`INT`/`TERM` trap, so an interrupt or a probe failure still restores `accept-routes`. Verify the restore by re-reading `tailscale debug prefs` (`RouteAll`) **and** `ip route get`, not by assuming the command worked.
- **`tailscale set` changes only the pref you name; `tailscale up` re-asserts a whole prefs set.** Use `set` for a single-pref experiment.
- 🔴 **The v2 watcher under-reported the WORST episodes.** `loss_pct()` had no `tail -1`, so ping output matching `% packet loss` twice returned a MULTI-LINE value; `[ "$p" -ge N ]` then errored and fell to the else branch, logging it **quiet**. Seen at `2026-09-30T06:00:29Z` as `poll: 100\n100% quiet`. Reproduced with a `ping` shim. Old log "quiet" lines are not trustworthy at the high end.
- ⚠ **The ping output producing that double match was NOT reproducible** — v3 fixes the CLASS (shape-validate, fail LOUD via `BADPARSE`) rather than a guessed cause. Do not write up a mechanism for it.
- 🔴 **The first version of that fix contained an unreachable guard** — `1.2.3% packet loss` matched the tail `2.3`, so `BADPARSE` could never fire. The lookbehind `(?<![\d.])` is load-bearing; reachability was then proved.
- 🔴 **`cairn-validate --validate <path>` fails a PRISTINE template with "missing or empty `scope:`"** when the file sits outside the store. Control it against `--template` output before believing a verdict about your own file; the authoritative check is the post-write `hygiene.sh validate --scope <scope>`.
- ⚠ **`handoff_doc.py` reports `leakscan: NO SCANNER FOUND … PASS BY ABSENCE`** in devrc — it looks for `tests/leakscan.py`, which does not exist here. The real gate is `scripts/tests/test_no_public_ips.py`; run it yourself on a doc with a leak history.
- 🔴 **`claim-work --release` is per-WORKTREE**: claiming from the base clone and releasing from elsewhere is refused. Release from the same checkout you claimed in.

- 🔴 **A PID resolved immediately after `setsid` can be a transient — this arc recorded a dead one twice in one day.** `2309522` was written into two commits as "the running watcher"; the real process was `2309463` (`ppid=1`). Re-resolve from `/proc/<pid>/cmdline` at the moment you need it, and treat both the pid file and any number in this doc as a hint.
- 🔴 **LET THE INSTRUMENT RUN BEFORE THEORISING — 9 hours of it changed the question.** Every hypothesis in this arc was built on single ad-hoc probes of 100–300 packets. The unattended dataset showed the blackouts reach **59 s** (not 6.5–20 s), fire on **28%** of polls, and couple the two overlays in **98.8%** of episodes. None of that was visible in the hand-run samples the remedies were designed against.
- ⚠ **`grep -c TRIGGER` over the whole watcher log double-counts across watcher generations** — the log is appended across the v2→v3 handover. Slice from the `WATCHER HANDOVER` marker (`awk '/WATCHER HANDOVER/{f=1} f'`) before quoting any count.

- 🔴 **NEBULA DOES NOT TALK TO PEERS ON `:4242` — filtering on it watches the LIGHTHOUSES.** `listen.port: 0` ⇒ one ephemeral local socket (`51711`); NATed peers are reached on their NAT-mapped port. A `udp port 4242` capture returns lighthouse keepalives while looking exactly like peer traffic. It produced a confident wrong reading: a conntrack flow `[ASSURED]` with a stable source port through a 75% episode was reported as "the peer's flow survived, the local stack is innocent" — it was a **lighthouse** flow; the peer's data path was never observed. **Filter on the LOCAL port.**
- 🔴 **FIVE INSTRUMENT FAILURES IN ONE SESSION, EACH PRODUCING OUTPUT THAT LOOKED LIKE A FINDING.** (1) `pgrep -f` matched the agent's own wrapper shell → a dead PID in two commits. (2) A conntrack-timeout differential assumed idle flows decay; keepalives pin them near the 120 s max, so every peer read `NONE` — i.e. "all peers relayed". (3) `tcpdump -G` without `-w` is fatal, and with stderr to `/dev/null` it reported a clean-looking 0 packets. (4) A positive control tested `wc -l > 0` instead of "a packet matched", passing on a line tcpdump called `0 packets captured`. (5) The `:4242` filter. 🔴 **The pattern: every one was a ZERO or a UNIFORM result — a uniform result across all arms is the tell that the INSTRUMENT failed, not the system.** Never silence stderr on a capture tool; make a positive control assert the THING, never a proxy.
- ⚠ **`tcpdump` arg errors fire BEFORE the permission check** — reaching "You don't have permission" proves the flags parsed. The FILTER cannot be validated that way (a malformed filter gives the same error), so filter correctness rests on the run's own positive control.
- 🔴 **An outbound-port extractor must anchor on the LOCAL port** or it counts inbound replies as remotes: `> [0-9.]+\.\K[0-9]+` matched our own `51711` on reply lines. Use `\.<localport> > [0-9.]+\.\K[0-9]+`, and control it BOTH ways — reject the local port, and confirm a second genuine remote still counts.

## How to verify
```bash
S=/tmp/claude-1000/-home-zach-workspace-devrc/765865a3-5a34-4368-9c27-c442fd52106c/scratchpad
for p in $(pgrep -x bash); do [ -r /proc/$p/cmdline ] && tr '\0' ' ' < /proc/$p/cmdline \
  | grep -q flap-watch3 && ps -o pid=,etime= -p $p; done        # watcher alive
awk '/WATCHER HANDOVER/{f=1} f' $S/flap-watch2.log > /tmp/v3.log  # v3's OWN record only
grep -c 'poll:' /tmp/v3.log; grep -c TRIGGER /tmp/v3.log; grep -c BADPARSE /tmp/v3.log
ip rule show | grep 5150        # ABSENT since the 2026-09-27 reboot
sudo -n -l                      # `ip`/`nft` NOT granted — design remedies to this
```
Lockstep check (the central fact): parse `/tmp/v3.log` for paired `nebula(`/`tailscale(` loss+run per episode and count exact matches — was 167/169 on 2026-09-30.
## How to verify — CARRIED verbatim from the 09-24 doc (arc closing-condition)
🔴 RUN IT ALL WITH THE TUNNEL UP, and substitute HOME_PUB first; with the tunnel down several lines go vacuously green (`ip link show airvpn` fails, split-tunnel probe returns `dev wlp170s0`, pill up=false — those three catch you).
```bash
# tunnel + split-tunnel, with the tunnel UP:
ip link show airvpn && ip rule | grep 500         # pin present: uidrange 991-991 lookup main
HOME_PUB='<home-public-ip>'                       # set at run time; NEVER inline the value
case "$HOME_PUB" in *'<'*) echo "substitute HOME_PUB first" >&2; exit 2;; esac
ip route get "$HOME_PUB" uid 991                  # → via <gw> dev wlp170s0 (NOT airvpn)
ip route get "$(getent ahostsv4 one.one.one.one | awk '{print $1; exit}')" | head -1
                                                  # → dev airvpn table 51820
curl -s https://ipinfo.io/json | jq -r .country   # → US
ssh zach@10.42.0.30 'echo nebula-ok'              # nebula path alive with tunnel up
python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.cache/bar-status/airvpn.json'))); print(d['up'], d['verdict'], d['server'], d['country_code'])"
systemctl --user list-timers airvpn-status-poll.timer --no-pager | head -3
# v6-remote noise only, no MTU/routing churn:
journalctl -u 'nebula@mesh' --since '<up-time>' | grep -c 'Failed to write outgoing packet|Failed to send handshake'
```
🔴 Killswitch re-test protocol: `claude/skills/bar/reference/airvpn.md` (laptop section) — FAIL-CLOSED on this laptop's ONLY uplink. Instant bail keeping the tunnel: `sudo nft delete table inet airvpn_ks`; full teardown: `sudo /etc/nixos/i3blocks-scripts/airvpn-sudo down`.
