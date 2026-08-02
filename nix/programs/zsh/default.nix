{ config, ... }:
let
  # 🔴 SINGLE SOURCE OF TRUTH — shared with the opencode `shell.env` plugin
  # generated in nix/home.nix. Defining these handles in two places is what let
  # scripts/opencode/env.js drift (hardcoded /home/zach, no existence guard, and
  # a KC_PROD that zsh never had). Add a handle THERE, not here.
  handles = import ../../agent-handles.nix { home = config.home.homeDirectory; };
  exportIf = flag: name: path:
    "[[ ${flag} ${path} ]] && export ${name}=${path}";
  handleLines =
    (builtins.attrValues (builtins.mapAttrs (exportIf "-d") handles.repos))
    ++ (builtins.attrValues (builtins.mapAttrs (exportIf "-f") handles.kubeconfigs));
in
{
  enable = true;
  autocd = true;
  dotDir = config.home.homeDirectory + "/.config/zsh";
  enableCompletion = true;

  # MUST live in .zshenv (via envExtra), not just .zshrc/initContent: NON-interactive
  # shells (Claude Code's Bash tool runs `zsh -c`) source .zshenv only. Without this,
  # every unmatched glob (`grep --include=*.go`, `ls /tmp/foo* 2>/dev/null`) aborts the
  # command with "no matches found" — the single largest source of session command errors.
  envExtra = ''
    unsetopt nomatch

    # ── Canonical repo roots + kubeconfigs for agent shells ─────────────────
    # Claude Code's Bash tool runs NON-interactive `zsh -c`, which sources ONLY
    # .zshenv and does NOT persist shell state between calls. So agents were
    # re-typing `cd <repo> && …` / `export KUBECONFIG=…` on ~50% of Bash calls
    # (measured: ~3.8k plumbing turns/week). Exporting stable handles ONCE here
    # lets a call be `git -C $DATAPACKET …` / `KUBECONFIG=$KC_DPPROD kubectl …`
    # with no per-call setup. Existence-guarded so the laptop (no civit
    # checkouts) and any missing file stay clean — no stale vars.
    # NO default KUBECONFIG on purpose: a merged/default one lets a bare
    # `kubectl` hit prod. Pick a cluster explicitly per command.
    #
    # 🔴 GENERATED from nix/agent-handles.nix — the SAME file that generates
    # opencode's plugin/env.js. Do not add a handle by hand here; it would exist
    # for Claude Code and not for opencode, which is exactly the drift this
    # replaced. `KC_PROD` arrived this way (env.js had it, zsh did not).
${builtins.concatStringsSep "\n" (map (l: "    " + l) handleLines)}

    # ── Global test concurrency cap ─────────────────────────────────────────
    # Vitest sizes its worker pool off the FULL CPU count with no awareness of
    # sibling processes, so N concurrent agents each claim all 24 threads —
    # measured as two vitest runs at 91% + 71% CPU against PSI cpu some=63%
    # (contention, since PSI full stayed at 0%). Capping here (.zshenv, so it
    # reaches the non-interactive `zsh -c` every agent Bash call uses) bounds
    # the total. Starting value, not a benchmarked optimum; NEW shells only.
    #
    # All THREE vars on purpose — the name changed by major version and the
    # repos here straddle the rename:
    #   Vitest 4.x  reads VITEST_MAX_WORKERS
    #   Vitest 2/3  read  VITEST_MAX_THREADS (pool=threads)
    #                and  VITEST_MAX_FORKS   (pool=forks — the 3.x DEFAULT)
    # Setting only MAX_WORKERS is a silent no-op on 2.x/3.x, which is what
    # promptver (3.2.4), codemirror-lang-sdprompt (2.1.9) and both
    # civitai-advertising repos (^3.2.3) actually run. Unknown env vars are
    # ignored, so setting all three is safe in both directions.
    export VITEST_MAX_WORKERS=4
    export VITEST_MAX_THREADS=4
    export VITEST_MAX_FORKS=4

    # Deliberately NOT setting GOMAXPROCS. Go 1.25+ derives it from the cgroup
    # CPU limit and updates it dynamically; pinning an env var DISABLES that,
    # making the system less adaptive, not more. It also double-caps builds,
    # since `go build -p` and `go test -parallel` both default to GOMAXPROCS.
    # To bound Go, put the shell in a systemd slice with CPUQuota= — Go picks
    # that up natively, and it bounds non-Go processes in the slice too.
  '';

  initContent = let
    zshRc = builtins.readFile ../../../.zshrc;
    hasDevRc = builtins.pathExists "${config.home.homeDirectory}/.devrc";
    content = if hasDevRc then zshRc + builtins.readFile "${config.home.homeDirectory}/.devrc" else zshRc;
  in
    ''
    ${content}

    # Pass unmatched globs through literally instead of aborting the command
    # (bash default behavior). Without this, zsh fails commands like
    # `grep --include=*.go` or `ls /tmp/foo* 2>/dev/null` with "no matches found".
    unsetopt nomatch

    # ── activity telemetry (interactive only) ───────────────────────────────
    # preexec/precmd capture each HUMAN command + its duration/exit-code and
    # `emit` an event to the local spool (the collector daemon ships it to
    # ClickHouse). These live in initContent / .zshrc — interactive shells only.
    # Claude Code's Bash tool runs NON-interactive `zsh -c` (sources .zshenv
    # only), so the agent's own commands are correctly EXCLUDED from the log.
    # The hot path is pure shell (no python/jq): `emit` does an atomic >> append.
    ACTIVITY_EMIT="${config.home.homeDirectory}/.config/activity-collector/emit"
    if [[ -x "$ACTIVITY_EMIT" ]]; then
      autoload -Uz add-zsh-hook

      _activity_preexec() {
        _ACTIVITY_CMD="$1"
        _ACTIVITY_START=$EPOCHREALTIME    # float seconds; zsh/datetime
        _ACTIVITY_CWD="$PWD"
      }

      _activity_precmd() {
        local ec=$?
        [[ -z "$_ACTIVITY_CMD" ]] && return
        local dur_ms=0
        if [[ -n "$_ACTIVITY_START" ]]; then
          dur_ms=$(( (EPOCHREALTIME - _ACTIVITY_START) * 1000 ))
          dur_ms=''${dur_ms%.*}
          (( dur_ms < 0 )) && dur_ms=0
        fi
        # project = git repo basename (or cwd basename fallback), else "".
        local proj=""
        local root
        root=$(command git -C "$_ACTIVITY_CWD" rev-parse --show-toplevel 2>/dev/null)
        [[ -n "$root" ]] && proj="''${root:t}"
        # session = tmux session:window.pane if in tmux, else shell-PID.
        local sess
        if [[ -n "$TMUX" ]]; then
          sess=$(command tmux display-message -p '#S:#I.#P' 2>/dev/null)
        fi
        [[ -z "$sess" ]] && sess="sh-$$"
        "$ACTIVITY_EMIT" \
          source=zsh kind=command \
          "b64:text=$_ACTIVITY_CMD" "b64:cwd=$_ACTIVITY_CWD" \
          "duration_ms=$dur_ms" "exit_code=$ec" \
          "b64:project=$proj" "b64:session=$sess" \
          "b64:app=''${TERM_PROGRAM:-''${TERM:-}}" &!
        _ACTIVITY_CMD=""
        _ACTIVITY_START=""
      }

      # EPOCHREALTIME needs zsh/datetime.
      zmodload zsh/datetime 2>/dev/null
      add-zsh-hook preexec _activity_preexec
      add-zsh-hook precmd  _activity_precmd
    fi

    PROMPT='%{$fg_bold[white]%}%c%{$reset_color%} $(git_prompt_info)'

    ZSH_THEME_GIT_PROMPT_PREFIX="%{$fg[green]%}"
    ZSH_THEME_GIT_PROMPT_SUFFIX="%{$reset_color%} "
    ZSH_THEME_GIT_PROMPT_DIRTY="%{$fg[blue]%} %{$fg_bold[yellow]%}!"
    ZSH_THEME_GIT_PROMPT_CLEAN="%{$fg[blue]%}"
    '';

  oh-my-zsh = {
    enable = true;

    plugins = [
      "git"
    ];
  };

  shellAliases = {
    g = "git";
    d = "docker";
    n = "nvim";
    k = "kubectl";
    nemo = "GTK_THEME=Adwaita-dark nemo";

    # Toggle headless vs graphical mode (see ~/.server-mode marker in home.nix).
    # graphical-mode re-enables dunst/espanso; headless-mode disables them.
    graphical-mode = "rm -f ~/.server-mode && home-manager switch --flake ~/workspace/devrc --impure";
    headless-mode = "touch ~/.server-mode && home-manager switch --flake ~/workspace/devrc --impure";
  };

  history = {
    append = true;
    save = 15000;
    saveNoDups = true;
  };

}
