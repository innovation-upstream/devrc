// Package sent loads "what did I send" data by exec'ing the `oc-sent` CLI —
// never by opening the sqlite store itself.
//
// 🔴 WHY EXECCING THE PYTHON CLI IS THE DESIGN, NOT A SHORTCUT. The store's
// schema carries no stability guarantee, and this repo's own tooling has
// twice declined to couple to it: the dispatch tooling refuses to read the
// internal schema, and `scripts/collector/opencode/` wraps it behind
// `_shared`/`export.py`. `oc-sent` is that store's tested human-facing
// reader — one reader of the schema, `--json` is its pinned machine
// contract (the shape tests live in
// `scripts/collector/opencode/tests/test_sent.py`, and a contract change
// lands with them in the same commit) — so this package parses THAT, and a
// schema drift surfaces here as the CLI's own exit code, already worded.
//
// 🔴 EXIT-CODE MAPPING IS PART OF THE CONTRACT: `oc-sent` returns 0 and 4
// with a JSON payload on stdout (4 = no displayable messages — an empty
// session is a renderable state), and 2/3/5 with an EMPTY stdout. Anything
// else is a failure of the pipeline, reported verbatim.
package sent

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
)

// Message is one user-typed message, exactly the --json contract's fields.
type Message struct {
	EpochMs int64  `json:"epoch_ms"`
	TS      string `json:"ts"`
	Agent   string `json:"agent"`
	Text    string `json:"text"`
}

// Session identifies the session the messages belong to.
type Session struct {
	ID    string `json:"id"`
	Title string `json:"title"`
}

// Data is the whole --json payload.
type Data struct {
	Session          Session   `json:"session"`
	Messages         []Message `json:"messages"`
	TextlessHidden   int       `json:"textless_hidden"`
	OtherAgentHidden int       `json:"other_agent_hidden"`
	ResolvedFrom     *string   `json:"resolved_from"`
	Notes            []string  `json:"notes"`
}

// Runner is the exec seam the tests inject. Run returns the CLI's stdout,
// stderr and exit code; err is for "could not start at all" (binary
// missing) only.
type Runner interface {
	Run(argv []string) (stdout string, stderr string, exitCode int, err error)
}

// LiveRunner execs `oc-sent` from PATH.
type LiveRunner struct{}

func (LiveRunner) Run(argv []string) (string, string, int, error) {
	cmd := exec.Command(argv[0], argv[1:]...)
	var out, errb strings.Builder
	cmd.Stdout = &out
	cmd.Stderr = &errb
	err := cmd.Run()
	code := 0
	if exit, ok := err.(*exec.ExitError); ok {
		code = exit.ExitCode()
		err = nil // an exit CODE is data, not a start failure
	}
	return out.String(), errb.String(), code, err
}

// Load runs `oc-sent here <cwd> --json` and parses the payload.
func Load(cwd string, r Runner) (Data, error) {
	out, errb, code, err := r.Run([]string{"oc-sent", "here", cwd, "--json"})
	if err != nil {
		return Data{}, fmt.Errorf(
			"oc-sent is not runnable (is it on PATH?): %v", err)
	}
	switch code {
	case 0, 4:
		var d Data
		if jerr := json.Unmarshal([]byte(out), &d); jerr != nil {
			return Data{}, fmt.Errorf(
				"cannot parse oc-sent --json output (rc %d): %v", code, jerr)
		}
		return d, nil
	default:
		detail := strings.TrimSpace(errb)
		if detail == "" {
			detail = strings.TrimSpace(out)
		}
		return Data{}, fmt.Errorf("oc-sent failed (rc %d): %s", code, detail)
	}
}
