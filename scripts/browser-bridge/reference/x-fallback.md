# The X-server fallback (xdotool / maim)

**Load this when:** `browser screenshot` is unsatisfactory for a genuinely
un-composited window and you must capture the raw X window instead · `xdotool search`
returns nothing · a capture came back blank/dark while the command exited 0 · a
capture shows a DIFFERENT tab than the one you navigated.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`. Try CDP `screenshot` first — it works on a
BACKGROUND/occluded tab and needs none of this.

If a window is on another i3 workspace and even CDP capture is unsatisfactory, you
can still bypass the extension and capture the X window directly:

```sh
# 1. The Bash tool has NO X env by default — without this xdotool silently
#    "sees" zero windows and every search returns nothing.
export DISPLAY=:0 XAUTHORITY=/home/zach/.Xauthority

# 2. Find the window by PAGE TITLE, not by class — several Brave windows exist
#    and class-matching gives you an arbitrary one.
nix-shell -p xdotool --run 'xdotool search --name "<page title fragment>"'

# 3. Make it VISIBLE — focusing its workspace is not enough.
i3-msg '[id="<winid>"] focus'

# 4. Capture (settle first; the compositor needs a beat after the raise).
nix-shell -p xdotool maim --run 'xdotool sleep 2; maim -i <winid> out.png'
nix-shell -p imagemagick --run 'magick out.png -crop WxH+X+Y +repage cropped.png'
```

⚠ Step 3 focuses a window — that TAKES THE OPERATOR'S SCREEN, same cost as
`browser activate`. 🔴 RECORD both axes BEFORE step 3 (`xdotool getactivewindow`;
`i3-msg -t get_workspaces | jq -r '.[]|select(.focused).num'`), then restore BOTH
afterwards, on failure too: `i3-msg '[id="<prev-winid>"] focus'` **and**
`i3-msg workspace <n>`. Focusing a window that lives on ANOTHER workspace switches
to that workspace, so step 3 moves the WORKSPACE by construction, not just focus.
Restoring the recorded window usually switches back — but not if it has closed
(a criteria command silently no-ops) or there was none to record, so restore the
workspace explicitly.

Two traps that produce a confidently-wrong result:

- **A window on the focused workspace can still be *behind* another window** — the
  capture then comes back blank/dark while `maim` exits 0. **Verify by LOOKING at
  the image**, never by trusting the exit code.
- **`maim -i <winid>` captures whatever tab is active in that window**, which may
  not be the tab you just `nav`ed. After navigating, re-find the window **by the
  new page title** so you're capturing the tab you think you are.
- *(Historical note: the "future option" of a `chrome.debugger` + CDP
  `Page.captureScreenshot` path for off-screen tabs is now IMPLEMENTED and is the
  primary `screenshot` path — this X fallback is no longer the way to capture a
  background tab.)*
