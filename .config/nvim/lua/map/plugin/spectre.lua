map("n", "<leader>S", "<cmd>lua require('spectre').open()<CR>")
map("n", "<leader>sw", "<cmd>lua require('spectre').open_visual({select_word=true})<CR>")
map("v", "<leader>s", "<cmd>lua require('spectre').open_visual()<CR>")
map("n", "<leader>sp", "viw:lua require('spectre').open_file_search()<cr>")
