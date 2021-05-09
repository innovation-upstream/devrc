" Configure ale (this must run before ale is loaded)
let g:ale_fix_on_save = 1
let g:ale_fixers = {'javascriptreact': ['eslint'], 'typescriptreact': ['eslint'], 'typescript': ['eslint']}
let g:ale_linters = {'javascriptreact': ['eslint'], 'typescriptreact': ['eslint']}
let g:ale_completion_enabled = 0
let g:ale_completion_autoimport = 1
nmap <silent> <C-k> <Plug>(ale_previous)
nmap <silent> <C-j> <Plug>(ale_next)
let g:ale_javascript_eslint_executable = 'eslint_d --cache'

