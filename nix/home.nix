{ config, pkgs, lib, isNixOS ? false, ... }:

let
  home = config.home.homeDirectory;
  workspace = "${home}/workspace";
  # Server mode: `touch ~/.server-mode`. Historically this ALSO gated the graphical
  # services (dunst, espanso) off — but the workbench carries the marker to enable
  # its server-side tasks (mail-actions-archive, repo-cos) while STILL running a full
  # X/i3 desktop, so gating the desktop bits on serverMode wrongly disabled them
  # there (same trap the i3 bar hit — see graphical.nix). serverMode now gates ONLY
  # server-side task enablement; graphical services key off `graphical` below.
  serverMode = builtins.pathExists "${home}/.server-mode";
  # Initiatives-sync (Phase 1) master switch — gates only whether the TIMER is wired
  # into timers.target; the service definition is always emitted (so it can be started
  # by hand). Kept OFF through the initial supervised validation so a routine deploy
  # (ship.sh / home-manager switch) could never silently enable an unvalidated
  # prod-write timer. ENABLED now: the first supervised live write validated the
  # DDL/insert path (snapshot #1 wrote 23 rows to prod, telemetry-on, and the DSN role
  # is confirmed to have CREATE SCHEMA). The timer runs hourly (see the timer below).
  enableInitiativesSync = true;
  # Graphical host = runs X/i3 (both current NixOS hosts do; only a genuinely headless
  # box would not). Approximated as isNixOS, mirroring graphical.nix — deliberately NOT
  # !serverMode, which is true on the graphical workbench.
  graphical = isNixOS;
  # TEMPORARY diagnostic: the keylog CPU-spin stack capture (service + timer,
  # see keylog-spin-capture below). Gated as its own flag rather than reusing
  # `graphical` because enabling it drags an UNCACHED from-source py-spy build
  # (Rust + libunwind) into every `home-manager switch` on that host, and a
  # build failure there fails the whole switch. Flip to false to opt a host out
  # without touching the unit definitions. DELETE the flag and the units once
  # the spin is root-caused and the CPUQuota on keylog.service comes off.
  # NOT on the laptop: it reads kernel.yama.ptrace_scope=1 (workbench reads 0,
  # verified 2026-07-30), so py-spy can never attach to keylog.service there and
  # the unit would exit at the sysctl gate every 5min forever. Worse, merely
  # ENABLING it drags the uncached from-source py-spy build (Rust + libunwind)
  # into the laptop's `home-manager switch` — and a build failure there fails
  # the whole switch, which would stop the laptop converging to origin/main for
  # a workbench-only diagnostic. (`isLaptop` is defined below; `let` bindings
  # are order-independent.)
  enableKeylogSpinCapture = graphical && !isLaptop;
  # Host discriminator for the graphical config (i3 + i3status-rust bar). Evaluated
  # per-host under `--impure`: the laptop has an intel_backlight, the workbench does
  # not. Threaded into ./graphical.nix via _module.args below. Drives battery/backlight
  # (laptop) vs rig-control/DDC (workbench). Do NOT use serverMode for this — it is
  # true on the graphical workbench (it only gates dunst/espanso there).
  isLaptop = builtins.pathExists "/sys/class/backlight/intel_backlight";
  userPackages = import ./pkgs { inherit pkgs workspace; };
  sessionVariables = import ./sessionVariables.nix {
    inherit pkgs;
    elixirLspPath = pkgs.vscode-extensions.elixir-lsp.vscode-elixir-ls;
    playwrightBrowsersPath = pkgs.playwright-driver.browsers;
    homePath = home;
  };
  programs = import ./programs { inherit pkgs config; };
in
{
  # Graphical (i3 + i3status-rust bar) config lives in ./graphical.nix; isLaptop is
  # threaded to it as a module arg so it can branch battery/backlight vs rig/DDC.
  imports = [ ./graphical.nix ];
  _module.args.isLaptop = isLaptop;

  programs = programs;

  # Espanso text expander service (X11/i3)
  services.espanso = {
    enable = graphical;
    package = pkgs.espanso;
    x11Support = true;
    waylandSupport = false;

    configs = {
      default = {
        # ALT+SPACE conflicts with i3 focus mode_toggle, use CTRL instead
        search_shortcut = "CTRL+SPACE";
        backend = "Clipboard";
      };
    };

    matches = {
      base = {
        matches = [
          # Date/time with labels for search bar (ALT+SPACE)
          { trigger = ":date"; replace = "{{mydate}}"; label = "Today's date"; search_terms = ["today" "calendar"]; vars = [{ name = "mydate"; type = "date"; params = { format = "%Y-%m-%d"; }; }]; }
          { trigger = ":time"; replace = "{{mytime}}"; label = "Current time"; search_terms = ["now" "clock"]; vars = [{ name = "mytime"; type = "date"; params = { format = "%H:%M"; }; }]; }
          { trigger = ":datetime"; replace = "{{mydt}}"; label = "Date and time"; search_terms = ["timestamp"]; vars = [{ name = "mydt"; type = "date"; params = { format = "%Y-%m-%d %H:%M"; }; }]; }
          { trigger = ":iso"; replace = "{{myiso}}"; label = "ISO 8601 timestamp"; search_terms = ["utc" "rfc"]; vars = [{ name = "myiso"; type = "date"; params = { format = "%Y-%m-%dT%H:%M:%S%z"; }; }]; }

          # Paths - labeled for autocomplete
          { trigger = ":hlt"; replace = "${workspace}/homelab-talos "; label = "homelab-talos path"; search_terms = ["infra"]; }
          { trigger = ":kuc"; replace = "${workspace}/kubeclaw "; label = "kubeclaw path"; search_terms = ["kubeclaw"]; }
          { trigger = ":nixos"; replace = "/etc/nixos/configuration.nix"; label = "nixos config"; search_terms = ["nixos" "configuration"]; }

          # SSH connect
          { trigger = ":sshwn"; replace = "ssh zach@10.42.0.30"; label = "SSH workbench (nebula)"; search_terms = ["ssh" "workbench" "wb" "nebula" "mesh" "remote"]; }
          { trigger = ":sshwl"; replace = "ssh zach@192.168.50.250"; label = "SSH workbench (LAN)"; search_terms = ["ssh" "workbench" "wb" "lan" "local"]; }
          { trigger = ":sshln"; replace = "ssh zach@10.42.0.100"; label = "SSH laptop (nebula)"; search_terms = ["ssh" "laptop" "framewo" "nebula" "mesh" "remote"]; }
          { trigger = ":sshll"; replace = "ssh zach@192.168.50.155"; label = "SSH laptop (LAN)"; search_terms = ["ssh" "laptop" "framewo" "lan" "local"]; }

          # hot singles
          { trigger = "dashbaord"; replace = "dashboard"; }
          { trigger = "reocmmend"; replace = "recommend"; }

          # Workflows
          # (:whn removed 2026-07-06 — 0 fires over the audit window, superseded by
          #  /handoff + :eos which ends "…then write the handoff to proceed in next session")
          { trigger = ":eos"; replace = "reflect on work done and key learnings this session then identify skills and documentation that may need updating, then dispatch subagent to update those skills and any relevant docs, then do the handoff to proceed in next session"; label = "End-of-session ritual: review → update skills/docs → handoff"; search_terms = ["end" "session" "wrap" "handoff" "skills" "review" "update" "docs" "ritual"]; }
          { trigger = ":acq"; replace = "dispatch subagent to process feedback\nask me clarifying questions and recommend anything you think would be useful to include before dispatching (include complete test coverage)"; label = "Ask clarifying questions + suggest additions"; search_terms = ["ask" "clarify" "clarifying" "questions" "elicit" "scope" "include"]; }
          { trigger = ":kickoff"; replace = "give me the kickoff message to copy paste to next session"; label = "Kickoff message for next session"; search_terms = ["kickoff" "kick off" "next session" "copy paste" "handoff" "message"]; }
          # Removed 2026-07-25 via /espanso-audit — all keylog-evidence-backed:
          #  ZERO-FIRE set — 0 keylog fires + short-form hand-typing; steering already in
          #   RULES.md / slash-commands: :rnx, :pst ("proceed, dispatch" typed 40+×),
          #   :aep (/audit-pr invoked directly 39×), re-check trio :nday/:fhrs/:fdays.
          #  TYPING-TOIL set — keylog typing-stream shows the shortcut EXISTED but the long
          #   form was hand-typed instead (trigger→habit transfer failed; short mid-sentence
          #   prefixes don't stick for a search-first espanso user):
          #   :ds (1 fire vs 94 hand-typed; duplicates the RULES "dispatch subagent" default),
          #   :rns (1 vs 20; overlaps /resume). See claudedocs/espanso-typing-toil-2026-07-25.md.

          { trigger = ":cc"; replace = "${workspace}/civit/civitai "; label = "civitai main repo path"; search_terms = ["civitai" "repo" "web"]; }
          { trigger = ":cdp"; replace = "${workspace}/civit/datapacket-talos "; label = "civitai datapacket-talos path"; search_terms = ["civitai"]; }
          { trigger = ":cgf"; replace = "${workspace}/civit/civitai-gpu-fleet "; label = "civitai gpu-fleet path"; search_terms = ["civitai"]; }
          { trigger = ":cmo"; replace = "${workspace}/civit/civitai-orchestration "; label = "civitai-orchestration path"; search_terms = ["civitai" "orchestration"]; }
          { trigger = ":csc"; replace = "${workspace}/civit/civitai-spine-controller "; label = "civitai-spine-controller path"; search_terms = ["civitai" "spine controller" "spine"]; }
          { trigger = ":cpk"; replace = "${workspace}/civit/datapacket-talos/prod-kubeconfig "; label = "civitai dp prod kubeconfig path"; search_terms = ["civitai"]; }
          { trigger = ":subk"; replace = "${workspace}/civit/civitai-gpu-fleet/submodel-dc-03-a-kubeconfig "; label = "civitai submodel dc 03 kubeconfig path"; search_terms = ["civitai" "gpu" "submodel" "dc 03"]; }

          # Utilities
          { trigger = ":uuid"; replace = "{{uuid}}"; label = "Generate UUID"; search_terms = ["guid" "random"]; vars = [{ name = "uuid"; type = "shell"; params = { cmd = "uuidgen"; }; }]; }
          { trigger = ":clip"; replace = "{{clip}}"; label = "Paste from clipboard"; vars = [{ name = "clip"; type = "clipboard"; }]; }
        ];
      };
    };
  };

  # Compositor disabled — NVIDIA forceFullCompositionPipeline handles vsync/tearing
  # picom conflicts with NVIDIA's composition pipeline causing workspace switch flicker
  # services.picom = {
  #   enable = true;
  #   backend = "glx";
  #   vSync = true;
  # };

  # Notification daemon (Gruvbox-themed, CALM). Value formats verified parse-clean
  # against dunst 1.13.2 (the running version): `width`/`offset` take a paren-tuple
  # `(min, max)` / `(x, y)` (v1.11- used NxN); the home-manager module quotes string
  # values, and dunst strips those quotes before enum/tuple parsing (confirmed via a
  # control reload that DID warn on a bogus value but not on these). After a switch,
  # re-inspect ~/.config/dunst/dunstrc + `journalctl --user -u dunst` for warnings.
  services.dunst = {
    enable = graphical;
    settings = {
      global = {
        # Match the i3 bar font so nerd-font glyphs in notification bodies render.
        font = "JetBrainsMono Nerd Font 10";
        frame_color = "#504945";
        separator_color = "frame";
        corner_radius = 4;
        # Placement: top-right, offset down far enough to clear the ~24-34px top bar.
        origin = "top-right";
        offset = "(12, 40)";
        # Bounded, content-sized width (grows to 420px max, never wider).
        width = "(0, 420)";
        # Cap the visible stack + keep a recall buffer (dunstctl history-pop).
        notification_limit = 4;
        history_length = 40;
        stack_duplicates = true;      # collapse repeats into one with a counter
        show_indicators = false;      # no "(x more)" / action hints — calmer
        # Mouse: left dismisses the current toast, middle fires its action then
        # dismisses, right opens the dunst context menu.
        mouse_left_click = "close_current";
        mouse_middle_click = "do_action, close_current";
        mouse_right_click = "context";
      };
      urgency_low = {
        background = "#282828";
        foreground = "#ebdbb2";
        frame_color = "#504945";
        timeout = 5;
      };
      urgency_normal = {
        background = "#282828";
        foreground = "#ebdbb2";
        frame_color = "#83a598";      # gruvbox blue accent
        timeout = 10;
      };
      urgency_critical = {
        background = "#cc241d";        # gruvbox red bg
        foreground = "#ebdbb2";
        frame_color = "#fb4934";       # bright-red frame
        timeout = 0;                   # sticky until dismissed
      };
      # Native fullscreen DND: a filterless rule matches all notifications and,
      # while a fullscreen window (video/games/screen-share) is focused, routes
      # toasts STRAIGHT TO HISTORY — nothing shows, nothing accumulates, and
      # nothing dumps on exit. Recall missed ones with $mod+n (history-pop).
      # NOTE: `pushback` (the prior value) instead PAUSES each toast's expiry
      # timer while fullscreen, so on a workbench that's fullscreen a lot they
      # never expired and piled up — `suppress` is the calm-but-no-pile-up fix.
      # Urgent agent approvals still reach the phone via clawgate push.
      fullscreen_suppress = {
        fullscreen = "suppress";
      };
    };
  };

  # Workaround: ensure espanso config directory exists
  home.activation.espansoConfigDir = lib.hm.dag.entryAfter ["writeBoundary"] ''
    mkdir -p ~/.config/espanso/config
  '';

  # Seed the activity-collector EnvironmentFile with safe defaults if it does not
  # exist yet. The real file holds the (future) ClickHouse credentials, so it is
  # NEVER in the nix store and NEVER committed — created here once, chmod 600,
  # then edited in place. We copy the in-repo .env.example as the template.
  home.activation.activityCollectorEnv = lib.hm.dag.entryAfter ["writeBoundary"] ''
    envFile="$HOME/.config/activity-collector/env"
    if [ ! -e "$envFile" ]; then
      mkdir -p "$HOME/.config/activity-collector"
      cp ${../scripts/collector/.env.example} "$envFile"
      chmod 600 "$envFile"
      echo "activity-collector: seeded $envFile from .env.example (edit to add CLICKHOUSE_PASSWORD)"
    fi
  '';

  home.stateVersion = "24.11";

  home.packages = if isNixOS
  then
    userPackages ++ [pkgs.autorandr pkgs.ddcutil pkgs.yad]
  else
    userPackages;

  home.sessionVariables = sessionVariables;

  home.sessionPath = [
    "${home}/.local/bin"
    "${home}/go/bin"
    "${home}/.npm-packages/bin"
  ];

  # Default browser: Brave (Chromium-based). Declaratively own the web
  # scheme/mime handlers so both hosts agree; matches the $mod+b i3 launcher
  # and the activity-collector's BROWSER_APP=brave labelling.
  xdg.mimeApps = {
    enable = true;
    defaultApplications = {
      "text/html" = "brave-browser.desktop";
      "application/xhtml+xml" = "brave-browser.desktop";
      "x-scheme-handler/http" = "brave-browser.desktop";
      "x-scheme-handler/https" = "brave-browser.desktop";
      "x-scheme-handler/about" = "brave-browser.desktop";
      "x-scheme-handler/unknown" = "brave-browser.desktop";
      # Default file manager: nemo (the repo's packaged nemo-with-extensions;
      # cf. the GTK_THEME=Adwaita-dark nemo alias). Own inode/directory so
      # "open folder" resolves declaratively rather than via desktop-file scan.
      "inode/directory" = "nemo.desktop";
    };
  };

  # Symlink tmux scripts
  home.file.".config/tmux/idle-update.sh" = {
    source = ../scripts/tmux-idle-update.sh;
    executable = true;
  };
  home.file.".config/tmux/pipe-activity.sh" = {
    source = ../scripts/tmux-pipe-activity.sh;
    executable = true;
  };
  home.file.".config/tmux/activity-receiver.sh" = {
    source = ../scripts/tmux-activity-receiver.sh;
    executable = true;
  };
  home.file.".config/tmux/task-hook.sh" = {
    source = ../scripts/tmux-task-hook.sh;
    executable = true;
  };
  home.file.".config/tmux/task-resume.sh" = {
    source = ../scripts/tmux-task-resume.sh;
    executable = true;
  };
  # Canonical scratchpad slot table (session<->hotkey<->color<->codename), sourced by
  # scratch-monitor/initiatives/status; must sit beside them under ~/.config/tmux/.
  home.file.".config/tmux/scratch-slots.sh" = {
    source = ../scripts/tmux-scratch-slots.sh;
  };
  home.file.".config/tmux/scratch-picker.sh" = {
    source = ../scripts/tmux-scratch-picker.sh;
    executable = true;
  };
  home.file.".config/tmux/scratch-status.sh" = {
    source = ../scripts/tmux-scratch-status.sh;
    executable = true;
  };
  home.file.".config/tmux/scratch-monitor.sh" = {
    source = ../scripts/tmux-scratch-monitor.sh;
    executable = true;
  };
  home.file.".config/tmux/claude-counters.sh" = {
    source = ../scripts/tmux-claude-counters.sh;
    executable = true;
  };
  # agent-ops "mission control" popup (prefix+A). Renders over the existing
  # deterministic sources (bar-status cache + a live tmux/process scan + a
  # TTL-cached initiative-scan) — see scripts/agent-ops.
  home.file.".config/tmux/agent-ops" = {
    source = ../scripts/agent-ops;
    executable = true;
  };

  home.file.".config/tmux/activity-emit.sh" = {
    source = ../scripts/tmux-activity-emit.sh;
    executable = true;
  };

  # CPU load monitor: desktop alert on sustained high load
  home.file.".config/cpu-monitor/cpu-monitor.sh" = {
    source = ../scripts/cpu-monitor.sh;
    executable = true;
  };

  # systemd-unit failure handler: the ExecStart of the notify-failure@ template
  # unit below. Emits a sticky desktop toast pointing at the failed unit's
  # journal (headless-safe: logs + exits 0 when no X/dunst). Symlinked from the
  # repo so both hosts stay in sync (like cpu-monitor.sh above).
  home.file.".config/notify-failure/notify-failure.sh" = {
    source = ../scripts/notify-failure.sh;
    executable = true;
  };

  # Activity-telemetry collector: hot-path emit helper + daemon. Symlinked from
  # the repo so both hosts stay in sync. Config (CLICKHOUSE_URL/credentials) lives
  # in ~/.config/activity-collector/env — created below, NOT in the nix store.
  home.file.".config/activity-collector/emit" = {
    source = ../scripts/collector/emit;
    executable = true;
  };
  home.file.".config/activity-collector/collector.py" = {
    source = ../scripts/collector/collector.py;
    executable = true;
  };

  # Claude Code activity source (5th source): a periodic tailer that scans the
  # ~/.claude transcripts and emits NEW user-typed messages / slash-commands as
  # source=claude events via the shared emit helper. Symlinked recursively so the
  # tailer lands at ~/.config/activity-collector/claude/tailer.py and resolves its
  # sibling emit at ~/.config/activity-collector/emit (two dirs up). Driven by a
  # systemd user TIMER (below), not Restart=always — it is a periodic oneshot.
  home.file.".config/activity-collector/claude" = {
    source = ../scripts/collector/claude;
    recursive = true;
  };

  # OpenCode activity source (6th source): tailer that scans OpenCode
  # transcripts and emits events via the shared emit helper. Driven by a
  # systemd user timer (below).
  home.file.".config/activity-collector/opencode" = {
    source = ../scripts/collector/opencode;
    recursive = true;
  };

  # GUI activity collectors (keylogger + browser receiver). The whole module
  # dir is symlinked recursively so the daemons can import their sibling modules
  # (keymap/chunker/winctx/spool_emit). The browser receiver reuses keylog's
  # spool_emit (single source of truth for the v1 line format), so keylog/ must
  # be present even on a browser-only host.
  home.file.".config/activity-collector/keylog" = {
    source = ../scripts/collector/keylog;
    recursive = true;
  };
  home.file.".config/activity-collector/browser-ext" = {
    source = ../scripts/collector/browser-ext;
    recursive = true;
  };
  # browser-bridge server (SIBLING to the activity receiver above — a *command*
  # channel that lets a Claude skill drive the live Brave tab, NOT telemetry).
  # Deployed as a single-file symlink into ~/.config/browser-bridge/ so the
  # runtime-created, writable ~/.config/browser-bridge/token can live alongside
  # the read-only nix-store server.py (symlinking the whole dir would collide
  # with that token file). server.py is stdlib-only + standalone (no sibling
  # imports), so unlike the receiver it has NO don't-.resolve() import quirk.
  home.file.".config/browser-bridge/server.py".source = ../scripts/browser-bridge/server.py;
  # i3 focus collector. Reuses keylog's spool_emit (the v1 line format), so
  # keylog/ must be present alongside it (it always is — shipped above).
  home.file.".config/activity-collector/i3" = {
    source = ../scripts/collector/i3;
    recursive = true;
  };

  # Global Claude Code behavioural config — single source of truth for both
  # hosts (these were drifting when edited per-host). Synced via scripts/ship.sh.
  # NOTE: now read-only symlinks into the nix store → edit `devrc/claude/*.md`
  # (or devrc/claude/commands/*.md) then `home-manager switch` (or ship.sh),
  # NOT `~/.claude/*.md` directly.
  # CLAUDE.md stays per-host/mutable (host-specific). skills/ are now MANAGED like
  # commands/ (read-only store symlinks, below) so they ship to all hosts — edit in
  # devrc/claude/skills/ then `home-manager switch` (or ship.sh), NOT ~/.claude/skills/.
  home.file.".claude/RULES.md" = {
    source = ../claude/RULES.md;
    force = true;  # overwrite the pre-existing unmanaged file on first switch
  };
  home.file.".claude/PRINCIPLES.md" = {
    source = ../claude/PRINCIPLES.md;
    force = true;
  };
  # Slash-commands — recursively symlinked so each command lands individually at
  # ~/.claude/commands/<name>.md as a read-only store symlink.
  # Edit in devrc/claude/commands/ then switch; both hosts stay in lockstep.
  home.file.".claude/commands" = {
    source = ../claude/commands;
    recursive = true;
    force = true;
  };
  # Skills — same pattern as commands: each file under devrc/claude/skills/<name>/
  # lands as a read-only store symlink at ~/.claude/skills/<name>/, so skills ship
  # to all hosts in lockstep. Edit in devrc/claude/skills/ then switch. (Previously
  # per-host/mutable, which meant a skill edited on one host never reached the other.)
  home.file.".claude/skills" = {
    source = ../claude/skills;
    recursive = true;
    force = true;
  };
  # bash-guard — MANAGED (was per-host/unmanaged, so the deterministic
  # enforcement of the 🔴 Git Workflow rules in RULES.md DRIFTED between hosts:
  # workbench had 6 checks, the laptop a Jun-23 copy with 4). Edit
  # devrc/scripts/claude-hooks/ then switch. `force = true` alone is NOT enough
  # to displace a hand-placed regular file at this path — the switch returns
  # rc=0 and silently leaves it unmanaged. `dropStaleClaudeHooks` below is what
  # actually removes it; see the measurement in that comment. Registration in
  # ~/.claude/settings.json stays per-host and needs no change — it already
  # invokes `python3 ~/.claude/hooks/bash-guard.py`, which this symlink backs.
  home.file.".claude/hooks/bash-guard.py" = {
    source = ../scripts/claude-hooks/bash-guard.py;
    force = true;
  };
  # `browser` skill — the DELIBERATE EXCEPTION to the store-symlink pattern above.
  # Its source of truth is the browser-bridge subsystem in THIS repo
  # (scripts/browser-bridge/{SKILL.md,browser}), NOT devrc/claude/skills/. So rather
  # than copy those into the store we point ~/.claude/skills/browser/ at the live,
  # MUTABLE repo checkout via mkOutOfStoreSymlink — the skill then always tracks the
  # working tree (editable in place; a SKILL.md/CLI edit shows up with no switch).
  # Ships to both hosts declaratively, replacing the previously HAND-MADE symlinks.
  # Additive to ship.sh's skills rsync (no-delete; identical symlinks on both hosts
  # are a no-op). One-time on each host: `rm ~/.claude/skills/browser/{SKILL.md,browser}`
  # if the old manual symlinks exist, else the first switch reports "would be clobbered".
  home.file.".claude/skills/browser/SKILL.md".source =
    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/scripts/browser-bridge/SKILL.md";
  home.file.".claude/skills/browser/browser".source =
    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/scripts/browser-bridge/browser";
  # Claude Code hooks managed here (the script only — the settings.json
  # registration is per-host/unmanaged, as for bash-guard.py above, whose script
  # is likewise managed now). audit-pr-nudge fires
  # PostToolUse on `gh pr create` and injects context so Claude reflexively offers
  # `/audit-pr` (transcript audit: that request was hand-typed ≥14x while the skill
  # sat unused). Registered as `python3 ~/.claude/hooks/audit-pr-nudge.py`.
  home.file.".claude/hooks/audit-pr-nudge.py" = {
    source = ../scripts/claude-hooks/audit-pr-nudge.py;
  };
  # shell-env-nudge fires PostToolUse on Bash calls that re-type a repo/kubeconfig
  # path (`cd <repo>`, `export KUBECONFIG=<path>`) and hints the pre-exported $handle
  # (deterministic, once per handle per session). The in-the-moment counterpart to
  # the CLAUDE.md pointers — opt-in guidance didn't stick, so nudge at the moment.
  home.file.".claude/hooks/shell-env-nudge.py" = {
    source = ../scripts/claude-hooks/shell-env-nudge.py;
  };
  # claude-notify is the turn-finished notifier: on UserPromptSubmit/Stop/SubagentStop
  # it fires a desktop toast (or, headless, a clawgate phone push as FALLBACK) when a
  # turn ran >= threshold (default 60s). Telemetry shows Claude runs mostly on the
  # headless workbench (98x/14d vs 8x on the laptop) with the long waits there
  # (306min vs 41min) — exactly where the phone-push fallback earns its keep. Script
  # delivered here to BOTH hosts; the 3 settings.json events are registered per-host
  # by register-nudge-hook.py (settings.json is per-host/unmanaged).
  home.file.".claude/hooks/claude-notify.py" = {
    source = ../scripts/claude-hooks/claude-notify.py;
  };

  # These hooks previously existed as PLAIN local files (claude-notify.py +
  # test_claude_notify.py on the laptop; bash-guard.py on BOTH hosts). A
  # hand-placed regular file at a managed path is NOT replaced by the store
  # symlink — `force = true` is NOT sufficient. Measured 2026-07-30 on
  # workbench: two consecutive `home-manager switch` runs returned rc=0 and
  # printed "Creating home file links", yet ~/.claude/hooks/bash-guard.py stayed
  # a regular file; `mv`-ing it away and re-switching produced the symlink
  # immediately. So the file silently stays UNMANAGED and drifts — exactly the
  # failure this hook was made managed to end (workbench had 6 checks, the
  # laptop a months-old copy with 4).
  #
  # Removing the stale non-symlink BEFORE checkLinkTargets is what actually
  # works. Guarded on `! -L` so a legitimately-managed store symlink is never
  # touched — this only ever removes a hand-placed regular file, whose content
  # is in git anyway.
  home.activation.dropStaleClaudeHooks = lib.hm.dag.entryBefore ["checkLinkTargets"] ''
    for f in "$HOME/.claude/hooks/claude-notify.py" \
             "$HOME/.claude/hooks/test_claude_notify.py" \
             "$HOME/.claude/hooks/bash-guard.py"; do
      if [ -e "$f" ] && [ ! -L "$f" ]; then
        $DRY_RUN_CMD rm -f "$f"
      fi
    done
  '';

  # Reusable failure-notification TEMPLATE unit. The important user units below
  # (+ bar-status-poll in graphical.nix) carry OnFailure=notify-failure@%n.service,
  # so when one enters the `failed` state systemd instantiates this with the failed
  # unit's name (%i) and the handler fires a desktop toast pointing at its journal.
  # This is the observability backstop: Zach reasons THROUGH these agents, so a
  # silently-dead timer/collector is the worst failure mode — make it loud.
  #
  # Installed on EVERY host (a template is inert until instanced); the toast itself
  # is gated on the graphical host by only exporting NOTIFY_FAILURE_GRAPHICAL=1
  # there (mirrors how dunst/espanso key off `graphical`). On a headless host the
  # handler logs to the journal and exits 0 — it never errors (an erroring
  # OnFailure handler is itself an invisible failure). Minimal user-unit env, so
  # PATH is explicit: bash + coreutils (tr/id/head) + procps (pgrep) + gnugrep +
  # libnotify (notify-send), exactly the cpu-monitor toast toolchain.
  systemd.user.services."notify-failure@" = {
    Unit = {
      Description = "Desktop toast when the user unit %i fails";
    };
    Service = {
      Type = "oneshot";
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.bash pkgs.coreutils pkgs.procps pkgs.gnugrep pkgs.libnotify ]}"
      ] ++ lib.optional graphical "NOTIFY_FAILURE_GRAPHICAL=1";
      ExecStart = "${pkgs.bash}/bin/bash %h/.config/notify-failure/notify-failure.sh %i";
      # Re-run with fresh handler code after a script-only edit (cf. the
      # X-Restart-Triggers rationale on the collector units below).
      X-Restart-Triggers = [ "${../scripts/notify-failure.sh}" ];
    };
  };

  systemd.user.services.cpu-monitor = {
    Unit = {
      Description = "Desktop alert on sustained high CPU load";
      After = [ "graphical-session.target" ];
    };
    Service = {
      # PATH must be explicit: a user service does not inherit the login shell PATH.
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.coreutils pkgs.gawk pkgs.procps pkgs.gnugrep pkgs.libnotify ]}"
        # This laptop runs hot at idle (cooling needs attention); warn early.
        "CPU_MON_TEMP_THRESHOLD=88"
        # Never alert on these (games / Android stack / expected heavy apps):
        # case-insensitive substring match on the busy process's command.
        # COMMA-separated (a space gets split by systemd's Environment= parsing
        # and silently drops entries). Add more, e.g. "anno,logd,steam,lmkd".
        "CPU_MON_IGNORE=anno,logd"
      ];
      ExecStart = "${pkgs.bash}/bin/bash %h/.config/cpu-monitor/cpu-monitor.sh";
      Restart = "always";
      RestartSec = 10;
    };
    Install = {
      # default.target = starts on login. i3 is not systemd-integrated, so the
      # script borrows DISPLAY/DBUS from i3's /proc environ to reach dunst.
      WantedBy = [ "default.target" ];
    };
  };

  # Activity-telemetry collector daemon. Batches spooled events and ships them to
  # ClickHouse. Mirrors the cpu-monitor user-service pattern (Restart=always,
  # explicit PATH via lib.makeBinPath). Config comes from the EnvironmentFile
  # (not the nix store); EnvironmentFile is optional so a missing file (e.g. mid
  # first switch, before activation seeds it) does not fail the unit.
  systemd.user.services.activity-collector = {
    Unit = {
      Description = "Personal activity-telemetry collector → ClickHouse";
      # No graphical-session dep: this must run in headless/server mode too.
      After = [ "network.target" ];
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "simple";
      # PATH must be explicit: a user service does not inherit the login PATH.
      # python3 (with stdlib only) + base64/coreutils for the helper path.
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.python312 pkgs.coreutils pkgs.bash ]}"
      ];
      EnvironmentFile = "-%h/.config/activity-collector/env";
      ExecStart = "${pkgs.python312}/bin/python3 %h/.config/activity-collector/collector.py";
      Restart = "always";
      RestartSec = 10;
      # Restart on a script-only change. sd-switch only restarts a unit when the
      # unit definition itself changes; the script is symlinked-by-path, so a
      # code edit alone leaves the daemon running STALE code until a manual
      # `systemctl --user restart`. Pinning the script's store path here makes the
      # unit definition change whenever the code changes → switch restarts it.
      X-Restart-Triggers = [ "${../scripts/collector/collector.py}" ];
    };
    Install = {
      WantedBy = [ "default.target" ];
    };
  };

  # Claude Code activity source — periodic oneshot, runs BOTH transcript tailers on
  # the SAME 5-min cadence (Type=oneshot ExecStart lines run sequentially):
  #   1. tailer.py         — the MESSAGE STREAM (kind=prompt|command).
  #   2. session-tailer.py — LAYER A per-session rollups (kind=session-summary):
  #                          deterministic tool/token/lang/git counts, the
  #                          telemetry-native replacement for the built-in
  #                          /insights session-meta cache. NO LLM.
  #                          EMIT-ON-SETTLE: it does NOT re-ship a live session's
  #                          rollup on every tick (that produced 27k rows over 702
  #                          sessions, 97.4% superseded). Tunables, read from the
  #                          environment at run time, defaults in the script:
  #                          CLAUDE_SUMMARY_SETTLE_MINUTES (20) and
  #                          CLAUDE_SUMMARY_INTERIM_HOURS (4). Left unset here so
  #                          the script's defaults are the single source of truth.
  # Stdlib-only python + the emit helper's bash/coreutils on PATH. No graphical/
  # network dep — both only read local transcripts and append to the local spool,
  # so they run in headless/server mode too. Host is stamped by the collector
  # daemon (ACTIVITY_HOST). No X-Restart-Triggers: the timer re-runs fresh code
  # each cycle (a oneshot picks up the new store path on its next fire).
  systemd.user.services.claude-activity-source = {
    Unit = {
      Description = "Tail Claude Code transcripts → activity spool (prompts + session summaries)";
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "oneshot";
      # First run backfills the WHOLE transcript corpus (both tailers scan every
      # session). That can far exceed systemd's default ~90s start timeout; a
      # SIGTERM mid-backfill would strand state and re-storm next tick. Give it
      # room — session-tailer.py also now checkpoints its state incrementally so
      # an interrupted run still resumes rather than restarts.
      TimeoutStartSec = 600;
      # Session teardown (logout/reboot) SIGTERMs this oneshot mid-scan, which
      # systemd records as Failed with result 'signal' → a phantom OnFailure
      # toast for a perfectly normal shutdown. Treat SIGTERM as success; a real
      # crash still exits non-zero and still notifies (an OOM kill is SIGKILL,
      # not SIGTERM, so it is unaffected).
      #
      # CAVEAT — the two tailers are NOT equally safe to interrupt.
      # session-tailer.py checkpoints incrementally, so a SIGTERM'd run resumes.
      # tailer.py does NOT: it saves state once, after its whole loop, so an
      # interrupted run re-emits everything it already emitted. Those are
      # source=claude kind=prompt|command rows, which (unlike kind=session-summary)
      # have no argMax dedupe on read, so the duplicates inflate counts. That
      # hazard predates this setting, but suppressing the toast removes the only
      # signal it just happened — fix by checkpointing tailer.py incrementally.
      SuccessExitStatus = "TERM";
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.python312 pkgs.coreutils pkgs.bash ]}"
      ];
      ExecStart = [
        "${pkgs.python312}/bin/python3 %h/.config/activity-collector/claude/tailer.py"
        "${pkgs.python312}/bin/python3 %h/.config/activity-collector/claude/session-tailer.py"
      ];
    };
  };

  # Timer: fire the tailer ~every 5 min. OnUnitActiveSec re-arms relative to the
  # last run, so a slow scan never overlaps itself. OnStartupSec gives one prompt
  # run shortly after login. Persistent catches up a single missed run after sleep.
  systemd.user.timers.claude-activity-source = {
    Unit = {
      Description = "Periodic timer for the Claude Code activity source";
    };
    Timer = {
      OnStartupSec = "1min";
      OnUnitActiveSec = "5min";
      Persistent = true;
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };

  # OpenCode activity source (6th source): tail OpenCode transcripts and emit
  # prompts + session summaries as source=opencode events via the shared emit
  # helper. Same pattern as the Claude source — a periodic oneshot driven by its
  # own timer (below).
  systemd.user.services.opencode-activity-source = {
    Unit = {
      Description = "Tail OpenCode transcripts → activity spool (prompts + session summaries)";
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "oneshot";
      TimeoutStartSec = 600;
      SuccessExitStatus = "TERM";
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.python312 pkgs.coreutils pkgs.bash ]}"
      ];
      ExecStart = [
        "${pkgs.python312}/bin/python3 %h/.config/activity-collector/opencode/tailer.py"
        "${pkgs.python312}/bin/python3 %h/.config/activity-collector/opencode/session_tailer.py"
      ];
    };
  };

  systemd.user.timers.opencode-activity-source = {
    Unit = {
      Description = "Periodic timer for the OpenCode activity source";
    };
    Timer = {
      OnStartupSec = "1min";
      OnUnitActiveSec = "5min";
      Persistent = true;
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };

  # X11 full-content keystroke collector. Captures globally via the RECORD
  # extension (python-xlib) as the logged-in user — needs the X session, so it
  # is gated on graphical-session.target (NOT started in headless/server mode).
  # Writes typing units into the same spool the activity-collector ships.
  # NOTE: staged but NOT enabled here; enablement is a deliberate converge step.
  systemd.user.services.keylog = {
    Unit = {
      Description = "X11 full-content keystroke collector → activity spool";
      # Requires a live X session (RECORD + active-window context).
      After = [ "graphical-session.target" ];
      PartOf = [ "graphical-session.target" ];
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "simple";
      # python3 WITH python-xlib (the X RECORD plumbing). DISPLAY is borrowed
      # from the running session; i3 is not systemd-integrated, so import the
      # graphical env if available.
      Environment = [
        "PATH=${lib.makeBinPath [ (pkgs.python312.withPackages (ps: [ ps.xlib ps.pyyaml ])) pkgs.coreutils ]}"
      ];
      ExecStart = "${pkgs.python312.withPackages (ps: [ ps.xlib ps.pyyaml ])}/bin/python3 %h/.config/activity-collector/keylog/keylog.py";
      Restart = "always";
      RestartSec = 10;
      # Ceiling on a measured runaway: this unit was observed pinning a full
      # core (96% CPU over a 3s sample, ~2 kernel ticks — i.e. spinning in
      # userspace, not blocked on X) sustained over 24h+ regardless of typing.
      # Root cause is still open; the cap bounds the blast radius on a box that
      # is CPU-contended (PSI cpu some ~63%).
      #
      # ⚠ COUPLED to keylog-spin-capture.sh's threshold (default 20%). A 30%
      # quota lets a 3s sample accrue at most ~90 of 300 ticks = 30%, leaving
      # only a 10-point margin over the threshold. Lowering this below ~25%
      # blinds the watcher silently — change both together, or not at all.
      #
      # NOT yet verified that capture keeps up under load while throttled: if
      # the spin turns out to be backlog-driven (X RECORD buffer draining
      # slower than it fills), throttling the drainer could make the backlog
      # WORSE rather than bounding it. Watch for dropped events / rising RSS.
      # Remove once the spin is fixed and verified idle-at-~0%.
      CPUQuota = "30%";
      # Restart on a script-only change (see activity-collector for rationale).
      X-Restart-Triggers = [ "${../scripts/collector/keylog}" ];
    };
    Install = {
      WantedBy = [ "graphical-session.target" ];
    };
  };

  # Catch the keylog spin in the act. keylog.service degenerates into a
  # ~full-core userspace loop over hours/days (see CPUQuota above); a restart
  # clears it, which is exactly what makes it hard to diagnose — noticing it
  # and restarting destroys the evidence. This samples CPU and dumps the
  # Python stack the first time it crosses the threshold, then self-disables
  # (rm ~/.cache/keylog-spin/.captured to re-arm).
  #
  # nixpkgs' py-spy fails its own test suite (test_thread_names panics on an
  # Option::unwrap of None — upstream test bug, not a build problem), so the
  # check phase is skipped. Pinning it here also gives it a GC root, instead
  # of rebuilding it from source on every ad-hoc nix-shell.
  #
  # ⚠ BUILD COST: neither plain nor overridden py-spy is in cache.nixos.org
  # (both drv outputs 404), so every host that switches COMPILES it from
  # source (Rust + libunwind), and a build failure there fails the whole
  # home-manager switch. That is the price of a temporary diagnostic — set
  # enableKeylogSpinCapture = false (defined in the `let` block ABOVE) to opt
  # a host out entirely.
  #
  # py-spy needs kernel.yama.ptrace_scope=0 to attach to a non-descendant, and
  # that is NOT uniform here: the workbench reads 0, the LAPTOP reads 1
  # (verified 2026-07-30). On a ptrace_scope=1 host py-spy can never attach, so
  # the script checks the sysctl FIRST and exits quietly — no dump, no toast,
  # no retry. It also gives up after KEYLOG_SPIN_MAX_FAILS incomplete captures,
  # so a broken py-spy cannot turn the 5-min timer into a permanent loop.
  systemd.user.services.keylog-spin-capture = lib.mkIf enableKeylogSpinCapture {
    Unit = {
      Description = "Capture a py-spy stack dump if keylog.service starts spinning";
      # Only meaningful alongside a live keylog.service, which is itself
      # graphical-session-gated — no point waking on a headless host.
      After = [ "graphical-session.target" ];
      PartOf = [ "graphical-session.target" ];
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "oneshot";
      # Hang guard only. The actual cap on how long keystroke capture can be
      # ptrace-frozen is the `timeout 10` / `timeout 15` wrapping each py-spy
      # invocation in the script — systemd's timeout is the outer backstop.
      TimeoutStartSec = 45;
      Environment = [
        "PATH=${lib.makeBinPath [
          (pkgs.py-spy.overrideAttrs (_: { doCheck = false; }))
          pkgs.coreutils pkgs.procps pkgs.systemd pkgs.gnugrep pkgs.gawk
          pkgs.libnotify pkgs.bash
        ]}"
      ];
      ExecStart = "${pkgs.bash}/bin/bash ${../scripts/keylog-spin-capture.sh}";
    };
  };

  systemd.user.timers.keylog-spin-capture = lib.mkIf enableKeylogSpinCapture {
    Unit = {
      Description = "Periodic check for the keylog CPU spin";
      PartOf = [ "graphical-session.target" ];
    };
    Timer = {
      OnStartupSec = "5min";
      OnUnitActiveSec = "5min";
      # (No Persistent — it only applies to OnCalendar timers, not monotonic
      # ones. Same rationale as the other monotonic timers in this file.)
    };
    Install = {
      # graphical-session.target, NOT timers.target: keylog.service only runs
      # under a graphical session, so on a headless host this would otherwise
      # wake every 5min just to find MainPID=0 and exit.
      WantedBy = [ "graphical-session.target" ];
    };
  };

  # i3 focus collector. Subscribes to i3's IPC event stream and emits a
  # source=i3 record on every window-focus / workspace-focus change — capturing
  # attention even when the user is only READING (the keylogger only records
  # focus context WHEN typing). Needs a live i3 IPC socket, so it is gated on
  # graphical-session.target (laptop-only, NOT started in headless/server mode),
  # exactly like keylog. Writes into the same spool the activity-collector ships,
  # reusing keylog's spool_emit (single source of truth for the v1 line format).
  systemd.user.services.i3-source = {
    Unit = {
      Description = "i3 window/workspace focus collector → activity spool";
      # Requires a live i3 (IPC socket); tracks the graphical session.
      After = [ "graphical-session.target" ];
      PartOf = [ "graphical-session.target" ];
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "simple";
      # python3 WITH i3ipc (the IPC client). WM_CLASS/title come straight from
      # the i3ipc container, so no Xlib is needed. I3SOCK is auto-discovered by
      # i3ipc from the running session.
      Environment = [
        "PATH=${lib.makeBinPath [ (pkgs.python312.withPackages (ps: [ ps.i3ipc ])) pkgs.coreutils ]}"
      ];
      ExecStart = "${pkgs.python312.withPackages (ps: [ ps.i3ipc ])}/bin/python3 %h/.config/activity-collector/i3/i3source.py";
      Restart = "always";
      RestartSec = 10;
      # Restart on a script-only change (see activity-collector for rationale).
      # Tracks i3/ AND keylog/, because i3source reuses keylog's spool_emit
      # (single source of truth for the v1 line format).
      X-Restart-Triggers = [ "${../scripts/collector/i3}" "${../scripts/collector/keylog}" ];
    };
    Install = {
      WantedBy = [ "graphical-session.target" ];
    };
  };

  # Browser-activity receiver: localhost HTTP bridge that the MV3 extension
  # POSTs to; writes browser nav/focus events into the activity spool. Loopback
  # only, stdlib-only python. No X dependency (the extension lives in the
  # browser), but it is only useful alongside a running browser, so it tracks
  # default.target like the collector. Staged but NOT enabled.
  systemd.user.services.browser-activity-receiver = {
    Unit = {
      Description = "Browser-activity receiver (localhost → activity spool)";
      After = [ "network.target" ];
    };
    Service = {
      Type = "simple";
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.python312 pkgs.coreutils ]}"
        # Bind loopback only; keep off any external interface.
        "BROWSER_RECEIVER_HOST=127.0.0.1"
        "BROWSER_RECEIVER_PORT=8787"
        # The browser is Brave (Chromium-based); label records accordingly so
        # they don't masquerade as generic chromium.
        "BROWSER_APP=brave"
      ];
      ExecStart = "${pkgs.python312}/bin/python3 %h/.config/activity-collector/browser-ext/receiver.py";
      Restart = "always";
      RestartSec = 10;
      # Restart on a script-only change (see activity-collector for rationale).
      # Tracks browser-ext AND keylog, because the receiver reuses keylog's
      # spool_emit (single source of truth for the v1 line format).
      X-Restart-Triggers = [ "${../scripts/collector/browser-ext}" "${../scripts/collector/keylog}" ];
    };
    Install = {
      WantedBy = [ "default.target" ];
    };
  };

  # browser-bridge — loopback rendezvous server that lets a Claude Code skill
  # drive the user's LIVE Brave tab (getHtml/eval/tabs/nav/screenshot) via the
  # browser-bridge MV3 extension. SIBLING to browser-activity-receiver above;
  # modelled on it exactly (loopback env, python312, X-Restart-Triggers so a
  # `home-manager switch` restarts on a script change, WantedBy default.target).
  # Bound to 127.0.0.1 only + bearer-token auth (token auto-created 0600 at
  # ~/.config/browser-bridge/token on first start). Staged + enabled but inert
  # until the unpacked extension is loaded in Brave (see the `browser` skill).
  systemd.user.services.browser-bridge = {
    Unit = {
      Description = "Browser bridge (loopback command channel → live Brave tab)";
      After = [ "network.target" ];
    };
    Service = {
      Type = "simple";
      Environment = [
        # pkgs.i3 puts `i3-msg` on the service PATH: the `activate` op's
        # host-side i3 foregrounding (#196) resolves i3-msg via PATH, but a
        # systemd --user service otherwise inherits only python3+coreutils —
        # NOT /run/current-system/sw/bin where i3-msg lives — so `which i3-msg`
        # returned None in-service and `activate` silently reported i3:"skipped"
        # even on the graphical i3 host. Pin the hermetic store path (pkgs.i3)
        # and keep the system profile as a fallback. server.py ALSO resolves
        # i3-msg by well-known absolute path (belt-and-suspenders).
        "PATH=${lib.makeBinPath [ pkgs.python312 pkgs.coreutils pkgs.i3 ]}:/run/current-system/sw/bin"
        # Bind loopback only; never reachable off-host.
        "BROWSER_BRIDGE_HOST=127.0.0.1"
        # Distinct from the activity receiver's 8787.
        "BROWSER_BRIDGE_PORT=8788"
      ];
      ExecStart = "${pkgs.python312}/bin/python3 %h/.config/browser-bridge/server.py";
      Restart = "always";
      RestartSec = 10;
      # Restart on a script-only change (cf. the receiver above). server.py is
      # standalone, so only its own path needs tracking.
      X-Restart-Triggers = [ "${../scripts/browser-bridge/server.py}" ];
    };
    Install = {
      WantedBy = [ "default.target" ];
    };
  };

  # Laptop-only SOCKS5 tunnel to the homelab kube API via the workbench. The
  # homelab API server (192.168.50.94:6443) is LAN-only and the laptop is
  # nebula-only, so it cannot reach the API directly. This holds an `ssh -D`
  # SOCKS proxy on 127.0.0.1:1080 through the workbench (nebula 10.42.0.30, which
  # IS on the LAN). The kubeconfig ~/.kube/homelab-nebula.yaml points at the real
  # API with `proxy-url: socks5://127.0.0.1:1080` — server stays 192.168.50.94 so
  # its TLS cert still verifies. Gated on graphical-session.target → starts on the
  # laptop only (the headless workbench reaches the API directly and never starts
  # it, like keylog). The kubeconfig is placed out-of-band (chmod 600, holds admin
  # creds — deliberately NOT in the world-readable nix store).
  systemd.user.services.homelab-kube-tunnel = {
    Unit = {
      Description = "SOCKS5 tunnel to the homelab kube API via the workbench (nebula)";
      After = [ "graphical-session.target" "network-online.target" ];
      PartOf = [ "graphical-session.target" ];
    };
    Service = {
      Type = "simple";
      Environment = [ "PATH=${lib.makeBinPath [ pkgs.openssh ]}" ];
      # -N: no remote command; -D: dynamic SOCKS on loopback. Keepalives let
      # systemd notice a dead link and Restart it.
      ExecStart = "${pkgs.openssh}/bin/ssh -N -D 127.0.0.1:1080 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes zach@10.42.0.30";
      Restart = "always";
      RestartSec = 10;
    };
    Install = {
      WantedBy = [ "graphical-session.target" ];
    };
  };

  # Mail-actions invoice archiver — daily DETERMINISTIC pass (no LLM). Scans the
  # homelab Postgres `mail` table for invoice PDFs and uploads them + JSON
  # sidecars to the minio-archive bucket `taxes-{year}-invoices`. Idempotent: a
  # clean run reports 0 new candidates once the existing invoices are archived.
  #
  # It reaches the cluster via `kubectl port-forward` (pulling MinIO creds + the
  # PG DSN from k8s secrets itself), and runs its Python under nix-shell for the
  # archive-path deps. Type=oneshot, fired by the timer below.
  #
  # A user service runs with a minimal environment, so PATH/NIX_PATH must be
  # explicit (cf. the activity-collector units above): kubectl + nix (nix-shell)
  # + bash/coreutils on PATH, and NIX_PATH so `nix-shell -p` can resolve
  # <nixpkgs>. KUBECONFIG points at the homelab admin config. The logic lives in
  # the committed wrapper (scripts/mail-actions/run-archive.sh) to keep the unit
  # clean and version-controlled.
  #
  # WORKBENCH-ONLY (gated on serverMode). Both hosts build the same flake, but the
  # archiver needs DIRECT LAN access to the homelab API (the committed kubeconfig
  # points at 192.168.50.94:6443, no proxy). The laptop is nebula-only and reaches
  # the API solely via the SOCKS tunnel + nebula kubeconfig above, so its run would
  # just fail noisily — and a second host archiving the same mail table is pure
  # redundancy (idempotent, but wasteful). serverMode (= ~/.server-mode marker,
  # true on the headless workbench, false on the graphical laptop) is the existing
  # host discriminator and currently coincides exactly with "has direct LAN access".
  systemd.user.services.mail-actions-archive = lib.mkIf serverMode {
    Unit = {
      Description = "Mail-actions invoice archiver → minio-archive (deterministic, daily)";
      After = [ "network-online.target" ];
      Wants = [ "network-online.target" ];
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "oneshot";
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.kubectl pkgs.nix pkgs.bash pkgs.coreutils pkgs.gnused pkgs.gnugrep pkgs.gawk ]}"
        # `nix-shell -p` resolves <nixpkgs> from NIX_PATH; the minimal user-unit
        # env does not carry it. Mirror the system channel the login shell uses.
        "NIX_PATH=nixpkgs=/nix/var/nix/profiles/per-user/root/channels/nixos"
        "KUBECONFIG=%h/workspace/homelab-talos/homelab-kubeconfig"
        # nix-shell needs a HOME for its caches; %h is exported by systemd but be
        # explicit for the nested invocation.
        "HOME=%h"
      ];
      ExecStart = "${pkgs.bash}/bin/bash %h/workspace/devrc/scripts/mail-actions/run-archive.sh";
      # Re-run the unit when the wrapper changes (cf. X-Restart-Triggers above).
      X-Restart-Triggers = [ "${../scripts/mail-actions/run-archive.sh}" ];
    };
  };

  # Timer: fire the archiver daily at 06:00 local. Persistent=true catches up a
  # single missed run (e.g. host asleep at 06:00) on the next wake.
  # Workbench-only, same as its service (see the serverMode note above).
  systemd.user.timers.mail-actions-archive = lib.mkIf serverMode {
    Unit = {
      Description = "Daily timer for the mail-actions invoice archiver";
    };
    Timer = {
      OnCalendar = "*-*-* 06:00:00";
      Persistent = true;
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };

  # Initiatives consolidation (PHASE 1) — periodic sync of the on-demand
  # initiative-scan into the homelab `mailbox` Postgres (initiatives schema), so
  # later apps (a live viewer + a router) query a durable, live store instead of
  # re-running the expensive scan. The wrapper (scripts/initiatives/run-sync.sh)
  # shells out to initiative-scan.py --json and writes one append-only snapshot via
  # a kubectl port-forward — SAME cluster-access shape as mail-actions-archive.
  #
  # WORKBENCH-ONLY (gated on serverMode), identical rationale to the archiver: the
  # homelab kubeconfig points at 192.168.50.94:6443 (direct LAN, no proxy), which
  # only this host has; the laptop is nebula-only and its run would just fail noisily.
  #
  # CLICKHOUSE_* creds are provisioned by the wrapper at RUN TIME via a sops decrypt
  # (NO plaintext secret at rest — same recipe as the /initiatives slash command), so
  # the scan runs TELEMETRY-ON. The decrypt is fully best-effort and degrades to
  # telemetry-off if the age key / homelab repo / sops / decrypt is unavailable, so it
  # can never fail the sync. `sops` is put on the unit PATH below for exactly this.
  #
  # Minimal user-unit env, so PATH is explicit: nix (nix-shell) + git + gh (the
  # scan's branch/PR reads) + kubectl (the port-forward) + sops (the run-time reader
  # cred decrypt) + coreutils/sed/grep, and NIX_PATH so `nix-shell -p` resolves
  # <nixpkgs>. The wrapper's nix-shell adds psycopg2 (the DB write) + requests (the
  # scan's ClickHouse read).
  systemd.user.services.initiatives-sync = lib.mkIf serverMode {
    Unit = {
      Description = "Initiatives sync — initiative-scan → homelab mailbox Postgres (initiatives schema)";
      After = [ "network-online.target" ];
      Wants = [ "network-online.target" ];
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "oneshot";
      # Hard ceiling so a half-hung kubectl / scan can't wedge the timer; the
      # cgroup is killed and the timer re-arms on the next OnUnitActiveSec.
      # 600s (not 300) gives the ONE-TIME cold-recap batch headroom after the
      # 14d window widen surfaced ~60+ newly-stalled cards (2 vLLM recaps each,
      # committed at batch end BEFORE write_snapshot) — warm runs stay ~6-15s and
      # the 15-min OnUnitActiveSec interval is untouched, so a 10-min ceiling can't
      # overlap. (Proper hardening — recap after snapshot / per-card commit — is a
      # follow-up; this removes the first-run snapshot-loss risk the audit flagged.)
      TimeoutStartSec = 600;
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.nix pkgs.git pkgs.gh pkgs.kubectl pkgs.sops pkgs.bash pkgs.coreutils pkgs.gnused pkgs.gnugrep ]}"
        "NIX_PATH=nixpkgs=/nix/var/nix/profiles/per-user/root/channels/nixos"
        "KUBECONFIG=%h/workspace/homelab-talos/homelab-kubeconfig"
        # Explicit host tag — user units do NOT source .zshenv, so resolve_host()
        # would otherwise only land on "workbench" by falling through
        # gethostname()=="nixos". Explicit here so a future laptop copy can't mis-tag.
        "ACTIVITY_HOST=workbench"
        # Phase B — LLM recap generation (best-effort; the sync NEVER fails if the model
        # is down/slow — cards fall back to the deterministic summary). Points at the
        # homelab vLLM (ns promptver, svc/vllm-recap:8000, served model "recap"); the
        # generator kubectl-port-forwards to it on an ephemeral local port.
        "INITIATIVES_RECAP_ENABLED=1"
        "RECAP_NAMESPACE=promptver"
        "RECAP_SERVICE=svc/vllm-recap"
        "RECAP_SERVICE_PORT=8000"
        "RECAP_MODEL=recap"
        "HOME=%h"
      ];
      ExecStart = "${pkgs.bash}/bin/bash %h/workspace/devrc/scripts/initiatives/run-sync.sh";
      # Re-run the unit when the wrapper changes (cf. X-Restart-Triggers above).
      X-Restart-Triggers = [ "${../scripts/initiatives/run-sync.sh}" ];
    };
  };

  # Timer: fire the sync ~every 15min so the store is "realtime enough" (the live tmux
  # overlay is already render-time live; this keeps momentum/PRs/next-step fresh). The
  # scan is EXPENSIVE (git-log across all repos + transcript parse + ClickHouse +
  # `gh pr list` open+merged per repo + a kubectl port-forward), but 4×/hr keeps `gh`
  # well under the 5000/hr rate limit. OnUnitActiveSec re-arms after each run so a slow
  # sync never overlaps itself; OnStartupSec gives one prompt run after login. (No
  # Persistent — it only applies to OnCalendar timers, not monotonic ones.) The ↻ button
  # in the viewer forces an out-of-band sync on demand (single-flighted + debounced).
  #
  # DOUBLE-GATED: serverMode (workbench-only, LAN access) AND enableInitiativesSync
  # (the OFF-by-default master switch in the let-block above). With the switch false
  # the timer unit is not emitted at all, so NO deploy can wire it into timers.target
  # until the first supervised live write validates the write path.
  systemd.user.timers.initiatives-sync = lib.mkIf (serverMode && enableInitiativesSync) {
    Unit = {
      Description = "Periodic timer for the initiatives → Postgres sync";
    };
    Timer = {
      OnStartupSec = "2min";
      OnUnitActiveSec = "15min";
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };

  # Initiatives consolidation (PHASE 3) — the LIVE WEB VIEWER over the Phase-1 store.
  # A long-running stdlib-http.server (scripts/initiatives/viewer.py, launched by
  # run-viewer.sh) that renders the current initiatives from `initiatives.latest`
  # (ghost-free: newest snapshot only) grouped by repo, with momentum badges,
  # next-step, open PRs, and a LIVE tmux overlay read from THIS host at render time.
  # It is the durable, browser-viewable counterpart to the ephemeral agent-ops TUI.
  #
  # WORKBENCH-ONLY (gated on serverMode), same rationale as the sync: the homelab
  # kubeconfig is direct-LAN only here, AND the viewer must run on the host whose
  # tmux server it reads (the live overlay). It binds the workbench's OWN LAN address
  # (192.168.50.250:8899, eth1 — NOT 192.168.50.94, which is a homelab node hosting the
  # kube-apiserver/NodePorts and is not assignable here) — internal work data, deliberately
  # NOT wired into the public homelab gateway. Public exposure would be a later, explicit choice.
  #
  # For READS it needs NO ClickHouse/sops creds (it only reads the already-synced store).
  # BUT the ↻ refresh button shells out to run-sync.sh (POST /refresh → a subprocess),
  # which re-runs the FULL sync — so the viewer unit's PATH now also carries `sops` (the
  # run-time ClickHouse reader-cred decrypt → telemetry-on) and `gh` (the scan's PR reads);
  # KUBECONFIG + NIX_PATH are already set. Without sops/gh a refresh still works but the
  # produced snapshot degrades to telemetry-off / no-PR (best-effort, never fails).
  # It's enabled directly under serverMode with no off-by-default master switch: reads are
  # low-risk and the refresh is single-flighted + debounced (~60s) in the code. Crash-loop
  # safety is in the CODE, not the unit — every store read is per-request and a DB outage
  # renders an error page while the process keeps serving, so Restart=on-failure only ever
  # fires on a genuine process crash (e.g. the port already bound), backed off by RestartSec.
  #
  # Minimal user-unit env, so PATH is explicit: nix (nix-shell) + kubectl (the
  # port-forward) + git (repo/worktree discovery + the sops-decrypt's git show) + tmux (the
  # live pane read) + sops + gh (the refresh subprocess's sync) + bash/coreutils/sed/grep,
  # and NIX_PATH so `nix-shell -p` resolves <nixpkgs>. The wrapper's nix-shell adds
  # psycopg2 (the DB read) + requests (the scan import).
  systemd.user.services.initiatives-viewer = lib.mkIf serverMode {
    Unit = {
      Description = "Initiatives live web viewer — initiatives.latest + live tmux overlay";
      After = [ "network-online.target" ];
      Wants = [ "network-online.target" ];
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "simple";
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.nix pkgs.kubectl pkgs.git pkgs.tmux pkgs.sops pkgs.gh pkgs.bash pkgs.coreutils pkgs.gnused pkgs.gnugrep ]}"
        "NIX_PATH=nixpkgs=/nix/var/nix/profiles/per-user/root/channels/nixos"
        "KUBECONFIG=%h/workspace/homelab-talos/homelab-kubeconfig"
        "ACTIVITY_HOST=workbench"
        "INITIATIVES_VIEWER_HOST=192.168.50.250"
        "INITIATIVES_VIEWER_PORT=8899"
        # PRIMARY /api/ask path (Phase 1 initiatives agent): the model-driven OpenClaw devpod
        # (homelab ns devpod-initiatives, svc/initiatives-devpod:18789, openclaw/initiatives,
        # DeepSeek V4 Pro). The MODEL selects which deterministic skill-tool(s) to run (incl.
        # multiple for compound questions); the viewer reaches it via a kubectl port-forward
        # (same homelab reach as the store) + a gateway token derived from the in-cluster
        # HOOKS_TOKEN secret. On ANY failure it FALLS BACK to the deterministic assistant
        # below — so the sidebar always answers.
        "INITIATIVES_AGENT_ENABLED=1"
        "AGENT_NAMESPACE=devpod-initiatives"
        "AGENT_SERVICE=svc/initiatives-devpod"
        "AGENT_PORT=18789"
        "AGENT_MODEL=openclaw/initiatives"
        "AGENT_SECRET=initiatives-agent-secrets"
        # FALLBACK model for /api/ask when the agent is unreachable: the deterministic regex
        # assistant phrases over the SAME homelab vLLM the recap generator uses (ns promptver,
        # svc/vllm-recap:8000, served model "recap"). Best-effort; a model outage degrades to
        # the plain deterministic renderer.
        "INITIATIVES_RECAP_ENABLED=1"
        "RECAP_NAMESPACE=promptver"
        "RECAP_SERVICE=svc/vllm-recap"
        "RECAP_SERVICE_PORT=8000"
        "RECAP_MODEL=recap"
        "HOME=%h"
      ];
      ExecStart = "${pkgs.bash}/bin/bash %h/workspace/devrc/scripts/initiatives/run-viewer.sh";
      # Only ever restarts on a real crash (see the crash-loop note above); back off so a
      # persistently-unbindable port doesn't spin.
      Restart = "on-failure";
      RestartSec = "10s";
      # Every file the LONG-RUNNING viewer process holds in memory. The siblings are loaded
      # ONCE by explicit importlib path at first use and then cached for the life of the
      # process, so a change to any of them is invisible until the unit restarts — listing
      # only viewer.py meant a tasks.py-only (or dispatch/archive/nextstep-only) change
      # switched cleanly and then silently did nothing.
      # ⚠ `initiative-scan.py` is in the same boat (attach_tmux caches the scan module) but is
      # NOT listed: it belongs to the sync unit too, and a scan change already requires an
      # explicit `systemctl --user restart initiatives-viewer.service` (see the skill's gotchas).
      X-Restart-Triggers = [
        "${../scripts/initiatives/run-viewer.sh}"
        "${../scripts/initiatives/viewer.py}"
        "${../scripts/initiatives/agent_client.py}"
        "${../scripts/initiatives/tasks.py}"
        "${../scripts/initiatives/dispatch.py}"
        "${../scripts/initiatives/archive.py}"
        "${../scripts/initiatives/nextstep.py}"
      ];
    };
    Install = {
      WantedBy = [ "default.target" ];
    };
  };

  # Repo chief-of-staff — WEEKLY: deterministic scan of Zach's repos for improvement
  # signals (TODO/FIXME, skipped tests, `latest` tags, churn, large files) → cheap LLM
  # synthesis (OpenRouter) → ranked proposal digest EMAILED. The "agents bring me ideas"
  # experiment (scripts/repo-cos/, `run-weekly.sh` wrapper).
  #
  # SELF-HOSTED MAIL (default): the digest is SENT via Zach's postfix relay in the
  # PRODUCTION cluster (From: repo-cos@mail.zacx.dev, DKIM-signed; Reply-To:
  # repo-cos@inbox.zacx.dev) and his REPLY is READ back from the HOMELAB Postgres `mail`
  # table (his reply routes Gmail→his MX→mail-receiver→Postgres). BOTH go through a
  # `kubectl port-forward` — so the weekly send now depends on the production cluster
  # (relay) + the homelab cluster (postgres) + TWO port-forwards. Both are BEST-EFFORT:
  # a hiccup logs + skips (send fails loudly, feedback returns None) rather than wedging.
  # The two kubeconfigs (production for relay, homelab for postgres) are exported by the
  # wrapper; the Python resolves each per operation. The Gmail SMTP/IMAP fallback
  # (REPO_COS_SEND=gmail / REPO_COS_REPLY_SRC=imap) still exists behind those toggles and
  # is the only path needing the SOPS app-password.
  #
  # WORKBENCH-ONLY (serverMode): the full repo set (incl. the civitai client repos) lives
  # here, the OpenRouter key + SOPS age key + both kubeconfigs are here, and this host has
  # direct LAN access to both cluster APIs. Minimal user-unit env, so PATH needs nix
  # (nix-shell) + git + rg + kubectl (the two port-forwards) + coreutils, and NIX_PATH so
  # `nix-shell -p` resolves <nixpkgs>. The wrapper's nix-shell adds psycopg2 (Postgres read)
  # + kubectl + sops; creds are loaded by the wrapper, never in the nix store.
  systemd.user.services.repo-cos = lib.mkIf serverMode {
    Unit = {
      Description = "Repo chief-of-staff — weekly repo-scan → LLM proposals → email digest";
      After = [ "network-online.target" ];
      Wants = [ "network-online.target" ];
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "oneshot";
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.nix pkgs.git pkgs.ripgrep pkgs.kubectl pkgs.bash pkgs.coreutils pkgs.gnused pkgs.gnugrep ]}"
        "NIX_PATH=nixpkgs=/nix/var/nix/profiles/per-user/root/channels/nixos"
        "HOME=%h"
      ];
      ExecStart = "${pkgs.bash}/bin/bash %h/workspace/devrc/scripts/repo-cos/run-weekly.sh";
      X-Restart-Triggers = [ "${../scripts/repo-cos/run-weekly.sh}" ];
    };
  };

  systemd.user.timers.repo-cos = lib.mkIf serverMode {
    Unit = {
      Description = "Weekly timer for the repo chief-of-staff proposal digest";
    };
    Timer = {
      OnCalendar = "Mon *-*-* 08:00:00";
      Persistent = true;
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };

  # Seed the task-spec-drafter env file with safe defaults (DRAFTER_MODE=shadow)
  # if it does not exist yet. It holds no secrets today, but is chmod 600 and kept
  # OUT of the nix store so DRAFTER_MODE flips (shadow -> on) are a one-line edit +
  # switch, no code change — mirrors the activity-collector env seeding above.
  home.activation.taskSpecDrafterEnv = lib.hm.dag.entryAfter ["writeBoundary"] ''
    envFile="$HOME/.claude/task-spec-drafter.env"
    if [ ! -e "$envFile" ]; then
      mkdir -p "$HOME/.claude"
      cp ${../scripts/task-spec-drafter/task-spec-drafter.env.example} "$envFile"
      chmod 600 "$envFile"
      echo "task-spec-drafter: seeded $envFile from the example (DRAFTER_MODE=shadow)"
    fi
  '';

  # Deep-context task-spec drafter — DAILY, SHADOW-first (scripts/task-spec-drafter/).
  # A verifier/triage layer over the ClickUp "To Schedule" queue: per-ticket it runs
  # a headless `claude -p` deep-context pass (ENRICH -> VERIFY vs live git/PRs/metrics
  # -> CLASSIFY -> DRAFT only genuine TASKs; a deterministic safety gate force-escalates
  # security/money/destructive tickets to NEEDS-DECISION). It emits a triage queue and
  # EMAILS the day's digest (the review surface) reusing repo-cos's DKIM-signed relay.
  #
  # SHADOW by default (DRAFTER_MODE in ~/.claude/task-spec-drafter.env, seeded above):
  # it writes the queue + emails the digest + LOGS "would POST to clawgate" and
  # dispatches NOTHING / POSTs NOTHING to clawgate / mutates no repo/cluster until the
  # env flag flips to `on`. Delta-scoping keeps each daily run cheap (only new/changed
  # tickets); the first run baselines the backlog (see the README).
  #
  # WORKBENCH-ONLY (serverMode), same rationale as repo-cos / mail-actions-archive: the
  # civitai checkout + prod kubeconfig + clickup skill CLI + the `claude` CLI (ambient
  # auth) all live here, and this host has direct LAN access to the cluster APIs the
  # verify step + email relay reach.
  #
  # Minimal user-unit env, so PATH is explicit. The pipeline needs profile-installed
  # CLIs that are NOT in devrc's flake — `claude` (headless reasoning) and `gh` (PR
  # checks) come from the home-manager profile — so %h/.nix-profile/bin + the system
  # profile are on PATH ahead of the pinned deterministic tools (node for the clickup
  # CLI, git, kubectl, jq, curl, python3, coreutils). KUBECONFIG for the per-ticket
  # claude pass is set by drafter.sh itself (it exports the prod kubeconfig per call);
  # REPO_COS_PROD_KUBECONFIG here is the relay kubeconfig for the digest email.
  systemd.user.services.task-spec-drafter = lib.mkIf serverMode {
    Unit = {
      Description = "Deep-context task-spec drafter (ClickUp triage, daily, shadow-first)";
      After = [ "network-online.target" ];
      Wants = [ "network-online.target" ];
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "oneshot";
      # Each ticket runs a headless claude pass with real tool calls; daily cadence
      # tolerates an occasional long run. Bound: the FIRST (empty-state) run is the
      # worst case — it processes at most DRAFTER_MAX_TICKETS (default 25) and
      # baselines the rest, so 25 × DRAFTER_TIMEOUT(240s) = 6000s. 7200s clears that
      # with headroom (steady-state delta runs are a handful of tickets). If you
      # raise the cap or per-ticket timeout, raise this to match so a run never gets
      # SIGTERM'd mid-loop (which would strand the digest + fire a failure toast).
      TimeoutStartSec = 7200;
      Nice = 10;
      Environment = [
        # claude + gh live in the HM profile (not devrc's flake) -> profile bins
        # first, then the system profile (curl), then the pinned deterministic tools.
        "PATH=%h/.nix-profile/bin:/run/current-system/sw/bin:${lib.makeBinPath [ pkgs.nodejs_26 pkgs.git pkgs.kubectl pkgs.jq pkgs.curl pkgs.python312 pkgs.bash pkgs.coreutils pkgs.gnugrep pkgs.gnused pkgs.gawk ]}"
        "HOME=%h"
        # Relay kubeconfig for the digest email (reuses repo-cos's postfix relay).
        "REPO_COS_PROD_KUBECONFIG=%h/workspace/homelab-talos/production-kubeconfig"
        # The global PermissionRequest hook (clawgate) has no matcher, so it also fires
        # for THIS unattended pass — where a permission prompt is unanswerable by
        # construction: nobody is awake at 08:00 to tap approve, and the run is headless.
        # Left on, the hook polls to CLAWGATE_HOOK_DEADLINE (170s) against a 240s
        # DRAFTER_TIMEOUT, so a single blocked call burns most of one ticket's budget
        # AND pushes a pointless notification. The hook supports a per-session opt-out;
        # take it. (Interactive sessions are untouched — this is unit-scoped.)
        "CLAWGATE_REMOTE_APPROVAL=off"
      ];
      ExecStart = "${pkgs.bash}/bin/bash %h/workspace/devrc/scripts/task-spec-drafter/drafter.sh";
      # Re-run with fresh code after a script-only edit (cf. X-Restart-Triggers above).
      X-Restart-Triggers = [ "${../scripts/task-spec-drafter/drafter.sh}" ];
    };
  };

  # Timer: fire the drafter daily at 08:00 local. Persistent=true catches up a
  # single missed run (host asleep at 08:00) on the next wake. Shadow-by-default,
  # so enabling it changes nothing externally until DRAFTER_MODE=on.
  systemd.user.timers.task-spec-drafter = lib.mkIf serverMode {
    Unit = {
      Description = "Daily timer for the deep-context task-spec drafter";
    };
    Timer = {
      OnCalendar = "*-*-* 08:00:00";
      Persistent = true;
      RandomizedDelaySec = 300;
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };
}
