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
source("config/plugin/compe.lua")
source("config/plugin/eslint.lua")
source("config/plugin/nvim_tree.lua")

source("map/plugin/fzf.lua")
source("map/plugin/compe.lua")
source("map/plugin/nvim_tree.lua")

source("setup.lua")
