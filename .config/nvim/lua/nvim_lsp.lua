local nvim_lsp = require('lspconfig')

local on_attach = function(client, bufnr)
  local function buf_set_keymap(...) vim.api.nvim_buf_set_keymap(bufnr, ...) end
  local function buf_set_option(...) vim.api.nvim_buf_set_option(bufnr, ...) end

  buf_set_option('omnifunc', 'v:lua.vim.lsp.omnifunc')

  -- Mappings.
  local opts = { noremap=true, silent=true }
  buf_set_keymap('n', 'gD', '<Cmd>lua vim.lsp.buf.declaration()<CR>', opts)
  buf_set_keymap('n', 'gd', '<Cmd>lua vim.lsp.buf.definition()<CR>', opts)
  buf_set_keymap('n', 'K', '<Cmd>lua vim.lsp.buf.hover()<CR>', opts)
  buf_set_keymap('n', 'gi', '<cmd>lua vim.lsp.buf.implementation()<CR>', opts)
  buf_set_keymap('n', '<C-s>', '<cmd>lua vim.lsp.buf.signature_help()<CR>', opts)
  buf_set_keymap('n', '<space>wa', '<cmd>lua vim.lsp.buf.add_workspace_folder()<CR>', opts)
  buf_set_keymap('n', '<space>wr', '<cmd>lua vim.lsp.buf.remove_workspace_folder()<CR>', opts)
  buf_set_keymap('n', '<space>wl', '<cmd>lua print(vim.inspect(vim.lsp.buf.list_workspace_folders()))<CR>', opts)
  buf_set_keymap('n', '<space>D', '<cmd>lua vim.lsp.buf.type_definition()<CR>', opts)
  buf_set_keymap('n', '<space>rr', '<cmd>lua vim.lsp.buf.rename()<CR>', opts)
  buf_set_keymap('n', 'gr', '<cmd>lua vim.lsp.buf.references()<CR>', opts)
  buf_set_keymap('n', '<space>e', '<cmd>lua vim.diagnostic.open_float()<CR>', opts)
  buf_set_keymap('n', 'gp', '<cmd>lua vim.diagnostic.goto_prev({float=true})<CR>', opts)
  buf_set_keymap('n', 'gn', '<cmd>lua vim.diagnostic.goto_next({float=true})<CR>', opts)
  buf_set_keymap('n', 'gl', '<cmd>lua vim.diagnostic.setloclist()<CR>', opts)
  buf_set_keymap('n', 'do', '<cmd>lua vim.lsp.buf.code_action()<CR>', opts)
  buf_set_keymap('n', 'dc', 'i' .. client.name, opts)

  -- Set some keybinds conditional on server capabilities
  if client.server_capabilities.documentFormattingProvider then
    buf_set_keymap("n", "<space>f", "<cmd>lua vim.lsp.buf.format { async = true }<CR>", opts)
  elseif client.server_capabilities.documentRangeFormattingProvider then
    buf_set_keymap("n", "<space>f", "<cmd>lua vim.lsp.buf.format { async = true }<CR>", opts)
  end

  -- Set autocommands conditional on server_capabilities
  if client.server_capabilities.documentHighlight then
    vim.api.nvim_exec([[
      hi LspReferenceRead cterm=bold ctermbg=blue guibg=LightYellow
      hi LspReferenceText cterm=bold ctermbg=blue guibg=LightYellow
      hi LspReferenceWrite cterm=bold ctermbg=blue guibg=LightYellow
      augroup lsp_document_highlight
        autocmd! * <buffer>
        autocmd CursorHold <buffer> lua vim.lsp.buf.document_highlight()
        autocmd CursorMoved <buffer> lua vim.lsp.buf.clear_references()
      augroup END
    ]], false)
  end
end

nvim_lsp.ts_ls.setup {
  on_attach = on_attach,
}

nvim_lsp.eslint.setup {
  on_attach = function(client, bufnr)
    vim.api.nvim_create_autocmd("BufWritePre", {
      buffer = bufnr,
      command = "EslintFixAll",
    })
    on_attach(client, bufnr)
  end
}

nvim_lsp.gopls.setup {
  on_attach = function(client, bufnr)
    vim.api.nvim_create_autocmd("BufWritePre", {
      buffer = bufnr,
      command = "lua vim.lsp.buf.format{}",
    })
    on_attach(client, bufnr)
  end,
  root_dir = nvim_lsp.util.root_pattern('go.mod')
}

-- The Graphql LSP we get from nixpkgs doesn't seem to work
--nvim_lsp.graphql.setup{
  --on_attach = on_attach,
--}

local capabilities = vim.lsp.protocol.make_client_capabilities()
capabilities.textDocument.completion.completionItem.snippetSupport = true

nvim_lsp.cssls.setup {
  capabilities = capabilities,
  cmd = { "css-languageserver", "--stdio" },
  filetypes = { "css", "scss", "less" },
  root_dir = nvim_lsp.util.root_pattern("package.json"),
  on_attach = on_attach,
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
}

nvim_lsp.rust_analyzer.setup{
  on_attach = on_attach,
}

nvim_lsp.bashls.setup{
  on_attach = on_attach,
}

nvim_lsp.perlls.setup{
  on_attach = on_attach,
}

--nvim_lsp.dhall_lsp_server.setup{
  --on_attach = on_attach,
--}

nvim_lsp.solidity_ls.setup{
  on_attach = on_attach,
}

nvim_lsp.tailwindcss.setup{
  on_attach = on_attach,
}

nvim_lsp.volar.setup{
  on_attach = on_attach,
  --filetypes = {'typescript', 'javascript', 'javascriptreact', 'typescriptreact', 'vue', 'json'}
  filetypes = {'vue'}
}

nvim_lsp.cucumber_language_server.setup{
  on_attach = on_attach,
}

nvim_lsp.pyright.setup{
  on_attach = on_attach,
}

nvim_lsp.starpls.setup{
  on_attach = on_attach,
}

-- This is set in sessionVariables.nix
local elixirlspPath = os.getenv("ELIXIR_LSP_PATH")
nvim_lsp.elixirls.setup{
  on_attach = on_attach,
  cmd = { elixirlspPath },
}

nvim_lsp.html.setup {
  on_attach = on_attach,
  capabilities = capabilities,
  filetypes = { "html", "templ", "typescriptreact", "javascriptreact" },
  init_options =  {
    embeddedLanguages = {
      css = true,
      javascript = true
    },
    provideFormatter = true -- this was false before, idk why
  },
}

nvim_lsp.nixd.setup {
  on_attach = on_attach,
}

nvim_lsp.yamlls.setup {
  on_attach = on_attach,
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
}

--[[
nvim_lsp.sumneko_lua.setup{
  on_attach = on_attach,
  settings = {
   Lua = {
      runtime = {
        -- Tell the language server which version of Lua you're using (most likely LuaJIT in the case of Neovim)
        version = 'LuaJIT',
      },
      diagnostics = {
        -- Get the language server to recognize the `vim` global
        globals = {'vim'},
      },
      workspace = {
        -- Make the server aware of Neovim runtime files
        library = vim.api.nvim_get_runtime_file("", true),
      },
      -- Do not send telemetry data containing a randomized but unique identifier
      telemetry = {
        enable = false,
      },
    },
  },
}
]]--

