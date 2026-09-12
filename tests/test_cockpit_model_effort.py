"""Transcript reading is opt-in, and an unobserved value is never guessed.

The cockpit's model/effort columns are sourced from a seat's Claude Code
transcript — the one fleet fact no structured surface reports. Two properties
carry the whole design, and neither was covered by the tenant-local original
(prop_ycat73pyavc5vaj2mxsfpm7s2q shipped with `tests_run: false`):

1. **The gate withholds access, not just output.** With
   `cockpit.read_transcripts` unset, nothing may be opened — a version that
   read the file and then discarded the values would satisfy a
   returns-None assertion while doing exactly what the flag forbids. So the
   test asserts on the filesystem call, not the return value.
2. **Every failure renders as not-observed.** An invented model name is
   indistinguishable from an observed one, which is the failure this column
   exists to avoid, so all failure paths must reach `(None, None)`.

Each absence assertion here is paired with a positive control — a test that
the same call returns a real value when it should. Absence proved through an
instrument never shown to be live is not evidence.
"""

from __future__ import annotations

import json

import pytest

from empirica.core.cockpit import model_effort as me
from empirica.core.cockpit.project_cockpit_config import transcript_reading_enabled


@pytest.fixture(autouse=True)
def _clear_cache():
    me._CACHE.clear()
    yield
    me._CACHE.clear()


def _make_project(tmp_path, *, read_transcripts: bool | None, records: list[dict] | None):
    """A project dir + its mangled transcript dir, wired through the module's
    own path convention rather than a hand-built one."""
    proj = tmp_path / "proj"
    (proj / ".empirica").mkdir(parents=True)
    cfg = "cockpit:\n  loops: []\n"
    if read_transcripts is not None:
        cfg = f"cockpit:\n  read_transcripts: {str(read_transcripts).lower()}\n"
    (proj / ".empirica" / "project.yaml").write_text(cfg)

    if records is not None:
        tdir = tmp_path / "projects" / str(proj).replace("/", "-")
        tdir.mkdir(parents=True)
        (tdir / "session.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return proj


@pytest.fixture
def projects_root(tmp_path, monkeypatch):
    monkeypatch.setattr(me, "_PROJECTS", str(tmp_path / "projects"))
    return tmp_path


_RECORDS = [
    {"type": "user", "message": {"role": "user", "content": "hi"}},
    {"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-4-8"}, "effort": "high"},
]


class TestConsentGate:
    def test_disabled_by_default_opens_nothing(self, projects_root, monkeypatch):
        """The load-bearing one: assert on ACCESS, not on the return value.

        A version that read the transcript and then dropped the values would
        return (None, None) too — and would be doing precisely what the flag
        exists to forbid."""
        import builtins

        proj = _make_project(projects_root, read_transcripts=None, records=_RECORDS)

        opened: list[str] = []
        real_open = builtins.open

        def _spy(path, *a, **k):
            opened.append(str(path))
            return real_open(path, *a, **k)

        monkeypatch.setattr(builtins, "open", _spy)
        result = me.read_model_effort(str(proj))
        monkeypatch.undo()

        assert result == (None, None)
        # Reading the config to LEARN consent is expected; opening the
        # transcript is the act the flag forbids.
        transcripts = [p for p in opened if p.endswith(".jsonl")]
        assert transcripts == [], f"transcript opened despite the flag being unset: {transcripts}"

    def test_enabled_reads_the_values(self, projects_root):
        """Positive control for the two tests above and below — without it,
        'returns None' would be satisfied by a module that never works."""
        proj = _make_project(projects_root, read_transcripts=True, records=_RECORDS)
        assert me.read_model_effort(str(proj)) == ("claude-opus-4-8", "high")

    def test_explicit_false_is_off(self, projects_root):
        proj = _make_project(projects_root, read_transcripts=False, records=_RECORDS)
        assert me.read_model_effort(str(proj)) == (None, None)

    @pytest.mark.parametrize("value", ["true", "yes", 1, "1", "on"])
    def test_truthy_non_boolean_does_not_enable(self, projects_root, value):
        """An ambiguous config must not resolve to MORE access than no config.

        `read_transcripts: "true"` (a string) reads as intent to enable, but a
        loose truthiness check would also enable on values the author did not
        mean as consent. Only a real boolean True counts."""
        proj = projects_root / "proj"
        (proj / ".empirica").mkdir(parents=True)
        (proj / ".empirica" / "project.yaml").write_text(f'cockpit:\n  read_transcripts: "{value}"\n')
        assert transcript_reading_enabled(str(proj)) is False

    def test_missing_project_yaml_is_off(self, tmp_path):
        assert transcript_reading_enabled(str(tmp_path / "nonexistent")) is False
        assert transcript_reading_enabled(None) is False


class TestNeverGuesses:
    def test_no_transcript_is_not_observed(self, projects_root):
        proj = _make_project(projects_root, read_transcripts=True, records=None)
        assert me.read_model_effort(str(proj)) == (None, None)

    def test_records_without_the_fields_are_not_observed(self, projects_root):
        proj = _make_project(
            projects_root,
            read_transcripts=True,
            records=[{"type": "user", "message": {"role": "user", "content": "hi"}}],
        )
        assert me.read_model_effort(str(proj)) == (None, None)

    def test_torn_leading_line_is_skipped_not_repaired(self, projects_root):
        """Slicing the tail mid-record leaves a fragment; it must be skipped
        without losing the intact records after it."""
        proj = _make_project(projects_root, read_transcripts=True, records=_RECORDS)
        tdir = projects_root / "projects" / str(proj).replace("/", "-")
        f = tdir / "session.jsonl"
        f.write_text('{"message": {"mod' + "\n" + f.read_text())
        assert me.read_model_effort(str(proj)) == ("claude-opus-4-8", "high")

    def test_non_string_field_is_rejected(self, projects_root):
        """A structurally-valid record with a wrong-typed model must not
        become a rendered cell — `str(...)` on it would print something
        plausible-looking."""
        proj = _make_project(
            projects_root,
            read_transcripts=True,
            records=[{"message": {"model": {"name": "opus"}}, "effort": ["high"]}],
        )
        assert me.read_model_effort(str(proj)) == (None, None)

    def test_newest_values_win_over_older_ones(self, projects_root):
        proj = _make_project(
            projects_root,
            read_transcripts=True,
            records=[
                {"message": {"model": "claude-sonnet-5"}, "effort": "low"},
                {"message": {"model": "claude-opus-5"}, "effort": "xhigh"},
            ],
        )
        assert me.read_model_effort(str(proj)) == ("claude-opus-5", "xhigh")


class TestCache:
    def test_second_call_does_not_reread(self, projects_root, monkeypatch):
        proj = _make_project(projects_root, read_transcripts=True, records=_RECORDS)
        assert me.read_model_effort(str(proj)) == ("claude-opus-4-8", "high")

        calls: list[str] = []
        monkeypatch.setattr(me, "_parse_tail", lambda p: calls.append(p) or (None, None))
        assert me.read_model_effort(str(proj)) == ("claude-opus-4-8", "high")
        assert calls == [], "cache miss — the tail was re-parsed on an unchanged file"


class TestShortModel:
    def test_strips_vendor_prefix_keeps_version(self):
        """`opus-5` vs `opus-4-8` is the distinction the column exists for, so
        the version-bearing tail must survive the shortening."""
        assert me.short_model("claude-opus-4-8") == "opus-4-8"
        assert me.short_model("claude-opus-5") == "opus-5"

    def test_none_stays_none(self):
        """The caller decides how unobserved renders; this must not invent a
        placeholder of its own."""
        assert me.short_model(None) is None
