"""Requirements agent: architectural drivers and questions raised by the requirements themselves."""

from __future__ import annotations

from archagent.models import CLASSIFICATION_RANK, Driver, Requirements

DIMENSIONS: tuple[str, ...] = (
    "simplicity",
    "scalability",
    "availability",
    "cost",
    "compliance",
    "latency",
    "events",
)


def derive_drivers(req: Requirements) -> list[Driver]:
    """Weight each design dimension (1 low, 3 high) from the requirements, with the reason."""
    w, n, d, c = req.workload, req.nfr, req.data, req.constraints
    caps = set(req.capabilities)
    drivers: list[Driver] = []

    team = 3 if c.team_size <= 8 else 2 if c.team_size <= 20 else 1
    drivers.append(
        Driver(
            name="simplicity", weight=team, reason=f"team of {c.team_size} has to build and run it"
        )
    )

    if w.peak_rps >= 1000 or w.growth_per_year >= 2:
        scale, why = 3, f"peak {w.peak_rps:g} rps with {w.growth_per_year:.0%} yearly growth"
    elif w.peak_rps >= 200:
        scale, why = 2, f"peak {w.peak_rps:g} rps"
    else:
        scale, why = 1, f"peak {w.peak_rps:g} rps is modest"
    drivers.append(Driver(name="scalability", weight=scale, reason=why))

    avail = 3 if n.availability_pct >= 99.95 else 2 if n.availability_pct >= 99.9 else 1
    drivers.append(
        Driver(
            name="availability", weight=avail, reason=f"target {n.availability_pct}% availability"
        )
    )

    budget = c.monthly_budget_usd
    cost = (
        3
        if budget is not None and budget < 1500
        else 2
        if budget is not None and budget < 5000
        else 1
    )
    drivers.append(
        Driver(
            name="cost",
            weight=cost,
            reason=f"budget ${budget:,.0f} a month" if budget is not None else "no budget stated",
        )
    )

    strict = {"hipaa", "pci_dss"} & set(d.regulations)
    if strict or d.classification == "restricted":
        comp, why = 3, "strict regulation or restricted data"
    elif d.regulations or d.pii or CLASSIFICATION_RANK[d.classification] >= 2:
        comp, why = 2, "regulated, personal or confidential data"
    else:
        comp, why = 1, "no special data obligations"
    drivers.append(Driver(name="compliance", weight=comp, reason=why))

    lat = 3 if n.p95_latency_ms <= 100 else 2 if n.p95_latency_ms <= 250 else 1
    drivers.append(Driver(name="latency", weight=lat, reason=f"p95 target {n.p95_latency_ms} ms"))

    if "realtime" in caps:
        ev, why = 3, "real-time delivery is required"
    elif "async_jobs" in caps:
        ev, why = 2, "background processing is required"
    else:
        ev, why = 1, "mostly request and response"
    drivers.append(Driver(name="events", weight=ev, reason=why))
    return drivers


def open_questions(req: Requirements) -> list[str]:
    """Inconsistencies and gaps in the requirements that a human should resolve."""
    w, n, d, c = req.workload, req.nfr, req.data, req.constraints
    caps = set(req.capabilities)
    questions: list[str] = []
    if n.availability_pct >= 99.99 and n.regions == 1:
        questions.append(
            f"{n.availability_pct}% availability is hard to reach in one region. Should the design span regions?"
        )
    if n.rto_minutes < 15 and n.regions == 1:
        questions.append(
            f"An RTO of {n.rto_minutes} minutes leaves little time to recover from a regional outage. Is a second region needed?"
        )
    if n.rpo_minutes == 0:
        questions.append(
            "A zero RPO needs synchronous replication, which adds write latency and limits distance between sites. Is that intended?"
        )
    if "payments" in caps and "pci_dss" not in d.regulations:
        questions.append(
            "Payments are in scope but PCI DSS is not listed. Will a hosted payment page keep card data out of scope?"
        )
    if "payments" in caps and CLASSIFICATION_RANK[d.classification] < 2:
        questions.append(
            "Payments usually imply at least confidential data. Should the classification be raised?"
        )
    if d.pii and d.classification in ("public", "internal"):
        questions.append(
            "Personal data is present but the classification is low. Should it be confidential?"
        )
    if "hipaa" in d.regulations and d.classification != "restricted":
        questions.append("HIPAA data is usually restricted. Should the classification be raised?")
    if d.residency and n.regions > 1:
        questions.append(
            f"Data must stay in {', '.join(d.residency)} but the design uses {n.regions} regions. Which regions are allowed?"
        )
    if "ml_inference" in caps and n.p95_latency_ms < 100:
        questions.append(
            "Model inference rarely fits in under 100 ms at p95. Is the latency target for the whole request?"
        )
    if w.peak_rps > w.users:
        questions.append(
            "Peak requests per second exceed the user count. Are the units right, or is most traffic from machines?"
        )
    if c.team_size == 1 and n.availability_pct >= 99.95:
        questions.append(
            "A one-person team cannot cover on-call for this availability target. What is the support plan?"
        )
    if "third_party_integrations" in caps:
        questions.append(
            "Which third-party endpoints will the system call, so egress can be allow-listed?"
        )
    if c.monthly_budget_usd is None:
        questions.append(
            "No monthly budget is set, so the cost check has nothing to compare against."
        )
    return questions
