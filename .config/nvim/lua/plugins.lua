return require('packer').startup(function(use)
  use {'nvim-treesitter/nvim-treesitter', run = ':TSUpdate'}
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
end)

