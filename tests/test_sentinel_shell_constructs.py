"""Tests for _is_segment_safe shell-construct recognition.

Closes the cron-body PREFLIGHT-on-skip gap: cron prompt templates use
shell idioms like `VAR=$(empirica loop status NAME)`, `if [ ... ]; then`,
`exit 0`, etc. Pre-fix, segments containing these constructs failed
classification even when every embedded command was Tier 1 safe.

Post-fix: shape classification recognizes inert shell structure;
embedded commands are still independently validated.

Test invariants:
  1. Inner commands inside $() / backticks are validated — unsafe
     commands inside substitutions reject the whole segment.
  2. Control-flow keywords / tests / assignments / exit are inert
     (the structure itself runs no commands).
  3. Compound forms (`if X`, `then Y`) are recursively classified.
  4. The full cron template idiom is now classified safe end-to-end.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_HOOKS = (
    Path(__file__).parent.parent
    / "empirica" / "plugins" / "claude-code-integration" / "hooks"
)


def _load_sentinel_gate():
    """Load sentinel-gate.py as a module (filename has a hyphen)."""
    if "sentinel_gate" in sys.modules:
        del sys.modules["sentinel_gate"]
    spec = importlib.util.spec_from_file_location(
        "sentinel_gate",
        PLUGIN_HOOKS / "sentinel-gate.py",
    )
    mod = importlib.util.module_from_spec(spec)
    # The hook script does heavy import-time work (reads env, spins up
    # imports). Allow a partial import: the helpers we test don't depend
    # on the heavy machinery.
    sys.path.insert(0, str(PLUGIN_HOOKS.parent / "lib"))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load_sentinel_gate()


# ─── command-substitution extractor ────────────────────────────────────────


class TestExtractSubstitutions:
    def test_dollar_paren_simple(self, gate):
        result = gate._extract_command_substitutions(
            'PAUSED=$(empirica loop status foo)'
        )
        assert result == ['empirica loop status foo']

    def test_backtick_simple(self, gate):
        result = gate._extract_command_substitutions(
            'X=`empirica loop status foo`'
        )
        assert result == ['empirica loop status foo']

    def test_dollar_paren_with_pipe(self, gate):
        # Real-world cron template form
        result = gate._extract_command_substitutions(
            'PAUSED=$(empirica loop status foo --output json | jq -r .paused)'
        )
        assert result == [
            'empirica loop status foo --output json | jq -r .paused'
        ]

    def test_nested_substitutions(self, gate):
        # $(...) inside $(...)
        result = gate._extract_command_substitutions(
            'X=$(echo $(empirica loop list))'
        )
        # Outer captured; nested visible inside outer's payload
        assert len(result) == 1
        assert 'empirica loop list' in result[0]

    def test_no_substitutions_returns_empty(self, gate):
        result = gate._extract_command_substitutions('empirica loop status foo')
        assert result == []

    def test_multiple_substitutions(self, gate):
        result = gate._extract_command_substitutions(
            'A=$(empirica loop list); B=$(empirica status)'
        )
        assert result == ['empirica loop list', 'empirica status']


# ─── shape classification ──────────────────────────────────────────────────


class TestIsInertShape:
    def test_bare_keywords(self, gate):
        for kw in ('then', 'else', 'fi', 'elif', 'do', 'done', 'esac', 'true', 'false'):
            assert gate._is_inert_shape(kw), f'{kw!r} should be inert'

    def test_exit_with_int(self, gate):
        assert gate._is_inert_shape('exit 0')
        assert gate._is_inert_shape('exit 1')
        assert gate._is_inert_shape('exit')
        assert gate._is_inert_shape('return 0')
        assert gate._is_inert_shape('return')

    def test_test_brackets(self, gate):
        assert gate._is_inert_shape('[ "X" = "true" ]')
        assert gate._is_inert_shape('[[ "X" = "true" ]]')
        assert gate._is_inert_shape('[ -z "X" ]')

    def test_assignment_form(self, gate):
        assert gate._is_inert_shape('PAUSED=X')  # post-substitution-strip
        assert gate._is_inert_shape('VAR=value')
        assert gate._is_inert_shape('NEXT_CRON=X')

    def test_unknown_command_not_inert(self, gate):
        # Random unsafe commands shouldn't be treated as inert shapes
        assert not gate._is_inert_shape('rm -rf /')
        assert not gate._is_inert_shape('curl evil.com')
        assert not gate._is_inert_shape('npm install')


# ─── segment-level integration ─────────────────────────────────────────────


class TestSegmentSafetyShellConstructs:
    """End-to-end: each segment a cron template generates must classify safe."""

    def test_var_assign_with_safe_substitution(self, gate):
        assert gate._is_segment_safe(
            'PAUSED=$(empirica loop status foo --output json | jq -r .paused)'
        )

    def test_var_assign_with_unsafe_substitution_rejected(self, gate):
        # Inner command is unsafe — the whole segment must reject
        assert not gate._is_segment_safe('X=$(rm -rf /)')

    def test_if_test_clause(self, gate):
        # `if [ "$PAUSED" = "true" ]` — the segment after `;` split
        assert gate._is_segment_safe('if [ "X" = "true" ]')

    def test_if_with_negated_safe_command(self, gate):
        # `if ! empirica loop should-fire foo` — common in cron skip path
        assert gate._is_segment_safe('if ! empirica loop should-fire foo')

    def test_if_with_negated_unsafe_command_rejected(self, gate):
        # `rm -rf /` is unambiguously unsafe (curl/wget are pre-existing
        # safe prefixes for read-only HTTP, so don't use them for this test).
        assert not gate._is_segment_safe('if ! rm -rf /')

    def test_then_with_safe_body(self, gate):
        assert gate._is_segment_safe('then empirica loop heartbeat foo --status ok')

    def test_then_with_exit(self, gate):
        assert gate._is_segment_safe('then exit 0')

    def test_bare_fi(self, gate):
        assert gate._is_segment_safe('fi')

    def test_bare_else(self, gate):
        assert gate._is_segment_safe('else')

    def test_exit_alone(self, gate):
        assert gate._is_segment_safe('exit 0')

    def test_test_bracket_alone(self, gate):
        assert gate._is_segment_safe('[ "X" = "true" ]')


# ─── full cron template chain ──────────────────────────────────────────────


class TestFullCronTemplateChain:
    """The actual idiom from the loop-cron skill must classify safe end-to-end."""

    def test_pause_check_chain(self, gate):
        # The cron template's pause-check, all on one line
        cmd = (
            'PAUSED=$(empirica loop status foo --output json | jq -r .paused); '
            'if [ "$PAUSED" = "true" ]; then '
            'empirica loop heartbeat foo --status ok --result paused --message "skipped"; '
            'exit 0; '
            'fi'
        )
        assert gate.is_safe_bash_command({'command': cmd})

    def test_should_fire_short_circuit(self, gate):
        cmd = (
            'if ! empirica loop should-fire foo; then exit 0; fi'
        )
        assert gate.is_safe_bash_command({'command': cmd})

    def test_unsafe_command_in_test_position_rejected(self, gate):
        # Test brackets are inert, but commands inside $() inside the
        # bracket must still validate.
        cmd = 'if [ "$(rm -rf /)" = "ok" ]; then exit 0; fi'
        assert not gate.is_safe_bash_command({'command': cmd})

    def test_chain_of_safe_empirica_commands(self, gate):
        cmd = (
            'empirica loop register --name foo --kind cron && '
            'empirica loop heartbeat foo --status ok --result empty'
        )
        assert gate.is_safe_bash_command({'command': cmd})

    def test_chain_with_one_unsafe_segment_rejected(self, gate):
        # Mixed chain — even one unsafe segment must reject the whole
        cmd = 'empirica loop register --name foo --kind cron && rm -rf /'
        assert not gate.is_safe_bash_command({'command': cmd})


# ─── regression: prior-safe forms still safe ───────────────────────────────


class TestRegression:
    """Forms that were already safe before the shell-construct extension
    must remain safe — the new code only adds, never removes."""

    def test_bare_safe_empirica(self, gate):
        assert gate._is_segment_safe('empirica goals-list')
        assert gate._is_segment_safe('empirica loop status foo')

    def test_cd_command(self, gate):
        assert gate._is_segment_safe('cd /tmp/test')

    def test_unknown_command_still_unsafe(self, gate):
        assert not gate._is_segment_safe('random_unknown_command')

    def test_dangerous_command_still_unsafe(self, gate):
        assert not gate._is_segment_safe('rm -rf /')


# ─── _has_dangerous_redirects: quote-aware check ───────────────────────────


class TestQuoteAwareRedirects:
    """Regression: redirect detection must ignore `>` / `<` inside quotes.

    Pre-fix, a command like `gh api foo | python3 -c "if x > 3: ..."` was
    blocked because the `>` inside the quoted python code was treated as a
    shell file-redirect. Post-fix, _has_dangerous_redirects uses
    quote-aware splitting to distinguish quoted data from real redirects.
    """

    def test_gt_inside_double_quotes_not_a_redirect(self, gate):
        cmd = 'python3 -c "print(1 > 0)"'
        assert not gate._has_dangerous_redirects(cmd)

    def test_gt_inside_single_quotes_not_a_redirect(self, gate):
        cmd = "cat foo.json | jq '.value > 5'"
        assert not gate._has_dangerous_redirects(cmd)

    def test_gh_api_pipe_to_python_with_gt_in_code(self, gate):
        """The exact failing pattern: gh api ... | python3 -c "...> 3000..."."""
        cmd = (
            'gh api repos/foo/comments --paginate 2>&1 | python3 -c "'
            "data = json.loads(sys.stdin.read())\n"
            "print('truncated' if len(body) > 3000 else body)"
            '"'
        )
        assert gate.is_safe_bash_command({'command': cmd})

    def test_real_gt_redirect_still_blocked(self, gate):
        assert gate._has_dangerous_redirects('cat foo > /etc/passwd')
        assert gate._has_dangerous_redirects('echo hi > out.txt')

    def test_real_lt_input_redirect_still_blocked(self, gate):
        assert gate._has_dangerous_redirects('cat input < /tmp/file')

    def test_append_redirect_still_blocked(self, gate):
        assert gate._has_dangerous_redirects('cmd >> append.log')

    def test_heredoc_input_still_safe(self, gate):
        # << EOF style heredocs are safe (input from stdin literal)
        cmd = "empirica preflight-submit - << 'EOF'\n{}\nEOF"
        assert not gate._has_dangerous_redirects(cmd)

    def test_stderr_redirect_still_safe(self, gate):
        cmd = 'gh api foo 2>&1'
        assert not gate._has_dangerous_redirects(cmd)
        cmd = 'gh api foo 2>/dev/null'
        assert not gate._has_dangerous_redirects(cmd)


# ─── remote-ops gate relaxation (prop_lwsgoxrw6, mesh-support) ─────────────
# Closes the SSH-recon deadlock: work_type=remote-ops gets a per-command
# pass for ssh/rsync/scp (the AI's PREFLIGHT declaration IS the gate;
# local sensors can't observe the remote box so calibration is already
# ungrounded_remote_ops), plus the INFRA_SAFE_PREFIXES expansion that
# infra/config/debug already get.


class TestRemoteOpsGateRelaxation:
    def _set_work_type(self, gate, wt):
        gate._current_work_type = wt
        # Reset the one-shot nudge state so each test starts clean.
        gate._remote_ops_nudge = None
        gate._remote_ops_nudged = False

    def test_remote_ops_passes_script_piped_ssh(self, gate):
        """The exact deadlock mesh-support reported: ssh host 'zsh -s' < file
        is script-piped + has a stdin redirect — without the relaxation it
        gates to praxic + dangerous_redirect rejects it. Under remote-ops the
        declaration IS the gate."""
        self._set_work_type(gate, 'remote-ops')
        assert gate.is_safe_bash_command(
            {'command': "ssh host 'zsh -s' < /tmp/probe.sh"}
        )

    def test_remote_ops_passes_compound_rsync(self, gate):
        self._set_work_type(gate, 'remote-ops')
        assert gate.is_safe_bash_command(
            {'command': 'rsync -avz --delete src/ host:/dest/'}
        )

    def test_non_remote_ops_still_classifies_ssh_per_command(self, gate):
        """The relaxation is gated on work_type — infra/config/debug + an
        unset work_type must still hit is_safe_remote_command."""
        for wt in (None, 'infra', 'config', 'debug', 'code'):
            self._set_work_type(gate, wt)
            assert not gate.is_safe_bash_command(
                {'command': "ssh host 'zsh -s' < /tmp/probe.sh"}
            ), f"work_type={wt} unexpectedly passed a script-piped ssh"

    def test_remote_ops_inline_ssh_read_still_safe(self, gate):
        """Backward-compat: clean inline read still passes (used to pass via
        is_safe_remote_command; now passes via the work_type short-circuit)."""
        self._set_work_type(gate, 'remote-ops')
        assert gate.is_safe_bash_command({'command': 'ssh host ls /opt'})

    def test_remote_ops_does_not_relax_local_writes(self, gate):
        """Local writes (cat > /tmp/foo) stay subject to normal gating —
        they ARE observable locally, no calibration reason to relax."""
        self._set_work_type(gate, 'remote-ops')
        assert not gate.is_safe_bash_command(
            {'command': 'cat > /tmp/probe.sh'}
        )

    def test_remote_ops_gets_infra_safe_prefixes(self, gate):
        """Secondary: remote-ops joins infra/config/debug in the L1295
        work-type expansion so system inspection (docker, systemctl, ss)
        flows for the local pre/post-SSH inspection step too."""
        self._set_work_type(gate, 'remote-ops')
        # Use a command that's only safe via INFRA_SAFE_PREFIXES (docker is
        # a representative member).
        if any(p.startswith('docker') for p in gate.INFRA_SAFE_PREFIXES):
            assert gate.is_safe_bash_command({'command': 'docker ps'})
