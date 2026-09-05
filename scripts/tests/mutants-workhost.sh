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
# 139 tests collect today, NONE deselected. The floor sits well under that so it
# catches a suite that failed to collect, not routine growth. (This number is
# re-derived, not remembered: `python3 -m pytest --collect-only -q` on the suite.)
MIN_TESTS=125

# 🔴 No deselect. An earlier version of this file excluded
# `test_workhost_is_tracked_by_git` and justified it with "it asserts about the
# REAL repository, so it cannot pass against a .git-less copy". That stopped
# being true at 041cd4db, which gave the test two arms — with `.git` it asks git,
# without `.git` it asserts the file is present at all, which is the flake's
# tracked-files-only copy proving the same thing. It passes here, so the
# exclusion is gone rather than left carrying a stale reason.
RELSUITE="scripts/tests/test_workhost.py"

failing() {
  local out n f total
  out="$(cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$RELSUITE" \
    -q --no-header --tb=no -p no:cacheprovider 2>/dev/null)"
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
  's@^        if result.state not in acceptable:@        if False:@'

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

printf '\n== the fourth state: an untrusted host key is not an unreachable host ==\n'
run 'untrusted-key-collapsed'   test_untrusted_key_is_its_own_json_value \
  's@^UNTRUSTED_KEY = "untrusted-key"@UNTRUSTED_KEY = "unreachable"@'
run 'hostkey-missing-unclassified' test_a_host_key_refusal_is_untrusted_key_not_unreachable \
  's@^    if HOSTKEY_MISSING_MARKER in stderr:@    if False:@'
# The OTHER direction: ssh exits 255 for auth failure too, so a classifier that
# calls everything a host-key problem is just the old conflation reversed.
run 'auth-called-a-key-problem'  test_an_auth_failure_is_unreachable_not_untrusted_key \
  's@^    return UNREACHABLE, None, False@    return UNTRUSTED_KEY, "host key for alias %r not in known_hosts", False@'
run 'changed-key-loses-its-detail' test_a_changed_host_key_is_untrusted_key_with_its_own_detail \
  's@^            "host key for alias %r CHANGED since it was trusted",@            "host key for alias %r not in known_hosts",@'
run 'probe-gains-tofu'          test_the_probe_never_enables_tofu \
  's@\["ssh", "-o", "BatchMode=yes"\]@["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]@'

printf '\n== the report names the cause and the way out ==\n'
run 'advice-block-silenced'     test_the_report_names_the_cause_and_the_way_out \
  's@^    untrusted = \[r for r in results if r.state == UNTRUSTED_KEY\]@    untrusted = []@'
run 'advice-fires-when-fine'    test_no_advice_is_printed_when_a_path_is_actually_usable \
  's@^    if chosen is not None:@    if False:@'
run 'changed-key-offered-tofu'  test_a_changed_key_is_not_offered_the_accept_key_shortcut \
  's@^    unseen = \[r for r in untrusted if not r.key_changed\]@    unseen = list(untrusted)@'

printf '\n== the bootstrap escape hatch, and its limits ==\n'
run 'accept-key-inert'          test_accept_key_lets_an_untrusted_path_be_used \
  's@^    acceptable = (OK, UNTRUSTED_KEY) if accept_key else (OK,)@    acceptable = (OK,)@'
run 'accept-key-too-wide'       test_accept_key_does_not_make_an_unreachable_path_usable \
  's@^    acceptable = (OK, UNTRUSTED_KEY) if accept_key else (OK,)@    acceptable = (OK, UNTRUSTED_KEY, UNREACHABLE) if accept_key else (OK,)@'

printf '\n== a forward that reports success must actually forward ==\n'
run 'forward-bind-failure-ok'   test_forward_builds_a_dash_L_tunnel \
  's@return cmd + FORWARD_FAILURE_OPTS + opts + \[address\]@return cmd + opts + [address]@'
run 'kubectl-bind-failure-ok'   test_the_kubectl_tunnel_also_exits_on_a_failed_bind \
  's@^            + FORWARD_FAILURE_OPTS$@            + []@'
run 'forward-spec-unvalidated'  test_forward_with_no_spec_is_refused_rather_than_forwarding_nothing \
  's@^        validate_forward_spec(spec)@        pass@'
run 'forward-shape-unchecked'   test_forward_rejects_a_spec_that_is_not_a_port_forward \
  's@^        if len(parts) != 3 or not parts\[0\].isdigit() or not parts\[2\].isdigit():@        if False:@'
run 'forward-rejects-bind-addr' test_forward_accepts_an_explicit_bind_address \
  's@^        if len(parts) == 4:@        if False:@'

printf '\n== the wrong machine is worse than no machine ==\n'
run 'lan-fallback-unconditional' test_the_lan_address_alone_does_not_prove_we_are_the_target \
  's@^    if host.lan and not host.nebula and host.lan in local:@    if host.lan and host.lan in local:@'
run 'lan-fallback-removed'      test_the_lan_address_is_accepted_when_the_host_has_no_nebula_address \
  's@^    if host.lan and not host.nebula and host.lan in local:@    if False:@'

printf '\n== crashing is not reporting ==\n'
run 'local-kubectl-crash'       test_kubectl_without_a_local_kubectl_exits_127_not_a_traceback \
  's@^            return 127$@            raise@'
run 'tailscale-non-object'      test_a_non_object_tailscale_status_is_not_configured_not_a_crash \
  's@^    if not isinstance(status, dict):@    if False:@'
run 'missing-ssh-blamed-on-net' test_no_ssh_binary_is_not_configured_not_unreachable \
  's@^            path, NOT_CONFIGURED, address, "no ssh binary on PATH", elapsed()@            path, UNREACHABLE, address, "no ssh binary on PATH", elapsed()@'
run 'env-timeout-unguarded'     test_a_junk_workhost_timeout_warns_and_still_runs \
  's@^    except (TypeError, ValueError):@    except ():@'
run 'env-timeout-always-default' test_a_valid_workhost_timeout_is_still_honoured \
  's@^    return value$@    return default@'
run 'timeout-help-hides-the-var' test_the_timeout_flag_documents_the_env_var \
  's@(default: %g, or \$WORKHOST_TIMEOUT)@(default: %g)@'

printf '\n== --dry-run prints what actually runs ==\n'
run 'dry-run-space-joined'      test_dry_run_quotes_arguments_containing_spaces \
  's@^        sys.stdout.write(shlex.join(cmd) + "\\n")$@        sys.stdout.write(" ".join(cmd) + "\\n")@'
run 'kubectl-hides-placeholder' test_the_kubectl_dry_run_admits_its_port_is_a_placeholder \
  's@is a placeholder@is chosen later@'

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
