# Host telemetry → homelab Prometheus + Loki

Ships this host's metrics and journal to the homelab observability stack, so
that when the machine stops, the evidence is already off the box.

Deployed by `nix/observability.nix` as **user** systemd units (no sudo, ships to
both hosts via `ship.sh`).

## Why

The laptop stopped uncleanly **16 times** across its retained journal — six of
them since 2026-07-29 — and it was believed to have frozen "twice in the past
month". Two things made that invisible:

1. **The last minutes never reached disk.** journald buffers, and a hard lockup
   takes the buffer with it. Shipping continuously moves those minutes off the
   host before it dies.
2. **Nothing counted the stops.** `boot_outcome.py` turns them into a series.

The other half of the problem — that the kernel recorded no trace at all,
because TLP disables the NMI hard-lockup detector — is
`nix/system/apply-freeze-instrumentation.sh`.

## One-time setup per host

Endpoints and credentials live **outside the repo entirely** (it is public) and
**outside the nix store** (world-readable). Create, on each host:

```bash
mkdir -p ~/.config/obs-ship
cat > ~/.config/obs-ship/env <<'EOF'
OBS_PROM_URL=http://<prometheus-host>:<port>/api/v1/write
OBS_LOKI_URL=http://<loki-host>:<port>/loki/api/v1/push
OBS_USERNAME=<user>
OBS_PASSWORD=<pass>
OBS_HOST=workbench          # or: laptop
EOF
chmod 600 ~/.config/obs-ship/env
systemctl --user restart alloy
```

The homelab Prometheus already has `enableRemoteWriteReceiver: true`, so it
accepts `remote_write` directly — no scrape target or ingress needed. Reach it
over the Nebula mesh.

`EnvironmentFile` is optional (`-` prefixed): a host without this file still
switches cleanly, it just does not ship. That is deliberate — a missing
credential must not break a deploy.

## Verify

```bash
systemctl --user status alloy node-exporter
curl -s localhost:9101/metrics | grep -c '^node_'        # exporter is up
curl -s localhost:9101/metrics | grep '^host_'           # boot-outcome metrics
journalctl --user -u alloy -n 20                         # shipping errors, if any
```

In Grafana, `host_boot_previous_clean == 0` means the last boot ended abruptly.

## What it emits

| metric | meaning |
|---|---|
| `host_boot_previous_clean` | 1 = previous boot reached systemd's shutdown sequence, 0 = stopped abruptly |
| `host_boot_previous_end_timestamp_seconds` | when the previous boot's journal ends |
| `host_unclean_boots_observed` | unclean endings across the retained journal |
| `host_boots_examined` | boots actually classified — **read this beside the count above** |
| `host_boot_outcome_scrape_ok` | 0 = could not measure; the counts are then absent, not zero |

🔴 `host_unclean_boots_observed` is only interpretable next to
`host_boots_examined`. A zero from a scan that walked nothing is a failure, not
an all-clear, which is why the classifier omits the count entirely rather than
emitting `0` when it cannot measure.

"Unclean" is a **symptom count, not a diagnosis**: it covers a hard lockup, but
also a flat battery, a held power button, and a panic.

## Privacy — a transport allowlist, not pattern-matching

**Only `kernel`, `journal` and `syslog` are shipped. `stdout` is dropped.**

That is the guarantee. It is a `keep` rule, so it is default-DENY: a transport
that does not exist yet is dropped rather than shipped.

Measured on this host (7 days, ~98k lines): `stdout` is **69.9%** of the journal
and carries every content class that made shipping logs risky — container
output, database errors echoing query values, object-store credential errors,
agent tooling. The freeze evidence lives in `kernel` (1.9%) and `journal`
(24.1%), which are kept.

Why not redaction alone: five successive audit rounds each found a defect in the
credential-redaction regexes, and three of those were introduced by the
preceding fix. A denylist has to enumerate every shape a secret can take. The
regex stages are still there as **defence-in-depth** for the retained 30%, and
still run before the Loki write — but they are no longer the guarantee.

Verified with a positive control, not just by reading the config: in a live run
the source window held 57 `stdout` entries and the pipeline shipped **0** of
them, while keeping all 30 `journal`, 1 `syslog` and 1 `kernel`.

**Cost:** no application-level context around a freeze. Workload state is
covered by metrics instead (PSI, thermals, memory, the boot-outcome counter).

`dunst` notification bodies are dropped separately by identifier, since they
arrive on a retained transport.

## Not covered here (needs root)

- Shipping `/var/lib/systemd/pstore/*` crash dumps (0600, root-owned)
- NVMe SMART counters

Both would follow the staged `nix/system/apply-*.sh` pattern.

## Config validity

`alloy.alloy` is validated **at build time** by `nix/observability.nix`, so an
invalid config fails `home-manager switch` rather than silently taking the agent
down — a dead agent looks exactly like a frozen host, which is the one thing
this pipeline must not be ambiguous about.

Use `alloy validate`, never `alloy fmt`: fmt checks syntax only and accepted a
real `faster_drop_reason` typo with rc=0.
