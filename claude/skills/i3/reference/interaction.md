# i3 — keyboard, mouse, clipboard and compound interaction

## Keyboard simulation ("type")
`xdotool` sends keystrokes to the **focused** window.

**Safety protocol — always in this order:**
1. Confirm which window is focused (`i3-msg -t get_tree | jq …`).
2. If targeting a specific window, focus it first via i3-msg criteria.
3. Brief settle after the focus switch (`sleep 0.1`) so X11 catches up.
4. Then send keystrokes.

```bash
# Type text with inter-key delay (ms) to avoid dropped chars
xdotool type --delay 12 "hello world"

# Type into a specific window (focus via i3 first — more reliable than xdotool search)
i3-msg '[class="Brave-browser"] focus' && sleep 0.1 && xdotool type --delay 12 "search query"

# Special characters
xdotool type --clearmodifiers --delay 12 "text with @special #chars"
```

```bash
# Single keys
xdotool key Return
xdotool key Escape
xdotool key Tab
xdotool key BackSpace

# Modifier combos (lowercase modifier names)
xdotool key ctrl+s           # save
xdotool key ctrl+c           # copy
xdotool key ctrl+v           # paste
xdotool key ctrl+shift+t     # reopen tab in browser
xdotool key alt+F4           # close window
xdotool key ctrl+l           # browser address bar
xdotool key super+l          # (if bound to lock)

# Sequence
xdotool key ctrl+a && sleep 0.05 && xdotool key ctrl+c   # select all + copy
```

**IMPORTANT:** `xdotool type` sends raw characters. For anything needing a modifier
(Ctrl+C is NOT the letter C with ctrl), use `xdotool key`, never `xdotool type`.

⚠ The harness blocks a command that *starts* with `sleep N && …`. Keep the sleep in the
middle of a chain (as above), or use the `Monitor` tool for a real wait.

## Mouse simulation ("click")
Coordinate-based and therefore fragile — use only when no better method exists
(prefer i3 IPC; prefer the `browser` skill or Playwright for browsers).

**Safety protocol for clicks:**
1. Screenshot first to see what is at the target coordinates.
2. Confirm the target visually before clicking.
3. Screenshot after to verify the result.
4. For consequential clicks, tell the user what you are about to click and why.

```bash
xdotool getmouselocation                              # current position (debugging)
xdotool mousemove 500 300 click 1                     # left-click at absolute coords
xdotool mousemove 500 300 click 3                     # right-click
xdotool mousemove 500 300 click 2                     # middle-click
xdotool mousemove 500 300 click --repeat 2 --delay 50 1   # double-click

# Click relative to a window (more stable than absolute coords)
WID=$(xdotool search --class "Brave-browser" | head -1)
xdotool mousemove --window "$WID" 100 50 click 1

# Click then restore the mouse position
ORIG=$(xdotool getmouselocation --shell)
xdotool mousemove 500 300 click 1
eval "$ORIG" && xdotool mousemove "$X" "$Y"
```

```bash
# Scroll: button 4 = up, button 5 = down
xdotool click --repeat 3 --delay 50 5                 # scroll down 3 clicks
xdotool click --repeat 3 --delay 50 4                 # scroll up 3 clicks
xdotool mousemove 500 300 click --repeat 5 --delay 50 5   # scroll at a position

# Drag from (x1,y1) to (x2,y2)
xdotool mousemove 100 200 mousedown 1 mousemove 500 400 mouseup 1
```

## Clipboard
```bash
XCLIP="xclip"

echo -n "text to copy" | "$XCLIP" -selection clipboard     # write
"$XCLIP" -selection clipboard -o                            # read

# Copy the focused window's selection, then read it
xdotool key ctrl+c && sleep 0.1 && "$XCLIP" -selection clipboard -o

# Paste
echo -n "text to paste" | "$XCLIP" -selection clipboard && sleep 0.05 && xdotool key ctrl+v
```

🔴 Reading the clipboard may expose secrets (passwords, tokens). Read it only when the user
explicitly asks. Never log clipboard contents to a file or a commit message.

## Desktop notifications (dunst)
```bash
notify-send "Title" "Message body"
notify-send -u critical "Alert" "Something important"
notify-send -t 5000 "Timed" "Disappears in 5 seconds"
```

## Compound patterns

**Read text out of a GUI window**
```bash
i3-msg '[class="Brave-browser"] focus' && sleep 0.1
xdotool key ctrl+a && sleep 0.05 && xdotool key ctrl+c && sleep 0.1
xclip -selection clipboard -o
```

**Fill a text field in a GUI app**
```bash
i3-msg '[class="TargetApp"] focus' && sleep 0.1
xdotool mousemove 400 300 click 1 && sleep 0.1        # coords from a screenshot
xdotool key ctrl+a && sleep 0.05 && xdotool type --delay 12 "new text"
```

**Navigate a browser to a URL**
```bash
i3-msg '[class="Brave-browser"] focus' && sleep 0.1
xdotool key ctrl+l && sleep 0.1
xdotool type --delay 8 "https://example.com" && sleep 0.05 && xdotool key Return
```

**Screenshot → verify → act loop**
```bash
flameshot full -p /tmp/claude-i3-screenshot.png   # 1. capture
# 2. Read the image (multimodal) — analyze what is visible
# 3. Decide the action
# 4. Execute it (click, type, …)
flameshot full -p /tmp/claude-i3-screenshot.png   # 5. capture again
# 6. Read and confirm
rm -f /tmp/claude-i3-screenshot.png               # 7. clean up
```

**IMPORTANT:** For browser automation beyond simple URL navigation, prefer the `browser`
skill (Zach's real logged-in Brave) or Playwright — both beat coordinate-based clicking.
Reserve xdotool mouse interaction for native GUI apps with no programmatic API.
