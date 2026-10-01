// 🔴 THE MODULE PATH IS NOT A FETCHABLE URL, AND THAT IS DELIBERATE.
//
// The source lives at `nix/pkgs/tools/oc-sent-tui/src/` inside this repo, so
// the honest import path would be
// `github.com/innovation-upstream/devrc/nix/pkgs/tools/oc-sent-tui/src`,
// which every import statement would then carry. Nothing ever `go get`s this —
// it is built by `default.nix` from a local path, exactly as mention-review
// and stt-voice are — so the shorter path is chosen for readability and
// recorded here rather than left to be rediscovered.
module github.com/innovation-upstream/devrc/oc-sent-tui

// Floor matches nixpkgs' Go and Bubble Tea v2's own `go 1.25.0` requirement —
// the same line its two sibling modules carry.
go 1.25.0

require (
	charm.land/bubbletea/v2 v2.0.9
	charm.land/lipgloss/v2 v2.0.6
)

require (
	github.com/charmbracelet/colorprofile v0.4.3 // indirect
	github.com/charmbracelet/ultraviolet v0.0.0-20260811164956-006e29f97886 // indirect
	github.com/charmbracelet/x/ansi v0.11.8 // indirect
	github.com/charmbracelet/x/term v0.2.2 // indirect
	github.com/charmbracelet/x/termios v0.1.1 // indirect
	github.com/charmbracelet/x/windows v0.2.2 // indirect
	github.com/clipperhouse/displaywidth v0.11.0 // indirect
	github.com/clipperhouse/uax29/v2 v2.7.0 // indirect
	github.com/lucasb-eyer/go-colorful v1.4.1 // indirect
	github.com/mattn/go-runewidth v0.0.27 // indirect
	github.com/muesli/cancelreader v0.2.2 // indirect
	github.com/rivo/uniseg v0.4.7 // indirect
	github.com/xo/terminfo v0.0.0-20220910002029-abceb7e1c41e // indirect
	golang.org/x/sync v0.22.0 // indirect
	golang.org/x/sys v0.47.0 // indirect
)
