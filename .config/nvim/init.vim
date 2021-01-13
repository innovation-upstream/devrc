syntax on

set noerrorbells
set tabstop=2 softtabstop=2
set shiftwidth=2
set expandtab
set smartindent
set nu
set relativenumber
set smartcase
set noswapfile
set nobackup
set undodir=~/.vim/undodir
set undofile
set incsearch
set colorcolumn=100
set updatetime=50
set cmdheight=2
set re=0
set background=dark
set timeoutlen=300
set guicursor=
set hidden
set hlsearch

highlight ColorColumn ctermbg=blue guibg=lightgrey

call plug#begin(stdpath('config') . '/plugged')
Plug 'jremmen/vim-ripgrep'
Plug 'tpope/vim-fugitive'
Plug 'mbbill/undotree'
Plug 'sheerun/vim-polyglot'
Plug 'junegunn/fzf', { 'do': { -> fzf#install() } }
Plug 'junegunn/fzf.vim'
Plug 'neoclide/coc.nvim', {'branch': 'release'}
Plug 'gruvbox-community/gruvbox'
Plug 'sainnhe/gruvbox-material'
Plug 'tweekmonster/gofmt.vim'
Plug 'haya14busa/incsearch.vim'
Plug 'mhinz/vim-signify'
Plug 'cappyzawa/starlark.vim'
call plug#end()

let g:gofmt_exe = 'goimports'
let g:gruvbox_contrast_dark = 'hard'
if exists('+termguicolors')
    let &t_8f = "\<Esc>[38;2;%lu;%lu;%lum"
    let &t_8b = "\<Esc>[48;2;%lu;%lu;%lum"
endif
let g:gruvbox_invert_selection='0'

let g:go_highlight_build_constraints = 1
let g:go_highlight_extra_types = 1
let g:go_highlight_fields = 1
let g:go_highlight_functions = 1
let g:go_highlight_methods = 1
let g:go_highlight_operators = 1
let g:go_highlight_structs = 1
let g:go_highlight_types = 1
let g:go_highlight_function_parameters = 1
let g:go_highlight_function_calls = 1
let g:go_highlight_generate_tags = 1
let g:go_highlight_format_strings = 1
let g:go_highlight_variable_declarations = 1
let g:go_auto_sameids = 1
let g:coc_global_extensions = ['coc-json', 'coc-git', 'coc-go', 'coc-tsserver', 'coc-eslint']

colorscheme gruvbox

if executable('rg')
    let g:rg_derive_root='true'
endif

let loaded_matchparen = 1
let mapleader = " "

let g:netrw_browse_split = 2
let g:netrw_banner = 0
let g:netrw_winsize = 25
let g:fzf_layout = { 'down': '20%' }
let g:incsearch#auto_nohlsearch = 1

nnoremap <nowait> <C-p> :FZF<CR>
nnoremap <leader>prw :CocSearch <C-R>=expand("<cword>")<CR><CR>
nnoremap <leader>h :wincmd h<CR>
nnoremap <leader>j :wincmd j<CR>
nnoremap <leader>k :wincmd k<CR>
nnoremap <leader>l :wincmd l<CR>
nnoremap <leader>u :UndotreeShow<CR>
nnoremap <leader>pv :wincmd v<bar> :Ex <bar> :vertical resize 30<CR>
nnoremap <silent> <space>d :<C-u>CocList diagnostics<cr>
nnoremap <silent> K :call <SID>show_documentation()<CR>
nnoremap <Leader>ee oif err != nil {<CR>return err<CR>}<CR><esc>kkI<esc>
nnoremap <silent> <Leader>. :Files <C-r>=expand("%:h")<CR>/<CR>
nmap <leader>gd <Plug>(coc-definition)
nmap <leader>gy <Plug>(coc-type-definition)
nmap <leader>gi <Plug>(coc-implementation)
nmap <leader>gr <Plug>(coc-references)
nmap <leader>rr <Plug>(coc-rename)
nmap <leader>do <Plug>(coc-codeaction)
nmap <leader>L $
nmap <leader>g[ <Plug>(coc-diagnostic-prev)
nmap <leader>g] <Plug>(coc-diagnostic-next)
nmap <silent> <leader>gp <Plug>(coc-diagnostic-prev-error)
nmap <silent> <leader>gn <Plug>(coc-diagnostic-next-error)
map <leader>es :CocCommand eslint.executeAutofix<cr>

nmap <leader>gj :diffget //3<CR>
nmap <leader>gf :diffget //2<CR>
nmap <leader>gs :G<CR>

map /  <Plug>(incsearch-forward)
map ?  <Plug>(incsearch-backward)
map g/ <Plug>(incsearch-stay)

nmap <leader>gj <plug>(signify-next-hunk)
nmap <leader>gk <plug>(signify-prev-hunk)
nmap <leader>gJ 9999<leader>gj
nmap <leader>gK 9999<leader>gk
nmap <leader>gh :Glog! -- % <bar> :wincmd j<CR>

function! s:show_documentation()
  if (index(['vim','help'], &filetype) >= 0)
    execute 'h '.expand('<cword>')
  else
    call CocAction('doHover')
  endif
endfunction
