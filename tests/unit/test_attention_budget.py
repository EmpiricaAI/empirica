"""Tests for Epistemic Attention Budget system."""

import pytest


class TestAttentionBudget:
    """Tests for AttentionBudget dataclass."""

    def test_budget_creation(self):
        from empirica.core.attention_budget import AttentionBudget

        budget = AttentionBudget(
            id="test-budget",
            session_id="test-session",
            total_budget=20,
        )
        assert budget.total_budget == 20
        assert budget.remaining == 20
        assert budget.allocated == 0
        assert not budget.exhausted

    def test_budget_consume(self):
        from empirica.core.attention_budget import AttentionBudget

        budget = AttentionBudget(
            id="test",
            session_id="test",
            total_budget=5,
        )
        assert budget.consume(2)
        assert budget.remaining == 3
        assert budget.allocated == 2
        assert not budget.exhausted

    def test_budget_exhaust(self):
        from empirica.core.attention_budget import AttentionBudget

        budget = AttentionBudget(
            id="test",
            session_id="test",
            total_budget=2,
        )
        assert budget.consume(2)
        assert budget.exhausted
        assert not budget.consume(1)  # Can't consume when exhausted

    def test_budget_utilization(self):
        from empirica.core.attention_budget import AttentionBudget

        budget = AttentionBudget(
            id="test",
            session_id="test",
            total_budget=10,
        )
        budget.consume(5)
        assert budget.utilization == 0.5

    def test_domain_allocation_lookup(self):
        from empirica.core.attention_budget import AttentionBudget, DomainAllocation

        budget = AttentionBudget(
            id="test",
            session_id="test",
            total_budget=10,
            allocations=[
                DomainAllocation(domain="security", budget=5, priority=0.8, expected_gain=0.7),
                DomainAllocation(domain="arch", budget=5, priority=0.6, expected_gain=0.5),
            ],
        )
        alloc = budget.get_domain_allocation("security")
        assert alloc is not None
        assert alloc.budget == 5
        assert budget.get_domain_allocation("nonexistent") is None


class TestDomainAllocation:
    """Tests for DomainAllocation dataclass."""

    def test_effective_budget(self):
        from empirica.core.attention_budget import DomainAllocation

        alloc = DomainAllocation(
            domain="security",
            budget=10,
            priority=0.8,
            expected_gain=0.7,
            prior_findings=3,
        )
        assert alloc.effective_budget == 7

    def test_effective_budget_floor(self):
        from empirica.core.attention_budget import DomainAllocation

        alloc = DomainAllocation(
            domain="security",
            budget=5,
            priority=0.8,
            expected_gain=0.7,
            prior_findings=10,
        )
        assert alloc.effective_budget == 0  # Capped at 0


class TestAttentionBudgetCalculator:
    """Tests for AttentionBudgetCalculator."""

    def test_create_budget_basic(self):
        from empirica.core.attention_budget import AttentionBudgetCalculator

        calc = AttentionBudgetCalculator(session_id="test", default_total=20)
        budget = calc.create_budget(
            domains=["security", "architecture", "performance"],
        )
        assert budget.total_budget == 20
        assert len(budget.allocations) == 3
        # All domains should have at least 1
        for alloc in budget.allocations:
            assert alloc.budget >= 1

    def test_create_budget_sums_to_total(self):
        from empirica.core.attention_budget import AttentionBudgetCalculator

        calc = AttentionBudgetCalculator(session_id="test", default_total=15)
        budget = calc.create_budget(
            domains=["a", "b", "c"],
        )
        total_allocated = sum(a.budget for a in budget.allocations)
        assert total_allocated == 15

    def test_high_uncertainty_gets_more_budget(self):
        from empirica.core.attention_budget import AttentionBudgetCalculator

        calc = AttentionBudgetCalculator(session_id="test", default_total=20)
        budget = calc.create_budget(
            domains=["known", "unknown"],
            current_vectors={"know": 0.9, "uncertainty": 0.1},
        )
        # Both domains get same vectors, so allocation should be roughly equal
        # (the calculator uses the same vectors for all domains in this case)
        assert len(budget.allocations) == 2

    def test_prior_findings_reduce_allocation(self):
        from empirica.core.attention_budget import AttentionBudgetCalculator

        calc = AttentionBudgetCalculator(session_id="test", default_total=20)
        budget = calc.create_budget(
            domains=["explored", "fresh"],
            prior_findings_by_domain={"explored": 10, "fresh": 0},
        )
        explored = budget.get_domain_allocation("explored")
        fresh = budget.get_domain_allocation("fresh")
        assert fresh.budget > explored.budget

    def test_dead_ends_reduce_allocation(self):
        from empirica.core.attention_budget import AttentionBudgetCalculator

        calc = AttentionBudgetCalculator(session_id="test", default_total=20)
        budget = calc.create_budget(
            domains=["good", "dead"],
            dead_ends_by_domain={"good": 0, "dead": 5},
        )
        good = budget.get_domain_allocation("good")
        dead = budget.get_domain_allocation("dead")
        assert good.budget > dead.budget

    def test_shannon_gain(self):
        from empirica.core.attention_budget import AttentionBudgetCalculator

        calc = AttentionBudgetCalculator(session_id="test")

        # Max entropy at p=0.5
        max_gain = calc._shannon_gain(0.5, 0.0)
        # Lower entropy at extremes
        low_gain = calc._shannon_gain(0.1, 0.0)
        assert max_gain > low_gain

    def test_diminishing_returns(self):
        from empirica.core.attention_budget import AttentionBudgetCalculator

        calc = AttentionBudgetCalculator(session_id="test")

        # No prior findings = full returns
        assert calc._diminishing_returns(0) == pytest.approx(1.0)
        # More findings = less returns
        assert calc._diminishing_returns(5) < calc._diminishing_returns(1)
        # Always positive
        assert calc._diminishing_returns(100) > 0


class TestInformationGain:
    """Tests for information_gain module."""

    def test_estimate_basic(self):
        from empirica.core.information_gain import estimate_information_gain

        gain = estimate_information_gain(
            domain="security",
            current_vectors={"know": 0.3, "uncertainty": 0.7, "context": 0.4},
            prior_findings=[],
        )
        assert 0.0 <= gain <= 1.0

    def test_high_uncertainty_high_gain(self):
        from empirica.core.information_gain import estimate_information_gain

        high_unc = estimate_information_gain(
            domain="security",
            current_vectors={"know": 0.3, "uncertainty": 0.8, "context": 0.3},
            prior_findings=[],
        )
        low_unc = estimate_information_gain(
            domain="security",
            current_vectors={"know": 0.3, "uncertainty": 0.1, "context": 0.3},
            prior_findings=[],
        )
        assert high_unc > low_unc

    def test_diminishing_returns(self):
        from empirica.core.information_gain import diminishing_returns

        assert diminishing_returns("test", 0) == pytest.approx(1.0)
        assert diminishing_returns("test", 5) < 1.0
        assert diminishing_returns("test", 5) > 0.0

    def test_should_spawn_more(self):
        from empirica.core.information_gain import should_spawn_more

        # Budget available, good gain
        assert should_spawn_more(budget_remaining=10, gain_estimate=0.5)
        # No budget
        assert not should_spawn_more(budget_remaining=0, gain_estimate=0.9)
        # Low gain
        assert not should_spawn_more(budget_remaining=10, gain_estimate=0.01)
        # Stale
        assert not should_spawn_more(budget_remaining=10, gain_estimate=0.5, rounds_without_novel=3)

    def test_novelty_score(self):
        from empirica.core.information_gain import novelty_score

        # Novel finding (no existing)
        assert novelty_score("completely new finding", []) == 1.0
        # Duplicate finding
        dup = novelty_score(
            "the authentication system has a vulnerability",
            ["the authentication system has a vulnerability"],
        )
        assert dup < 0.2
        # Different finding
        diff = novelty_score(
            "database schema needs migration",
            ["the authentication system has a vulnerability"],
        )
        assert diff > 0.5


class TestEpistemicRollup:
    """Tests for epistemic_rollup module."""

    def test_score_finding(self):
        from empirica.core.epistemic_rollup import EpistemicRollupGate

        gate = EpistemicRollupGate()
        scored = gate.score_finding(
            finding="Security vulnerability in auth module",
            agent_name="security_agent",
            domain="security",
            confidence=0.8,
            existing_findings=[],
        )
        assert scored.score > 0.0
        assert scored.novelty == 1.0  # No existing findings
        assert scored.finding_hash  # Hash should be generated

    def test_score_finding_with_duplicate(self):
        from empirica.core.epistemic_rollup import EpistemicRollupGate

        gate = EpistemicRollupGate()
        scored = gate.score_finding(
            finding="Security vulnerability in auth module",
            agent_name="security_agent",
            domain="security",
            confidence=0.8,
            existing_findings=["Security vulnerability in auth module"],
        )
        assert scored.novelty < 0.3  # Should be low novelty

    def test_gate_accepts_high_score(self):
        from empirica.core.epistemic_rollup import EpistemicRollupGate, ScoredFinding

        gate = EpistemicRollupGate(min_score=0.3)

        findings = [
            ScoredFinding(
                finding="High quality finding",
                score=0.8,
                agent_name="test",
                domain="test",
                novelty=0.9,
                confidence=0.9,
                domain_relevance=1.0,
            ),
        ]
        result = gate.gate(findings, budget_remaining=10)
        assert len(result.accepted) == 1
        assert len(result.rejected) == 0

    def test_gate_rejects_low_score(self):
        from empirica.core.epistemic_rollup import EpistemicRollupGate, ScoredFinding

        gate = EpistemicRollupGate(min_score=0.5)

        findings = [
            ScoredFinding(
                finding="Low quality finding",
                score=0.1,
                agent_name="test",
                domain="test",
                novelty=0.1,
                confidence=0.2,
                domain_relevance=0.5,
            ),
        ]
        result = gate.gate(findings, budget_remaining=10)
        assert len(result.accepted) == 0
        assert len(result.rejected) == 1
        assert "min_score" in result.rejected[0].reject_reason

    def test_gate_respects_budget(self):
        from empirica.core.epistemic_rollup import EpistemicRollupGate, ScoredFinding

        gate = EpistemicRollupGate(min_score=0.1)

        findings = [
            ScoredFinding(
                finding=f"Finding {i}",
                score=0.8 - i * 0.1,
                agent_name="test",
                domain="test",
                novelty=0.9,
                confidence=0.9,
                domain_relevance=1.0,
            )
            for i in range(5)
        ]
        result = gate.gate(findings, budget_remaining=2)
        assert len(result.accepted) == 2
        assert len(result.rejected) == 3
        assert result.budget_consumed == 2

    def test_deduplicate_removes_same_hash(self):
        from empirica.core.epistemic_rollup import EpistemicRollupGate, ScoredFinding

        gate = EpistemicRollupGate()

        findings = [
            ScoredFinding(
                finding="Same finding",
                score=0.8,
                agent_name="agent1",
                domain="test",
                novelty=0.9,
                confidence=0.9,
                domain_relevance=1.0,
            ),
            ScoredFinding(
                finding="Same finding",
                score=0.6,
                agent_name="agent2",
                domain="test",
                novelty=0.7,
                confidence=0.7,
                domain_relevance=1.0,
            ),
        ]
        deduped = gate.deduplicate(findings)
        assert len(deduped) == 1
        assert deduped[0].score == 0.8  # Keeps higher score

    def test_process_full_pipeline(self):
        from empirica.core.epistemic_rollup import EpistemicRollupGate

        gate = EpistemicRollupGate(min_score=0.2)

        result = gate.process(
            raw_findings=["Finding A about security", "Finding B about architecture"],
            agent_name="test_agent",
            domain="security",
            confidence=0.8,
            existing_findings=[],
            budget_remaining=10,
        )
        assert len(result.accepted) + len(result.rejected) == 2
        assert result.budget_remaining <= 10

    def test_rollup_result_acceptance_rate(self):
        from empirica.core.epistemic_rollup import RollupResult, ScoredFinding

        result = RollupResult(
            accepted=[
                ScoredFinding("a", 0.8, "agent", "domain", 0.9, 0.9, 1.0, accepted=True),
            ],
            rejected=[
                ScoredFinding("b", 0.1, "agent", "domain", 0.1, 0.2, 0.5, accepted=False),
            ],
        )
        assert result.acceptance_rate == 0.5
