# `slotTablePath` is overridable so the tests can run THIS module — the real
# artifact, not a re-spelling of it — over a synthetic slot table and read the
# bindings it actually generates. Production never passes it.
{ pkgs, slotTablePath ? ../../../scripts/tmux-scratch-slots.sh, ... }:
let
  # Generate the scratchpad popup toggles from the canonical slot table
  # (scripts/tmux-scratch-slots.sh) instead of hardcoding them — with their
  # per-slot color + codename — in .tmux.conf. One source of truth: the same file
  # the tmux HUDs source and initiative-scan.py parses. Add/rename a scratchpad by
  # editing the slot table only.
  #
  # 🔴 THIS FILE CANNOT CLASSIFY A LINE ITSELF, AND THAT IS THE POINT. It holds
  # no `builtins.match` and no `builtins.split`: every verdict about what a slot
  # IS comes from ./slot-table.nix, which reads the grammar out of the table's
  # own `# SLOT_ENTRY_RE:` marker line — the single copy that
  # scripts/i3status-scratchpads (the bar's colour legend) reads too. This file
  # and that one used to carry a regex EACH and they disagreed in both
  # directions: `#[0-9a-fA-F]+` here accepted a 9- or 12-digit hex colour (both
  # valid pango) that the legend's `{3,8}` dropped, so a slot got a key binding
  # and no legend entry; and the legend matched anywhere in the text while this
  # one is whole-line, so a COMMENTED-OUT slot line got a legend entry
  # advertising a hotkey bound to nothing. Slot entries look like
  # "scratch4:V:#83a598:Vapor" (session:key:color:name).
  #
  # scripts/tests/test_scratchpads_block.py evaluates this module and compares
  # the bindings it GENERATES against the legend's own parse of the same table,
  # on the real table and on synthetic ones — a value, not a spelling.
  slotTable = import ./slot-table.nix { path = slotTablePath; };
  # Byte-identical (per tmux's normalization) to the former hand-written bindings —
  # verified via a `tmux list-keys` diff before the cutover.
  mkBind = s: "bind -n M-${s.key} if-shell -F '#{==:#{session_name},${s.sess}}'"
    + " { detach-client }"
    + " { display-popup -d \"#{pane_current_path}\" -xC -yC -w 90% -h 90%"
    + " -S 'fg=${s.color}' -T ' ${s.name} '"
    + " -E 'tmux attach-session -t ${s.sess} || tmux new-session -s ${s.sess}' }";
  # 🔴 A PARTIALLY-PARSEABLE TABLE MUST FAIL THE BUILD, NOT SHIP FEWER KEYS.
  # The bar legend's counterpart of this renders `scratch ?`; here the only
  # honest "unmeasured" is a build error, because the alternative is a switch
  # that silently succeeds having generated 3 bindings out of 20 and an operator
  # whose Alt-keys quietly stop working.
  #
  # `candidateLines` is deliberately WIDER than the grammar (see slot-table.nix)
  # — a yardstick that narrows with the grammar cannot see a dropped entry.
  scratchBindings =
    if builtins.length slotTable.slotLines < builtins.length slotTable.candidateLines
    then throw ("${toString slotTablePath} declares "
      + toString (builtins.length slotTable.candidateLines)
      + " slot entries but only "
      + toString (builtins.length slotTable.slotLines)
      + " match the `# SLOT_ENTRY_RE:` grammar — refusing to generate a partial "
      + "set of scratchpad bindings. A slot entry is a quoted "
      + "`session:key:#colour:name` string alone on its line, indented with "
      + "spaces or tabs only; the colour must be 3, 6, 9 or 12 hex digits, and "
      + "a trailing comment after the closing quote is NOT accepted. Fix the "
      + "malformed entries or delete them outright.")
    else builtins.concatStringsSep "\n" (map mkBind slotTable.slots);

  # 🔴 CONTINUUM'S 15-MINUTE AUTOSAVE IS A STATUS-LINE INTERPOLATION, AND
  # NOTHING ELSE DRIVES IT.  `continuum.tmux:main()` calls
  # `add_resurrect_save_interpolation`, which PREPENDS
  # `#(<plugin>/scripts/continuum_save.sh)` to `status-right`; every status
  # refresh then runs that script, which checks whether the save interval has
  # elapsed and, if so, invokes resurrect's save.  There is no timer, no hook
  # and no daemon — that interpolation IS the timer.
  #
  # WHY WE SET IT OURSELVES INSTEAD OF LETTING THE PLUGIN DO IT.  home-manager
  # emits `run-shell <plugin>.tmux` for every plugin FIRST and `extraConfig`
  # AFTER, and `extraConfig` begins with the whole of the repo-root
  # `.tmux.conf` — which contains a plain `set -g status-right '…'`.  A plain
  # `set` REPLACES the option, so continuum's prepend is silently discarded a
  # few lines later.  MEASURED on the workbench 2026-09-04, on a server up
  # since 2026-08-05: `status-right` held zero occurrences of `continuum_save`
  # (and so did `status-left`), and `@continuum-save-last-timestamp` was
  # 1785949443 — exactly `#{start_time}` + 1s, i.e. the value
  # `delay_saving_environment_on_first_plugin_load` writes at plugin load and
  # NOT one continuum_save.sh ever wrote.  That timestamp is the discriminating
  # evidence: it is set inside the same `if ! another_tmux_server_running`
  # block as the interpolation, so its presence proves the block RAN and
  # refutes the rival "continuum short-circuited" explanation.  The
  # interpolation was added and then clobbered.  Reproduced on a throwaway
  # `-L` socket: `run-shell` that prepends, followed by a plain `set -g
  # status-right`, yields zero occurrences.
  #
  # `set -ag` (append) is what makes this survive a reload rather than
  # accumulate: each `source-file` re-runs the plain `set -g status-right`
  # first, resetting the value, then this single append.  Verified on a
  # throwaway socket — one occurrence after the initial load and still exactly
  # one after two further reloads.  `continuum_save.sh` prints nothing, so the
  # expanded status line is byte-identical with and without it (also measured);
  # `status-right-length 70` is unaffected.
  #
  # 🔴 REFERENCED THROUGH THE PACKAGE, NEVER A LITERAL /nix/store PATH.  This
  # makes the plugin a real dependency of the generation, so it is GC-rooted
  # for as long as that generation lives.  A hardcoded hash is how the sibling
  # outage in this area happened — the live server still pointed at a resurrect
  # store path that had been garbage-collected.
  #
  # Pinned by scripts/tests/test_tmux_continuum_save_interpolation.py, which
  # simulates tmux's own set/append semantics over the generated config and
  # asserts the interpolation survives to the FINAL value.
  continuumSave =
    "${pkgs.tmuxPlugins.continuum}/share/tmux-plugins/continuum/scripts/continuum_save.sh";
  continuumSaveInterpolation = ''

    # --- continuum autosave driver (see nix/programs/tmux/default.nix) ---
    # MUST stay after the last plain `set -g status-right` or it is clobbered.
    set -ag status-right '#(${continuumSave})'
  '';
in
{
  enable = true;
  prefix = "C-a";
  keyMode = "vi";
  baseIndex = 1;
  terminal = "tmux-256color";
  escapeTime = 0;
  historyLimit = 50000;
  focusEvents = true;
  extraConfig = builtins.readFile ../../../.tmux.conf
    + "\n# --- generated scratchpad popup toggles (see nix/programs/tmux/default.nix) ---\n"
    + scratchBindings + "\n"
    + continuumSaveInterpolation;
  plugins = with pkgs.tmuxPlugins; [
    {
      plugin = resurrect;
      extraConfig = ''
        set -g @resurrect-capture-pane-contents 'on'
        set -g @resurrect-strategy-nvim 'session'
        # Auto-save claude-session-to-window mappings every continuum save cycle
        # (15 min).  The hook script runs in the background so continuum's own
        # save is never blocked.  This keeps restore-plan.json fresh so a crash
        # recovery always has a recent plan.
        #
        # 🔴 THE KIND IS `post-save-all`, NOT `post-save`.  resurrect only ever
        # invokes five kinds (post-save-layout, post-save-all, pre-restore-all,
        # pre-restore-pane-processes, post-restore-all); `execute_hook` looks up
        # `@resurrect-hook-<kind>` and an option nobody invokes reads back as ""
        # and is silently a no-op.  This line said `post-save` from the day it
        # landed, so the callback NEVER ran: the log it writes on every run had
        # never been created and tmux-session-restore.service failed on every
        # boot with a plan >1400h stale.  `post-save-all` fires at the end of
        # save.sh with no args, which is what this callback wants;
        # `post-save-layout` fires earlier and is passed the state file path.
        # Pinned by scripts/tests/test_tmux_resurrect_hook_names.py.
        set -g @resurrect-hook-post-save-all '~/.config/tmux/tmux-post-save.sh'
      '';
    }
    {
      plugin = continuum;
      extraConfig = ''
        set -g @continuum-restore 'on'
        set -g @continuum-save-interval '15'
      '';
    }
    {
      plugin = tmux-fzf;
      extraConfig = ''
        # Rebind from F (default) to f
        unbind-key F
        bind-key f run-shell -b "${tmux-fzf}/share/tmux-plugins/tmux-fzf/main.sh"
      '';
    }
  ];
}
