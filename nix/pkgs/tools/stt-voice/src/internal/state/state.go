// Package state owns stt-voice's state file and the transitions between its
// four states. The file (~/.cache/stt-voice/state.json) is the ONLY shared
// state in the system: every stt-voice process is short-lived (i3 `exec`), so
// the state machine lives on disk, and the bar pill (scripts/i3status-stt)
// renders the same file the Go tool writes.
//
// 🔴 TWO TRIGGER SOURCES, ONE MACHINE. Hold-to-talk has an i3 hotkey pair
// (`$mod+m` press/release) and the bar pill's click; both funnel through the
// same Decide() below, so a click racing a keypress cannot fork the state.
//
// 🔴 THE TAP DEBOUNCE IS A STATE-MACHINE RULE, NOT A UI RULE. A stop arriving
// less than TapWindow after start is a tap: discard, go idle, no TUI. It is
// decided from `started_at` in the file, so it holds no matter which trigger
// fired the stop.
//
// 🔴 CRASH REPAIR IS READ-SIDE. A recording/transcribing row whose `pid` is
// dead is a crashed run; Repaired() maps it to idle so no command ever acts
// on a ghost. The bar script independently does the same check so a dead
// recorder never leaves a stale REC pill even with no stt-voice command
// running.
//
// Writes are atomic (temp file + rename in the same directory) so a
// half-written state file is a malformed file the readers treat as
// unmeasured, never a plausible-looking lie.
package state

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

// The four states. The strings ARE the file's contract with the bar script
// (scripts/i3status-stt renders them); renaming one is a two-file change the
// cross-language guard (test_i3_stt_voice.py) and the bar's own tests pin.
const (
	Idle         = "idle"
	Recording    = "recording"
	Transcribing = "transcribing"
	Error        = "error"
)

// TapWindow is the minimum hold a stop will act on. A stop arriving sooner is
// a tap: the recording is discarded, state goes idle, no TUI opens. 500ms per
// the feature spec; a held key always exceeds it (keypress-to-release at
// human speed is ≥100ms, and the deliberate "say one word" hold is seconds).
const TapWindow = 500 * time.Millisecond

// File is the on-disk shape. `pid` is the pid of the process whose liveness
// vouches for the row (the recording supervisor, or the stop process mid-POST)
// and is 0 for idle/error rows. `started_at` is epoch seconds — the bar pill
// renders elapsed from it, so it must move at wall-clock speed.
type File struct {
	State     string  `json:"state"`
	Pid       int     `json:"pid"`
	StartedAt float64 `json:"started_at,omitempty"`
	LastError string  `json:"last_error,omitempty"`
}

// Event is a trigger. Both trigger sources map onto these; there is no third.
type Event string

const (
	EvStart  Event = "start"
	EvStop   Event = "stop"
	EvCancel Event = "cancel"
)

// Directive is what the CALLER must physically do to realise a transition.
// Decide() itself performs no I/O; every side effect is named here so the
// whole machine stays a pure function and the tests can assert on it.
type Directive int

const (
	DoNone Directive = iota
	// DoRecord: this process becomes the recording supervisor.
	DoRecord
	// DoFinalize: end the recording (Target pid), keep the wav, transcribe.
	DoFinalize
	// DoTapDiscard: end the recording (Target pid), DELETE the wav, idle.
	// The tap path — no toast, no TUI, exit 0.
	DoTapDiscard
	// DoDiscard: DoTapDiscard via the explicit cancel path.
	DoDiscard
	// DoPost: the transcription+TUI half of stop (config, POST, history, TUI).
	DoPost
)

// Decision carries the next state, the directive, and the pid any end-recording
// directive must signal (the recording supervisor from the PREVIOUS row — the
// caller needs it after Decide has already replaced the state).
type Decision struct {
	Next   File
	Do     Directive
	Target int
	Note   string
}

// Decide is the WHOLE state machine. `actor` is the pid that will own the
// next state's liveness (the caller's own pid).
//
// 🔴 THE SECOND TRIGGER SOURCE IS A NO-OP, NOT AN ERROR. `start` while
// already recording (bar click + hotkey racing) is silent: the recording that
// is already running is the one the operator wants.
func Decide(f File, ev Event, now float64, actor int) Decision {
	switch ev {
	case EvStart:
		switch f.State {
		case Recording:
			return Decision{Next: f, Do: DoNone, Note: "already recording"}
		case Transcribing:
			return Decision{Next: f, Do: DoNone, Note: "transcription in flight"}
		default: // Idle, Error, anything unknown: start fresh (a start clears error)
			return Decision{
				Next: File{State: Recording, Pid: actor, StartedAt: now},
				Do:   DoRecord,
			}
		}
	case EvStop:
		if f.State != Recording {
			return Decision{Next: f, Do: DoNone, Note: "nothing to stop"}
		}
		target := f.Pid
		if now-f.StartedAt < TapWindow.Seconds() {
			return Decision{
				Next:   File{State: Idle, Pid: 0},
				Do:     DoTapDiscard,
				Target: target,
				Note:   "tap (<0.5s hold): recording discarded",
			}
		}
		return Decision{
			Next:   File{State: Transcribing, Pid: actor, StartedAt: f.StartedAt},
			Do:     DoFinalize,
			Target: target,
		}
	case EvCancel:
		if f.State != Recording {
			return Decision{Next: f, Do: DoNone, Note: "nothing to cancel"}
		}
		return Decision{
			Next:   File{State: Idle, Pid: 0},
			Do:     DoDiscard,
			Target: f.Pid,
		}
	default:
		return Decision{Next: f, Do: DoNone, Note: "unknown event"}
	}
}

// ToggleEvent maps the bar pill's click onto the machine: start when idle
// (or after an error — the error is stale information by then), stop when
// recording, and NOTHING while transcribing (the POST is in flight; a click
// must not race it). false = the click does nothing.
func ToggleEvent(f File) (Event, bool) {
	switch f.State {
	case Recording:
		return EvStop, true
	case Idle, Error:
		return EvStart, true
	default:
		return "", false
	}
}

// Repaired maps a crashed run to idle. 🔴 THE PID IS THE ONLY LIVENESS
// EVIDENCE: a recording/transcribing row whose pid cannot be signaled is
// stale by definition — its process cannot ever write the next row.
func (f File) Repaired(alive func(int) bool) (File, bool) {
	if f.State != Recording && f.State != Transcribing {
		return f, false
	}
	if f.Pid > 0 && alive != nil && alive(f.Pid) {
		return f, false
	}
	// pid 0 / negative / dead: stale
	return File{State: Idle, Pid: 0, LastError: ""}, true
}

// Store reads and writes the state file. Now and Alive are injectable for
// tests; Signal is called after every successful Set so the bar repaints
// with zero lag — one writer for the signal, at the write, or the pill lags.
type Store struct {
	Path   string
	Now    func() float64
	Alive  func(int) bool
	Signal func()
}

// DefaultPath is ~/.cache/stt-voice/state.json (XDG_CACHE_HOME honoured,
// same resolution as scripts/i3status-stt's state_path() — the pair is pinned
// by test_i3_stt_voice.py).
func DefaultPath() (string, error) {
	base, err := os.UserCacheDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(base, "stt-voice", "state.json"), nil
}

// NewStore builds the live store: wall clock, kill -0, bar signal.
func NewStore() (Store, error) {
	p, err := DefaultPath()
	if err != nil {
		return Store{}, err
	}
	return Store{Path: p, Now: func() float64 { return float64(time.Now().UnixNano()) / 1e9 },
		Alive:  func(pid int) bool { return killZero(pid) },
		Signal: func() { SignalBar(pkillSignalOffset) },
	}, nil
}

// pkillSignalOffset is set by main (which owns the guard-parsed constant) via
// SetSignalOffset before NewStore is used. Default 20 matches main.go.
var pkillSignalOffset = 20

// SetSignalOffset is called once from main; the tests never call it.
func SetSignalOffset(n int) { pkillSignalOffset = n }

// killZero is `kill -0`: true when the pid exists and is signalable. A
// PermissionError means it exists but belongs to someone else — alive.
// (Defined in signal_unix.go together with waitForDeath, so the syscall
// surface stays in one file.)

// Load reads the file. A missing file is the ordinary fresh-host idle.
// A MALFORMED file reads as idle but is NOT silently rewritten here — the
// caller persists the next real transition over it (and the bar script hides
// on unmeasurable either way).
func (s Store) Load() File {
	raw, err := os.ReadFile(s.Path)
	if err != nil {
		return File{State: Idle}
	}
	var f File
	if json.Unmarshal(raw, &f) != nil || f.State == "" {
		return File{State: Idle}
	}
	if repaired, changed := f.Repaired(s.Alive); changed {
		// 🔴 Persist the repair at read time: the next reader must not
		// re-decide on a ghost. A failed write is non-fatal — the decision
		// below already uses the repaired value.
		_ = s.Set(repaired)
		return repaired
	}
	return f
}

// Set persists atomically (temp file in the SAME directory + rename, so a
// reader never sees a partial file) and then signals the bar.
func (s Store) Set(f File) error {
	dir := filepath.Dir(s.Path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("state dir: %w", err)
	}
	raw, err := json.Marshal(f)
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".state-*.json")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer func() {
		if tmpName != "" {
			os.Remove(tmpName) // no-op after a successful rename
		}
	}()
	if _, err := tmp.Write(raw); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmpName, s.Path); err != nil {
		return err
	}
	tmpName = "" // renamed; the deferred remove must not touch the real file
	if s.Signal != nil {
		s.Signal()
	}
	return nil
}

// --- the two side effects the store needs, kept out of the hot path ---------
//
// SignalBar and the SIGINT plumbing live in this package (rather than a
// separate effects package) because the store's post-write signal is the one
// thing every state writer needs and tests must be able to inject around.

// SignalBar repainting the pill: pkill -RTMIN+<n> i3status-rs. Best effort —
// a missing pkill must never fail a state write.
func SignalBar(n int) {
	_ = exec.Command("pkill", fmt.Sprintf("-RTMIN+%d", n), "i3status-rs").Run()
}

// EndRecording signals a recording supervisor to stop and waits for it to be
// gone. The supervisor's own SIGINT path finalizes the wav (pw-record writes
// the header on close) and exits without touching state, so the caller owns
// whatever state comes next.
//
// 🔴 THE FALLBACK KILLS THE CHILDREN FIRST. If the supervisor does not exit
// within grace, its pw-record child is the thing holding the mic; killing the
// supervisor alone would orphan it, recording forever into a file that is
// about to be deleted. So: SIGINT the supervisor, wait grace, then (children
// still attached) SIGINT the children, wait briefly, and only then SIGKILL
// the supervisor.
func EndRecording(pid int, grace time.Duration) {
	if pid <= 0 || !killZero(pid) {
		return
	}
	_ = signalPid(pid, os.Interrupt)
	if waitForDeath(pid, grace) {
		return
	}
	// children (pw-record) first — they reparent the instant the supervisor
	// dies, and then nobody can find them by parentage
	_ = exec.Command("pkill", "-INT", "-P", fmt.Sprint(pid), "pw-record").Run()
	if !waitForDeath(pid, 2*time.Second) {
		_ = signalPid(pid, killSignal)
		waitForDeath(pid, time.Second)
	}
}
