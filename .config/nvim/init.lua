nvimConfigDir=string.format("%s/.config/nvim", os.getenv("DEVRC_DIR"))

local function source(relPath)
  dofile(string.format("%s/lua/%s", nvimConfigDir, relPath))
end

source("helpers.lua")

source("config/native.lua")
source("map/native.lua")

source("plugins.lua")
source("nvim_lsp.lua")

source("config/plugin/treesitter.lua")
source("config/plugin/cmp.lua")
source("config/plugin/harpoon.lua")

source("map/plugin/fzf.lua")
source("map/plugin/spectre.lua")
source("map/plugin/qdr.lua")
--source("map/plugin/harpoon.lua")
