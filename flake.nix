{
  description = "DEVRC - personal development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { nixpkgs, home-manager, ... }: {
    homeConfigurations."zach" = home-manager.lib.homeManagerConfiguration {
      pkgs = nixpkgs.legacyPackages.x86_64-linux;
      extraSpecialArgs = { isNixOS = true; };
      modules = [
        ./nix/home.nix
        {
          home.username = "zach";
          home.homeDirectory = "/home/zach";
        }
      ];
    };
  };
}
