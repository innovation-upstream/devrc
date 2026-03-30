''
set $mod Mod1

font pango:monospace 8

# NetworkManager applet
exec --no-startup-id nm-applet

# Volume control (PipeWire via pactl, 5% steps)
bindsym XF86AudioRaiseVolume exec --no-startup-id pactl set-sink-volume @DEFAULT_SINK@ +5%
bindsym XF86AudioLowerVolume exec --no-startup-id pactl set-sink-volume @DEFAULT_SINK@ -5%
bindsym XF86AudioMute exec --no-startup-id pactl set-sink-mute @DEFAULT_SINK@ toggle
bindsym XF86AudioMicMute exec --no-startup-id pactl set-source-mute @DEFAULT_SOURCE@ toggle

floating_modifier $mod

# Terminal
bindsym $mod+Return exec [ ! "$I3CONFIG_DEFAULT_TERMINAL" = "" ] && $I3CONFIG_DEFAULT_TERMINAL || i3-sensible-terminal

# Kill focused window
bindsym $mod+Shift+q kill

# Application launcher (rofi replaces fragile dmenu filter pipeline)
bindsym $mod+d exec --no-startup-id rofi -show drun -show-icons -theme gruvbox-dark-hard

# Screenshots (flameshot)
bindsym Print exec --no-startup-id flameshot gui
bindsym $mod+Print exec --no-startup-id flameshot full -p ~/Pictures

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
bindsym $mod+w layout tabbed
bindsym $mod+e layout toggle split

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

# Status bar (Gruvbox dark)
bar {
        status_command SCRIPT_DIR=/etc/nixos/i3blocks-scripts i3blocks -c /etc/i3blocks.conf
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

# Speech-to-text dictation (faster-whisper)
bindsym $mod+s exec --no-startup-id ~/workspace/devrc/scripts/dictate

# Quick workspace switching
bindsym $mod+Tab workspace back_and_forth

# Scratchpad
bindsym $mod+minus move scratchpad
bindsym $mod+equal scratchpad show

# Thin borders
default_border pixel 2

''
