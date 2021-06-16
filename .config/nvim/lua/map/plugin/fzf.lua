openPicker = function()
  coroutine.wrap(function()
    local choice = require "fzf".fzf(vim.env.FZF_DEFAULT_COMMAND)
    if choice then
      vim.api.nvim_command(string.format("e %s", choice[1]))
    end
  end)()
end

map("n", "<C-P>", ":lua openPicker()<CR>")
