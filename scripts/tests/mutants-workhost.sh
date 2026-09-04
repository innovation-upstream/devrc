#!/usr/bin/env bash
# Mutation battery for `scripts/workhost` — every guard in
# `scripts/tests/test_workhost.py` broken on purpose, so the claim "these tests
# would have caught it" can be RE-DERIVED rather than believed.
#
#   bash scripts/tests/mutants-workhost.sh          # exit 0 only if ALL rows ok
#
# 🔴 IT NEVER TOUCHES YOUR WORKING TREE. The target is copied into a mktemp -d
#    and mutated there; a final sha256 check proves the copy was restored.
# 🔴 EVERY MUTANT NAMES THE TEST THAT MUST KILL IT. Dying to a different test is
#    a failure (WRONG-KILLER), because that means the guard you think covers the
#    behaviour is not the one doing the work.
# 🔴 EVERY MUTATION IS DIFFED BEFORE IT RUNS. A sed that silently matched
#    nothing would score SURVIVED and read as a coverage hole that is not there.
# 🔴 PYTHONDONTWRITEBYTECODE=1 IS LOAD-BEARING, NOT HYGIENE. CPython validates a
#    cached module on source mtime-in-whole-SECONDS + size, so a same-length edit
#    landing in the same second as the last import is invisible: the test imports
#    the ORIGINAL bytecode and the mutant is scored SURVIVED WITHOUT EVER HAVING
#    RUN. Every mutant here is applied by rewriting the file in place, so this
#    trap is live.
# 🔴 THREE CONTROLS, ALL MANDATORY:
#      * the unmutated baseline must be GREEN, or the run aborts;
#      * a MIN_TESTS floor, so a suite that never collected cannot read as clean;
#      * a positive control (a mutant known to be covered) AND a SURVIVES control
#        (a comment-only, behaviour-free edit that must kill nothing, proving the
#        harness keys on behaviour rather than on text).
#
# SCOPE: this file is a claim about the mutants enumerated below and nothing
# else. Add a row when you add a guard.
set -uo pipefail
CDPATH=
D="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
SRC="$(cd "$D/../.." && pwd)"

T="$(mktemp -d /tmp/workhost-mut-XXXXXX)"; trap 'rm -rf "$T"' EXIT
ROOT="$T/tree"
mkdir -p "$ROOT/scripts/tests" "$ROOT/nix"
cp -a "$SRC/scripts/workhost"                "$ROOT/scripts/"     # cp -a keeps the exec bit
cp -a "$SRC/scripts/testlib"                 "$ROOT/scripts/"
cp -a "$SRC/scripts/tests/test_workhost.py"  "$ROOT/scripts/tests/"
# The delivery test reads this file; copying it keeps the baseline green without
# weakening the row set.
cp -a "$SRC/nix/home.nix"                    "$ROOT/nix/"
[ -e "$ROOT/.git" ] && { echo "🔴 the copy carries a .git — refusing to run"; exit 2; }
find "$ROOT" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

SCRIPT="$ROOT/scripts/workhost"
SUITE="$ROOT/scripts/tests/test_workhost.py"
cp -a "$SCRIPT" "$T/script.orig"
ORIG_SHA="$(sha256sum "$T/script.orig" | cut -d' ' -f1)"
restore() { cp -a "$T/script.orig" "$SCRIPT"; }

FAILURES=0
ROWS=0
# 89 tests collect today; one is deselected below. The floor sits well under
# that so it catches a suite that failed to collect, not routine growth.
MIN_TESTS=80

# `test_workhost_is_tracked_by_git` asserts about the REAL repository, so it
# cannot pass against a .git-less copy. It is a delivery guard, not a behaviour
# guard, and no mutant below targets it — excluding it cannot hide a killer.
# Nodeids are reported RELATIVE to rootdir, so the deselect must be relative
# too — an absolute one silently matches nothing and the row reads as a red
# baseline rather than as a filter that failed to apply.
RELSUITE="scripts/tests/test_workhost.py"
DESELECT="--deselect $RELSUITE::test_workhost_is_tracked_by_git"

failing() {
  local out n f total
  out="$(cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$RELSUITE" \
    $DESELECT -q --no-header --tb=no -p no:cacheprovider 2>/dev/null)"
  # Read the CONTENT, never an exit code: count the runner's own result lines.
  n="$(sed -n 's/^\([0-9]*\) passed.*/\1/p;s/^[0-9]* failed, \([0-9]*\) passed.*/\1/p' <<<"$out" | tail -1)"
  f="$(sed -n 's/^\([0-9]*\) failed.*/\1/p' <<<"$out" | tail -1)"
  total=$(( ${n:-0} + ${f:-0} ))
  if [ "$total" -lt "$MIN_TESTS" ]; then
    echo "__HARNESS_BROKE__ only $total test(s) ran (floor $MIN_TESTS)"
    return
  fi
  sed -n 's/^FAILED [^:]*::\([A-Za-z0-9_]*\).*/\1/p' <<<"$out" | sort -u
}

run() { # run <name> <expected killer | SURVIVES> <sed-expr>
  local name="$1" expect="$2" expr="$3" got
  ROWS=$((ROWS+1))
  restore
  sed "$expr" "$SCRIPT" > "$T/m"
  if cmp -s "$T/m" "$SCRIPT"; then
    printf '  🔴 %-34s MUTATION DID NOT APPLY (sed matched nothing)\n' "$name"
    FAILURES=$((FAILURES+1)); restore; return
  fi
  cat "$T/m" > "$SCRIPT"          # NOT cp -a: that carried the wrong mode once
  got="$(failing)"
  restore

  if grep -q '__HARNESS_BROKE__' <<<"$got"; then
    printf '  🔴 %-34s HARNESS BROKE: %s\n' "$name" "$(tr '\n' ' ' <<<"$got")"
    FAILURES=$((FAILURES+1)); return
  fi

  if [ "$expect" = "SURVIVES" ]; then
    if [ -z "$got" ]; then
      printf '  ok %-34s survived, as designed (behaviour-free edit)\n' "$name"
    else
      printf '  🔴 %-34s SURVIVES control KILLED by: %s\n' "$name" "$(tr '\n' ' ' <<<"$got")"
      FAILURES=$((FAILURES+1))
    fi
    return
  fi

  if [ -z "$got" ]; then
    printf '  🔴 %-34s SURVIVED — no test failed\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  if grep -qx "$expect" <<<"$got"; then
    printf '  ok %-34s killed by %s\n' "$name" "$expect"
  else
    printf '  🔴 %-34s WRONG-KILLER — died to: %s (wanted %s)\n' \
      "$name" "$(tr '\n' ' ' <<<"$got")" "$expect"
    FAILURES=$((FAILURES+1))
  fi
}

printf 'mutating a COPY at %s (your worktree is untouched)\n' "$ROOT"
printf 'baseline (must be empty): '
b="$(failing)"
if [ -z "$b" ]; then echo "clean"; else echo "🔴 ALREADY RED: $(tr '\n' ' ' <<<"$b")"; exit 1; fi

printf '\n== known_hosts identity: the alias must be on every ssh-family call ==\n'
run 'hostkeyalias-dropped'      test_host_key_alias_is_set_for_every_path_and_verb \
  's@opts = \["-o", "HostKeyAlias=%s" % host.name\]@opts = []@'
run 'rsync-loses-the-alias'     test_rsync_threads_the_alias_through_dash_e \
  's@ssh_cmd = " ".join(\["ssh"\] + opts)@ssh_cmd = "ssh"@'

printf '\n== the probe must be a real auth handshake, not a port check ==\n'
run 'batchmode-dropped'         test_probe_is_a_real_ssh_session_not_a_port_check \
  's@\["ssh", "-o", "BatchMode=yes"\]@["ssh"]@'
run 'connect-timeout-dropped'   test_timeout_reaches_ssh_as_connect_timeout \
  's@opts += \["-o", "ConnectTimeout=%d" % max(1, int(connect_timeout))\]@pass@'

printf '\n== three states, never two ==\n'
run 'not-configured-collapsed'  test_tailscale_absent_is_not_configured \
  's@^NOT_CONFIGURED = "not-configured"@NOT_CONFIGURED = "unreachable"@'
run 'absent-tailnet-guessed'    test_tailnet_without_this_host_is_not_configured \
  's@return None, "%s is not in the tailnet" % name@return "203.0.113.1", ""@'

printf '\n== the exit-code contract ==\n'
run 'remote-exit-swallowed'     test_remote_exit_code_passes_through_unchanged \
  's@return subprocess.run(cmd).returncode@subprocess.run(cmd); return 0@'
run 'no-path-code-is-not-3'     test_no_path_reachable_exits_3 \
  's@^EXIT_NO_PATH = 3@EXIT_NO_PATH = 1@'

printf '\n== path selection and precedence ==\n'
run 'precedence-reversed'       test_best_available_path_is_selected \
  's@^PATH_ORDER = ("lan", "nebula", "tailscale")@PATH_ORDER = ("tailscale", "nebula", "lan")@'
run 'forced-path-falls-back'    test_forcing_a_down_path_fails_and_does_not_fall_back \
  's@^        if result.state != OK:@        if False:@'

printf '\n== running ON the target must exec locally ==\n'
run 'local-detection-disabled'  test_runs_locally_when_the_nebula_address_is_ours \
  's@^    local = is_local_host(host)@    local = False@'
run 'nebula-addr-not-consulted' test_runs_locally_when_the_nebula_address_is_ours \
  's@^    if host.nebula and host.nebula in local:@    if False:@'

printf '\n== probing is parallel and bounded ==\n'
run 'probing-made-serial'       test_probes_run_in_parallel_not_in_series \
  's@max_workers=len(PATH_ORDER)@max_workers=1@'
run 'probe-timeout-dropped'     test_timeout_is_honoured \
  's@^            timeout=timeout,$@            timeout=None,@'

printf '\n== verb construction ==\n'
run 'tmux-loses-its-pty'        test_tmux_requests_a_pty \
  's@return \["ssh", "-t"\] + opts@return ["ssh"] + opts@'
run 'scp-colon-not-substituted' test_scp_substitutes_the_address_for_a_leading_colon \
  's@^        if arg.startswith(":"):@        if False:@'
run 'kubectl-tunnel-mistarget'  test_kubectl_tunnel_targets_the_hosts_apiserver_port \
  's@"%d:127.0.0.1:%d" % (kube_port, host.kube_port)@"%d:0.0.0.0:%d" % (kube_port, host.kube_port)@'

printf '\n== the local-exec branch of each verb ==\n'
run 'local-scp-drops-the-colon'  test_scp_becomes_a_local_copy_on_the_target \
  's@stripped = \[a\[1:\] if a.startswith(":") else a for a in args\]@stripped = list(args)@'
run 'local-tmux-loses-session'   test_tmux_attaches_locally_on_the_target \
  's@            return \["tmux", "new-session", "-A", "-s"\] + session@            return ["tmux", "new-session"]@'
run 'local-forward-silently-ok'  test_forward_is_refused_on_the_target_itself \
  's@        raise ValueError("`forward` is meaningless on the target host itself")@        return ["true"]@'

printf '\n== stream discipline and the JSON contract ==\n'
run 'report-pollutes-stdout'    test_the_report_goes_to_stderr_so_stdout_stays_pipeable \
  's@report_stream = sys.stdout if reporting_verb else sys.stderr@report_stream = sys.stdout@'
run 'json-drops-local-field'    test_json_shape_field_by_field \
  's@^        "local": local,$@@'

printf '\n== the positive control: a mutant already known to be covered ==\n'
run 'already-caught-control'    test_no_path_reachable_exits_3 \
  's@^EXIT_NO_PATH = 3@EXIT_NO_PATH = 9@'

printf '\n== the SURVIVES control: a behaviour-free edit must kill NOTHING ==\n'
run 'comment-only-edit'         SURVIVES \
  's@^# Exit codes$@# Exit-codes@'

printf '\n== the script was restored byte-identical ==\n'
NOW_SHA="$(sha256sum "$SCRIPT" | cut -d' ' -f1)"
if [ "$NOW_SHA" = "$ORIG_SHA" ]; then
  printf '  ok sha256 %s\n' "$NOW_SHA"
else
  printf '  🔴 RESTORE FAILED (%s != %s)\n' "$NOW_SHA" "$ORIG_SHA"
  FAILURES=$((FAILURES+1))
fi

printf '\nscope: %d mutant(s), enumerated in this file. NOT a claim about any\n' "$ROWS"
printf '       guard this file does not name — add a row when you add a guard.\n'
if [ "$FAILURES" -eq 0 ]; then
  echo "ALL OK (for the $ROWS mutant(s) above)"; exit 0
fi
echo "$FAILURES of $ROWS row(s) not ok"; exit 1
