syntax on

" Convert to lua once this PR is merged
" https://github.com/neovim/neovim/pull/13479
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
set colorcolumn=80
set updatetime=50
set cmdheight=2
set re=0
set background=dark
set timeoutlen=300
set guicursor=
set hidden
set hlsearch
set scrolloff=8
set completeopt=menuone,noinsert,noselect
set shortmess+=c

highlight ColorColumn ctermbg=blue guibg=lightgrey

let loaded_matchparen = 1
let mapleader = " "

let g:netrw_browse_split = 2
let g:netrw_banner = 0
let g:netrw_winsize = 25

