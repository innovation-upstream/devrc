inoremap <expr> <Tab>   pumvisible() ? "\<C-n>" : "\<Tab>"
inoremap <expr> <S-Tab> pumvisible() ? "\<C-p>" : "\<S-Tab>"
"use <c-j> to switch to previous completion
imap <c-j> <Plug>(completion_next_source)
"use <c-k> to switch to next completion
imap <c-k> <Plug>(completion_prev_source)

