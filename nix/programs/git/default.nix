{}:
{
  enable = true;
  lfs.enable = true;
  aliases = {
    co = "checkout";
  };
  extraConfig = {
    url."git@github.com:".insteadOf = "https://github.com/";
    init = {
      defaultBranch = "trunk";
    };
  };
}
