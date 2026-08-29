-- 🔴 SELF-LOCATING, deliberately -- do NOT reintroduce os.getenv("DEVRC_DIR").
-- That variable is set only by a systemd Environment= line in graphical.nix, so
-- outside a graphical session (ssh, a bare TTY, a unit, cron) it is nil, and
-- string.format("%s", nil) yields the literal "nil" -- every source() below then
-- resolved under "nil/.config/nvim/" and the whole lua half silently did not
-- load. Measured over real ssh 2026-08-29.
--
-- This file's own path cannot be unset, so deriving the directory from it
-- removes the dependency rather than defaulting it. nix substitutes the same
-- path into init.vim at build time so that THIS file can be found in the first
-- place; the two must agree, and neither reads the environment.
local thisFile = debug.getinfo(1, "S").source:sub(2)
nvimConfigDir = thisFile:match("^(.*)/init%.lua$")
assert(nvimConfigDir, "could not derive the nvim config dir from " .. thisFile)

local function source(relPath)
  dofile(string.format("%s/lua/%s", nvimConfigDir, relPath))
end

source("config/plugin/gruvbox.lua")
source("config/plugin/lazygit.lua")

source("helpers.lua")

source("config/native.lua")
source("map/native.lua")

source("plugins.lua")
source("nvim_lsp.lua")

source("config/plugin/treesitter.lua")
source("config/plugin/cmp.lua")

source("map/plugin/fzf.lua")
source("map/plugin/spectre.lua")
source("map/plugin/qdr.lua")
source("map/plugin/lazygit.lua")
source("map/plugin/ranger.lua")
source("map/plugin/claudecode.lua")
source("map/plugin/tig.lua")
