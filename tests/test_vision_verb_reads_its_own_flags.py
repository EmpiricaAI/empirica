"""`empirica vision` reads the arguments its parser defines.

The parser gave a positional `image_path`; the handler read `args.pattern` and
`args.image` and `args.session_id`, none of which existed. With Pillow present
every call raised AttributeError; without it, the install hint printed and the
verb exited 0. Found by the unread-flag probe (argparse dests with no reader).

Pillow is not a dependency of the test image: the analyzer is stubbed so the
test checks the plumbing, which was the defect.
"""

from __future__ import annotations

import argparse
import json

import pytest

from empirica.cli.command_handlers import vision_commands as vc
from empirica.cli.parsers.vision_parsers import add_vision_parsers


def _parse(argv):
    parser = argparse.ArgumentParser()
    add_vision_parsers(parser.add_subparsers(dest="command"))
    return parser.parse_args(argv)


def _assessment(path):
    return vc.BasicImageAssessment(
        image_path=path,
        slide_number=None,
        width=4,
        height=4,
        format="PNG",
        mode="RGB",
        file_size_kb=0.1,
        aspect_ratio=1.0,
        pixel_count=16,
        is_presentation_size=False,
    )


@pytest.fixture
def stubbed_analyzer(monkeypatch):
    seen = {}

    class _A:
        def analyze_image(self, path, _slide_number=None):
            seen["image"] = str(path)
            return _assessment(str(path))

        def analyze_deck(self, pattern):
            seen["pattern"] = pattern
            return [_assessment("a.png"), _assessment("b.png")]

    monkeypatch.setattr(vc, "HAS_PIL", True)
    monkeypatch.setattr(vc, "VisionAnalyzer", _A)
    return seen


def test_a_single_image_reaches_the_analyzer(stubbed_analyzer, capsys):
    rc = vc.handle_vision_analyze(_parse(["vision", "slide.png", "--output", "json"]))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert stubbed_analyzer["image"] == "slide.png"
    assert out["ok"] is True and out["analyzed"] == 1


def test_a_pattern_is_a_deck(stubbed_analyzer, capsys):
    rc = vc.handle_vision_analyze(_parse(["vision", "--pattern", "deck/*.png", "--output", "json"]))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert stubbed_analyzer["pattern"] == "deck/*.png"
    assert out["analyzed"] == 2


def test_missing_pillow_is_exit_1_not_a_hint_at_exit_0(monkeypatch, capsys):
    monkeypatch.setattr(vc, "HAS_PIL", False)
    rc = vc.handle_vision_analyze(_parse(["vision", "slide.png"]))
    assert rc == 1
    assert "pillow" in capsys.readouterr().out.lower()


@pytest.mark.usefixtures("stubbed_analyzer")
def test_no_target_is_refused():
    assert vc.handle_vision_analyze(_parse(["vision"])) == 1
