return require('packer').startup(function(use)
  use 'wbthomason/packer.nvim'
  use {
    'nvim-treesitter/nvim-treesitter',
    run = ':TSUpdate'
  }
  use {'jremmen/vim-ripgrep', cmd = 'Rg'}
  use 'tweekmonster/gofmt.vim'
  use 'cappyzawa/starlark.vim'
  use 'rust-lang/rust.vim'
  use 'ibhagwan/fzf-lua'
  use 'nvim-lua/plenary.nvim'
  use 'windwp/nvim-spectre'
  use {
    'ZacxDev/qdr.nvim',
    requires = 'vijaymarupudi/nvim-fzf',
  }
  use 'MunifTanjim/nui.nvim'
  use 'tpope/vim-obsession'
  use { 'tveskag/nvim-blame-line', cmd = 'ToggleBlameLine' }
  use 'vmchale/dhall-vim'
  use 'simrat39/symbols-outline.nvim'
  use 'nvim-telescope/telescope.nvim'
  use 'github/copilot.vim'
  use 'neovim/nvim-lspconfig'
  use 'hrsh7th/cmp-nvim-lsp'
  use 'hrsh7th/cmp-buffer'
  use 'hrsh7th/cmp-path'
  use 'hrsh7th/cmp-cmdline'
  use 'hrsh7th/nvim-cmp'
end)

