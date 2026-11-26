"""Goal-work detection, fourth review pass for 1.17.20: no global line joining, escaped `\\$'` is not an ANSI quote."""

from __future__ import annotations

import pytest

from empirica.core import sentinel_discipline as sd


@pytest.mark.parametrize(
    "command",
    [
        "cat > s.sh <<'EOF'\nfoo \\\nEOF\nempirica goals-create --objective x",  # a quoted heredoc ends at EOF, unjoined
        "empirica goals-create --objective x \\\\\nls --help",  # an escaped backslash keeps its newline as a separator
        "echo \\$'foo\\' ; empirica goals-create --objective 'x'",  # \$' is a plain $ and an ordinary quote
        "empirica \\\ngoals-create --objective x",  # a continuation between the two words is still one command
    ],
)
def test_detected(command):
    assert sd._goal_work(command) == "an empirica goal"


def test_an_even_run_of_backslashes_leaves_the_ansi_quote_real():
    """`\\\\$'a\\'b'`: the backslashes escape each other, so $'...' is ANSI and the goal after it runs (fifth pass)."""
    assert sd._goal_work("echo \\\\$'a\\'b' ; empirica goals-create x ; echo 'c'") == "an empirica goal"


@pytest.mark.parametrize(
    "command",
    [
        "empirica goals-create --objective x \\\n --status planned",
    ],
)
def test_flags_on_a_continued_line_belong_to_the_statement(command):
    assert sd._goal_work(command) is None


@pytest.mark.parametrize(
    "command",
    [
        "empirica goals-create \\\n --help",  # --help is exempt only as the first argument on the verb's line
        'empirica goals-create \\\n - <<\'EOF\'\n{"status": "planned"}\nEOF',  # stdin is not read
    ],
)
def test_narrow_exemptions_over_detect_the_rest(command):
    assert sd._goal_work(command) is not None


@pytest.mark.parametrize(
    "command, expected",
    [
        ("empirica goals-create x # note \\\n--status planned", "an empirica goal"),  # a comment continues nothing
        ("empirica goals-create x # --status planned", "an empirica goal"),  # planned in a comment does not count
        ("# empirica goals-create x\nls", "an empirica goal"),  # over-detection by design: comments never hide a verb
        ("echo a#b; empirica goals-create x", "an empirica goal"),  # # inside a word is not a comment
        ("echo a\\ #b; empirica goals-create a", "an empirica goal"),  # an escaped space before # (seventh pass)
        ("echo a\\;#b; empirica goals-create a", "an empirica goal"),
        ("echo ${x:- #}; empirica goals-create a", "an empirica goal"),  # # inside ${...}
        ("echo ${x/ #/y}; empirica goals-create a", "an empirica goal"),
    ],
)
def test_comments_only_withhold_exemptions(command, expected):
    assert sd._goal_work(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "echo hi # don't\nempirica goals-create x\necho 'bye'",  # a quote in a real comment opens nothing (eighth pass)
        "ls # see <<EOF\nempirica goals-create x",
        'ls # a "quote\nempirica goals-create x\necho "z"',
        "# it's\nempirica goals-add-task --goal-id g --description d\n# that's it",
        "ls ; # x'\nempirica goals-create x --status active\n'",
    ],
)
def test_a_quote_inside_a_comment_hides_nothing(command):
    assert sd._goal_work(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        'OUT="$(cd "/tmp" && empirica goals-create --objective x)"',  # ninth pass: quotes inside "$(...)"
        'empirica goals-create --objective x --description "Fix \\`x\\` so --help works"',
        'empirica goals-create --objective x --description "a \\`y\\` use --status planned"',
        "cat <<EOF\n$(empirica goals-create --objective x)\nEOF",  # an unquoted heredoc runs substitutions
        "cat <<EOF\n`empirica goals-create --objective x`\nEOF",
        "bash -lc 'empirica goals-create --objective x'",
        "bash -o pipefail -c 'empirica goals-create --objective x'",
        "eval 'empirica goals-create --objective x'",
        "bash <<'EOF'\nempirica goals-create --objective x\nEOF",  # a heredoc fed to a shell is a script
        'cat <<E"OF"\nx\nEOF\nempirica goals-create --objective x',
        "echo $((1<<x)); empirica goals-create --objective x",
        'X="$(empirica goals-create --objective x)" --status planned',
        "empirica goals-create --objective x & echo --status planned",
        "empirica goals-create --objective x\\ --help",
        "empirica goals-create --objective x --status planned --status active",  # the later --status wins
        "empirica goals-create --objective x--status planned",
        "echo it\\'s; empirica goals-create x; echo 'y'",
    ],
)
def test_ninth_pass_misses_are_detected(command):
    assert sd._goal_work(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "rg -n 'empirica goals-create' docs",  # inert single quotes
        'git commit -m "docs: mention empirica goals-create"',  # inert double quotes
        "cat <<'EOF' > n.md\nempirica goals-create --objective x\nEOF",  # quoted heredoc delimiter
        "empirica goals-create --help",
        "empirica goals-create --objective x --status planned",
        'empirica goals-create --objective "x" --status "planned"',
        "empirica goals-create --objective x --status=planned",
    ],
)
def test_the_narrow_exemptions_still_hold(command):
    assert sd._goal_work(command) is None


@pytest.mark.parametrize(
    "command",
    [
        'cd "$HOME" && empirica goals-create --objective "Fix the parser"',  # tenth pass, the common one
        'G="$(date)"; empirica goals-create --objective "Next"',
        "echo \"Done in $PWD, let's plan\"; empirica goals-create --objective 'x'",
        "echo 'fix\n\nbody' && empirica goals-create --objective 'Next'",  # a quote spanning lines
        "echo 'a\nx 'empirica goals-create' y'",  # line 2 runs the verb in bash
        "python3 -c 'import os; os.system(\"empirica goals-create --objective x\")'",
        "python3 - <<'EOF'\nimport subprocess; subprocess.run('empirica goals-create --objective x', shell=True)\nEOF",
        "cat > f <<'EOF'\nempirica goals-create --objective x\nEOF\nsource f",
        "cat > f <<'EOF'\nempirica goals-create --objective x\nEOF\n. f",
        "empirica goals-create --objective x --status planned --stat in_progress",
        "echo \"<<'EOF'\"\nempirica goals-create --objective x\nEOF",
    ],
)
def test_tenth_pass_misses_are_detected(command):
    assert sd._goal_work(command) is not None
