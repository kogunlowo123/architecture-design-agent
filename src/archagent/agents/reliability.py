"""Reliability agent: composite availability from component figures, and single points of failure."""

from __future__ import annotations

from archagent.models import AvailabilityLine, AvailabilityReport, Component, Design

MINUTES_PER_MONTH = 43_200
CORRELATION_CAP = 0.99995
FAILOVER_SUCCESS = 0.9

ASSUMPTIONS = [
    "Components fail independently, and only replicas in different zones add redundancy.",
    f"Redundancy within a tier is capped at {CORRELATION_CAP * 100:.3f}% because failover is imperfect and failures correlate.",
    "Managed services publish an availability figure that already includes their internal redundancy.",
    f"A standby region takes over successfully {FAILOVER_SUCCESS:.0%} of the time when the primary region is down.",
    "Only components marked critical are on the serial availability path. Planned maintenance and human error are not modelled.",
]


def effective_availability(component: Component) -> float:
    """Availability of one tier after zone redundancy."""
    if component.managed_redundancy:
        return component.availability
    redundancy = min(component.replicas, component.zones)
    if redundancy < 2:
        return component.availability
    combined = 1 - (1 - component.availability) ** redundancy
    return max(component.availability, min(combined, CORRELATION_CAP))


def is_single_point_of_failure(component: Component) -> bool:
    """A critical, self-managed component that lives in one zone or has one replica."""
    return (
        component.critical
        and not component.managed_redundancy
        and min(component.replicas, component.zones) < 2
    )


class ReliabilityAgent:
    """Multiplies the availability of every critical component, then credits standby regions."""

    def analyze(self, design: Design, target_pct: float) -> AvailabilityReport:
        lines: list[AvailabilityLine] = []
        stack = 1.0
        for component in design.components:
            if not component.critical:
                continue
            effective = effective_availability(component)
            stack *= effective
            lines.append(
                AvailabilityLine(
                    component=component.id,
                    kind=component.kind.value,
                    single=component.availability,
                    replicas=component.replicas,
                    zones=component.zones,
                    effective=effective,
                )
            )
        achieved = stack
        for _ in range(design.regions - 1):
            achieved = achieved + (1 - achieved) * stack * FAILOVER_SUCCESS
        achieved = min(achieved, 0.999999)
        weakest = min(lines, key=lambda line: line.effective).component if lines else ""
        return AvailabilityReport(
            target_pct=target_pct,
            stack_pct=round(stack * 100, 4),
            achieved_pct=round(achieved * 100, 4),
            downtime_minutes_per_month=round((1 - achieved) * MINUTES_PER_MONTH, 1),
            meets_target=achieved * 100 >= target_pct,
            lines=lines,
            single_points_of_failure=[
                c.id for c in design.components if is_single_point_of_failure(c)
            ],
            weakest=weakest,
            assumptions=ASSUMPTIONS,
        )
