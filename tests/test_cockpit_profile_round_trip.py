"""A full rewrite of a cockpit profile keeps who it is for (goal 61d7bd28, 1.17.19 sweep).

_serialize, used when provisioning falls back to rewriting a profile, wrote neither socket, socket_group, server_access nor
audience, so a shared stage came back as a single-user cockpit.
"""

from __future__ import annotations

import yaml

from empirica.core.cockpit.launcher import config as cfg

SHARED = """session_name: stage
socket: /srv/cockpit/stage.sock
socket_group: consult
server_access: [anna, bob]
audience: shared
projects:
  - name: alpha
    path: /tmp/alpha
"""


def _round_trip(tmp_path, text):
    a = tmp_path / "a.yaml"
    a.write_text(text)
    first = cfg.load_config(a)
    b = tmp_path / "b.yaml"
    b.write_text(yaml.safe_dump(cfg._serialize(first), sort_keys=False))
    return first, cfg.load_config(b)


def test_a_shared_stage_survives_a_rewrite(tmp_path):
    first, again = _round_trip(tmp_path, SHARED)
    for field in ("socket", "socket_group", "server_access", "audience"):
        assert getattr(again, field) == getattr(first, field), field


def test_an_explicit_single_audience_beside_a_socket_survives(tmp_path):
    first, again = _round_trip(tmp_path, "session_name: s\nsocket: /tmp/s.sock\naudience: single\n")
    assert again.audience == "single" and again.socket == first.socket


def test_a_plain_single_user_profile_gains_no_sharing_keys(tmp_path):
    a = tmp_path / "a.yaml"
    a.write_text("session_name: solo\n")
    out = cfg._serialize(cfg.load_config(a))
    assert not {"socket", "socket_group", "server_access", "audience"} & set(out)
