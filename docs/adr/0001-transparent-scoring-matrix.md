# ADR 0001: Choose styles with a transparent weighted matrix

- Status: Accepted
- Date: 2026-09-19

## Context

Architecture advice from a model is easy to produce and hard to audit. Engineers need to see why a style was
recommended, and to disagree with a specific judgment instead of the whole answer.

## Decision

Requirements are turned into weights for seven dimensions (simplicity, scalability, availability, cost,
compliance, latency, events), each with a stated reason. Five styles have fixed 1 to 5 scores per dimension.
The result is a weighted percentage. A few hard rules rule styles out with a written reason, such as
microservices for a team under five. The matrix lives in `patterns.py` as data.

## Consequences

- The recommendation is reproducible and every input to it is visible in the report.
- The scores are judgment. They are documented as such and are easy to edit.
- Ties are broken by a fixed order that prefers the simpler style.
- The tool does not discover styles it does not know about.
