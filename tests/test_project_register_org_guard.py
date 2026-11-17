"""project-register registers with the practice's own org's sign-in and refuses a mismatch (goal 898cfceb; cortex
prop_mbhfclatcnddvjtlptt4dvrrfq: a person with seats in two orgs registered a practice in the wrong one, because the
request carries no org and the client said nothing about which sign-in it used)."""

from __future__ import annotations

import argparse

from empirica.cli.command_handlers import projects_commands as pc


def _step(monkeypatch, *, practice_org, signed_in_org, posted):
    monkeypatch.setattr(pc, "_resolve_cortex_config", lambda args, org_id=None: ("https://c.test", f"tok-{org_id}"))
    monkeypatch.setattr(pc, "_signed_in_org", lambda url, bearer, timeout: signed_in_org)
    monkeypatch.setattr(pc, "_post_project", lambda *a, **k: posted.append(a) or (200, {"project_id": "p1"}))
    monkeypatch.setattr(pc, "_link_user_to_project", lambda *a, **k: {"ok": True})
    return pc._project_register_cortex_step(
        argparse.Namespace(), project_id="p1", name="n", repo_url=None, no_cortex=False, skip_link=True,
        force_metadata_update=False, timeout=5.0, org_id=practice_org,
    )  # fmt: skip


def test_a_sign_in_for_another_org_is_refused_before_anything_is_posted(monkeypatch):
    posted: list = []
    out = _step(monkeypatch, practice_org="org-consulting", signed_in_org="org-nle", posted=posted)
    assert out["ok"] is False and not posted
    assert (
        "org-nle" in out["reason"]
        and "org-consulting" in out["reason"]
        and "auth login --org org-consulting" in out["reason"]
    )


def test_the_matching_org_registers_and_says_where(monkeypatch):
    posted: list = []
    out = _step(monkeypatch, practice_org="org-consulting", signed_in_org="org-consulting", posted=posted)
    assert posted and out.get("org") == "org-consulting"


def test_a_practice_naming_no_org_registers_where_the_sign_in_is_and_says_so(monkeypatch):
    posted: list = []
    out = _step(monkeypatch, practice_org=None, signed_in_org="org-nle", posted=posted)
    assert posted and out.get("org") == "org-nle"
