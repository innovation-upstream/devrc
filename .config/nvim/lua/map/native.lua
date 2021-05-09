map('n', '<leader>h', '<cmd>wincmd h<CR>')
map('n', '<leader>j', '<cmd>wincmd j<CR>')
map('n', '<leader>k', '<cmd>wincmd k<CR>')
map('n', '<leader>l', '<cmd>wincmd l<CR>')
map('n', '<leader>pv', ':wincmd v<bar> :Explore<bar> :vertical resize 30<CR>')
map('n', '<leader>ee', 'oif err != nil {<CR>return errors.WithStack(err)<CR>}<CR><esc>kk_')
map('n', '<leader>.', ':Files <C-r>=expand("%:h")<CR>/<CR>')

