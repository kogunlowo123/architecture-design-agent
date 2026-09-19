"""ADR agent: records the main decisions with the numbers that justified them."""

from __future__ import annotations

from archagent.models import (
    Adr,
    AvailabilityReport,
    ComponentKind,
    CostReport,
    Design,
    Driver,
    Requirements,
    StyleScore,
    Threat,
)

_STYLE_TEXT = {
    "modular_monolith": "one deployable application with strict internal module boundaries",
    "layered_managed": "web and API tiers on managed compute, backed by managed data services",
    "microservices": "independently deployed domain services behind an API gateway, each with its own database",
    "event_driven": "a synchronous API plus a queue and background workers for asynchronous work",
    "serverless": "functions behind an API gateway with managed data services",
}


def _title(style: str) -> str:
    return style.replace("_", " ")


def build_adrs(
    req: Requirements,
    drivers: list[Driver],
    styles: list[StyleScore],
    considered: list[str],
    design: Design,
    availability: AvailabilityReport,
    threats: list[Threat],
    cost: CostReport,
) -> list[Adr]:
    """Draft ADRs. Status is Proposed: a human accepts them."""
    adrs = [_style_adr(req, drivers, styles, considered, design), _data_adr(req, design)]
    adrs.append(_availability_adr(req, design, availability))
    adrs.append(_security_adr(req, design, threats))
    if design.of_kind(ComponentKind.QUEUE):
        adrs.append(_async_adr(design))
    for number, adr in enumerate(adrs, start=1):
        adr.number = number
    return adrs


def _style_adr(
    req: Requirements,
    drivers: list[Driver],
    styles: list[StyleScore],
    considered: list[str],
    design: Design,
) -> Adr:
    top = sorted(drivers, key=lambda d: -d.weight)[:4]
    chosen = next(s for s in styles if s.style == design.style)
    alternatives = [
        f"{_title(s.style)}: {'not viable, ' + s.disqualified if s.disqualified else f'scored {s.score}'}"
        for s in styles
        if s.style != design.style
    ]
    return Adr(
        number=0,
        title=f"Use a {_title(design.style)} architecture",
        context="Strongest drivers: " + "; ".join(f"{d.name} ({d.reason})" for d in top) + ".",
        decision=f"Build {req.name} as {_STYLE_TEXT[design.style]}. It scored {chosen.score} out of 100 in the weighted matrix.",
        consequences=[*chosen.strengths, *chosen.weaknesses, *considered]
        or ["No dimension was a clear strength or weakness."],
        alternatives=alternatives,
    )


def _data_adr(req: Requirements, design: Design) -> Adr:
    dbs = design.of_kind(ComponentKind.DATABASE)
    cache = design.of_kind(ComponentKind.CACHE)
    primary = dbs[0] if dbs else None
    decision = "Use a managed relational database"
    if primary:
        decision += f" ({primary.product}) with {primary.replicas} instance(s) across {primary.zones} zone(s) and {primary.replication} replication"
    decision += (
        f", backups every {primary.backup_interval_minutes} minutes."
        if primary and primary.backup_interval_minutes
        else "."
    )
    consequences = [
        f"Recovery point objective of {req.nfr.rpo_minutes} minutes is addressed by replication and backups.",
        "Schema changes need a migration process and backward-compatible releases.",
    ]
    if cache:
        consequences.append(
            "A cache absorbs most reads but adds an invalidation problem the team must design for."
        )
    if any("read replica" in c.notes for c in dbs):
        consequences.append("Read replicas serve reads with some replication lag.")
    return Adr(
        number=0,
        title="Store transactional data in a managed relational database",
        context=f"The system holds {req.workload.data_gb:g} GB growing {req.workload.growth_per_year:.0%} a year with {req.workload.read_ratio:.0%} reads.",
        decision=decision,
        consequences=consequences,
        alternatives=[
            "A document store: flexible schema, weaker joins and constraints.",
            "Self-managed database: more control, more operational burden.",
        ],
    )


def _availability_adr(req: Requirements, design: Design, availability: AvailabilityReport) -> Adr:
    region_text = (
        f"across {design.regions} regions with a standby that takes over on failure"
        if design.regions > 1
        else "in a single region across multiple zones"
    )
    return Adr(
        number=0,
        title="Meet the availability target with zone redundancy"
        + (" and a standby region" if design.regions > 1 else ""),
        context=f"Target {req.nfr.availability_pct}% availability, RTO {req.nfr.rto_minutes} minutes, RPO {req.nfr.rpo_minutes} minutes.",
        decision=f"Deploy {region_text}. The model estimates {availability.achieved_pct}% "
        f"(about {availability.downtime_minutes_per_month:g} minutes of downtime a month); the weakest tier is {availability.weakest or 'not applicable'}.",
        consequences=[
            "The estimate uses published figures and assumptions listed in the design document. Validate it with failure testing.",
            "Failover must be rehearsed. An untested failover is not a control.",
            *(
                ["The target is not met by this design. See the findings."]
                if not availability.meets_target
                else []
            ),
        ],
        alternatives=[
            "A single zone: cheaper, but any zone failure is an outage.",
            "Active-active regions: better recovery time, much higher cost and data-consistency work.",
        ],
    )


def _security_adr(req: Requirements, design: Design, threats: list[Threat]) -> Adr:
    kinds = {c.kind for c in design.components}
    controls = [
        name
        for kind, name in (
            (ComponentKind.IDENTITY, "managed identity provider"),
            (ComponentKind.WAF, "web application firewall"),
            (ComponentKind.SECRETS, "secrets and key management"),
            (ComponentKind.OBSERVABILITY, "centralized logging and monitoring"),
        )
        if kind in kinds
    ]
    open_threats = [t for t in threats if t.status != "mitigated"]
    return Adr(
        number=0,
        title="Adopt a baseline set of security controls",
        context=f"Data is classified {req.data.classification}"
        + (", includes personal data" if req.data.pii else "")
        + (
            f", and falls under {', '.join(r.upper() for r in req.data.regulations)}"
            if req.data.regulations
            else ""
        )
        + ".",
        decision="Include "
        + (", ".join(controls) if controls else "no managed security components")
        + "; encrypt data at rest and in transit; keep data stores private.",
        consequences=[
            f"{len(threats)} STRIDE threats were enumerated and {len(open_threats)} still need work.",
            "Controls in the design are necessary, not sufficient. Configuration and operation decide whether they work.",
        ],
        alternatives=[
            "Rely on network perimeter alone: rejected because one foothold exposes everything behind it."
        ],
    )


def _async_adr(design: Design) -> Adr:
    queue = design.of_kind(ComponentKind.QUEUE)[0]
    return Adr(
        number=0,
        title="Process background work through a queue with a dead-letter path",
        context="Some work is slow or unreliable enough that it should not block a request.",
        decision=f"Publish jobs to {queue.product} and process them with separately scaled workers. Failed messages move to a dead-letter queue after a retry limit.",
        consequences=[
            "Handlers must be idempotent because messages can be delivered more than once.",
            "Users see eventual consistency for results of background work.",
            "Queue depth and dead-letter depth need alarms.",
        ],
        alternatives=[
            "Do the work inside the request: simpler, but slow requests and retries hurt the user."
        ],
    )
