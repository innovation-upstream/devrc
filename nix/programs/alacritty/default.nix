{ pkgs, config, lib ? pkgs.lib, ... }:
let
  # The Alacritty hint handler for clawgate / GitHub / ClickUp mentions.
  #
  # A wrapper rather than pointing `command` straight at the script, for one
  # reason: Alacritty spawns a hint command with ITS OWN environment, which came
  # from the display manager, and the script needs git / tmux / xdg-open /
  # notify-send. Pinning those to store paths makes the click work regardless of
  # what the session PATH happens to hold.
  #
  # `$PATH` is APPENDED, not replaced: `rofi` is a SYSTEM package here (it is
  # not in nix/pkgs — nix/i3/config.nix invokes it bare), so pulling
  # `pkgs.rofi` in would install a second, independently-versioned copy whose
  # theme could drift from the launcher's. The picker must look like every other
  # picker on this desktop.
  mentionOpen = pkgs.writeShellScript "alacritty-mention-open" ''
    export PATH=${lib.makeBinPath [
      # `gh` is PASS 3's only tool, and its absence is SILENT: FileNotFoundError
      # is caught as OSError and the search returns {}, so the fallback would be
      # inert in production with a fully green suite. `~/.nix-profile` is also
      # blanked for ~1s during every home-manager switch, so relying on the
      # inherited PATH is not enough.
      pkgs.python312 pkgs.git pkgs.tmux pkgs.xdg-utils pkgs.libnotify pkgs.gh
    ]}:$PATH
    exec ${pkgs.python312}/bin/python3 \
      ${config.home.homeDirectory}/workspace/devrc/scripts/mention-open.py "$@"
  '';
in
{
  enable = true;

  settings = {
    # Bell sound handled by tmux hook (set-hook alert-bell) to avoid double notification
    bell = {
      duration = 0;
    };

    # Honor OSC 52 *copy* requests so tmux (incl. over SSH from the workbench)
    # can set this laptop's system clipboard. "OnlyCopy" is alacritty's default
    # but pinned here so the clipboard path can't silently break on a default
    # change; paste-via-OSC52 stays disabled (it's a security footgun).
    terminal = {
      osc52 = "OnlyCopy";
    };

    # Selection → clipboard on mouse release, so highlighting text alone copies
    # it (no Ctrl+Shift+C needed). Tradeoff: every selection, even an accidental
    # drag, replaces the system clipboard.
    #
    # This only matters while ALACRITTY owns the selection. It does today because
    # tmux's mouse mode is off — but note WHY: `mouse` is off by tmux's OWN
    # DEFAULT, and this repo does not set it anywhere (`git grep mouse -- '*tmux*'`
    # finds nothing in `.tmux.conf` or `nix/programs/tmux/`). So the condition is
    # inherited, not pinned. Turning tmux mouse mode ON hands the selection to
    # tmux and this setting stops applying inside a tmux pane — which is most of
    # this terminal's use. Measured 2026-09-05: `tmux show -gv mouse` → `off`.
    selection = {
      save_to_clipboard = true;
    };

    # ----------------------------------------------------------------------- #
    # HINTS — clickable text in the terminal grid
    # ----------------------------------------------------------------------- #
    # 🔴 `hints.enabled` IS AN ARRAY, AND DECLARING IT REPLACES ALACRITTY'S
    # BUILT-IN DEFAULT ENTIRELY. There is no merge. Adding the mention hint
    # WITHOUT re-declaring the URL hint below would silently delete URL clicking
    # — the single most-used interaction in this terminal — with no error, no
    # warning, and nothing in the config that looks wrong.
    #
    # `scripts/tests/test_alacritty_hints.py` exists for exactly that: it pins
    # the URL hint's presence AND its regex, so a future edit that drops or
    # mangles it fails the suite instead of being discovered by a click that
    # does nothing.
    #
    # ORDER: the URL hint is declared FIRST, so that a mention-shaped substring
    # inside a URL (`github.com/owner/repo#1`) is claimed by the hint that
    # already opens exactly the right page.
    # ⚠ That alacritty resolves an overlap in DECLARATION order is a reasonable
    # reading of its hint lookup and was NOT verified — checking it needs a live
    # terminal. So this ordering is DEFENSIVE, not load-bearing: if the other
    # hint won, `mention-open.py` resolves `owner/repo#N` to the same GitHub
    # issue URL anyway.
    hints.enabled = [
      # ------------------------------------------------------------------- #
      # 1. THE BUILT-IN URL HINT, RE-DECLARED
      # ------------------------------------------------------------------- #
      # Every field below is alacritty 0.17.0's own default, verbatim from the
      # compiled binary (the `man 5 alacritty` rendering is roff-escaped and
      # line-wrapped — do not copy it from there).
      #
      # ⚠ ONE DELIBERATE, DOCUMENTED SUBSTITUTION IN THE REGEX. Alacritty's
      # built-in default carries LITERAL C0/C1 control characters in the
      # excluded set — the compiled string is
      #   …[^<NUL>-<US><DEL>-<U+009F><>"\s{-}\^⟨⟩`\\]+
      # because Rust source can write `\u{0000}` and get the character itself.
      # A Nix string cannot hold a NUL, and routing one through the TOML writer
      # would be its own hazard, so the identical set is spelled with the regex
      # engine's OWN escapes: `\x00-\x1F` and `\x7F-\x9F`. Same matched
      # language, expressible in this file. Everything else — the scheme
      # alternation, `<>`, `"`, `\s`, `{-}` (a `{`..`}` range, so it also
      # excludes `|`), `⟨⟩`, the backtick and the escaped backslash — is
      # character-for-character the default.
      {
        regex = "(ipfs:|ipns:|magnet:|mailto:|gemini://|gopher://|https://|http://|news:|file:|git://|ssh:|ftp://)[^\\x00-\\x1F\\x7F-\\x9F<>\"\\s{-}\\^⟨⟩`\\\\]+";
        command = "xdg-open";
        hyperlinks = true;      # also match OSC-8 escape hyperlinks
        post_processing = true; # trim trailing punctuation / unbalanced brackets
        persist = false;
        mouse = { enabled = true; };  # no mods: plain hover underlines, plain click opens
        binding = { key = "O"; mods = "Control|Shift"; };
      }

      # ------------------------------------------------------------------- #
      # 2. MENTIONS — clawgate tasks, GitHub issues/PRs, ClickUp task ids
      # ------------------------------------------------------------------- #
      # Matches `#370`, `devrc#591`, `civitai/talos-infra#1065`, `868abc123`.
      #
      # 🔴 THIS REGEX IS DELIBERATELY LOOSER THAN THE SCANNER, AND THAT IS THE
      # DESIGN, NOT A BUG. Rust's regex crate has NO lookaround, so the
      # trailing-digit guard that lets `scripts/collector/mention_scan.py`
      # reject a six-digit hex colour cannot be written here. Without it, a
      # `{1,5}` bound would match the first FIVE digits of `#282828` and
      # cheerfully offer to open task 28282. So the bound is `{1,6}`: the whole
      # colour literal is swallowed into one match, handed to the handler, and
      # REJECTED there by the strict scanner. The handler is the authority; this
      # regex only decides what is underlined.
      #
      # Residual cosmetic cost, stated rather than hidden: a six-digit hex
      # colour still gets underlined on hover. Clicking it opens nothing and
      # says so.
      #
      # `post_processing = false` — that pass exists to repair URL
      # over-capture (stripping a trailing `)` or `.` that a greedy URL regex
      # swallowed). This pattern already ends exactly at the reference, so the
      # pass can only ever REMOVE characters that were matched on purpose.
      #
      # `hyperlinks = false` — an OSC-8 escape hyperlink is a URL; matching
      # mentions against them would route a URL into the mention handler.
      {
        regex = "(?:[A-Za-z0-9][A-Za-z0-9-]*/)?(?:[A-Za-z0-9][A-Za-z0-9_-]*)?#[0-9]{1,6}|868[a-z0-9]{6}";
        command = "${mentionOpen}";
        hyperlinks = false;
        post_processing = false;
        persist = false;
        # Identical mouse semantics to the URL hint above — Zach's requirement
        # is that a mention behaves "the same way link clicking already does".
        mouse = { enabled = true; };
        # Ctrl+Shift+M for keyboard hint mode. NOT Ctrl+Shift+O (the URL hint's,
        # re-declared above) and NOT any of the keyboard.bindings below, which
        # are all Control / Control|Shift on Back / Left / Right.
        binding = { key = "M"; mods = "Control|Shift"; };
      }
    ];

    # Gruvbox Dark theme
    colors = {
      primary = {
        background = "#282828";
        foreground = "#ebdbb2";
      };
      normal = {
        black = "#282828";
        red = "#cc241d";
        green = "#98971a";
        yellow = "#d79921";
        blue = "#458588";
        magenta = "#b16286";
        cyan = "#689d6a";
        white = "#a89984";
      };
      bright = {
        black = "#928374";
        red = "#fb4934";
        green = "#b8bb26";
        yellow = "#fabd2f";
        blue = "#83a598";
        magenta = "#d3869b";
        cyan = "#8ec07c";
        white = "#ebdbb2";
      };
    };

    keyboard.bindings = [
      { key = "Back"; mods = "Control"; chars = "\\u0017"; }       # Delete word
      { key = "Back"; mods = "Control|Shift"; chars = "\\u0015"; } # Delete to line start
      { key = "Left"; mods = "Control"; chars = "\\u001bb"; }      # Word back
      { key = "Right"; mods = "Control"; chars = "\\u001bf"; }     # Word forward
    ];
  };
}
