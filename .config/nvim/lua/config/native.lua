vim.cmd([[
  autocmd BufReadPost * wincmd =
]])

-- Clipboard: fall back to OSC 52 when there is no X display.
--
-- With DISPLAY set, neovim finds xclip on its own and `"+y` works with no
-- config. Without it -- an ssh session, or a bare TTY -- neovim 0.12 does
-- NOT auto-enable OSC 52, and `"+y` fails outright with
-- `clipboard: No provider`. That silently breaks :Absc below too.
--
-- tmux already emits OSC 52 for its own copy-mode yanks (see .tmux.conf);
-- this closes the same gap for neovim's registers.
--
-- Paste is served from a local cache rather than read back off the
-- terminal. vim.ui.clipboard.osc52.paste waits up to 10s for the terminal
-- to answer an OSC 52 query, and alacritty is deliberately configured
-- `osc52 = "OnlyCopy"` (nix/programs/alacritty) so it never will -- wiring
-- its paste straight through would hang for 10s on every `"+p`.
if vim.env.DISPLAY == nil or vim.env.DISPLAY == '' then
  local osc52 = require('vim.ui.clipboard.osc52')

  -- Seeded empty so a paste before any yank returns empty rather than nil.
  local cache = { ['+'] = { { '' }, 'v' }, ['*'] = { { '' }, 'v' } }

  local function copy(reg)
    local emit = osc52.copy(reg)
    return function(lines, regtype)
      cache[reg] = { lines, regtype or 'v' }
      emit(lines)
    end
  end

  local function paste(reg)
    return function()
      return cache[reg]
    end
  end

  vim.g.clipboard = {
    name = 'OSC 52',
    copy = { ['+'] = copy('+'), ['*'] = copy('*') },
    paste = { ['+'] = paste('+'), ['*'] = paste('*') },
  }
end

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
