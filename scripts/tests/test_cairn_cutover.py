#!/usr/bin/env python3
"""`scripts/cairn-cutover.py` — the criterion 9 cutover's refusals.

🔴 WHAT THIS FILE IS GUARDING: THAT EVERY WAY OF NOT KNOWING ENDS IN A REFUSAL.
The script's job is to move a curated, client-confidential, per-host store into a
hosted one and then make local disk read-only. Every failure of that operation
is silent by nature — a bullet that was overwritten, an entry that was stranded,
a freeze that did not take. So the guards below are all of one shape: an input
the script CANNOT resolve must stop it, and a zero it did not measure must never
read as a clean one.

The two claims that carry the most weight, and are therefore tested with
controls rather than assertions:

  * the backup precondition REFUSES on every way of not getting an answer, not
    only on a stale answer (devrc#1132 exists because fifteen places asserted
    this store's backup state and were wrong);
  * the freeze's evidence is a WATCHED EACCES on a real file, and the check
    fails when a single entry is still writable.
"""

from __future__ import annotations

import http.server
import importlib.machinery
import importlib.util
import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CUTOVER = REPO / "scripts" / "cairn-cutover.py"
SERVER_PY = REPO / "scripts" / "subsystem-store-api" / "server.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(path))
    )
    module = importlib.util.module_from_spec(spec)
    # `sys.modules[name] = module` BEFORE `exec_module` — see
    # `test_cairn_cli._load_api` for the dataclass/`__module__` mechanism.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cc():
    sys.path.insert(0, str(REPO / "scripts" / "lib"))
    return _load("cairn_cutover_under_test", CUTOVER)


@pytest.fixture(scope="module")
def srv():
    sys.path.insert(0, str(REPO / "scripts" / "lib"))
    return _load("srv_cut", SERVER_PY)


def _entry(scope: str, service: str, *bullets: str, aliases: str | None = None) -> str:
    head = ["---", f"service: {service}", f"scope: {scope}", "sensitivity: internal"]
    if aliases is not None:
        head.append(f"aliases: [{aliases}]")
    head.append("---")
    return "\n".join([
        *head, "", "## What it is", f"the {service}.", "",
        "## Pointers", f"- `{scope}: src/{service}.py`", "",
        "## Nuance / work-history", *bullets, "",
    ])


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return root


# =============================================================================
# The merge rule
# =============================================================================


class TestTheMergeRule:
    def test_an_entry_the_pod_lacks_is_a_pure_ADD(self, cc, tmp_path):
        local = _tree(tmp_path / "l", {"sc/a.md": _entry("sc", "a", "- 2026-01-01: x.")})
        pod = _tree(tmp_path / "p", {})
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None)
        assert [i.verdict for i in plan.items] == [cc.ADD]

    def test_a_byte_identical_entry_is_SAME_which_is_what_makes_a_RERUN_a_NOOP(
        self, cc, tmp_path
    ):
        text = _entry("sc", "a", "- 2026-01-01: x.")
        local = _tree(tmp_path / "l", {"sc/a.md": text})
        pod = _tree(tmp_path / "p", {"sc/a.md": text})
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None)
        assert [i.verdict for i in plan.items] == [cc.SAME]
        assert plan.shippable == []

    def test_a_STALE_pod_copy_is_SUPERSEDED_by_the_host_not_hand_merged(
        self, cc, tmp_path
    ):
        """The commonest divergence by far, and the one that must NOT stop a run.

        The pod's tree was produced FROM this host's tree, so for any entry the
        pod did not itself change, the host's copy contains everything the pod's
        does. Here the pod holds the pre-rewrite `OPEN:` line and the host holds
        the `RESOLVED` one — measured on the real store as the shape of 14 of 15
        such divergences. Refusing these would make the script demand a hand
        merge for every ordinary edit since the last seed, i.e. a gate nobody
        could pass.
        """
        local = _tree(tmp_path / "l", {"sc/a.md": _entry(
            "sc", "a", "- 2026-02-09: RESOLVED beef1234: the lease expired early.")})
        pod = _tree(tmp_path / "p", {"sc/a.md": _entry(
            "sc", "a", "- 2026-02-09: OPEN: the lease expired early.")})
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None)
        assert [i.verdict for i in plan.items] == [cc.SUPERSEDES]
        assert "NONE is API-attributed" in plan.items[0].reason

    def test_an_API_ATTRIBUTED_pod_bullet_the_host_lacks_forces_a_HAND_MERGE(
        self, cc, tmp_path
    ):
        """🔴 THE DISCRIMINATOR THE WHOLE RULE TURNS ON.

        A bullet carrying the `[cairn: actor/session]` trailer was written
        THROUGH the pod, so it exists in the served copy and nowhere else. This
        fixture differs from the SUPERSEDES one above by exactly that trailer and
        by nothing else — same scope, same entry, same divergence, same bullet
        count — so a mutant that stops looking for attribution flips this case
        and only this case.
        """
        local = _tree(tmp_path / "l", {"sc/a.md": _entry(
            "sc", "a", "- 2026-02-09: the lease expired early.")})
        pod = _tree(tmp_path / "p", {"sc/a.md": _entry(
            "sc", "a",
            "- 2026-02-09: the lease expired early.",
            "- 2026-02-11: the retry storm follows it [cairn: zach/sess-77]")})
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None)
        assert [i.verdict for i in plan.items] == [cc.NEEDS_MERGE]
        assert "exists nowhere else" in plan.items[0].reason

    def test_a_HOST_vs_HOST_divergence_is_ALWAYS_a_hand_merge(self, cc, tmp_path):
        """🔴 NO SUPERSESSION ARGUMENT EXISTS HERE, whatever the mtimes say.

        Neither host's copy is a derivative of the other's — they are two
        independent edits of one file on two unreplicated stores. Note that the
        pod's copy carries NO attribution here, so the previous rule would have
        said SUPERSEDES: the peer check is what makes this case land differently,
        which is exactly the mutation that must be caught.
        """
        local = _tree(tmp_path / "l", {"sc/a.md": _entry(
            "sc", "a", "- 2026-03-04: this host's account.")})
        pod = _tree(tmp_path / "p", {"sc/a.md": _entry(
            "sc", "a", "- 2026-03-01: the seeded copy.")})
        peer = {"sc/a.md": cc.EntryFacts("sc/a.md", "e" * 64, (), ())}
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None, peer=peer)
        assert [i.verdict for i in plan.items] == [cc.NEEDS_MERGE]
        assert "neither is a derivative" in plan.items[0].reason

    def test_a_HOST_vs_HOST_agreement_is_NOT_a_hand_merge(self, cc, tmp_path):
        """The control for the rule above: the peer holding the SAME bytes must
        not trip it, or every shared entry would demand a merge and the check
        would be a constant `True` wearing a predicate's clothes."""
        text = _entry("sc", "a", "- 2026-03-04: agreed.")
        local = _tree(tmp_path / "l", {"sc/a.md": text})
        pod = _tree(tmp_path / "p", {"sc/a.md": _entry("sc", "a", "- 2026-03-01: old.")})
        same_sha = cc.read_store(local)["sc/a.md"].sha256
        peer = {"sc/a.md": cc.EntryFacts("sc/a.md", same_sha, (), ())}
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None, peer=peer)
        assert [i.verdict for i in plan.items] == [cc.SUPERSEDES]

    def test_ONLY_a_hand_authored_file_clears_a_NEEDS_MERGE(self, cc, tmp_path):
        local = _tree(tmp_path / "l", {"sc/a.md": _entry("sc", "a", "- 2026-02-09: host.")})
        pod = _tree(tmp_path / "p", {"sc/a.md": _entry(
            "sc", "a", "- 2026-02-09: host.",
            "- 2026-02-11: pod-only [cairn: zach/sess-77]")})
        merged = tmp_path / "m"
        before = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                               store_root=local, merged_dir=merged)
        assert [i.verdict for i in before.items] == [cc.NEEDS_MERGE]
        _tree(merged, {"sc/a.md": _entry(
            "sc", "a", "- 2026-02-09: host.",
            "- 2026-02-11: pod-only [cairn: zach/sess-77]")})
        after = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                              store_root=local, merged_dir=merged)
        assert [i.verdict for i in after.items] == [cc.MERGED]
        # The SOURCE must be the hand-authored file, not the host's copy —
        # otherwise the resolution is accepted and then silently discarded.
        assert after.items[0].source == merged / "sc/a.md"

    def test_the_attribution_pattern_matches_what_the_SERVER_actually_renders(
        self, cc, srv
    ):
        """🔴 PINNED BEHAVIOURALLY AGAINST THE WRITER, not against a copy of its
        regex. The discriminator is only as good as its agreement with the thing
        that produces the trailer; a second spelling of one pattern is the shape
        that drifts silently. So the SERVER renders a bullet and this asserts the
        cutover's pattern sees it.

        The NEGATIVE half is the part that matters: an ordinary bullet with no
        trailer must NOT match, or every divergence becomes a hand merge and the
        script is unusable.
        """
        rendered = srv.render_bullet(
            "the retry storm follows it", actor="zach", session="sess-77",
            today="2026-02-11",
        )
        assert cc.ATTRIBUTION.search(rendered), rendered
        assert not cc.ATTRIBUTION.search("- 2026-02-11: the retry storm follows it")

    def test_the_walk_skips_what_seed_sh_cannot_ship(self, cc, tmp_path):
        """Dot-scopes, symlinked scopes and non-`.md` files are outside the
        population `seed.sh` ships. A planner walking a wider set would push
        entries the pusher drops and then report them as landed."""
        root = _tree(tmp_path / "l", {
            "sc/a.md": _entry("sc", "a", "- 2026-01-01: x."),
            ".hidden/b.md": _entry("hidden", "b", "- 2026-01-01: x."),
            "sc/notes.txt": "not an entry",
        })
        (root / "linked").symlink_to(root / "sc")
        assert sorted(cc.read_store(root)) == ["sc/a.md"]


# =============================================================================
# Ref collisions
# =============================================================================


class TestRefCollisions:
    def test_two_entries_claiming_one_ALIAS_is_LIVE_and_BLOCKS(self, cc):
        union = {
            "sc/one.md": cc.EntryFacts("sc/one.md", "1" * 64, ("shared-alias",), ()),
            "sc/two.md": cc.EntryFacts("sc/two.md", "2" * 64, ("shared-alias",), ()),
        }
        found = cc.ref_collisions(union)
        assert [c.live for c in found] == [True]
        assert found[0].claimants == ("one.md", "two.md")

    def test_an_alias_SHADOWED_by_a_filename_is_LATENT_and_does_NOT_block(self, cc):
        """🔴 MEASURED, NOT ASSUMED — and the measurement is the next test.

        `resolve_ref_tiered` reaches the alias tier only when the FILENAME tier
        returned zero hits, so an alias spelling another entry's filename can
        never be chosen. Treating it as live would refuse a migration over a
        defect that is already present, already harmless, and present on the
        real store today — a permanently-red gate.
        """
        union = {
            "sc/one.md": cc.EntryFacts("sc/one.md", "1" * 64, (), ()),
            "sc/two.md": cc.EntryFacts("sc/two.md", "2" * 64, ("one",), ()),
        }
        found = cc.ref_collisions(union)
        assert [c.live for c in found] == [False]
        assert found[0].shadowed_by == ("one.md",)

    def test_the_FILENAME_tier_really_does_win__measured_against_the_resolver(self):
        """The precedence claim the LATENT class rests on, exercised on the real
        resolver rather than restated from its docstring.

        🔴 A DOCSTRING IS NOT A CODE PATH. The whole `latent` classification is
        an argument about which tier answers first; if that is wrong, this script
        waves through a collision that makes two entries unwritable.
        """
        sys.path.insert(0, str(REPO / "scripts" / "lib"))
        import subsystem_recall as rc

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, {
                "sc/one.md": _entry("sc", "one", "- 2026-01-01: x."),
                "sc/two.md": _entry("sc", "two", "- 2026-01-01: y.", aliases="one"),
            })
            _store, index = rc.load_store(str(root), verb="read")
            entry, tier = rc.resolve_ref_tiered("one", index, "sc")
        assert entry is not None and entry.filename == "one.md"
        assert tier == "filename", (
            "the alias tier answered first; the LATENT classification is unsafe"
        )

    def test_two_files_with_one_SLUG_is_a_FILENAME_collision(self, cc):
        union = {
            "sc/dup.md": cc.EntryFacts("sc/dup.md", "1" * 64, (), ()),
            "other/dup.md": cc.EntryFacts("other/dup.md", "2" * 64, (), ()),
        }
        # Different SCOPES — refs resolve within a scope, so this is NOT one.
        assert cc.ref_collisions(union) == []

    def test_alias_owner_parsing_refuses_a_malformed_spec(self, cc):
        assert cc.parse_alias_owner(["sc:the-ref=winner.md"]) == {("sc", "the-ref"): "winner.md"}
        for bad in ["sc-the-ref=winner.md", "sc:the-ref", "sc:=winner.md", ":a=b"]:
            with pytest.raises(ValueError):
                cc.parse_alias_owner([bad])

    def test_normalisation_is_the_RESOLVERS_not_a_second_spelling(self, cc):
        """The rule as people describe it (`lower`, `_`->`-`) is NOT the rule as
        it runs: the real one also folds every other non-slug character. A
        collision check normalising differently models a reader that does not
        exist."""
        sys.path.insert(0, str(REPO / "scripts" / "lib"))
        import subsystem_resolver

        assert cc.normalize_ref is subsystem_resolver.normalize_ref
        # A case the naive spelling gets wrong, so the identity above is not the
        # only thing holding this together.
        assert cc.normalize_ref("Foo Bar_Baz") == subsystem_resolver.normalize_ref(
            "Foo Bar_Baz"
        )
        assert cc.normalize_ref("Foo Bar_Baz") != "foo bar-baz"


# =============================================================================
# The backup precondition
# =============================================================================


class TestBackupPrecondition:
    """🔴 EVERY WAY OF NOT GETTING AN ANSWER IS A REFUSAL.

    The failure this guards is specific and has happened: fifteen places in this
    repo asserted the store's backup state and were wrong about it, in both
    directions across its life (devrc#1132). So the gate asks the cluster, and a
    missing field, a missing binary, a non-zero exit and an unparseable body are
    all refusals with their own sentence — never "0 hours ago" and never a pass.
    """

    def _fake_kubectl(self, tmp_path: Path, *, body: str, rc: int = 0) -> Path:
        """A `kubectl` on PATH that prints exactly what a test wants.

        🔴 A FAKE BINARY, NOT A MONKEYPATCH OF `run`. The thing under test is
        how this code reads a REAL subprocess's stdout and status; patching the
        runner would test the test's own idea of subprocess semantics.
        """
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        script = bindir / "kubectl"
        script.write_text(
            "#!/usr/bin/env bash\n"
            f"cat <<'EOF'\n{body}\nEOF\n"
            f"exit {rc}\n"
        )
        script.chmod(0o755)
        return bindir

    def _with_path(self, monkeypatch, bindir: Path):
        monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")

    def test_a_FRESH_successful_run_passes(self, cc, tmp_path, monkeypatch):
        now = datetime(2026, 4, 7, 15, 0, tzinfo=timezone.utc)
        stamp = (now - timedelta(hours=11)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._with_path(monkeypatch, self._fake_kubectl(
            tmp_path, body=json.dumps({"status": {"lastSuccessfulTime": stamp}})))
        ok, sentence = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None, now=now)
        assert ok, sentence
        assert "backup OK" in sentence and "11.0 h ago" in sentence

    def test_a_STALE_run_refuses_and_prints_the_age_AND_the_ceiling(
        self, cc, tmp_path, monkeypatch
    ):
        """🔴 THE FIXTURE OVERSHOOTS THE BOUNDARY, DELIBERATELY. 50 h against a
        36 h ceiling is neither a multiple of the ceiling nor adjacent to it, so
        an off-by-one or a flipped comparison cannot land on the same verdict by
        arithmetic accident — and the boundary itself is measured separately
        below, from both sides."""
        now = datetime(2026, 4, 7, 15, 0, tzinfo=timezone.utc)
        stamp = (now - timedelta(hours=50)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._with_path(monkeypatch, self._fake_kubectl(
            tmp_path, body=json.dumps({"status": {"lastSuccessfulTime": stamp}})))
        ok, sentence = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None, now=now)
        assert not ok
        assert "50.0 h old" in sentence and "36.0 h" in sentence
        # The remedy is named, and it names the form that actually updates
        # lastSuccessfulTime — a hand-rolled Job does not.
        assert "create job --from=cronjob/cj" in sentence

    @pytest.mark.parametrize("hours, expected", [(35.5, True), (36.5, False)])
    def test_the_boundary_is_measured_from_BOTH_sides(
        self, cc, tmp_path, monkeypatch, hours, expected
    ):
        """One measurement is not a general claim: a guard tested only on the
        far side of its boundary passes with the comparison deleted."""
        now = datetime(2026, 4, 7, 15, 0, tzinfo=timezone.utc)
        stamp = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._with_path(monkeypatch, self._fake_kubectl(
            tmp_path, body=json.dumps({"status": {"lastSuccessfulTime": stamp}})))
        ok, _ = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None, now=now)
        assert ok is expected

    def test_a_CronJob_that_has_NEVER_succeeded_is_COULD_NOT_MEASURE_not_a_pass(
        self, cc, tmp_path, monkeypatch
    ):
        """🔴 THE SILENT-ZERO ARM. `status.lastSuccessfulTime` is absent on a
        CronJob that has never completed a run — and a bare `-o jsonpath` prints
        an empty string for that AND for a mis-spelled query, which is why the
        whole object is fetched and the key looked up here."""
        self._with_path(monkeypatch, self._fake_kubectl(
            tmp_path, body=json.dumps({"status": {"lastScheduleTime": "2026-04-07T03:45:00Z"}})))
        ok, sentence = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None)
        assert not ok
        assert "COULD NOT MEASURE" in sentence
        assert "never completed a run" in sentence

    def test_a_MISSING_CronJob_refuses(self, cc, tmp_path, monkeypatch):
        self._with_path(monkeypatch, self._fake_kubectl(
            tmp_path, body='Error from server (NotFound): cronjobs "cj" not found', rc=1))
        ok, sentence = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None)
        assert not ok
        assert "COULD NOT MEASURE" in sentence and "exited 1" in sentence

    def test_kubectl_NOT_ON_PATH_refuses(self, cc, tmp_path, monkeypatch):
        """An absent tool is the case a naive `rc != 0` check gets right by luck
        and a `try/except` around a parse gets wrong. It must not pass."""
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        ok, sentence = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None)
        assert not ok
        assert "COULD NOT MEASURE" in sentence

    def test_UNPARSEABLE_output_refuses(self, cc, tmp_path, monkeypatch):
        self._with_path(monkeypatch, self._fake_kubectl(tmp_path, body="<html>edge</html>"))
        ok, sentence = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None)
        assert not ok
        assert "did not parse" in sentence

    def test_an_UNPARSEABLE_TIMESTAMP_refuses(self, cc, tmp_path, monkeypatch):
        self._with_path(monkeypatch, self._fake_kubectl(
            tmp_path, body=json.dumps({"status": {"lastSuccessfulTime": "yesterday"}})))
        ok, sentence = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None)
        assert not ok
        assert "unparseable timestamp" in sentence


# =============================================================================
# The write-route probe
# =============================================================================


class TestWriteRouteProbe:
    def _serve(self, code: int, status: str):
        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = b"probe\n"
                self.send_response(code)
                self.send_header("x-store-status", status)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):
                return

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def test_a_400_means_the_route_IS_deployed(self, cc):
        """🔴 THE INVERSION IS THE POINT, AND IT IS WHY THE PROBE IS SAFE. Only a
        server that DISPATCHED the POST can validate the body and refuse it 400;
        a read-only image never gets that far. So a refusal is the pass, and no
        input capable of being written is ever sent."""
        srv = self._serve(400, "bad-request")
        try:
            ok, sentence = cc.write_route_deployed(
                url=f"http://127.0.0.1:{srv.server_address[1]}", token="t", scope="sc")
        finally:
            srv.shutdown(); srv.server_close()
        assert ok, sentence
        assert "IS deployed" in sentence

    def test_a_405_is_reported_as_an_OPERATOR_problem(self, cc):
        srv = self._serve(405, "read-only")
        try:
            ok, sentence = cc.write_route_deployed(
                url=f"http://127.0.0.1:{srv.server_address[1]}", token="t", scope="sc")
        finally:
            srv.shutdown(); srv.server_close()
        assert not ok
        assert "READ-ONLY" in sentence and "operator problem" in sentence

    @pytest.mark.parametrize("code, status", [(401, "unauthorized"), (403, "")])
    def test_any_OTHER_answer_is_UNMEASURED_never_a_pass(self, cc, code, status):
        srv = self._serve(code, status)
        try:
            ok, sentence = cc.write_route_deployed(
                url=f"http://127.0.0.1:{srv.server_address[1]}", token="t", scope="sc")
        finally:
            srv.shutdown(); srv.server_close()
        assert not ok
        assert "COULD NOT MEASURE" in sentence

    def test_an_UNREACHABLE_host_is_UNMEASURED(self, cc):
        import socket as _socket

        with _socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            dead = sock.getsockname()[1]
        ok, sentence = cc.write_route_deployed(
            url=f"http://127.0.0.1:{dead}", token="t", scope="sc")
        assert not ok
        assert "COULD NOT MEASURE" in sentence

    def test_the_probe_body_CANNOT_be_written_by_the_real_server(self, srv, tmp_path):
        """🔴 THE SAFETY CLAIM, EXERCISED AGAINST THE REAL VALIDATOR rather than
        argued. `{}` must be refused by `_bullet_request_problem` — i.e. before
        any ref is resolved, any file opened or any bullet rendered — so the
        probe cannot write whatever ref or scope it is pointed at."""
        assert srv._bullet_request_problem({}) is not None
        assert srv._bullet_request_problem(json.loads("{}")) is not None


# =============================================================================
# The freeze
# =============================================================================


class TestTheFreezeIsWatchedNotAsserted:
    def test_probe_writable_reports_by_SYSCALL_and_moves_with_the_mode(
        self, cc, tmp_path
    ):
        """The positive control and the negative control of the freeze's only
        instrument, as a pair. A prober that always said `refused` would satisfy
        every downstream assertion in this file."""
        path = tmp_path / "e.md"
        path.write_text("x")
        assert cc.probe_writable(path) == "writable"
        path.chmod(0o444)
        assert cc.probe_writable(path) == "refused"
        path.chmod(0o644)
        assert cc.probe_writable(path) == "writable"

    def test_the_probe_MUTATES_NOTHING(self, cc, tmp_path):
        """It opens a curated entry for append. If it ever wrote a byte — or
        moved an mtime — it would be a mutation of client-confidential content
        with a crash window, run over every entry in the store."""
        path = tmp_path / "e.md"
        path.write_text("- 2026-01-01: content that must not move.\n")
        before = (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_size)
        assert cc.probe_writable(path) == "writable"
        after = (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_size)
        assert before == after

    def test_survey_prints_EXAMINED_beside_every_count(self, cc, tmp_path):
        root = _tree(tmp_path / "s", {
            "sc/a.md": _entry("sc", "a", "- 2026-01-01: x."),
            "sc/b.md": _entry("sc", "b", "- 2026-01-01: y."),
        })
        assert cc.survey(root) == {"examined": 2, "writable": 2, "refused": 0, "other": 0}
        cc.set_entry_mode(root, 0o444)
        assert cc.survey(root) == {"examined": 2, "writable": 0, "refused": 2, "other": 0}

    def test_an_EMPTY_store_reports_examined_0_rather_than_a_clean_zero(self, cc, tmp_path):
        """🔴 THE REASSURING ZERO. `writable: 0` from a walk that visited nothing
        is byte-identical to a fully frozen store; only `examined` separates
        them, which is why every count is printed beside it."""
        empty = tmp_path / "empty"
        empty.mkdir()
        assert cc.survey(empty) == {"examined": 0, "writable": 0, "refused": 0, "other": 0}

    def test_the_freeze_leaves_SCOPE_DIRECTORIES_writable(self, cc, tmp_path):
        """A deliberate asymmetry with a known cost: the hosted API has no CREATE
        route, so freezing the directories too would leave a brand-new
        subsystem's first entry with nowhere to go at all."""
        root = _tree(tmp_path / "s", {"sc/a.md": _entry("sc", "a", "- 2026-01-01: x.")})
        cc.set_entry_mode(root, 0o444)
        assert (root / "sc").stat().st_mode & 0o200, "the scope directory was frozen too"
        (root / "sc" / "brand-new.md").write_text("still possible")

    def test_set_entry_mode_is_IDEMPOTENT(self, cc, tmp_path):
        root = _tree(tmp_path / "s", {
            "sc/a.md": _entry("sc", "a", "- 2026-01-01: x."),
            "sc/b.md": _entry("sc", "b", "- 2026-01-01: y."),
        })
        assert cc.set_entry_mode(root, 0o444) == 2
        assert cc.set_entry_mode(root, 0o444) == 0


# =============================================================================
# End to end: the refusals stop the run, and DRY RUN changes nothing
# =============================================================================


class TestTheScriptRefusesRatherThanProceeds:
    def test_an_EMPTY_local_store_refuses_before_anything_else(self, cc, tmp_path):
        """A cutover that pushed an empty delta and then froze an empty store
        would report success having done nothing — and would then be believed."""
        empty = tmp_path / "empty"
        empty.mkdir()
        rc = cc.main(["--store", str(empty)])
        assert rc == cc.RC_NO_STORE

    def test_a_FAILED_backup_check_stops_before_the_store_is_even_read(
        self, cc, tmp_path, monkeypatch
    ):
        """🔴 ORDER MATTERS, NOT JUST OUTCOME. The backup gate must run before any
        network call and before any mode bit moves, so a run that cannot prove a
        backup exists has not touched anything at all."""
        root = _tree(tmp_path / "s", {"sc/a.md": _entry("sc", "a", "- 2026-01-01: x.")})
        monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))
        before = {p: p.stat().st_mode for p in root.rglob("*.md")}
        rc = cc.main(["--store", str(root), "--run-dir", str(tmp_path / "run")])
        assert rc == cc.RC_BACKUP
        assert {p: p.stat().st_mode for p in root.rglob("*.md")} == before
        assert not (tmp_path / "run" / "delta").exists()

    def test_UNFREEZE_is_a_real_rollback_and_is_DRY_RUN_by_default(self, cc, tmp_path):
        root = _tree(tmp_path / "s", {"sc/a.md": _entry("sc", "a", "- 2026-01-01: x.")})
        cc.set_entry_mode(root, 0o444)
        assert cc.survey(root)["refused"] == 1
        assert cc.main(["--store", str(root), "--unfreeze"]) == cc.RC_OK
        assert cc.survey(root)["refused"] == 1, "a dry run changed the mode bits"
        assert cc.main(["--store", str(root), "--unfreeze", "--apply"]) == cc.RC_OK
        assert cc.survey(root)["writable"] == 1

    def test_FREEZE_alone_is_dry_run_by_default_then_takes_and_is_idempotent(
        self, cc, tmp_path
    ):
        root = _tree(tmp_path / "s", {
            "sc/a.md": _entry("sc", "a", "- 2026-01-01: x."),
            "sc/b.md": _entry("sc", "b", "- 2026-01-01: y."),
        })
        assert cc.main(["--store", str(root), "--freeze"]) == cc.RC_OK
        assert cc.survey(root)["writable"] == 2, "a dry run froze the store"
        assert cc.main(["--store", str(root), "--freeze", "--apply"]) == cc.RC_OK
        assert cc.survey(root) == {"examined": 2, "writable": 0, "refused": 2, "other": 0}
        # The idempotent re-run: already frozen, still exit 0, nothing changed.
        assert cc.main(["--store", str(root), "--freeze", "--apply"]) == cc.RC_OK
        assert cc.survey(root)["refused"] == 2

    def test_a_FREEZE_over_an_empty_store_is_COULD_NOT_MEASURE_not_success(
        self, cc, tmp_path
    ):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert cc.main(["--store", str(empty), "--freeze", "--apply"]) == cc.RC_COULD_NOT_MEASURE

    def test_a_PARTIAL_freeze_FAILS_and_the_mode_bits_are_ROLLED_BACK(
        self, cc, tmp_path, monkeypatch
    ):
        """🔴 THE ONLY TEST THAT REACHES THE VERIFICATION ITSELF.

        Every other freeze test exercises a chmod that WORKS, so the check
        `refused == examined` is satisfied by the happy path and its removal
        changes nothing — a mutation sweep scored exactly that, which is why this
        test exists. Reaching it needs a freeze that is applied and does not take,
        so `set_entry_mode` is replaced by one that freezes all but one file. That
        is not a contrived state: an ACL, an overlay mount, a file owned by
        another user or a `chmod` that raced a writer all produce it, and the
        consequence is a store that LOOKS frozen while one entry still accepts a
        write that will die at the next seed.

        Two assertions, and the second is the point: the specific exit code
        (RC_FREEZE_INEFFECTIVE, not merely non-zero), and that the mode bits were
        RESTORED rather than left half-applied.
        """
        root = _tree(tmp_path / "s", {
            "sc/a.md": _entry("sc", "a", "- 2026-01-01: x."),
            "sc/b.md": _entry("sc", "b", "- 2026-01-01: y."),
            "sc/c.md": _entry("sc", "c", "- 2026-01-01: z."),
        })
        real = cc.set_entry_mode

        def leaky(store, mode):
            if mode != 0o444:
                return real(store, mode)
            changed = 0
            for rel in sorted(cc.read_store(store))[1:]:   # skips exactly one
                (store / rel).chmod(mode)
                changed += 1
            return changed

        monkeypatch.setattr(cc, "set_entry_mode", leaky)
        rc = cc.main(["--store", str(root), "--freeze", "--apply"])
        assert rc == cc.RC_FREEZE_INEFFECTIVE, rc
        after = cc.survey(root)
        assert after == {"examined": 3, "writable": 3, "refused": 0, "other": 0}, (
            f"the failed freeze was left half-applied: {after}. A store that looks "
            f"frozen and is not is worse than one that plainly is not."
        )

    def test_ROLLBACK_PUSH_over_an_empty_pre_push_dir_refuses(self, cc, tmp_path):
        """A rollback that restores nothing must not report success — that is the
        reassuring zero wearing a remediation's clothes."""
        run_dir = tmp_path / "run"
        (run_dir / "pre-push").mkdir(parents=True)
        assert cc.main(["--rollback-push", str(run_dir)]) == cc.RC_USAGE
        assert cc.main(["--rollback-push", str(tmp_path / "nope")]) == cc.RC_USAGE

    def test_the_MANIFEST_carries_no_bullet_text(self, cc, tmp_path, capsys):
        """The peer manifest crosses machines. It answers "does the other host
        hold this, and is it the same bytes?", which a sha256 answers — so
        shipping the prose would move client-confidential content over a channel
        that does not need it, for no gain."""
        root = _tree(tmp_path / "s", {"sc/a.md": _entry(
            "sc", "a", "- 2026-01-01: a distinctive sentence nobody else writes.")})
        assert cc.main(["--store", str(root), "--manifest"]) == cc.RC_OK
        out = capsys.readouterr().out
        assert "a distinctive sentence nobody else writes" not in out
        payload = json.loads(out)
        assert set(payload) == {"sc/a.md"}
        assert set(payload["sc/a.md"]) == {"sha256", "aliases"}


class TestTheDeltaTree:
    def test_materialise_copies_ONLY_the_shippable_entries(self, cc, tmp_path):
        text = _entry("sc", "same", "- 2026-01-01: unchanged.")
        local = _tree(tmp_path / "l", {
            "sc/same.md": text,
            "sc/new.md": _entry("sc", "new", "- 2026-01-02: brand new."),
        })
        pod = _tree(tmp_path / "p", {"sc/same.md": text})
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None)
        dest = tmp_path / "delta"
        cc._materialise(plan, dest)
        assert sorted(str(p.relative_to(dest)) for p in dest.rglob("*.md")) == ["sc/new.md"]

    def test_materialise_REBUILDS_the_tree_so_a_stale_run_cannot_leak_in(
        self, cc, tmp_path
    ):
        """`seed.sh` rsyncs SOURCE->STAGE with `--delete`, so anything left in
        the delta tree from an earlier plan is pushed by a later one."""
        dest = tmp_path / "delta"
        _tree(dest, {"sc/left-over.md": "from an earlier run"})
        local = _tree(tmp_path / "l", {"sc/new.md": _entry("sc", "new", "- 2026-01-02: x.")})
        plan = cc.plan_delta(cc.read_store(local), {}, store_root=local, merged_dir=None)
        cc._materialise(plan, dest)
        assert not (dest / "sc" / "left-over.md").exists()

    def test_save_prepush_keeps_the_SERVED_bytes_of_every_OVERWRITE_and_no_ADD(
        self, cc, tmp_path
    ):
        """🔴 THIS IS PHASE 3's ROLLBACK, AND IT MUST BE TAKEN BEFORE THE PUSH.
        An ADD has no pre-image (and the API has no delete verb, so it could not
        be rolled back anyway); an overwrite replaces bytes that afterwards exist
        only in the daily backup.

        ⚠ WHAT THIS TEST CANNOT DISTINGUISH, said rather than left to be found:
        the ADD is skipped because the served copy HAS NO SUCH FILE, which is the
        same fact the verdict `ADD` records. A sweep proved the two inseparable —
        the redundant verdict check was deleted for that reason — so this pins
        the OUTCOME (only overwrites are saved) and makes no claim about which
        expression produced it.
        """
        local = _tree(tmp_path / "l", {
            "sc/changed.md": _entry("sc", "changed", "- 2026-02-09: the host's newer line."),
            "sc/added.md": _entry("sc", "added", "- 2026-02-10: brand new."),
        })
        pod = _tree(tmp_path / "p", {
            "sc/changed.md": _entry("sc", "changed", "- 2026-02-01: the served copy.")})
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None)
        dest = tmp_path / "pre-push"
        cc._save_prepush(plan, pod, dest)
        saved = sorted(str(p.relative_to(dest)) for p in dest.rglob("*.md"))
        assert saved == ["sc/changed.md"]
        assert "the served copy" in (dest / "sc" / "changed.md").read_text()
