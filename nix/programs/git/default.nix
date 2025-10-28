{}:
{
  enable = true;
  lfs.enable = true;
  settings = {
    url."git@github.com:".insteadOf = "https://github.com/";
    init = {
      defaultBranch = "trunk";
    };
  };
}
