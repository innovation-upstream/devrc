package state

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

// aliveIf records which pids count as alive per test.
func aliveIf(live map[int]bool) func(int) bool {
	return func(pid int) bool { return live[pid] }
}

// 🔴 THE TRANSITIONS. Every guard below names the trigger source question it
// answers; the machine must answer it identically no matter WHICH source
// (hotkey press/release or bar click) delivered the event.

func TestStartBeginsRecordingFromIdleAndError(t *testing.T) {
	for _, from := range []File{{State: Idle}, {State: Error, LastError: "401"}} {
		d := Decide(from, EvStart, 100.0, 42)
		if d.Do != DoRecord {
			t.Fatalf("start from %s: Do=%v, want DoRecord", from.State, d.Do)
		}
		if d.Next.State != Recording || d.Next.Pid != 42 || d.Next.StartedAt != 100.0 {
			t.Fatalf("start from %s: next=%+v", from.State, d.Next)
		}
		if d.Next.LastError != "" {
			t.Fatalf("a start must CLEAR a stale error, got %q", d.Next.LastError)
		}
	}
}

func TestStartWhileRecordingIsANoOpFromEitherSource(t *testing.T) {
	// the hotkey press AND the bar click both land here; neither may disturb
	// the live recording row (the pid is the SUPERVISOR's, not the actor's)
	f := File{State: Recording, Pid: 7, StartedAt: 90.0}
	d := Decide(f, EvStart, 100.0, 42)
	if d.Do != DoNone || d.Note == "" {
		t.Fatalf("start while recording: %+v — the second trigger source must be a documented no-op", d)
	}
	if d.Next != f {
		t.Fatalf("start while recording must not rewrite the row: %+v -> %+v", f, d.Next)
	}
}

func TestStartWhileTranscribingIsANoOp(t *testing.T) {
	f := File{State: Transcribing, Pid: 7, StartedAt: 90.0}
	if d := Decide(f, EvStart, 100.0, 42); d.Do != DoNone {
		t.Fatalf("start while transcribing must be a no-op, got %+v", d)
	}
}

func TestStopOnNothingIsANoOpFromEitherSource(t *testing.T) {
	// the bar click fires `stop` whenever the pill is clicked; the i3
	// --release binding fires it on EVERY release. Idle, transcribing and
	// error rows must all swallow it silently.
	for _, from := range []File{
		{State: Idle}, {State: Transcribing, Pid: 9},
		{State: Error, LastError: "x"},
	} {
		if d := Decide(from, EvStop, 100.0, 42); d.Do != DoNone {
			t.Fatalf("stop from %s: %+v — every release/click must be silent", from.State, d)
		}
	}
}

func TestStopAfterARealHoldFinalizesAndTranscribes(t *testing.T) {
	f := File{State: Recording, Pid: 7, StartedAt: 90.0}
	d := Decide(f, EvStop, 95.0, 42)
	if d.Do != DoFinalize || d.Target != 7 {
		t.Fatalf("stop: %+v, want DoFinalize targeting the supervisor pid 7", d)
	}
	if d.Next.State != Transcribing || d.Next.Pid != 42 {
		t.Fatalf("stop: the stop process must own the transcribing row: %+v", d.Next)
	}
	// started_at carries into the transcribing row: the bar renders elapsed
	// from it only for recording, but keeping it costs nothing and the file
	// then says when the utterance began.
	if d.Next.StartedAt != 90.0 {
		t.Fatalf("started_at lost on the transcribing row: %+v", d.Next)
	}
}

func TestStopWithinTheTapWindowDiscards(t *testing.T) {
	f := File{State: Recording, Pid: 7, StartedAt: 100.0}
	// exactly at the boundary: < TapWindow is a tap, == is not — measure at
	// both the boundary and the middle (one measurement is not a general claim)
	d := Decide(f, EvStop, 100.0+TapWindow.Seconds(), 42)
	if d.Do != DoFinalize {
		t.Fatalf("a hold of exactly TapWindow must NOT be a tap: %+v", d)
	}
	d = Decide(f, EvStop, 100.0+TapWindow.Seconds()/2, 42)
	if d.Do != DoTapDiscard {
		t.Fatalf("a hold of half the tap window must be discarded: %+v", d)
	}
	if d.Target != 7 {
		t.Fatalf("the tap must signal the supervisor (pid 7), got %d", d.Target)
	}
	if d.Next.State != Idle || d.Next.Pid != 0 {
		t.Fatalf("a tap goes idle with no pid: %+v", d.Next)
	}
}

func TestCancelDiscardsOnlyWhileRecording(t *testing.T) {
	f := File{State: Recording, Pid: 7, StartedAt: 90.0}
	d := Decide(f, EvCancel, 95.0, 42)
	if d.Do != DoDiscard || d.Target != 7 || d.Next.State != Idle {
		t.Fatalf("cancel while recording: %+v", d)
	}
	for _, from := range []File{{State: Idle}, {State: Transcribing, Pid: 9}, {State: Error}} {
		if d := Decide(from, EvCancel, 100.0, 42); d.Do != DoNone {
			t.Fatalf("cancel from %s must be a no-op, got %+v", from.State, d)
		}
	}
}

func TestToggleEventMapsBothSources(t *testing.T) {
	// click when idle/error -> start; click when recording -> stop; click
	// while transcribing -> nothing (the POST is in flight)
	if ev, ok := ToggleEvent(File{State: Idle}); !ok || ev != EvStart {
		t.Fatalf("toggle on idle: %v %v", ev, ok)
	}
	if ev, ok := ToggleEvent(File{State: Error}); !ok || ev != EvStart {
		t.Fatalf("toggle on error: %v %v — a stale error must not lock the pill", ev, ok)
	}
	if ev, ok := ToggleEvent(File{State: Recording, Pid: 7}); !ok || ev != EvStop {
		t.Fatalf("toggle on recording: %v %v", ev, ok)
	}
	if _, ok := ToggleEvent(File{State: Transcribing, Pid: 7}); ok {
		t.Fatal("toggle while transcribing must do nothing")
	}
}

// 🔴 CRASH REPAIR. The bar script maps a dead pid to idle for RENDERING; the
// store must also repair on READ so no command acts on a ghost recording.

func TestRepairedMapsDeadPidToIdle(t *testing.T) {
	for _, f := range []File{
		{State: Recording, Pid: 7, StartedAt: 90.0},
		{State: Transcribing, Pid: 7},
	} {
		got, changed := f.Repaired(aliveIf(nil))
		if !changed || got.State != Idle || got.Pid != 0 {
			t.Fatalf("dead pid on %s: %+v changed=%v — a ghost must become idle", f.State, got, changed)
		}
	}
}

func TestRepairedKeepsALiveRun(t *testing.T) {
	f := File{State: Recording, Pid: 7, StartedAt: 90.0}
	got, changed := f.Repaired(aliveIf(map[int]bool{7: true}))
	if changed || got != f {
		t.Fatalf("live run was rewritten: changed=%v got=%+v", changed, got)
	}
}

func TestRepairedNeverTouchesTerminalRows(t *testing.T) {
	// idle and error rows carry no live-process claim; a dead pid there is
	// meaningless and must not trigger a rewrite (the error row would lose
	// its message)
	for _, f := range []File{{State: Idle}, {State: Error, LastError: "keep me"}} {
		if _, changed := f.Repaired(aliveIf(nil)); changed {
			t.Fatalf("row %+v was repaired", f)
		}
	}
}

func TestRepairedTreatsPidZeroAsDead(t *testing.T) {
	f := File{State: Recording, Pid: 0}
	if _, changed := f.Repaired(aliveIf(nil)); !changed {
		t.Fatal("pid 0 must repair to idle — os.kill(0) would probe the whole process group")
	}
}

// --- the store: load/repair/set, atomicity, and the post-write signal ------

func TestStoreLoadMissingFileIsIdle(t *testing.T) {
	s := Store{Path: filepath.Join(t.TempDir(), "nope.json")}
	if f := s.Load(); f.State != Idle {
		t.Fatalf("missing file: %+v", f)
	}
}

func TestStoreLoadCorruptFileIsIdle(t *testing.T) {
	p := filepath.Join(t.TempDir(), "state.json")
	os.WriteFile(p, []byte("{ half a state"), 0o600)
	if f := (Store{Path: p}).Load(); f.State != Idle {
		t.Fatalf("corrupt file: %+v — unmeasured, never coerced", f)
	}
}

func TestStoreSetIsAtomicAndSignals(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "state.json")
	var mu sync.Mutex
	signals := 0
	s := Store{Path: p, Signal: func() { mu.Lock(); signals++; mu.Unlock() }}
	if err := s.Set(File{State: Recording, Pid: 1, StartedAt: 2}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	var f File
	if json.Unmarshal(raw, &f) != nil || f.State != Recording {
		t.Fatalf("round-trip failed: %s", raw)
	}
	mu.Lock()
	defer mu.Unlock()
	if signals != 1 {
		t.Fatalf("Set signaled %d times, want exactly 1 — the bar repaint is the write's job", signals)
	}
	// no temp litter: a leftover .state-*.json next to the file would be read
	// by nothing but is still a partial write a crash left behind
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		if e.Name() != "state.json" {
			t.Fatalf("temp litter left behind: %s", e.Name())
		}
	}
}

func TestStoreLoadRepairsAndPersistsAGhostRun(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "state.json")
	os.WriteFile(p, []byte(`{"state":"recording","pid":123456,"started_at":1.5}`), 0o600)
	var persisted File
	s := Store{Path: p, Alive: func(int) bool { return false },
		Signal: func() {}, Now: func() float64 { return 99 }}
	f := s.Load()
	if f.State != Idle {
		t.Fatalf("ghost not repaired on load: %+v", f)
	}
	raw, _ := os.ReadFile(p)
	json.Unmarshal(raw, &persisted)
	if persisted.State != Idle {
		t.Fatalf("the repair was not persisted: %s — the next reader would re-decide on the ghost", raw)
	}
}

func TestDefaultPathMatchesTheBarScript(t *testing.T) {
	t.Setenv("XDG_CACHE_HOME", "/tmp/xdg-fixture")
	p, err := DefaultPath()
	if err != nil {
		t.Fatal(err)
	}
	if p != filepath.Join("/tmp/xdg-fixture", "stt-voice", "state.json") {
		t.Fatalf("state path %q — the bar script (scripts/i3status-stt) and the Go tool must read ONE file", p)
	}
}

func TestEndRecordingIsANoOpForDeadOrMissingPids(t *testing.T) {
	// the guard below the signal: end-recording must never touch a pid that
	// is not there (a double stop must not signal a recycled pid's process
	// group by accident)
	EndRecording(0, 10*time.Millisecond)
	EndRecording(-3, 10*time.Millisecond)
	EndRecording(2_000_000, 10*time.Millisecond) // beyond pid_max on Linux
}
