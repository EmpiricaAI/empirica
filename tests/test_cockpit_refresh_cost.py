"""A periodic task whose period is shorter than its own work is a busy loop.

`empirica tui` (PID 173117) had held **54.3% of a core for 17.8 days** on David's
machine — reported from outside by mesh-support, because a pegged core is not
something the running process complains about. Measured before the fix:

    aggregate_all()             1.7 – 2.0 s   (REFRESH_SECONDS was 2.0)
    of which yaml.safe_load     3.24 s of 4.08 s under cProfile — 417 calls
    of which json.loads         58,246 calls over an 444-line log

Three independent defects, and only the third is the one that matters:

1. **Four call sites each re-parsed the same `project.yaml`**, per instance, per
   refresh, for a file that had not changed in days.
2. **The fires log was re-read per instance.** An instance with *no* events had to
   parse every line to establish that, so the cost scaled with the instance count
   rather than the tail length — and while looking at it, the match turned out to
   be **unsatisfiable**: the log is keyed by practice `ai_id` (`empirica-cortex`)
   and the lookup passed the seat (`tmux_6`), so the cockpit's "latest 5 events"
   pane had rendered empty for every tmux row since it shipped. An impossible
   match and a genuinely quiet mesh produce the identical blank pane.
3. **`set_interval(2.0)` was never measured against the work it schedules.** Fixing
   1 and 2 cut the scan to ~0.4 s and left this untouched: a fixed period degrades
   into continuous execution on a slower box or with more instances, silently.

So the caching tests below assert *cheapness*, and the pacing tests assert the
*property* — the scan cannot exceed a bounded share of a core regardless of cost.
Only the third survives someone adding a fifth expensive reader.
"""

from __future__ import annotations

import json

import pytest

from empirica.cli.tui.cockpit_app import _MAX_REFRESH_DUTY, REFRESH_SECONDS, refresh_gap
from empirica.utils import yaml_cache


@pytest.fixture(autouse=True)
def clean_cache():
    yaml_cache.clear_cache()
    yield
    yaml_cache.clear_cache()


@pytest.fixture
def project(tmp_path):
    """A project root with a real `.empirica/project.yaml`."""
    root = tmp_path / "proj"
    (root / ".empirica").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text(
        "ai_id: empirica-test\nproject_id: pid-123\ncockpit:\n  loops: []\n"
    )
    return root


def _count_parses(monkeypatch) -> list[int]:
    """Wrap yaml.safe_load so a test can see how often it actually ran.

    The whole claim is "this stopped happening", and a timing assertion would be
    flaky on a loaded box. Counting the call is the direct observation.
    """
    import yaml

    calls = [0]
    real = yaml.safe_load

    def counting(*a, **kw):
        calls[0] += 1
        return real(*a, **kw)

    monkeypatch.setattr(yaml, "safe_load", counting)
    return calls


# ── the cache ────────────────────────────────────────────────────────────────


def test_an_unchanged_file_is_parsed_once_however_often_it_is_read(project, monkeypatch):
    """THE property. 100 reads of an untouched file cost one parse."""
    calls = _count_parses(monkeypatch)
    for _ in range(100):
        assert yaml_cache.load_project_yaml(project)["ai_id"] == "empirica-test"
    assert calls[0] == 1, f"re-parsed an unchanged file {calls[0]} times"


def test_the_counter_would_have_caught_the_old_behaviour(project, monkeypatch):
    """NEGATIVE CONTROL. Without this, a `safe_load` that silently stopped being
    called at all — a broken import, a swallowed exception — would pass the test
    above for entirely the wrong reason."""
    calls = _count_parses(monkeypatch)
    for _ in range(5):
        yaml_cache.clear_cache()
        yaml_cache.load_project_yaml(project)
    assert calls[0] == 5, "the parse counter is not observing real parses"


def test_an_edit_is_visible_on_the_very_next_read(project):
    """Keyed on (mtime_ns, size), not a TTL: there is no staleness window to
    wait out. A cockpit showing a stale ai_id after a rename would be a worse
    defect than the one being fixed."""
    p = project / ".empirica" / "project.yaml"
    assert yaml_cache.load_project_yaml(project)["ai_id"] == "empirica-test"

    p.write_text("ai_id: empirica-renamed\nproject_id: pid-123\n")
    assert yaml_cache.load_project_yaml(project)["ai_id"] == "empirica-renamed"


def test_a_same_length_edit_is_still_seen(project):
    """`size` alone would miss this; `mtime_ns` is what catches it. Included
    because a cache keyed on size alone passes the test above by luck."""
    p = project / ".empirica" / "project.yaml"
    yaml_cache.load_project_yaml(project)
    original = p.read_text()
    p.write_text(original.replace("empirica-test", "empirica-tset"))
    assert len(p.read_text()) == len(original)
    assert yaml_cache.load_project_yaml(project)["ai_id"] == "empirica-tset"


def test_a_deleted_file_is_not_served_from_cache(project):
    """A pointer that outlives its file is how a cockpit reports a project that
    no longer exists."""
    p = project / ".empirica" / "project.yaml"
    assert yaml_cache.load_project_yaml(project)
    p.unlink()
    assert yaml_cache.load_project_yaml(project) == {}


def test_a_recreated_file_is_read_fresh(project):
    """POSITIVE CONTROL on the eviction above — it must drop the entry, not
    poison the key."""
    p = project / ".empirica" / "project.yaml"
    yaml_cache.load_project_yaml(project)
    p.unlink()
    yaml_cache.load_project_yaml(project)
    p.write_text("ai_id: empirica-reborn\n")
    assert yaml_cache.load_project_yaml(project)["ai_id"] == "empirica-reborn"


@pytest.mark.parametrize(
    "content",
    ["", "just a string", "- a\n- list\n", "key: [unclosed\n"],
    ids=["empty", "scalar", "sequence", "malformed"],
)
def test_anything_that_is_not_a_mapping_reads_as_empty(project, content):
    """Same non-raising contract the four replaced call sites had — the change
    may alter cost, never behaviour."""
    (project / ".empirica" / "project.yaml").write_text(content)
    assert yaml_cache.load_project_yaml(project) == {}


def test_no_project_path_is_not_an_error():
    assert yaml_cache.load_project_yaml(None) == {}
    assert yaml_cache.load_project_yaml("") == {}


def test_the_cache_is_bounded(tmp_path):
    """A 17-day process is exactly where an unbounded dict is a leak rather than
    a cache."""
    for i in range(yaml_cache._MAX_ENTRIES + 20):
        f = tmp_path / f"f{i}.yaml"
        f.write_text(f"n: {i}\n")
        yaml_cache.load_yaml_cached(f)
    assert len(yaml_cache._cache) <= yaml_cache._MAX_ENTRIES


# ── every reader goes through the cache ──────────────────────────────────────


@pytest.mark.parametrize(
    ("reader", "expected"),
    [
        ("ai_id", "empirica-test"),
        ("compliance_project_id", "pid-123"),
        ("services_project_id", "pid-123"),
        ("cockpit_config", {"loops": []}),
    ],
)
def test_all_four_cockpit_readers_share_one_parse(project, monkeypatch, reader, expected):
    """The cost was never one reader — it was four readers that did not know the
    others existed. Parametrised so a failure names WHICH one drifted back to
    opening the file itself."""
    from empirica.core.cockpit.compliance_view import _project_id_from_path as compliance_pid
    from empirica.core.cockpit.project_cockpit_config import _load_cockpit_block
    from empirica.core.cockpit.services_view import _project_id_from_path as services_pid
    from empirica.utils.session_resolver import InstanceResolver

    readers = {
        "ai_id": lambda: InstanceResolver.ai_id(project_path=str(project)),
        "compliance_project_id": lambda: compliance_pid(str(project)),
        "services_project_id": lambda: services_pid(str(project)),
        "cockpit_config": lambda: _load_cockpit_block(str(project)),
    }

    yaml_cache.load_project_yaml(project)  # prime, as the sweep's first reader does
    calls = _count_parses(monkeypatch)
    assert readers[reader]() == expected
    assert calls[0] == 0, f"{reader} re-parsed project.yaml instead of using the shared cache"


# ── the fires log: one pass, keyed by practice ───────────────────────────────


@pytest.fixture
def fires_log(tmp_path, monkeypatch):
    """A loop_fires.log keyed the way the listener actually writes it — by
    practice `ai_id`, never by tmux seat."""
    home = tmp_path / "home"
    (home / ".empirica").mkdir(parents=True)
    log = home / ".empirica" / "loop_fires.log"
    lines = []
    for practice in ("empirica", "empirica-cortex"):
        for n in range(8):
            lines.append(json.dumps({"instance_id": practice, "n": n}))
    lines.append("not json at all")
    lines.append("")
    lines.append(json.dumps({"no_instance_id": True}))
    log.write_text("\n".join(lines) + "\n")
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    return log


def test_events_are_bucketed_by_practice_and_capped(fires_log):
    from empirica.core.cockpit.instance_state import _read_events_by_instance

    buckets = _read_events_by_instance(limit=5)
    assert set(buckets) == {"empirica", "empirica-cortex"}
    assert all(len(v) == 5 for v in buckets.values()), "limit not applied per bucket"


def test_events_are_most_recent_first(fires_log):
    """The pane is captioned 'latest 5'. Reversed ordering would show the oldest
    five and look identical to a caption nobody reads."""
    from empirica.core.cockpit.instance_state import _read_events_by_instance

    got = [e["n"] for e in _read_events_by_instance(limit=5)["empirica"]]
    assert got == [7, 6, 5, 4, 3]


def test_junk_lines_are_skipped_without_killing_the_pass(fires_log):
    """One unparseable line must not blank the whole pane — the log is appended
    to concurrently by every listener on the box."""
    from empirica.core.cockpit.instance_state import _read_events_by_instance

    assert len(_read_events_by_instance(limit=5)) == 2


def test_one_pass_over_the_log_regardless_of_instance_count(fires_log, monkeypatch):
    """The per-instance form could not be made cheap: proving an instance has NO
    events required reading every line, so cost scaled with instances."""
    real_loads = json.loads
    calls = [0]

    def counting(*a, **kw):
        calls[0] += 1
        return real_loads(*a, **kw)

    monkeypatch.setattr(json, "loads", counting)
    from empirica.core.cockpit.instance_state import _read_events_by_instance

    _read_events_by_instance(limit=5)
    assert calls[0] <= 19, f"{calls[0]} parses for a 19-line log — the pass is not single"


def test_a_missing_log_is_empty_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "nowhere")
    from empirica.core.cockpit.instance_state import _read_events_by_instance

    assert _read_events_by_instance() == {}


def test_the_lookup_key_is_the_practice_not_the_seat():
    """The unsatisfiable match, guarded structurally.

    A cockpit row is discovered as `tmux_6`; the log is written with
    `instance_id=empirica-cortex`. Measured on the live box before the fix: 11
    instances, **0 events rendered**, with 43 events sitting in the log. After:
    43. An impossible match and a quiet mesh render the same empty pane, which
    is why this is asserted on the source rather than on the output.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "empirica/core/cockpit/instance_state.py").read_text()
    assert ".get(loop_key, [])" in src, "recent_events must key on the practice ai_id (loop_key)"
    assert ".get(instance_id, [])" not in src, "keying recent_events on the seat can never match"


# ── the pacing rule ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("cost", [0.4, 1.7, 2.0, 5.0, 30.0])
def test_the_scan_can_never_exceed_the_duty_bound(cost):
    """THE property, and the only one that survives someone adding a fifth
    expensive reader. At 2.0 s cost the old fixed period gave a duty cycle of
    1.0 — which is what 54.3% of a core for 17.8 days looks like."""
    duty = cost / (cost + refresh_gap(cost))
    assert duty <= _MAX_REFRESH_DUTY + 1e-9, f"a {cost}s scan would run at {duty:.0%} of a core"


def test_the_old_fixed_period_would_fail_that(monkeypatch):
    """NEGATIVE CONTROL with the real pre-fix behaviour: a constant 2.0 s period
    against a 2.0 s scan is a 50% duty cycle by this measure and a busy loop in
    practice, since the work does not pause for the timer."""
    duty = 2.0 / (2.0 + REFRESH_SECONDS)
    assert duty > _MAX_REFRESH_DUTY, "the guard cannot distinguish the defect from the fix"


def test_a_cheap_scan_still_honours_the_floor():
    """Without the floor, a 5 ms scan would re-run at 30 Hz — the same defect
    arrived at from the other end."""
    assert refresh_gap(0.005) == REFRESH_SECONDS


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_no_measurement_yet_falls_back_to_the_floor(bad):
    """First tick, and the defensive case. A zero here must not become a
    zero-delay timer."""
    assert refresh_gap(bad) == REFRESH_SECONDS


def test_the_cockpit_arms_a_self_pacing_timer_not_a_fixed_interval():
    """Structural, because the alternative is running the TUI and watching
    `top` — which is exactly how this went unnoticed for 17.8 days."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "empirica/cli/tui/cockpit_app.py").read_text()
    assert "set_interval(REFRESH_SECONDS, self.refresh_payload)" not in src, "the fixed period is back"
    assert "self.set_timer(refresh_gap(" in src
