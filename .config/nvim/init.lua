nvimConfigDir=string.format("%s/.config/nvim", os.getenv("DEVRC_DIR"))

local function source(relPath)
  dofile(string.format("%s/lua/%s", nvimConfigDir, relPath))
end

source("helpers.lua")

source("map/native.lua")

source("plugins.lua")
source("nvim_lsp.lua")

source("map/plugin/telescope.lua")
