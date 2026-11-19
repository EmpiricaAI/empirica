"""One Python floor everywhere: pyproject's requires-python, the checks doctor and diagnose run, and both installers.

The 1.17.11 sweep found doctor, diagnose, setup-claude-code and install.sh still passing 3.10 after the floor moved to 3.11.5.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from empirica.core.python_floor import MIN_PYTHON, MIN_PYTHON_TEXT

_ROOT = Path(__file__).resolve().parent.parent


def test_the_constant_is_pyprojects_floor():
    text = (_ROOT / "pyproject.toml").read_text()
    assert re.search(r'requires-python\s*=\s*">=([\d.]+)"', text).group(1) == MIN_PYTHON_TEXT


def test_doctor_and_diagnose_fail_below_the_floor(monkeypatch):
    from empirica.cli.command_handlers import diagnose, doctor

    below = (MIN_PYTHON[0], MIN_PYTHON[1], MIN_PYTHON[2] - 1, "final", 0)
    monkeypatch.setattr(sys, "version_info", _vi(below))
    assert doctor.check_python().status == doctor.FAIL
    assert diagnose.check_python_version().status == diagnose.FAIL


def _vi(t):
    from collections import namedtuple

    return namedtuple("version_info", "major minor micro releaselevel serial")(*t)


def test_install_sh_states_the_same_floor_and_compares_micro_versions(tmp_path):
    script = (_ROOT / "empirica/plugins/claude-code-integration/install.sh").read_text()
    assert f'MIN_PYTHON="{MIN_PYTHON_TEXT}"' in script and "3.10" not in script
    check = (
        re.search(r'if "\$py" -c "(.+?)" 2>/dev/null; then', script).group(1).replace("$MIN_PYTHON", MIN_PYTHON_TEXT)
    )
    assert (
        subprocess.run([sys.executable, "-c", check], check=False).returncode == 0
    )  # this interpreter is at the floor or above
