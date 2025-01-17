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
