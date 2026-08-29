vim.g.lazygit_floating_window_scaling_factor = 1
vim.g.lazygit_use_custom_config_file_path = 1
-- 🔴 Derived from init.lua's self-located root, NOT from the environment.
-- $DEVRC_DIR is set only by a systemd Environment= line in graphical.nix, so in
-- any non-graphical session it is nil and string.format("%s", nil) yields the
-- literal "nil" -- lazygit was silently pointed at "nil/.config/lazygit/
-- config.yml" over ssh. `nvimConfigDir` is set by init.lua before this file is
-- sourced and is <repo>/.config/nvim, so one level up is the repo root.
local repoRoot = assert(nvimConfigDir, "nvimConfigDir unset -- init.lua must run first")
                   :match("^(.*)/%.config/nvim$")
assert(repoRoot, "could not derive the repo root from " .. tostring(nvimConfigDir))
local path = string.format("%s/.config/lazygit/config.yml", repoRoot)

vim.g.lazygit_config_file_path = path

