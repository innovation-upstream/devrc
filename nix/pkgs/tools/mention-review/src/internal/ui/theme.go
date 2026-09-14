package ui

import (
	"image/color"

	"charm.land/lipgloss/v2"
)

// Gruvbox Dark, matching the Alacritty palette in
// `nix/programs/alacritty/default.nix`.
//
// 🔴 THE HEX VALUES ARE A LEDGER, AND A TEST PINS THEM TWO-WAY AGAINST THAT
// NIX FILE. The whole point of a matching palette is that the review window
// does not look like a different application from the terminal it opened out
// of, and a palette that drifts is a palette that stopped doing its one job.
// `theme_test.go` parses the Nix `colors` block and asserts the two maps are
// EQUAL — so a colour changed on either side fails the suite, rather than being
// noticed on screen months later.
//
// ⚠ IN LIP GLOSS v2 `lipgloss.Color` IS A FUNCTION RETURNING A STDLIB
// `color.Color`, NOT A STRING TYPE — every v1 example spells it as a
// conversion and will not compile. The whole `Renderer` concept is gone too:
// no `NewRenderer`, no `SetDefaultRenderer`, no `SetColorProfile`. That is
// what invalidates the standard golden-file recipe and any palette code copied
// from a v1 tutorial.

// PaletteHex is the ledger. Keys are `<group>.<name>` exactly as the Nix
// attribute set nests them.
var PaletteHex = map[string]string{
	"primary.background": "#282828",
	"primary.foreground": "#ebdbb2",

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

// c looks a colour up BY LEDGER KEY. 🔴 Nothing below spells a hex literal:
// a second copy of a value is how a value and its guard drift apart, and this
// way the ledger is not merely documentation of the palette — it IS the
// palette, so the test that pins it pins what is actually rendered.
func c(key string) color.Color { return lipgloss.Color(PaletteHex[key]) }

var (
	bg0 = c("primary.background")
	fg0 = c("primary.foreground")

	bBlack   = c("bright.black")
	bRed     = c("bright.red")
	bGreen   = c("bright.green")
	bYellow  = c("bright.yellow")
	bBlue    = c("bright.blue")
	bMagenta = c("bright.magenta")
)

// Styles. 🔴 COLOUR IS DECORATION ON TOP OF A WORD THAT ALREADY SAYS IT, NEVER
// THE CARRIER. The operator's font renders the red/yellow/green severity
// circles as one indistinguishable glyph, so every meaning-bearing state in
// this UI is spelled out — `CHANGES`, `CLEAN`, `1 FAILING`, `M`/`A`/`D` — and
// `words_test.go` renders each one with all colour removed and asserts the
// word survives.
var (
	styDim    = lipgloss.NewStyle().Foreground(bBlack)
	styText   = lipgloss.NewStyle().Foreground(fg0)
	styTitle  = lipgloss.NewStyle().Foreground(bYellow).Bold(true)
	styAccent = lipgloss.NewStyle().Foreground(bBlue)
	styGood   = lipgloss.NewStyle().Foreground(bGreen)
	styWarn   = lipgloss.NewStyle().Foreground(bYellow)
	styBad    = lipgloss.NewStyle().Foreground(bRed)
	styMerged = lipgloss.NewStyle().Foreground(bMagenta)

	// Diff rows. lazygit's three colours and nothing else (§6.3).
	styAdd    = lipgloss.NewStyle().Foreground(bGreen)
	styDel    = lipgloss.NewStyle().Foreground(bRed)
	styCtx    = lipgloss.NewStyle().Foreground(fg0)
	styHunk   = lipgloss.NewStyle().Foreground(bBlue).Bold(true)
	styMeta   = lipgloss.NewStyle().Foreground(bBlack).Italic(true)
	styCursor = lipgloss.NewStyle().Foreground(bg0).Background(bYellow)

	borderFocus = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(bYellow)
	borderBlur = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(bBlack)
)
