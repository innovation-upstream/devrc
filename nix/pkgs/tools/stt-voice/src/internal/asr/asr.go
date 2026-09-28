// Package asr is the transcription client: the operator's env file, the
// OpenAI-compatible multipart POST, and the response shape.
//
// 🔴 THE CONFIG FILE IS THE OPERATOR'S, NOT THIS TOOL'S. One file
// (`~/.config/stt/env`, 0600, manual — SECRETS.md row) already serves the
// `scripts/stt` bash CLI on every mesh host, and this tool reads THE SAME two
// keys (STT_API_URL, STT_API_TOKEN) rather than minting a second credential
// file for the same secret. 🔴 THE FILE IS NEVER OPENED BY DEVELOPMENT — no
// test, no fixture, no debug run touches the live path; tests build their own
// env files in t.TempDir() and pass the path in. The deployed tool is the
// only reader.
//
// 🔴 MISSING OR SHORT CREDENTIALS ARE A TOAST + A NON-ZERO EXIT, NEVER A
// SILENT FAIL (and never a guessed default: unlike scripts/stt there is no
// URL fallback here — a hold-to-talk that silently transcribed to a guessed
// host would be a worse failure than no transcription at all).
package asr

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// MinTokenLen is the "short token" bar. A bearer token below this cannot be
// a real credential, and sending it would produce a 401 whose cause the
// operator cannot tell from a wrong token — so the tool refuses BEFORE the
// request and says so in the toast.
const MinTokenLen = 8

// Language is sent with every request (the feature spec: language=en).
const Language = "en"

// DefaultTimeout bounds one transcription call.
const DefaultTimeout = 60 * time.Second

// Config is the two keys the endpoint needs.
type Config struct {
	URL   string
	Token string
}

// Result is the endpoint's JSON response shape:
// {"text": "...", "duration_s": 4.2, "elapsed_s": 0.9}.
type Result struct {
	Text      string  `json:"text"`
	DurationS float64 `json:"duration_s"`
	ElapsedS  float64 `json:"elapsed_s"`
}

// DefaultPath is the operator's env file, shared with scripts/stt. (Never
// opened by development or tests — the code path is exercised with fixture
// paths only.)
func DefaultPath() string {
	base := os.Getenv("XDG_CONFIG_HOME")
	if base == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return ""
		}
		base = filepath.Join(home, ".config")
	}
	return filepath.Join(base, "stt", "env")
}

// LoadConfig parses an env file of KEY=VALUE lines (blank lines and `#`
// comments skipped, optional single/double quotes stripped) into a Config.
// Unknown keys are ignored — the file belongs to the operator, and scripts/stt
// sources it with `.` so anything shell-legal is fair game there.
func LoadConfig(path string) (Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return Config{}, fmt.Errorf("config file %s does not exist (see SECRETS.md: STT_API_URL + STT_API_TOKEN)", path)
		}
		return Config{}, fmt.Errorf("config file %s unreadable: %w", path, err)
	}
	var cfg Config
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			continue // not an assignment; the operator's file, not our format
		}
		key = strings.TrimSpace(key)
		value = strings.TrimSpace(value)
		value = trimQuotes(value)
		switch key {
		case "STT_API_URL":
			cfg.URL = value
		case "STT_API_TOKEN":
			cfg.Token = value
		}
	}
	return cfg, nil
}

func trimQuotes(v string) string {
	if len(v) >= 2 &&
		((v[0] == '"' && v[len(v)-1] == '"') || (v[0] == '\'' && v[len(v)-1] == '\'')) {
		return v[1 : len(v)-1]
	}
	return v
}

// Validate refuses to run with what cannot be a real credential pair.
// 🔴 THE MESSAGE NAMES THE FILE AND THE KEY, because the toast is the only
// surface the operator gets.
func (c Config) Validate() error {
	if c.URL == "" {
		return errors.New("STT_API_URL is missing from ~/.config/stt/env")
	}
	u, err := url.Parse(c.URL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return fmt.Errorf("STT_API_URL %q is not a usable http(s) URL", c.URL)
	}
	if len(strings.TrimSpace(c.Token)) < MinTokenLen {
		return fmt.Errorf("STT_API_TOKEN is missing or shorter than %d characters", MinTokenLen)
	}
	return nil
}

// Endpoint joins the configured base URL with the OpenAI-compatible
// transcriptions path.
func Endpoint(base string) string {
	return strings.TrimRight(base, "/") + "/v1/audio/transcriptions"
}

// Transcribe POSTs the wav as a multipart request and decodes the response.
// The http.Client is injected so the timeout (and the tests' httptest server)
// are the caller's decision.
func Transcribe(ctx context.Context, client *http.Client, cfg Config, wavPath string) (Result, error) {
	var zero Result
	if err := cfg.Validate(); err != nil {
		return zero, err
	}
	audio, err := os.ReadFile(wavPath)
	if err != nil {
		return zero, fmt.Errorf("recording %s: %w", wavPath, err)
	}

	body := &bytes.Buffer{}
	mw := multipart.NewWriter(body)
	fw, err := mw.CreateFormFile("file", "recording.wav")
	if err != nil {
		return zero, err
	}
	if _, err := fw.Write(audio); err != nil {
		return zero, err
	}
	if err := mw.WriteField("language", Language); err != nil {
		return zero, err
	}
	if err := mw.Close(); err != nil {
		return zero, err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, Endpoint(cfg.URL), body)
	if err != nil {
		return zero, err
	}
	req.Header.Set("Content-Type", mw.FormDataContentType())
	req.Header.Set("Authorization", "Bearer "+cfg.Token)

	resp, err := client.Do(req)
	if err != nil {
		return zero, fmt.Errorf("POST %s: %w", Endpoint(cfg.URL), err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return zero, fmt.Errorf("reading the transcription response: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		return zero, fmt.Errorf("the ASR endpoint answered HTTP %d: %s",
			resp.StatusCode, snippet(raw))
	}
	var result Result
	if err := json.Unmarshal(raw, &result); err != nil {
		return zero, fmt.Errorf("the ASR endpoint did not answer JSON: %s", snippet(raw))
	}
	if strings.TrimSpace(result.Text) == "" {
		return zero, errors.New("the ASR endpoint returned an empty transcript")
	}
	return result, nil
}

func snippet(raw []byte) string {
	s := strings.TrimSpace(string(raw))
	if len(s) > 200 {
		s = s[:200] + "…"
	}
	if s == "" {
		s = "(empty body)"
	}
	return s
}
