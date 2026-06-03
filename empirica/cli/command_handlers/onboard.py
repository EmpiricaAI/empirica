"""
Onboarding Command - Interactive Introduction to Empirica

Shows the current Empirica capabilities, epistemic transaction workflow, and quick start guide.
"""

import sys

from ..cli_utils import handle_cli_error


def handle_onboard_command(args):
    """Interactive onboarding guide for new users"""
    try:
        ai_id = getattr(args, 'ai_id', None)
        if not ai_id:
            try:
                from empirica.utils.session_resolver import InstanceResolver
                ai_id = InstanceResolver.ai_id()
            except Exception:
                ai_id = None
        if not ai_id:
            # Final fallback for first-run cases where the project isn't
            # initialized yet — basename derivation handled by setup-claude-code
            # at project-init time. Until then, surface a generic example.
            ai_id = 'empirica'

        print(f"""
╔══════════════════════════════════════════════════════════════════════╗
║                                                                      ║
║  Empirica - Epistemic Self-Awareness for AI Agents                   ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝

Empirica helps AI agents track what they KNOW, what they can DO, and
how UNCERTAIN they are - throughout any task. It measures learning,
detects overconfidence, and builds calibration over time.

═══════════════════════════════════════════════════════════════════════

CORE CONCEPTS

1. 13-Vector Epistemic State
   Every assessment uses 13 dimensions to track your knowledge:

   Foundation:    KNOW, DO, CONTEXT
   Comprehension: CLARITY, COHERENCE, SIGNAL, DENSITY
   Execution:     STATE, CHANGE, COMPLETION, IMPACT
   Gate:          ENGAGEMENT
   Meta:          UNCERTAINTY (higher = more uncertain)

2. Epistemic Transaction Workflow
   PREFLIGHT --> CHECK --> POSTFLIGHT --> POST-TEST --> COMPLIANCE

   - PREFLIGHT:  Assess baseline + declare domain/criticality
   - CHECK:      Sentinel gate - ready to act? (domain-aware thresholds)
   - POSTFLIGHT: Measure learning delta (what did you learn?)
   - POST-TEST:  Deterministic checks inform your assessment
   - COMPLIANCE: Domain checklist - iterate until all checks pass

3. Noetic vs Praxic Phases
   - Noetic:  Investigation (read, search, explore, log findings)
   - Praxic:  Action (write code, edit files, commit)
   - The Sentinel gates praxic tools until CHECK passes

4. Three-Vector Calibration
   - Self-assessed: Your PREFLIGHT/POSTFLIGHT predictions
   - Observed:      What deterministic services measured (tests, lint, etc.)
   - Grounded:      Your reasoned synthesis with rationale
   - Services INFORM - they don't score. You produce the calibration.
   - Brier scoring measures your predictions of check outcomes

5. Domain Compliance
   - Domains (default, cybersec, docs, remote-ops) define checklists
   - Criticality (low/medium/high/critical) scales required checks
   - Checks are tiered: lint+complexity always, tests at goal close
   - Results cached by content hash - same files = instant recheck

═══════════════════════════════════════════════════════════════════════

QUICK START

Step 1: Initialize project (once per repo)
   $ cd /path/to/your/git/repo
   $ empirica project-init

Step 2: Create a session
   $ empirica session-create --ai-id {ai_id} --output json

Step 3: Load project context
   $ empirica project-bootstrap --output json

Step 4: Create a goal for your task
   $ empirica goals-create --objective "What you're trying to do"

Step 5: Run PREFLIGHT (opens a transaction)
   $ empirica preflight-submit - << 'EOF'
   {{
     "task_context": "What you're about to do",
     "work_type": "code",
     "domain": "default",
     "criticality": "medium",
     "vectors": {{"know": 0.6, "uncertainty": 0.4, "context": 0.7, "clarity": 0.8}},
     "reasoning": "Honest assessment of current state"
   }}
   EOF
   # domain/criticality are optional — set them for compliance checks

Step 6: Investigate (noetic phase)
   - Read code, search patterns, explore
   - Log sources as you discover them:
     $ empirica source-add --title "API docs" --source-type doc --url "https://..."
   - Log findings WITH source refs:
     $ empirica finding-log --finding "Discovered X works by Y" --impact 0.7 --source <source-id>
     $ empirica unknown-log --unknown "Need to investigate Z"
     $ empirica deadend-log --approach "Tried X" --why-failed "Failed because Y"

Step 7: CHECK gate (when ready to act)
   $ empirica check-submit - << 'EOF'
   {{
     "vectors": {{"know": 0.75, "uncertainty": 0.3, "context": 0.8, "clarity": 0.85}},
     "reasoning": "Why I'm ready to proceed"
   }}
   EOF

Step 8: Act (praxic phase) - write code, run tests, commit
   - Log decisions WITH evidence:
     $ empirica decision-log --choice "Use pattern X" --rationale "Because Y" --evidence <finding-id>

Step 9: Complete your goal
   $ empirica goals-complete --goal-id <ID> --reason "Done because..."

Step 10: POSTFLIGHT (closes the transaction)
   $ empirica postflight-submit - << 'EOF'
   {{
     "vectors": {{"know": 0.85, "uncertainty": 0.2, "context": 0.9, "clarity": 0.9}},
     "reasoning": "What I learned - compare to PREFLIGHT"
   }}
   EOF

═══════════════════════════════════════════════════════════════════════

KEY CAPABILITIES

Noetic Artifacts (breadcrumbs - log as you work):
   $ empirica source-add --title "..." --source-type doc  # External sources
   $ empirica finding-log --finding "..." --source <id>   # What was learned (with provenance)
   $ empirica unknown-log --unknown "..."                 # What's unclear
   $ empirica deadend-log --approach "..."                # Failed approaches
   $ empirica assumption-log --assumption "..."           # Unverified beliefs
   $ empirica decision-log --choice "..." --evidence <id> # Choices (with evidence)

Praxic Artifacts (goals - track progress):
   $ empirica goals-create --objective "..."     # Create goal
   $ empirica goals-add-task --goal-id <ID>      # Add task
   $ empirica goals-complete --goal-id <ID>      # Complete goal
   $ empirica goals-list                         # Show active goals

Project Management:
   $ empirica project-init                       # Initialize in CWD
   $ empirica project-bootstrap                  # Load project context
   $ empirica project-list                       # List all projects
   $ empirica project-switch <name>              # Switch project

Domain Compliance:
   $ empirica domain-list                              # List domains + criticalities
   $ empirica domain-show cybersec                     # Show checklist for a domain
   $ empirica domain-resolve code --domain cybersec    # What checks would run?
   $ empirica resolve <artifact-id>                    # Resolve any artifact by ID

Calibration Reports:
   $ empirica calibration-report                        # Grounded verification
   $ empirica calibration-report --learning-trajectory  # Self-referential delta
   $ empirica calibration-report --trajectory           # Trend over time

Semantic Search (requires Qdrant):
   $ empirica project-search --task "query"      # Search past learnings
   $ empirica project-embed                      # Sync to vector store

Session Management:
   $ empirica sessions-show --session-id <ID>    # View session details
   $ empirica session-snapshot <ID>              # Save snapshot
   $ empirica handoff-create ...                 # Create handoff report

═══════════════════════════════════════════════════════════════════════

TRANSACTION DISCIPLINE + EPISTEMIC PLANNING

A transaction = one measured chunk of work. PREFLIGHT opens a measurement
window. POSTFLIGHT closes it, runs compliance checks, and captures learning.

   Session Start
     +-- Create goals (from task description)
     +-- Transaction 1: Goal A (domain=default, criticality=medium)
     |    PREFLIGHT -> [noetic: investigate] -> CHECK -> [praxic: implement]
     |    -> POSTFLIGHT -> [compliance: lint ✓ complexity ✓ tests deferred]
     +-- Transaction 2: Goal B (informed by T1's findings)
     |    PREFLIGHT -> [noetic: investigate] -> CHECK -> [praxic: implement]
     |    -> POSTFLIGHT -> [compliance: lint ✓ complexity ✓ tests ✓ (goal close)]
     +-- Release
          Tests run at goal_completion tier, dep_audit at release tier

Use the /epistemic-transaction skill to plan multi-goal work:
   1. Interview the task (clarify scope)
   2. Explore the codebase (noetic tools)
   3. Decompose into goals (empirica goals-create)
   4. Generate transaction plan (estimated vectors per goal)
   5. Execute with PREFLIGHT/CHECK/POSTFLIGHT per transaction

Use the /empirica-constitution skill for routing decisions:
   - Which mechanism for this situation?
   - Cross-project writing (--project-id <name>)
   - When to checkpoint, when to compact

Between transactions: close completed goals, resolve unknowns,
verify or falsify assumptions. Each goal = one transaction.

═══════════════════════════════════════════════════════════════════════

KEY PRINCIPLES

1. Honest Self-Assessment
   Rate what you ACTUALLY know, not what you hope to figure out.
   High uncertainty is valid data, not failure.

2. Measure Learning
   Compare PREFLIGHT vs POSTFLIGHT to see epistemic growth:
   - KNOW increase = learned domain knowledge
   - UNCERTAINTY decrease = reduced ambiguity
   - Three-vector model: your assessment + service observations + grounded state
   - Services inform your score — they don't replace it

3. Log As You Discover
   Findings, unknowns, dead-ends - log them as they happen.
   This builds searchable memory and prevents re-exploration.

4. The Sentinel Is Your Ally
   It gates praxic action until you've investigated enough.
   Honest measurement makes you better. Gaming it only hurts yourself.

═══════════════════════════════════════════════════════════════════════

INTEGRATION OPTIONS

Claude Code (recommended):
   $ empirica setup-claude-code
   # Installs plugin, hooks, CLAUDE.md, MCP server

MCP Server (for any AI agent):
   $ empirica mcp-list-tools  # inspection only
   # Exposes Empirica as MCP tools

Python API:
   from empirica.data.session_database import SessionDatabase
   db = SessionDatabase(project_path="/path/to/project")
   session_id = db.create_session(ai_id="{ai_id}")
   db.close()

═══════════════════════════════════════════════════════════════════════

RESTART RECOVERY

If you restart your machine, terminal, or tmux:
- Open transactions are preserved in .empirica/active_transaction*.json
- On next session start, orphaned transactions are auto-adopted
- The session resumes with its transaction and project context intact
- Project context maps via CWD → .empirica/project.yaml

Multi-terminal (tmux panes):
- Each pane gets isolated instance files (TMUX_PANE-keyed)
- Concurrent projects in different panes won't interfere
- After tmux restart, pane IDs change but transactions survive

═══════════════════════════════════════════════════════════════════════

NEXT STEPS

1. Initialize your project:   empirica project-init
2. Set up Claude Code:        empirica setup-claude-code
3. Create your first session:  empirica session-create --ai-id {ai_id}
4. Run your first PREFLIGHT:  empirica preflight-submit -
5. Check your calibration:    empirica calibration-report

For help with any command:
   $ empirica <command> --help

For full command list:
   $ empirica --help
""")

    except Exception as e:
        handle_cli_error(e, "Onboarding", getattr(args, 'verbose', False))
        sys.exit(1)
