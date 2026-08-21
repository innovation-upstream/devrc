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

## Privacy

The journal here is not neutral telemetry — it carries desktop-notification
bodies, database errors echoing query values, and object-store credential
errors. `alloy.alloy` drops `dunst` lines entirely and redacts credential-shaped
assignments before anything is written. Scrubbing runs **before** the Loki
write, and `test_obs_alloy_config.py` pins that ordering.

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
