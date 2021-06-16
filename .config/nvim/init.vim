" Set native vim options (must run first so leader mapping use the correct
" leader key
source $DEVRC_DIR/.config/nvim/config/native.vim

luafile $DEVRC_DIR/.config/nvim/init.lua

source $DEVRC_DIR/.config/nvim/config/plugin/ale.vim
source $DEVRC_DIR/.config/nvim/config/plugin/vim_go.vim
source $DEVRC_DIR/.config/nvim/config/plugin/gruvbox.vim
source $DEVRC_DIR/.config/nvim/config/plugin/rg.vim
source $DEVRC_DIR/.config/nvim/config/plugin/incsearch.vim
"source $DEVRC_DIR/.config/nvim/config/plugin/nvim_lsp.vim
source $DEVRC_DIR/.config/nvim/config/plugin/completion_nvim.vim
"source $DEVRC_DIR/.config/nvim/map/plugin/completion_nvim.vim
source $DEVRC_DIR/.config/nvim/map/plugin/undotree.vim
source $DEVRC_DIR/.config/nvim/map/plugin/fugitive.vim
source $DEVRC_DIR/.config/nvim/map/plugin/incsearch.vim
source $DEVRC_DIR/.config/nvim/map/plugin/signify.vim

