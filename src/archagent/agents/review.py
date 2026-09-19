"""Review agent: architecture rules applied to any design, generated or hand-written.

Each rule has a stable identifier so findings can be tracked and suppressed by reference. Rules that
need requirements are skipped when only a design is supplied.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from archagent.catalog import Catalog
from archagent.models import (
    CLASSIFICATION_RANK,
    DATA_KINDS,
    AvailabilityReport,
    ComponentKind,
    CostReport,
    Design,
    Finding,
    Requirements,
    Severity,
)

TAIL_MULTIPLIER = 2.0
_APP_ENTRY = {
    ComponentKind.COMPUTE,
    ComponentKind.SERVERLESS,
    ComponentKind.API_GATEWAY,
    ComponentKind.LOAD_BALANCER,
}
_STORAGE = {
    ComponentKind.DATABASE,
    ComponentKind.OBJECT_STORAGE,
    ComponentKind.SEARCH,
    ComponentKind.DATA_WAREHOUSE,
}


@dataclass(frozen=True)
class Context:
    design: Design
    req: Requirements | None
    availability: AvailabilityReport
    cost: CostReport | None
    catalog: Catalog

    @property
    def classification(self) -> str:
        return self.req.data.classification if self.req else "internal"

    @property
    def target(self) -> float:
        return self.availability.target_pct


Rule = Callable[[Context], list[Finding]]


def _finding(
    rule: str, severity: Severity, title: str, detail: str, fix: str, ids: list[str] | None = None
) -> Finding:
    return Finding(
        rule=rule,
        severity=severity,
        title=title,
        detail=detail,
        recommendation=fix,
        components=ids or [],
    )


def _spof(ctx: Context) -> list[Finding]:
    ids = ctx.availability.single_points_of_failure
    if not ids:
        return []
    severity = Severity.HIGH if ctx.target >= 99.9 else Severity.LOW
    return [
        _finding(
            "ARCH-001",
            severity,
            "Single point of failure",
            f"{', '.join(ids)} sit on the critical path with one replica or one zone, so one failure takes the system down.",
            "Run at least two replicas in different zones behind a health-checked load balancer.",
            ids,
        )
    ]


def _availability_gap(ctx: Context) -> list[Finding]:
    a = ctx.availability
    if a.meets_target:
        return []
    gap = a.target_pct - a.achieved_pct
    weakest = next((c for c in ctx.design.components if c.id == a.weakest), None)
    if weakest is not None and weakest.managed_redundancy:
        fix = (
            f"{a.weakest} is a managed service whose published figure caps the whole design. "
            "Choose an offering with a higher service level, take it off the critical path, or add a standby region."
        )
    else:
        fix = "Add zone redundancy to the weakest tier, remove serial dependencies, or add a standby region."
    return [
        _finding(
            "ARCH-002",
            Severity.CRITICAL if gap > 0.5 else Severity.HIGH,
            "Availability target not met",
            f"The design reaches about {a.achieved_pct}% against a target of {a.target_pct}% "
            f"(about {a.downtime_minutes_per_month:g} minutes of downtime a month). The weakest tier is {a.weakest}.",
            fix,
            [a.weakest] if a.weakest else [],
        )
    ]


def _public_data(ctx: Context) -> list[Finding]:
    ids = [c.id for c in ctx.design.components if c.kind in DATA_KINDS and c.public]
    if not ids:
        return []
    return [
        _finding(
            "ARCH-003",
            Severity.CRITICAL,
            "Data store reachable from the internet",
            f"{', '.join(ids)} accept connections from public networks.",
            "Place data stores in private subnets and reach them only from the application tier.",
            ids,
        )
    ]


def _authentication(ctx: Context) -> list[Finding]:
    public = [c.id for c in ctx.design.components if c.public and c.kind in _APP_ENTRY]
    has_authn = any(c.authn for c in ctx.design.components) or ctx.design.of_kind(
        ComponentKind.IDENTITY
    )
    if not public or has_authn or CLASSIFICATION_RANK[ctx.classification] < 1:
        return []
    return [
        _finding(
            "ARCH-004",
            Severity.HIGH,
            "Public entry without authentication",
            f"{', '.join(public)} are internet-facing but nothing authenticates callers.",
            "Add an identity provider and validate tokens at the gateway and in the service.",
            public,
        )
    ]


def _identity_missing(ctx: Context) -> list[Finding]:
    if (
        ctx.req is None
        or "auth" not in ctx.req.capabilities
        or ctx.design.of_kind(ComponentKind.IDENTITY)
    ):
        return []
    return [
        _finding(
            "ARCH-005",
            Severity.HIGH,
            "Authentication is required but no identity provider exists",
            "The requirements include sign-in but the design has no identity component.",
            "Add a managed identity provider rather than building credential storage.",
        )
    ]


def _plaintext(ctx: Context) -> list[Finding]:
    edges = [e for e in ctx.design.connections if not e.encrypted]
    if not edges:
        return []
    worst = max(CLASSIFICATION_RANK[e.classification] for e in edges)
    severity = Severity.HIGH if worst >= 2 else Severity.MEDIUM
    names = [f"{e.source}->{e.target}" for e in edges]
    return [
        _finding(
            "ARCH-006",
            severity,
            "Unencrypted connections",
            f"These links carry traffic without TLS: {', '.join(names)}.",
            "Enable TLS on every hop, including inside the private network.",
            sorted({e.source for e in edges}),
        )
    ]


def _encryption_at_rest(ctx: Context) -> list[Finding]:
    ids = [
        c.id
        for c in ctx.design.components
        if c.kind in _STORAGE and c.encrypted_at_rest is not True
    ]
    if not ids:
        return []
    sensitive = (ctx.req is not None and ctx.req.data.pii) or CLASSIFICATION_RANK[
        ctx.classification
    ] >= 2
    return [
        _finding(
            "ARCH-007",
            Severity.HIGH if sensitive else Severity.MEDIUM,
            "Storage not encrypted at rest",
            f"{', '.join(ids)} are not marked as encrypted at rest.",
            "Turn on encryption at rest with managed keys, or customer-managed keys for regulated data.",
            ids,
        )
    ]


def _recovery_point(ctx: Context) -> list[Finding]:
    if ctx.req is None:
        return []
    rpo = ctx.req.nfr.rpo_minutes
    weak = []
    for db in ctx.design.of_kind(ComponentKind.DATABASE):
        covered = (
            db.replication == "sync"
            or (db.replication == "async" and rpo >= 5)
            or (db.backup_interval_minutes is not None and db.backup_interval_minutes <= rpo)
        )
        if not covered:
            weak.append(db.id)
    if not weak:
        return []
    return [
        _finding(
            "ARCH-008",
            Severity.HIGH,
            "Backups do not meet the recovery point objective",
            f"{', '.join(weak)} cannot restore to within {rpo} minutes of a failure.",
            "Shorten the backup interval, enable point-in-time recovery, or replicate to a standby.",
            weak,
        )
    ]


def _recovery_time(ctx: Context) -> list[Finding]:
    if ctx.req is None:
        return []
    out: list[Finding] = []
    if ctx.req.nfr.rto_minutes < 15 and ctx.design.regions == 1:
        out.append(
            _finding(
                "ARCH-009",
                Severity.MEDIUM,
                "Recovery time is short for a single region",
                f"An RTO of {ctx.req.nfr.rto_minutes} minutes leaves no time to rebuild after a regional outage.",
                "Run a warm standby in a second region and rehearse the failover.",
            )
        )
    if ctx.design.regions < ctx.req.nfr.regions:
        out.append(
            _finding(
                "ARCH-010",
                Severity.HIGH,
                "Fewer regions than required",
                f"The requirements ask for {ctx.req.nfr.regions} regions and the design uses {ctx.design.regions}.",
                "Add the missing regions, or relax the requirement and accept the availability consequence.",
            )
        )
    return out


def _observability(ctx: Context) -> list[Finding]:
    if ctx.design.of_kind(ComponentKind.OBSERVABILITY):
        return []
    return [
        _finding(
            "ARCH-011",
            Severity.MEDIUM,
            "No observability",
            "The design has no metrics, logs or traces component, so failures and attacks will go unseen.",
            "Add centralized logging, metrics with alerts, and tracing on the request path.",
        )
    ]


def _waf(ctx: Context) -> list[Finding]:
    public = [c.id for c in ctx.design.components if c.public and c.kind in _APP_ENTRY]
    if not public or ctx.design.of_kind(ComponentKind.WAF):
        return []
    payments = ctx.req is not None and "payments" in ctx.req.capabilities
    high = payments or CLASSIFICATION_RANK[ctx.classification] >= 2
    return [
        _finding(
            "ARCH-012",
            Severity.HIGH if high else Severity.MEDIUM,
            "Public entry without a web application firewall",
            f"{', '.join(public)} are internet-facing with no WAF or equivalent filtering.",
            "Put a managed WAF with rate limiting in front of every public endpoint.",
            public,
        )
    ]


def _budget(ctx: Context) -> list[Finding]:
    cost = ctx.cost
    if cost is None or cost.budget_usd is None or cost.within_budget is not False:
        return []
    over = (cost.total_monthly_usd - cost.budget_usd) / cost.budget_usd
    return [
        _finding(
            "ARCH-013",
            Severity.HIGH if over > 0.25 else Severity.MEDIUM,
            "Estimate exceeds the budget",
            f"The estimate is ${cost.total_monthly_usd:,.0f} a month against a budget of ${cost.budget_usd:,.0f} ({over:.0%} over).",
            "Apply the cost levers in the report, or reduce the availability tier or scope.",
        )
    ]


def _team_fit(ctx: Context) -> list[Finding]:
    if ctx.req is None or ctx.design.style != "microservices" or ctx.req.constraints.team_size >= 8:
        return []
    count = len(ctx.design.of_kind(ComponentKind.COMPUTE))
    return [
        _finding(
            "ARCH-014",
            Severity.MEDIUM,
            "Microservices for a small team",
            f"{count} independently deployed services need pipelines, on-call and tracing for a team of {ctx.req.constraints.team_size}.",
            "Start with a modular monolith or a layered design and split services when team boundaries appear.",
        )
    ]


def _compliance(ctx: Context) -> list[Finding]:
    if ctx.req is None or not ctx.req.data.regulations:
        return []
    regs = set(ctx.req.data.regulations)
    out: list[Finding] = []
    if not ctx.design.of_kind(ComponentKind.SECRETS):
        out.append(
            _finding(
                "ARCH-015",
                Severity.MEDIUM,
                "No secrets or key management",
                f"{', '.join(sorted(regs)).upper()} expects controlled key and credential handling.",
                "Add a managed secrets store and key management service.",
            )
        )
    if regs & {"hipaa", "pci_dss", "soc2"} and not any(
        c.audit_logging for c in ctx.design.components
    ):
        out.append(
            _finding(
                "ARCH-016",
                Severity.HIGH,
                "No audit logging for a regulated system",
                "None of the components writes an audit log, which these frameworks require.",
                "Enable audit logging on the application and the data stores and ship it to write-once storage.",
            )
        )
    return out


def _residency(ctx: Context) -> list[Finding]:
    if ctx.req is None or not ctx.req.data.residency or ctx.design.regions < 2:
        return []
    return [
        _finding(
            "ARCH-017",
            Severity.LOW,
            "Multi-region design with a residency constraint",
            f"Data must stay in {', '.join(ctx.req.data.residency)} but the design uses {ctx.design.regions} regions.",
            "Pin every region to an allowed jurisdiction and confirm replication targets.",
        )
    ]


def _dead_letter(ctx: Context) -> list[Finding]:
    ids = [c.id for c in ctx.design.of_kind(ComponentKind.QUEUE) if c.dead_letter is not True]
    if not ids:
        return []
    return [
        _finding(
            "ARCH-018",
            Severity.LOW,
            "Queue without a dead-letter queue",
            f"{', '.join(ids)} has no dead-letter path, so one poison message can stall consumers.",
            "Add a dead-letter queue, a retry limit and an alarm on its depth.",
            ids,
        )
    ]


def _avoid(ctx: Context) -> list[Finding]:
    if ctx.req is None or not ctx.req.constraints.avoid:
        return []
    hits: list[str] = []
    for c in ctx.design.components:
        text = f"{c.name} {c.product} {c.kind.value}".lower()
        if any(
            item.strip().lower() and item.strip().lower() in text
            for item in ctx.req.constraints.avoid
        ):
            hits.append(c.id)
    if not hits:
        return []
    return [
        _finding(
            "ARCH-019",
            Severity.MEDIUM,
            "Design uses technology the requirements exclude",
            f"{', '.join(hits)} match an item in constraints.avoid.",
            "Replace them with an approved alternative.",
            hits,
        )
    ]


def _request_path_latency(design: Design) -> float:
    per_kind: dict[ComponentKind, float] = {}
    for c in design.components:
        if c.on_request_path and c.latency_ms > 0:
            per_kind[c.kind] = max(per_kind.get(c.kind, 0.0), c.latency_ms)
    return sum(per_kind.values())


def _latency(ctx: Context) -> list[Finding]:
    if ctx.req is None:
        return []
    estimate = _request_path_latency(ctx.design) * TAIL_MULTIPLIER
    if estimate <= ctx.req.nfr.p95_latency_ms:
        return []
    return [
        _finding(
            "ARCH-020",
            Severity.MEDIUM,
            "Latency budget at risk",
            f"Typical hop latencies add up to about {estimate:.0f} ms at p95 (with a {TAIL_MULTIPLIER:g}x tail factor) "
            f"against a target of {ctx.req.nfr.p95_latency_ms} ms.",
            "Cache hot reads, remove serial hops, colocate the tiers, or relax the target.",
        )
    ]


def _capacity(ctx: Context) -> list[Finding]:
    if ctx.req is None:
        return []
    w = ctx.req.workload
    out: list[Finding] = []
    compute = [
        c for c in ctx.design.components if c.kind is ComponentKind.COMPUTE and c.on_request_path
    ]
    capacity = ctx.catalog[ComponentKind.COMPUTE].capacity_rps
    if compute and capacity:
        total = sum(c.replicas for c in compute) * capacity
        if total < w.peak_rps:
            out.append(
                _finding(
                    "ARCH-021",
                    Severity.HIGH,
                    "Compute cannot serve peak load",
                    f"{sum(c.replicas for c in compute)} replica(s) give about {total:.0f} rps against a peak of {w.peak_rps:g} rps.",
                    "Add replicas or enable autoscaling with headroom above the peak.",
                    [c.id for c in compute],
                )
            )
    db_capacity = ctx.catalog[ComponentKind.DATABASE].capacity_rps
    writes = w.peak_rps * (1 - w.read_ratio)
    if db_capacity and ctx.design.of_kind(ComponentKind.DATABASE) and writes > db_capacity:
        out.append(
            _finding(
                "ARCH-022",
                Severity.HIGH,
                "Write load exceeds a single database primary",
                f"About {writes:.0f} writes per second against roughly {db_capacity:.0f} for one primary.",
                "Partition the data, batch or queue writes, or choose a store built for the write rate.",
            )
        )
    return out


RULES: tuple[Rule, ...] = (
    _spof,
    _availability_gap,
    _public_data,
    _authentication,
    _identity_missing,
    _plaintext,
    _encryption_at_rest,
    _recovery_point,
    _recovery_time,
    _observability,
    _waf,
    _budget,
    _team_fit,
    _compliance,
    _residency,
    _dead_letter,
    _avoid,
    _latency,
    _capacity,
)


class ReviewAgent:
    """Runs every rule and returns findings, most severe first."""

    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog

    def review(
        self,
        design: Design,
        availability: AvailabilityReport,
        req: Requirements | None = None,
        cost: CostReport | None = None,
    ) -> list[Finding]:
        ctx = Context(design, req, availability, cost, self._catalog)
        findings = [f for rule in RULES for f in rule(ctx)]
        return sorted(findings, key=lambda f: (-f.severity.rank, f.rule))
