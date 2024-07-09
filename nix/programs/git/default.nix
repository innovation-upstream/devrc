{}:
{
  enable = true;
  lfs.enable = true;
  aliases = {
    co = "checkout";
  };
  extraConfig = {
    init = {
      defaultBranch = "trunk";
    };
  };
}
