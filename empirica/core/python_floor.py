"""The oldest Python empirica runs on: pyproject's requires-python, for the checks that run before or outside pip.

3.11.5, not 3.11: soupsieve 4 (a dependency since 1.16.2) needs it, and on 3.11.0-3.11.4 pip silently resolved an older
empirica instead (2026-11-19). tests/test_python_floor_everywhere.py keeps this, pyproject and the installers in step.
"""

MIN_PYTHON: tuple[int, int, int] = (3, 11, 5)
MIN_PYTHON_TEXT = ".".join(map(str, MIN_PYTHON))
