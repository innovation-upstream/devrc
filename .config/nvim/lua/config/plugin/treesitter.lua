-- Init treesitter
require'nvim-treesitter.configs'.setup {
  ensure_installed = { "go", "graphql", "json", "bash", "typescript", "lua", "javascript", "markdown", "regex", "tsx", "yaml", "nix", "json", "html" },
  parser_install_dir = "$HOME/.treesitter-parsers",
  highlight = {
    enable = true,
  },
  indent = {
    enable = true
  }
}

vim.opt.runtimepath:append("$HOME/.treesitter-parsers")

