# Hand patch: two stale lines in `/etc/nixos/configuration.nix`

**Staged, not applied.** Two edits per host, both inside
`systemd.tmpfiles.rules`. Read the diff, make the edits yourself, then
`sudo nixos-rebuild boot` (see the caveat at the bottom about `switch`).

## Why this is a hand patch and not code

`apply-tmp-churn-retention.sh` used to do this automatically, by evicting
"stale" rule lines. That code was removed on 2026-09-04 after three adversarial
audit rounds, because:

- **Its premise was false.** Eviction defended against a stale line sitting
  *above* the corrected one and winning (systemd takes the first line per path
  and logs `Duplicate line for path …, ignoring`). But the script inserts at the
  **top** of the list, so it cannot produce that ordering. A surviving stale line
  is cruft plus one log line — not a dead rule.
- **It cost two 🔴 regressions in three rounds**, each introduced by the fix for
  the previous finding. An unanchored substring splice silently commented out an
  unrelated live rule while leaving the stale one in place; a whole-line scan for
  the closing bracket walked into a following `systemd.user.tmpfiles.rules` and
  deleted an entry there. Both runs reported success and passed their own
  verifier.
- **The population is two machines.** Regex-rewriting a hand-maintained config as
  root is not worth it at that scale.

## The two edits

### 1. The comment header contradicts the rules beneath it

`/etc/nixos/configuration.nix:582` (workbench, measured 2026-09-04) reads:

```
    # /tmp churn retention (2026-08-15). mtime-ONLY ageing (`m:`), because
```

directly above eight `mM:7d` rules. `m:` is the spelling that ages **no
directory at all** — lower-case selects the timestamp for files, upper-case for
directories, and naming `m` alone gives directories no criterion. The sentence
describes the bug the rules below it were corrected to avoid, so a maintainer
reading it would "fix" the rules back to the broken spelling.

Replace lines 582–586 with:

```
    # /tmp churn retention (2026-08-15, age-by corrected 2026-09-02). mtime-only
    # ageing for BOTH files and directories (`mM:`) — lower-case covers files only,
    # which ages no directory at all. systemd-tmpfiles otherwise ages on the newest
    # of atime/mtime/ctime, and any `du`/`find` over /tmp refreshes atime, which is
    # why the stock 10d rule never expires anything. Scoped to machine-generated
    # prefixes ONLY: a blanket rule would delete live git worktrees parked in /tmp.
    # See nix/system/apply-tmp-churn-retention.sh.
```

### 2. Delete the dead `homelab-talos-prs-*` rule

`/etc/nixos/configuration.nix:593`:

```
    "e /tmp/homelab-talos-prs-* - - - mM:7d"
```

Delete that line. It was a dead rule for its entire life: `e` acts on a
**directory's** contents and silently ignores a plain file, and every match is a
plain file. The producer is `homelab-talos/flake.nix` —
`_pr_cache="/tmp/homelab-talos-prs-$EUID"`, written with `echo … > "$_pr_cache"`.
Measured 2026-09-02 on live `/tmp`: **821 matches, 0 directories, 821 files.**

It was withdrawn from the repo's ledger in this PR, but withdrawing a glob does
not remove it from a host that already applied it — nothing does, which is why
this step exists.

## Verify after editing

```bash
grep -c 'mtime-ONLY ageing'   /etc/nixos/configuration.nix   # expect 0
grep -c 'age-by corrected'    /etc/nixos/configuration.nix   # expect 1
grep -c 'homelab-talos-prs'   /etc/nixos/configuration.nix   # expect 0
grep -c 'mM:7d'               /etc/nixos/configuration.nix   # expect 7
grep -c ' m:7d'               /etc/nixos/configuration.nix   # expect 0
```

Then re-run the script; it must report `all 7 rules already present — nothing to
insert` and leave the file byte-identical.

## The laptop is UNMEASURED

Its `/etc/nixos` is not readable from the workbench. Run the same five checks
there. If `mtime-ONLY ageing` or `homelab-talos-prs` appear, apply the same two
edits; if the rules are absent entirely, the host never ran the script and needs
`sudo bash nix/system/apply-tmp-churn-retention.sh` instead.

## Applying it

🔴 **`nixos-rebuild switch` was BLOCKED on the workbench on 2026-09-02** by a
`switchInhibitors` pre-switch check on an unrelated `dbus-implementation :
dbus -> broker` channel migration. That is not caused by this change. Use
`sudo nixos-rebuild boot` and reboot when convenient; do **not** reach for
`NIXOS_NO_CHECK=1` on a box running k3s.

Until a rebuild happens, `/etc/tmpfiles.d/00-nixos.conf` carries none of these
rules — the config is edited but nothing is live.
