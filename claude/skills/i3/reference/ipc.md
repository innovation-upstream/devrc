# i3 — IPC reference

## Query commands (`i3-msg -t <type>`)
| Type | Returns |
|------|---------|
| get_tree | Full container tree with all windows, layouts, geometry |
| get_workspaces | Workspace list: num, name, visible, focused, urgent, output, rect |
| get_outputs | Monitor list: name, active, primary, current_workspace, rect |
| get_marks | Array of all mark names |
| get_binding_modes | Available modes (default, resize) |
| get_bar_config | Bar IDs; with ID arg returns full bar config |
| get_version | i3 version info |
| get_config | Loaded config file contents |
| get_binding_state | Current binding mode name |

## Container tree node fields
Key fields when parsing `get_tree`:
- `type`: "root", "output", "con", "floating_con", "workspace", "dockarea"
- `name`: window title or workspace name
- `window`: X11 window ID (null for containers)
- `window_properties`: {class, instance, title, window_role, machine}
- `focused`: boolean
- `urgent`: boolean
- `layout`: "splith", "splitv", "stacked", "tabbed"
- `marks`: array of mark strings
- `rect`: {x, y, width, height}
- `nodes`: tiling children
- `floating_nodes`: floating children
- `fullscreen_mode`: 0 (none), 1 (output), 2 (global)
- `floating`: "auto_on", "auto_off", "user_on", "user_off"

## Action commands (`i3-msg '<command>'`)
- **Focus**: `focus left|right|up|down|parent|child|floating|tiling|mode_toggle`
- **Move**: `move left|right|up|down|to workspace <n>|to output <name>`
- **Workspace**: `workspace <n>|next|prev|back_and_forth`
- **Layout**: `layout splith|splitv|tabbed|stacking|toggle [layouts...]`
- **Split**: `split h|v|toggle`
- **Floating**: `floating enable|disable|toggle`
- **Fullscreen**: `fullscreen enable|disable|toggle`
- **Sticky**: `sticky enable|disable|toggle`
- **Border**: `border normal|pixel|none`
- **Resize**: `resize grow|shrink width|height <n> px [or <n> ppt]`
- **Resize set**: `resize set <width> ppt <height> ppt`
- **Mark**: `mark [--add|--replace|--toggle] <name>`
- **Unmark**: `unmark [name]`
- **Scratchpad**: `move scratchpad`, `scratchpad show`
- **Exec**: `exec [--no-startup-id] <command>`
- **Rename**: `rename workspace <old> to <new>`
- **Reload/Restart**: `reload`, `restart`
- **Exit**: `exit` (REQUIRES explicit user confirmation)

## Criteria selectors
Prefix commands with `[criteria]` to target specific windows:
```
[class="X"]        Window class (WM_CLASS second value)
[instance="X"]     Window instance (WM_CLASS first value)
[title="X"]        Window title (supports regex)
[con_mark="X"]     Container mark
[workspace="X"]    Windows on workspace
[con_id=N]         Container ID (from get_tree)
[id=N]             X11 window ID
[urgent=latest]    Most recently urgent window
[floating]         Floating windows only
[tiling]           Tiling windows only
[window_role="X"]  Window role
```

Multiple criteria combine as AND: `[class="Alacritty" title=".*vim.*"]`

## Common action patterns
```bash
i3-msg '[class="Brave-browser"] focus'                       # focus by class
i3-msg '[title=".*pattern.*"] focus'                         # focus by title substring
i3-msg 'move container to workspace number 3'                # move focused window
i3-msg '[class="Brave-browser"] move container to workspace number 2'
i3-msg 'layout tabbed'
i3-msg 'layout toggle split'
i3-msg 'resize grow width 100 px'
i3-msg 'resize set 60 ppt 0 ppt'
i3-msg 'move scratchpad'
i3-msg 'scratchpad show'
i3-msg 'mark mymark'
i3-msg '[con_mark="mymark"] focus'
i3-msg 'exec --no-startup-id brave'                          # always --no-startup-id
```

## Layout recipes (for `arrange`)
| Recipe | Layout |
|---|---|
| `dev` | terminal left 60%, browser right 40% |
| `monitor` | split into 3 columns for dashboards |
| `pair` | two terminals side by side 50/50 |
| `present` | single fullscreen window |

```bash
# dev
i3-msg 'workspace 1; layout splith'
i3-msg '[class="Alacritty"] focus; resize set 60 ppt 0 ppt'
i3-msg '[class="Brave-browser"] move container to workspace number 1'

# pair — query the tree first to identify the two terminal windows, then arrange
i3-msg 'workspace 1; layout splith'
```

Query current state to understand the starting point, execute the command sequence,
then verify the final layout matches the recipe's intent.
