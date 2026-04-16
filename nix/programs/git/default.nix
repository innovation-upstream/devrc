{}:
{
  enable = true;
  lfs.enable = true;
  settings = {
    url."git@github.com:".insteadOf = "https://github.com/";
    init.defaultBranch = "trunk";
    pull.rebase = true;
    push.autoSetupRemote = true;
    rerere.enabled = true;
    merge.conflictstyle = "zdiff3";
    diff.algorithm = "histogram";
    rebase.autoStash = true;
  };
}
