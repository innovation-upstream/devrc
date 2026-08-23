# Laptop freezes — diagnosis as of 2026-08-21

## The finding

**16 unclean stops** across the retained journal, not the "twice in the past
month" they were believed to be. The rate was invisible because the only
detector was a human noticing that work had been interrupted — a freeze while
away from the machine left no impression at all.

Classified by whether the boot reached systemd's shutdown sequence:

| kernel | unclean stops |
|---|---|
| 6.18.4 | Mar 22 |
| 6.19.11 | Apr 12, Apr 17 |
| 7.0.0 | May 18, May 19, May 24, Jul 29, **Jul 31**, Aug 6, Aug 10, Aug 17, Aug 20 |

🔴 **There is no onset.** An early reading of this called Jul 29 the start and
reasoned "no software change coincides with onset, so suspect recent hardware
degradation". That framing was wrong — the stops go back to March. June through
Jul 4 was genuinely clean, but a clean stretch is not an onset.

## What the Jul 31 crash dump says

`/var/lib/systemd/pstore/1785525502/002/dmesg.txt` (root-only). This is the one
freeze that captured a trace, because it was a real kernel fault rather than a
hardware hang.

**Mechanism, decoded:**

1. Page fault at `ct_kernel_enter_state+0x8`. The instruction decodes as

   ```
   mov  %gs:0x1a4d130(%rip),%rax      # load per-CPU context-tracking pointer
   lock xadd %edi,-0x7b4b2138(%rax)   # atomic add into it
   ```

   with `RAX = 0x000000040001f30a` — **not a kernel address**. The arithmetic
   closes exactly: `0x40001f30a - 0x7b4b2138 = 0x384b6d1d2 = CR2`. So the
   per-CPU pointer was garbage; the access itself was fine.

2. The fault handler re-entered the same path —
   `irqentry_enter → ct_nmi_enter → ct_kernel_enter_state → fault` — recursing
   ~272 bytes of stack per level across eleven visible frames.

3. Stack exhausted → `#DF` double fault → `Thread overran stack, or stack
   corrupted`, `stack recursion on stack type 5`. Unrecoverable, no clean panic
   path, and the machine simply stopped rather than rebooting.

**Two details that matter:**

- `CPU: 4 PID: 0 Comm: swapper/4` — the **idle task**. The corruption was hit
  transitioning out of idle, which fits freezes happening while nobody is at the
  machine and explains why the rate went unnoticed.
- `Tainted: G W` — something had already WARNed earlier in that boot. That
  journal has rotated away, so the first symptom is unknown.

## Leading hypothesis: memory corruption

A garbage value where a per-CPU pointer belongs is a corruption signature, and
the stops span **three kernel versions**. A regression in the entry /
context-tracking path would not survive 6.18 → 6.19 → 7.0.

Honest limits: there is **one** dump. Attributing the other fifteen stops to the
same cause is inference. "Unclean" is also a symptom count, not a diagnosis — it
covers a hard lockup, a flat battery, a held power button and a panic alike.

Ruled out along the way: memory *pressure* (62 GB total, 40 GB free, zero swap
used, no OOM), suspend/resume (no sleep transition precedes the Aug 17 or Aug 20
stops), and any coincident config change (kernel and BIOS 03.17 constant across
the boots examined; NixOS generations only Jun 23 and Aug 13).

An upstream search for this signature (`ct_kernel_enter_state`, double fault,
lazy preempt) found no matching report — which is not evidence either way.

## Next step: memtest86+

`nix/system/apply-freeze-followup-2026-08-21.sh` adds a memtest86+ entry to the
systemd-boot menu, so the test is one reboot away.

🔴 **Budget hours.** A full pass over 64 GiB is not quick, and **a short clean
run is not evidence of good RAM** — let it complete at least one full pass,
ideally overnight.

If memtest is clean, the next discriminator is pinning an older kernel and
watching whether the rate changes — but note the rate is roughly one stop every
1–2 weeks, so that experiment needs weeks, which is exactly why the metric below
exists.

## What is now instrumented

- **PR #616** — `kernel.nmi_watchdog=1` (TLP had been disabling the hard-lockup
  detector on every AC↔battery transition), `hardlockup_panic=1`, `panic=20`,
  journald `SyncIntervalSec=30s`. Applied and verified live 2026-08-21.
- **PR #634 / #671** — host telemetry to homelab Prometheus + Loki. The
  `host_unclean_boots_observed` / `host_boots_examined` pair makes the freeze
  **rate** a queryable series instead of an anecdote. Read them together: a zero
  from a scan that walked nothing is not an all-clear.
- **This script** — `panic_on_oops=1`, reversing an explicit decision in #616.
  That decision reasoned "an oops is already logged; the silent case is the hard
  lockup". The dump refutes it for this machine: the first fault *was* an oops
  and it cascaded into an unrecoverable double fault. Panicking at step 1 yields
  one clean trace plus an automatic reboot on the `panic=20` already set.

## Still open

- The `Tainted: G W` warning that preceded the crash is unidentified.
- Only one of sixteen stops has a captured trace. The instrumentation above is
  what changes that for the next one.
- The workbench also had an unclean stop (Jul 14, coredumps then silence), but
  its journal retains only two boots, so no rate can be established there.
