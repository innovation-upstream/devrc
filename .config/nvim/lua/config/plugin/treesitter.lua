-- Init treesitter
require'nvim-treesitter.configs'.setup {
  ensure_installed = { "go", "graphql", "json", "bash", "typescript", "lua" },
  highlight = {
    enable = true,
  },
  indent = {
    enable = true
  }
}


