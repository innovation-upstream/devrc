package asr

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// writeFixture writes an env file with the given body and returns its path.
// The live operator file (~/.config/stt/env) is NEVER read here — every test
// passes its own fixture path.
func writeFixture(t *testing.T, body string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "env")
	if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestLoadConfigParsesTheTwoKeys(t *testing.T) {
	cfg, err := LoadConfig(writeFixture(t, `
# comment line
STT_API_URL=http://10.42.0.10:8118
STT_API_TOKEN="tok-with-quotes"
OTHER_KEY=ignored
no_equals_sign
`))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.URL != "http://10.42.0.10:8118" {
		t.Fatalf("URL = %q", cfg.URL)
	}
	if cfg.Token != "tok-with-quotes" {
		t.Fatalf("Token = %q (quotes must be stripped)", cfg.Token)
	}
}

func TestLoadConfigMissingFileIsLoudAndNamesTheFile(t *testing.T) {
	_, err := LoadConfig(filepath.Join(t.TempDir(), "env"))
	if err == nil || !strings.Contains(err.Error(), "does not exist") {
		t.Fatalf("missing config: %v — the operator gets this in a toast; it must name the file", err)
	}
}

func TestValidateRejectsMissingShortAndBadURL(t *testing.T) {
	for _, c := range []Config{
		{}, // both missing
		{URL: "http://ok", Token: "short"},
		{URL: "not a url", Token: "long-enough-token"},
		{URL: "ftp://wrong-scheme", Token: "long-enough-token"},
		{URL: "http://", Token: "long-enough-token"},
	} {
		if err := c.Validate(); err == nil {
			t.Fatalf("Validate accepted %+v", c)
		}
	}
	if err := (Config{URL: "http://10.42.0.10:8118", Token: "long-enough-token"}).Validate(); err != nil {
		t.Fatalf("valid config rejected: %v", err)
	}
}

func TestEndpointJoinsWithAndWithoutTrailingSlash(t *testing.T) {
	if got := Endpoint("http://h:1"); got != "http://h:1/v1/audio/transcriptions" {
		t.Fatalf("Endpoint = %q", got)
	}
	if got := Endpoint("http://h:1/"); got != "http://h:1/v1/audio/transcriptions" {
		t.Fatalf("Endpoint = %q", got)
	}
}

// serve spins an httptest server that records the request and answers with
// the given status/body.
type captured struct {
	auth        string
	contentType string
	body        []byte
}

func serve(t *testing.T, status int, body string, rec *captured) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		rec.auth = r.Header.Get("Authorization")
		rec.contentType = r.Header.Get("Content-Type")
		rec.body = raw
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
}

func TestTranscribeSendsTheOpenAICompatibleMultipart(t *testing.T) {
	// 🔴 THE WIRE CONTRACT: POST /v1/audio/transcriptions, Bearer auth,
	// multipart fields `file` (named recording.wav, audio bytes) and
	// `language=en` — and NO model field (the endpoint is self-hosted with
	// its own default model; see the DECISIONS note).
	var rec captured
	srv := serve(t, http.StatusOK,
		`{"text":"hello world","duration_s":4.2,"elapsed_s":0.9}`, &rec)
	defer srv.Close()

	wav := filepath.Join(t.TempDir(), "recording.wav")
	if err := os.WriteFile(wav, []byte("RIFF-fake-wav-bytes"), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg := Config{URL: srv.URL, Token: "long-enough-token"}

	got, err := Transcribe(context.Background(), srv.Client(), cfg, wav)
	if err != nil {
		t.Fatal(err)
	}
	if got.Text != "hello world" || got.DurationS != 4.2 || got.ElapsedS != 0.9 {
		t.Fatalf("result = %+v", got)
	}
	if rec.auth != "Bearer long-enough-token" {
		t.Fatalf("Authorization = %q", rec.auth)
	}
	if !strings.HasPrefix(rec.contentType, "multipart/form-data") {
		t.Fatalf("Content-Type = %q", rec.contentType)
	}
	b := string(rec.body)
	if !strings.Contains(b, `name="file"; filename="recording.wav"`) {
		t.Fatalf("multipart is missing the file field:\n%s", b)
	}
	if !strings.Contains(b, "RIFF-fake-wav-bytes") {
		t.Fatal("the wav bytes are not in the multipart body")
	}
	if !strings.Contains(b, `name="language"`) || !strings.Contains(b, "en") {
		t.Fatalf("multipart is missing language=en:\n%s", b)
	}
}

func TestTranscribeReports401WithTheBody(t *testing.T) {
	var rec captured
	srv := serve(t, http.StatusUnauthorized, `{"error":"bad token"}`, &rec)
	defer srv.Close()
	_, err := Transcribe(context.Background(), srv.Client(),
		Config{URL: srv.URL, Token: "long-enough-token"}, fakeWav(t))
	if err == nil || !strings.Contains(err.Error(), "401") ||
		!strings.Contains(err.Error(), "bad token") {
		t.Fatalf("401 surfaced as %v — the operator must see the endpoint's words", err)
	}
}

func TestTranscribeReports5xx(t *testing.T) {
	var rec captured
	srv := serve(t, http.StatusInternalServerError, `boom`, &rec)
	defer srv.Close()
	_, err := Transcribe(context.Background(), srv.Client(),
		Config{URL: srv.URL, Token: "long-enough-token"}, fakeWav(t))
	if err == nil || !strings.Contains(err.Error(), "500") {
		t.Fatalf("5xx surfaced as %v", err)
	}
}

func TestTranscribeTimesOut(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(400 * time.Millisecond) // longer than the client's timeout
		_, _ = w.Write([]byte(`{}`))
	}))
	defer srv.Close()
	client := &http.Client{Timeout: 100 * time.Millisecond}
	_, err := Transcribe(context.Background(), client,
		Config{URL: srv.URL, Token: "long-enough-token"}, fakeWav(t))
	if err == nil {
		t.Fatal("a hung endpoint must time out, not hang the stop path")
	}
	if !errors.Is(err, context.DeadlineExceeded) &&
		!strings.Contains(err.Error(), "Timeout") &&
		!strings.Contains(err.Error(), "timeout") {
		t.Fatalf("timeout surfaced as %v (want a timeout-class error)", err)
	}
}

func TestTranscribeRejectsNonJSONAndEmptyTranscripts(t *testing.T) {
	var rec captured
	srv := serve(t, http.StatusOK, `<html>not json</html>`, &rec)
	defer srv.Close()
	_, err := Transcribe(context.Background(), srv.Client(),
		Config{URL: srv.URL, Token: "long-enough-token"}, fakeWav(t))
	if err == nil || !strings.Contains(err.Error(), "JSON") {
		t.Fatalf("non-JSON surfaced as %v", err)
	}

	srv2 := serve(t, http.StatusOK, `{"text":"  ","duration_s":1}`, &rec)
	defer srv2.Close()
	_, err = Transcribe(context.Background(), srv2.Client(),
		Config{URL: srv2.URL, Token: "long-enough-token"}, fakeWav(t))
	if err == nil || !strings.Contains(err.Error(), "empty transcript") {
		t.Fatalf("empty transcript surfaced as %v — a TUI showing a blank entry is the silent-zero failure", err)
	}
}

func TestTranscribeWithoutARecordingFailsLoudly(t *testing.T) {
	var rec captured
	srv := serve(t, http.StatusOK, `{}`, &rec)
	defer srv.Close()
	_, err := Transcribe(context.Background(), srv.Client(),
		Config{URL: srv.URL, Token: "long-enough-token"},
		filepath.Join(t.TempDir(), "missing.wav"))
	if err == nil || !strings.Contains(err.Error(), "missing.wav") {
		t.Fatalf("missing wav surfaced as %v", err)
	}
}

func fakeWav(t *testing.T) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "recording.wav")
	if err := os.WriteFile(p, []byte("RIFF-fake-wav-bytes"), 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}
