vim.api.nvim_command("set termguicolors")
vim.api.nvim_command("highlight NvimTreeFolderIcon guibg=blue")
vim.api.nvim_command("let g:nvim_tree_show_icons = { 'git': 1, 'folder': 0, 'files': 0, 'folder_arrow': 0, }")
vim.api.nvim_command("let g:nvim_tree_root_folder_modifier = ':t'")
