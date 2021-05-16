return require('packer').startup(function(use)
  use 'wbthomason/packer.nvim'
  use {'jremmen/vim-ripgrep', cmd = 'Rg'}
  use {'nvim-treesitter/nvim-treesitter', run = ':TSUpdate'}
  use 'tpope/vim-fugitive'
  use 'mbbill/undotree'
  use 'gruvbox-community/gruvbox'
  use 'sainnhe/gruvbox-material'
  use 'tweekmonster/gofmt.vim'
  use 'haya14busa/incsearch.vim'
  use 'mhinz/vim-signify'
  use 'cappyzawa/starlark.vim'
  use 'neovim/nvim-lspconfig'
  use 'nvim-lua/completion-nvim'
  use 'steelsojka/completion-buffers'
  use 'dense-analysis/ale'
  use 'nvim-treesitter/completion-treesitter'
  use {
    'nvim-telescope/telescope.nvim',
    requires = {{'nvim-lua/popup.nvim'}, {'nvim-lua/plenary.nvim'}}
  }
  use 'alexaandru/nvim-lspupdate'
end)

