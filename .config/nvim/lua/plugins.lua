return require('packer').startup(function(use)
  use {'jremmen/vim-ripgrep', cmd = 'Rg'}
  use {'nvim-treesitter/nvim-treesitter', run = ':TSUpdate'}
  use 'tweekmonster/gofmt.vim'
  use 'cappyzawa/starlark.vim'
  use 'rust-lang/rust.vim'
  use {
    'kyazdani42/nvim-tree.lua',
    tag = 'nightly'
  }
  use 'ibhagwan/fzf-lua'
  use 'vijaymarupudi/nvim-fzf'
  use 'nvim-lua/plenary.nvim'
  use 'windwp/nvim-spectre'
  use 'ZacxDev/qdr.nvim'
  use 'MunifTanjim/nui.nvim'
end)

