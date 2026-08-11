# session-manager — cross-host SSH

## 🔴 The laptop is `zach@10.42.0.100`, NOT `10.42.0.10`

`10.42.0.100` is the **laptop** on nebula. `10.42.0.10` is the **homelab gateway**.

This is the one error in this subsystem that does not fail loudly: pointing at the gateway
makes SSH succeed against a real, reachable host, which then reports *its* tmux state as
the laptop's. Nothing errors, nothing is empty, and the output looks entirely plausible.
So the literal is pinned by a test asserting equality (`host == "10.42.0.100"`), not by a
substring check — `"10.42.0.10" not in target` is satisfied by the correct value too and
would be a guard that cannot fail.

Precedent for the address: `claude/skills/standup/standup.sh:26` (`LAP="zach@10.42.0.100"`),
the repo's only pre-existing one.

The LAN address `192.168.50.155` reaches the laptop **only when both hosts are on the same
network**, which they usually are not. A LAN-first probe therefore buys a connect timeout on
every scan for a benefit that is normally unavailable, so it is not implemented.

## How the remote call is made

```
ssh -o BatchMode=yes -o ConnectTimeout=4 -o StrictHostKeyChecking=accept-new \
    zach@10.42.0.100 '<shlex-joined argv>'
```

- `BatchMode=yes` — an SSH prompt can never hang a scan.
- `ConnectTimeout=4`, plus a 12s overall subprocess timeout.
- The argv is `shlex.join`ed because the remote side runs a **shell**: the tmux format
  string contains `{`, `}`, `#` and `|` and must arrive quoted or the remote `tmux` receives
  a mangled `-F`.
- Two calls per host: `tmux list-panes -a -F <8-field format>` and
  `tmux list-windows -a -F '#{window_id}'`.

## Failure taxonomy — three outcomes, never two

| what happened | `reachable` | `error` | rendered as |
|---|---|---|---|
| panes returned | `true` | `null` | the rows |
| `tmux` exits non-zero saying *"no server running"* | `true` | `null` | a **real** zero |
| ssh/tmux failed, timed out, or refused | `false` | the stderr | `UNREACHABLE` + *"this is NOT zero windows"* |

A host that fails is **visible in both output modes** and the scan still exits `0` with the
other host's data — partial data is reported as partial, not silently dropped. A run where
*no* host answered exits `4`, never `0` and never `3`.

## What does NOT cross the wire

fuzzyclaw task files are local state and are read on the local host only. A remote window
therefore carries `fuzzyclaw: null`, `claude_session_id: null` and `age_secs: null`, and is
classified busy/idle from its title glyph alone — never `stale`, because no age was
measured. Joining the local host's task files onto same-named remote sessions would
fabricate facts; a test pins that it does not happen.

## Local host identity

`hostname` is `nixos` on **both** machines, so it cannot disambiguate them. The local label
comes from `ACTIVITY_HOST` — the process env first, then `~/.config/activity-collector/env`
— and defaults to `workbench`. The host matching that label is queried locally; the other is
queried over SSH.
