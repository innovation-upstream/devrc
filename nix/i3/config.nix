# i3 window-manager config, rendered to ~/.config/i3/config by home-manager
# (xdg.configFile."i3/config".text). Raw string (NOT the HM i3 DSL) so Zach can
# keep hand-maintaining it. Function of { isLaptop } so the one file serves both
# hosts: laptop gets backlight brightness bindings; workbench gets the rig-control
# (yad) float rule. Reconciled superset of the pre-migration drift between the repo
# nix/system/i3config.nix and the live /etc/nixos/i3config.nix.
{ isLaptop ? false }:

let
  # Laptop-only: hardware backlight brightness keys (workbench has no backlight).
  brightnessBindings =
    if isLaptop then ''

      # Brightness (laptop backlight, 5% steps; Shift for 1% fine control)
      bindsym XF86MonBrightnessUp exec --no-startup-id brightnessctl set +5%
      bindsym XF86MonBrightnessDown exec --no-startup-id brightnessctl set 5%-
      bindsym Shift+XF86MonBrightnessUp exec --no-startup-id brightnessctl set +1%
      bindsym Shift+XF86MonBrightnessDown exec --no-startup-id brightnessctl set 1%-''
    else "";

  # Workbench-only: float the rig-control (yad) popup instead of tiling it.
  rigControlFloat =
    if isLaptop then ""
    else ''

      # Float the rig-control (yad) popup as a compact centered window instead of tiling it
      for_window [class="Yad" title="Rig Controls"] floating enable, move position center'';

  # Workbench-only: dual-monitor layout. The 1920x1080 (HDMI-0) sits to the LEFT of
  # the 3440x1440 ultrawide (DP-0, primary), bottom-aligned (both on the same desk, so
  # the shorter panel gets a 360px dead zone at its top). Restored on every i3 start;
  # hotplug mid-session still needs a manual re-run of this xrandr.
  monitorLayout =
    if isLaptop then ""
    else ''

      # Dual-monitor layout: 1080p (HDMI-0) left of the ultrawide (DP-0, primary)
      exec --no-startup-id xrandr --output DP-0 --primary --mode 3440x1440 --rate 143.92 --pos 1920x0 --output HDMI-0 --mode 1920x1080 --pos 0x360

      # Pin workspaces to outputs.  WITHOUT this i3 places a workspace on
      # whichever output it happens to pick, so the same workspace lands on a
      # different physical screen run to run — and HDMI-0 is usually absent, so
      # the mistake only surfaces when the second monitor is plugged in.
      #
      # This is deliberately NOT a layout snapshot.  It restores no windows and
      # keeps no state: i3 re-evaluates these lines on every start, so there is
      # nothing to go stale and nothing to refresh.  Window/pane state lives in
      # tmux (resurrect + continuum + scripts/tmux-session-restore.py) and is
      # already restored there; the i3 layer carries no state worth snapshotting
      # (measured 2026-09-04: 2 workspaces, 6 windows, 5 of them one terminal).
      #
      # `workspace 3 output HDMI-0 DP-0` is a FALLBACK LIST, not a pair: i3 uses
      # the first CONNECTED output, so workspace 3 lands on DP-0 when HDMI-0 is
      # unplugged rather than vanishing.
      #
      # These live inside monitorLayout on purpose — it is already gated on
      # `isLaptop`, and pinning to HDMI-0 on a host that has no HDMI-0 would be
      # wrong.  An output name that matches no output is silently IGNORED by i3,
      # which is why test_i3_workspace_output_pinning.py pins every name here to
      # an output the xrandr line above actually configures.
      workspace 1 output DP-0
      workspace 2 output DP-0
      workspace 3 output HDMI-0 DP-0'';

  # 🔴 PER-HOST, AND THE TWO VALUES MUST NOT BE COLLAPSED BACK INTO ONE.
  #
  # The `<w> ppt <h> ppt` operands of the review TUI's `resize set` (the rule is
  # near the bottom of this file; only the numbers live up here, so the rule's
  # STRUCTURE cannot diverge between hosts by accident).
  #
  # 🔴 `ppt` IS A PERCENTAGE OF THE **OUTPUT** RECT, NOT OF THE WORKSPACE. Every
  # number here used to be derived against the workspace, and that was wrong by
  # the height of the status bar. ESTABLISHED FROM i3 4.25.1 SOURCE (the version
  # running on both hosts), two independent artifacts:
  #
  #   src/commands.c, cmd_resize_set():
  #     const Con *output = con_get_output(floating_con);
  #     … cwidth  = output->rect.width  * ((double)cwidth  / 100.0);
  #     … cheight = output->rect.height * ((double)cheight / 100.0);
  #   testcases/t/252-floating-size.t: `fake-outputs 1333x999`, then
  #     cmd 'resize set 33 ppt 20 ppt'; do_test(int(0.33*1333), int(0.2*999));
  #
  # i3's own test outputs carry no bar, so workspace == output there — which is
  # exactly why this never surfaces in i3's suite and had to be read off the C.
  #
  # ⚠ AND `move position center` USES A DIFFERENT RECT AGAIN — the WORKSPACE's.
  # src/commands.c, cmd_move_window_to_center(): for `position` it calls
  # `floating_center(floating_con, con_get_workspace(floating_con)->rect)` (only
  # `move absolute position center` uses the root rect). So this one chain sizes
  # against the output and places against the workspace. That asymmetry is
  # harmless at these percentages and would not be at 100 ppt of the height.
  #
  # MEASURED 2026-09-12, read-only, both hosts, i3 4.25.1. Rects from
  # `i3-msg -t get_outputs` / `-t get_workspaces`; cell size from `TIOCGWINSZ` on
  # each host's own running alacritty pty, which reports the grid in rows/cols AND
  # its size in pixels, so the cell is a DIVISION and not a DPI estimate. The pty
  # must be a DIRECT child of alacritty — a tmux pane's winsize is synthesized by
  # tmux and is not evidence about any font.
  #
  #   host       OUTPUT rect  workspace rect  bar   cell (px)     largest grid seen
  #   workbench  3440x1440    3440x1413       27px  11.0 x 22.0   312 x 63
  #   laptop     2256x1504    2256x1480       24px  19.0 x 37.0   118 x 39
  #
  # The WIDTH is unaffected (the bar is a full-width strip, so output width ==
  # workspace width); the HEIGHT is what the old derivation understated, by 27px
  # and 24px respectively. The laptop's cell is 1.73x wider and 1.68x taller than
  # the workbench's — 2.90x the AREA. (An earlier revision said "roughly twice in
  # each axis … roughly four times the area". That was never measured and it
  # over-states the gap, which is how one percentage came to look like it could
  # serve both hosts.)
  #
  # laptop, 90 ppt x 90 ppt -> ~2030 x ~1353 px of the 2256x1504 OUTPUT. Inside
  # the 2256x1480 workspace in both axes, which is the reported defect fixed —
  # and it stays comfortably inside under every decoration model below, so the
  # host that actually HAS the defect is fixed either way.
  # workbench, 64 ppt x 77 ppt -> 2201 x 1108 px of the 3440x1440 OUTPUT, which
  # is 200x50 cells of the BARE RECT at an 11.0x22.0 cell.
  #
  # 🔴 AND THE BARE RECT IS NOT WHAT RENDERS. The window's DECORATION (next
  # paragraph) takes 4 px off each axis, so the CLIENT area is 2197x1104 =
  # **199x50** cells — not the 200x50 the deployed `REVIEW_COLUMNS`x`REVIEW_LINES`
  # produces (200x50 CLIENT cells = 2200x1100 px). So:
  #
  #   🔴 THIS CHANGE DOES NOT KEEP THE WORKBENCH'S GRID IDENTICAL. It is one
  #   COLUMN narrower (199 vs 200) under the corrected model, and that is true of
  #   `78 ppt` as well — the height is the axis that matches, the width is not.
  #   An earlier revision of this comment promised "the workbench must come out
  #   of this change rendering what it renders now"; that promise is RETIRED
  #   rather than re-justified, because the arithmetic does not keep it.
  #
  #   What the stakes actually are: the workbench's
  #   `~/.config/mention-open/picks.jsonl` DOES NOT EXIST (measured 2026-09-12;
  #   the laptop's does), so the review TUI has never opened on this host and
  #   nobody has ever seen either grid. The complaint was the LAPTOP's.
  #
  # 🔴 77 OR 78 ARE INDISTINGUISHABLE, AND 77 IS KEPT ONLY BECAUSE IT IS WHAT IS
  # ALREADY COMMITTED. Under the corrected decoration model both render 199x50:
  #
  #   ppt            rect         client (BS_PIXEL bw2)   cells
  #   64 ppt 77 ppt  2201 x 1108  2197 x 1104             199 x 50
  #   64 ppt 78 ppt  2201 x 1123  2197 x 1119             199 x 50
  #
  # ⚠ An earlier revision of this comment said `78 ppt` is "51 rows: one cell
  # MORE than today, i.e. a visible change". **51 is a row count of the BARE
  # RECT** (1123 // 22 = 51); the decoration this same comment documents absorbs
  # it, and floor(1119 / 22) is 50. So that rationale for preferring 77 was
  # VOID. 🔴 IT HAS NOT BEEN REPLACED WITH A BETTER ONE — no purpose for 77 over
  # 78 could be found, and "I could not find a purpose" is the finding. 77 stays
  # because it is the value already committed and deployed in this branch, not
  # because it renders differently.
  #
  # ⚠ THE CELL ARITHMETIC MODELS THE i3 RECT PLUS A BORDER, SO THE GRID IS STILL
  # APPROXIMATE — do not quote these pixel figures to a tenth. One term is
  # modelled now and one is not.
  #
  # (a) DECORATION — MODELLED, and the earlier claim here was WRONG. This comment
  # said floats take i3's default `default_floating_border = BS_NORMAL` ("a
  # titlebar plus borders"). THEY DO NOT. `config.default_floating_border` is
  # applied ONLY inside `floating_enable()` under `if (automatic)`
  # (src/floating.c:353-354). `for_window … floating enable` is a COMMAND:
  # `cmd_floating()` calls `floating_enable(con, false)` (src/commands.c:1157),
  # and `run_assignments()` runs at src/manage.c:588 (map time; :746 on the
  # remanage path) — AFTER the `want_floating` decision at src/manage.c:462-546.
  # `want_floating` is set only by window-type atoms / `_NET_WM_STATE_MODAL` /
  # sticky / transient-for / a fixed min==max size hint, and an alacritty
  # toplevel matches none of those. So the container keeps what
  # `con_new_skeleton()` gave it (src/con.c:44, `config.default_border`), which
  # `default_border pixel 2` sets to **BS_PIXEL with `logical_px(2)`**, and
  # `current_border_width` is `config.default_border_width` (src/manage.c:546,
  # the non-floating arm). `con_border_style_rect_without_title()`'s
  # non-BS_NORMAL branch is `{bw, bw, -2bw, -2bw}` (src/con.c:1846-1849) — 2 px
  # on all four sides, so
  # the client area is rect MINUS 4 px in BOTH axes. No titlebar.
  #
  # LIVE, read-only (`i3-msg -t get_tree`, `-t get_config`, i3 4.25.1): the
  # running alacritty windows report `border=pixel, current_border_width=2`, and
  # the config carries no `default_floating_border`, no `new_float` and no
  # `for_window … border`. ⚠ Those live windows sit in a TABBED parent, where
  # `con_border_style()` (src/con.c:1942) overrides a non-BS_NORMAL style to
  # BS_NORMAL for a >1-child tabbed container — which is why their client height
  # is rect-2 rather than rect-4. A FLOAT's parent is a CT_FLOATING_CON with
  # `layout = L_SPLITH` (src/floating.c:291), so no override applies and the
  # -4/-4 above is the float's case. The live read is evidence for the BORDER
  # STYLE and the CELL SIZE; the float's own inset comes off the C.
  #
  # (b) SIZE-INCREMENT SNAPPING — STILL NOT MODELLED. `floating_resize`
  # (src/floating.c) upscales the decorated rect to a multiple of the window's
  # width/height increments. Quantifying it needs a window opened or resized on
  # the operator's live desk, which is theirs to do.
  reviewSizePpt =
    if isLaptop then "90 ppt 90 ppt"
    else "64 ppt 77 ppt";
in
''
set $mod Mod1

font pango:monospace 8
${monitorLayout}

# NetworkManager applet
exec --no-startup-id nm-applet

# Volume control (PipeWire via pactl, 5% steps)
bindsym XF86AudioRaiseVolume exec --no-startup-id pactl set-sink-volume @DEFAULT_SINK@ +5%
bindsym XF86AudioLowerVolume exec --no-startup-id pactl set-sink-volume @DEFAULT_SINK@ -5%
bindsym XF86AudioMute exec --no-startup-id pactl set-sink-mute @DEFAULT_SINK@ toggle
bindsym XF86AudioMicMute exec --no-startup-id pactl set-source-mute @DEFAULT_SOURCE@ toggle
${brightnessBindings}

floating_modifier $mod

# Float any window explicitly launched with WM_CLASS "float" (e.g. the VPN detail
# terminal: `alacritty --class float,float`). No such rule existed pre-migration.
for_window [class="float"] floating enable
# 🔴 THE RULE ABOVE DELIBERATELY SETS NO POSITION, AND THIS ONE IS NARROWER FOR
# THAT REASON. `class="float"` is shared by every float terminal in the system —
# a dozen bar-click detail windows in nix/graphical.nix, plus media-menu and
# airvpn-menu — so hanging `move position center` off it would relocate windows
# nobody asked to move. alacritty's `--class` is `<general>,<instance>`, so the
# mention-open picker names ITSELF in the instance half (`PICKER_CLASS` in
# scripts/mention-open.py), and i3 matches that with `instance=`: this rule
# catches exactly that one window.
#
# Without it the picker takes i3's default placement for a new float, which pins
# it to the LEFT edge of the screen — the wrong place for a modal the operator
# has just summoned and is about to read.
#
# `floating enable` is REPEATED rather than inherited. i3 runs every matching
# `for_window` in file order, so today the rule above has already floated the
# window by the time this line runs — but `move position center` on a still-TILED
# window is a silent no-op, and this rule should not be able to become one if the
# rule above is ever reordered or narrowed. Repeating it is idempotent.
#
# NO `resize set` HERE, ON PURPOSE. The picker already sizes itself from
# `PICKER_COLUMNS`/`PICKER_LINES` via `-o window.dimensions.{columns,lines}` on
# the alacritty command line, in CHARACTER CELLS. An i3 `resize set` would
# restate that geometry in PIXELS, in a second file that cannot see those
# constants — so changing the picker's size would silently leave i3 forcing the
# old one. One geometry decision, one place: alacritty sizes, i3 only centres.
for_window [class="float" instance="mention-open"] floating enable, move position center
# 🔴 THE REVIEW TUI IS THE ONE FLOAT i3 DOES SIZE, AND i3 IS ITS ONLY AUTHORITY —
# which is not a contradiction of the paragraph above but its consequence.
# `window.dimensions` is a count of CHARACTER CELLS, and a cell is not a length:
# its pixel size is a function of the font size and the display's DPI. The picker
# is small enough that no display makes its cell count overflow, so leaving its
# geometry in one place costs nothing. The review window is not: at 200x50 cells it
# opened bigger than the laptop's screen (3800x1850 px against a 2256x1480
# workspace — 168% x 125%) and was unusable.
#
# scripts/mention-open.py therefore passes NO `window.dimensions` for this window
# at all: the cell hint is DELETED, not lowered. A hint and an authority are two
# numbers for one decision, and the hint could only ever be wrong on one host —
# 140x40, the lowered value, is 2660x1480 px on the laptop, still 118% of its
# width. Without a hint alacritty maps at its own default (~80x24: 880x528 px on
# the workbench, 1520x888 on the laptop, comfortably inside both workspaces) and
# i3 resizes UP, so the pre-resize flash is small-then-right instead of
# oversized-then-right. If this rule is ever absent the window is SMALL, which is
# usable; the hint's failure mode was a window larger than the screen, which is
# not. One geometry decision, one place — here.
#
# THE PERCENTAGES ARE PER-HOST (`reviewSizePpt` in the `let` block above, which
# carries the measured rects, the cell sizes, the arithmetic and the i3 source
# citations). 🔴 `ppt` IS A PERCENTAGE OF THE **OUTPUT** RECT, NOT THE WORKSPACE —
# i3 4.25.1 `src/commands.c`, `cmd_resize_set()`, multiplies by
# `con_get_output(floating_con)->rect`, so the bar's 27px (workbench) / 24px
# (laptop) is NOT subtracted. `ppt` is the right UNIT on any display, but a
# percentage is still not a host-independent SIZE: 90 ppt is ~2030x1354 on the
# laptop and 3096x1296 on the workbench, +41% wider than the size that drew the
# complaint. The UNIT is not a taste call — `resize set 90 90` is legal i3 meaning
# 90 PIXELS, because the unit defaults to px when omitted — and neither is the
# FACT that the two hosts differ; scripts/tests/test_i3_picker_centering.py
# asserts both, so collapsing these back to one number reddens.
#
# ⚠ `move position center` IN THIS SAME CHAIN CENTRES AGAINST THE **WORKSPACE**
# rect, not the output's (`cmd_move_window_to_center()` -> `floating_center(…,
# con_get_workspace(…)->rect)`; only `move absolute position center` uses the root
# rect). One chain, two different rects. Harmless at these percentages; it would
# not be near 100 ppt of the height, where the window would be taller than the
# workspace it is being centred in.
#
# `floating enable` is REPEATED, exactly as in the picker's rule and for a sharper
# reason: `resize set` on a still-TILED window is a silent no-op, so this rule must
# not depend on the shared rule above still being reached first.
#
# `instance="mention-review"` and NOT `class="float"`: the general half is shared by
# every float terminal in the system, so sizing there would resize a dozen bar-click
# detail windows, media-menu, airvpn-menu — and the picker, undoing the paragraph
# above. The instance half is `REVIEW_CLASS` in scripts/mention-open.py, and
# scripts/tests/test_i3_picker_centering.py derives it from that constant rather
# than spelling it, so a rename there reddens instead of leaving this rule inert.
#
# 🔴 DEPLOYING THIS NEEDS THREE STEPS, AND THE THIRD IS NOT OPTIONAL:
#
#     merge  ->  scripts/ship.sh  ->  i3-msg reload   (on EACH host)
#
# The `i3-msg reload` is a REQUIRED DEPLOY STEP, not a verification nicety. The
# two halves of this feature land on different schedules: scripts/mention-open.py
# is exec'd straight out of the working tree (nix/programs/alacritty/default.nix
# runs ~/workspace/devrc/scripts/mention-open.py), so a plain `git pull`
# makes the hint-DELETION live with no switch at all — while THIS file is
# `xdg.configFile."i3/config".text` (nix/graphical.nix), which needs a
# `home-manager switch` AND then an explicit reload, because i3 does not re-read
# its config when the file changes and nothing in nix/graphical.nix or any
# activation script reloads it.
#
# So between `ship.sh` and the reload there is a window in which NEITHER half
# sizes the TUI: the script passes no dimensions, the running i3 has no rule, and
# the review window opens at alacritty's own default ~80x24 on BOTH hosts. That is
# small but usable (the old failure mode was a window larger than the screen), and
# on the workbench it IS a large change from today's 200x50, persisting until
# somebody reloads. ⚠ That window is not the only change to the workbench: the
# RESIZED size is ~199x50 client cells, one column narrower than today (see
# `reviewSizePpt`'s derivation above). This change does not keep the workbench's
# grid identical, and the comment here used to claim the PR "promises not to"
# change it.
#
# An automatic reload is deliberately NOT wired into the activation script: that
# would reload i3 on every future `home-manager switch`, which is a change to
# shared graphical infrastructure far beyond this window, and it is the operator's
# call.
#
# ⚠ WHAT IS AND IS NOT ESTABLISHED about i3 honouring this chain. The MECHANISM is
# read off i3 4.25.1's own source and test suite: `cmd_resize_set()` takes the
# `con_inside_floating()` branch and multiplies `ppt` by the OUTPUT rect, and
# `testcases/t/252-floating-size.t` exercises `resize set <n> ppt <n> ppt` on a
# floating window against a fake output. What is still NOT verified live is the
# end-to-end: that this `for_window` fires at map time on these hosts and the
# window comes up at that size. Confirming that needs an `i3-msg reload` plus an
# opened window on the operator's desk, which is theirs to run. Note also that
# this is the only `resize set` DIRECTIVE in this file outside the `mode "resize"`
# bindings, and this file is the only i3 config in the repo — so there is no prior
# art here to argue from.
for_window [class="float" instance="mention-review"] floating enable, resize set ${reviewSizePpt}, move position center
# 🔴 `(?i)` IS LOAD-BEARING, not decoration. i3 criteria are PCRE and
# CASE-SENSITIVE by default (the userguide's "case-insensitive" examples are
# showing you how to opt IN with `(?i)`), and `class` matches the SECOND field
# of WM_CLASS — the one toolkits conventionally capitalise. So a bare
# `class="espanso"` fires only if espanso reports a lowercase class, and if it
# reports `Espanso` the rule silently never matches: no error, no warning, just
# a window that keeps tiling. Which one espanso 2.4.0 actually sets could not be
# determined without popping its search window on a live desktop, so the rule is
# written to match either.
for_window [class="(?i)espanso"] floating enable
${rigControlFloat}

# Terminal
bindsym $mod+Return exec [ ! "$I3CONFIG_DEFAULT_TERMINAL" = "" ] && $I3CONFIG_DEFAULT_TERMINAL || i3-sensible-terminal

# Kill focused window
bindsym $mod+Shift+q kill

# Application launcher (rofi replaces fragile dmenu filter pipeline)
bindsym $mod+d exec --no-startup-id rofi -show drun -show-icons -theme gruvbox-dark-hard

# Screenshots (ksnip)
bindsym Print exec --no-startup-id ksnip --rectarea
bindsym $mod+Print exec --no-startup-id ksnip --fullscreen --save

# Screen lock
bindsym $mod+Shift+x exec --no-startup-id i3lock -c 282828

# Focus (vim-style)
bindsym $mod+h focus left
bindsym $mod+j focus down
bindsym $mod+k focus up
bindsym $mod+l focus right

# Focus (arrow keys)
bindsym $mod+Left focus left
bindsym $mod+Down focus down
bindsym $mod+Up focus up
bindsym $mod+Right focus right

# Move (vim-style, consistent with focus)
bindsym $mod+Shift+h move left
bindsym $mod+Shift+j move down
bindsym $mod+Shift+k move up
bindsym $mod+Shift+l move right

# Move (arrow keys)
bindsym $mod+Shift+Left move left
bindsym $mod+Shift+Down move down
bindsym $mod+Shift+Up move up
bindsym $mod+Shift+Right move right

# Fullscreen
bindsym $mod+f fullscreen toggle

# Layouts
# tabbed moved off $mod+w (was) so Alt+w passes through to tmux scratch11 (Wheat)
bindsym $mod+Shift+t layout tabbed
bindsym $mod+e layout toggle split
bindsym $mod+equal exec --no-startup-id ~/workspace/devrc/scripts/i3-grid

# Floating
bindsym $mod+Shift+space floating toggle
bindsym $mod+space focus mode_toggle

# Focus parent
bindsym $mod+a focus parent

# Workspaces
set $ws1 "1"
set $ws2 "2"
set $ws3 "3"
set $ws4 "4"
set $ws5 "5"
set $ws6 "6"
set $ws7 "7"
set $ws8 "8"
set $ws9 "9"
set $ws10 "10"

bindsym $mod+1 workspace number $ws1
bindsym $mod+2 workspace number $ws2
bindsym $mod+3 workspace number $ws3
bindsym $mod+4 workspace number $ws4
bindsym $mod+5 workspace number $ws5
bindsym $mod+6 workspace number $ws6
bindsym $mod+7 workspace number $ws7
bindsym $mod+8 workspace number $ws8
bindsym $mod+9 workspace number $ws9
bindsym $mod+0 workspace number $ws10

bindsym $mod+Shift+1 move container to workspace number $ws1
bindsym $mod+Shift+2 move container to workspace number $ws2
bindsym $mod+Shift+3 move container to workspace number $ws3
bindsym $mod+Shift+4 move container to workspace number $ws4
bindsym $mod+Shift+5 move container to workspace number $ws5
bindsym $mod+Shift+6 move container to workspace number $ws6
bindsym $mod+Shift+7 move container to workspace number $ws7
bindsym $mod+Shift+8 move container to workspace number $ws8
bindsym $mod+Shift+9 move container to workspace number $ws9
bindsym $mod+Shift+0 move container to workspace number $ws10

# Reload / restart / exit
bindsym $mod+Shift+c reload
bindsym $mod+Shift+r restart
bindsym $mod+Shift+e exec "i3-nagbar -t warning -m 'Exit i3?' -B 'Yes, exit i3' 'i3-msg exit'"

# Resize mode (vim-style, consistent with focus/move)
mode "resize" {
        bindsym h resize shrink width 10 px or 10 ppt
        bindsym j resize grow height 10 px or 10 ppt
        bindsym k resize shrink height 10 px or 10 ppt
        bindsym l resize grow width 10 px or 10 ppt

        bindsym Left resize shrink width 10 px or 10 ppt
        bindsym Down resize grow height 10 px or 10 ppt
        bindsym Up resize shrink height 10 px or 10 ppt
        bindsym Right resize grow width 10 px or 10 ppt

        bindsym Return mode "default"
        bindsym Escape mode "default"
        bindsym $mod+r mode "default"
}

bindsym $mod+r mode "resize"

# 🔴 GAME MODE — an EMPTY binding mode, and "empty" is the entire mechanism.
#
# `$mod` is Mod1 (line 66) — ALT, not Super. i3 therefore holds a global X11
# grab on ~60 Alt combos: Alt+Tab, Alt+1..Alt+0, Alt+Shift+1..0, Alt+Return,
# Alt+d/f/e/a/b/n/r/h/j/k/l/space/grave/minus/equal. A grab means the keypress
# is delivered to i3 and NEVER reaches the focused window, so inside a game
# every one of those keys is simply dead — this is not merely "rofi pops over
# the game", the game does not see the key at all.
#
# Two fixes were considered and rejected:
#   * a guard script on the exec (`bindsym $mod+d exec launcher-guard`) — i3
#     still holds the grab and still swallows the key, so the game stays deaf.
#     It only changes what i3 does AFTER eating the keypress.
#   * a conditional grab (bind only when the focused window is not a game) —
#     `bindsym` takes no `[class=…]` criteria; i3 has no such mechanism.
# Switching binding mode is the ONLY native way to make i3 release the grabs:
# entering a mode ungrabs every default-mode binding and grabs only this mode's.
# So the emptiness below is load-bearing — anything added here is a key the
# game goes back to not receiving.
#
# TWO escape keys on purpose, not redundancy: not every keyboard has a
# dedicated Pause key, and the worst failure this feature can have is being
# stuck in game mode with no way out. Both are UNBOUND in the default mode, and
# `test_i3_game_mode.py` fails if a future binding silently takes either one.
# Neither is a $mod combo — a mode that exists to release Alt must not need Alt
# to leave. Rescue path if the bar is hidden behind a fullscreen game:
# `DISPLAY=:0 i3-msg mode default` over SSH (see the `bar` skill).
#
# The `pkill -RTMIN+18` repaints the bar's game pill instantly. 18 must match
# `gamemodeBlock.signal` in nix/graphical.nix and the pill's own click handler;
# all three are pinned together by test_i3_game_mode.py. There is deliberately
# NO `bindsym … mode "game"` in the default mode: the way IN is the bar pill
# (getting in is the easy direction — the bar is visible and clickable then),
# and a default-mode binding would be one more grab for no gain.
mode "game" {
        bindsym Pause       mode "default", exec --no-startup-id pkill -RTMIN+18 i3status-rs
        bindsym Scroll_Lock mode "default", exec --no-startup-id pkill -RTMIN+18 i3status-rs
}

# Status bar (Gruvbox dark) — statusline is now i3status-rust (i3status-rs); the
# i3bar workspace/background colors below stay as-is (i3status-rust replaces only
# the statusline content, not the i3bar chrome).
bar {
        status_command i3status-rs ~/.config/i3status-rust/config-top.toml
        font pango:JetBrainsMono Nerd Font 10
        position top
        colors {
                background #282828
                statusline #ebdbb2
                separator  #504945
                focused_workspace  #83a598 #282828 #83a598
                active_workspace   #504945 #282828 #ebdbb2
                inactive_workspace #282828 #282828 #665c54
                urgent_workspace   #cc241d #cc241d #ebdbb2
        }
}

# Launch browser
bindsym $mod+b exec --no-startup-id brave

# ($mod+i used to open the "agent-ops" mission-control dashboard; it was freed
# for tmux scratch15 (fern) long before the dashboard itself was RETIRED, along
# with its tmux prefix+A popup and its bar button. Nothing here launches it.)

# Quick workspace switching
bindsym $mod+Tab workspace back_and_forth

# Scratchpad
bindsym $mod+minus move scratchpad

# Notifications (dunst). dunstctl ships with the dunst package on PATH.
bindsym $mod+n exec --no-startup-id dunstctl history-pop            # recall last dismissed
bindsym $mod+Shift+n exec --no-startup-id dunstctl set-paused toggle && pkill -RTMIN+15 i3status-rs # manual DND (quiet mode) + refresh the notifications bar block

# Background blur toggle (picom dual_kawase)
bindsym $mod+Shift+b exec --no-startup-id ~/workspace/devrc/scripts/toggle-blur.sh
bindsym $mod+grave exec --no-startup-id dunstctl close-all          # clear the whole stack

# Thin borders
default_border pixel 2
''
