-- Init treesitter
-- If you get a _ts_parse_query error, see:
-- https://github.com/nvim-treesitter/nvim-treesitter/issues/3054#issuecomment-1166347997
vim.opt.runtimepath:append("$HOME/.treesitter-parsers")

require'nvim-treesitter.configs'.setup {
  ensure_installed = { "go", "graphql", "json", "bash", "typescript", "lua", "javascript", "markdown", "regex", "tsx", "yaml", "nix", "json", "html", "rust", "solidity", "vue", "elixir" },
  parser_install_dir = "$HOME/.treesitter-parsers",
  highlight = {
    enable = true,
  },
  indent = {
    enable = true
  },
}

