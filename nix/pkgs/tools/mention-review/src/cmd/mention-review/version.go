package main

// 🔴 THIS LINE IS THE SINGLE SOURCE OF TRUTH FOR THE VERSION, AND
// `nix/pkgs/tools/mention-review/default.nix` READS IT OUT OF THIS FILE.
//
// It is never spelled in the Nix expression. `clawgatectl.nix` exists in its
// current form because a hand-maintained `version = "x.y.z"` literal there was
// a claim about code somewhere else, and on 2026-08-14 it stamped `0.7.95` onto
// a binary built from `0.7.87` source — producing a CLI that printed help and
// exited 0 for a subcommand it did not have. Both halves live in this repo
// here, so the drift window is smaller, but the mechanism is free and the
// failure mode is identical.
//
// 🔴 THE NIX SIDE MATCHES THIS EXACT SHAPE AND NOTHING ELSE:
//
//	var buildVersion = "([^"]+)".*
//
// EXACTLY ONE matching line in this file, or the package is not built at all
// (`available = false`). Zero matches means the declaration was renamed or
// reformatted; two or more means the pattern became ambiguous and picking
// either would be a guess presented as a fact. Do not add a second `var
// buildVersion` anywhere in this file, do not reformat this declaration across
// two lines, and 🔴 do not give the Nix side a fallback literal — a package
// that cannot state truthfully what it is building must not be installed.
//
// Failing the *switch* is the worse outcome and is deliberately not what
// happens: ship.sh reports a failed switch as a SKIPPED host, which this
// repo's CLAUDE.md documents as silently stopping all future delivery to that
// machine. `mention-review: command not found` is the loud failure instead.
var buildVersion = "0.3.0"
