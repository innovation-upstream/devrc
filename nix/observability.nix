{ config, pkgs, lib, ... }:

# Host telemetry -> homelab Prometheus + Loki.
#
# WHY USER-LEVEL, NOT /etc/nixos
# ------------------------------
# `services.alloy` and `services.prometheus.exporters.node` are NixOS options,
# so they would live in /etc/nixos — which is per-host, NOT in this repo, and
# needs sudo for every change. As user units they are managed here and ship to
# both hosts through `ship.sh` like everything else.
#
# Nothing here needs root: node_exporter's /proc and /sys collectors are
# world-readable, and `zach` is in `wheel`, which systemd's journal ACLs grant
# full read of the system journal (verified: `journalctl -b -1` works unprivileged).
#
# The two pieces that DO need root are deliberately excluded and staged
# separately: shipping /var/lib/systemd/pstore dumps (0600 root) and NVMe SMART.
#
# WHAT THIS IS FOR
# ----------------
# The laptop has stopped uncleanly 16 times across its retained journal, and the
# rate was never visible — it was believed to be "twice in the past month". Six
# of those were since 2026-07-29 alone. Two failures made that invisible:
#
#   1. The final minutes before each freeze were never written to disk. journald
#      syncs periodically; a hard lockup takes the RAM buffer with it. Shipping
#      the journal continuously moves those minutes OFF the box before it dies.
#   2. Nothing counted the stops. `boot-outcome` below turns that into a series.
#
# See nix/system/apply-freeze-instrumentation.sh (PR #616) for the other half:
# making the kernel capture a trace at all.

let
  # 🔴 The config is VALIDATED AT BUILD TIME, so an invalid one cannot be
  # deployed — `home-manager switch` fails instead.
  #
  # This matters more than it looks: a broken Alloy config is SILENT from the
  # consumer's side. The agent exits, the host simply stops appearing in
  # Prometheus, and "no data for this host" is indistinguishable from the hard
  # freeze this pipeline exists to detect. A deploy-time failure is loud; a
  # runtime one impersonates the very signal we are trying to measure.
  #
  # `alloy validate` (not `alloy fmt`) is required: fmt checks SYNTAX only and
  # accepted `faster_drop_reason = "..."` — a real typo in this file's first
  # draft — with rc=0. validate resolves component names, attribute names and
  # inter-component references. scripts/tests/test_obs_alloy_config.py pins that
  # distinction with negative controls.
  alloyConfig = pkgs.runCommandLocal "alloy-config-validated"
    { nativeBuildInputs = [ pkgs.grafana-alloy ]; }
    ''
      export HOME="$TMPDIR"
      # Placeholders only: validate resolves references and attribute names, and
      # never contacts an endpoint. The real values arrive at RUNTIME from the
      # gitignored EnvironmentFile — they must never enter the nix store, which
      # is world-readable.
      export OBS_PROM_URL="http://127.0.0.1:9090/api/v1/write"
      export OBS_LOKI_URL="http://127.0.0.1:3100/loki/api/v1/push"
      export OBS_USERNAME="validate"
      export OBS_PASSWORD="validate"
      export OBS_HOST="validate"

      alloy validate ${../scripts/obs/alloy.alloy}
      cp ${../scripts/obs/alloy.alloy} $out
    '';

  # node_exporter listens on loopback only. Nothing scrapes this host from
  # outside: the laptop roams, so the agent PUSHES (remote_write) rather than
  # being scraped. Binding to 0.0.0.0 would expose host metrics on every café
  # network for no benefit.
  nodeExporterPort = 9101;

  textfileDir = "${config.home.homeDirectory}/.local/state/node-exporter-textfile";
  bootOutcome = "${config.home.homeDirectory}/.local/share/obs/boot_outcome.py";
in
{
  home.file.".local/share/obs/boot_outcome.py".source = ../scripts/obs/boot_outcome.py;

  # ---------------------------------------------------------------- exporter --
  systemd.user.services.node-exporter = {
    Unit = {
      Description = "Prometheus node_exporter (loopback only)";
      After = [ "network.target" ];
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "simple";
      # PATH must be explicit: a user service does not inherit the login PATH.
      Environment = [ "PATH=${lib.makeBinPath [ pkgs.coreutils ]}" ];
      ExecStartPre = "${pkgs.coreutils}/bin/mkdir -p ${textfileDir}";
      ExecStart = lib.concatStringsSep " " [
        "${pkgs.prometheus-node-exporter}/bin/node_exporter"
        "--web.listen-address=127.0.0.1:${toString nodeExporterPort}"
        "--collector.textfile.directory=${textfileDir}"
        # thermal_zone and drm are NOT on by default. Both matter here: the
        # freeze hypotheses that remain open are thermal and GPU-adjacent.
        "--collector.thermal_zone"
        "--collector.drm"
        # Off by default and genuinely useful on a roaming laptop.
        "--collector.systemd"
        # Noise on a workstation: every docker veth churns netdev/arp series.
        "--no-collector.arp"
        "--no-collector.infiniband"
        "--no-collector.nfs"
        "--no-collector.nfsd"
      ];
      Restart = "always";
      RestartSec = 10;
    };
    Install.WantedBy = [ "default.target" ];
  };

  # ------------------------------------------------------------------- agent --
  systemd.user.services.alloy = {
    Unit = {
      Description = "Grafana Alloy — ship host metrics + journal to homelab";
      After = [ "network.target" "node-exporter.service" ];
      Wants = [ "node-exporter.service" ];
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "simple";
      Environment = [ "PATH=${lib.makeBinPath [ pkgs.coreutils ]}" ];

      # 🔴 Endpoints and credentials come from OUTSIDE the nix store, which is
      # world-readable — and this repo is PUBLIC, so they cannot be committed
      # either. The leading `-` makes it optional: a host that has not been
      # given credentials yet must not fail its switch, it just does not ship.
      EnvironmentFile = [ "-%h/.config/obs-ship/env" ];

      ExecStartPre = "${pkgs.coreutils}/bin/mkdir -p %h/.local/state/alloy";
      ExecStart = lib.concatStringsSep " " [
        "${pkgs.grafana-alloy}/bin/alloy run"
        "--storage.path=%h/.local/state/alloy"
        # Loopback-only UI, and a non-default port so it cannot collide with the
        # cluster's own Alloy conventions.
        "--server.http.listen-addr=127.0.0.1:12349"
        "${alloyConfig}"
      ];
      Restart = "always";
      # Longer than node-exporter's: a restart loop against an unreachable
      # endpoint should back off, not hammer a sleeping laptop's radio.
      RestartSec = 30;
    };
    Install.WantedBy = [ "default.target" ];
  };

  # ------------------------------------------------------- unclean-boot count --
  # Runs once shortly after boot (classifying the PREVIOUS boot, which is the
  # one that just ended badly), then daily so the series does not go stale on a
  # host that stays up for weeks.
  systemd.user.services.boot-outcome = {
    Unit = {
      Description = "Classify whether the previous boot ended cleanly";
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "oneshot";
      Environment = [ "PATH=${lib.makeBinPath [ pkgs.systemd pkgs.coreutils ]}" ];
      ExecStart = "${pkgs.python3}/bin/python3 ${bootOutcome} --textfile ${textfileDir}/boot_outcome.prom";
    };
  };

  systemd.user.timers.boot-outcome = {
    Unit.Description = "Classify the previous boot shortly after startup, then daily";
    Timer = {
      # 2min: let journald settle and the previous boot's tail be readable.
      OnBootSec = "2min";
      OnUnitActiveSec = "1d";
      Persistent = true;
    };
    Install.WantedBy = [ "timers.target" ];
  };
}
