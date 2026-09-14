# Test quality tools — asking what coverage cannot

Coverage answers *"was this line executed?"*. A green, high-coverage suite can
still be pinning a defect, because none of these are the same question:

| Question | Tool |
|---|---|
| Could this test have failed **on any wrong answer**? | `scripts/audit_test_assertions.py` |
| Has this test quietly **stopped meaning what it says**? | `scripts/test_freshness_audit.py` |
| Could this test have failed **on the bug it was written for**? | `scripts/negative_control.py` |

They do not overlap, and a test can pass all of the first two and still fail the
third. That is not hypothetical — it is why the third exists.

---

## `negative_control.py` — would these tests have caught this bug?

```bash
python3 scripts/negative_control.py --human          # HEAD
python3 scripts/negative_control.py <sha> --human
python3 scripts/negative_control.py <sha> --strict   # exit 1 on a finding
```

Takes the commit's **tests**, puts them against the parent commit's **source**,
and runs them. At least one must fail. The property is already in git — a bugfix
commit contains both halves — so there is nothing to annotate and nothing to keep
up to date.

**Why it is needed at all.** For a *live* defect this check is free: you watched
the test go red before you fixed it. For a **latent** one there is no red phase,
because the broken branch is unreachable from natural data — so the test's
ability to fail is an untested claim sitting inside a green suite. Four
timestamp-ordering tests went green immediately here and only a hand revert of the
fix showed that three of them could fail at all.

| Outcome | Meaning |
|---|---|
| `DISCRIMINATES` | at least one test failed against the pre-fix source — the good result |
| `VACUOUS` | every test passed against the pre-fix source |
| `NOT_APPLICABLE` | no tests changed, or no source changed — **counted separately, never as a pass** |

`VACUOUS` is a finding **only when the commit calls itself a fix**. A refactor
whose tests pass against the parent is expected and says nothing — conflating the
two would make the tool noise, which is how tools like this get disabled.

It never touches your working tree: the parent's source is assembled in a
throwaway git worktree. A source file the commit *added* is removed rather than
reverted, since a test for code that did not yet exist could not have passed
against the parent.

---

## `audit_test_assertions.py` — could this test fail at all?

Finds tests with no reachable assertion. Resolves one level of same-module helper
calls first, because a naive AST scan counts `_expect(401, None)` as dead and
reports a number nobody can act on. Separates a deliberate *must not raise*
contract (weak but honest) from genuinely dead tests.

## `test_freshness_audit.py` — does this test still mean what it says?

Four checks: stale fixture schemas (a hand-built `CREATE TABLE` missing columns
the real schema has — the dominant vector), unfalsifiable tests, tautological
asserts, and tests frozen over churning code. `--baseline` prints counts only:
**a rising count is the signal, not the absolute number.**

---

## None of them run in CI, on purpose

All three report; none gate. A sweep that blocks CI on a heuristic is one people
disable, and a disabled check is worse than no check because it reads as covered.
Run them at a release, after a refactor, or when a green suite feels too easy —
`/eat-the-broccoli` is the fuller version of that instinct.

`--strict` exists for when you want one wired into a pipeline deliberately.
