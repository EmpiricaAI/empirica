"""remote_control: true in a cockpit profile names each claude pane's Remote Control session after its practice (goal
8da5f220; autonomy prop_xqwy3a2pszfwbjrvwh3stke3oe: every session showed as 'Remote control' on the phone, and only a name
given at connect time sticks). Claude Code takes `--remote-control [name]` (checked on 2.1.326)."""

from __future__ import annotations

import yaml

from empirica.core.cockpit.launcher.config import _serialize, load_config


def _profile(tmp_path, **extra):
    path = tmp_path / "config-x.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "session_name": "x",
                "projects": [
                    {"name": "core", "path": str(tmp_path), "launch": "claude --continue"},
                    {"name": "lab", "path": str(tmp_path), "launch": "ecodex"},
                    {"name": "named", "path": str(tmp_path), "launch": "claude --remote-control mine"},
                ],
                **extra,
            }
        )
    )
    return path


def test_on_each_claude_pane_is_named_after_its_practice(tmp_path):
    config = load_config(_profile(tmp_path, remote_control=True))
    launches = {p.name: p.launch for p in config.projects}
    assert launches["core"] == "claude --continue --remote-control core"
    assert launches["lab"] == "ecodex"  # not claude
    assert launches["named"] == "claude --remote-control mine"  # a name already given wins


def test_off_by_default_and_never_written_back_into_launch_lines(tmp_path):
    assert load_config(_profile(tmp_path)).projects[0].launch == "claude --continue"
    out = _serialize(load_config(_profile(tmp_path, remote_control=True)))
    assert out["remote_control"] is True
    assert [p["launch"] for p in out["projects"]][:2] == ["claude --continue", "ecodex"]
