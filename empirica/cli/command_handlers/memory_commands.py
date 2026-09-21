"""
Memory Management CLI Command Handlers

Exposes the memory budget infrastructure (attention, context, rollup, information gain)
as first-class CLI commands for epistemic memory management.
"""

import json
import logging

from ..cli_utils import handle_cli_error

logger = logging.getLogger(__name__)


def handle_memory_prime_command(args):
    """
    Handle memory-prime command: Allocate attention budget across domains.

    Uses AttentionBudgetCalculator with Shannon information gain to distribute
    investigation budget across multiple domains with diminishing returns.
    """
    try:
        from empirica.core.attention_budget import (
            AttentionBudgetCalculator,
            persist_budget,
        )

        # Parse JSON inputs
        try:
            domains = json.loads(args.domains)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON for --domains: {e}")
            return None

        try:
            prior_findings = json.loads(args.prior_findings)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON for --prior-findings: {e}")
            return None

        try:
            dead_ends = json.loads(args.dead_ends)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON for --dead-ends: {e}")
            return None

        # Create calculator and allocate budget
        calculator = AttentionBudgetCalculator(session_id=args.session_id)
        budget = calculator.create_budget(
            domains=domains,
            current_vectors={"know": args.know, "uncertainty": args.uncertainty},
            prior_findings_by_domain=prior_findings,
            dead_ends_by_domain=dead_ends,
            total_budget=args.budget,
        )

        # Optionally persist
        if getattr(args, "persist", False):
            persist_budget(budget)

        result = budget.to_dict()

        if args.output == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"🎯 Attention Budget Allocated (total: {budget.total_budget})")
            print("=" * 60)
            for alloc in budget.allocations:
                bar_len = int(alloc.budget / budget.total_budget * 30)
                bar = "█" * bar_len + "░" * (30 - bar_len)
                print(f"  {alloc.domain:20s} [{bar}] {alloc.budget:2d} (gain: {alloc.expected_gain:.2f})")
            print("=" * 60)
            print(f"Budget ID: {budget.id}")
            if getattr(args, "persist", False):
                print("✓ Persisted to database")

        return None  # Avoid cli_core.py double-printing

    except Exception as e:
        handle_cli_error(e, "Memory prime", getattr(args, "verbose", False))
        return None


def handle_memory_scope_command(args):
    """
    Handle memory-scope command: Retrieve memories by scope using zone tiers.

    Uses ContextBudgetManager to find items in ANCHOR/WORKING/CACHE zones
    based on scope vectors and priority thresholds.
    """
    try:
        from empirica.core.context_budget import (
            ContentType,
            MemoryZone,
            get_budget_manager,
        )

        # Get or create the budget manager
        try:
            manager = get_budget_manager(session_id=args.session_id)
        except ValueError:
            # First time - create manager
            manager = get_budget_manager(session_id=args.session_id)

        # Determine zone filter
        zone_filter = None
        if args.zone != "all":
            zone_map = {
                "anchor": MemoryZone.ANCHOR,
                "working": MemoryZone.WORKING,
                "cache": MemoryZone.CACHE,
            }
            zone_filter = zone_map.get(args.zone)

        # Determine content type filter
        content_filter = None
        if args.content_type:
            try:
                content_filter = ContentType(args.content_type)
            except ValueError:
                print(f"Warning: Unknown content type '{args.content_type}'")

        # Find items
        items = manager.find_items(
            zone=zone_filter,
            content_type=content_filter,
            min_priority=args.min_priority,
        )

        # Sort by priority (derived from scope vectors)
        # Higher scope_breadth = prefer WORKING zone
        # Higher scope_duration = prefer higher epistemic_value
        def scope_adjusted_priority(item):
            base = item.compute_priority()
            breadth_boost = args.scope_breadth if item.zone == MemoryZone.WORKING else 0
            duration_boost = args.scope_duration * item.epistemic_value
            return base + breadth_boost + duration_boost

        items.sort(key=scope_adjusted_priority, reverse=True)

        result = {
            "session_id": args.session_id,
            "scope": {"breadth": args.scope_breadth, "duration": args.scope_duration},
            "zone_filter": args.zone,
            "items": [item.to_dict() for item in items],
            "count": len(items),
        }

        if args.output == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"📦 Memory Scope Query (scope: breadth={args.scope_breadth}, duration={args.scope_duration})")
            print("=" * 70)
            if not items:
                print("  No items found matching criteria")
            else:
                for item in items[:10]:  # Show top 10
                    zone_icon = {"anchor": "⚓", "working": "⚙️", "cache": "💾"}.get(item.zone.value, "?")
                    print(
                        f"  {zone_icon} {item.label[:50]:50s} {item.estimated_tokens:5d}t  p={item.compute_priority():.2f}"
                    )
                if len(items) > 10:
                    print(f"  ... and {len(items) - 10} more")
            print("=" * 70)

        return None  # Avoid cli_core.py double-printing

    except Exception as e:
        handle_cli_error(e, "Memory scope", getattr(args, "verbose", False))
        return None


def handle_memory_value_command(args):
    """
    Handle memory-value command: Retrieve memories ranked by information gain.

    Uses information_gain.py to score memories by novelty and expected gain,
    respecting a token budget.
    """
    try:
        from empirica.core.context_budget import estimate_tokens
        from empirica.core.information_gain import estimate_information_gain, novelty_score
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()

        # Get project_id
        project_id = args.project_id
        if not project_id:
            session = db.get_session(args.session_id)
            if session:
                project_id = session.get("project_id")

        if not project_id:
            print("Error: Could not determine project_id")
            return None

        # Get findings/unknowns from project
        findings = db.get_project_findings(project_id) or []
        unknowns = db.get_project_unknowns(project_id) or []
        db.close()

        # Combine and score by information gain
        all_items = []
        existing_texts = []

        for f in findings:
            text = f.get("finding", "")
            tokens = estimate_tokens(text)
            novelty = novelty_score(text, existing_texts) if existing_texts else 1.0
            gain = estimate_information_gain(
                domain=f.get("subject", "general"),
                current_vectors={"know": args.know if hasattr(args, "know") else 0.5, "uncertainty": 0.5},
                prior_findings=existing_texts,
            )
            value = (gain * novelty) / max(tokens, 1) * 1000  # Value per 1k tokens
            all_items.append(
                {
                    "type": "finding",
                    "text": text,
                    "tokens": tokens,
                    "novelty": novelty,
                    "gain": gain,
                    "value": value,
                    "subject": f.get("subject"),
                }
            )
            existing_texts.append(text)

        for u in unknowns:
            if u.get("is_resolved"):
                continue
            text = u.get("unknown", "")
            tokens = estimate_tokens(text)
            novelty = novelty_score(text, existing_texts) if existing_texts else 1.0
            # Unknowns have slightly higher base gain (they represent knowledge gaps)
            gain = (
                estimate_information_gain(
                    domain=u.get("subject", "general"),
                    current_vectors={"know": 0.4, "uncertainty": 0.6},
                    prior_findings=existing_texts,
                )
                * 1.2
            )
            value = (gain * novelty) / max(tokens, 1) * 1000
            all_items.append(
                {
                    "type": "unknown",
                    "text": text,
                    "tokens": tokens,
                    "novelty": novelty,
                    "gain": gain,
                    "value": value,
                    "subject": u.get("subject"),
                }
            )
            existing_texts.append(text)

        # Filter by min gain
        all_items = [i for i in all_items if i["gain"] >= args.min_gain]

        # Sort by value (gain/token)
        all_items.sort(key=lambda x: x["value"], reverse=True)

        # Select within budget
        selected = []
        total_tokens = 0
        for item in all_items:
            if total_tokens + item["tokens"] <= args.budget:
                selected.append(item)
                total_tokens += item["tokens"]

        result = {
            "session_id": args.session_id,
            "project_id": project_id,
            "query": args.query,
            "budget": args.budget,
            "tokens_used": total_tokens,
            "items_selected": len(selected),
            "items_available": len(all_items),
            "items": selected,
        }

        if args.output == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"💎 Memory Value Retrieval (budget: {args.budget} tokens)")
            print("=" * 70)
            print(f"Selected {len(selected)} items using {total_tokens} tokens")
            print("-" * 70)
            for item in selected[:10]:
                icon = "📝" if item["type"] == "finding" else "❓"
                print(f"  {icon} [{item['tokens']:4d}t] v={item['value']:.2f} | {item['text'][:50]}...")
            if len(selected) > 10:
                print(f"  ... and {len(selected) - 10} more")
            print("=" * 70)

        return None  # Avoid cli_core.py double-printing

    except Exception as e:
        handle_cli_error(e, "Memory value", getattr(args, "verbose", False))
        return None


def handle_pattern_check_command(args):
    """
    Handle pattern-check command: Real-time pattern sentinel.

    Checks current approach against dead-ends and mistake patterns.
    Lightweight enough to call frequently during work.
    """
    try:
        from empirica.core.qdrant.pattern_retrieval import check_against_patterns
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()

        # Get project_id
        project_id = args.project_id
        if not project_id:
            session = db.get_session(args.session_id)
            if session:
                project_id = session.get("project_id")

        db.close()

        if not project_id:
            print("Error: Could not determine project_id")
            return None

        # Check patterns
        warnings = check_against_patterns(
            project_id=project_id,
            current_approach=args.approach,
            vectors={"know": args.know, "uncertainty": args.uncertainty},
            threshold=args.threshold,
        )

        # Compute risk level
        dead_end_count = len(warnings.get("dead_end_matches", []))
        # mistake_risk can be string or None in current implementation
        mistake_risk_val = warnings.get("mistake_risk")
        mistake_risk = 0.5 if mistake_risk_val else 0.0  # Treat non-None as medium risk
        calibration_bias = warnings.get("calibration_bias", {})

        risk_level = "low"
        if dead_end_count > 0 or mistake_risk > 0.5:
            risk_level = "high"
        elif mistake_risk > 0.3 or calibration_bias:
            risk_level = "medium"

        result = {
            "session_id": args.session_id,
            "project_id": project_id,
            "approach": args.approach,
            "risk_level": risk_level,
            "dead_end_matches": warnings.get("dead_end_matches", []),
            "mistake_risk": mistake_risk,
            "calibration_bias": calibration_bias,
            "recommendation": warnings.get("recommendation", "proceed"),
        }

        if args.output == "json":
            print(json.dumps(result, indent=2))
        else:
            risk_icons = {"low": "✅", "medium": "⚠️", "high": "🛑"}
            print(f"{risk_icons[risk_level]} Pattern Check: {risk_level.upper()}")
            print("=" * 60)
            print(f"Approach: {args.approach[:60]}...")
            print("-" * 60)

            if warnings.get("dead_end_matches"):
                print("☠️ Dead-end matches:")
                for de in warnings["dead_end_matches"][:3]:
                    print(f"   • {de.get('approach', '')[:50]}...")
                    print(f"     Why failed: {de.get('why_failed', '')[:50]}...")

            if mistake_risk > 0.3:
                print(f"⚠️ Mistake risk: {mistake_risk:.0%}")
                print("   High uncertainty + low know is a historical mistake pattern")

            if calibration_bias and isinstance(calibration_bias, dict):
                print("📊 Calibration warnings:")
                for vector, bias in calibration_bias.items():
                    if isinstance(bias, (int, float)):
                        print(f"   {vector}: {'+' if bias > 0 else ''}{bias:.2f}")
                    else:
                        print(f"   {vector}: {bias}")

            if risk_level == "low":
                print("✅ No concerning patterns detected. Proceed with confidence.")

            print("=" * 60)

        return None  # Avoid cli_core.py double-printing

    except Exception as e:
        handle_cli_error(e, "Pattern check", getattr(args, "verbose", False))
        return None


def _collect_child_findings(db, parent_session_id):
    """Collect findings from all child sessions of a parent.

    Returns (children, all_findings) tuple.
    """
    cursor = db.conn.cursor()
    cursor.execute(
        """
        SELECT session_id, ai_id FROM sessions
        WHERE parent_session_id = ?
        ORDER BY start_time
    """,
        (parent_session_id,),
    )
    children = [dict(row) for row in cursor.fetchall()]

    all_findings = []
    for child in children:
        cursor.execute(
            """
            SELECT finding, impact, subject
            FROM project_findings
            WHERE session_id = ?
            ORDER BY created_timestamp DESC
        """,
            (child["session_id"],),
        )
        child_findings = [dict(row) for row in cursor.fetchall()]
        for f in child_findings:
            all_findings.append(
                {
                    "finding": f.get("finding", ""),
                    "agent_name": child.get("ai_id", "unknown"),
                    "session_id": child["session_id"],
                    "impact": f.get("impact", 0.5),
                    "subject": f.get("subject"),
                }
            )

    return children, all_findings


def handle_session_rollup_command(args):
    """
    Handle session-rollup command: Aggregate findings from parallel agents.

    Uses EpistemicRollupGate to score, deduplicate, and gate findings
    from child sessions into the parent session.
    """
    try:
        from empirica.core.epistemic_rollup import (
            EpistemicRollupGate,
            log_rollup_decision,
        )
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()

        if db.conn is None:
            print("Error: Database connection unavailable")
            return {"ok": False, "error": "Database unavailable"}

        children, all_findings = _collect_child_findings(db, args.parent_session_id)

        if not children:
            print(f"No child sessions found for parent {args.parent_session_id}")
            db.close()
            return {"ok": False, "error": "No child sessions"}

        project_id = args.project_id
        if not project_id:
            parent = db.get_session(args.parent_session_id)
            if parent:
                project_id = parent.get("project_id")
        db.close()

        if not all_findings:
            print("No findings to rollup from child sessions")
            return {"ok": True, "accepted": 0, "rejected": 0}

        gate = EpistemicRollupGate(
            min_score=args.min_score,
            jaccard_threshold=args.jaccard_threshold,
            use_semantic_dedup=args.semantic_dedup,
        )

        scored = []
        for f in all_findings:
            sf = gate.score_finding(
                finding=f["finding"],
                agent_name=f["agent_name"],
                domain=f.get("subject", "general"),
                confidence=f.get("impact", 0.5),
                existing_findings=[s.finding for s in scored],
                domain_relevance=1.0,
            )
            scored.append(sf)

        deduped = gate.deduplicate(scored, project_id)
        result = gate.gate(deduped, args.budget)

        if args.log_decisions:
            log_rollup_decision(
                session_id=args.parent_session_id,
                budget_id=None,
                result=result,
            )

        output = {
            "parent_session_id": args.parent_session_id,
            "child_sessions": len(children),
            "total_findings": len(all_findings),
            "after_dedup": len(deduped),
            "accepted": len(result.accepted),
            "rejected": len(result.rejected),
            "acceptance_rate": result.acceptance_rate,
            "total_score": result.total_score,
            "budget_consumed": result.budget_consumed,
            "budget_remaining": result.budget_remaining,
            "accepted_findings": [f.to_dict() for f in result.accepted],
            "rejected_findings": [f.to_dict() for f in result.rejected],
        }

        if args.output == "json":
            print(json.dumps(output, indent=2))
        else:
            print(f"🔄 Session Rollup: {args.parent_session_id[:8]}...")
            print("=" * 70)
            print(f"Child sessions: {len(children)}")
            print(f"Total findings: {len(all_findings)} → Deduped: {len(deduped)} → Accepted: {len(result.accepted)}")
            print(f"Acceptance rate: {result.acceptance_rate:.0%}")
            print("-" * 70)
            if result.accepted:
                print("✅ Accepted findings:")
                for f in result.accepted[:5]:
                    print(f"   [{f.agent_name}] {f.finding[:50]}... (score: {f.score:.2f})")
                if len(result.accepted) > 5:
                    print(f"   ... and {len(result.accepted) - 5} more")
            if result.rejected:
                print(f"❌ Rejected: {len(result.rejected)} findings")
            print("=" * 70)

        return None

    except Exception as e:
        handle_cli_error(e, "Session rollup", getattr(args, "verbose", False))
        return None


def handle_memory_report_command(args):
    """
    Handle memory-report command: Get context budget report.

    Like /proc/meminfo for the AI context window.
    """
    try:
        from empirica.core.context_budget import get_budget_manager

        # Get or create the budget manager
        try:
            manager = get_budget_manager(session_id=args.session_id)
        except ValueError:
            manager = get_budget_manager(session_id=args.session_id)

        report = manager.get_budget_report()
        result = report.to_dict()

        if args.output == "json":
            print(json.dumps(result, indent=2))
        else:
            print("📊 Context Budget Report")
            print("=" * 60)

            # Utilization bar
            util_pct = int(report.utilization * 50)
            util_bar = "█" * util_pct + "░" * (50 - util_pct)
            print(f"Total: [{util_bar}] {report.utilization:.0%}")
            print(f"       {report.total_used:,} / {report.total_capacity:,} tokens")
            print("-" * 60)

            # Zone breakdown
            zones = [
                ("⚓ ANCHOR", report.anchor_used, report.anchor_limit, report.anchor_items),
                ("⚙️ WORKING", report.working_used, report.working_target, report.working_items),
                ("💾 CACHE", report.cache_used, report.cache_limit, report.cache_items),
            ]
            for name, used, limit, items in zones:
                pct = used / limit * 100 if limit > 0 else 0
                bar_len = int(pct / 100 * 20)
                bar = "█" * bar_len + "░" * (20 - bar_len)
                print(f"{name:12s} [{bar}] {used:,}/{limit:,}t ({items} items)")

            print("-" * 60)
            if report.under_pressure:
                print("⚠️ MEMORY PRESSURE DETECTED")
            if report.eviction_candidates > 0:
                print(f"🗑️ Eviction candidates: {report.eviction_candidates}")

            print("=" * 60)

        # CC Memory Layer Stats
        try:
            from empirica.core.memory_manager import get_memory_stats

            mem_stats = get_memory_stats()
            if "error" not in mem_stats:
                if args.output == "json":
                    result["cc_memory"] = mem_stats
                else:
                    print("\n📁 Claude Code Memory Layer")
                    print("-" * 60)
                    print(f"  Memory dir:  {mem_stats.get('memory_dir', 'N/A')}")
                    print(
                        f"  Files:       {mem_stats.get('file_count', 0)} ({mem_stats.get('total_size_bytes', 0) // 1024}KB)"
                    )
                    print(f"  MEMORY.md:   {mem_stats.get('memory_md_lines', 0)} lines (cap: 200)")
                    has_auto = mem_stats.get("memory_md_has_auto_section", False)
                    auto_lines = mem_stats.get("auto_section_lines", 0)
                    print(f"  Auto section: {'Yes' if has_auto else 'No'} ({auto_lines} lines)")
                    promoted = [f for f in mem_stats.get("files", []) if f["name"].startswith("promoted_")]
                    manual = [f for f in mem_stats.get("files", []) if not f["name"].startswith("promoted_")]
                    print(f"  Manual files: {len(manual)}")
                    print(f"  Promoted:    {len(promoted)}")
                    print("=" * 60)
        except Exception:
            pass  # CC memory stats are optional enrichment

        return None  # Avoid cli_core.py double-printing

    except Exception as e:
        handle_cli_error(e, "Memory report", getattr(args, "verbose", False))
        return None
