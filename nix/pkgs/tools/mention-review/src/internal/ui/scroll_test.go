package ui

import (
	"fmt"
	"io"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"

	"github.com/innovation-upstream/devrc/mention-review/internal/udiff"
)

// 🔴 THE PHASE-1 SCROLL RE-MEASUREMENT, ON THE *REAL* PANEL.
//
// Phase 0 measured a bare `viewport`. This measures the whole four-panel screen
// — Overview + Commits + Files + the generated footer + the diff viewport — at
// the measured p99 (~4,000 diff lines) and at the observed max (~10,000).
//
// It exists because bubbletea#1724 reports the v2 "cursed renderer" at
// ~300–800 µs per scroll frame against v1's ~50–100 µs, and names log viewers
// and code browsers as the affected shape. A scrolling diff viewer is exactly
// that workload.
//
// 🔴 IT OPENS NO WINDOW. Output goes to an in-memory writer; input is a pipe.
//
// TWO NUMBERS, BECAUSE THEY ANSWER DIFFERENT QUESTIONS:
//   - `View()` cost   — how expensive a frame is. Comparable to #1724.
//   - CPU per frame   — the same question end-to-end, through the real renderer.
// WALL latency is NOT a measure of render cost here: Bubble Tea's frame clock
// is 60 fps by default (120 max), so a healthy program is clock-bound and its
// wall latency is 1/fps whatever the buffer holds. Reporting wall time alone
// would hide a renderer that had got 10x slower but still fitted in the budget.

// syntheticDiffLines builds n lines of realistic-looking diff.
//
// 🔴 SYNTHETIC. This repository is PUBLIC; a captured diff must not land in it.
func syntheticDiffFiles(totalLines int) []udiff.FileInput {
	const perFile = 400
	var files []udiff.FileInput
	remaining := totalLines
	for i := 0; remaining > 0; i++ {
		n := perFile
		if n > remaining {
			n = remaining
		}
		remaining -= n
		var b strings.Builder
		// One hunk per file, with n body lines: alternating adds, deletes and
		// context. The header's counts are DERIVED from what is written, so the
		// fixture cannot drift out of validity.
		var body strings.Builder
		oldN, newN := 0, 0
		for j := 0; j < n; j++ {
			line := fmt.Sprintf("    ctx := build(req, %d)  // a moderately long trailing comment %d", j, i)
			switch j % 4 {
			case 0:
				body.WriteString("+" + line + "\n")
				newN++
			case 1:
				body.WriteString("-" + line + "\n")
				oldN++
			default:
				body.WriteString(" " + line + "\n")
				oldN++
				newN++
			}
		}
		fmt.Fprintf(&b, "@@ -1,%d +1,%d @@ func handler%d(req *Request) error {\n", oldN, newN, i)
		b.WriteString(body.String())
		files = append(files, udiff.FileInput{
			Path:       fmt.Sprintf("pkg/generated/module_%03d.go", i),
			ChangeType: "MODIFIED",
			Additions:  newN,
			Deletions:  oldN,
			Patch:      b.String(),
		})
	}
	return files
}

func bigApp(t testing.TB, lines int) App {
	t.Helper()
	d, err := udiff.Parse(syntheticDiffFiles(lines))
	if err != nil {
		t.Fatal(err)
	}
	a := New(fxOwner, fxName, fxNum)
	a.Width, a.Height = 140, 40
	a, _ = a.Step(PRLoaded{Snap: fixturePR()})
	a, _ = a.Step(DiffLoaded{Diff: d})
	return a
}

// --- A. frame cost, precise --------------------------------------------------

func benchScreen(b *testing.B, lines int) {
	a := bigApp(b, lines)
	n := len(a.Diff.Lines)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		a.diffCur = i % n
		a.syncDiffViewport()
		_ = a.render()
	}
}

func BenchmarkFullScreenMedian200(b *testing.B) { benchScreen(b, 200) }
func BenchmarkFullScreenP99_4000(b *testing.B)  { benchScreen(b, 4000) }
func BenchmarkFullScreenMax10000(b *testing.B)  { benchScreen(b, 10000) }

// 🔴 THE INSTRUMENT CONTROL FOR THE BENCHMARKS ABOVE. §6.2 hazard 1 says
// `SoftWrap = true` makes `viewport.calculateLine` O(n) where false is O(1). If
// the two are indistinguishable, the benchmark is not reading the buffer and
// its flat numbers mean nothing.
func benchScreenSoftWrap(b *testing.B, lines int) {
	a := bigApp(b, lines)
	a.vp.SoftWrap = true
	n := len(a.Diff.Lines)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		a.diffCur = i % n
		a.syncDiffViewport()
		_ = a.render()
	}
}

func BenchmarkFullScreenSoftWrapP99_4000(b *testing.B) { benchScreenSoftWrap(b, 4000) }
func BenchmarkFullScreenSoftWrapMax10000(b *testing.B) { benchScreenSoftWrap(b, 10000) }

// --- B. end to end, through the real renderer --------------------------------

type countingWriter struct {
	mu     sync.Mutex
	writes int
	signal chan struct{}
}

func (w *countingWriter) Write(p []byte) (int, error) {
	w.mu.Lock()
	w.writes++
	w.mu.Unlock()
	select {
	case w.signal <- struct{}{}:
	default:
	}
	return len(p), nil
}

func (w *countingWriter) count() int {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.writes
}

func procCPU() time.Duration {
	var ru syscall.Rusage
	if err := syscall.Getrusage(syscall.RUSAGE_SELF, &ru); err != nil {
		return 0
	}
	tv := func(t syscall.Timeval) time.Duration {
		return time.Duration(t.Sec)*time.Second + time.Duration(t.Usec)*time.Microsecond
	}
	return tv(ru.Utime) + tv(ru.Stime)
}

// TestScrollingTheRealPanelMeetsTheFrameBudget is the Phase-1 measurement.
//
// It FAILS if the per-frame CPU cost exceeds the 60 fps budget, so this is a
// guard as well as a report. The threshold is the frame budget itself rather
// than a number copied off a passing run: a run that cannot produce a frame
// inside 1/60 s is janky by definition, and any tighter bound would be a
// ratchet on this box's speed rather than on the program.
func TestScrollingTheRealPanelMeetsTheFrameBudget(t *testing.T) {
	if os.Getenv("DEVRC_SKIP_SCROLL_MEASUREMENT") != "" {
		t.Skip("scroll measurement disabled by DEVRC_SKIP_SCROLL_MEASUREMENT")
	}
	const frameBudget60 = time.Second / 60 // 16.67 ms

	for _, lines := range []int{200, 4000, 10000} {
		t.Run("lines="+strconv.Itoa(lines), func(t *testing.T) {
			a := bigApp(t, lines)
			keys := 120
			if max := len(a.Diff.Lines) - a.vp.Height() - 2; keys > max {
				keys = max
			}
			if keys < 10 {
				t.Fatalf("only %d scrollable steps at %d lines", keys, lines)
			}

			cw := &countingWriter{signal: make(chan struct{}, 1)}
			inR, inW := io.Pipe()
			defer inW.Close()

			p := tea.NewProgram(a,
				tea.WithInput(inR),
				tea.WithOutput(cw),
				tea.WithWindowSize(140, 40),
				tea.WithoutSignalHandler(),
				tea.WithColorProfile(0),
			)
			done := make(chan tea.Model, 1)
			go func() {
				m, err := p.Run()
				if err != nil {
					t.Error(err)
				}
				done <- m
			}()

			select {
			case <-cw.signal:
			case <-time.After(5 * time.Second):
				t.Fatal("no initial frame — the harness rendered NOTHING")
			}

			cpu0 := procCPU()
			lat := make([]time.Duration, 0, keys)
			for i := 0; i < keys; i++ {
				select {
				case <-cw.signal:
				default:
				}
				start := time.Now()
				p.Send(keyPress("j"))
				select {
				case <-cw.signal:
					lat = append(lat, time.Since(start))
				case <-time.After(3 * time.Second):
					// ⚠ THIS IS NOT NECESSARILY A HANG. The v2 renderer only
					// writes when the view actually CHANGED, so a scroll step
					// past the bottom legitimately produces no frame. `keys` is
					// capped to the scrollable range above so that cannot
					// happen here — reaching this means something else stalled.
					t.Fatalf("step %d produced no frame within 3s", i)
				}
			}
			cpu := procCPU() - cpu0
			perFrame := cpu / time.Duration(len(lat))

			p.Quit()
			final := <-done
			fm, _ := final.(App)

			// --- POSITIVE CONTROLS. A zero here is the harness observing
			// nothing, which would otherwise read as a very fast pass.
			if cw.count() == 0 {
				t.Fatal("0 renderer writes — the harness observed NOTHING")
			}
			if fm.diffCur == 0 {
				t.Fatal("the diff cursor never moved — nothing was scrolled")
			}
			if len(lat) != keys {
				t.Fatalf("collected %d latencies, want %d", len(lat), keys)
			}

			p50, p99, mx := stats(lat)
			t.Logf("lines=%-6d frames=%-4d finalCursor=%-5d", lines, cw.count(), fm.diffCur)
			t.Logf("  CPU per frame  %v   (budget at 60 fps: %v, %.1f%% used)",
				perFrame.Round(time.Microsecond), frameBudget60.Round(time.Microsecond),
				100*float64(perFrame)/float64(frameBudget60))
			t.Logf("  key->frame WALL  p50=%v p99=%v max=%v  (frame-clock bound at 1/60s)",
				p50.Round(10*time.Microsecond), p99.Round(10*time.Microsecond),
				mx.Round(10*time.Microsecond))

			// 🔴 THE GUARD. Not a ratchet on a measured number — the 60 fps
			// frame budget itself.
			if perFrame > frameBudget60 {
				t.Errorf("scrolling a %d-line diff costs %v of CPU per frame, "+
					"which does not fit the 60 fps budget of %v — this is the "+
					"Phase-0 kill criterion, re-measured on the real panel",
					lines, perFrame, frameBudget60)
			}
		})
	}
}

func stats(lat []time.Duration) (p50, p99, mx time.Duration) {
	s := append([]time.Duration(nil), lat...)
	sort.Slice(s, func(i, j int) bool { return s[i] < s[j] })
	at := func(q float64) time.Duration { return s[int(q*float64(len(s)-1))] }
	return at(0.50), at(0.99), s[len(s)-1]
}
