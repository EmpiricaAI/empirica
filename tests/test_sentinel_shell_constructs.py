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

PLUGIN_HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


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


# ─── read-only sqlite3 classification (flag-robust) ─────────────────────────


class TestSqliteClassifier:
    """is_safe_sqlite_command must classify read-only sqlite3 as noetic even
    when display flags precede the db path (the old regex assumed the db path
    was the first arg, so `sqlite3 -header db "SELECT…"` got false-gated)."""

    DB = "/home/x/.empirica/workspace/workspace.db"

    @pytest.mark.parametrize(
        "cmd",
        [
            'sqlite3 {db} "SELECT * FROM t"',
            'sqlite3 {db} ".schema organizations"',
            'sqlite3 {db} "PRAGMA table_info(t)"',
            'sqlite3 {db} "WITH x AS (SELECT 1 AS n) SELECT n FROM x"',  # CTE read
            'sqlite3 -header {db} "SELECT a, b FROM t"',  # bare flag before path
            'sqlite3 -json {db} "SELECT 1"',
            'sqlite3 -line -readonly {db} "SELECT 1"',  # multiple bare flags
            "sqlite3 -separator ' | ' {db} \"SELECT a, b FROM t\"",  # value-flag
            'sqlite3 -header {db} "SELECT 1" 2>/dev/null',  # trailing redirect ignored
        ],
    )
    def test_reads_are_noetic(self, gate, cmd):
        assert gate.is_safe_sqlite_command(cmd.format(db=self.DB)) is True

    @pytest.mark.parametrize(
        "cmd",
        [
            'sqlite3 {db} "INSERT INTO t VALUES (1)"',
            'sqlite3 {db} "UPDATE t SET a=1"',
            'sqlite3 {db} "DELETE FROM t"',
            'sqlite3 {db} "DROP TABLE t"',
            'sqlite3 -header {db} "DELETE FROM t"',  # flag + write still blocked
            'sqlite3 {db} "WITH x AS (SELECT 1) DELETE FROM t"',  # writable CTE
            'sqlite3 {db} ".output /tmp/x"',  # file-writing meta
            "sqlite3 {db}",  # interactive REPL — no query
        ],
    )
    def test_writes_and_interactive_are_praxic(self, gate, cmd):
        assert gate.is_safe_sqlite_command(cmd.format(db=self.DB)) is False

    def test_end_to_end_via_is_safe_bash(self, gate):
        # The dispatch path callers actually hit.
        assert gate.is_safe_bash_command({"command": f'sqlite3 -header {self.DB} "SELECT 1"'}) is True
        assert gate.is_safe_bash_command({"command": f'sqlite3 {self.DB} "DROP TABLE t"'}) is False


# ─── command-substitution extractor ────────────────────────────────────────


class TestExtractSubstitutions:
    def test_dollar_paren_simple(self, gate):
        result = gate._extract_command_substitutions("PAUSED=$(empirica loop status foo)")
        assert result == ["empirica loop status foo"]

    def test_backtick_simple(self, gate):
        result = gate._extract_command_substitutions("X=`empirica loop status foo`")
        assert result == ["empirica loop status foo"]

    def test_dollar_paren_with_pipe(self, gate):
        # Real-world cron template form
        result = gate._extract_command_substitutions("PAUSED=$(empirica loop status foo --output json | jq -r .paused)")
        assert result == ["empirica loop status foo --output json | jq -r .paused"]

    def test_nested_substitutions(self, gate):
        # $(...) inside $(...)
        result = gate._extract_command_substitutions("X=$(echo $(empirica loop list))")
        # Outer captured; nested visible inside outer's payload
        assert len(result) == 1
        assert "empirica loop list" in result[0]

    def test_no_substitutions_returns_empty(self, gate):
        result = gate._extract_command_substitutions("empirica loop status foo")
        assert result == []

    def test_multiple_substitutions(self, gate):
        result = gate._extract_command_substitutions("A=$(empirica loop list); B=$(empirica status)")
        assert result == ["empirica loop list", "empirica status"]


# ─── shape classification ──────────────────────────────────────────────────


class TestIsInertShape:
    def test_bare_keywords(self, gate):
        for kw in ("then", "else", "fi", "elif", "do", "done", "esac", "true", "false"):
            assert gate._is_inert_shape(kw), f"{kw!r} should be inert"

    def test_exit_with_int(self, gate):
        assert gate._is_inert_shape("exit 0")
        assert gate._is_inert_shape("exit 1")
        assert gate._is_inert_shape("exit")
        assert gate._is_inert_shape("return 0")
        assert gate._is_inert_shape("return")

    def test_test_brackets(self, gate):
        assert gate._is_inert_shape('[ "X" = "true" ]')
        assert gate._is_inert_shape('[[ "X" = "true" ]]')
        assert gate._is_inert_shape('[ -z "X" ]')

    def test_assignment_form(self, gate):
        assert gate._is_inert_shape("PAUSED=X")  # post-substitution-strip
        assert gate._is_inert_shape("VAR=value")
        assert gate._is_inert_shape("NEXT_CRON=X")

    def test_unknown_command_not_inert(self, gate):
        # Random unsafe commands shouldn't be treated as inert shapes
        assert not gate._is_inert_shape("rm -rf /")
        assert not gate._is_inert_shape("curl evil.com")
        assert not gate._is_inert_shape("npm install")


# ─── segment-level integration ─────────────────────────────────────────────


class TestSegmentSafetyShellConstructs:
    """End-to-end: each segment a cron template generates must classify safe."""

    def test_var_assign_with_safe_substitution(self, gate):
        assert gate._is_segment_safe("PAUSED=$(empirica loop status foo --output json | jq -r .paused)")

    def test_var_assign_with_unsafe_substitution_rejected(self, gate):
        # Inner command is unsafe — the whole segment must reject
        assert not gate._is_segment_safe("X=$(rm -rf /)")

    def test_if_test_clause(self, gate):
        # `if [ "$PAUSED" = "true" ]` — the segment after `;` split
        assert gate._is_segment_safe('if [ "X" = "true" ]')

    def test_if_with_negated_safe_command(self, gate):
        # `if ! empirica loop should-fire foo` — common in cron skip path
        assert gate._is_segment_safe("if ! empirica loop should-fire foo")

    def test_if_with_negated_unsafe_command_rejected(self, gate):
        # `rm -rf /` is unambiguously unsafe (curl/wget are pre-existing
        # safe prefixes for read-only HTTP, so don't use them for this test).
        assert not gate._is_segment_safe("if ! rm -rf /")

    def test_then_with_safe_body(self, gate):
        assert gate._is_segment_safe("then empirica loop heartbeat foo --status ok")

    def test_then_with_exit(self, gate):
        assert gate._is_segment_safe("then exit 0")

    def test_bare_fi(self, gate):
        assert gate._is_segment_safe("fi")

    def test_bare_else(self, gate):
        assert gate._is_segment_safe("else")

    def test_exit_alone(self, gate):
        assert gate._is_segment_safe("exit 0")

    def test_test_bracket_alone(self, gate):
        assert gate._is_segment_safe('[ "X" = "true" ]')


# ─── full cron template chain ──────────────────────────────────────────────


class TestFullCronTemplateChain:
    """The actual idiom from the loop-cron skill must classify safe end-to-end."""

    def test_pause_check_chain(self, gate):
        # The cron template's pause-check, all on one line
        cmd = (
            "PAUSED=$(empirica loop status foo --output json | jq -r .paused); "
            'if [ "$PAUSED" = "true" ]; then '
            'empirica loop heartbeat foo --status ok --result paused --message "skipped"; '
            "exit 0; "
            "fi"
        )
        assert gate.is_safe_bash_command({"command": cmd})

    def test_should_fire_short_circuit(self, gate):
        cmd = "if ! empirica loop should-fire foo; then exit 0; fi"
        assert gate.is_safe_bash_command({"command": cmd})

    def test_unsafe_command_in_test_position_rejected(self, gate):
        # Test brackets are inert, but commands inside $() inside the
        # bracket must still validate.
        cmd = 'if [ "$(rm -rf /)" = "ok" ]; then exit 0; fi'
        assert not gate.is_safe_bash_command({"command": cmd})

    def test_chain_of_safe_empirica_commands(self, gate):
        cmd = "empirica loop register --name foo --kind cron && empirica loop heartbeat foo --status ok --result empty"
        assert gate.is_safe_bash_command({"command": cmd})

    def test_chain_with_one_unsafe_segment_rejected(self, gate):
        # Mixed chain — even one unsafe segment must reject the whole
        cmd = "empirica loop register --name foo --kind cron && rm -rf /"
        assert not gate.is_safe_bash_command({"command": cmd})


# ─── regression: prior-safe forms still safe ───────────────────────────────


class TestRegression:
    """Forms that were already safe before the shell-construct extension
    must remain safe — the new code only adds, never removes."""

    def test_bare_safe_empirica(self, gate):
        assert gate._is_segment_safe("empirica goals-list")
        assert gate._is_segment_safe("empirica loop status foo")

    def test_cd_command(self, gate):
        assert gate._is_segment_safe("cd /tmp/test")

    def test_unknown_command_still_unsafe(self, gate):
        assert not gate._is_segment_safe("random_unknown_command")

    def test_dangerous_command_still_unsafe(self, gate):
        assert not gate._is_segment_safe("rm -rf /")


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
        assert gate.is_safe_bash_command({"command": cmd})

    def test_real_gt_redirect_still_blocked(self, gate):
        assert gate._has_dangerous_redirects("cat foo > /etc/passwd")
        assert gate._has_dangerous_redirects("echo hi > out.txt")

    def test_real_lt_input_redirect_still_blocked(self, gate):
        assert gate._has_dangerous_redirects("cat input < /tmp/file")

    def test_append_redirect_still_blocked(self, gate):
        assert gate._has_dangerous_redirects("cmd >> append.log")

    def test_heredoc_input_still_safe(self, gate):
        # << EOF style heredocs are safe (input from stdin literal)
        cmd = "empirica preflight-submit - << 'EOF'\n{}\nEOF"
        assert not gate._has_dangerous_redirects(cmd)

    def test_stderr_redirect_still_safe(self, gate):
        cmd = "gh api foo 2>&1"
        assert not gate._has_dangerous_redirects(cmd)
        cmd = "gh api foo 2>/dev/null"
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
        self._set_work_type(gate, "remote-ops")
        assert gate.is_safe_bash_command({"command": "ssh host 'zsh -s' < /tmp/probe.sh"})

    def test_remote_ops_passes_compound_rsync(self, gate):
        self._set_work_type(gate, "remote-ops")
        assert gate.is_safe_bash_command({"command": "rsync -avz --delete src/ host:/dest/"})

    def test_non_remote_ops_still_classifies_ssh_per_command(self, gate):
        """The relaxation is gated on work_type — infra/config/debug + an
        unset work_type must still hit is_safe_remote_command."""
        for wt in (None, "infra", "config", "debug", "code"):
            self._set_work_type(gate, wt)
            assert not gate.is_safe_bash_command({"command": "ssh host 'zsh -s' < /tmp/probe.sh"}), (
                f"work_type={wt} unexpectedly passed a script-piped ssh"
            )

    def test_remote_ops_inline_ssh_read_still_safe(self, gate):
        """Backward-compat: clean inline read still passes (used to pass via
        is_safe_remote_command; now passes via the work_type short-circuit)."""
        self._set_work_type(gate, "remote-ops")
        assert gate.is_safe_bash_command({"command": "ssh host ls /opt"})

    def test_remote_ops_does_not_relax_local_writes(self, gate):
        """Local writes (cat > /tmp/foo) stay subject to normal gating —
        they ARE observable locally, no calibration reason to relax."""
        self._set_work_type(gate, "remote-ops")
        assert not gate.is_safe_bash_command({"command": "cat > /tmp/probe.sh"})

    def test_remote_ops_gets_infra_safe_prefixes(self, gate):
        """Secondary: remote-ops joins infra/config/debug in the L1295
        work-type expansion so system inspection (docker, systemctl, ss)
        flows for the local pre/post-SSH inspection step too."""
        self._set_work_type(gate, "remote-ops")
        # Use a command that's only safe via INFRA_SAFE_PREFIXES (docker is
        # a representative member).
        if any(p.startswith("docker") for p in gate.INFRA_SAFE_PREFIXES):
            assert gate.is_safe_bash_command({"command": "docker ps"})


class TestNewlineChainAndPipeOverAllow:
    """Newline-separated planning-verb chains are safe (David-flagged
    prop_7eswxsax over-gating bug); the fix must NOT widen the pipe over-allow
    (`empirica goals-list | sh`) and must NOT shred heredocs."""

    # ── the flagged over-gating repro: now ALLOWED ──────────────────────
    def test_newline_chain_of_planning_verbs_allowed(self, gate):
        cmd = (
            "GOAL_ID=abc\n"
            "empirica goals-add-task --goal-id $GOAL_ID --description 'one' 2>&1 | tail -2\n"
            "empirica goals-add-task --goal-id $GOAL_ID --description 'two' 2>&1 | tail -2"
        )
        assert gate.is_safe_bash_command({"command": cmd}) is True

    def test_newline_chain_plain_planning_verbs_allowed(self, gate):
        cmd = "empirica goals-create --objective 'a'\nempirica unknown-log --unknown 'b'"
        assert gate.is_safe_bash_command({"command": cmd}) is True

    def test_single_goals_add_task_markdown_pipe_description_allowed(self, gate):
        # `|` (markdown table) and newline live INSIDE the quoted description —
        # not chain/pipe operators. Must stay allowed (the rich-markdown case).
        cmd = 'empirica goals-add-task --goal-id X --description "## T\n| col | col |\n| a | b |"'
        assert gate.is_safe_bash_command({"command": cmd}) is True

    # ── security: the fix must NOT over-allow ───────────────────────────
    def test_newline_chain_with_rm_blocked(self, gate):
        cmd = "empirica goals-add-task --goal-id X --description 'a'\nrm -rf /tmp/foo"
        assert gate.is_safe_bash_command({"command": cmd}) is False

    def test_empirica_chained_to_rm_still_blocked(self, gate):
        assert gate.is_safe_bash_command({"command": "empirica goals-list && rm -rf /"}) is False

    def test_empirica_piped_to_sh_blocked(self, gate):
        # The pre-existing over-allow this fix closes: trailing pipe to an
        # executor must not pass on the bare empirica-prefix match.
        assert gate.is_safe_bash_command({"command": "empirica goals-list | sh"}) is False

    def test_empirica_piped_to_bash_blocked(self, gate):
        assert gate.is_safe_bash_command({"command": "empirica goals-list | bash"}) is False

    def test_newline_chain_with_piped_executor_blocked(self, gate):
        cmd = "empirica goals-add-task --goal-id X --description 'a'\nempirica goals-list | sh"
        assert gate.is_safe_bash_command({"command": cmd}) is False

    # ── still-allowed read-only pipes + heredocs (no regression) ────────
    def test_empirica_piped_to_tail_allowed(self, gate):
        assert gate.is_safe_bash_command({"command": "empirica goals-list | tail -2"}) is True

    def test_heredoc_preflight_not_shredded(self, gate):
        # `<<` present → newline-splitting is skipped so the multi-line JSON
        # body isn't shredded into bogus segments.
        cmd = "empirica preflight-submit - << 'EOF'\n{\"vectors\": {}}\nEOF"
        assert gate.is_safe_bash_command({"command": cmd}) is True


class TestNoeticAllowlistExpansion:
    """T1: expanded inert-tool allowlist + the per-tool write/exec-flag guards
    (find/fd/sort/yq/ast-grep/awk membrane-hole closures)."""

    # ── new inert prefixes flow free ────────────────────────────────────
    @pytest.mark.parametrize(
        "cmd",
        [
            "yq '.foo' config.yaml",
            "sort -rn data.txt",
            "sort",  # bare (stdin) — covered via prefix.rstrip() match
            "uniq -c",
            "cut -d, -f2 x.csv",
            "tr -d ' '",
            "nl file.py",
            "column -t",
            "rev",
            "tac log.txt",
            "xxd binary.bin",
            "od -c x",
            "strings ./a.out",
            "fd -e py",
            "fd pattern src/",
            "ast-grep -p 'def $F()' --lang py",
            "bat README.md",
            "tokei",
            "scc .",
            "gron data.json",
            "git rev-parse HEAD",
            "git for-each-ref refs/notes/",
            "git describe --tags",
            "git shortlog -sn",
            "git grep TODO",
            "git config --get user.name",
            "git config --list",
            "vulture empirica/",
            "pip-audit",
        ],
    )
    def test_new_inert_prefixes_allowed(self, gate, cmd):
        assert gate._matches_safe_prefix(cmd) is True
        assert gate.is_safe_bash_command({"command": cmd}) is True

    # ── write/exec flags GATED even though the tool name is safe-listed ──
    @pytest.mark.parametrize(
        "cmd",
        [
            "find . -delete",
            "find . -name '*.py' -delete",
            "find . -exec rm {} +",
            "find . -fls out.txt",
            "fd -e tmp -x rm",
            "fd pattern --exec rm",
            "sort -o out.txt in.txt",
            "sort --output=out.txt in.txt",
            "yq -i '.x=1' f.yaml",
            "yq --inplace '.x=1' f.yaml",
            "ast-grep --rewrite 'X' -p 'Y'",
            "ast-grep -U -p 'Y'",
            "awk 'BEGIN{system(\"rm -rf x\")}'",
            "awk '{print $1 > \"out.txt\"}' in",
            "awk '{print >> \"a\"}'",
            "gawk 'BEGIN{system(\"id\")}'",
        ],
    )
    def test_write_exec_flags_gated(self, gate, cmd):
        assert gate._has_dangerous_tool_flags(cmd) is True
        assert gate._matches_safe_prefix(cmd) is False
        assert gate.is_safe_bash_command({"command": cmd}) is False

    # ── no false-positives: inert variants of the guarded tools stay safe ─
    @pytest.mark.parametrize(
        "cmd",
        [
            "find . -type f -name '*.py'",
            "find . -maxdepth 2",
            "fd -e py --hidden",
            "sort -rn",
            "awk '$3 > 100'",  # numeric comparison, not a write
            "awk '{print $1, $2}'",  # print with no file redirect
            "awk '$1 > \"foo\"'",  # string comparison, no print-to-file
            "ast-grep -p 'def $F()'",
            "yq '.version' pyproject.toml",
        ],
    )
    def test_guard_no_false_positive(self, gate, cmd):
        assert gate._has_dangerous_tool_flags(cmd) is False
        assert gate._matches_safe_prefix(cmd) is True


class TestNoeticCompoundAndBootstrap:
    """Regression lock for ecodex's noetic-whitelist gap report (prop_3ucy5y7f).

    ecodex reported two shapes CHECK-gated on a stale-vendored sentinel-gate.py:
      (1) `cd <dir> && <noetic>` (grep / `empirica noetic-batch`) — allegedly
          gated because the whitelist "prefix-matches so a leading cd defeats it";
      (2) `empirica project-bootstrap` — allegedly treated praxic.

    Both are ALREADY noetic in current develop: `_classify_chain` segment-classifies
    &&/;/|| chains (a leading `cd` no longer defeats it — every segment is checked),
    and project-bootstrap is Tier-1. These tests lock that contract so the reported
    shapes can't silently regress, and stand as executable proof the fix is in-tree
    (the report was a stale vendored copy — re-vendor resolves it).
    """

    @pytest.mark.parametrize(
        "cmd",
        [
            "cd /home/u/proj && grep -rn pattern src/",
            "cd /home/u/proj && rg pattern",
            "cd /home/u/proj && empirica noetic-batch -",
            "cd /home/u/proj && empirica project-search --task 'foo'",
            "cd /home/u/proj && empirica project-bootstrap",
            "cd /home/u/proj && empirica goals-list",
        ],
    )
    def test_cd_then_noetic_is_safe(self, gate, cmd):
        # A leading `cd` must NOT defeat the whitelist — the chain is
        # segment-classified, so each segment (cd + the noetic op) is checked.
        assert gate.is_safe_bash_command({"command": cmd}) is True

    @pytest.mark.parametrize(
        "cmd",
        [
            "empirica project-bootstrap",
            "empirica project-bootstrap --output json",
            "empirica project-bootstrap --session-id abc123",
            "empirica noetic-batch -",
        ],
    )
    def test_bootstrap_and_noetic_batch_are_tier1(self, gate, cmd):
        assert gate.is_safe_empirica_command(cmd) is True
        assert gate.is_safe_bash_command({"command": cmd}) is True

    @pytest.mark.parametrize(
        "cmd",
        [
            "cd /home/u/proj && rm -rf /",
            "cd /home/u/proj && grep x file > /tmp/out",  # redirect (#256)
            "cd /home/u/proj && python3 -c 'import os; os.remove(\"x\")'",
            # Write-FLAG holes on otherwise-inert tools — a leading `cd` must not
            # launder these through a chain segment either (broccoli sweep guard:
            # locks the _matches_safe_prefix → _has_dangerous_tool_flags path at
            # the chain-segment integration level, not just the unit level).
            "cd /home/u/proj && find . -delete",
            "cd /home/u/proj && sed -i 's/a/b/' f",
            "cd /home/u/proj && yq -i '.x=1' f",
            "cd /home/u/proj && sort -o out in",
        ],
    )
    def test_leading_cd_does_not_smuggle_praxic(self, gate, cmd):
        # The security invariant behind the fix: segment-classification means a
        # safe leading `cd` can't launder an unsafe tail past the gate.
        assert gate.is_safe_bash_command({"command": cmd}) is False


# ─── pipe-target parity (read-only receivers) ───────────────────────────────


class TestPipeTargetParity:
    """A read-only tool the Sentinel trusts as a standalone command must be
    trusted as a pipe RECEIVER too. Pre-fix, SAFE_PIPE_TARGETS (~20 entries)
    was a fraction of SAFE_BASH_PREFIXES (~95), so `sqlite3 db 'SELECT…' |
    column -t` and friends got false-gated the moment a read-only formatter sat
    on the tail. The unified classifier reuses _matches_safe_prefix (which
    applies _has_dangerous_tool_flags), bringing receiver trust to parity while
    keeping mutation flags praxic in ANY pipe position.
    """

    @pytest.mark.parametrize(
        "cmd",
        [
            "sqlite3 /x/.empirica/sessions.db \"SELECT * FROM goals\" | column -t -s '|'",
            "grep foo file.py | nl",
            "cat data.json | gron",
            "git log --oneline | tac",
            "rg pattern src/ | bat",
            "cat f.bin | xxd | head",
            "git log | head | column -t",  # 3-stage read chain
            "cat f | wc -l",  # regression: previously-safe target
            "echo '{}' | jq .",  # regression: previously-safe target
        ],
    )
    def test_read_only_receivers_are_noetic(self, gate, cmd):
        assert gate.is_safe_pipe_chain(cmd) is True
        assert gate.is_safe_bash_command({"command": cmd}) is True

    @pytest.mark.parametrize(
        "cmd",
        [
            "find . -delete | cat",  # mutating SOURCE
            "cat f | sort -o out.txt",  # -o write flag mid-pipe (hole closed)
            "cat f | yq -i '.x=1'",  # in-place edit mid-pipe
            "cat f | awk 'system(\"rm x\")'",  # awk exec mid-pipe
            "python3 -c 'import os; os.system(\"x\")' | cat",  # exec SOURCE stays gated
            "grep x file | xargs rm",  # destructive fan-out
            "cat f | sh",  # shell executor
            "grep x file | column > out.txt",  # redirect on receiver
        ],
    )
    def test_mutating_or_exec_segments_stay_gated(self, gate, cmd):
        assert gate.is_safe_pipe_chain(cmd) is False

    def test_executor_receiver_stays_receiver_only(self, gate):
        # python3 -c is a legacy SAFE_PIPE_TARGET — trusted as a RECEIVER of
        # already-vetted data, but never as a pipe SOURCE (arbitrary exec).
        assert gate.is_safe_pipe_chain("cat f.json | python3 -c 'import sys; print(len(sys.stdin.read()))'") is True
        assert gate.is_safe_pipe_chain("python3 -c 'print(1)' | cat") is False


# ─── classifier parity + tier completeness (gardening-pass follow-ups) ───────


class TestClassifierParity:
    """Four over-gating gaps surfaced by the first epistemic-gardening pass, all
    the same 'read-only op trusted in one syntactic position but not another'
    class as the pipe-parity fix (#319):

      1. Help/version queries gate on non-tiered verbs (verb, not --help, decided).
      2. _is_segment_safe omitted sqlite + python -c (chain segments false-gated).
      3. Single-command path omitted inert shapes (bare `VAR=value` gated).
      4. finding-resolve / goals-archive / goals-reopen / goals-activate missing
         from the TIER2 workflow whitelist.
    """

    # 1. Help / version — inert regardless of verb, quote-aware.
    @pytest.mark.parametrize(
        "cmd",
        [
            "empirica goals-archive --help",
            "empirica finding-resolve --help",
            "empirica entity-delete X --help",  # non-tiered mutating verb + --help
            "empirica sync-push --version",
        ],
    )
    def test_help_version_queries_are_noetic(self, gate, cmd):
        assert gate.is_safe_empirica_command(cmd) is True
        assert gate.is_safe_bash_command({"command": cmd}) is True

    @pytest.mark.parametrize(
        "cmd",
        [
            'empirica entity-delete X --reason "run --help first"',  # --help INSIDE a quoted arg
            'empirica sync-push --force --note "--version bump"',  # --version inside a quote
        ],
    )
    def test_help_flag_in_quoted_arg_does_not_leak(self, gate, cmd):
        # A mutating, non-tiered verb must still gate — the help/version token is
        # inside a quoted argument, so shlex keeps it in a larger token (no match).
        assert gate.is_safe_empirica_command(cmd) is False

    # 2. sqlite + python -c as CHAIN SEGMENTS (no pipe).
    @pytest.mark.parametrize(
        "cmd",
        [
            'echo hi && sqlite3 db.db "SELECT 1"',
            'sqlite3 db.db "SELECT 1"; sqlite3 db.db "SELECT date(x,\'unixepoch\') FROM t"',
            "echo a && python3 -c 'print(1)'",
        ],
    )
    def test_readonly_sqlite_python_in_chain_segments_noetic(self, gate, cmd):
        assert gate.is_safe_bash_command({"command": cmd}) is True

    @pytest.mark.parametrize(
        "cmd",
        [
            'echo x && sqlite3 db.db "DELETE FROM t"',  # sqlite write in chain
            'echo x && sqlite3 db.db "DROP TABLE t"',
            "echo x && python3 -c 'import os; os.remove(\"f\")'",  # mutating python in chain
        ],
    )
    def test_mutating_sqlite_python_in_chain_still_gated(self, gate, cmd):
        assert gate.is_safe_bash_command({"command": cmd}) is False

    # 3. Inert shapes standalone (parity with chain-segment path).
    @pytest.mark.parametrize("cmd", ["DB=.empirica/x.db", "[ -f foo ]", "exit 0"])
    def test_inert_shapes_standalone_noetic(self, gate, cmd):
        assert gate.is_safe_bash_command({"command": cmd}) is True

    def test_assignment_then_mutation_still_gated(self, gate):
        assert gate.is_safe_bash_command({"command": "DB=x && rm file"}) is False

    # 4. TIER2 workflow-verb completeness.
    @pytest.mark.parametrize(
        "verb",
        ["finding-resolve", "goals-archive", "goals-reopen", "goals-activate"],
    )
    def test_lifecycle_verbs_are_tiered(self, gate, verb):
        # These are epistemic-workflow verbs (siblings unknown-resolve / goals-complete
        # / goals-prune are all TIER2) — they flow without a CHECK like the rest.
        assert gate.is_safe_empirica_command(f"empirica {verb} --older-than 30") is True
