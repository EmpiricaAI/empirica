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
