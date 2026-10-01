package ui

import (
	"image/color"

	"charm.land/lipgloss/v2"
)

// Gruvbox Dark, matching the Alacritty palette in
// `nix/programs/alacritty/default.nix` — the same mechanism, for the same
// reason, as mention-review's internal/ui/theme.go: the popup must not look
// like a different application from the terminal it opened out of.
//
// 🔴 THE HEX VALUES ARE A LEDGER, AND theme_test.go PINS THEM TWO-WAY
// AGAINST THAT NIX FILE. Nothing below spells a hex literal: the ledger is
// not documentation of the palette — it IS the palette, so the test that
// pins it pins what is actually rendered.
//
// ⚠ IN LIPGLOSS V2 `lipgloss.Color` IS A FUNCTION RETURNING A STDLIB
// `color.Color`, NOT A STRING TYPE — v1 tutorial spellings do not compile.
var PaletteHex = map[string]string{
	"normal.black":   "#282828",
	"normal.red":     "#cc241d",
	"normal.green":   "#98971a",
	"normal.yellow":  "#d79921",
	"normal.blue":    "#458588",
	"normal.magenta": "#b16286",
	"normal.cyan":    "#689d6a",
	"normal.white":   "#a89984",

	"bright.black":   "#928374",
	"bright.red":     "#fb4934",
	"bright.green":   "#b8bb26",
	"bright.yellow":  "#fabd2f",
	"bright.blue":    "#83a598",
	"bright.magenta": "#d3869b",
	"bright.cyan":    "#8ec07c",
	"bright.white":   "#ebdbb2",
}

func c(key string) color.Color { return lipgloss.Color(PaletteHex[key]) }

var (
	fg      = c("bright.white")  // message text
	dim     = c("bright.black")  // timestamps, footers, notes
	accent  = c("bright.yellow") // the selection bar
	titleBg = c("normal.black")
)
