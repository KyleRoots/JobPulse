---
description: Mandatory pre-implementation analysis for all JobPulse code changes. Perform blast radius assessment, risk analysis, and deployment planning before writing any code.
---

# Production-Ready Change Protocol

> **MANDATORY**: All code changes to JobPulse must include this analysis BEFORE implementation begins.

## Step 1: Change Summary

Before writing any code, document:

```
CHANGE SUMMARY:
- Primary objective: [what problem is being solved]
- Files to be modified: [list with approximate line ranges]
- Functions/methods to be changed: [specific names]
```

## Step 2: Blast Radius Assessment

Analyze all code paths that touch or are touched by your changes:

```
BLAST RADIUS:
- Direct impact: [what will change in the target functionality]
- Indirect impact: [other systems/components that call or depend on this code]
- Confirmed safe: [areas verified to have no impact]
```

**Rules:**
- Search for all callers of modified functions
- Check model attribute access across all call sites
- Verify that new parameters/methods work correctly with all existing data types

## Step 3: Risk Level Classification

```
RISK LEVEL:
- High risk: [anything that could break critical user-facing features]
- Medium risk: [edge cases or non-critical paths that might behave differently]
- Low risk: [minor changes with comprehensive test coverage]
```

## Step 4: Safeguards

```
SAFEGUARDS:
- Test coverage: [which existing tests cover this? which new tests needed?]
- Rollback plan: [specific git commands, estimated time]
- Monitoring plan: [what to watch in logs, for how long]
```

**Test requirements for new code:**
- Every new method/function MUST have companion unit tests
- Model attribute access MUST be validated via model attribute tests
- Log-only parameters MUST use `getattr(obj, 'attr', 'unknown')` for defensive access

## Step 5: Deployment Recommendation

Select one:
```
DEPLOYMENT RECOMMENDATION:
[ ] Deploy immediately — low risk, comprehensive testing
[ ] Deploy with active monitoring — medium risk, watch specific patterns
[ ] Deploy to staging first — high risk, needs validation
[ ] Needs discussion — unclear impacts, requires review
```

## Step 6: Cross-Functional Validation Checklist

Before deploying, confirm ALL:

- [ ] Direct functionality works (the thing being fixed)
- [ ] Adjacent functionality unchanged (things that call or are called by the changes)
- [ ] Data integrity preserved (no corruption of existing or new data)
- [ ] Performance impact acceptable (no significant slowdowns)
- [ ] Error handling intact (exceptions and edge cases handled)
- [ ] All new methods have companion tests
- [ ] Full test suite passes

## Zero-Impact Principle

If your change touches code beyond the immediate fix:

1. **STOP** before implementing
2. Document all potential impacts
3. Propose alternative approaches that minimize blast radius
4. Request review before proceeding

## Incident Reference

This protocol was established after the 2026-02-16 vetting outage (commit `60d3fe7`), where a logging-only parameter (`candidate_id`) referenced a non-existent model attribute (`CandidateJobMatch.bullhorn_candidate_id`), crashing the entire note creation flow for 49 minutes.
