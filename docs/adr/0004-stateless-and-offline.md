# ADR 0004: Stateless, offline and deterministic

- Status: Accepted
- Date: 2026-09-19

## Context

Requirements can describe unreleased products and sensitive data. The tool should be safe to run on them and
should give the same answer twice so results can be reviewed and version-controlled.

## Decision

No database, no network calls in the default configuration, and no reads of real infrastructure. All agents are
pure functions of their inputs. The optional language model sees only aggregate counts and cannot change a
finding, and its text is rejected if it contains a number that is not in those counts.

## Consequences

- Output can be committed next to the requirements and diffed in review.
- Prices and availability figures are static catalogue data, so users supply their own with a pricing file.
- There is no drift detection against what is actually deployed.
