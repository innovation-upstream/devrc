---
description: "Rig-control: check status, toggle sleep/wake, edit colors, restart timers. Args: (none)=status, sleep, wake, colors, edit, restart."
---

Rig-control RGB gradient system — workbench only.

Current state:
!`cat "$HOME/.cache/rig-control/state" 2>/dev/null || echo "no state file"`
Active timers:
!`systemctl --user list-timers rig-control-* --no-pager 2>/dev/null || echo "no timers"`
Current gradient color:
!`~/workspace/devrc/scripts/rig-control-fade --print 2>/dev/null || echo "cannot read"`

Action requested: $ARGUMENTS

- No args or "status": show the above and summarize.
- "sleep": run `rig-control.sh sleep`, verify state changed to "sleeping".
- "wake": run `rig-control.sh wake`, verify state changed to "awake".
- "colors": show `scripts/rig-control-colors.conf` with current time bracket highlighted.
- "edit": guide the user through editing the color schedule — show current, ask what to change, remind to `ship.sh` after.
- "restart": run `systemctl --user restart rig-control-fade.timer` and verify it's active.
