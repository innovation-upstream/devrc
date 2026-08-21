# Handoff: laptop-freezes — 2026-08-21

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

> No `clawgate-task:` field: `clawgate_handoff.sh resolve` exited **5** (nothing
> resolved). An unknown session id answers 200 with an empty array, so that cannot
> distinguish "touched no task" from "wrong id". No task was created.

## Goal

The laptop was reported as having "hard-frozen twice in the past month". Find out
what is actually happening and make the next occurrence diagnosable.

## State now

- **Branch:** `main`, clean, at `origin/main` (`29d7913e`).
- **The freeze cause is NOT resolved.** Everything below is instrumentation plus
  one decoded crash trace. See *Open investigations*.

**Merged and deployed this session**

| PR | what | verification actually performed |
|---|---|---|
| #616 | `nmi_watchdog=1`, `hardlockup_panic=1`, `panic=20`, journald `SyncIntervalSec=30s` | live: all three sysctls read correct; `NMI_WATCHDOG=1` present in the generated `/etc/tlp.conf` |
| #634 | host telemetry → homelab Prometheus + Loki (Alloy + node_exporter + boot-outcome, user units, both hosts) | live: metrics + logs landing, `stdout` absent |
| #671 | removed `basic_auth` — an env-driven block with empty values still sends `Basic Og==` | live: 64 metric series + 200 log entries under `host="authtrial"` |
| #679 | browser-bridge SKILL.md was 221 B over its ceiling — **main was red**, unrelated to this work | `test_skill_size.py` 4 passed; 11,961 B / 327 free |
| #680 | staged `panic_on_oops=1` + a memtest86+ boot entry, plus the diagnosis doc | fixture-tested: edits land, idempotent, refuses-and-restores on a missing anchor |

**Live state right now (laptop):**

```
kernel.nmi_watchdog      1     kernel.panic           20
kernel.hardlockup_panic  1     kernel.panic_on_oops    0   <- #680 not yet applied
memtest boot entry       0                                 <- #680 not yet applied
```

**Deploy status, honestly:** #616 is applied and verified. #634/#671 are deployed
and shipping on the **laptop**. #680 is **staged only** — it needs a `sudo` run.

🔴 **The workbench is NOT converged** and `ship.sh` skips it (rc7): its base clone
sits on `zach/browser-civitai-account-switcher` while `main` is held by another
session's worktree at `/home/zach/workspace/devrc-r3-pushctl`. Deliberately not
touched — another session's live state. Consequence is nil: it still ships
telemetry on the previous config, and what it is missing is a staged script and a
doc, neither of which deploys.

## Open investigations — live diagnosis state

### Laptop stops uncleanly ~every 1–2 weeks; cause unproven

- **Symptom + exact repro:** no repro. The machine stops without reaching
  systemd's shutdown sequence. **16 occurrences** across the retained journal.
  Classify with:
  `for b in $(seq -29 -1); do journalctl -b $b --no-pager | tail -12 | grep -q 'Journal stopped\|System Power Off\|Shutting down' || echo "$b UNCLEAN"; done`

- **Observed (with values):**
  - Distribution by kernel — **the key measurement**:
    `6.18.4` Mar 22 · `6.19.11` Apr 12, Apr 17 · `7.0.0` May 18/19/24, Jul 29/31,
    Aug 6/10/17/20. **Three kernel versions.**
  - The one captured trace, `/var/lib/systemd/pstore/1785525502/002/dmesg.txt`
    (root-only), from the Jul 31 stop:
    - `BUG: unable to handle page fault for address: 0000000384b6d1d2`
    - `RIP: 0010:ct_kernel_enter_state+0x8/0x20`
    - Instruction decodes as `mov %gs:0x1a4d130(%rip),%rax` then
      `lock xadd %edi,-0x7b4b2138(%rax)`, with `RAX = 0x000000040001f30a` —
      **not a kernel address**. `0x40001f30a - 0x7b4b2138 = 0x384b6d1d2 = CR2`
      exactly, so the per-CPU pointer was garbage; the access itself was fine.
    - Recursion `irqentry_enter → ct_nmi_enter → ct_kernel_enter_state → fault`,
      ~272 B of stack per level over eleven visible frames → stack exhausted →
      `Oops: 0002 [#1]`, `#DF`, `Thread overran stack, or stack corrupted`,
      `WARNING: stack recursion on stack type 5`. Machine stopped, did not reboot.
    - `CPU: 4 UID: 0 PID: 0 Comm: swapper/4` — the **idle task**.
    - `Tainted: G W` — something WARNed earlier that boot; that journal is gone.
  - Hardware: `Framework Laptop/FRANBMCP06, BIOS 03.17 10/27/2022`, i7-1165G7,
    64 GiB. Both `xe` (used_by=0) and `i915` (bound) are loaded.

- **Ruled out:**
  - *Memory pressure* — 62 GiB total, 40 GiB free, zero swap used, no OOM kills.
  - *Suspend/resume* — no sleep transition precedes the Aug 17 or Aug 20 stops.
  - *A coincident config change* — BIOS 03.17 constant; NixOS generations only
    Jun 23 and Aug 13; the stops straddle both.
  - *A kernel regression in the entry/context-tracking path* — **weak**: it would
    not survive 6.18 → 6.19 → 7.0.
  - *"Onset Jul 29"* — 🔴 **RETRACTED.** An earlier reading called Jul 29 the
    start and argued "no software change coincides with onset ⇒ recent hardware
    degradation". The stops go back to March; there is no onset. Do not re-derive.
  - Upstream search for `ct_kernel_enter_state` + double fault + lazy preempt
    found no matching report. Not evidence either way.

- **Leading hypothesis:** **memory corruption.** A garbage value where a per-CPU
  pointer belongs, hit from the idle task, across three kernel versions.
  Honest limit: **one** dump for sixteen stops — attributing the rest is inference.
  "Unclean" is a symptom count, not a diagnosis (covers flat battery, held power
  button, panic).

- **Next probe:** run the staged script, reboot, pick memtest86+ from the boot
  menu, let it complete **at least one full pass** (hours on 64 GiB).
  ```bash
  sudo bash ~/workspace/devrc/nix/system/apply-freeze-followup-2026-08-21.sh
  ```
  🔴 A short clean pass is **not** evidence of good RAM.

### Unidentified `Tainted: G W` warning preceding the Jul 31 crash

- **Observed:** the dump reads `Tainted: [W]=WARN`, so a WARN fired earlier in
  that boot. Boot `-7`'s journal no longer retains it
  (`journalctl -b -7 -k | grep WARNING` → empty).
- **Next probe:** nothing to run retroactively. With #680 applied, the next event
  panics at the first fault and captures it, so the *first* symptom should be in
  the next trace rather than lost behind hundreds of recursive frames.

## Next steps (ranked)

1. **Run the staged script + memtest** (command above). This is the discriminator.
2. **If memtest is clean:** pin an older kernel and watch the rate. Note the rate
   is ~1 stop per 1–2 weeks, so that experiment needs *weeks* — which is what
   `host_unclean_boots_observed` is for.
3. **Converge the workbench** once another session frees `main` there, then
   `scripts/ship.sh` (it will pick up #680's files).
4. **Optional:** alert on `host_boot_previous_clean == 0` → the existing
   Telegram receiver, so a freeze pages rather than waiting to be queried.

## Gotchas / decisions / dead-ends

- 🔴 **TLP silently disables the hard-lockup detector.** The kernel enables it at
  boot; TLP writes `0` seconds later, because `NMI_WATCHDOG` is absent from the
  generated `/etc/tlp.conf` and it falls back to its shipped `defaults.conf`. It
  re-applies on **every AC↔battery transition**, so the fix must live in
  `services.tlp.settings`, never `boot.kernel.sysctl`.
- 🔴 **`panic_on_oops` was excluded from #616 and that was wrong.** The reasoning
  was "an oops is already logged; the silent case is the hard lockup". This
  machine's oops was neither survivable nor usefully logged. #680 reverses it.
- 🔴 **Journal shipping is a transport ALLOWLIST, not redaction.** Five audit
  rounds each found a defect in the credential-redaction regexes, three of them
  introduced by the preceding fix. `stdout` (69.9% of the journal, and every
  risky content class) is now dropped structurally; the regexes remain as
  defence-in-depth only. Do not "improve" them back into being load-bearing.
- **Alloy's `stage.replace` substitutes per CAPTURE GROUP and does no `$1`
  expansion**; with zero groups it replaces nothing. `alloy validate` accepts
  every wrong variant — regex semantics are not a syntax property.
- **`alloy validate`, never `alloy fmt`** — fmt accepted a real typo with rc=0.
  Validation runs at *build* time in `nix/observability.nix`, so a broken config
  fails `home-manager switch` rather than silently taking the agent down.
- **A dangling managed symlink blocks `home-manager switch`** — an Aug-13
  `opencode/skills/clickup` symlink into a GC'd store path made `mkdir` fail.
  `rm` it and re-switch.
- **Deployed ≠ restarted.** A `switch` does not restart a unit whose config did
  not change, so Alloy ran for over an hour with empty endpoints (`url=""`) after
  the env file was created. Restart the consumer.
- **`time() - timestamp(metric)`, not `value[0]`**, to measure staleness —
  `value[0]` is the query evaluation time and reads 0s against hours-old data.

## How to verify

```bash
# 1. freeze instrumentation is live AND durable
sysctl kernel.nmi_watchdog kernel.hardlockup_panic kernel.panic kernel.panic_on_oops
grep NMI_WATCHDOG "$(readlink -f /etc/tlp.conf)"      # must be 1 — survives power events
#    then move the charger and re-check nmi_watchdog

# 2. telemetry is shipping, and stdout is NOT
systemctl --user status alloy node-exporter
N=192.168.50.94
curl -s -G "http://$N:30909/api/v1/query" --data-urlencode \
  'query=time() - timestamp(node_time_seconds{host="laptop"})'          # expect < 60
curl -s -G "http://$N:30310/loki/api/v1/query_range" --data-urlencode \
  'query={host="laptop"}' --data-urlencode "start=$(( $(date +%s) - 300 ))000000000" \
  --data-urlencode "end=$(date +%s)000000000" | grep -c stdout            # expect 0

# 3. the freeze rate as a series
curl -s -G "http://$N:30909/api/v1/query" --data-urlencode \
  'query=host_unclean_boots_observed'   # read BESIDE host_boots_examined —
                                        # a zero from a scan that walked nothing
                                        # is not an all-clear
```
