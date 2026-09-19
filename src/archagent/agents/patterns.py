"""Pattern agent: ranks architecture styles against the drivers with a transparent weighted matrix."""

from __future__ import annotations

from archagent.agents.requirements import DIMENSIONS
from archagent.models import Driver, Requirements, StyleName, StyleScore

# How well each style serves each dimension, 1 (poor) to 5 (excellent). Editable judgment, not fact.
STYLE_MATRIX: dict[StyleName, dict[str, int]] = {
    "modular_monolith": {
        "simplicity": 5,
        "scalability": 2,
        "availability": 3,
        "cost": 5,
        "compliance": 3,
        "latency": 5,
        "events": 2,
    },
    "layered_managed": {
        "simplicity": 4,
        "scalability": 3,
        "availability": 4,
        "cost": 4,
        "compliance": 4,
        "latency": 4,
        "events": 3,
    },
    "microservices": {
        "simplicity": 1,
        "scalability": 5,
        "availability": 4,
        "cost": 2,
        "compliance": 4,
        "latency": 3,
        "events": 4,
    },
    "event_driven": {
        "simplicity": 2,
        "scalability": 4,
        "availability": 4,
        "cost": 3,
        "compliance": 3,
        "latency": 3,
        "events": 5,
    },
    "serverless": {
        "simplicity": 4,
        "scalability": 4,
        "availability": 4,
        "cost": 4,
        "compliance": 3,
        "latency": 2,
        "events": 4,
    },
}

STYLE_ORDER: tuple[StyleName, ...] = (
    "layered_managed",
    "modular_monolith",
    "serverless",
    "event_driven",
    "microservices",
)

_LABEL = {
    "simplicity": "operational simplicity",
    "scalability": "scalability",
    "availability": "availability",
    "cost": "cost",
    "compliance": "compliance isolation",
    "latency": "latency",
    "events": "event handling",
}


def _avoided(style: StyleName, avoid: list[str]) -> bool:
    names = {style, style.replace("_", " ")}
    if style == "serverless":
        names |= {"lambda", "functions"}
    if style == "microservices":
        names |= {"kubernetes", "service mesh"}
    return any(item.strip().lower() in names for item in avoid)


def disqualification(style: StyleName, req: Requirements) -> str:
    """Reason a style cannot be recommended for these requirements, or an empty string."""
    if _avoided(style, req.constraints.avoid):
        return "listed in constraints.avoid"
    if style == "microservices" and req.constraints.team_size < 5:
        return f"a team of {req.constraints.team_size} cannot operate several independently deployed services"
    if style == "serverless" and req.nfr.p95_latency_ms <= 50:
        return "cold starts conflict with a p95 latency target of 50 ms or less"
    return ""


def rank_styles(req: Requirements, drivers: list[Driver]) -> list[StyleScore]:
    """Score every style 0 to 100, best first. Disqualified styles sort last with score 0."""
    weights = {d.name: d.weight for d in drivers}
    total = sum(weights.get(dim, 1) for dim in DIMENSIONS)
    scored: list[StyleScore] = []
    for style in STYLE_ORDER:
        row = STYLE_MATRIX[style]
        points = sum(weights.get(dim, 1) * row[dim] for dim in DIMENSIONS)
        score = round(100 * points / (5 * total), 1)
        why_not = disqualification(style, req)
        strengths = [
            f"strong on {_LABEL[dim]}"
            for dim in DIMENSIONS
            if weights.get(dim, 1) >= 2 and row[dim] >= 4
        ]
        weaknesses = [
            f"weak on {_LABEL[dim]}"
            for dim in DIMENSIONS
            if weights.get(dim, 1) >= 2 and row[dim] <= 2
        ]
        scored.append(
            StyleScore(
                style=style,
                score=0.0 if why_not else score,
                disqualified=why_not,
                strengths=strengths,
                weaknesses=weaknesses,
            )
        )
    order = {s: i for i, s in enumerate(STYLE_ORDER)}
    return sorted(scored, key=lambda s: (bool(s.disqualified), -s.score, order[s.style]))
