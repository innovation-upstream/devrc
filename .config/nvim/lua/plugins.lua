return require('packer').startup(function(use)
  use {'jremmen/vim-ripgrep', cmd = 'Rg'}
  use {'nvim-treesitter/nvim-treesitter', run = ':TSUpdate'}
  use 'tweekmonster/gofmt.vim'
  use 'cappyzawa/starlark.vim'
  --use {
    --'nvim-telescope/telescope.nvim',
    --requires = {{'nvim-lua/popup.nvim'}, {'nvim-lua/plenary.nvim'}}
  --}
  --use 'vijaymarupudi/nvim-fzf'
end)

