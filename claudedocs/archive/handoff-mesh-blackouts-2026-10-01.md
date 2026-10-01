# Archive: mesh-blackouts — evicted 2026-10-01

Blocks moved out of `claudedocs/handoff-mesh-blackouts-laptop-workbench.md` to keep it under
the 65,536 B ceiling. 🔴 **Unlike the tunnel archive, these are NOT merely superseded — one of
them rests on a MEASUREMENT ARTIFACT** (the watcher's tailscale arm fell back to the nebula
address, manufacturing a 99% 'lockstep'). Read them for the raw numbers and for the record of
what was believed; do NOT adopt their conclusions. The live doc's RETRACTION block is current.

### 🔴🔴 THE LOSS IS ON THE **RETURN** LEG — every mechanism proposed in this arc was aimed at the forward path
- as-of: 2026-09-30
- 🔴 **This is the first result that PREDICTS the lockstep, which is this arc's closing condition. It rests on ONE episode — treat it as a strong lead, not a settled mechanism, and let watcher v4 accumulate more.**
- **Symptom + exact repro:** `bash <scratchpad>/direction.sh`. The laptop sends exactly N ICMP echo REQUESTS and records its own round-trip loss; the workbench's kernel counts how many requests actually ARRIVED, read unprivileged from `/proc/net/snmp` `Icmp:InEchos`. Comparing the two separates the directions, which **no round-trip measurement can**. Needs NO root on either host.
- **Observed (with values), 2026-09-30, tunnel down:** one episode caught — **sent 200, arrived 200, round-trip lost 63, shortfall 0**. Every request reached the workbench; 63 replies never came back. Verdict `RETURN-LEG`. `via: measurement`
- **Positive control (passed, twice):** a 20-ping burst moves `InEchos` by exactly 20, and a 12-ping burst by exactly 12 — so arrivals are attributable. Background ICMP over 20 s of genuine quiet is **0**.
- 🔴 **Ruled out — the forward path as the site of the loss**, for this episode: 200 of 200 requests arrived while 31.5% of the round trips failed. `via: measurement`
- **Leading hypothesis — and the reason it matters: it PREDICTS THE LOCKSTEP.** If the laptop's CGNAT mapping is evicted, **inbound** packets are dropped while **outbound** still works and re-creates the mapping. Both overlays' return traffic arrives at the same CGNAT for the same subscriber, so both die in the same second and both recover on the laptop's next outbound packet — which is exactly the 170-of-172 identical-loss signature. It also explains the far box reaching the workbench cleanly (nothing in that path touches the laptop's CGNAT) and fits the gateway peer staying clean if its flow is busy enough to keep its mapping warm. ⚠ **UNTESTED as a mechanism**; only the direction is measured.
- **Next probe:** (a) let v4 accumulate `DIRECTION:` lines and require several episodes to agree; (b) if RETURN-LEG holds, test the mechanism by shortening the overlays' keepalive so the mapping cannot idle out — nebula `punchy` interval, tailscale's equivalent — which is a config change, not a network fix, and would be the first actionable remedy this arc has produced.


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


