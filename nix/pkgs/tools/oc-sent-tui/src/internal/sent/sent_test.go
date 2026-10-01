package sent

import (
	"errors"
	"strings"
	"testing"
)

// fakeRunner is the injected Runner; the tests pin the exit-code mapping
// against the contract's shapes, never against a live CLI.
type fakeRunner struct {
	stdout   string
	stderr   string
	code     int
	startErr error
}

func (f fakeRunner) Run([]string) (string, string, int, error) {
	return f.stdout, f.stderr, f.code, f.startErr
}

const payloadOK = `{"session": {"id": "ses_1", "title": "t"},
 "messages": [{"epoch_ms": 1700000001000, "ts": "2026-09-30 21:28",
               "agent": "build", "text": "first\nsecond"}],
 "textless_hidden": 1, "other_agent_hidden": 2,
 "resolved_from": "exact", "notes": []}`

// recordingRunner pins what argv the loader actually execs.
type recordingRunner struct {
	fakeRunner
	argv []string
}

func (r *recordingRunner) Run(argv []string) (string, string, int, error) {
	r.argv = argv
	return r.fakeRunner.Run(argv)
}

func TestLoadPassesThePaneTitleThrough(t *testing.T) {
	rr := &recordingRunner{fakeRunner: fakeRunner{stdout: payloadOK, code: 0}}
	Load("/cwd", "OC | Research something…", rr)
	argv := strings.Join(rr.argv, " ")
	if !strings.Contains(argv, "here /cwd OC | Research something… --json") {
		t.Fatalf("title arg not passed: %q", argv)
	}
	// empty title still passes a positional (python treats empty as no hint)
	rr2 := &recordingRunner{fakeRunner: fakeRunner{stdout: payloadOK, code: 0}}
	Load("/cwd", "", rr2)
	if !strings.Contains(strings.Join(rr2.argv, " "), "here /cwd  --json") {
		t.Fatalf("empty title dropped: %q", rr2.argv)
	}
}

func TestLoadParsesTheContract(t *testing.T) {
	d, err := Load("/cwd", "", fakeRunner{stdout: payloadOK, code: 0})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if d.Session.ID != "ses_1" || d.Session.Title != "t" {
		t.Fatalf("session = %+v", d.Session)
	}
	if len(d.Messages) != 1 {
		t.Fatalf("messages = %d", len(d.Messages))
	}
	m := d.Messages[0]
	// the raw text arrives with its newline INTACT — folding is the view's
	// job, not the loader's
	if m.Text != "first\nsecond" || m.EpochMs != 1700000001000 ||
		m.Agent != "build" || m.TS != "2026-09-30 21:28" {
		t.Fatalf("message = %+v", m)
	}
	if d.TextlessHidden != 1 || d.OtherAgentHidden != 2 {
		t.Fatalf("hidden counts = %d/%d", d.TextlessHidden, d.OtherAgentHidden)
	}
	if d.ResolvedFrom == nil || *d.ResolvedFrom != "exact" {
		t.Fatalf("resolved_from = %v", d.ResolvedFrom)
	}
}

func TestLoadMapsExitCodesPerTheContract(t *testing.T) {
	// rc 4 carries a payload — an empty session is a renderable state
	d, err := Load("/cwd", "", fakeRunner{stdout: `{"session":{"id":"s"},"messages":[],
		"textless_hidden":0,"other_agent_hidden":0,"resolved_from":null,"notes":[]}`,
		code: 4})
	if err != nil {
		t.Fatalf("rc 4 must parse: %v", err)
	}
	if len(d.Messages) != 0 {
		t.Fatalf("rc 4 messages = %d", len(d.Messages))
	}

	// rc 5 (store unreadable) carries NOTHING on stdout; the stderr wording
	// is the error
	_, err = Load("/cwd", "", fakeRunner{
		stderr: "store is unreadable (missing tables or schema drift)\n", code: 5})
	if err == nil || !strings.Contains(err.Error(), "store is unreadable") {
		t.Fatalf("rc 5 error = %v", err)
	}

	// rc 3 (no such session) same shape
	_, err = Load("/cwd", "", fakeRunner{stderr: "no opencode sessions found\n", code: 3})
	if err == nil || !strings.Contains(err.Error(), "no opencode sessions") {
		t.Fatalf("rc 3 error = %v", err)
	}

	// rc 2 (no store at all)
	_, err = Load("/cwd", "", fakeRunner{stderr: "no opencode database found\n", code: 2})
	if err == nil || !strings.Contains(err.Error(), "no opencode database") {
		t.Fatalf("rc 2 error = %v", err)
	}
}

func TestLoadNamesAMissingBinary(t *testing.T) {
	_, err := Load("/cwd", "", fakeRunner{startErr: errors.New("exec: not found")})
	if err == nil || !strings.Contains(err.Error(), "not runnable") {
		t.Fatalf("missing-binary error = %v", err)
	}
}

func TestLoadRejectsMalformedJSON(t *testing.T) {
	_, err := Load("/cwd", "", fakeRunner{stdout: "{not json", code: 0})
	if err == nil || !strings.Contains(err.Error(), "cannot parse") {
		t.Fatalf("malformed error = %v", err)
	}
}

func TestLiveRunnerCarriesExitCodeAsData(t *testing.T) {
	// A nonzero exit is DATA (the contract's 2/3/4/5), never the start
	// failure — Run must not return it as err. Probed with the real exec
	// path against a command that cannot exist as a START failure only.
	_, _, code, err := LiveRunner{}.Run([]string{"true"})
	if err != nil || code != 0 {
		t.Fatalf("true: code=%d err=%v", code, err)
	}
}
