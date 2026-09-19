"""Application service: orchestrates the agents into a design or a review."""

from __future__ import annotations

from archagent.agents.adrs import build_adrs
from archagent.agents.components import ComponentAgent
from archagent.agents.cost import CostAgent
from archagent.agents.patterns import rank_styles
from archagent.agents.reliability import ReliabilityAgent
from archagent.agents.requirements import derive_drivers, open_questions
from archagent.agents.review import ReviewAgent
from archagent.agents.security import SecurityAgent
from archagent.agents.summary import SummaryWriter, facts_for
from archagent.catalog import Catalog
from archagent.errors import SpecError
from archagent.models import (
    CostReport,
    Design,
    DesignReport,
    Requirements,
    ReviewResult,
    StyleScore,
)

MAX_CANDIDATES = 3


class DesignService:
    """Facade used by the CLI and library callers."""

    def __init__(self, catalog: Catalog, summary_writer: SummaryWriter) -> None:
        self._catalog = catalog
        self._components = ComponentAgent(catalog)
        self._reliability = ReliabilityAgent()
        self._cost = CostAgent(catalog)
        self._security = SecurityAgent()
        self._review = ReviewAgent(catalog)
        self._summary = summary_writer

    def design(self, req: Requirements) -> DesignReport:
        """Derive drivers, pick a style, build and cost the design, review it and document decisions.

        The top-scoring styles are each built and priced. When a budget is set, the best-scoring style
        that fits it wins, and cheaper choices made over a higher-scoring style are recorded.

        Raises:
            SpecError: If every style is excluded by the requirements.
        """
        drivers = derive_drivers(req)
        styles = rank_styles(req, drivers)
        viable = [s for s in styles if not s.disqualified][:MAX_CANDIDATES]
        if not viable:
            raise SpecError(
                "no architecture style is viable for these requirements; review constraints.avoid"
            )

        candidates: list[tuple[StyleScore, Design, CostReport]] = []
        for score in viable:
            design = self._components.build(req, score.style)
            candidates.append((score, design, self._cost.estimate(design, req)))

        chosen = candidates[0]
        considered: list[str] = []
        if req.constraints.monthly_budget_usd is not None:
            fitting = [c for c in candidates if c[2].within_budget]
            if fitting:
                chosen = fitting[0]
                for score, _, cost in candidates:
                    if score is chosen[0]:
                        break
                    considered.append(
                        f"{score.style.replace('_', ' ')} scored {score.score} but is estimated at "
                        f"${cost.total_monthly_usd:,.0f} a month, over the ${req.constraints.monthly_budget_usd:,.0f} budget."
                    )
            else:
                considered.append(
                    "No candidate style fits the budget. The highest-scoring style is shown with its overrun."
                )

        _, design, cost = chosen
        availability = self._reliability.analyze(design, req.nfr.availability_pct)
        findings = self._review.review(design, availability, req, cost)
        threats = self._security.analyze(design, req)
        adrs = build_adrs(req, drivers, styles, considered, design, availability, threats, cost)
        summary = self._summary.write(facts_for(design, availability, cost, findings, threats))
        return DesignReport(
            requirements=req,
            drivers=drivers,
            styles=styles,
            considered=considered,
            design=design,
            availability=availability,
            threats=threats,
            cost=cost,
            findings=findings,
            adrs=adrs,
            open_questions=open_questions(req),
            summary=summary,
        )

    def review(
        self, design: Design, req: Requirements | None = None, *, target_pct: float | None = None
    ) -> ReviewResult:
        """Review an existing design. Requirements unlock the rules that compare against needs and budget."""
        target = (
            req.nfr.availability_pct if req else (target_pct if target_pct is not None else 99.9)
        )
        availability = self._reliability.analyze(design, target)
        cost = self._cost.estimate(design, req) if req else None
        findings = self._review.review(design, availability, req, cost)
        return ReviewResult(
            design=design,
            availability=availability,
            threats=self._security.analyze(design, req),
            cost=cost,
            findings=findings,
        )

    @property
    def catalog(self) -> Catalog:
        return self._catalog
