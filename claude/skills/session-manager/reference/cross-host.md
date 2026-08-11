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
  `tmux list-windows -a -F '#{window_id}|#{window_index}|#{session_name}'`.

🔴 **Those two calls are two INDEPENDENT measurements, and each publishes its own verdict.**
`reachable` / `error` describe **list-panes**; `windows_measured` / `windows_error` describe
**list-windows**. Reading one off the other is how a failed `list-windows` became invisible:
`live_window_ids` published as a measured `[]`, every fuzzyclaw task dropped,
`claude_session_id` went null and the run still said `status: "ok"`, exit `0`. When
`list-windows` did not answer, `live_window_ids` is `null` — never `[]`.

## Failure taxonomy — three outcomes, never two

| what happened | `reachable` | `error` | rendered as |
|---|---|---|---|
| panes returned | `true` | `null` | the rows |
| `tmux` exits non-zero saying *"no server running"* | `true` | `null` | a **real** zero |
| ssh/tmux failed, timed out, or refused | `false` | the stderr | `UNREACHABLE` + *"this is NOT zero windows"* |

And for the window list, independently:

| what happened | `windows_measured` | `live_window_ids` | rendered as |
|---|---|---|---|
| the window list returned | `true` | the ids | (nothing special) |
| `list-windows` failed while `list-panes` succeeded | `false` | `null` | `WINDOW LIST UNMEASURED` + *"this is NOT zero live windows"* |

A host that fails is **visible in both output modes** and the scan still exits `0` with the
other host's data — partial data is reported as partial, not silently dropped. A run where
*no* host answered exits `4`, never `0` and never `3`.

`tail` has outcomes the scan does not, because it targets ONE window:

| what happened | `reachable` | `found` | `no_server` | exit |
|---|---|---|---|---|
| the capture came back with text | `true` | `true` | `false` | `0` |
| the window exists, scrollback empty | `true` | `true` | `false` | `3` |
| the host answered: no such window | `true` | `false` | `false` | `2` |
| the host answered: **no tmux server at all** | `true` | `false` | `true` | `5` |
| the host said nothing | `false` | `null` | `false` | `4` |

🔴 Row 4 is why `no_server` exists. The table above says *"no server running"* is a
**reachable** host — right for a scan, where it means a live host with zero windows. But
`tail` took the success branch on it and published `found: true, text: ""` → exit `3`,
indistinguishable from row 2, which is the only thing exit `3` is documented to mean. So
`run_tmux` now carries a `no_server` discriminant and `tail_window` branches on it **before**
the reachable return.

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
