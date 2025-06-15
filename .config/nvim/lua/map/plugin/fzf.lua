openPicker = function(cmd)
  coroutine.wrap(function()
    local choice = require "fzf".fzf(cmd)
    if choice then
      vim.api.nvim_command(string.format("e %s", choice[1]))
    end
  end)()
end

map("n", "<C-P>", ":lua openPicker(vim.env.FZF_DEFAULT_COMMAND)<CR>")
map("n", "<M-p>", ":lua openPicker(vim.env.FZF_INCLUDE_HIDDEN_COMMAND)<CR>")

openBufferPicker = function()
  coroutine.wrap(function()
    -- Get list of open buffers with paths
    local buffers = {}
    for _, buf in ipairs(vim.api.nvim_list_bufs()) do
      if vim.api.nvim_buf_is_loaded(buf) then
        local name = vim.api.nvim_buf_get_name(buf)
        if name ~= "" then
          table.insert(buffers, name)
        end
      end
    end

    -- Run fzf
    local choice = require "fzf".fzf(buffers)
    if choice then
      vim.api.nvim_command(string.format("e %s", choice[1]))
    end
  end)()
end

map("n", "<C-b>", ":lua openBufferPicker()<CR>")

