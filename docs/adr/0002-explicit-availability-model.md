# ADR 0002: Model availability as a series product with capped redundancy

- Status: Accepted
- Date: 2026-09-19

## Context

Teams pick an availability target and then discover that a chain of dependent services cannot meet it.
A model that only says "use multiple zones" hides the arithmetic that shows why.

## Decision

Each component has a single-instance availability. Replicas add redundancy only across zones. Redundancy is
capped at 99.995% for a tier, because failover is imperfect and failures correlate. Managed services use
their published figure. The stack figure is the product over critical components, and a standby region adds
`A + (1 - A) * A * 0.9`. The report prints every line of the calculation and the assumptions.

## Consequences

- Designs can be compared on a number that is explained, and the weakest tier is named.
- The figure is an estimate under stated assumptions. It ignores maintenance and human error, and the
  README says so.
- Serial chains of managed services can fail a 99.9% target on their own, which is a useful finding.
