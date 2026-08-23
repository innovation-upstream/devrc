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

- **Branch:** `main`, clean, at `origin/main` (`dc7ebb7b`).
- **The freeze cause is NOT resolved.** Everything below is instrumentation plus
  **two** decoded crash traces. See *Open investigations*.

🔴 **2026-08-21 19:0x — A 17TH STOP HAPPENED, AND IT CHANGED TWO CONCLUSIONS.**
Boot `-1` ran Aug 20 17:20 → **Aug 21 18:45:09** and ended with no shutdown
sequence. It captured a dump. Two things came out of it:

1. **#616's hard-lockup panic has never once been armed.** `kernel.nmi_watchdog`
   reads **0** at steady state despite #616's TLP fix being correct and working.
   `powertop --auto-tune` runs *after* TLP and writes 0 itself. So
   `hardlockup_panic=1` has been dead code on every boot since #616 merged.
2. **The memory-corruption hypothesis got much stronger**, from a second dump
   with a *different* signature that nevertheless points the same way.

**Merged and deployed this session**

| PR | what | verification actually performed |
|---|---|---|
| #616 | `nmi_watchdog=1`, `hardlockup_panic=1`, `panic=20`, journald `SyncIntervalSec=30s` | live: all three sysctls read correct; `NMI_WATCHDOG=1` present in the generated `/etc/tlp.conf` |
| #634 | host telemetry → homelab Prometheus + Loki (Alloy + node_exporter + boot-outcome, user units, both hosts) | live: metrics + logs landing, `stdout` absent |
| #671 | removed `basic_auth` — an env-driven block with empty values still sends `Basic Og==` | live: 64 metric series + 200 log entries under `host="authtrial"` |
| #679 | browser-bridge SKILL.md was 221 B over its ceiling — **main was red**, unrelated to this work | `test_skill_size.py` 4 passed; 11,961 B / 327 free |
| #680 | staged `panic_on_oops=1` + a memtest86+ boot entry, plus the diagnosis doc | fixture-tested: edits land, idempotent, refuses-and-restores on a missing anchor |

**Live state right now (laptop), measured 2026-08-21 after the 17th stop:**

```
kernel.nmi_watchdog      0  <- 🔴 NOT 1. An earlier reading of 1 was taken
                           #    before powertop.service had run. See below.
kernel.hardlockup_panic  1  <- inert while nmi_watchdog=0
kernel.panic            20
kernel.panic_on_oops     0  <- #680 not yet applied
memtest boot entry       0  <- #680 not yet applied
```

🔴 **`nmi_watchdog=1` in the table above is a CORRECTION, not a regression.**
The value genuinely was 1 when the earlier session read it — TLP had just been
restarted. It is 0 in steady state because powertop runs later. Reading it once,
right after touching TLP, could not see that; the value must be read *after* the
boot sequence settles, or better, re-read after restarting the writer.

**Deploy status, honestly:** #616 is applied and verified. #634/#671 are deployed
and shipping on the **laptop**. #680 is **staged only** — it needs a `sudo` run.

🔴 **The workbench is NOT converged** and `ship.sh` skips it (rc7): its base clone
sits on `zach/browser-civitai-account-switcher` while `main` is held by another
session's worktree at `/home/zach/workspace/devrc-r3-pushctl`. Deliberately not
touched — another session's live state. Consequence is nil: it still ships
telemetry on the previous config, and what it is missing is a staged script and a
doc, neither of which deploys.

## Open investigations — live diagnosis state

### 🔴 The NMI watchdog is disabled by powertop — RESOLVED, fix staged

- **Measured** (`nix/system/diagnose-freeze-2026-08-21.sh`, a discriminating
  control — each unit restarted in isolation from a known value of 1):

  | action | resulting `kernel.nmi_watchdog` |
  |---|---|
  | write 1 by hand (positive control) | 1 — the file *is* writable |
  | `systemctl restart powertop.service` | **0** |
  | `systemctl restart tlp.service` | **1** |

  So powertop is the writer and TLP is innocent — #616's TLP fix is correct and
  necessary, just not sufficient. `powerManagement.powertop.enable = true` is set
  in `/etc/nixos/configuration.nix`, its unit is `After=multi-user.target` (i.e.
  after TLP), and disabling the NMI watchdog is one of `--auto-tune`'s standard
  tunables.

- **Why this matters more than a stray sysctl:** the hard-lockup detector is what
  *makes* `hardlockup_panic=1` fire. With `nmi_watchdog=0` the detector never
  runs, so #616's headline mechanism — "a hard lockup now panics instead of
  hanging silently" — has been inoperative on every boot since it merged. The
  16th and 17th stops were both unwatched.
- **Fix:** step 3 of `apply-freeze-followup-2026-08-21.sh` adds an
  `ExecStartPost` to `powertop.service` re-asserting 1. Not yet applied.
- 🔴 **Verify it by restarting the WRITER, not by reading the value.** A bare
  `sysctl kernel.nmi_watchdog` right after a switch reads 1 for the same reason
  the earlier session's reading did, and proves nothing:
  `sudo systemctl restart powertop.service && sysctl kernel.nmi_watchdog`.

### Laptop stops uncleanly ~every 1–2 weeks; cause unproven

- **Symptom + exact repro:** no repro. The machine stops without reaching
  systemd's shutdown sequence. **17 occurrences** across the retained journal
  (the 17th: Aug 21 18:45:09, boot `-1`).
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

  - 🔴 **The SECOND captured trace, from the 2026-08-21 18:45 stop.** Five
    decoded `dmesg.txt` files under `/var/lib/systemd/pstore/17873559{36,37,39}/`
    (root-only; `diagnose-freeze-2026-08-21.sh` dumps them world-readable). The
    pstore fragments are labelled `Oops#N PartM` in **descending** M — read them
    bottom-up or the trace looks scrambled.

    **First fault (`[#1]`), the only one that matters — the rest are fallout:**
    ```
    Oops: general protection fault, probably for non-canonical
          address 0x4000000000000018: 0000 [#1] SMP NOPTI
    CPU: 2  UID: 1000  PID: 2792101  Comm: alloy  Not tainted  7.0.0
    RIP: 0010:timerqueue_add+0x37/0xd0
    RAX: 4000000000000000  RCX: 4000000000000010  RDX: 4000000000000000
    RSI: 0000533d7a14cfa8  R09/R11..R15: ffff8dde........  (sane heap ptrs)
    Call Trace: epoll_pwait -> do_epoll_wait
                -> schedule_hrtimeout_range_clock
                -> hrtimer_start_range_ns -> timerqueue_add
    ```
    Decoding `Code:` — `48 89 d0` (`mov %rdx,%rax`), `48 8d 48 10`
    (`lea 0x10(%rax),%rcx`), then the faulting `<48> 3b 70 18`
    (`cmp 0x18(%rax),%rsi`). `RAX + 0x18 = 0x4000000000000018` = the reported
    address exactly, so the decode is confirmed by arithmetic, not by eyeballing.
    RDX is the rbtree node pointer loaded by the descent loop's `mov (%rcx),%rdx`.

    **The finding: `0x4000000000000000` is NULL with exactly one bit set —
    bit 62.** And NULL is precisely the value that belongs there: `timerqueue_add`
    descends `while (*link)`, so the leaf's child pointer *is* NULL and is what
    terminates the loop. One flipped bit turned the terminator into a
    non-canonical pointer, the loop failed to stop, and it dereferenced it.
    Corroborating that the surrounding state was otherwise healthy:
    - every genuine kernel pointer in the frame reads `0xffff8dde…`/`0xffffd249…`
      — only this one word is wrong;
    - `RSI` (the new timer's expiry) `= 0x533d7a14cfa8` = 91,523.5 s, against an
      uptime at fault of 91,522.67 s — an expiry 0.84 s in the future. **The
      timer being inserted is perfectly sane; only the tree pointer is corrupt.**

    **`Not tainted`** — unlike Jul 31. See the `Tainted: G W` section below.

    Cascade after the first fault (all within ~20 ms, `[#2]`–`[#6]`):
    `seq_read_iter` on CPU 5 (`.claude-wrapped`), `__queue_work` on CPU 1
    (`tmux: server`), then `[#3]`–`[#6]` are `__show_trace_log_lvl` repeatedly
    hitting `Thread overran stack, or stack corrupted` — the oops *printer*
    blowing the stack while dumping the earlier oopses. Machine stopped.

  - **The two dumps do NOT share a signature — and that is itself informative.**
    Jul 31 was a page fault at `ct_kernel_enter_state` from the idle task on
    CPU 4; Aug 21 is a GP fault at `timerqueue_add` from `alloy` on CPU 2.
    Different subsystem, different CPU, different task, different fault class.
    A software bug that reproduced twice would be expected to look alike; a
    corrupted word landing wherever it happens to land would not. What the two
    *do* share is shape: **a single 64-bit word carrying one anomalous high bit**
    (bit 62 here; bit 34 in Jul 31's `RAX = 0x000000040001f30a`) where a pointer
    belonged. Honest limit: the bit positions differ, so this is not a stuck bit
    at one fixed position, and two samples cannot establish a distribution.

  - **ECC cannot help here — checked, not assumed.** `igen6_edac v2.5.1` loads,
    but `/sys/devices/system/edac/mc/` contains **no `mc0`**: the driver
    registered no controller, i.e. Tiger Lake IBECC is disabled in firmware.
    There are no correctable-error counters to read, so memtest86+ is the only
    available discriminator. (`lsmem`: 66 G online; one `ee1004` SPD EEPROM at
    `0-0050` enumerated, so this appears to be a single SODIMM — worth
    confirming with `dmidecode -t memory` before planning any swap test.)

  - Two lines immediately preceding the fault, unexplained and possibly relevant:
    `[88942] perf: interrupt took too long (3941 > 3940), lowering
    kernel.perf_event_max_sample_rate to 50000` and, ~600 µs before the oops,
    `[91522.675527] Scheduler frequency invariance went wobbly, disabling!` —
    the latter means the APERF/MPERF MSR ratio read implausibly. Not a diagnosis;
    noted because it is the only anomaly in the seconds before the crash.

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

- **Leading hypothesis:** **memory corruption**, and the Aug 21 dump strengthens
  it materially. The single-bit corruption of a word whose correct value (NULL)
  is *known from the algorithm* is much tighter evidence than Jul 31's "this
  pointer looks like garbage": here the expected value, the observed value, and
  the one-bit distance between them are all pinned. Add that the two dumps differ
  in every particular except that shape, and a software bug fits poorly.
  Honest limits, unchanged in kind: **two** dumps for seventeen stops — attributing
  the other fifteen is inference. "Unclean" is a symptom count, not a diagnosis
  (covers flat battery, held power button, panic). And a bit flip observed in DRAM
  *contents* does not by itself localise the fault to the DIMM — CPU, IMC, or
  cache can corrupt a word in flight, and memtest86+ exercises all of them
  together without separating them. A **clean** memtest is therefore the more
  informative outcome, because it is the one that redirects the search.

- **Next probe:** run the staged script, reboot, pick memtest86+ from the boot
  menu, let it complete **at least one full pass** (hours on 64 GiB).
  ```bash
  sudo bash ~/workspace/devrc/nix/system/apply-freeze-followup-2026-08-21.sh
  ```
  🔴 A short clean pass is **not** evidence of good RAM.

### Unidentified `Tainted: G W` warning preceding the Jul 31 crash — DOWNGRADED

- **Observed:** the Jul 31 dump reads `Tainted: [W]=WARN`, so a WARN fired earlier
  in that boot. Boot `-7`'s journal no longer retains it
  (`journalctl -b -7 -k | grep WARNING` → empty).
- 🔴 **The Aug 21 crash's first fault reads `Not tainted`**, and boot `-1`'s
  journal contains **zero** `WARNING:`/`BUG:`/`Oops` lines across its whole
  25-hour life. So an identical class of crash occurred with no preceding WARN:
  **the earlier WARN was not a precondition.** Worth no further effort unless it
  recurs. (`Tainted: [D]=DIE` on the Aug 21 *cascade* oopses is just the `[#1]`
  oops having already happened — not a third signal.)
- Note the same journal fact cuts against journald as a witness generally: the
  freeze left **no kernel messages at all** in the final two minutes of boot `-1`.
  pstore is the only channel that captured anything. Do not read an empty journal
  as "nothing happened".

## Next steps (ranked)

1. **Run the staged script + memtest** (command above). This is the discriminator.
   It now carries the powertop fix too, so it arms the watchdog *and* stages the
   memtest entry in one rebuild. After the rebuild and **before** rebooting into
   memtest, confirm the watchdog survives its writer:
   `sudo systemctl restart powertop.service && sysctl kernel.nmi_watchdog` → 1.
2. **If memtest is clean:** it does *not* clear memory — it clears the DIMM under
   memtest's access patterns. Before pinning an older kernel, prefer the cheaper
   discriminators: (a) `dmidecode -t memory` to establish how many sticks there
   are — if two, reseat and run one at a time, which is the only test that
   localises; (b) check for a BIOS newer than 03.17 (10/27/2022) — this is a
   2022 firmware on an 11th-gen Framework, and IMC/training fixes are exactly
   the class of thing that shows up as rare single-bit corruption; (c) *then*
   pin an older kernel. Note the rate is ~1 stop per 1–2 weeks, so a kernel
   experiment needs *weeks* — which is what `host_unclean_boots_observed` is for.
   🔴 Whatever you change, change **one** thing: at ~1 event per 1–2 weeks, two
   simultaneous changes cost a month to disambiguate.
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
- 🔴 **…and so does `powertop --auto-tune`, which runs AFTER TLP.** Fixing the
  first writer and verifying the value immediately is how #616 shipped a fix that
  read correct and was inert for weeks. The general lesson, worth more than the
  sysctl: **for any value that a daemon owns, "I set it and read it back" is not
  verification — restart every writer you know of and read it again.** Finding
  one writer is not evidence there is only one; enumerate what else runs later
  (`systemctl list-units`, ordering after `multi-user.target`).
- 🔴 **An `Oops` beyond `[#1]` is fallout — read the FIRST one.** The Aug 21
  capture has six, on three CPUs, in unrelated subsystems, spanning 20 ms. Four of
  them are the stack-trace printer itself overrunning the stack. Diagnosing from
  `[#3]` would have produced a confident, entirely wrong story about
  `__show_trace_log_lvl`. Likewise pstore's `Oops#N PartM` fragments run
  **descending in M**, so a naive `cat` reads the trace backwards.
- **Decode `Code:` and check the arithmetic closes.** Both dumps were pinned this
  way — computing the faulting address from the registers and matching it against
  the kernel's own reported address. When those agree, the decode is proven rather
  than plausible, and the register holding the corrupt value is identified
  unambiguously. It is what turns "a pointer looks wrong" into "this specific
  word differs from its correct value by one bit".
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
# 🔴 the reading above is NOT sufficient for nmi_watchdog — restart its writers:
sudo systemctl restart powertop.service && sysctl kernel.nmi_watchdog   # expect 1
sudo systemctl restart tlp.service      && sysctl kernel.nmi_watchdog   # expect 1

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
