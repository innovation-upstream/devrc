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
  # Drift-deadman master switch (scripts/drift-check.sh) — gates ONLY whether the
  # timer is wired into timers.target. The SERVICE definition is always emitted, so
  # `systemctl --user start drift-check` works by hand on either host regardless.
  # ON since 2026-08-11. Both preconditions were MEASURED on the workbench, not
  # argued — each is a thing scratch clones structurally cannot test:
  #   1. the ssh leg reaches the laptop from a systemd-user context (no ssh-agent):
  #      a hand-run of drift-check.service found REAL drift (laptop 1 behind), rc 10.
  #   2. the failure toast actually DISPLAYS. It did not, at first — dunst was paused
  #      with 286 notifications queued, so the alert was sent and silently binned.
  #      #381 fixed that; re-measured here as displayed 0 -> 1 with waiting UNCHANGED
  #      while paused=true, driven by a real unit failure (rc 8) on a throwaway clone.
  # (2) is why this flag waited: a timer alerting into a paused queue is WORSE than no
  # timer, because it manufactures the appearance of coverage.
  enableDriftDeadman = true;
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
  # WORKBENCH ONLY. This flag now doubles as a SECURITY boundary: when set it
  # wires KEYLOG_ALLOW_ANY_PTRACER=1 onto keylog.service, which opens the
  # keystroke collector's live memory to any same-UID process (see
  # keylog.py:_allow_any_ptracer). So it MUST fail CLOSED — an unenrolled host
  # must not silently get it.
  #
  # It is gated on `serverMode` (an explicit operator-set marker, `~/.server-mode`),
  # NOT on `!isLaptop`. `!isLaptop` fails OPEN: `isLaptop` is a backlight probe
  # authored purely to discriminate the laptop's display config (see below), and
  # ANY future graphical NixOS host with an AMD (`amdgpu_bl0`), NVIDIA, or
  # ACPI-only (`acpi_video0`) backlight — or none — evaluates `isLaptop=false` and
  # would inherit PR_SET_PTRACER_ANY on its keylogger with no error and no signal.
  # `serverMode` is the same explicit marker every workbench-only server task keys
  # off (mail-actions-archive, initiatives-sync, repo-cos, task-spec-drafter): the
  # workbench carries it, the laptop does not, and a brand-new host does not until
  # the operator deliberately `touch ~/.server-mode`. (There is no per-host
  # hostName/osConfig to allowlist on here: this is a STANDALONE home-manager
  # config — flake.nix hardcodes home.username="zach" and both hosts report
  # gethostname()=="nixos" — so an impure operator marker is the only real
  # host allowlist available.)
  #
  # Keeping it workbench-only is ALSO required for BUILD COST: enabling it drags an
  # UNCACHED from-source py-spy build (Rust + libunwind) into every
  # `home-manager switch`, and a build failure there fails the whole switch —
  # which on the laptop would stop it converging to origin/main for a
  # workbench-only diagnostic. And because the laptop never sets
  # PR_SET_PTRACER_ANY, its keystroke collector stays untraceable by siblings.
  enableKeylogSpinCapture = graphical && serverMode;
  # Host discriminator for the graphical config (i3 + i3status-rust bar). Evaluated
  # per-host under `--impure`: the laptop has an intel_backlight, the workbench does
  # not. Threaded into ./graphical.nix via _module.args below. Drives battery/backlight
  # (laptop) vs rig-control/DDC (workbench). Do NOT use serverMode for this — it is
  # true on the graphical workbench (it only gates dunst/espanso there).
  isLaptop = builtins.pathExists "/sys/class/backlight/intel_backlight";
  userPackages = import ./pkgs { inherit pkgs workspace; };
  # Dependency tree for the `clickup` skill, built from its committed
  # package-lock.json. See nix/pkgs/clickup-node-modules.nix.
  clickupNodeModules = pkgs.callPackage ./pkgs/clickup-node-modules.nix { };
  # 🔴 The skills tree AS DEPLOYED — `claude/skills` with clickup's built
  # node_modules injected. Both skill mappings below (~/.claude/skills and
  # ~/.config/opencode/skills) use THIS, not `../claude/skills` directly.
  #
  # It exists because of how node resolves modules: from the REALPATH of the
  # importing file, not the path you invoked. `home.file` deploys each skill file
  # as a symlink into the store copy of `claude/skills`, so `lib/markdown.mjs`
  # resolves `unified` starting at `/nix/store/<…>-hm_skills/clickup/node_modules`
  # — a directory that does not exist. A `home.file` for
  # `.claude/skills/clickup/node_modules` puts the tree at the DEPLOYED path,
  # which node never looks at: MEASURED, `node ~/.claude/skills/clickup/query.mjs
  # accounts` died with `Cannot find package 'unified' imported from
  # /nix/store/…-hm_skills/clickup/lib/markdown.mjs` with that symlink in place
  # and resolving correctly. node_modules has to sit in the SAME store tree as
  # the sources, which means injecting it into the source of the mapping.
  #
  # 🔴 `ln -sT`, never a bare `ln -s`. If `claude/skills/clickup/node_modules`
  # ever existed in the checkout, `cp -R` would create that directory in $out
  # and a bare `ln -s` would then put the link INSIDE it
  # (`$out/clickup/node_modules/node_modules`) — silently, exit 0, and the
  # deployed skill would carry a committed tree while the nix-built one dangled
  # one level down. `-T` treats the target as a plain name, so that case fails
  # the build instead. (`claude/skills/.gitignore` is what makes it unlikely; a
  # gitignore is not a guarantee, and this costs one letter.)
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } ''
    cp -R ${../claude/skills} "$out"
    chmod -R u+w "$out"
    ln -sT ${clickupNodeModules}/node_modules "$out/clickup/node_modules"
  '';
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
          # Paths - labeled for autocomplete
          { trigger = ":hlt"; replace = "${workspace}/homelab-talos "; label = "homelab-talos path"; search_terms = ["infra"]; }
          { trigger = ":kuc"; replace = "${workspace}/kubeclaw "; label = "kubeclaw path"; search_terms = ["kubeclaw"]; }

          # SSH connect
          { trigger = ":sshwn"; replace = "ssh zach@10.42.0.30"; label = "SSH workbench (nebula)"; search_terms = ["ssh" "workbench" "wb" "nebula" "mesh" "remote"]; }
          { trigger = ":sshwl"; replace = "ssh zach@192.168.50.250"; label = "SSH workbench (LAN)"; search_terms = ["ssh" "workbench" "wb" "lan" "local"]; }
          { trigger = ":sshln"; replace = "ssh zach@10.42.0.100"; label = "SSH laptop (nebula)"; search_terms = ["ssh" "laptop" "nebula" "mesh" "remote"]; }
          { trigger = ":sshll"; replace = "ssh zach@192.168.50.155"; label = "SSH laptop (LAN)"; search_terms = ["ssh" "laptop" "lan" "local"]; }

          # hot singles
          # (dashbaord is the ONLY snippet with real direct-trigger traffic:
          #  5 of the 5 non-search fires in 2026-07-25→08-05 were this typo-fix.)
          { trigger = "dashbaord"; replace = "dashboard"; }

          # Workflows
          # (:whn removed 2026-07-06 — 0 fires over the audit window, superseded by
          #  /handoff + :eos, which now OPENS with the handoff rather than ending on it)
          # 2026-08-04: rewritten to carry an EVICTION half. Every verb in the old text
          # was additive ("identify skills that may need updating" → "update those
          # skills"), and the subagent it dispatched arrived with no byte budget and no
          # view of the file's size, so appending was the cheapest way to comply.
          # Measured downstream over 30 days: 5,355 lines added vs 863 deleted across
          # every SKILL.md — 84% of edit volume pure addition. app-blocks/SKILL.md grew
          # 42,425 B in a single day (2026-08-03→04), more than the whole 40,960 B cap.
          # The ritual also asked for a reflection on "work done this session", which is
          # session-SHAPED, so the subagent wrote session-shaped blocks: 42 dated
          # `### Session …` headings, 43% of that file. Handoff now comes FIRST so the
          # narrative has a home before any skill is touched. This is the weak half —
          # a snippet enforces nothing; the gate is what makes it stick. See
          # /prune-skill + scripts/skill-audit.py.
          { trigger = ":eos"; replace = "reflect on work done and key learnings this session, then write the handoff FIRST — all session narrative, status and what-shipped goes THERE, never into a skill. Then extract only the durable reusable lessons, and name the single skill or doc that should own each. Then dispatch subagent to fold them in under these rules: report each target file's byte size before and after; every edit must be net-neutral or smaller — evict, merge or demote something stale in the SAME edit; never append a dated session block to a SKILL.md; collapse a retraction to a one-line do-not-re-derive tell rather than preserving the superseded belief; if a file is over budget with nowhere to demote to, say so instead of appending. Then give me the kickoff message."; label = "End-of-session ritual: handoff first → durable lessons only → net-neutral skill edits"; search_terms = ["end" "session" "wrap" "handoff" "skills" "review" "update" "docs" "ritual" "prune" "evict" "bloat"]; }
          # 2026-08-05 /espanso-audit — DISCOVERABILITY fix, not a content fix.
          # :acq fired ZERO times in the 20-day window, yet its expansion appears
          # in 36 genuine user messages (clipboard-pasted from elsewhere) — so
          # demand is proven and the TEXT is deliberately untouched. The keystroke
          # stream shows the opener "dispatch to process feedback:" hand-typed 12×,
          # twice trailing a mistyped ":acc"/":accc". Root cause: 168 of 173 fires
          # go through the Ctrl+Space SEARCH UI, so `label` + `search_terms` ARE the
          # interface — and the old search_terms (ask/clarify/questions/elicit/…)
          # contained none of the words he actually types: feedback, dispatch,
          # process. Lead the label with "feedback" and add those three terms.
          { trigger = ":acq"; replace = "dispatch subagent to process feedback\nask clarifying questions and recommend anything useful to include before dispatching (include complete test coverage)"; label = "Process feedback: dispatch subagent + ask clarifying questions"; search_terms = ["feedback" "dispatch" "process" "ask" "clarify" "clarifying" "questions" "elicit" "scope" "include"]; }
          { trigger = ":kickoff"; replace = "give me the kickoff message to copy paste to next session"; label = "Kickoff message for next session"; search_terms = ["kickoff" "kick off" "next session" "copy paste" "handoff" "message"]; }
          # Added 2026-08-05 via /espanso-audit — both are WHOLE-STANDALONE-MESSAGE
          # shaped, the one shape that has stuck (:eos 72 fires, :kickoff 38); every
          # mid-sentence FRAGMENT snippet has been pruned (:ds, :rns, :pst, :rnx).
          # Typing-stream demand: "recommend next actions" 81 rows (25 of them that
          # phrase standing ALONE as a whole message); "limit restored, resume agent"
          # 68 rows. NOTE the noun: the pruned :rns said "recommend next STEPS" and
          # was typed only 3× — the real demand is "actions". He also searched the
          # bar for "recom" on 2026-07-27 hunting for exactly this, and found nothing.
          { trigger = ":rna"; replace = "recommend next actions"; label = "Recommend next actions"; search_terms = ["recommend" "recom" "next" "actions" "rank" "leverage"]; }
          { trigger = ":lr"; replace = "limit restored, resume agent"; label = "Limit restored — resume agent"; search_terms = ["limit" "restored" "resume" "agent" "continue" "quota"]; }
          # Added 2026-08-05 via /espanso-audit. WHOLE-STANDALONE-MESSAGE shaped,
          # which is the ONLY shape that has ever stuck here (:eos 72 fires,
          # :kickoff 38); every mid-sentence FRAGMENT snippet has since been
          # pruned (:ds :rns :pst :rnx :aep :nday :fhrs :fdays). SHAPE, not
          # length, is the predictor — :ds's phrase was hand-typed 94× and the
          # snippet still only ever fired once.
          # Measured demand: 8 genuine instances over ~3 weeks across the keylog
          # typing stream + transcripts, 5 of them standing ALONE as a whole
          # message, and typed with heavy toil ("in th eme meantime",
          # "meatimmeantime", "wahwhat can youwe rudo").
          # His vocabulary is meantime / while that runs / tee up / queue up /
          # in parallel — "meanwhile" has ZERO hits, so it is deliberately NOT a
          # search term. 168 of 173 fires go through the Ctrl+Space SEARCH UI,
          # so `label` + `search_terms` ARE the interface: the label leads with
          # the word he'd search, and the multi-word terms exist so his real
          # queries ("in the meantime", "what can we do") tokenize onto this
          # snippet (see espanso_detect._term_matches).
          { trigger = ":mt"; replace = "tee up what we can do in the meantime: identify work that is INDEPENDENT of what is currently running — nothing touching the same files — then dispatch it in parallel with complete test coverage. if we are actually blocked until that finishes, say so plainly instead of inventing filler work."; label = "Meantime: tee up independent parallel work while that runs"; search_terms = ["meantime" "in the meantime" "while" "while that runs" "parallel" "queue" "queue up" "tee" "tee up" "wait" "blocked" "idle" "what can we do"]; }
          # Removed 2026-07-25 via /espanso-audit — all keylog-evidence-backed:
          #  ZERO-FIRE set — 0 keylog fires + short-form hand-typing; steering already in
          #   RULES.md / slash-commands: :rnx, :pst ("proceed, dispatch" typed 40+×),
          #   :aep (/audit-pr invoked directly 39×), re-check trio :nday/:fhrs/:fdays.
          #  TYPING-TOIL set — keylog typing-stream shows the shortcut EXISTED but the long
          #   form was hand-typed instead (trigger→habit transfer failed; short mid-sentence
          #   prefixes don't stick for a search-first espanso user):
          #   :ds (1 fire vs 94 hand-typed; duplicates the RULES "dispatch subagent" default),
          #   :rns (1 vs 20; overlaps /resume). See claudedocs/espanso-typing-toil-2026-07-25.md.
          # Removed 2026-08-05 via /espanso-audit — 0 keylog fires each over the
          #  20 days 2026-07-25→08-05 (:nixos — this host's config is a flake in
          #  ~/workspace/devrc, so /etc/nixos/configuration.nix is a dead path;
          #  :iso and :datetime — :date+:time cover it; "reocmmend" — the typo
          #  never recurred, unlike "dashbaord" which is still the ONLY snippet
          #  with direct-trigger traffic). Kept despite showing 0 ATTRIBUTED
          #  fires: the four :ssh* plus :cgf/:subk — their searches are MULTI-WORD
          #  ("ssh work", "ssh lap", "civit prod") and the detector's _term_matches
          #  could not attribute a term containing a space (fixed in the same
          #  commit), so 19+ of the 46 unattributed rows are theirs. Unattributable
          #  ≠ dead — do not prune them on the old numbers.

          { trigger = ":cc"; replace = "${workspace}/civit/civitai "; label = "civitai main web app repo path"; search_terms = ["civitai" "repo" "web" "app"]; }
          { trigger = ":cdp"; replace = "${workspace}/civit/datapacket-talos "; label = "civitai datapacket-talos path"; search_terms = ["civitai"]; }
          { trigger = ":cgf"; replace = "${workspace}/civit/civitai-gpu-fleet "; label = "civitai gpu-fleet path"; search_terms = ["civitai"]; }
          { trigger = ":cmo"; replace = "${workspace}/civit/civitai-orchestration "; label = "civitai-orchestration path"; search_terms = ["civitai" "orchestration"]; }
          { trigger = ":csc"; replace = "${workspace}/civit/civitai-spine-controller "; label = "civitai-spine-controller path"; search_terms = ["civitai" "spine controller" "spine"]; }
          { trigger = ":cpk"; replace = "${workspace}/civit/datapacket-talos/prod-kubeconfig "; label = "civitai dp prod kubeconfig path"; search_terms = ["civitai"]; }
          { trigger = ":subk"; replace = "${workspace}/civit/civitai-gpu-fleet/submodel-dc-03-a-kubeconfig "; label = "civitai submodel dc 03 kubeconfig path"; search_terms = ["civitai" "gpu" "submodel" "dc 03"]; }

          # Utilities
          { trigger = ":clip"; replace = "{{clip}}"; label = "Paste from clipboard"; vars = [{ name = "clip"; type = "clipboard"; }]; }
        ];
      };
    };
  };

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
        # RECALL BUFFER sized against the MEASURED notification rate, not a guess.
        # Audited 2026-08-11 on the workbench: ~330 notifications/day reach dunst.
        # At the previous value of 40 the buffer therefore held under THREE HOURS.
        #
        # RE-MEASURED 2026-08-12 (PR #409's per-producer split was partly wrong;
        # the 300 slots are not):
        #   claude-notify  ~174/day workbench desktop toasts (peak 386), plus
        #                  ~150/day on the LAPTOP, which #409 never measured at
        #                  all. Corroborated by a second, independent instrument:
        #                  dunst's own history is 93% (workbench) / 81% (laptop)
        #                  `claude`. This is the producer that mattered.
        #   cpu-monitor    ~23/day workbench, ~13/day laptop — NOT the ~90/day
        #                  #409 reported. That mean straddled a regime break:
        #                  raising CPU_MON_THRESHOLD/RUNAWAY_PCT on 08-05 cut the
        #                  workbench from 123-267/day to 11-32/day. Two
        #                  instruments agree after the break.
        #   earlyoom       415 notifications/11 days but on only THREE days
        #                  (50/161/204); zero on the other eight.
        # The icon-warning instrument used for the last two was calibrated, not
        # assumed: 4 probes carrying that icon produced exactly 4 warnings.
        #
        # That is the recall path for anything DND swallowed, and it is the only
        # one: a paused notification sits in the `waiting` queue, which dunst 1.13.2
        # exposes only as a COUNT (`dunstctl count waiting`) — its contents cannot
        # be enumerated or recovered. History is what `$mod+n` (history-pop) and
        # scripts/notif-center read.
        #
        # Measured consequence of 40: on the workbench the history was 38/40
        # `system-notify` (earlyoom OOM-kill toasts, which arrive in bursts of 100+
        # during a single kill episode), having evicted everything else — including
        # the `notify-failure` deadman toasts that PR #381 exists to protect. One
        # OOM burst was enough to flush the entire recall buffer.
        #
        # This does NOT suppress or reduce anything; it only widens the window in
        # which a swallowed notification can still be recovered. Cost is a few
        # hundred structs in dunst's memory. Reverting is this one line.
        history_length = 300;
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
      # EARLYOOM BURST COALESCING — N kills in one episode collapse to ONE toast.
      #
      # earlyoom's `-g` kills whole process groups and systembus-notify emits one
      # notification per killed process. Measured on the workbench: 415 kill
      # notifications in 11 days, concentrated into THREE days (50 / 161 / 204) —
      # 111 of them inside a single 3-minute window on 08-11, and zero on the
      # other eight days. That burst shape is what makes it worth fixing: it is
      # not a steady rate you can threshold away, it is an occasional wall.
      #
      # A shared `set_stack_tag` makes dunst REPLACE the previous toast carrying
      # the tag rather than enqueue a new one, so an episode of any size occupies
      # one slot showing its most recent kill.
      #
      # VALIDATED 2026-08-12 on the laptop — the thing PR #409 could not do. It
      # probed twice and both runs were confounded by `fullscreen_suppress`
      # routing the probes to history, so the tagged and untagged arms both read
      # 0: a zero from a control that never observed anything. The re-test made
      # the UNTAGGED arm an explicit POSITIVE CONTROL and refused to read the
      # tagged number unless that control moved first. Result on dunst 1.13.2,
      # no fullscreen window focused, 5 notifications per arm with DISTINCT
      # summaries (so the global `stack_duplicates` cannot masquerade as
      # stack-tag behaviour):
      #     untagged (control) -> displayed = 3     [observable]
      #     tagged             -> displayed = 1     [collapsed]
      #
      # WHAT IS LOST: the per-process detail of every kill but the newest, ON THE
      # DESKTOP ONLY. `journalctl -u earlyoom` keeps every kill permanently with
      # process, RSS and cmdline, and that is where an episode is actually read.
      # What the toast is for — "something got OOM-killed, that's why your run
      # died" — is one bit, and one toast carries it.
      #
      # SCOPE: keyed on the appname systembus-notify hard-codes. It cannot match
      # `notify-failure` (a different appname), and it sets no `fullscreen` key,
      # so it takes no part in the last-write-wins ordering that protects the
      # deadman bypass below.
      system_notify_stack = {
        appname = "system-notify";
        set_stack_tag = "system-notify-burst";
      };
      # DEADMAN BYPASS — the ONE class of toast that must defeat do-not-disturb.
      #
      # WHY: `notify-failure` toasts are the only signal that an important user
      # unit died. Measured 2026-08-10 on the workbench: dunst `is-paused=true`
      # with 30 notifications queued behind it, and `drift-check.service` sitting
      # in `failed` (status 10, genuine drift) — the OnFailure handler ran, sent
      # its toast, and the toast went into the WAITING queue and was never shown.
      # It is not even in `dunstctl history` (history only holds toasts that were
      # DISPLAYED then dismissed/expired), so the failure was invisible in every
      # surface a human looks at. DND is a state Zach deliberately enters via the
      # bar's bell button, so "just unpause it" is not a fix.
      #
      # TWO INDEPENDENT SUPPRESSORS had to be defeated — measured, not assumed:
      #
      #  1. PAUSE LEVEL. `dunstctl set-paused true` (what the bar button runs)
      #     sets pause level **100**, the maximum. dunst(5) says a notification
      #     shows when its override_pause_level is "greater than" the pause
      #     level, which would make 100 unbeatable. THAT IS WRONG — the
      #     implementation compares >=. Measured on dunst 1.13.2 at pause
      #     level 100: override_pause_level=100 -> displayed 0->1, waiting 0->0;
      #     override_pause_level=99 -> displayed 0->0, waiting 0->1. So 100 is
      #     both necessary and sufficient; anything less is silently swallowed.
      #
      #  2. FULLSCREEN SUPPRESSION. `fullscreen_suppress` above is FILTERLESS, so
      #     it also matches this toast and routes it straight to history whenever
      #     any fullscreen window is focused — a second, independent way for a
      #     deadman alert to vanish (observed dropping real toasts on the laptop).
      #     `fullscreen = "show"` opts this one rule back out.
      #
      # ORDERING MATTERS: home-manager renders these sections alphabetically and
      # dunst applies rules in file order, last-write-wins. The `zz_` prefix
      # guarantees this rule is applied AFTER `fullscreen_suppress` regardless of
      # what rules are added later. scripts/tests/test_dunst_dnd_bypass.py pins
      # both the values and that ordering.
      #
      # SCOPE: keyed on appname, which scripts/notify-failure.sh sets via
      # `notify-send -a notify-failure`. That is ALL unit-failure toasts, not an
      # opt-in subset — justified by the measured firing rate (7 activations in
      # ~6 months of laptop journal, 1 in 9 days of workbench journal), which is
      # far too low to make DND feel broken. Deliberately NOT keyed on
      # `urgency = critical`: other tools send critical toasts, and those must
      # still respect DND.
      zz_notify_failure_bypass = {
        appname = "notify-failure";
        override_pause_level = 100;
        fullscreen = "show";
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

  # browser-bridge: deploy the unpacked MV3 extension to a STABLE, git-immune
  # path that Brave can be pointed at permanently.
  #
  # WHY: Brave has been loading the extension straight out of the git working
  # tree (~/workspace/devrc/scripts/browser-bridge/extension). devrc is worked on
  # by many concurrent sessions, so any other session's `git checkout`, branch
  # switch or worktree operation silently swaps the extension's code out from
  # under a live verification — measured: it reverted a staged build mid-session.
  # A copy under ~/.local/share/ is untouchable by `git checkout`/`stash`/branch
  # switch/worktree ops.
  #
  # ⚠ HONEST SCOPE — this is NOT "no git operation can reach it". `home-manager
  # switch` (and `ship.sh`) rewrites this tree from whatever the working tree
  # holds AT THAT MOMENT. A concurrent session sitting on another branch that
  # runs a switch still swaps the extension mid-verification. What this removes
  # is the SILENT class (a checkout with no switch); a switch is at least an
  # explicit, logged act. `browser ping` is what makes the remaining case
  # detectable rather than invisible.
  #
  # WHY A REAL COPY, NOT `home.file … recursive = true`: that would deploy the
  # tree as read-only /nix/store SYMLINKS. Whether Chromium's unpacked-extension
  # loader accepts a tree of dangling-into-the-store symlinks is exactly the kind
  # of thing that must be MEASURED against live Brave, and a wrong guess here is
  # expensive (it costs a full Brave restart to find out, and the operator's tabs
  # are not restorable). A plain copy (`cp -rL`) removes the question entirely and
  # is equally git-immune. Cost: the whole tree is rewritten on every switch.
  #
  # ⚠ FLAKE TRAP: flakes only see git-TRACKED files, so a NEW extension file that
  # has not been `git add`ed is silently omitted from ${../scripts/browser-bridge/extension}
  # and therefore from the deployed tree — a partially-updated extension with NO
  # error anywhere. `git add` a new extension file BEFORE switching. (Same trap
  # already documented for claude/skills/ in CLAUDE.md.)
  #
  # The copy is built beside the target and swapped in with an ATOMIC directory
  # exchange, so neither a half-written tree nor a MISSING directory is ever
  # visible at the path Brave loads from. Re-pointing Brave at this path is a
  # MANUAL operator step (brave://extensions → remove the repo-path entry → Load
  # unpacked → this directory), once per profile; until then the previously
  # loaded repo-path extension keeps working unchanged.
  #
  # SWAP = `mv -T --exchange` (renameat2 RENAME_EXCHANGE), fallback to a
  # rename-away dance. Two earlier designs were rejected/regressed:
  #   * hash-suffixed dir + symlink flip — the unpacked extension's ID is derived
  #     from its absolute directory path, and `ping` reports `chrome.runtime.id`
  #     precisely so the operator can confirm WHICH directory Brave loaded. A
  #     target whose name changes every switch risks changing that ID every
  #     switch (whether Chromium canonicalises a symlink before hashing is
  #     unmeasured), destroying the id-stability the probe depends on.
  #   * `rm -rf "$bbDst"` then `mv -T` — measured to DELETE the deployed tree
  #     outright under two concurrent activations (3/3 trials: one side exits 0
  #     while the other removes the tree it just installed, then aborts). Worse
  #     than the nesting bug it replaced: an absent directory is exactly the
  #     mid-verification breakage this deploy exists to prevent.
  # RENAME_EXCHANGE gets atomicity AND a stable path — no trade needed. It swaps
  # the two directories in one syscall, so $bbDst is never absent for any window,
  # and the OLD tree lands at $bbTmp for cleanup AFTER the swap succeeded (so a
  # failed deploy leaves the previous extension in place rather than nothing).
  # Linux/ext4-specific; the fallback covers a filesystem without renameat2.
  #
  # Concurrency (measured, distinct PIDs, 400-file source, 3/3 trials): the
  # exchange path leaves BOTH activations exiting 0 with a complete tree and zero
  # leftovers; the fallback path always leaves $bbDst present and complete, with
  # the loser failing LOUDLY and one `.old.<pid>` that the next run's sweep
  # reclaims. $$-suffixed temp names are collision-free BY CONSTRUCTION across
  # processes (note: a bash SUBSHELL inherits its parent's $$, so `( … ) &` does
  # not model two switches — separate processes do). The sweep below is what
  # bounds the leak that a fixed temp name was wrongly used to solve: it reclaims
  # only siblings whose owning PID is gone, never a live activation's.
  home.activation.browserBridgeExtension =
    lib.hm.dag.entryAfter ["writeBoundary"] ''
      bbSrc=${../scripts/browser-bridge/extension}
      bbDst="$HOME/.local/share/browser-bridge-ext"
      bbTmp="$bbDst.new.$$"
      $DRY_RUN_CMD mkdir -p "$HOME/.local/share"

      # Sweep leftovers from interrupted earlier runs — but ONLY those whose
      # owning PID is dead, so a concurrent activation's in-flight temp survives.
      # `cp -rL` from the read-only store yields a 0555 tree and `rm -rf` cannot
      # unlink inside one (measured), which under activation's `set -eu` would
      # abort the entire switch — so chmod always precedes rm. chmod on a missing
      # path exits non-zero, hence the `|| true`.
      # Known, accepted limits: (1) a `…new.<pid>` whose PID has been REUSED by
      # an unrelated process is spared forever — leak-only, never corruption, and
      # vanishingly rare at pid_max 4194304; (2) a suffix that is not a PID at
      # all (`…new.abc`) is spared deliberately — this loop must not delete a
      # directory it cannot prove it created. The inverse (sweeping a LIVE
      # sibling mid-flight) is impossible.
      for bbOld in "$bbDst".new.* "$bbDst".old.*; do
        [ -e "$bbOld" ] || continue
        bbPid="''${bbOld##*.}"
        # An EMPTY suffix (`…new.`) is ours-but-broken, and must be swept here:
        # it would otherwise be spared forever, because `[ -d "/proc/" ]` is TRUE.
        if [ -n "$bbPid" ]; then
          case "$bbPid" in (*[!0-9]*) continue;; esac
          [ "$bbPid" = "$$" ] && continue
          [ -d "/proc/$bbPid" ] && continue   # a LIVE activation owns it
        fi
        $DRY_RUN_CMD chmod -R u+rwX "$bbOld" 2>/dev/null || true
        $DRY_RUN_CMD rm -rf "$bbOld"
      done

      $DRY_RUN_CMD chmod -R u+rwX "$bbTmp" 2>/dev/null || true
      $DRY_RUN_CMD rm -rf "$bbTmp"            # cp -rL NESTS if the target exists
      $DRY_RUN_CMD cp -rL "$bbSrc" "$bbTmp"   # -L → real files, not store symlinks
      $DRY_RUN_CMD chmod -R u+rwX "$bbTmp"    # writable for the NEXT switch's rm -rf

      # Install $bbTmp at $bbDst. Two attempts, because every branch test below
      # is a TOCTOU against a concurrent activation: the path can change shape
      # between the test and the syscall. Re-deciding once absorbs that instead
      # of acting on a stale observation. EVERY failure path either restores the
      # previous tree or leaves it untouched, and prints a named error before
      # aborting — a bare `mv:` message from `set -e` is not an acceptable exit.
      bbDone=""
      bbTry=0

      # --dry-run must SHOW the swap, perform nothing, and never fail. The loop
      # below cannot run under dry-run: its branches are decided from filesystem
      # effects that $DRY_RUN_CMD deliberately does not produce (nothing was
      # copied, nothing gets removed), so it would re-decide the same branch,
      # exhaust its attempts and abort the dry run — with blame text about a
      # concurrent activation that is not involved. Gate it out, and print what
      # would run, since that is the entire job of this mode.
      if [ -n "''${DRY_RUN_CMD:-}" ]; then
        echo "would install the browser-bridge extension at $bbDst:"
        echo "  mv -T --exchange $bbTmp $bbDst"
        echo "  # if RENAME_EXCHANGE is unavailable, the weaker fallback:"
        echo "  mv -T $bbDst $bbDst.old.$$ && mv -T $bbTmp $bbDst"
        bbDone=1
      fi

      while [ -z "$bbDone" ] && [ "$bbTry" -lt 2 ]; do
        bbTry=$((bbTry + 1))
        # `[ ! -L ]` matters: chmod -R FOLLOWS a symlink-to-directory and would
        # rewrite the modes of whatever it points at (measured). If an operator
        # ever symlinks this path at the repo checkout, a switch must not chmod
        # the repo — replace the link instead (the elif below).
        if [ -d "$bbDst" ] && [ ! -L "$bbDst" ]; then
          # THE ATOMIC PATH — the only one that never exposes an absent or
          # partial tree at $bbDst. Capture stderr: a failure here is NOT
          # necessarily "no RENAME_EXCHANGE" (EXDEV/ENOSPC/EACCES/EBUSY, or a
          # coreutils without the option, all land here and are
          # indistinguishable), so report what actually happened rather than
          # asserting a cause, and say plainly that the fallback is weaker.
          if bbErr="$($DRY_RUN_CMD mv -T --exchange "$bbTmp" "$bbDst" 2>&1)"; then
            bbDone=1
          else
            echo "browser-bridge: atomic directory exchange failed at $bbDst" \
                 "(mv said: $bbErr). Falling back to the rename-away swap," \
                 "which BRIEFLY leaves $bbDst absent — a Brave reload during" \
                 "that window can fail. Re-run the switch if it did." >&2
            bbBak="$bbDst.old.$$"
            $DRY_RUN_CMD rm -rf "$bbBak"
            if ! $DRY_RUN_CMD mv -T "$bbDst" "$bbBak"; then
              echo "browser-bridge: extension deploy FAILED before touching" \
                   "$bbDst — the previously deployed tree is UNCHANGED." >&2
              false                           # loud: set -e aborts the switch
            fi
            # $bbDst is absent from here until one of the two moves below.
            if $DRY_RUN_CMD mv -T "$bbTmp" "$bbDst"; then
              $DRY_RUN_CMD chmod -R u+rwX "$bbBak" 2>/dev/null || true
              $DRY_RUN_CMD rm -rf "$bbBak"
              bbDone=1
            else
              if $DRY_RUN_CMD mv -T "$bbBak" "$bbDst"; then
                echo "browser-bridge: extension deploy FAILED — RESTORED the" \
                     "previously deployed tree at $bbDst." >&2
              else
                echo "browser-bridge: extension deploy FAILED and the previous" \
                     "tree could not be restored. It is at $bbBak — move it to" \
                     "$bbDst by hand, or re-run home-manager switch." >&2
              fi
              false                           # loud: set -e aborts the switch
            fi
          fi
        elif [ -e "$bbDst" ] || [ -L "$bbDst" ]; then
          # A symlink or a plain file sits at the path (an operator artefact —
          # the documented rollback goes via brave://extensions and never
          # creates one). Remove it so the next iteration can install.
          #
          # `rm -f`, NOT `rm -rf`, and that is the whole point: the `elif` test
          # above is a TOCTOU, so by the time this line runs the path may have
          # become a DIRECTORY that a concurrent activation just installed.
          # `rm -rf` deletes it silently with rc=0 — measured, 5/5 — which is
          # exactly the hazard this loop exists to close. `rm -f` REFUSES a
          # directory (rc=1, tree intact — measured), removes a plain file, and
          # on a symlink drops the LINK, not its target (measured). `|| true`
          # keeps the refusal non-fatal so iteration 2 re-decides and takes the
          # atomic exchange branch — the intended recovery.
          $DRY_RUN_CMD rm -f "$bbDst" || true
        else
          # Absent — first install. If a concurrent activation wins the race and
          # creates the directory first, `mv -T` refuses (it will not descend
          # into an existing directory) and the next iteration exchanges into it.
          if $DRY_RUN_CMD mv -T "$bbTmp" "$bbDst"; then
            bbDone=1
          elif [ "$bbTry" -ge 2 ]; then
            echo "browser-bridge: extension deploy FAILED — could not install" \
                 "at $bbDst. No DIRECTORY was removed (a symlink or plain file" \
                 "at that path may have been); the new tree is at $bbTmp." \
                 "Re-run home-manager switch." >&2
            false                             # loud: set -e aborts the switch
          fi
        fi
      done

      # Belt-and-braces: the loop can only exit with bbDone empty by running out
      # of attempts, and that must never pass silently for a deploy.
      if [ -z "$bbDone" ]; then
        echo "browser-bridge: extension deploy FAILED — $bbDst did not settle" \
             "after $bbTry attempts (a concurrent activation is the likely" \
             "cause). No DIRECTORY was removed (a symlink or plain file at that" \
             "path may have been); the new tree is at $bbTmp. Re-run" \
             "home-manager switch." >&2
        false                                 # loud: set -e aborts the switch
      fi

      # After a successful exchange $bbTmp holds the OLD tree; after the fallback
      # or first install it no longer exists. Either way, clean up.
      $DRY_RUN_CMD chmod -R u+rwX "$bbTmp" 2>/dev/null || true
      $DRY_RUN_CMD rm -rf "$bbTmp"
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
  # session-created hook: names an auto-numbered session after its cwd. It
  # SOURCES scratch-slots.sh from its own directory, so it must land beside it
  # under ~/.config/tmux/ — the same reason that file is deployed there.
  home.file.".config/tmux/autoname-session.sh" = {
    source = ../scripts/tmux-autoname-session.sh;
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

  # tmux-resurrect post-save hook — captures claude-session-to-window mappings
  # every 15 min (continuum's save interval).  Runs in background so it never
  # blocks continuum's own save.  See nix/programs/tmux/default.nix for the
  # hook wiring.
  home.file.".config/tmux/tmux-post-save.sh" = {
    source = ../scripts/tmux-post-save.sh;
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
  # Shared by BOTH session summarisers (claude/session-tailer.py and
  # opencode/session_tailer.py) — the one definition of the `changed_paths*`
  # payload block. It must land at the collector ROOT, beside collector.py,
  # because each tailer puts its own grandparent dir on sys.path to import it;
  # that resolves identically in the repo (scripts/collector/) and here.
  # 🔴 Without this entry the switch succeeds and the tailers die on ImportError
  # at the next timer tick. scripts/tests/test_collector_deploy_declares.py
  # pins it for exactly that reason.
  home.file.".config/activity-collector/changed_paths.py".source =
    ../scripts/collector/changed_paths.py;

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
  # NOTE: the UNPACKED EXTENSION is deliberately NOT a home.file here — it is
  # copied by the browserBridgeExtension activation script below. See there for
  # why a real copy rather than store symlinks.
  # i3 focus collector. Reuses keylog's spool_emit (the v1 line format), so
  # keylog/ must be present alongside it (it always is — shipped above).
  home.file.".config/activity-collector/i3" = {
    source = ../scripts/collector/i3;
    recursive = true;
  };

  # Global Claude Code behavioural config — single source of truth for both
  # hosts (these were drifting when edited per-host). Synced via scripts/ship.sh.
  # NOTE: now read-only symlinks into the nix store → edit `devrc/claude/*.md`
  # (or devrc/claude/skills/<name>/SKILL.md) then `home-manager switch` (or
  # ship.sh), NOT `~/.claude/*` directly.
  # CLAUDE.md stays per-host/mutable (host-specific).
  home.file.".claude/RULES.md" = {
    source = ../claude/RULES.md;
    force = true;  # overwrite the pre-existing unmanaged file on first switch
  };
  home.file.".claude/PRINCIPLES.md" = {
    source = ../claude/PRINCIPLES.md;
    force = true;
  };
  # Skills — the SINGLE surface now. Upstream merged custom commands INTO skills
  # (a `.claude/commands/deploy.md` and a `.claude/skills/deploy/SKILL.md` both
  # produce `/deploy`), so `claude/commands/` was retired and all 17 commands were
  # migrated to `claude/skills/<name>/SKILL.md`. A command was effectively a skill
  # with null frontmatter: no bundled-file directory, no progressive disclosure,
  # and — the actual loss — no model invocation, so it only ever fired when a human
  # typed it. Every file under devrc/claude/skills/<name>/ (including `reference/`)
  # lands as a read-only store symlink at ~/.claude/skills/<name>/, so skills ship
  # to all hosts in lockstep. Edit in devrc/claude/skills/ then switch.
  # 🔴 SOURCE IS `claudeSkills`, NOT `../claude/skills` — it is the same tree plus
  # clickup's built node_modules, which has to live in the store copy for node to
  # resolve it. Read the `claudeSkills` comment in the `let` block before changing
  # this. node_modules is NOT committed (`claude/skills/.gitignore` pins that).
  # 🔴 The gitignore is the ONLY thing preventing a committed node_modules — the
  # earlier claim here, that "a path cannot be both", was false: `claudeSkills`
  # is one derivation, so a committed tree and the injected one do not collide
  # in home.nix at all. They collide inside `cp -R` + `ln`, which is why that
  # link is `ln -sT` (a bare `ln -s` would nest the link inside the copied
  # directory and succeed). See the `claudeSkills` comment in the `let` block.
  home.file.".claude/skills" = {
    source = claudeSkills;
    recursive = true;
    force = true;
  };
  # 🔴 close-the-loop's LEDGER — a deliberate mkOutOfStoreSymlink exception, for
  # CORRECTNESS, not convenience. The skill's contract is "read STATE.md first,
  # UPDATE it last, every run" and its `allowed-tools` grants Write/Edit — but while
  # the two files lived under `claude/skills/close-the-loop/` the `recursive = true`
  # mapping above landed them as read-only /nix/store symlinks, so every write
  # silently failed and the skill's central mechanism was INERT (measured 2026-08-10:
  # `test -w ~/.claude/skills/close-the-loop/STATE.md` -> false).
  #
  # The SOURCE therefore moved OUT of the skill tree to `claudedocs/close-the-loop/`
  # — it has to, because a path cannot be both a recursive store symlink and an
  # out-of-store one; the two `home.file` entries would collide. From there these
  # two links put them back at the deployed path the skill expects, pointing at the
  # live checkout, so a write lands in the working tree where it is version
  # controlled and applies with no switch. Side benefit: 116 KB of ledger (35 KB
  # STATE + 81 KB ARCHIVE) no longer enters the nix store on every switch.
  #
  # ⚠ A write here is UNCOMMITTED WORK in devrc — commit it in the SAME session
  # (RULES.md -> "Docs/notes written into a working tree are UNSAVED WORK").
  home.file.".claude/skills/close-the-loop/STATE.md".source =
    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/claudedocs/close-the-loop/STATE.md";
  home.file.".claude/skills/close-the-loop/ARCHIVE.md".source =
    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/claudedocs/close-the-loop/ARCHIVE.md";
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
  # 🔴 guard_core.py — the SHARED, caller-agnostic checking core. bash-guard.py
  # is now a thin adapter that imports it from its OWN directory, so this file
  # MUST land next to it or the guard fails closed on every Bash call (it denies
  # with an actionable message rather than passing commands through unchecked).
  # The SAME source file is also deployed to ~/.config/opencode/guard_core.py
  # below and driven by the opencode plugin — one implementation, two harnesses,
  # two named policies. "claude-code" was the frozen original six until
  # 2026-08-02, when SIX argv checks were added to it by explicit operator
  # decision — `git -C <p> reset --hard`, `git stash`, `git clean -f`,
  # `talosctl … reset`, `mkfs*` and `dd of=/dev/<block-dev>`, all 🔴 in RULES.md
  # or unconditionally destructive, and all measured ALLOW against the live hook
  # beforehand. "opencode" = those plus the one still-opencode-only family,
  # `rm -rf` of a critical path (held back deliberately — it has frequent
  # legitimate use here, so a deny would train the operator to route around it).
  # POLICIES in guard_core.py is the authority; test_guard_core.py pins it.
  home.file.".claude/hooks/guard_core.py" = {
    source = ../scripts/claude-hooks/guard_core.py;
    force = true;
  };
  # `browser` skill — the DELIBERATE EXCEPTION to the store-symlink pattern above.
  # Its source of truth is the browser-bridge subsystem in THIS repo
  # (scripts/browser-bridge/{SKILL.md,browser}), NOT devrc/claude/skills/. So rather
  # than copy those into the store we point ~/.claude/skills/browser/ at the live,
  # MUTABLE repo checkout via mkOutOfStoreSymlink — the skill then always tracks the
  # working tree (editable in place; a SKILL.md/CLI edit shows up with no switch).
  # Ships to both hosts declaratively, replacing the previously HAND-MADE symlinks.
  # One-time on each host: `rm ~/.claude/skills/browser/{SKILL.md,browser}`
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
  # search-tool-nudge fires PostToolUse on Bash calls that are a TREE SEARCH (`grep -r`,
  # bare `rg`, `find <path> -name`, `ls -R`, `find | xargs grep`) and points at the
  # native Grep/Glob tools. Same "prose didn't work, so nudge structurally" reasoning as
  # shell-env-nudge: over a 30-day telemetry window Bash was 71% of all tool calls
  # (workbench 31,355 / laptop 6,164) against 50 Grep+Glob calls total — zero on the
  # laptop — despite RULES.md already saying "Grep over bash grep, Glob over find".
  # Conservative by design (false positives are expensive on a per-Bash-call hook) and
  # deduped to once per kind per session.
  home.file.".claude/hooks/search-tool-nudge.py" = {
    source = ../scripts/claude-hooks/search-tool-nudge.py;
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
  # next-step-nudge fires on Stop when the turn that just ended named no next step, and
  # asks for one line saying what happens next. Measured over 14 days of operator
  # prompts: `recommend*` is 216 occurrences against 542 `proceed` + 134 `yes` — roughly
  # 1 in 3.5 approvals is a round trip that exists only because the assistant stopped
  # without saying what it would do. Concentrated in datapacket-talos (152) and cli (33),
  # not devrc, which is why this is a hook rather than devrc prose.
  #
  # 🔴 It does NOT block. It emits `hookSpecificOutput.additionalContext` on stdout and
  # exits 0 — the model gets the line as non-error feedback and the conversation
  # continues. An earlier revision asserted that additionalContext is unsupported on Stop
  # and used exit 2 instead; that premise was false (checked against the installed CLI's
  # own schema, claude-code 2.1.220), and exit 2 both blocked the turn and raised a "Stop
  # hook error occurred" notification on every fire. Still bounded to at most ONE fire per
  # session by an atomic claim, never twice in a row (stop_hook_active), off for subagents
  # and for headless callers (NEXT_STEP_NUDGE_OFF), with every error path exiting 0.
  # Measured on 11,789 real turn-final messages: fires on 0.8% of turns the operator
  # answered with a bare approval and 10.6% of the turns where they had to ask for a
  # recommendation.
  #
  # Registered on Stop (NOT SubagentStop) per-host by register-nudge-hook.py, appended
  # alongside the three pre-existing Stop hooks it must never clobber.
  home.file.".claude/hooks/next-step-nudge.py" = {
    source = ../scripts/claude-hooks/next-step-nudge.py;
  };
  # 🔴 THE AGENT ACTIVITY LEDGER — writer 1 (Claude Code), plus the shared module
  # it and `scripts/session-manager` BOTH read the record shape from.
  #
  # Why the module ships here and not only in the repo: the hook runs as
  # `python3 ~/.claude/hooks/agent-ledger-hook.py`, and Python puts the SCRIPT's
  # directory on sys.path — so `agent_ledger.py` must sit beside it, exactly the
  # arrangement bash-guard.py already has with guard_core.py. Same source file as
  # `scripts/lib/agent_ledger.py`, which session-manager loads by explicit path,
  # so writer and reader agree on the record BY CONSTRUCTION. A second copy of
  # the shape is how the two halves of a ledger drift apart while both look fine.
  #
  # What it buys: `session-manager`'s DEFAULT view lost `age_secs`, the `stale`
  # bucket derived from it, and `claude_session_id` when #419 switched fuzzyclaw
  # off — measured 2026-08-12, 0 rows with an age and 0 with a session id. This
  # writer restores all three from a source this repo owns. Spec:
  # claudedocs/spec-agent-activity-ledger.md (#428).
  #
  # 🔴 BOTH files are NEW, so both must be `git add`ed or the flake silently
  # omits them and the switch succeeds with the hook absent — this repo's
  # standing trap (CLAUDE.md).
  home.file.".claude/hooks/agent-ledger-hook.py" = {
    source = ../scripts/claude-hooks/agent-ledger-hook.py;
  };
  home.file.".claude/hooks/agent_ledger.py" = {
    source = ../scripts/lib/agent_ledger.py;
  };

  # 🔴 THE REGISTRAR ITSELF — the hook that makes the hooks above DO anything.
  # settings.json is per-host and unmanaged (permissions/allowlists), so a hook
  # script landing in ~/.claude/hooks/ registers nothing by itself; this script
  # appends the missing entries. Until 2026-08-13 it had NO home.file entry at
  # all — only the two comments above mentioning it — and nothing ever invoked
  # it. Measured consequence: next-step-nudge.py (#452) deployed to both hosts
  # and sat INERT, because the switch reported success about the LAYER BELOW
  # (the file landed) and nothing checked the layer above (it was registered).
  # Same shape as this repo's `git add`-or-the-flake-omits-it trap.
  #
  # No `force = true` and no `dropStaleClaudeHooks` entry, unlike bash-guard.py:
  # that treatment exists to displace a PRE-EXISTING hand-placed regular file,
  # and this path has never been hand-placed. MEASURED 2026-08-13 on both hosts
  # before the first switch that deploys it: `ls -la ~/.claude/hooks/` shows
  # eight entries on the workbench and eight on the laptop, all store symlinks,
  # and register-nudge-hook.py among none of them. If a foreign file ever does
  # appear here the switch fails LOUDLY ("would be clobbered") rather than
  # silently leaving it unmanaged — add it to dropStaleClaudeHooks then.
  home.file.".claude/hooks/register-nudge-hook.py" = {
    source = ../scripts/claude-hooks/register-nudge-hook.py;
  };

  # ...and RUN it, every switch. Delivering the registrar without invoking it
  # would only move the manual step, not remove it.
  #
  # 🔴 SLOT: after "linkGeneration", NOT merely after "writeBoundary".
  # linkGeneration is the step that creates the home-file symlinks, and it is
  # itself `entryAfter ["writeBoundary"]` — so two entries that both declare
  # only writeBoundary have NO order between them. MEASURED in this host's
  # current generated `activate`: activityCollectorEnv and
  # browserBridgeExtension (both writeBoundary-only) are emitted at lines 290
  # and 300, linkGeneration at 502 — i.e. the topo sort put them BEFORE the
  # files land. Copying that precedent here would have run the registrar
  # before ~/.claude/hooks/register-nudge-hook.py existed on the one switch
  # where it matters (the first one on each host), and worked on every switch
  # after — a bug visible only on a fresh host.
  #
  # VERIFIED on the built artifact rather than argued: `nix build` of this
  # config's activation-script derivation emits "registerClaudeHooks" at line
  # 546, after "linkGeneration" at 502. (Building that derivation activates
  # nothing — it only writes the script.)
  #
  # The wrapper never returns non-zero, so this cannot abort a switch under
  # activation's `set -eu -o pipefail`; see the contract in its header.
  # $DRY_RUN_CMD keeps `home-manager build`/dry-run read-only.
  home.activation.registerClaudeHooks =
    lib.hm.dag.entryAfter [ "writeBoundary" "linkGeneration" ] ''
      $DRY_RUN_CMD ${pkgs.bash}/bin/bash ${../scripts/claude-hooks/register-hooks-activation.sh} \
        "$HOME/.claude/hooks/register-nudge-hook.py" ${pkgs.python312}/bin/python3
    '';

  # ------------------------------------------------------------------------- #
  # opencode — global config, instruction file, env plugin and subagents.
  # Source of truth: devrc/scripts/opencode/ (+ the generated AGENTS.md below).
  # See scripts/opencode/README.md for the measured facts behind each choice.
  # ------------------------------------------------------------------------- #

  # 🔴 GENERATED, not symlinked — and that is the whole point.
  # opencode does NOT expand `@`-imports inside AGENTS.md/CLAUDE.md (measured on
  # v1.18.4 with an all-tools-denied agent, so no file read was possible: an
  # imported passphrase came back NONE, the same content inline came back
  # verbatim). ~/.claude/CLAUDE.md is ~1.5 KB of `@PRINCIPLES.md` + `@RULES.md`
  # import lines, so pointing opencode at it would deliver NONE of the 32 KB of
  # actual rules. A project AGENTS.md also SUPPRESSES CLAUDE.md (first match
  # wins), so this is the file opencode really reads.
  #
  # Concatenating at switch time means it can never drift from the sources
  # Claude Code reads. Measured result: 38,363 B / 37.5 KB ≈ 8.9k tokens (at the
  # 4.31 B/token measured on this exact content) — safe. (For scale: a 331 KB
  # AGENTS.md causes a permanent compaction loop.)
  # scripts/tests/test_opencode_config.py pins the content and a 100 KB ceiling.
  home.file.".config/opencode/AGENTS.md".text =
    builtins.readFile ../claude/PRINCIPLES.md
    + "\n\n" + builtins.readFile ../claude/RULES.md
    + "\n\n" + builtins.readFile ../claude/opencode-addendum.md;

  # Global config: model + small_model, the cheap-model pinning of the hidden
  # title/summary/compaction agents, a genuinely read-only `plan`, and the
  # permission block.
  # 🔴 The permission block's ordering is LOAD-BEARING and is the INVERSE of
  # Claude Code — opencode is LAST-MATCH-WINS, so `"*": "allow"` comes FIRST and
  # the denies after. Do not sort those keys. See the file's own header comment.
  home.file.".config/opencode/opencode.jsonc".source = ../scripts/opencode/opencode.jsonc;

  # 🔴 THE ENFORCEMENT LAYER. opencode.jsonc's globs are FRICTION; this plugin is
  # the control. It runs guard_core.py's "opencode" policy on every bash call
  # from `tool.execute.before` and THROWS on a deny, which hard-blocks the call.
  #
  # Why a plugin and not more globs: a glob matches a command node's full text,
  # so `talosctl -n <ip> reset` (a node wipe) resolved ALLOW at c1e4c02 because
  # the pattern required the tool and the verb to be adjacent. guard_core.py
  # tokenises instead — it splits on `;`/`&&`/`||`/`|`/`&`, strips `VAR=…`
  # prefixes and sudo/doas/env/timeout wrappers, recurses into `bash -c '…'`,
  # and reasons about argv.
  #
  # Why `tool.execute.before` and not `permission.ask`: MEASURED on 1.18.4 —
  # `permission.ask` is in the Hooks type and its `output.status` is typed
  # `"ask"|"deny"|"allow"`, but it NEVER FIRED in any probe (not on the allow
  # path, not on the ask path). `tool.execute.before` fired on every bash call.
  # So DENY is expressible from a plugin and ASK is not; ask-grade families stay
  # as globs.
  #
  # 🔴 Same deployment constraints as env.js: directly in `plugin/`, `.js` only,
  # non-recursive glob.
  home.file.".config/opencode/plugin/guard.js".source = ../scripts/opencode/plugin/guard.js;

  # 🔴 THIS PATH IS LOAD-BEARING AND IS RESOLVED BY $HOME, NOT BY THE PLUGIN'S
  # OWN LOCATION. The plugin used to compute `../guard_core.py` from
  # `import.meta.url`, which reads correctly against THIS declaration and is
  # wrong in reality: `home.file` makes the deploy path a symlink into the store,
  # node resolves `import.meta.url` through it, and the store is FLAT
  # (/nix/store/<hash>-hm_guard.js), so `..` was `/nix`. The guard failed closed
  # on every bash call in opencode. guard.js now looks in
  # `$HOME/.config/opencode/guard_core.py` — i.e. exactly the attrpath below —
  # so moving or renaming this entry breaks the guard. See the resolution comment
  # in scripts/opencode/plugin/guard.js and the deployed-layout regression tests
  # in scripts/tests/test_opencode_guard_plugin.py.
  #
  # The SAME source file that backs ~/.claude/hooks/guard_core.py is deployed
  # here too. Two independent deployments of one implementation: opencode's guard
  # does not depend on ~/.claude/ existing, and Claude Code's does not depend on
  # ~/.config/opencode/ existing.
  home.file.".config/opencode/guard_core.py".source = ../scripts/claude-hooks/guard_core.py;

  # Activity telemetry plugin — emits session/prompt/tool-call events into
  # activity.events via ~/.config/activity-collector/emit.
  #
  # 🔴 THIS ENTRY IS THE ONLY DEPLOYMENT. It replaces
  # scripts/collector/opencode/deploy-plugin.sh, a hand-run script that had to
  # be remembered per host — and was not: it was run on the workbench on
  # 2026-07-29 and NEVER on the laptop, which therefore recorded zero
  # kind=tool-call rows for the plugin's entire existence. Same constraints as
  # guard.js/env.js: directly in `plugin/`, `.js` only, non-recursive glob.
  #
  # 🔴 Do NOT also deploy to `plugins/` (plural). opencode's glob is
  # `{plugin,plugins}/*.{ts,js}` and reads BOTH, so a file in each loads the
  # plugin TWICE and double-emits every event. opencodeDropStalePluginsDir below
  # removes the pre-existing plural-dir symlink that deploy-plugin.sh left.
  home.file.".config/opencode/plugin/activity.js".source =
    ../scripts/collector/opencode/activity-plugin.js;

  # `shell.env` plugin — the only supported seam for putting environment into
  # opencode's bash tool (there is no `env` config key; setting one is silently
  # ignored). NOTE the bash tool DOES source .zshenv on this host — see the
  # correction in the generated header below; this plugin is belt-and-braces
  # for a non-zsh `$SHELL`, not a workaround for a missing mechanism.
  # 🔴 Must land DIRECTLY in `plugin/` as a `.js`: the glob is
  # `{plugin,plugins}/*.{ts,js}` — non-recursive, and a `.mjs` will NOT load.
  # Hence a single-file entry rather than a recursive dir symlink.
  #
  # 🔴 GENERATED (.text), not symlinked, from nix/agent-handles.nix — the SAME
  # file programs/zsh reads. It used to be a checked-in .js with a hardcoded
  # `/home/zach/workspace/homelab-talos` that duplicated (and had already
  # drifted from) the zsh block: no existence guard, and a KC_PROD zsh lacked.
  # Deriving both from `${config.home.homeDirectory}` removes the second copy.
  #
  # The existence guard matters: an unguarded handle pointing at a missing
  # kubeconfig is the failure mode where a command runs against NO cluster while
  # looking like it worked. `fs.existsSync` is used rather than a shell test
  # because this is a JS hook, and it is evaluated per shell.env call (cheap —
  # a handful of stats) so a checkout appearing mid-session is picked up.
  home.file.".config/opencode/plugin/env.js".text =
    let
      handles = import ./agent-handles.nix { home = config.home.homeDirectory; };
      entry = kind: name: path:
        "  if (${kind}(${builtins.toJSON path})) output.env.${name} = ${builtins.toJSON path};";
      lines =
        (builtins.attrValues (builtins.mapAttrs (entry "isDir") handles.repos))
        ++ (builtins.attrValues (builtins.mapAttrs (entry "isFile") handles.kubeconfigs));
    in
    ''
      // env.js — opencode plugin that injects the repo/kubeconfig handles into
      // every bash tool invocation.
      //
      // 🔴 GENERATED by nix/home.nix from nix/agent-handles.nix. Do NOT edit this
      // file, and do not edit ~/.config/opencode/plugin/env.js (a read-only store
      // symlink). Add or change a handle in nix/agent-handles.nix — that same file
      // also generates the zsh exports Claude Code sees, which is the point.
      //
      // WHY A PLUGIN AT ALL: opencode has NO `env` config key (verified on v1.18.4
      // — setting one is silently ignored), so the `shell.env` hook is the only
      // supported seam for putting variables into the bash tool.
      //
      // 🔴 CORRECTION (measured 2026-08-02, opencode 1.18.4, this host). An earlier
      // version of this comment claimed the bash tool "does NOT source zsh startup
      // files, so the handles devrc exports from .zshenv are invisible to it".
      // THAT IS FALSE HERE. The tool shell is zsh (`ZSH_VERSION` reports 5.9 inside
      // a bash tool call) and it DOES source `.zshenv`: with this plugin absent AND
      // `VITEST_MAX_WORKERS` explicitly unset in the parent environment, the tool
      // still reported `4` — a value set only by `.zshenv`. `$HOMELAB` likewise
      // resolved with no plugin present.
      //
      // The original negative control ("with the plugin present $KC_HOMELAB
      // resolves; with it absent it is empty") was real but MISATTRIBUTED. The
      // kubeconfigs are gitignored and absent from this checkout, so zsh's
      // existence guard correctly declines to export `KC_HOMELAB` — while the old
      // UNGUARDED env.js exported it regardless. The plugin looked load-bearing
      // because it was pointing a handle at a file that does not exist, which is
      // exactly the "runs against no cluster while looking like it worked" failure
      // the k8s agent prompt warns about. Hence the guard above.
      //
      // So this plugin is BELT-AND-BRACES, not a workaround for a missing
      // mechanism: it is independent of `$SHELL`, so the handles survive on a host
      // whose login shell is not zsh. Keep it, but do not restate the old claim.
      //
      // DEPLOYMENT CONSTRAINTS (measured on v1.18.4 — do not "tidy" these away):
      //   * the plugin glob is `{plugin,plugins}/*.{ts,js}` — NON-RECURSIVE, and
      //     `.ts`/`.js` ONLY. A `.mjs` file will NOT load. This file must therefore
      //     land directly at `~/.config/opencode/plugin/env.js`, never in a subdir.
      //   * the hook mutates `output.env`; it does not return a new object.
      import { statSync } from "node:fs";

      // Existence-guarded, mirroring the `[[ -d ]]` / `[[ -f ]]` tests in the zsh
      // block. A handle that points at a missing kubeconfig is worse than an
      // absent one: `KUBECONFIG=$KC_PROD kubectl …` would run against no cluster
      // while looking like it worked.
      const isDir = (p) => { try { return statSync(p).isDirectory(); } catch { return false; } };
      const isFile = (p) => { try { return statSync(p).isFile(); } catch { return false; } };

      export const EnvPlugin = async () => ({
        "shell.env": async (_input, output) => {
      ${builtins.concatStringsSep "\n" (map (l: "  " + l) lines)}
        },
      });
    '';

  # Subagents: nav (read-only navigator, bash DENIED — the deterministic fix for
  # ~356 file-navigation shell-outs), k8s (cluster ops), review (adversarial).
  # Deliberately only three: every available subagent permanently enlarges the
  # PRIMARY agent's `task` tool description on every single request.
  home.file.".config/opencode/agent" = {
    source = ../scripts/opencode/agent;
    recursive = true;
  };

  # opencode skills — symlink the same Claude Code source so both tools share one
  # source of truth. Edit in devrc/claude/skills/ then switch; both hosts + both
  # tools stay in lockstep.
  #
  # The former `~/.config/opencode/commands` mapping (← claude/commands/) is GONE
  # with that directory. MEASURED on opencode 1.18.4 before removing it, against a
  # live `opencode serve` + `opencode debug skill`:
  #   * it reads ~/.config/opencode/skills/<name>/SKILL.md — all 16 devrc skills
  #     enumerated with the right description and location;
  #   * `GET /command` lists SKILLS alongside commands (`"source":"skill"`), so a
  #     migrated skill stays typable as `/<name>` in the TUI;
  #   * it TOLERATES the Claude-Code-only frontmatter keys the migration adds
  #     (when_to_use, argument-hint, allowed-tools, disable-model-invocation,
  #     user-invocable) — a probe skill carrying all five loaded fine;
  #   * the skill BODY becomes the command template, `$ARGUMENTS` included.
  # Negative control (so the above is a fact about the code, not the tool): a
  # SKILL.md with NO frontmatter is DROPPED from the listing — 19 entries -> 18.
  # Not verified: whether opencode substitutes `$ARGUMENTS` at invocation for a
  # skill-sourced command. Its `hints` array is empty for skills and populated for
  # commands, which is at least a TUI autocomplete difference; proving substitution
  # needs a live model call. See the PR for the full matrix.
  # Same `claudeSkills` source as ~/.claude/skills above — same tree, same reason
  # (clickup's node_modules must be in the store copy, not at the deployed path).
  home.file.".config/opencode/skills" = {
    source = claudeSkills;
    recursive = true;
    force = true;
  };
  # Same close-the-loop ledger exception as ~/.claude/skills/ above — the two files
  # are sourced from claudedocs/close-the-loop/ and must be WRITABLE.
  home.file.".config/opencode/skills/close-the-loop/STATE.md".source =
    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/claudedocs/close-the-loop/STATE.md";
  home.file.".config/opencode/skills/close-the-loop/ARCHIVE.md".source =
    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/claudedocs/close-the-loop/ARCHIVE.md";

  # direnvrc — deploy the managed direnv config with layout opencode.
  home.file.".config/direnv/direnvrc" = {
    source = ../scripts/direnv/direnvrc;
    force = true;
  };

  # browser + dl-router: deliberate mkOutOfStoreSymlink exceptions (same as their
  # ~/.claude/skills/ counterparts above) so opencode sees the live working tree.
  home.file.".config/opencode/skills/browser/SKILL.md".source =
    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/scripts/browser-bridge/SKILL.md";
  home.file.".config/opencode/skills/browser/browser".source =
    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/scripts/browser-bridge/browser";
  home.file.".config/opencode/skills/dl-router/SKILL.md".source =
    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/scripts/dl-router/SKILL.md";
  home.file.".config/opencode/skills/dl-router/dl-route".source =
    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/scripts/dl-router/dl-route";
  # 🔴 clickup used to need a mkOutOfStoreSymlink here, pointing opencode's copy at
  # ~/.claude/skills/clickup — the standalone, uncommitted checkout that lived only
  # on this host. Now that the skill is IN `claude/skills/`, the recursive mapping
  # above covers it like every other skill, and that pointer is not merely
  # redundant but a CYCLE: `.config/opencode/skills/clickup` -> `~/.claude/skills/
  # clickup`, whose own links then resolve back through the opencode path. It
  # deployed a self-referential `<hash>-hm_clickup` entry INSIDE the skill dir.
  # node_modules needs no entry either: `claudeSkills` carries it into the tree
  # both mappings are built from.

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

  # Same failure mode as dropStaleClaudeHooks above, for opencode's config:
  # ~/.config/opencode/opencode.jsonc already exists on this host as a
  # HAND-PLACED REGULAR FILE (a 50-byte `$schema`-only stub). checkLinkTargets
  # would abort the whole switch with "would be clobbered", and `force = true`
  # is NOT sufficient to displace a real file at a managed path.
  #
  # Unlike the hooks above — whose content is in git, so deleting them is
  # lossless — this file is UNMANAGED and its content exists nowhere else. So
  # BACK IT UP rather than `rm`: move it aside to a timestamped .bak and let the
  # store symlink take the path. Guarded on `! -L` so a legitimately-managed
  # store symlink is never touched, which also makes this a no-op on every
  # switch after the first (and on a host that never had the stub).
  home.activation.opencodeDropStaleConfig = lib.hm.dag.entryBefore ["checkLinkTargets"] ''
    f="$HOME/.config/opencode/opencode.jsonc"
    if [ -e "$f" ] && [ ! -L "$f" ]; then
      $DRY_RUN_CMD mv -f "$f" "$f.pre-devrc-$(date +%Y%m%d%H%M%S).bak"
    fi
  '';

  # Remove the activity plugin's PRE-DECLARATIVE deployments. Until 2026-08-02
  # it was installed by a hand-run script that symlinked the repo's
  # activity-plugin.js into the config dir. Two stale copies can exist, and both
  # must go before the managed entry above can take effect:
  #
  #   plugin/activity.js  (SINGULAR) — the path home.file now owns. A
  #     pre-existing non-store symlink here makes checkLinkTargets ABORT THE
  #     WHOLE SWITCH with "would be clobbered", and `force` does not help. This
  #     symlink exists on the workbench right now (hand-made 2026-08-02 while
  #     diagnosing the outage), so without this step the very switch that
  #     deploys the fix would fail. Hence entryBefore checkLinkTargets.
  #
  #   plugins/activity.js (PLURAL) — where the old script deployed. opencode's
  #     glob is `{plugin,plugins}/*.{ts,js}` and 1.18.4 reads BOTH (measured: it
  #     logged a load error for each path independently), so leaving this one
  #     would load the plugin TWICE and double-emit every telemetry event.
  #
  # Only ever removes a SYMLINK whose target is the repo's activity-plugin.js —
  # a real file, a store symlink, or a foreign plugin someone put there
  # deliberately is left untouched. Each parent dir is removed only if it is
  # then empty. No-op on a host that never ran the script (the laptop) and on
  # every switch after the first.
  home.activation.opencodeDropStaleActivityPlugin = lib.hm.dag.entryBefore ["checkLinkTargets"] ''
    for d in "$HOME/.config/opencode/plugin" "$HOME/.config/opencode/plugins"; do
      f="$d/activity.js"
      if [ -L "$f" ]; then
        case "$(readlink "$f")" in
          /nix/store/*) ;;                       # managed — leave alone
          *activity-plugin.js)
            $DRY_RUN_CMD rm -f "$f"
            $DRY_RUN_CMD rmdir "$d" 2>/dev/null || true
            ;;
        esac
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
        # Raise thresholds to reduce alert noise (2026-08-05).
        "CPU_MON_THRESHOLD=48"
        "CPU_MON_RUNAWAY_PCT=95"
        "CPU_MON_COOLDOWN=600"
        # Never alert on these (games / Android stack / expected heavy apps):
        # case-insensitive substring match on the busy process's command.
        # COMMA-separated (a space gets split by systemd's Environment= parsing
        # and silently drops entries). Add more, e.g. "anno,logd,steam,lmkd".
        #
        # 🔴 MATCH THE `comm`, NOT THE GAME'S NAME. Linux truncates comm to 15
        # chars, so Farthest Frontier appears as "Farthest Fronti" — an entry of
        # "frontier" would never match anything and would look like it worked.
        # The comm also contains a SPACE, and is_ignored splits on spaces as well
        # as commas, so a two-word entry becomes two independent substrings.
        # Hence the single distinctive first token. Verified against 12 real
        # alerts on the workbench, all reading "Runaway process: Farthest Fronti".
        "CPU_MON_IGNORE=anno,logd,farthest"
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
      ]
      # Opt keylog in to being ptraced by the SIBLING keylog-spin-capture watcher
      # (py-spy) — but ONLY on the host where that diagnostic is enabled. keylog.py
      # calls prctl(PR_SET_PTRACER_ANY) when this is set, which is what lets py-spy
      # attach under Yama ptrace_scope=1 WITHOUT persisting ptrace_scope=0 host-wide.
      # Gated so a host NOT running the capture never opens its keystroke collector
      # to same-UID tracers for nothing. See _allow_any_ptracer in keylog.py for the
      # blast-radius trade-off.
      ++ lib.optional enableKeylogSpinCapture "KEYLOG_ALLOW_ANY_PTRACER=1";
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
      #
      # ...AND on an espanso CONFIG-only change. keylog loads the espanso trigger
      # set ONCE, at process init (keylog.py's ctor → espanso_triggers.load_triggers
      # over ~/.config/espanso/{match/base.yml,config/default.yml}). Pinning only
      # the keylog script directory meant an espanso-only edit to this file left
      # the keylog UNIT DEFINITION byte-identical, so sd-switch did not restart it
      # and the daemon kept matching the OLD trigger set. Counter-example on
      # record: d0156c5 (#325, the :eos rewrite) was espanso-only → no restart.
      # Consequence: a newly added snippet records ZERO fires and the next
      # /espanso-audit prunes it as dead — a self-reinforcing loop.
      # `graphical` guard: the espanso HM module only defines these xdg.configFile
      # entries under `mkIf cfg.enable`, and services.espanso.enable = graphical.
      X-Restart-Triggers = [ "${../scripts/collector/keylog}" ] ++ lib.optionals graphical [
        "${config.xdg.configFile."espanso/match/base.yml".source}"
        "${config.xdg.configFile."espanso/config/default.yml".source}"
      ];
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
  # py-spy attaching to a NON-descendant (keylog.service is a sibling unit) needs
  # same-UID ptrace, which Yama ptrace_scope=1 (the current fleet default) blocks
  # by default. Rather than persist ptrace_scope=0 host-wide — which would expose
  # EVERY same-UID process permanently — keylog.py opts ITSELF in via
  # prctl(PR_SET_PTRACER_ANY) when KEYLOG_ALLOW_ANY_PTRACER=1 (set on
  # keylog.service above, gated on enableKeylogSpinCapture). Verified on a live
  # scope=1 kernel: py-spy dumps both plain and --native frames against a tracee
  # that made the opt-in, and is denied against one that did not. The script still
  # bails on scope >= 2 (CAP_SYS_PTRACE, which the opt-in cannot grant) and gives
  # up after KEYLOG_SPIN_MAX_FAILS incomplete captures, so a broken py-spy cannot
  # turn the 5-min timer into a permanent loop.
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

  # dl-router — loopback sidecar for the media download router. SIBLING to
  # browser-bridge above and modelled on it (loopback bind, bearer token,
  # python312, WantedBy default.target), but a SEPARATE service and a separate
  # extension on purpose: a bug in download routing must not take down the
  # agent command channel.
  #
  # Unlike browser-bridge's standalone server.py this one imports its siblings
  # (matcher/store/dirindex/qbt/...), so the whole directory is run FROM THE
  # NIX STORE rather than symlinked file-by-file into ~/.config. That also
  # leaves ~/.config/dl-router/ free for the two things that must stay
  # writable: the user's config.toml and the runtime-created 0600 token.
  #
  # Port 8791 — 8790 is already taken on the workbench. Inert until
  # library_root is configured AND the unpacked extension is enabled in a Brave
  # profile (see the `dl-router` skill).
  systemd.user.services.dl-router = {
    Unit = {
      Description = "Media download router sidecar (loopback match/index service)";
      After = [ "network.target" ];
    };
    Service = {
      Type = "simple";
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.python312 pkgs.coreutils pkgs.yt-dlp ]}:/run/current-system/sw/bin"
        # Bind loopback only. server.py REFUSES any non-loopback address, so
        # this is belt-and-braces rather than the only guard.
        "DL_ROUTER_HOST=127.0.0.1"
        "DL_ROUTER_PORT=8791"
      ];
      ExecStart = "${pkgs.python312}/bin/python3 ${../scripts/dl-router}/server.py";
      Restart = "always";
      RestartSec = 10;
      # The store path in ExecStart already changes on any source edit, so the
      # unit is rewritten and restarted by itself; this keeps the repo's
      # explicit-trigger convention readable alongside the other units.
      X-Restart-Triggers = [ "${../scripts/dl-router}" ];
    };
    Install = {
      WantedBy = [ "default.target" ];
    };
  };

  # `dl-router` skill — same deliberate mkOutOfStoreSymlink exception as the
  # `browser` skill above: its source of truth is the subsystem in THIS repo,
  # so the skill tracks the live working tree with no switch needed.
  home.file.".claude/skills/dl-router/SKILL.md".source =
    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/scripts/dl-router/SKILL.md";
  home.file.".claude/skills/dl-router/dl-route".source =
    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/scripts/dl-router/dl-route";
  # The CLI on PATH (~/.local/bin is in home.sessionPath). Out-of-store so
  # `dl-route` always runs the checked-out code — it resolves its sibling
  # modules from its own resolved path.
  home.file.".local/bin/dl-route".source =
    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/scripts/dl-router/dl-route";

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
  # (NO plaintext secret at rest — same recipe as the /initiative-scan skill), so
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

  # ── PASSIVE DRIFT DEADMAN (scripts/drift-check.sh) ───────────────────────────
  # The gap this closes: `scripts/ship.sh` converges both hosts correctly, and a
  # host it cannot fast-forward is SKIPPED and left as found (rc=8). But NOTHING
  # RUNS SHIP ON A SCHEDULE — so a skipped host silently stops receiving every
  # change while looking completely healthy. That has now happened twice
  # (2026-08-06, 2026-08-09), both times found only because a human happened to
  # ship something unrelated and read the per-host lines.
  #
  # This unit is the thing that looks when nobody is looking. It is READ-ONLY: it
  # fetches (remote-tracking refs only) and reports. It never checks out, merges,
  # fast-forwards, rebases, resets, or runs home-manager.
  #
  # scripts/tests/test_drift_check.py enforces that statically, via an ALLOWLIST
  # of read-only git subcommands anchored at every command separator (`;`, `&&`,
  # `||`, `|`, `$(`) — not the first-word keyword grep it started as, which
  # missed `git update-ref`, `git config`, `git worktree add`, `rm -rf "$repo"`
  # and anything after a `&&`. It also recurses through wrappers (`timeout`,
  # `flock`, `stdbuf`, `ionice`, `nice`) and through `ssh <target> …` /
  # `bash -c` / `sh -c`; `ssh <target> git checkout …` was invisible to the
  # static AND the behavioural layer until #371 (it mutates the far host, and
  # `ssh` is stubbed in every behavioural test). The one file the script writes
  # is its consecutive-unreachable counter under $XDG_STATE_HOME/drift-check,
  # and that is itself pinned by an asserted ledger of redirection targets.
  #
  # 🔴 The static layer is NOT a proof of passivity, and the comment above is
  # scoped on purpose: it can only resolve command words that are literally in
  # the file. A `git` reached through expansion (`$g checkout`, `eval "$cmd"`)
  # or a payload assembled at runtime and piped over the ssh hop resolves to
  # nothing static. The BEHAVIOURAL tests close that for the local checkout and
  # for shapes nobody enumerated; nothing closes it for a runtime-built mutation
  # of the REMOTE host. See the "PASSIVITY" header block in drift-check.sh.
  #
  # ALERTING: no new notification mechanism. The script exits non-zero on drift,
  # the unit enters `failed`, and the EXISTING OnFailure=notify-failure@%n.service
  # turns that into the same sticky critical dunst toast every other important
  # user unit already uses. The toast names the unit; the rc legend + per-host
  # lines are in `journalctl --user -u drift-check`.
  #
  # 🔴 WHAT DOES **NOT** TOAST: an unreachable remote. This timer is serverMode-
  # gated, i.e. it runs on the WORKBENCH ONLY, so its remote leg always ssh's to
  # the LAPTOP — which is routinely shut, asleep or off-LAN. Failing the unit on
  # that would fire the same sticky critical toast as a genuine rc 8 up to 4× a
  # day, and a permanently-red gate trains you to click through the one alert
  # that must keep its meaning. The script therefore REPORTS an unreachable
  # remote every run but only escalates to rc 13 after
  # DRIFT_UNREACHABLE_ESCALATE consecutive misses (default 4 ≈ 24h at this
  # cadence), resetting the moment the host answers. Below that threshold the
  # remote leg contributes nothing to the exit code — so a local rc 8 with the
  # laptop shut still exits 8, still fails the unit, and still toasts.
  #
  # The service is emitted UNCONDITIONALLY (any host can run it by hand); only the
  # TIMER's timers.target wiring is gated — see enableDriftDeadman above.
  systemd.user.services.drift-check = {
    Unit = {
      Description = "Passive drift deadman — is either devrc host silently no longer receiving changes?";
      After = [ "network-online.target" ];
      Wants = [ "network-online.target" ];
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "oneshot";
      # Two `git fetch`es plus one ssh round trip. The ConnectTimeout inside the
      # script is 10s, so this ceiling only ever fires on a wedged fetch; the
      # cgroup is killed and the timer re-arms on the next OnUnitActiveSec.
      TimeoutStartSec = 180;
      Environment = [
        # iproute2 is load-bearing, not incidental: `ip -4 -o addr show` is how
        # local_ipv4s identifies WHICH host this is (both report hostname `nixos`).
        # Without it detection returns "unknown" and the script exits 6.
        "PATH=${lib.makeBinPath [ pkgs.git pkgs.openssh pkgs.iproute2 pkgs.bash pkgs.coreutils pkgs.gawk pkgs.gnused pkgs.gnugrep ]}"
        "HOME=%h"
      ];
      ExecStart = "${pkgs.bash}/bin/bash %h/workspace/devrc/scripts/drift-check.sh";
      # Re-run the unit when either the checker or the shared host-identity
      # predicate changes.
      X-Restart-Triggers = [
        "${../scripts/drift-check.sh}"
        "${../scripts/lib/host-role.sh}"
      ];
    };
  };

  # 6-hourly, not daily. The choice is bounded by the incident: on 2026-08-06 the
  # workbench was blocked "for hours"; on 2026-08-09 three commits sat un-pushed
  # long enough for a whole day's changes to miss that host. Daily would leave a
  # ~24h window in which every merge silently misses a host — which is the failure
  # itself, only shorter. 6h caps it at a quarter of that for 4 ssh+fetch round
  # trips a day, a cost too small to be worth trading against. Anything tighter
  # buys little: a human ships several times a day anyway, and the toast would
  # start reading as noise. OnStartupSec gives one prompt run 10min after the
  # user manager starts — i.e. after a WORKBENCH reboot. (An earlier version of
  # this comment justified it with the laptop having "just resumed"; that cannot
  # happen. The timer is serverMode-gated and the ~/.server-mode marker exists
  # only on the workbench, so this timer never runs on the laptop at all — the
  # laptop is only ever the far end of the ssh leg.)
  # No Persistent — it only applies to OnCalendar timers, not monotonic ones.
  systemd.user.timers.drift-check = {
    Unit = {
      Description = "Periodic timer for the passive devrc drift deadman";
    };
    Timer = {
      OnStartupSec = "10min";
      OnUnitActiveSec = "6h";
    };
    Install = {
      # 🔴 The master switch acts HERE and only here: with enableDriftDeadman
      # false the unit file still exists (so it can be started/inspected by hand)
      # but is wired into nothing, so no routine `ship.sh` can silently start a
      # timer nobody has supervised once.
      WantedBy = lib.optionals (serverMode && enableDriftDeadman) [ "timers.target" ];
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

  # ~/.claude log rotation. NOT serverMode-gated, deliberately: both hosts run
  # Claude Code and both accumulate these files. Measured on the workbench
  # 2026-08-02 — notify.log 39.8 MB (dead writer, last touched 2026-07-06),
  # clawgate-hook.log 12.0 MB (live), 51.6 MB unrotated in total and growing.
  #
  # Only ONE of those writers lives in this repo, which is why the cap is on the
  # DIRECTORY rather than in each writer: `clawgate-hook.log` comes from the
  # clawgate approval hook and `notify.log` from something no longer running.
  # The wrapper uses logrotate's `copytruncate` so an open fd (that hook fires on
  # every tool call) keeps writing to the same file instead of to an unlinked
  # inode. Scope is `*.log` ONLY — the hand-made `.bak` config copies in that
  # directory are never touched. See scripts/claude-log-rotate/rotate.sh.
  systemd.user.services.claude-log-rotate = {
    Unit = {
      Description = "Size-cap the unrotated logs in ~/.claude (logrotate, copytruncate)";
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "oneshot";
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.logrotate pkgs.bash pkgs.coreutils ]}"
        "HOME=%h"
      ];
      ExecStart = "${pkgs.bash}/bin/bash %h/workspace/devrc/scripts/claude-log-rotate/rotate.sh";
      # Re-run the unit when the wrapper changes (cf. X-Restart-Triggers above).
      X-Restart-Triggers = [ "${../scripts/claude-log-rotate/rotate.sh}" ];
    };
  };

  # Daily. Persistent catches up a single missed run (host asleep / powered off),
  # which matters here precisely because the growth is slow and unattended.
  systemd.user.timers.claude-log-rotate = {
    Unit = {
      Description = "Daily timer for the ~/.claude log size cap";
    };
    Timer = {
      OnCalendar = "*-*-* 04:00:00";
      Persistent = true;
      RandomizedDelaySec = 600;
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };

  # Autocommit for the /analyze-service index store.
  #
  # WHAT IT PROTECTS: ~/.claude/analyze-service-index/<scope>/<service>.md — the
  # curated recon nuance the /analyze-service write-back protocol appends to
  # (claude/skills/analyze-service/SKILL.md). Measured 2026-08-06 on the workbench:
  # 20 files, 56,862 bytes, actively written that same day, with NO git history,
  # NO backup and NO host sync (ship.sh copies no files between hosts at all —
  # it converges each host through git + `home-manager switch`). The
  # content is not re-derivable — it records gotchas and incident tie-ins that
  # were true at a moment in time — so one bad agent Write destroyed it silently.
  #
  # 🔴 WHY A TIMER RATHER THAN A LINE IN THE WRITE-BACK PROTOCOL. The store is
  # written by an agent's Write tool mid-recon, so no git operation happens
  # naturally, and "remember to commit afterwards" is precisely the mechanism
  # MEASURED not to stick here — claude/skills/close-the-loop/STATE.md records
  # opt-in prose steps failing and the pivot to autonomous loops. A backup that
  # depends on an agent remembering is not a backup. PRINCIPLES.md: prefer the
  # deterministic fix over the prose one.
  #
  # 🔴 DELIBERATELY **NOT** GATED ON serverMode, unlike mail-actions-archive /
  # initiatives-sync / ch-regrowth-check above. Those are gated because they need
  # the homelab kubeconfig, the LAN API or a server role. This needs nothing but a
  # local disk and git. It follows claude-log-rotate instead — the other unit that
  # maintains ~/.claude and runs everywhere. Gating it would be actively harmful:
  # /analyze-service runs on whichever host Zach is working from, the stores are
  # per-host and NOT synced, so a laptop-gated-out store would sit unversioned
  # forever while the workbench looked healthy — the exact silent gap this closes.
  #
  # NO NETWORK, BY CONSTRUCTION. The scopes hold client-identifying infrastructure
  # detail, so the script never adds a remote, never pushes and never fetches, and
  # there is deliberately no network dependency on the unit to hint otherwise.
  # See devrc 60e6d9d for why that matters.
  #
  # Failure is LOUD: the script exits non-zero on a locked index, an unexpected
  # non-.md file it refuses to sweep in, a scope nested inside a foreign repo, a
  # SYMLINK where a scope directory should be, or a scope it could not fully
  # enumerate (an unreadable subdirectory) — and OnFailure hands that to the
  # existing notify-failure@ toast. It is a silent no-op only when there is
  # genuinely nothing to commit.
  #
  # Minimal user-unit env, so PATH is explicit: git (the whole job), findutils
  # (scope + candidate enumeration), gnugrep/gnused (message assembly), plus
  # bash/coreutils.
  #
  # 🔴 CONTAINMENT IS THE PRIMARY NO-EXFILTRATION CONTROL — the static ledger in
  # the test file is secondary. Three fix rounds tried to make exfiltration
  # DETECTABLE by reading commit.sh, and each round was evaded a new way (`cp -r`,
  # eight wrapper prefixes, brace groups). This block makes it not land.
  #
  # ⚠ A PATH restriction is NOT that control, and it is worth saying so because
  # it reads like one. `cp` and `tee` live in pkgs.coreutils, which this script
  # genuinely needs (mktemp/sort/rm/realpath/tr) — so no honest PATH here can
  # make `cp` "fail on sight". What makes `cp` useless is having nowhere to
  # write.
  #
  # MEASURED 2026-08-06 under exactly these directives, on this host AND the
  # laptop (systemd-run --user with the same properties, journal-captured):
  #   * the committer runs normally and its commits persist in the real store;
  #   * `cp -r <scope> <dir>` fails "Read-only file system", rc=1, nothing lands
  #     (uncontained positive control: rc=0, 27 files copied);
  #   * bash's BUILTIN `/dev/tcp` egress fails rc=1 where it returns rc=0
  #     uncontained — a hole no PATH restriction can reach, since it needs no
  #     binary at all.
  #
  # 🔴 THAT SET WAS NOT COMPLETE, AND THE GAP WAS A LIVE ONE. Re-measured
  # 2026-08-07: `/dev/shm` was writable and its contents SURVIVED on the host.
  # The lesson generalises past the one path — "ProtectSystem=strict plus
  # ProtectHome=tmpfs means the store is the only writable path" is a claim
  # about two directives, not about the namespace, and the only way to find the
  # difference is to run it. The nix build sandbox has no systemd, so NO TEST IN
  # THIS REPO CAN CHECK ANY OF THIS: the suite can assert a directive is
  # present, never that it works. Adding a directive here without a
  # `systemd-run --user` measurement is how a declared-but-ineffective
  # hardening line ships, which is worse than none.
  #
  # Each directive earns its place:
  #   ProtectSystem=strict  everything read-only except what is bound back in
  #   ProtectHome=tmpfs     $HOME disappears; the store and the script are the
  #                         only parts of it that exist inside the namespace
  #   BindPaths=-<store>    the ONE writable path. `-` so a host that has never
  #                         run /analyze-service still starts and no-ops cleanly
  #                         (the script's "no store — nothing to do" path)
  #   InaccessiblePaths     closes /dev/shm, which none of the above covered
  #   PrivateTmp            mktemp scratch dies with the unit; also covers /var/tmp
  #   PrivateNetwork        no route off-box, for any binary or builtin
  #   NoNewPrivileges       no setuid escape from the above
  #
  # 🔴 BOTH BIND LISTS ARE FROZEN AT EXACTLY ONE ENTRY, and the test file
  # asserts their full contents rather than a prefix. Appending a second entry
  # is a one-token hole: `BindPaths = [ "-%h/.claude/analyze-service-index"
  # "-%h" ];` passed all 75 tests of the previous round and gives the unit a
  # writable real $HOME — `~/.ssh`, the devrc checkout, everything (MEASURED
  # 2026-08-07: write rc=0, content present on the host afterwards). This is
  # also why symlinked scopes are refused rather than supported: supporting them
  # means widening BindPaths to cover symlink targets, i.e. this exact hole.
  systemd.user.services.analyze-service-index-commit = {
    Unit = {
      Description = "Commit any dirty state in the /analyze-service index store";
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "oneshot";
      # Purely local git over ~20 small files. A hang means something is very
      # wrong (a stale lock, a wedged filesystem) and must not pin the timer.
      TimeoutStartSec = 120;
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.git pkgs.bash pkgs.coreutils pkgs.findutils pkgs.gnugrep pkgs.gnused ]}"
        "HOME=%h"
      ];
      ProtectSystem = "strict";
      ProtectHome = "tmpfs";
      # 🔴 NO LEADING `-` HERE, UNLIKE BindPaths. With `-` a missing source is
      # silently skipped and the unit fails as `bash: …/commit.sh: No such file
      # or directory`, status=127 — loud, but naming the wrong thing entirely.
      # MEASURED 2026-08-07, source removed, same unit otherwise:
      #   with `-`     → status=127, "bash: …/gone/commit.sh: No such file …"
      #   without `-`  → status=226/NAMESPACE, "Failed to set up mount
      #                  namespacing: …/gone: No such file or directory"
      # The second names the missing mount. `-` is right on the STORE (a host
      # that has never run /analyze-service must no-op cleanly, not fail) and
      # wrong here: this script ships with the repo, so its absence is a real
      # deployment fault and must say so.
      BindReadOnlyPaths = [ "%h/workspace/devrc/scripts/analyze-service-index" ];
      BindPaths = [ "-%h/.claude/analyze-service-index" ];
      # 🔴 /dev/shm IS WRITABLE AND PERSISTS ON THE HOST UNDER EVERYTHING ABOVE.
      # ProtectSystem=strict and ProtectHome=tmpfs do not cover it, and it is a
      # 1777 host-shared tmpfs, so it was a fully working exfiltration target.
      # MEASURED 2026-08-07 under the exact directive set above:
      #   `cp -r <store> /dev/shm/asi-leak` → rc=0, and after the unit exited
      #   the HOST still showed every file, mode 644, under a 1777 directory.
      # A mutant doing exactly that survived all 75 tests in the suite.
      # With this line: rc=1 "Permission denied", /dev/shm is `d---------`
      # inside the namespace, `touch` rc=1, and the committer is unaffected.
      #
      # What does NOT work, stated so nobody swaps it for the "tidier" option:
      #   PrivateDevices=true  → cp rc=0, content persists on the host (MEASURED)
      #   PrivateIPC=true      → cp rc=0, content persists on the host (MEASURED)
      #   TemporaryFileSystem=/dev/shm → private, but still WRITABLE
      # /var/tmp needs nothing: PrivateTmp already covers it (MEASURED: cp rc=0
      # inside, nothing on the host afterwards).
      InaccessiblePaths = [ "/dev/shm" "/dev/mqueue" ];
      PrivateTmp = true;
      PrivateNetwork = true;
      NoNewPrivileges = true;
      ExecStart = "${pkgs.bash}/bin/bash %h/workspace/devrc/scripts/analyze-service-index/commit.sh";
      # Re-run the unit when the committer changes (cf. X-Restart-Triggers above).
      X-Restart-Triggers = [ "${../scripts/analyze-service-index/commit.sh}" ];
    };
  };

  # HOURLY. Persistent catches up a missed run (host asleep / powered off).
  #
  # What the timer actually buys, stated at the scope it holds: a file that has
  # been committed at least once survives a later bad Write, because the previous
  # run's commit still has it. What it does NOT cover — content CREATED and
  # DESTROYED between two runs never reaches any commit and is unrecoverable.
  # That is not a corner case; it is the same-day write-then-clobber this work
  # exists to defend against.
  #
  # 🔴 THAT WINDOW IS NARROWED, NOT CLOSED, and the distinction is the whole
  # point. Only committing at write time would close it, and the store is written
  # by agents that will not do so (see commit.sh's header). But "cannot be
  # closed" was being used as an argument for leaving it at 24 h, and it is not
  # one: hourly cuts the unrecoverable window 24× for a cost measured at nothing.
  # MEASURED 2026-08-07, contained, at two points so the claim carries its own
  # scope:
  #   1 scope,   2 files            → ~80 ms per clean run
  #   21 scopes, 84 files, 204 KB   → ~200-220 ms per clean run
  #     (the live store's shape: 21 entries. First-ever run, bootstrapping 21
  #      repositories from nothing: 663 ms — once, then never again.)
  # 24 runs a day is therefore ~5 s of CPU. The unit touches no network
  # (PrivateNetwork=true), no remote and no other host, so hourly costs a
  # rounding error and buys 23 hours of exposure back.
  #
  # RandomizedDelaySec stays at 600 — well inside the hour, so runs cannot pile
  # up, and it keeps the unit off a predictable boundary.
  systemd.user.timers.analyze-service-index-commit = {
    Unit = {
      Description = "Hourly timer for the /analyze-service index autocommit";
    };
    Timer = {
      OnCalendar = "hourly";
      Persistent = true;
      RandomizedDelaySec = 600;
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };

  # ClickHouse regrowth check for the activity store.
  #
  # 2026-08-03 the store was cut 112.4 GB -> 82.1 MB after `system.trace_log`
  # accumulated 3.77 BILLION rows in ~5 weeks. The fix was a Flux config change
  # (logger trace -> warning, four query-profiler log tables removed, 7d/14d
  # TTLs, merge pool 32 -> 8). 🔴 NOBODY WILL NOTICE IF THAT SILENTLY STOPS
  # WORKING — a revert or a TTL that quietly stops binding reproduces the same
  # outcome, and the only symptom is a disk filling up months later. Hence a
  # timer. See scripts/collector/ch_regrowth.py for what is asserted, the
  # measured baseline behind every threshold, and why every error path is LOUD
  # rather than a clean-looking zero.
  #
  # WORKBENCH-ONLY (gated on serverMode), same discriminator and same rationale
  # as mail-actions-archive / initiatives-sync above: the committed homelab
  # kubeconfig points at the LAN API (192.168.50.94:6443) and the `kubectl exec
  # ... du` reading needs it. The laptop is nebula-only AND has an open,
  # unresolved nebula fault that makes these ClickHouse queries intermittently
  # stall to timeout (claudedocs/handoff-agent-setup-audit.md, investigation 1)
  # — a check that flakes is a check that gets ignored, which is the exact
  # failure mode this is built to avoid. A laptop run is still possible BY HAND:
  # KUBECONFIG=~/.kube/homelab-nebula.yaml CH_REGROWTH_URL=http://10.42.0.10:30123
  # scripts/collector/run-regrowth-check.sh
  #
  # NOTIFICATION: no new mechanism. The wrapper exits non-zero for ALARM (1),
  # CANNOT-TELL (2) and WARN (3), so any of the three is a unit failure and the
  # existing OnFailure=notify-failure@%n.service turns it into a sticky critical
  # toast (scripts/notify-failure.sh). The verdict — including every number it
  # read — is also written to ~/.cache/ch-regrowth/status.json for after the
  # fact. `SuccessExitStatus` is deliberately NOT set: exit 2 ("cannot tell")
  # must toast too, because a check that could not measure is not a clean store.
  #
  # Minimal user-unit env, so PATH is explicit: python312 (the checker), kubectl
  # (the du exec), sops (the run-time admin-password decrypt), bash/coreutils.
  systemd.user.services.ch-regrowth-check = lib.mkIf serverMode {
    Unit = {
      Description = "ClickHouse regrowth check for the activity store (read-only)";
      After = [ "network-online.target" ];
      Wants = [ "network-online.target" ];
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "oneshot";
      # Hard ceiling: three SELECTs and one `kubectl exec du`. A half-hung
      # kubectl must not wedge the timer — the cgroup is killed and the next
      # OnCalendar re-arms. Measured end-to-end against the live store
      # 2026-08-06: ~10s over nebula (the slower path).
      #
      # 180 -> 240 when the checker gained retries: a transient HTTP 500 (the
      # observed `Code: 241 MEMORY_LIMIT_EXCEEDED`) is now retried with backoff
      # and the TTL union falls back to one query per table (which retries too).
      # ch_regrowth.py bounds that itself with COLLECT_BUDGET_SECONDS = 120 — no
      # RETRY starts past 120s, and the per-table fallback loop breaks there.
      #
      # 🔴 WORST CASE IS 187s, NOT ~180. This comment used to say "120 + one
      # in-flight 30s query + the du exec", which understates it by a whole
      # query phase: only RETRIES are deadline-gated, so after ONE phase burns
      # its full budget (3 x 30s + 2s + 5s = 97s) each of the three remaining
      # phases still gets one ungated 30s attempt — 97 + 90 = 187. The
      # conclusion holds (187 < 240), but do not re-derive the margin from the
      # old sentence. This number is not asserted from a comment:
      # test_the_collection_budget_is_pinned_to_the_units_timeout SIMULATES the
      # adversary that maximises wall clock and reads THIS `TimeoutStartSec`
      # out of this file, so the two cannot drift apart.
      TimeoutStartSec = 240;
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.python312 pkgs.kubectl pkgs.sops pkgs.bash pkgs.coreutils ]}"
        "KUBECONFIG=%h/workspace/homelab-talos/homelab-kubeconfig"
        "HOME=%h"
      ];
      ExecStart = "${pkgs.bash}/bin/bash %h/workspace/devrc/scripts/collector/run-regrowth-check.sh";
      # Re-run the unit when either half of the check changes.
      X-Restart-Triggers = [
        "${../scripts/collector/run-regrowth-check.sh}"
        "${../scripts/collector/ch_regrowth.py}"
      ];
    };
  };

  # Monthly on the 11th. The date is not arbitrary: the 7-day TTLs first have
  # anything to act on around 2026-08-10 (no TTL had fired even ONCE when this
  # was written — every row was younger than its own TTL), so the first firing
  # on 2026-08-11 is the first run that can observe the bounding mechanism
  # actually working, and monthly is the steady state after that.
  # Persistent=true so a run missed with the host off fires on next boot —
  # which matters precisely because the growth is slow and unattended.
  systemd.user.timers.ch-regrowth-check = lib.mkIf serverMode {
    Unit = {
      Description = "Monthly timer for the ClickHouse regrowth check";
    };
    Timer = {
      OnCalendar = "*-*-11 09:00:00";
      Persistent = true;
      RandomizedDelaySec = 900;
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };

  # Post-reboot claude session restore — fires ~45s after login so
  # tmux-continuum has time to restore the session layout first, then this
  # service resumes claude conversations in each window.  Idempotent: skips
  # windows already running claude.
  systemd.user.services.tmux-session-restore = {
    Unit = {
      Description = "Resume claude conversations after tmux-continuum restores sessions";
      After = [ "graphical-session.target" ];
      Wants = [ "graphical-session.target" ];
      OnFailure = [ "notify-failure@%n.service" ];
    };
    Service = {
      Type = "oneshot";
      # The timer's OnActiveSec=45s already delays startup; no ExecStartPre needed.
      Environment = [
        "PATH=${lib.makeBinPath [ pkgs.python312 pkgs.tmux pkgs.coreutils ]}"
        "HOME=%h"
      ];
      ExecStart = "${pkgs.python312}/bin/python3 %h/workspace/devrc/scripts/tmux-session-restore.py restore --staleness-check 2";
      # Re-run the unit when the script changes.
      X-Restart-Triggers = [ "${../scripts/tmux-session-restore.py}" ];
    };
  };

  systemd.user.timers.tmux-session-restore = {
    Unit = {
      Description = "One-shot timer — run tmux-session-restore once after login";
    };
    Timer = {
      OnActiveSec = "45s";
      Unit = "tmux-session-restore.service";
    };
    Install = {
      WantedBy = [ "timers.target" ];
    };
  };
}
