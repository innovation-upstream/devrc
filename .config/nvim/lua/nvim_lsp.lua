-- LSP attach autocmd for keymaps and capabilities
vim.api.nvim_create_autocmd('LspAttach', {
  callback = function(args)
    local client = vim.lsp.get_client_by_id(args.data.client_id)
    local bufnr = args.buf

    -- Set omnifunc
    vim.bo[bufnr].omnifunc = 'v:lua.vim.lsp.omnifunc'

    -- Mappings
    local opts = { noremap = true, silent = true, buffer = bufnr }
    vim.keymap.set('n', 'gD', vim.lsp.buf.declaration, opts)
    vim.keymap.set('n', 'gd', vim.lsp.buf.definition, opts)
    vim.keymap.set('n', 'K', vim.lsp.buf.hover, opts)
    vim.keymap.set('n', 'gi', vim.lsp.buf.implementation, opts)
    vim.keymap.set('n', '<C-s>', vim.lsp.buf.signature_help, opts)
    vim.keymap.set('n', '<space>wa', vim.lsp.buf.add_workspace_folder, opts)
    vim.keymap.set('n', '<space>wr', vim.lsp.buf.remove_workspace_folder, opts)
    vim.keymap.set('n', '<space>wl', function()
      print(vim.inspect(vim.lsp.buf.list_workspace_folders()))
    end, opts)
    vim.keymap.set('n', '<space>D', vim.lsp.buf.type_definition, opts)
    vim.keymap.set('n', '<space>rr', vim.lsp.buf.rename, opts)
    vim.keymap.set('n', 'gr', vim.lsp.buf.references, opts)
    vim.keymap.set('n', '<space>e', vim.diagnostic.open_float, opts)
    vim.keymap.set('n', 'gp', function()
      vim.diagnostic.goto_prev({ float = true })
    end, opts)
    vim.keymap.set('n', 'gn', function()
      vim.diagnostic.goto_next({ float = true })
    end, opts)
    vim.keymap.set('n', 'gl', vim.diagnostic.setloclist, opts)
    vim.keymap.set('n', 'do', vim.lsp.buf.code_action, opts)
    vim.keymap.set('n', 'dc', 'i' .. client.name, opts)

    -- Set formatting keymap conditional on server capabilities
    if client.supports_method('textDocument/formatting') then
      vim.keymap.set('n', '<space>f', function()
        vim.lsp.buf.format({ async = true })
      end, opts)
    end

    -- Set autocommands conditional on server capabilities
    if client.supports_method('textDocument/documentHighlight') then
      vim.api.nvim_set_hl(0, 'LspReferenceRead', { cterm = { bold = true }, ctermbg = 'blue', bg = 'LightYellow' })
      vim.api.nvim_set_hl(0, 'LspReferenceText', { cterm = { bold = true }, ctermbg = 'blue', bg = 'LightYellow' })
      vim.api.nvim_set_hl(0, 'LspReferenceWrite', { cterm = { bold = true }, ctermbg = 'blue', bg = 'LightYellow' })

      local group = vim.api.nvim_create_augroup('lsp_document_highlight', { clear = false })
      vim.api.nvim_clear_autocmds({ group = group, buffer = bufnr })
      vim.api.nvim_create_autocmd('CursorHold', {
        group = group,
        buffer = bufnr,
        callback = vim.lsp.buf.document_highlight,
      })
      vim.api.nvim_create_autocmd('CursorMoved', {
        group = group,
        buffer = bufnr,
        callback = vim.lsp.buf.clear_references,
      })
    end

    -- ESLint auto-fix on save
    if client.name == 'eslint' then
      vim.api.nvim_create_autocmd("BufWritePre", {
        buffer = bufnr,
        command = "EslintFixAll",
      })
    end

    -- Go format on save
    if client.name == 'gopls' then
      vim.api.nvim_create_autocmd("BufWritePre", {
        buffer = bufnr,
        callback = function()
          vim.lsp.buf.format({})
        end,
      })
    end
  end,
})

-- TypeScript/JavaScript
vim.lsp.config('ts_ls', {
  root_markers = { 'package.json', 'tsconfig.json', 'jsconfig.json', '.git' },
})

-- ESLint
vim.lsp.config('eslint', {
  root_markers = { '.eslintrc', '.eslintrc.js', '.eslintrc.json', 'package.json', '.git' },
})

-- Go
vim.lsp.config('gopls', {
  root_markers = { 'go.mod', '.git' },
})

-- CSS
vim.lsp.config('cssls', {
  cmd = { 'css-languageserver', '--stdio' },
  filetypes = { 'css', 'scss', 'less' },
  root_markers = { 'package.json', '.git' },
  settings = {
    css = {
      validate = true
    },
    less = {
      validate = true
    },
    scss = {
      validate = true
    }
  },
})

-- Rust
vim.lsp.config('rust_analyzer', {
  root_markers = { 'Cargo.toml', '.git' },
})

-- Bash
vim.lsp.config('bashls', {
  root_markers = { '.git' },
})

-- Perl
vim.lsp.config('perlls', {
  root_markers = { '.git' },
})

-- Solidity
vim.lsp.config('solidity_ls', {
  root_markers = { '.git' },
})

-- Tailwind CSS
vim.lsp.config('tailwindcss', {
  root_markers = { 'tailwind.config.js', 'tailwind.config.ts', 'package.json', '.git' },
})

-- Cucumber
vim.lsp.config('cucumber_language_server', {
  root_markers = { '.git' },
})

-- Python
vim.lsp.config('pyright', {
  root_markers = { 'pyproject.toml', 'setup.py', 'setup.cfg', 'requirements.txt', 'Pipfile', '.git' },
})

-- Starlark
vim.lsp.config('starpls', {
  root_markers = { '.git' },
})

-- Elixir
local elixirlspPath = os.getenv("ELIXIR_LSP_PATH")
if elixirlspPath then
  vim.lsp.config('elixirls', {
    cmd = { elixirlspPath },
    root_markers = { 'mix.exs', '.git' },
  })
end

-- HTML
vim.lsp.config('html', {
  filetypes = { 'html', 'templ', 'typescriptreact', 'javascriptreact' },
  root_markers = { 'package.json', '.git' },
  init_options = {
    embeddedLanguages = {
      css = true,
      javascript = true
    },
    provideFormatter = true
  },
})

-- Nix
vim.lsp.config('nixd', {
  root_markers = { 'flake.nix', 'default.nix', '.git' },
})

-- YAML
vim.lsp.config('yamlls', {
  root_markers = { '.git' },
  settings = {
    yaml = {
      schemas = {
        ["https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/v1.33.3-standalone/all.json"] = "/*.yaml",
        ["http://json.schemastore.org/kustomization"] = "kustomization.yaml",
      },
      validate = true,
      completion = true,
      hover = true
    }
  }
})

-- Enable all configured LSP servers
local servers = {
  'ts_ls',
  'eslint',
  'gopls',
  'cssls',
  'rust_analyzer',
  'bashls',
  'perlls',
  'solidity_ls',
  'tailwindcss',
  'cucumber_language_server',
  'pyright',
  'starpls',
  'html',
  'nixd',
  'yamlls',
  'csharp_ls',
}

if elixirlspPath then
  table.insert(servers, 'elixirls')
end

for _, server in ipairs(servers) do
  vim.lsp.enable(server)
end

