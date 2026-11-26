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
        "empirica goals-create \\\n --help",
        'empirica goals-create \\\n - <<\'EOF\'\n{"status": "planned"}\nEOF',
    ],
)
def test_flags_on_a_continued_line_belong_to_the_statement(command):
    assert sd._goal_work(command) is None
