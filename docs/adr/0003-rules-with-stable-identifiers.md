# ADR 0003: Review with numbered, testable rules

- Status: Accepted
- Date: 2026-09-19

## Context

A design review is more useful when its findings can be tracked, discussed and suppressed by reference, and
when the same checks run on generated and hand-written designs.

## Decision

Each check is a small function from a context (design, optional requirements, availability, cost) to
findings, with a stable `ARCH-` identifier, a severity and a concrete recommendation. Rules that need
requirements are skipped when only a design is supplied. The CLI can fail a build at a chosen severity.

## Consequences

- Every rule has tests for firing and for staying quiet, and one test proves each identifier can appear.
- Severity depends on context, for example an unencrypted link is high for restricted data and medium
  otherwise.
- Adding a rule is local. Removing or renumbering one is a visible change.
