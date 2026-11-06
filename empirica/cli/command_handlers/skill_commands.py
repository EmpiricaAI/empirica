"""
Skill Commands - suggest and fetch skills into project_skills/*.yaml
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from ..cli_utils import handle_cli_error, printed

logger = logging.getLogger(__name__)


def _load_skill_sources(root: str) -> list[dict]:
    """Load available skill sources from SKILL_SOURCES.yaml."""
    import yaml  # type: ignore

    path = os.path.join(root, "docs", "skills", "SKILL_SOURCES.yaml")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("skills", [])


def handle_skill_suggest_command(args):
    """Handle skill-suggest command to find relevant skills for a task."""
    try:
        import yaml  # type: ignore

        from empirica.utils.session_resolver import InstanceResolver as R

        context_project = R.project_path()
        root = context_project if context_project else os.getcwd()
        task = getattr(args, "task", "")

        # First: check local project_skills/*.yaml
        local_skills = []
        skills_dir = os.path.join(root, "project_skills")
        if os.path.exists(skills_dir):
            for filename in os.listdir(skills_dir):
                if filename.endswith((".yaml", ".yml")):
                    try:
                        with open(os.path.join(skills_dir, filename), encoding="utf-8") as f:
                            skill = yaml.safe_load(f)
                            if skill:
                                local_skills.append(
                                    {
                                        "name": skill.get("title", skill.get("id", filename)),
                                        "id": skill.get("id", filename.replace(".yaml", "").replace(".yml", "")),
                                        "source": "local",
                                        "tags": skill.get("tags", []),
                                        "location": "project_skills",
                                    }
                                )
                    except Exception:
                        pass

        # Second: get available online sources (candidates to fetch)
        online_sources = _load_skill_sources(root)

        # Combine: local first (already fetched), then online candidates
        result = {
            "ok": True,
            "task": task,
            "suggestions": {"local": local_skills, "available_to_fetch": online_sources},
        }
        print(json.dumps(result, indent=2))
        return printed(result, args)
    except Exception as e:
        handle_cli_error(e, "Skill suggest", getattr(args, "verbose", False))
        return None


_SKILL_CANDIDATE_NAMES = (
    "skill.yaml",
    "skill.yml",
    "skill.json",
    "skill.md",
    "README.md",
    "readme.md",
)


def _normalize_skill_meta(meta: dict, name: str, tags: list) -> dict:
    """Build a skill object from a raw meta dict with sane defaults."""
    return {
        "id": meta.get("id") or name.lower().replace(" ", "-"),
        "title": meta.get("title") or name,
        "tags": meta.get("tags") or tags,
        "preconditions": meta.get("preconditions") or [],
        "steps": meta.get("steps") or [],
        "gotchas": meta.get("gotchas") or [],
        "references": meta.get("references") or [],
        "summary": meta.get("summary") or "",
    }


def _save_skill_yaml(skill_obj: dict, base_path: str) -> dict:
    """Save skill object to project_skills directory as YAML."""
    import yaml  # type: ignore

    slug = skill_obj["id"]
    out_dir = os.path.join(base_path, "project_skills")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{slug}.yaml")
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(skill_obj, f, sort_keys=False)
    return {"ok": True, "saved": out_path, "skill": skill_obj}


def _pick_archive_candidate(members: list) -> str | None:
    """Return the preferred member from a skill archive, or None."""
    for cand in _SKILL_CANDIDATE_NAMES:
        for m in members:
            if m.lower().endswith(cand):
                return m
    return None


def _parse_archive_member(zf, candidate: str, name: str, tags: list) -> dict:
    """Parse one archive member into a normalized skill object."""
    import yaml  # type: ignore

    from empirica.core.skills.parser import parse_markdown_to_skill

    with zf.open(candidate) as fh:
        data = fh.read()
    if candidate.lower().endswith((".yaml", ".yml")):
        meta = yaml.safe_load(data) or {}
        return _normalize_skill_meta(meta, name, tags)
    if candidate.lower().endswith(".json"):
        import json as _json

        meta = _json.loads(data.decode("utf-8", errors="ignore"))
        return _normalize_skill_meta(meta, name, tags)
    md_text = data.decode("utf-8", errors="ignore")
    return parse_markdown_to_skill(md_text, name=name, tags=tags)


def _archive_fallback_concat(zf, members: list, name: str, tags: list) -> dict:
    """Fallback when no preferred candidate exists: concat .md/.txt members."""
    from empirica.core.skills.parser import parse_markdown_to_skill

    md_text = ""
    for m in members:
        if m.lower().endswith((".md", ".txt")):
            with zf.open(m) as fh:
                md_text += fh.read().decode("utf-8", errors="ignore") + "\n\n"
    return parse_markdown_to_skill(md_text, name=name, tags=tags)


def _extract_skill_from_archive(file_path: str, name: str, tags: list) -> dict:
    """Open a .skill zip archive and return the parsed skill object."""
    import zipfile

    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)
    with zipfile.ZipFile(file_path, "r") as zf:
        members = zf.namelist()
        candidate = _pick_archive_candidate(members)
        if not candidate:
            return _archive_fallback_concat(zf, members, name, tags)
        return _parse_archive_member(zf, candidate, name, tags)


def _fetch_skill_from_url(url: str, name: str, tags: list) -> dict:
    """Fetch a skill definition from a URL (markdown)."""
    import requests  # type: ignore

    from empirica.core.skills.parser import parse_markdown_to_skill

    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return parse_markdown_to_skill(resp.text, name=name, tags=tags)


def handle_skill_fetch_command(args):
    """Handle skill-fetch command to download and save a skill definition."""
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        name = args.name
        url = getattr(args, "url", None)
        file_path = getattr(args, "file", None)
        tags = [t.strip() for t in (getattr(args, "tags", "") or "").split(",") if t.strip()]

        context_project = R.project_path()
        base_path = context_project if context_project else os.getcwd()

        if file_path:
            skill_obj = _extract_skill_from_archive(file_path, name, tags)
        elif url:
            skill_obj = _fetch_skill_from_url(url, name, tags)
        else:
            raise ValueError("--url or --file is required for skill-fetch")

        result = _save_skill_yaml(skill_obj, base_path)
        print(json.dumps(result, indent=2))
        return None  # Success — output already printed
    except Exception as e:
        handle_cli_error(e, "Skill fetch", getattr(args, "verbose", False))
        return None


def handle_skill_extract_command(args):
    """Extract decision frameworks from skill(s) to meta-agent-config.yaml."""
    try:
        from pathlib import Path

        from empirica.core.skills.extractor import extract_all_skills, extract_single_skill

        skill_dir = getattr(args, "skill_dir", None)
        skills_dir = getattr(args, "skills_dir", None)
        output_file = getattr(args, "output_file", "meta-agent-config.yaml")
        verbose = getattr(args, "verbose", False)
        output_format = getattr(args, "output", "json")

        if not skill_dir and not skills_dir:
            raise ValueError("Either --skill-dir or --skills-dir is required")

        if skills_dir:
            # Extract all skills from directory
            config = extract_all_skills(Path(skills_dir), Path(output_file), verbose=verbose)
            result: dict[str, Any] = {
                "ok": True,
                "mode": "multi",
                "skills_dir": str(skills_dir),
                "output_file": str(output_file),
                "domains": list(config.get("meta_agent", {}).get("domain_knowledge", {}).keys()),
            }
        else:
            # Extract single skill
            domain_data = extract_single_skill(Path(skill_dir), verbose=verbose)
            result = {"ok": True, "mode": "single", "skill_dir": str(skill_dir), "extracted": domain_data}

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            if skills_dir:
                print(f"Extracted {len(result['domains'])} skills to {output_file}")
                for domain in result["domains"]:
                    print(f"  - {domain}")
            else:
                domain_name = next(iter(domain_data.keys())) if domain_data else "unknown"
                print(f"Extracted skill: {domain_name}")

        return printed(result, args)
    except Exception as e:
        handle_cli_error(e, "Skill extract", getattr(args, "verbose", False))
        return None
