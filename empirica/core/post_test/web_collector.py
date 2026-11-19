"""
Web Evidence Collector

Grounded calibration evidence for website/static-site content work.
Activated when sessions modify .astro, .html, .jsx, .tsx, .vue, .svelte, .mdx files.

Evidence sources:
- Build verification: did the static site build pass? -> do, completion, state
- HTML validation: structural correctness of output -> clarity, coherence
- Link integrity: internal links resolve -> coherence, signal
- Terminology consistency: glossary term matching -> coherence, clarity, signal
- Asset verification: referenced images/SVGs exist -> do, state

Activated via evidence_profile: "web" in project.yaml or auto-detected from file extensions.
"""

import logging
import os
import re
import subprocess
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .collector import EvidenceItem, EvidenceQuality

logger = logging.getLogger(__name__)

# File extensions that trigger web profile auto-detection
WEB_EXTENSIONS = {".astro", ".html", ".jsx", ".tsx", ".vue", ".svelte", ".mdx"}


class _HTMLStructureValidator(HTMLParser):
    """Minimal HTML structure validator using stdlib html.parser."""

    def __init__(self):
        super().__init__()
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self._stack: list[str] = []
        self._has_html = False
        self._has_head = False
        self._has_title = False
        self._has_body = False
        self._has_charset = False
        self._empty_attrs: list[str] = []

    # Self-closing tags that don't need a closing tag
    VOID_ELEMENTS = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )

    def handle_starttag(self, tag: str, attrs: list):
        tag = tag.lower()
        if tag == "html":
            self._has_html = True
        elif tag == "head":
            self._has_head = True
        elif tag == "title":
            self._has_title = True
        elif tag == "body":
            self._has_body = True
        elif tag == "meta":
            for name, _value in attrs:
                if name == "charset":
                    self._has_charset = True

        # Check for empty href/src
        for name, value in attrs:
            if name in ("href", "src") and (value is None or value.strip() == ""):
                self._empty_attrs.append(f"<{tag}> has empty {name}")

        if tag not in self.VOID_ELEMENTS:
            self._stack.append(tag)

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in self.VOID_ELEMENTS:
            return
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        elif tag in self._stack:
            # Mismatched nesting
            self.errors.append(f"Mismatched tag: </{tag}> (expected </{self._stack[-1]}>)")
            # Pop up to the matching tag
            while self._stack and self._stack[-1] != tag:
                self._stack.pop()
            if self._stack:
                self._stack.pop()
        else:
            self.errors.append(f"Unexpected closing tag: </{tag}>")

    def finalize(self):
        """Call after feeding all content. Adds structure-level errors/warnings."""
        if self._stack:
            self.errors.append(f"Unclosed tags: {', '.join(self._stack)}")
        for attr_err in self._empty_attrs:
            self.errors.append(attr_err)

        # Structure warnings (not errors — many frameworks omit these)
        if not self._has_html:
            self.warnings.append("Missing <html> tag")
        if not self._has_head:
            self.warnings.append("Missing <head> tag")
        if not self._has_title:
            self.warnings.append("Missing <title> tag")
        if not self._has_body:
            self.warnings.append("Missing <body> tag")
        if not self._has_charset:
            self.warnings.append("Missing charset declaration")


_BASE_HREF = re.compile(r"""<base\s[^>]*href=["']([^"']+)["']""", re.IGNORECASE)


def _page_base(html_file: Path, content: str, output_dir: Path) -> Path | None:
    """The folder a page's relative links resolve against: its <base href> when it has one, else its own folder. None
    when the base is on another host, so relative links are external (1.17.9 sweep leftover)."""
    m = _BASE_HREF.search(content)
    if not m:
        return html_file.parent
    base = m.group(1).strip()
    if base.startswith(("http://", "https://", "//")):
        return None
    base_path = urlparse(base).path
    folder = output_dir / base_path.lstrip("/") if base_path.startswith("/") else html_file.parent / base_path
    return folder if base_path.endswith("/") or not base_path else folder.parent


def _resolve_page_path(link_path: str, base: Path, output_dir: Path) -> Path:
    """A browser resolves '/x' against the site root and 'x' / '../x' against the page's base."""
    return output_dir / link_path.lstrip("/") if link_path.startswith("/") else base / link_path


class WebEvidenceCollector:
    """Collects deterministic evidence from web/static-site artifacts."""

    def __init__(
        self,
        session_id: str,
        project_id: str | None = None,
        db=None,
        phase: str = "combined",
        check_timestamp: float | None = None,
        project_path: str | Path | None = None,
        preflight_timestamp: float | None = None,
    ):
        self.session_id = session_id
        self.project_id = project_id
        self.phase = phase
        self.check_timestamp = check_timestamp
        # Transaction start: without a build at POSTFLIGHT, output older than this was not produced by this transaction.
        self.preflight_timestamp = preflight_timestamp
        self._build_ran = False
        self._output_fresh: bool | None = None
        self._db = db
        self._owns_db = False
        # The project the caller already resolved (PostTestCollector passes its
        # transaction-bound root). Without it, `git rev-parse` from cwd, which
        # names whatever repo the process happens to stand in.
        self._project_path: Path | None = Path(project_path) if project_path else None
        self._web_config: dict[str, Any] | None = None
        #: source name -> why it produced nothing (e.g. the build was not opted in)
        self.notes: dict[str, str] = {}

    def _get_db(self):
        if self._db is None:
            from empirica.data.session_database import SessionDatabase

            self._db = SessionDatabase()
            self._owns_db = True
        return self._db

    def _close_db(self):
        if self._owns_db and self._db is not None:
            self._db.close()
            self._db = None
            self._owns_db = False

    def _get_project_path(self) -> Path | None:
        """The project root given at construction; otherwise detected from git in cwd."""
        if self._project_path is not None:
            return self._project_path
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                self._project_path = Path(result.stdout.strip())
                return self._project_path
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    def _get_web_config(self) -> dict[str, Any]:
        """Load web_evidence config from project.yaml."""
        if self._web_config is not None:
            return self._web_config

        self._web_config = {}
        project_path = self._get_project_path()
        if not project_path:
            return self._web_config

        try:
            import yaml

            config_path = project_path / ".empirica" / "project.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    config = yaml.safe_load(f) or {}
                self._web_config = config.get("web_evidence", {})
        except Exception:  # noqa: S110 — best-effort config load; empty dict fallback
            pass
        return self._web_config or {}

    def _detect_build_tool(self) -> dict[str, str] | None:
        """Auto-detect the build tool from project structure."""
        project_path = self._get_project_path()
        if not project_path:
            return None

        config = self._get_web_config()

        # Explicit config overrides auto-detection
        if config.get("build_command"):
            return {
                "tool": "custom",
                "command": config["build_command"],
                "output_dir": config.get("output_dir", "dist"),
            }

        # Auto-detect from project files
        if (project_path / "astro.config.mjs").exists() or (project_path / "astro.config.ts").exists():
            return {"tool": "astro", "command": "npx astro build", "output_dir": "dist"}

        if (
            (project_path / "next.config.js").exists()
            or (project_path / "next.config.mjs").exists()
            or (project_path / "next.config.ts").exists()
        ):
            return {"tool": "next", "command": "npx next build", "output_dir": ".next"}

        pkg_json = project_path / "package.json"
        if pkg_json.exists():
            try:
                import json

                pkg = json.loads(pkg_json.read_text())
                if "build" in pkg.get("scripts", {}):
                    return {"tool": "npm", "command": "npm run build", "output_dir": "dist"}
            except Exception:  # noqa: S110 — best-effort package.json parsing; other detectors below
                pass

        makefile = project_path / "Makefile"
        if makefile.exists():
            content = makefile.read_text()
            if re.search(r"^build\s*:", content, re.MULTILINE):
                return {"tool": "make", "command": "make build", "output_dir": "build"}

        return None

    def _get_output_dir(self) -> Path | None:
        """Get the build output directory."""
        project_path = self._get_project_path()
        if not project_path:
            return None

        config = self._get_web_config()
        output_dir_name = config.get("output_dir")

        if not output_dir_name:
            build_info = self._detect_build_tool()
            if build_info:
                output_dir_name = build_info["output_dir"]
            else:
                # Try common output directories
                for candidate in ("dist", "out", "build", "public"):
                    if (project_path / candidate).is_dir():
                        output_dir_name = candidate
                        break

        if output_dir_name:
            return project_path / output_dir_name
        return None

    def _output_written_this_transaction(self, output_dir: Path) -> bool:
        """May this output be graded as this transaction's work? Cached: four sources ask.

        Yes when the build ran here, or when no transaction start is known (a caller outside POSTFLIGHT, the same rule the
        test-report reader applies). Otherwise only when the newest file in the output dir was written at or after the
        transaction's PREFLIGHT (CHECK when no PREFLIGHT was given); an older tree is someone else's build, and
        `notes["web_output"]` says it was skipped. Cost: one stat per file in the output dir, web profile only."""
        if self._build_ran:
            return True
        if self._output_fresh is not None:
            return self._output_fresh
        start = self.preflight_timestamp or self.check_timestamp
        if not start:
            self._output_fresh = True
            return True
        newest = 0.0
        for dirpath, _dirs, files in os.walk(output_dir):
            for name in files:
                try:
                    newest = max(newest, os.stat(os.path.join(dirpath, name)).st_mtime)
                except OSError:
                    continue
        self._output_fresh = newest >= float(start)
        if not self._output_fresh:
            self.notes["web_output"] = (
                f"output not graded: {output_dir.name}/ was last written before this transaction's PREFLIGHT and no build ran"
            )
        return self._output_fresh

    def _get_changed_html_files(self) -> list[Path]:
        """HTML files in the output dir, when that output is this transaction's (see _output_written_this_transaction)."""
        output_dir = self._get_output_dir()
        if not output_dir or not output_dir.exists():
            return []
        if not self._output_written_this_transaction(output_dir):
            return []

        # Get all HTML files in output directory
        # (Scoping to changed-only would require mapping source->output,
        #  which varies by framework. For now, check all output HTML.)
        return list(output_dir.rglob("*.html"))

    def collect_all(self) -> list[EvidenceItem]:
        """Collect web-specific evidence from all available sources."""
        items = []

        collectors = [
            ("web_build", self._collect_build_verification),
            ("web_validation", self._collect_html_validation),
            ("web_links", self._collect_link_integrity),
            ("web_terminology", self._collect_terminology_consistency),
            ("web_assets", self._collect_asset_verification),
        ]

        for source_name, collector_fn in collectors:
            try:
                result = collector_fn()
                if result:
                    items.extend(result)
            except Exception as e:
                logger.debug(f"Web evidence source {source_name} failed: {e}")

        self._close_db()
        return items

    # --- Build Verification ---

    def _collect_build_verification(self) -> list[EvidenceItem]:
        """Run the project's build and check its exit code, only when the project opts in.

        This runs inside POSTFLIGHT, so an auto-detected `npm run build` (up to
        120 s) was a full build per transaction nobody asked for. It runs only
        when `.empirica/project.yaml` declares `web_evidence.build_command`;
        otherwise it is skipped and `notes["web_build"]` says why.
        """
        items: list[EvidenceItem] = []
        project_path = self._get_project_path()
        if not project_path:
            return items

        config = self._get_web_config()
        if not config.get("build_command"):
            self.notes["web_build"] = (
                "build not run: set web_evidence.build_command in .empirica/project.yaml to verify the build at POSTFLIGHT"
            )
            return items
        build_info = self._detect_build_tool()
        if not build_info:
            return items
        timeout = config.get("build_timeout", 120)

        start = time.time()
        # _build_ran is set only once the build succeeded (below): a build that raises left old output looking fresh
        try:
            result = subprocess.run(
                build_info["command"].split(),
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(project_path),
            )
            duration_ms = (time.time() - start) * 1000
            success = result.returncode == 0
            self._build_ran = (
                success  # a failed build wrote no fresh output: grade it only if it is fresh (second pass)
            )

            items.append(
                EvidenceItem(
                    source="web_build",
                    metric_name="build_success",
                    value=1.0 if success else 0.0,
                    raw_value={
                        "exit_code": result.returncode,
                        "build_tool": build_info["tool"],
                        "duration_ms": round(duration_ms, 1),
                        "stderr_tail": result.stderr[-500:] if not success else "",
                    },
                    quality=EvidenceQuality.OBJECTIVE,
                    supports_vectors=["do", "completion", "state"],
                )
            )

        except subprocess.TimeoutExpired:
            self._build_ran = False  # timed out: the output on disk may be the old one
            items.append(
                EvidenceItem(
                    source="web_build",
                    metric_name="build_success",
                    value=0.0,
                    raw_value={"error": f"Build timed out after {timeout}s", "build_tool": build_info["tool"]},
                    quality=EvidenceQuality.OBJECTIVE,
                    supports_vectors=["do", "completion", "state"],
                )
            )
        except FileNotFoundError:
            self._build_ran = False  # nothing was built: the output on disk is as old as it was
            self.notes["web_build"] = f"build not run: {build_info['command'].split()[0]!r} not found"
            logger.debug(f"Build command not found: {build_info['command']}")

        return items

    # --- HTML Validation ---

    def _collect_html_validation(self) -> list[EvidenceItem]:
        """Validate HTML structure of output files."""
        items = []
        html_files = self._get_changed_html_files()
        if not html_files:
            return items

        total_errors = 0
        total_warnings = 0
        file_results = []

        for html_file in html_files[:50]:  # Cap at 50 files
            try:
                content = html_file.read_text(errors="replace")
                validator = _HTMLStructureValidator()
                validator.feed(content)
                validator.finalize()

                total_errors += len(validator.errors)
                total_warnings += len(validator.warnings)

                if validator.errors:
                    file_results.append(
                        {
                            "file": str(html_file.name),
                            "errors": validator.errors[:5],
                        }
                    )
            except Exception as e:
                logger.debug(f"HTML validation failed for {html_file}: {e}")

        files_checked = len(html_files)
        if files_checked > 0:
            # Score: 1.0 - (error_count / files_checked), floored at 0.0
            validity_score = max(0.0, 1.0 - (total_errors / files_checked))

            items.append(
                EvidenceItem(
                    source="web_validation",
                    metric_name="html_validity",
                    value=validity_score,
                    raw_value={
                        "files_checked": files_checked,
                        "total_errors": total_errors,
                        "total_warnings": total_warnings,
                        "file_errors": file_results[:10],
                    },
                    quality=EvidenceQuality.OBJECTIVE,
                    supports_vectors=["clarity", "coherence"],
                )
            )

        return items

    # --- Link Integrity ---

    def _collect_link_integrity(self) -> list[EvidenceItem]:
        """Check that internal links in HTML output resolve to existing files."""
        items = []
        output_dir = self._get_output_dir()
        if not output_dir or not output_dir.exists():
            return items

        html_files = self._get_changed_html_files()
        if not html_files:
            return items

        valid_links = 0
        broken_links = []
        external_count = 0
        total_internal = 0

        for html_file in html_files[:50]:
            try:
                content = html_file.read_text(errors="replace")
                base = _page_base(html_file, content, output_dir)
                # Extract href and src attributes; the <base> tag's own href is not a link
                links = re.findall(r'(?:href|src)=["\']([^"\']+)["\']', _BASE_HREF.sub("", content))

                for link in links:
                    # Skip external links (protocol-relative //host/x included), anchors, data URIs, mailto, tel
                    if link.startswith(("http://", "https://", "//", "mailto:", "tel:", "data:", "#", "javascript:")):
                        external_count += 1
                        continue

                    if base is None:  # a <base> on another host: relative links leave the site
                        external_count += 1
                        continue

                    total_internal += 1

                    # Resolve the link path
                    parsed = urlparse(link)
                    link_path = parsed.path

                    if not link_path or link_path == "/":
                        valid_links += 1
                        continue

                    # A browser resolves '/x' against the site root and 'x' / '../x'
                    # against the page's own directory. Resolving every link against
                    # the root reported dist/blog/a.html -> b.html as broken.
                    resolved = _resolve_page_path(link_path, base, output_dir)
                    if resolved.exists():
                        valid_links += 1
                    elif (resolved.parent / (resolved.name + ".html")).exists():
                        valid_links += 1  # /about -> /about.html
                    elif (resolved / "index.html").exists():
                        valid_links += 1  # /about/ -> /about/index.html
                    else:
                        broken_links.append(
                            {
                                "file": str(html_file.name),
                                "link": link,
                            }
                        )

            except Exception as e:
                logger.debug(f"Link checking failed for {html_file}: {e}")

        if total_internal > 0:
            integrity_score = valid_links / total_internal

            items.append(
                EvidenceItem(
                    source="web_validation",
                    metric_name="link_integrity",
                    value=integrity_score,
                    raw_value={
                        "valid": valid_links,
                        "broken": broken_links[:20],
                        "total_internal": total_internal,
                        "external_skipped": external_count,
                    },
                    quality=EvidenceQuality.OBJECTIVE,
                    supports_vectors=["coherence", "signal"],
                )
            )

        return items

    # --- Terminology Consistency ---

    def _collect_terminology_consistency(self) -> list[EvidenceItem]:
        """Check glossary term consistency in changed HTML files."""
        items = []
        project_path = self._get_project_path()
        if not project_path:
            return items

        config = self._get_web_config()
        glossary_name = config.get("glossary", "glossary.yaml")
        glossary_path = project_path / ".empirica" / glossary_name

        if not glossary_path.exists():
            return items  # No glossary = skip silently

        try:
            import yaml

            with open(glossary_path) as f:
                glossary = yaml.safe_load(f) or {}
        except Exception:
            return items

        terms = glossary.get("terms", [])
        if not terms:
            return items

        html_files = self._get_changed_html_files()
        if not html_files:
            return items

        violations = []
        terms_checked = 0

        for html_file in html_files[:50]:
            try:
                content = html_file.read_text(errors="replace")
                # Strip HTML tags for text matching
                text = re.sub(r"<[^>]+>", " ", content)

                for term_def in terms:
                    canonical = term_def.get("canonical", "")
                    incorrect_variants = term_def.get("incorrect", [])
                    terms_checked += 1

                    for variant in incorrect_variants:
                        if variant.lower() in text.lower():
                            violations.append(
                                {
                                    "file": str(html_file.name),
                                    "found": variant,
                                    "should_be": canonical,
                                }
                            )

            except Exception as e:
                logger.debug(f"Terminology check failed for {html_file}: {e}")

        if terms_checked > 0:
            consistency_score = max(0.0, 1.0 - (len(violations) / terms_checked))

            items.append(
                EvidenceItem(
                    source="web_terminology",
                    metric_name="term_consistency",
                    value=consistency_score,
                    raw_value={
                        "violations": violations[:20],
                        "violation_count": len(violations),
                        "terms_checked": terms_checked,
                        "files_checked": len(html_files),
                    },
                    quality=EvidenceQuality.SEMI_OBJECTIVE,
                    supports_vectors=["coherence", "clarity", "signal"],
                )
            )

        return items

    # --- Asset Verification ---

    def _collect_asset_verification(self) -> list[EvidenceItem]:
        """Check that referenced assets (images, SVGs, PDFs) exist."""
        items = []
        output_dir = self._get_output_dir()
        project_path = self._get_project_path()
        if not output_dir or not project_path:
            return items

        config = self._get_web_config()
        public_dir = project_path / config.get("public_dir", "public")

        html_files = self._get_changed_html_files()
        if not html_files:
            return items

        found_assets = 0
        missing_assets = []
        total_referenced = 0

        # Asset patterns in HTML
        asset_pattern = re.compile(
            r'(?:src|href)=["\']([^"\']+\.(?:png|jpg|jpeg|gif|svg|webp|avif|ico|pdf|mp4|webm|mp3|woff2?|ttf|eot))["\']',
            re.IGNORECASE,
        )

        for html_file in html_files[:50]:
            try:
                content = html_file.read_text(errors="replace")
                base = _page_base(html_file, content, output_dir)
                assets = asset_pattern.findall(content)

                for asset in assets:
                    if asset.startswith(("http://", "https://", "//", "data:")) or base is None:
                        continue

                    total_referenced += 1
                    clean_path = asset.lstrip("/")
                    asset_path = urlparse(asset).path

                    # Relative to the page (or its <base>), then the output root and public dir as before
                    if (
                        _resolve_page_path(asset_path, base, output_dir).exists()
                        or (output_dir / clean_path).exists()
                        or (public_dir / clean_path).exists()
                    ):
                        found_assets += 1
                    else:
                        missing_assets.append(
                            {
                                "file": str(html_file.name),
                                "asset": asset,
                            }
                        )

            except Exception as e:
                logger.debug(f"Asset verification failed for {html_file}: {e}")

        if total_referenced > 0:
            integrity_score = found_assets / total_referenced

            items.append(
                EvidenceItem(
                    source="web_assets",
                    metric_name="asset_integrity",
                    value=integrity_score,
                    raw_value={
                        "found": found_assets,
                        "missing": missing_assets[:20],
                        "total": total_referenced,
                    },
                    quality=EvidenceQuality.OBJECTIVE,
                    supports_vectors=["do", "state"],
                )
            )

        return items
