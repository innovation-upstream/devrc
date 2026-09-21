package main

// 🔴 THIS LINE IS THE SINGLE SOURCE OF TRUTH FOR THE VERSION, AND
// `nix/pkgs/tools/stt-voice/default.nix` READS IT OUT OF THIS FILE.
//
// Carried over from mention-review (and originally clawgatectl): a
// hand-maintained `version = "x.y.z"` literal in the Nix expression was a
// claim about code somewhere else, and on 2026-08-14 clawgatectl stamped
// `0.7.95` onto a binary built from `0.7.87` source. The mechanism is free
// and the failure mode is identical here, so it is kept.
//
// 🔴 THE NIX SIDE MATCHES THIS EXACT SHAPE AND NOTHING ELSE:
//
//	var buildVersion = "([^"]+)".*
//
// EXACTLY ONE matching line in this file, or the package is not built at all
// (`available = false`). Zero matches means the declaration was renamed or
// reformatted; two or more means the pattern became ambiguous and picking
// either would be a guess presented as a fact. Do not add a second `var
// buildVersion` anywhere in this file, do not reformat this declaration
// across two lines, and 🔴 do not give the Nix side a fallback literal — a
// package that cannot state truthfully what it is building must not be
// installed.
//
// Failing the *switch* is the worse outcome and is deliberately not what
// happens: ship.sh reports a failed switch as a SKIPPED host, which this
// repo's CLAUDE.md documents as silently stopping all future delivery to that
// machine. `stt-voice: command not found` is the loud failure instead.
var buildVersion = "0.1.0"
