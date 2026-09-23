// Command stt-voice is hold-to-talk voice input: i3's $mod+m press starts
// recording the default mic, the release stops it, POSTs the audio to the
// self-hosted ASR endpoint, and opens the transcript TUI (class stt-voice,
// matched by i3's for_window rule). The bar pill (scripts/i3status-stt) is
// the second trigger source and renders the state file this tool writes.
//
// Commands:
//
//	stt-voice start     begin recording (this process BECOMES the supervisor)
//	stt-voice stop      stop + transcribe + TUI (also the i3 --release half)
//	stt-voice cancel    discard the in-flight recording, go idle
//	stt-voice toggle    the bar pill's click: start when idle, stop when recording
//	stt-voice tui       the transcript TUI (spawned by stop; not run by hand)
//
// Exit codes: 0 success/no-op; 1 a real failure (config, HTTP, recorder) —
// accompanied by a dunst toast, NEVER a silent fail; 2 usage.
package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	tea "charm.land/bubbletea/v2"

	"github.com/innovation-upstream/devrc/stt-voice/internal/asr"
	"github.com/innovation-upstream/devrc/stt-voice/internal/effects"
	"github.com/innovation-upstream/devrc/stt-voice/internal/history"
	"github.com/innovation-upstream/devrc/stt-voice/internal/recorder"
	"github.com/innovation-upstream/devrc/stt-voice/internal/state"
	"github.com/innovation-upstream/devrc/stt-voice/internal/ui"
)

// asrHTTPClient is the transcription client (timeout injected; tests use
// their own).
var asrHTTPClient = http.Client{Timeout: asr.DefaultTimeout}

const (
	// 🔴 pkillSignal is the real-time signal offset this tool pkills
	// i3status-rs with after EVERY state write. It MUST equal `signal` in
	// sttBlock (nix/graphical.nix) — the pair is pinned by
	// scripts/tests/test_i3_stt_voice.py, which also pins uniqueness against
	// every other block and the poller (a colliding signal repaints somebody
	// else's pill, which is worse than no repaint).
	pkillSignal = 20

	// 🔴 tuiClass is the WM_CLASS the TUI window carries. It MUST equal the
	// class in nix/i3/config.nix's for_window rule — same guard test.
	// alacritty's --class is <general>,<instance>; the rule matches the
	// general half.
	tuiClass = "stt-voice"

	// finalizeGrace is how long the supervisor gives pw-record to exit after
	// SIGINT (it writes the wav header on close), and how long the stop path
	// waits for the supervisor before escalating (see state.EndRecording).
	finalizeGrace = recorder.FinalizeGrace

	// tickInterval is the supervisor's bar-repaint cadence while recording:
	// the pill shows elapsed seconds, so it must tick at second granularity.
	// Each tick is one pkill (~2ms) — the reason this costs nothing is that
	// it runs ONLY while recording.
	tickInterval = time.Second

	// maxWavWait bounds how long stop waits for the finalized wav before
	// POSTing (the supervisor's SIGINT path finalizes it; a wedged recorder
	// falls through to the error path instead of hanging forever).
	maxWavWait = 3 * time.Second
)

func main() {
	state.SetSignalOffset(pkillSignal)

	args := os.Args[1:]
	// 🔴 --version IS ANSWERED BEFORE ARGUMENT VALIDATION (mention-review's
	// contract): the value is the one default.nix read out of version.go and
	// stamped back via -X, so the store path and the compiled-in string are
	// provably one value.
	if len(args) == 1 && (args[0] == "--version" || args[0] == "-v") {
		fmt.Println(buildVersion)
		return
	}
	if len(args) == 0 {
		usage()
		os.Exit(2)
	}
	switch args[0] {
	case "start":
		os.Exit(runStart())
	case "stop":
		os.Exit(runStop())
	case "cancel":
		os.Exit(runCancel())
	case "toggle":
		os.Exit(runToggle())
	case "tui":
		os.Exit(runTUI(args[1:]))
	case "-h", "--help":
		usage()
	default:
		fmt.Fprintf(os.Stderr, "stt-voice: unknown command %q\n", args[0])
		usage()
		os.Exit(2)
	}
}

func usage() {
	fmt.Fprint(os.Stderr, `usage: stt-voice start|stop|cancel|toggle|tui
  start   hold-to-talk: begin recording the default mic
  stop    stop recording, transcribe, open the transcript TUI
  cancel  discard the in-flight recording
  toggle  the bar pill's click: start when idle, stop when recording
  tui     the transcript TUI (spawned by stop; --entry ID --target WIN)
`)
}

func newStore() state.Store {
	s, err := state.NewStore()
	if err != nil {
		fmt.Fprintln(os.Stderr, "stt-voice:", err)
		os.Exit(2)
	}
	return s
}

// loadRepaired is the read every command begins with: a crashed run must be
// repaired to idle before anything decides on it.
func loadRepaired(s state.Store) state.File {
	f := s.Load()
	if repaired, changed := f.Repaired(s.Alive); changed {
		_ = s.Set(repaired)
		return repaired
	}
	return f
}

// --- start: the supervisor --------------------------------------------------

// runStart begins a recording. THIS PROCESS IS THE SUPERVISOR: i3 execs it on
// keypress and forgets it; it owns the pw-record child, repaints the bar once
// a second (the pill's elapsed tick), self-heals its state row, and turns an
// unexpected recorder death into an error row + toast.
func runStart() int {
	store := newStore()
	f := loadRepaired(store)
	d := state.Decide(f, state.EvStart, store.Now(), os.Getpid())
	switch d.Do {
	case state.DoRecord:
		if err := store.Set(d.Next); err != nil {
			effects.Toast("could not write state: " + err.Error())
			return 1
		}
		return supervise(store, d.Next.StartedAt)
	default:
		// already recording / transcription in flight: a no-op from either
		// trigger source, deliberately silent (i3 fires this on every press)
		return 0
	}
}

func supervise(store state.Store, startedAt float64) int {
	base, err := cacheBase()
	if err == nil {
		err = os.MkdirAll(base, 0o700)
	}
	wav, werr := wavPath()
	if err != nil || werr != nil {
		msg := "cache dir: " + firstErrText(err, werr)
		_ = store.Set(state.File{State: state.Error, Pid: os.Getpid(), LastError: msg})
		effects.Toast(msg)
		return 1
	}

	rec, rerr := recorder.Start(wav)
	if rerr != nil {
		msg := rerr.Error()
		_ = store.Set(state.File{State: state.Error, Pid: os.Getpid(), LastError: msg})
		effects.Toast("recording failed: " + msg)
		return 1
	}

	intCh := make(chan os.Signal, 1)
	signal.Notify(intCh, os.Interrupt, syscall.SIGTERM)
	defer signal.Stop(intCh)

	tick := time.NewTicker(tickInterval)
	defer tick.Stop()

	for {
		select {
		case <-intCh:
			// stop or cancel owns what happens next; this supervisor only
			// finalizes the wav (pw-record writes the header on close) and
			// exits WITHOUT touching state.
			_ = rec.Stop(finalizeGrace)
			return 0
		case werr := <-rec.Done():
			// the recorder died WITHOUT a signal from us: the recording
			// failed. Never leave a recording-looking state behind.
			msg := "recorder exited early: " + errText(werr)
			_ = store.Set(state.File{State: state.Error, Pid: os.Getpid(), LastError: msg})
			_ = os.Remove(wav)
			effects.Toast(msg)
			return 1
		case <-tick.C:
			effects.SignalBar(pkillSignal) // the REC elapsed tick
			// self-heal the row: a clobbered/corrupt file must not be able
			// to erase a live recording's state (the tick rewrites it).
			if f := store.Load(); f.State != state.Recording || f.Pid != os.Getpid() {
				_ = store.Set(state.File{
					State: state.Recording, Pid: os.Getpid(), StartedAt: startedAt,
				})
			}
		}
	}
}

// --- stop: finalize, transcribe, TUI ----------------------------------------

func runStop() int {
	store := newStore()
	f := loadRepaired(store)
	d := state.Decide(f, state.EvStop, store.Now(), os.Getpid())
	switch d.Do {
	case state.DoTapDiscard:
		endRecordingAndDiscard(store, d.Target)
		return 0
	case state.DoFinalize:
		return finalizeAndTranscribe(store, d.Target, d.Next.StartedAt)
	default:
		// nothing recording: every --release fires this; silent by design
		return 0
	}
}

func endRecordingAndDiscard(store state.Store, supervisor int) {
	state.EndRecording(supervisor, finalizeGrace)
	if wav, err := wavPath(); err == nil {
		_ = os.Remove(wav)
	}
	_ = store.Set(state.File{State: state.Idle, Pid: 0})
}

func finalizeAndTranscribe(store state.Store, supervisor int, startedAt float64) int {
	// 🔴 CAPTURE THE TARGET BEFORE ANYTHING SLOW. The operator's window is
	// focused NOW; a slow POST (the endpoint can take a while) must not be
	// able to change which window send-as-input types into.
	target := effects.CaptureActiveNow()

	state.EndRecording(supervisor, finalizeGrace)
	wav, err := wavPath()
	if err != nil {
		return failStop(store, err.Error())
	}
	if !effects.WaitForFile(wav, maxWavWait) {
		return failStop(store, "the recording did not finalize in time")
	}

	// Validate the config BEFORE the transcribing row: a config failure must
	// never flash a yellow "stt …" it cannot follow through on.
	cfg, cerr := asr.LoadConfig(asr.DefaultPath())
	if cerr == nil {
		cerr = cfg.Validate()
	}
	if cerr != nil {
		return failStop(store, cerr.Error())
	}

	if err := store.Set(state.File{
		State: state.Transcribing, Pid: os.Getpid(), StartedAt: startedAt,
	}); err != nil {
		return failStop(store, err.Error())
	}

	ctx, cancel := context.WithTimeout(context.Background(), asr.DefaultTimeout)
	defer cancel()
	result, terr := asr.Transcribe(ctx, &asrHTTPClient, cfg, wav)
	if terr != nil {
		return failStop(store, terr.Error())
	}

	// The transcript is the record; keep it before any UI can fail.
	now := store.Now()
	entry := history.Entry{
		ID:        time.Now().UnixNano(),
		TS:        now,
		Text:      result.Text,
		DurationS: result.DurationS,
		ElapsedS:  result.ElapsedS,
	}
	base, herr := cacheBase()
	if herr == nil {
		herr = history.Append(base, entry)
	}
	if herr != nil {
		return failStop(store, "history: "+herr.Error())
	}

	_ = store.Set(state.File{State: state.Idle, Pid: 0})
	_ = os.Remove(wav) // transcribed; the file has served its purpose

	if serr := spawnTUI(entry.ID, target); serr != nil {
		// The transcript IS in history; the TUI failing to open is a real
		// failure the operator must hear about, not a silent one.
		effects.Toast("transcript saved but the TUI failed to open: " + serr.Error())
		return 1
	}
	return 0
}

func failStop(store state.Store, msg string) int {
	_ = store.Set(state.File{State: state.Error, Pid: os.Getpid(), LastError: msg})
	effects.Toast(msg)
	return 1
}

// spawnTUI opens the transcript window. The class must match i3's for_window
// rule (tuiClass), and the process is detached (Setsid) so the stop path can
// exit while the TUI lives.
func spawnTUI(entryID int64, target int) error {
	self, err := os.Executable()
	if err != nil {
		return err
	}
	args := []string{
		"--class", tuiClass + "," + tuiClass,
		"-o", "window.dimensions.columns=100",
		"-o", "window.dimensions.lines=20",
		"-e", self, "tui",
		"--entry", strconv.FormatInt(entryID, 10),
		"--target", strconv.Itoa(target),
	}
	cmd := exec.Command("alacritty", args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	return cmd.Start()
}

// --- cancel + toggle ---------------------------------------------------------

func runCancel() int {
	store := newStore()
	f := loadRepaired(store)
	d := state.Decide(f, state.EvCancel, store.Now(), os.Getpid())
	if d.Do == state.DoDiscard {
		endRecordingAndDiscard(store, d.Target)
	}
	return 0
}

func runToggle() int {
	store := newStore()
	f := loadRepaired(store)
	ev, ok := state.ToggleEvent(f)
	switch {
	case ok && ev == state.EvStart:
		return runStart()
	case ok && ev == state.EvStop:
		return runStop()
	default:
		return 0 // transcribing: the POST is in flight; a click must not race it
	}
}

// --- the TUI command ---------------------------------------------------------

func runTUI(args []string) int {
	var entryID int64
	var target int
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--entry":
			if i+1 < len(args) {
				entryID, _ = strconv.ParseInt(args[i+1], 10, 64)
				i++
			}
		case "--target":
			if i+1 < len(args) {
				target, _ = strconv.Atoi(args[i+1])
				i++
			}
		}
	}
	base, err := cacheBase()
	if err != nil {
		effects.Toast("cache dir: " + err.Error())
		return 1
	}
	entries, err := history.Load(base)
	if err != nil {
		effects.Toast("history: " + err.Error())
		return 1
	}
	p := tea.NewProgram(ui.New(entries, entryID, target))
	if _, err := p.Run(); err != nil {
		fmt.Fprintln(os.Stderr, "stt-voice tui:", err)
		return 1
	}
	return 0
}

// --- shared helpers -----------------------------------------------------------

func errText(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

func firstErrText(errs ...error) string {
	for _, e := range errs {
		if e != nil && !errors.Is(e, context.Canceled) {
			return e.Error()
		}
	}
	return "unknown error"
}
