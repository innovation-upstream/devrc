vim.cmd([[
  autocmd BufReadPost * wincmd =
]])

-- Custom command to print absolute path of current file
vim.api.nvim_create_user_command('Abs', function()
  local abs_path = vim.fn.expand('%:p')
  print(abs_path)
end, {})

-- Custom command to print absolute path and copy to system clipboard
vim.api.nvim_create_user_command('Absc', function()
  local abs_path = vim.fn.expand('%:p')
  vim.fn.setreg('+', abs_path)
  print(abs_path)
end, {})
