"""Cost agent: monthly estimate from catalogue prices and the workload."""

from __future__ import annotations

from archagent.agents.components import JOB_SHARE, monthly_requests
from archagent.catalog import Catalog
from archagent.models import (
    Component,
    ComponentKind,
    CostLine,
    CostReport,
    Design,
    Requirements,
)

EGRESS_PER_GB = 0.09
STANDBY_REGION_FACTOR = 0.6
DISCLAIMER = (
    "An illustrative planning estimate from catalogue prices and the stated workload. "
    "It is not a quote. Replace prices with your negotiated rates using a pricing file."
)

_REQUEST_SHARE: dict[ComponentKind, float] = {
    ComponentKind.QUEUE: JOB_SHARE * 3,
    ComponentKind.OBJECT_STORAGE: 0.2,
    ComponentKind.NOTIFICATION: 0.1,
}
_STORAGE_KINDS = {
    ComponentKind.DATABASE,
    ComponentKind.SEARCH,
    ComponentKind.OBJECT_STORAGE,
    ComponentKind.DATA_WAREHOUSE,
}


class CostAgent:
    """Prices each component, adds data transfer, and compares the total with the budget."""

    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog

    def estimate(self, design: Design, req: Requirements) -> CostReport:
        w = req.workload
        requests_m = monthly_requests(w.peak_rps) / 1_000_000
        storage_gb = w.data_gb * (1 + w.growth_per_year / 2)
        region_factor = 1 + STANDBY_REGION_FACTOR * (design.regions - 1)

        lines: list[CostLine] = []
        for component in design.components:
            lines.append(self._price(component, requests_m, storage_gb, region_factor))
        egress_gb = requests_m * 1_000_000 * w.avg_payload_kb / 1_000_000
        lines.append(
            CostLine(
                item="Data transfer out",
                monthly_usd=round(egress_gb * EGRESS_PER_GB, 2),
                basis=f"{egress_gb:,.0f} GB a month at ${EGRESS_PER_GB}/GB",
            )
        )
        total = round(sum(line.monthly_usd for line in lines), 2)
        budget = req.constraints.monthly_budget_usd
        return CostReport(
            lines=lines,
            total_monthly_usd=total,
            budget_usd=budget,
            within_budget=None if budget is None else total <= budget,
            levers=self._levers(design, lines, total, budget),
            disclaimer=DISCLAIMER,
        )

    def _price(
        self, c: Component, requests_m: float, storage_gb: float, region_factor: float
    ) -> CostLine:
        if c.monthly_cost_usd > 0:
            return CostLine(
                item=c.id, monthly_usd=round(c.monthly_cost_usd, 2), basis="stated in the design"
            )
        entry = self._catalog[c.kind]
        parts: list[str] = []
        amount = entry.base_monthly
        if entry.base_monthly:
            parts.append(f"${entry.base_monthly:g} base")
        if entry.per_replica_monthly:
            amount += entry.per_replica_monthly * c.replicas
            parts.append(f"{c.replicas} x ${entry.per_replica_monthly:g}")
        if entry.per_million_requests:
            share = _REQUEST_SHARE.get(c.kind, 1.0)
            millions = requests_m * share
            amount += entry.per_million_requests * millions
            parts.append(f"{millions:,.1f}M requests x ${entry.per_million_requests:g}")
        if entry.per_gb_month and c.kind in _STORAGE_KINDS:
            copies = c.replicas if c.kind in (ComponentKind.DATABASE, ComponentKind.SEARCH) else 1
            amount += entry.per_gb_month * storage_gb * copies
            parts.append(f"{storage_gb:,.0f} GB x {copies} x ${entry.per_gb_month:g}")
        if region_factor > 1:
            amount *= region_factor
            parts.append(f"x {region_factor:g} for standby regions")
        return CostLine(
            item=c.id, monthly_usd=round(amount, 2), basis=", ".join(parts) or "no charge"
        )

    def _levers(
        self, design: Design, lines: list[CostLine], total: float, budget: float | None
    ) -> list[str]:
        levers: list[str] = []
        ranked = sorted(
            (line for line in lines if line.monthly_usd > 0), key=lambda line: -line.monthly_usd
        )
        if ranked and total > 0:
            top = ranked[0]
            levers.append(
                f"{top.item} is {top.monthly_usd / total:.0%} of spend. Right-size it first and price a one-year commitment."
            )
        databases = [c for c in design.components if c.kind is ComponentKind.DATABASE]
        if len(databases) > 1:
            price = {line.item: line.monthly_usd for line in lines}
            saving = sum(price.get(c.id, 0.0) for c in databases[1:])
            levers.append(
                f"Share one database cluster between services to save about ${saving:,.0f} a month, if the isolation allows it."
            )
        if design.regions > 1:
            levers.append(
                "Confirm the second region is needed. It adds about 60% to every component's cost."
            )
        if budget is not None and total > budget:
            levers.append(
                f"The estimate is ${total - budget:,.0f} over budget. Reduce availability tier, replica counts or scope, or raise the budget."
            )
        return levers
