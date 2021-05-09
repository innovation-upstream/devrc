nnoremap <leader>h :wincmd h<CR>
nnoremap <leader>j :wincmd j<CR>
nnoremap <leader>k :wincmd k<CR>
nnoremap <leader>l :wincmd l<CR>
nnoremap <leader>pv :wincmd v<bar> :Ex <bar> :vertical resize 30<CR>
nnoremap <silent> <space>d :<C-u>CocList diagnostics<cr>
nnoremap <Leader>ee oif err != nil {<CR>return errors.WithStack(err)<CR>}<CR><esc>kkI<esc>
nnoremap <silent> <Leader>. :Files <C-r>=expand("%:h")<CR>/<CR>
nmap <leader>L $

