# Archive: laptop-airvpn-tunnel — evicted 2026-09-30

Blocks moved out of `claudedocs/handoff-laptop-airvpn-tunnel.md` to keep it under the
65,536 B ceiling. 🔴 **Nothing here was deleted and nothing here is known-wrong** — each
block is either CLOSED or SUPERSEDED by a later measurement in the live doc. The
eliminations they record still stand; read them before re-running a probe they already
answered. The live doc carries a pointer at each eviction site.

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


### mosh's stated benefit was WRONG, and the correction is smaller than the claim
- as-of: 2026-09-24
- **Symptom + exact repro:** not a bug — a false claim that shipped in three places (PR body, commit message, source comment) and would have been acted on. Read `nix/pkgs/default.nix`'s mosh comment on `main` for the corrected wording.
- **Observed (with values):** mosh does **not** open a UDP session first. It runs `ssh <host> mosh-server new`, parses the key and port out of **that ssh session**, and only then switches to UDP. So the TCP/SSH handshake — the exact thing described as failing with a banner-exchange timeout — is **unchanged** by installing mosh. `via: code`
- **Ruled out — that mosh helps you *initiate* a session over a link failing during connect.** It cannot; the bootstrap is ssh. What it buys is keeping an **already-established** session alive across a drop, which is real but smaller. `via: code`
- **Ruled out — that the benefit was ever demonstrated here.** mosh was never installed while a blackout was live, and no host runs `mosh-server`. The shipped comment labels it an inference for that reason. `via: measurement`
- **Leading hypothesis:** for the 6.5–20 s blackouts measured on this path, plain ssh over TCP mostly *survives* — you get a frozen terminal, not a dropped session — so mosh's practical gain here is comfort rather than continuity. Untested.
- **Next probe:** none needed for the claim; it is settled. If you want the benefit, do the two-line edit in `nix/pkgs/default.nix`'s comment on the target host and then `mosh zach@10.42.0.30` during a live episode.


### Mesh blackouts laptop↔home (UDP-only) — RECURRENCE CONFIRMED 2026-09-30
- as-of: 2026-09-30
- **Symptom + exact repro:** `ping -c 120 -i 0.5 10.42.0.30` shows contiguous blackout runs; own-link control `ping -c 120 -i 0.5 192.168.1.1` must be 0%.
- **Observed (with values), 2026-09-30 05:50Z:** nebula to home 21.7% loss (26/120), blackouts at epochs 1790747264→1790747303 (~40 s) plus singles; local-gw control 0/120. ssh burst ×5: 4× `rc=0`, then `banner exchange timeout` rc=255 — the exact failure mode the `ip rule` pin was justified on.
- **Watcher relaunched same session:** old watcher (pid 2709742, launched 09-24 05:09Z) found DEAD — log gap 09-27 20:28 → present, 1 episode total (09-25 21:53Z: all three ICMP controls 0%, UDP runs 1–2 pkt, but its loss% fields garbled by a parse bug). New watcher pid **2115782** (`setsid`), log `<scratchpad>/flap-watch2.log`, pid file `flap-watch.pid`. Parse bug FIXED and positive-controlled (regex `\d+(?=% packet loss)` concatenated multi-digit lines → `loss=833333%`; new regex `(\d+(?:\.\d+)?)% packet loss | tail -1` verified reading 75.0).
- **First catch of the new watcher, 2026-09-30 05:52:57Z (TRIGGER at 95% poll loss):** discriminator 60 s concurrent — `icmp-local-gw` 0%/run 0 · `icmp-last-hop-before-home` 0%/run 0 · `icmp-far-box` 0%/run 0 · `nebula(UDP)` **7.5%, run 5 pkt** · `tailscale(UDP)` **7.5%, run 5 pkt**.
  - via: measurement
- **Ruled out — laptop link, home router/NAT, forward+return IP path:** carried from 09-24, unchanged — third vantage 0/300 loss while laptop lost 45% in the same window; mtr both directions clean; local gw 0%.
  - via: measurement
- **Leading hypothesis (STILL untested):** per-flow UDP state eviction on the CGNAT path — now backed by an episode-triggered capture (identical simultaneous loss on BOTH overlays while every ICMP control was clean), not just ad-hoc pings.
- **Next probe:** let the watcher accumulate episodes overnight (`grep -A9 'EPISODE' <scratchpad>/flap-watch2.log`), then decide the permanent `ip rule` pin form vs a tailscale route exclusion for UDP:4242 on the accumulated evidence.
- Scratchpad: `/tmp/claude-1000/-home-zach-workspace-devrc/765865a3-5a34-4368-9c27-c442fd52106c/scratchpad` (flap-watch.sh, flap-watch2.log, flap-watch.pid).

