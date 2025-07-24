vim.g.lazygit_floating_window_scaling_factor = 1
vim.g.lazygit_use_custom_config_file_path = 1
local devrcDir = os.getenv("DEVRC_DIR")
local path = string.format("%s/.config/lazygit/config.yml", devrcDir)

vim.g.lazygit_config_file_path = path

