require'nvim-tree'.setup {
  update_focused_file = {
    enable = true,
    update_cwd = true,
  },
  actions = {
    change_dir = {
      enable = false,
    },
  },
  hijack_directories = {
    enable = true,
    auto_open = false,
  },
  actions = {
    use_system_clipboard = false,
    open_file = {
      quit_on_open = true,
    },
  },
  view = {
    relativenumber = true,
    signcolumn = "no",
    mappings = {
      list = {
        { key = "%", action = "create" },
        { key = "K", action = "dir_up" },
        { key = "d", action = "create" },
        { key = "D", action = "remove" },
      },
    },
  },
  live_filter = {
    always_show_folders = false,
  },
}
