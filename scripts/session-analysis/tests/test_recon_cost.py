"""Tests for `scripts/session-analysis/recon_cost.py` — the recon-cost harness.

🔴 NO TEST READS A REAL TRANSCRIPT. `~/.claude/projects/**` holds the operator's
own prompts, a client's infrastructure and a model's summaries of both; devrc is
PUBLIC. Every transcript here is synthesised under `tmp_path` from strings chosen
to be UNMISTAKABLE if they leaked (`LEAK_*`), which is what makes
`TestNoCapturedTextEscapes` a real guard rather than a hopeful one.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SA_DIR = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("recon_cost", SA_DIR / "recon_cost.py")
R = importlib.util.module_from_spec(_spec)
sys.modules["recon_cost"] = R
_spec.loader.exec_module(R)

CMD = "analyze-service"

# 🔴 The leak canaries. Each is a string a REAL transcript would carry and this
# tool must never emit: the operator's typed command, a client path, a message
# body, a hostname. Pairwise distinct, and none is a substring of any other.
LEAK_CMD = "LEAKCANARY-typed-command-line"
LEAK_PATH = "LEAKCANARY-client-repo-path"
LEAK_BODY = "LEAKCANARY-human-message-body"
LEAK_HOST = "LEAKCANARY-internal-hostname"


# --- transcript builders -------------------------------------------------------


def _user(text: str) -> dict:
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def _assistant(blocks: list[dict]) -> dict:
    return {"type": "assistant", "message": {"content": blocks}}


def _tool_use(tid: str, name: str, command: str = "") -> dict:
    inp = {"command": command} if name == "Bash" else {"file_path": command}
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _tool_result(tid: str, content: str) -> dict:
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": content}]}}


def _invocation(command: str = CMD, args: str = "") -> dict:
    return _user(f"<command-name>/{command}</command-name><command-args>{args}</command-args>")


def _write_transcript(root: Path, name: str, msgs: list[dict]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / name
    p.write_text("".join(json.dumps(m) + "\n" for m in msgs), encoding="utf-8")
    return p


def _simple_session(bash_cmd: str = "kubectl get pods", result: str = "x" * 100) -> list[dict]:
    return [
        _invocation(args="roster"),
        _user("expansion of the slash command"),          # the harness, skipped
        _assistant([{"type": "text", "text": "looking"},
                    _tool_use("t1", "Bash", bash_cmd)]),
        _tool_result("t1", result),
        _assistant([{"type": "text", "text": "done"}]),
        _user("now do the follow-on"),                    # a REAL human turn: closes
        _assistant([_tool_use("t2", "Bash", "git commit")]),
        _tool_result("t2", "y" * 9999),                   # OUTSIDE the window
    ]


# =============================================================================
# 🔴 THE WINDOW BOUNDARY — three ways a `user` turn is not a human
# =============================================================================


class TestWindowBoundary:
    def test_the_window_closes_on_a_genuine_human_turn(self) -> None:
        [w] = R.windows_in(_simple_session(), CMD)
        assert w.closed_by_human is True
        assert w.tool_calls == 1, "the post-window Bash call leaked into the window"
        assert w.result_bytes < 200, "the post-window 9999-byte result leaked in"

    def test_the_slash_command_EXPANSION_does_not_close_it(self) -> None:
        """The turn right after a slash command is its own expansion. Counting it
        as human makes every window zero-length — the whole measurement."""
        [w] = R.windows_in(_simple_session(), CMD)
        assert w.assistant_turns == 2

    @pytest.mark.parametrize("marker", R.SYNTHETIC_MARKERS)
    def test_no_synthetic_marker_closes_the_window(self, marker: str) -> None:
        """🔴 Parametrized over the LEDGER, not a sample of it: a marker added to
        the list without a case here would be untested, and extending that list
        changes the measurement."""
        msgs = [
            _invocation(),
            _user("expansion"),
            _assistant([_tool_use("t1", "Bash", "ls")]),
            _tool_result("t1", "a" * 50),
            _user(f"{marker} injected content"),
            _assistant([_tool_use("t2", "Bash", "ls")]),
            _tool_result("t2", "b" * 50),
            _user("a real human turn"),
        ]
        [w] = R.windows_in(msgs, CMD)
        assert w.tool_calls == 2, f"{marker!r} closed the window"
        assert w.closed_by_human is True

    def test_a_tool_result_does_not_close_it_but_IS_counted(self) -> None:
        """🔴 The pair. "Does not close the window" and "is not interesting" are
        different claims, and conflating them measured zero-byte windows."""
        [w] = R.windows_in(_simple_session(result="z" * 2048), CMD)
        assert w.closed_by_human is True
        assert w.result_bytes > 2000

    def test_an_unclosed_window_is_REPORTED_as_unclosed(self) -> None:
        """A transcript that simply ends is a partial measurement, and the report
        prints the closed/total ratio rather than implying every window is whole."""
        msgs = [_invocation(), _user("expansion"), _assistant([_tool_use("t1", "Bash", "ls")])]
        [w] = R.windows_in(msgs, CMD)
        assert w.closed_by_human is False

    def test_a_bare_slash_invocation_is_matched_too(self) -> None:
        msgs = [_user(f"/{CMD} roster"), _user("expansion"),
                _assistant([{"type": "text", "text": "x"}]), _user("human")]
        assert len(R.windows_in(msgs, CMD)) == 1

    def test_a_DIFFERENT_command_is_not_matched(self) -> None:
        """The negative half: a matcher that matched everything would pass every
        positive test above."""
        assert R.windows_in(_simple_session(), "some-other-command") == []

    def test_every_window_in_one_transcript_is_measured(self) -> None:
        assert len(R.windows_in(_simple_session() + _simple_session(), CMD)) == 2


class TestTranscriptSelection:
    def test_agent_sidecars_are_skipped(self, tmp_path: Path) -> None:
        """A subagent's work already sits inside the parent's window as ONE tool
        call; counting its transcript too double-counts the dispatch."""
        _write_transcript(tmp_path / "p", "agent-abc.jsonl", _simple_session())
        _write_transcript(tmp_path / "p", "main.jsonl", _simple_session())
        rep = R.measure(tmp_path, CMD)
        assert rep.transcripts_read == 1
        assert rep.n == 1

    def test_a_malformed_line_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        p = _write_transcript(tmp_path / "p", "s.jsonl", _simple_session())
        p.write_text("{not json\n" + p.read_text(encoding="utf-8"), encoding="utf-8")
        assert R.measure(tmp_path, CMD).n == 1

    def test_transcripts_are_found_recursively(self, tmp_path: Path) -> None:
        _write_transcript(tmp_path / "a" / "b" / "c", "s.jsonl", _simple_session())
        assert R.measure(tmp_path, CMD).n == 1


# =============================================================================
# 🔴 CLASSIFICATION — order is the contract
# =============================================================================


class TestClassify:
    @pytest.mark.parametrize("cmd,want", [
        ("kubectl -n ns get pods", "kubectl:get-workloads"),
        ("kubectl -n ns get events --sort-by=.lastTimestamp", "kubectl:events"),
        ("kubectl -n ns logs deploy/x", "kubectl:logs"),
        ("kubectl -n ns describe pod x", "kubectl:describe/yaml"),
        ("kubectl config current-context", "kubectl:other"),
        ("flux get helmrelease -n ns x", "flux"),
        ("git log --oneline -10", "git:log"),
        ("git status", "git:other"),
        ("rg -n pattern .", "search:grep/rg"),
        ("find . -name '*.yaml'", "search:find/ls"),
        ("cat file.yaml", "read:cat/head/sed"),
        ("gh pr view 3", "gh"),
        ("curl -s http://x", "probe:net/db"),
        ("echo hello", "other"),
    ])
    def test_each_category_is_reachable(self, cmd: str, want: str) -> None:
        assert R.classify(cmd) == want

    def test_the_specific_kubectl_verbs_OUTRANK_kubectl_other(self) -> None:
        """🔴 Priority order is the whole contract. With `kubectl:other` first,
        every kubectl call collapses into one bucket and the report loses the
        distinction the fix was decided on."""
        names = [n for n, _ in R.CATEGORIES]
        assert names.index("kubectl:get-workloads") < names.index("kubectl:other")
        assert names.index("git:log") < names.index("git:other")

    def test_multiline_commands_are_normalized_before_matching(self) -> None:
        assert R.classify("kubectl -n ns \\\n  get pods") == "kubectl:get-workloads"

    def test_bash_categories_are_attributed_to_the_RIGHT_call(self) -> None:
        """Sizes are joined by `tool_use_id`. A join on ORDER would mis-attribute
        every byte the moment two calls resolve out of order."""
        msgs = [
            _invocation(), _user("expansion"),
            _assistant([_tool_use("a", "Bash", "kubectl get pods"),
                        _tool_use("b", "Bash", "git log --oneline")]),
            _tool_result("b", "g" * 1024),   # the SECOND call resolves FIRST
            _tool_result("a", "k" * 4096),
            _user("human"),
        ]
        [w] = R.windows_in(msgs, CMD)
        assert w.bash_categories == {"git:log": 1, "kubectl:get-workloads": 1}
        assert w.bash_category_bytes["kubectl:get-workloads"] > \
            w.bash_category_bytes["git:log"]


# =============================================================================
# 🔴 A ZERO IS AN INSTRUMENT FAILURE, NOT A MEASUREMENT
# =============================================================================


class TestZeroIsNeverAResult:
    def test_no_invocation_found_exits_NO_SAMPLE_and_prints_nothing_on_stdout(
        self, tmp_path: Path, capsys
    ) -> None:
        _write_transcript(tmp_path / "p", "s.jsonl", [_user("unrelated")])
        code = R.main(["--projects", str(tmp_path)])
        cap = capsys.readouterr()
        assert code == R.EXIT_NO_SAMPLE
        assert cap.out == "", "a zero was printed as if it were a report"
        assert "INSTRUMENT FAILURE" in cap.err

    def test_a_missing_transcript_root_is_a_USAGE_error_not_a_zero(
        self, tmp_path: Path, capsys
    ) -> None:
        """Distinguished from the above: a wrong path never looked at anything."""
        code = R.main(["--projects", str(tmp_path / "nope")])
        assert code == R.EXIT_USAGE
        assert code != R.EXIT_NO_SAMPLE
        assert "does not exist" in capsys.readouterr().err

    def test_the_positive_control_line_names_the_denominator(self, tmp_path: Path) -> None:
        _write_transcript(tmp_path / "p", "s.jsonl", _simple_session())
        _write_transcript(tmp_path / "p", "t.jsonl", [_user("unrelated")])
        text = R.render(R.measure(tmp_path, CMD))
        assert "1 real /analyze-service invocation(s) located in 2 transcript(s)" in text


# =============================================================================
# 🔴 NO CAPTURED TEXT ESCAPES
# =============================================================================


class TestNoCapturedTextEscapes:
    """devrc is PUBLIC and the baseline files live in it. Every surface is
    checked, because a guard on one is walkable by using the other."""

    @pytest.fixture
    def leaky(self, tmp_path: Path) -> Path:
        _write_transcript(tmp_path / "p", "s.jsonl", [
            _invocation(args=LEAK_CMD),
            _user("expansion"),
            _assistant([{"type": "text", "text": LEAK_BODY},
                        {"type": "thinking", "thinking": LEAK_BODY},
                        _tool_use("t1", "Bash", f"kubectl --context {LEAK_HOST} get pods"),
                        _tool_use("t2", "Read", LEAK_PATH)]),
            _tool_result("t1", LEAK_HOST * 20),
            _tool_result("t2", LEAK_BODY * 20),
            _user(f"{LEAK_BODY} — the human's next turn"),
        ])
        return tmp_path

    @pytest.mark.parametrize("canary", [LEAK_CMD, LEAK_PATH, LEAK_BODY, LEAK_HOST])
    def test_the_TEXT_report_carries_no_canary(self, leaky: Path, canary: str) -> None:
        assert canary not in R.render(R.measure(leaky, CMD))

    @pytest.mark.parametrize("canary", [LEAK_CMD, LEAK_PATH, LEAK_BODY, LEAK_HOST])
    def test_the_JSON_report_carries_no_canary(self, leaky: Path, canary: str) -> None:
        assert canary not in json.dumps(R.report_json(R.measure(leaky, CMD)))

    def test_the_canaries_WERE_actually_present_in_the_input(self, leaky: Path) -> None:
        """🔴 POSITIVE CONTROL. Without it, "no canary in the output" is equally
        satisfied by a fixture that never contained one — the reassuring zero
        this repo keeps paying for."""
        raw = (leaky / "p" / "s.jsonl").read_text(encoding="utf-8")
        for canary in (LEAK_CMD, LEAK_PATH, LEAK_BODY, LEAK_HOST):
            assert canary in raw
        # …and the run really measured that transcript, so the check was live.
        assert R.measure(leaky, CMD).n == 1

    def test_the_window_dataclass_has_no_text_carrying_field(self) -> None:
        """🔴 A STRUCTURAL claim on top of the behavioural one: a future field
        called `cmd` or `session` would pass every assertion above until someone
        rendered it. The reference harness this replaced had exactly that field.
        """
        for name, f in R.Window.__dataclass_fields__.items():
            t = str(f.type)
            assert "str" not in t or "dict[str, int]" in t, (
                f"Window.{name}: {t} — a string field can carry captured text; "
                f"counts and category-name keys are the only string surface allowed."
            )


# =============================================================================
# 🔴 AGGREGATION + COMPARISON
# =============================================================================


class TestAggregation:
    @pytest.mark.parametrize("n,want_kb", [
        (1, 0.0), (2, 1.0), (9, 8.0), (10, 9.0), (11, 9.0), (20, 18.0), (30, 27.0),
    ])
    def test_p90_picks_the_expected_element(self, n: int, want_kb: float) -> None:
        """🔴 LITERAL expectations, hand-computed from the DEFINITION (the
        `int(n*0.9)`-th smallest, 0-based), never re-derived from the
        implementation. The first version of this test computed its expectation
        with the implementation's own expression, so a mutation removing the
        clamp SURVIVED — the expectation moved with the code.

        Windows here are i KB for i in 0..n-1, so the expected value IS the
        index, which makes a wrong index visible as a wrong number.
        """
        rep = R.Report(CMD, tuple(R.Window(result_bytes=i * 1024) for i in range(n)))
        assert rep.p90_result_kb == want_kb

    @pytest.mark.parametrize("n", [1, 2, 9, 10, 11, 20, 30, 100])
    def test_p90_never_raises_for_any_sample_size(self, n: int) -> None:
        """The property the clamp is there for. It cannot currently fire —
        `int(n*0.9) < n` for every n ≥ 1 — and the docstring on the property says
        so rather than claiming a bug it does not fix."""
        rep = R.Report(CMD, tuple(R.Window(result_bytes=i * 1024) for i in range(n)))
        assert rep.p90_result_kb >= 0.0

    def test_the_tool_mix_aggregates_across_windows(self) -> None:
        rep = R.Report(CMD, (R.Window(tools={"Bash": 3, "Read": 1}),
                             R.Window(tools={"Bash": 2, "Agent": 1})))
        assert rep.tool_mix == {"Bash": 5, "Read": 1, "Agent": 1}

    def test_kb_per_call_is_per_CALL_not_per_window(self) -> None:
        rep = R.Report(CMD, (R.Window(bash_categories={"flux": 2},
                                      bash_category_bytes={"flux": 4096}),))
        assert rep.bash_mix["flux"] == {"calls": 2, "kb": 4.0, "kb_per_call": 2.0}


class TestCompare:
    BEFORE = {"n": 20, "median": {"tool_calls": 22.5, "assistant_turns": 39.5,
                                  "result_kb": 35.5},
              "p90_result_kb": 60.0, "max_result_kb": 91.0}

    def test_a_reduction_is_shown_as_a_reduction(self) -> None:
        after = {"n": 5, "median": {"tool_calls": 1.0, "assistant_turns": 4.0,
                                    "result_kb": 4.5},
                 "p90_result_kb": 5.0, "max_result_kb": 6.0}
        out = R.compare(self.BEFORE, after)
        assert "↓" in out
        assert "-21.5" in out, out

    def test_an_INCREASE_is_shown_as_an_increase(self) -> None:
        """The negative half: an arrow hardwired to ↓ passes the test above."""
        after = dict(self.BEFORE, p90_result_kb=99.0)
        assert "↑" in R.compare(self.BEFORE, after)

    def test_a_field_absent_from_either_side_is_NOT_COMPARED(self) -> None:
        """🔴 Never 0. A baseline missing a field would otherwise read as a
        dramatic improvement to zero."""
        out = R.compare({"n": 1, "median": {"tool_calls": 1.0, "assistant_turns": 1.0,
                                            "result_kb": 1.0}},
                        {"n": 1, "median": {"tool_calls": 1.0, "assistant_turns": 1.0,
                                            "result_kb": 1.0}})
        assert "NOT COMPARED" in out

    def test_the_caveat_about_an_empty_post_sample_is_printed(self) -> None:
        out = R.compare(self.BEFORE, dict(self.BEFORE))
        assert "A post-change sample of 0 is NOT an improvement" in out

    def test_an_unreadable_baseline_is_a_usage_error(self, tmp_path: Path, capsys) -> None:
        _write_transcript(tmp_path / "p", "s.jsonl", _simple_session())
        code = R.main(["--projects", str(tmp_path), "--compare", str(tmp_path / "nope.json")])
        assert code == R.EXIT_USAGE
        assert "baseline unreadable" in capsys.readouterr().err


class TestRecordedBaselineStaysReadable:
    """The committed baseline is an input to `--compare`. A schema change that
    made it unreadable would be found by whoever next tried to measure a fix —
    i.e. exactly when it is least welcome."""

    BASELINE = (Path(__file__).resolve().parents[3]
                / "claudedocs" / "analyze-service-baseline" / "BASELINE.json")

    def test_it_exists_and_parses(self) -> None:
        assert self.BASELINE.exists(), f"the recorded baseline is gone: {self.BASELINE}"
        json.loads(self.BASELINE.read_text(encoding="utf-8"))

    def test_compare_accepts_it_against_a_live_report(self, tmp_path: Path) -> None:
        _write_transcript(tmp_path / "p", "s.jsonl", _simple_session())
        now = R.report_json(R.measure(tmp_path, CMD))
        out = R.compare(json.loads(self.BASELINE.read_text(encoding="utf-8")), now)
        assert "NOT COMPARED" not in out, out

    def test_it_records_a_nonzero_sample(self) -> None:
        """A baseline of n=0 would be the instrument failure, committed."""
        assert json.loads(self.BASELINE.read_text(encoding="utf-8"))["n"] > 0

    @pytest.mark.parametrize("canary", ["/home/zach", "workspace/", "http"])
    def test_the_recorded_baseline_carries_no_path_or_url(self, canary: str) -> None:
        """🔴 Independent of the module guard: this checks the ARTIFACT that is
        actually committed to a PUBLIC repo, not the code that produced it."""
        assert canary not in self.BASELINE.read_text(encoding="utf-8")
