"""Rendering: Markdown design document, Mermaid diagram, ADR files, JSON and YAML."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from archagent.errors import RenderError
from archagent.models import (
    Adr,
    ComponentKind,
    Design,
    DesignReport,
    ReviewResult,
)
from archagent.security import md_cell, mermaid_label, slugify

FORMATS = ("md", "json", "yaml", "mmd")
_TIER_TITLES = {
    "edge": "Edge",
    "app": "Application",
    "data": "Data",
    "support": "Platform services",
}


def _node(component_id: str) -> str:
    return "n_" + component_id.replace("-", "_")


def render_mermaid(design: Design) -> str:
    """A flowchart of the design, grouped by tier. Dotted arrows mark unencrypted links."""
    lines = ["flowchart LR"]
    for tier, title in _TIER_TITLES.items():
        members = [c for c in design.components if c.tier == tier]
        if not members:
            continue
        lines.append(f'    subgraph {tier}["{title}"]')
        for c in members:
            label = mermaid_label(c.name)
            if c.product and c.product != c.name:
                label += f"<br/>{mermaid_label(c.product)}"
            if c.replicas > 1:
                label += f"<br/>x{c.replicas} in {c.zones} zone{'s' if c.zones > 1 else ''}"
            shape = f'[("{label}")]' if c.kind in _DATA_SHAPES else f'["{label}"]'
            lines.append(f"        {_node(c.id)}{shape}")
        lines.append("    end")
    for edge in design.connections:
        arrow = "-->" if edge.encrypted else "-.->"
        lines.append(
            f"    {_node(edge.source)} {arrow}|{mermaid_label(edge.protocol)}| {_node(edge.target)}"
        )
    public = [_node(c.id) for c in design.components if c.public]
    if public:
        lines.append("    classDef public stroke:#c0392b,stroke-width:2px")
        lines.append(f"    class {','.join(public)} public")
    return "\n".join(lines)


_DATA_SHAPES = {
    ComponentKind.DATABASE,
    ComponentKind.CACHE,
    ComponentKind.OBJECT_STORAGE,
    ComponentKind.SEARCH,
    ComponentKind.DATA_WAREHOUSE,
    ComponentKind.QUEUE,
}


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out.extend("| " + " | ".join(md_cell(cell) for cell in row) + " |" for row in rows)
    return out


def render_adr(adr: Adr) -> str:
    """One ADR in the common Status, Context, Decision, Consequences layout."""
    lines = [
        f"# ADR {adr.number:04d}: {md_cell(adr.title)}",
        "",
        f"- Status: {adr.status}",
        "",
        "## Context",
        "",
        adr.context,
        "",
        "## Decision",
        "",
        adr.decision,
        "",
        "## Consequences",
        "",
        *[f"- {c}" for c in adr.consequences],
    ]
    if adr.alternatives:
        lines += ["", "## Alternatives considered", "", *[f"- {a}" for a in adr.alternatives]]
    return "\n".join(lines) + "\n"


def _review_sections(result_findings: list[Any], threats: list[Any]) -> list[str]:
    out = ["## Review findings", ""]
    if result_findings:
        out += _table(
            ["Rule", "Severity", "Finding", "Detail", "Recommendation"],
            [
                [f.rule, f.severity.value, f.title, f.detail, f.recommendation]
                for f in result_findings
            ],
        )
    else:
        out.append("No findings.")
    out += ["", "## Threat model (STRIDE)", ""]
    out += _table(
        ["Id", "Component", "Category", "Threat", "Controls present", "Status", "Recommendation"],
        [
            [
                t.id,
                t.component,
                t.category,
                t.description,
                ", ".join(t.present_controls) or "none",
                t.status,
                t.recommendation,
            ]
            for t in threats
        ],
    )
    return out


def render_markdown(report: DesignReport) -> str:
    """The full design document."""
    req, design, avail, cost = report.requirements, report.design, report.availability, report.cost
    out = [f"# Architecture design: {md_cell(req.name)}", ""]
    if report.summary:
        out += ["## Summary", "", report.summary, ""]
    if req.summary:
        out += [f"> {md_cell(req.summary)}", ""]

    out += ["## Drivers", ""]
    out += _table(
        ["Driver", "Weight", "Why"], [[d.name, d.weight, d.reason] for d in report.drivers]
    )
    out += ["", "## Style selection", ""]
    out += _table(
        ["Style", "Score", "Notes"],
        [
            [
                s.style.replace("_", " "),
                "not viable" if s.disqualified else s.score,
                s.disqualified or "; ".join(s.strengths + s.weaknesses) or "no strong signal",
            ]
            for s in report.styles
        ],
    )
    out += ["", f"Chosen: **{design.style.replace('_', ' ')}**.", ""]
    out += [f"- {md_cell(note)}" for note in report.considered]
    if report.considered:
        out.append("")

    out += ["## Architecture", "", "```mermaid", render_mermaid(design), "```", ""]
    out += _table(
        ["Id", "Kind", "Product", "Replicas", "Zones", "Public", "Notes"],
        [
            [
                c.id,
                c.kind.value,
                c.product,
                c.replicas,
                c.zones,
                "yes" if c.public else "no",
                c.notes,
            ]
            for c in design.components
        ],
    )

    out += ["", "## Availability", ""]
    out.append(
        f"Target {avail.target_pct}%. Estimated {avail.achieved_pct}% "
        f"({avail.downtime_minutes_per_month:g} minutes of downtime a month). "
        f"{'The target is met.' if avail.meets_target else 'The target is not met.'}"
    )
    out += [""]
    out += _table(
        ["Component", "Kind", "Single", "Replicas", "Zones", "Effective"],
        [
            [
                line.component,
                line.kind,
                f"{line.single:.5f}",
                line.replicas,
                line.zones,
                f"{line.effective:.6f}",
            ]
            for line in avail.lines
        ],
    )
    if avail.single_points_of_failure:
        out += ["", f"Single points of failure: {', '.join(avail.single_points_of_failure)}."]
    out += ["", "Assumptions:", "", *[f"- {a}" for a in avail.assumptions], ""]

    out += ["## Cost", ""]
    out += _table(
        ["Item", "Monthly (USD)", "Basis"],
        [[line.item, f"{line.monthly_usd:,.2f}", line.basis] for line in cost.lines],
    )
    out += ["", f"Total: **${cost.total_monthly_usd:,.2f} a month**."]
    if cost.budget_usd is not None:
        out[-1] += (
            f" Budget ${cost.budget_usd:,.0f}, {'within budget' if cost.within_budget else 'over budget'}."
        )
    out += ["", *[f"- {md_cell(lever)}" for lever in cost.levers], "", f"_{cost.disclaimer}_", ""]

    out += _review_sections(report.findings, report.threats)
    out += ["", "## Decisions", ""]
    out += [f"- ADR {a.number:04d}: {md_cell(a.title)} ({a.status})" for a in report.adrs]
    out += ["", "## Open questions", ""]
    out += [f"- {md_cell(q)}" for q in report.open_questions] or ["None."]
    return "\n".join(out) + "\n"


def render_review_markdown(result: ReviewResult) -> str:
    """A review of an existing design."""
    design, avail = result.design, result.availability
    out = [
        f"# Design review: {md_cell(design.name)}",
        "",
        "```mermaid",
        render_mermaid(design),
        "```",
        "",
    ]
    out.append(
        f"Estimated availability {avail.achieved_pct}% against {avail.target_pct}% "
        f"({avail.downtime_minutes_per_month:g} minutes of downtime a month)."
    )
    if result.cost:
        out.append(f"Estimated cost ${result.cost.total_monthly_usd:,.2f} a month.")
    out.append("")
    out += _review_sections(result.findings, result.threats)
    return "\n".join(out) + "\n"


def render_json(model: DesignReport | ReviewResult) -> str:
    return model.model_dump_json(indent=2)


def render_design_yaml(design: Design) -> str:
    """The design as YAML, suitable for hand editing and ``archagent review``."""
    return yaml.safe_dump(json.loads(design.model_dump_json()), sort_keys=False)


def write_outputs(report: DesignReport, out_dir: Path, formats: list[str]) -> list[Path]:
    """Write the requested formats, plus ADR files, into ``out_dir``. Returns the paths."""
    unknown = [f for f in formats if f not in FORMATS]
    if unknown:
        raise RenderError(f"unknown format {unknown[0]!r}; choose from {', '.join(FORMATS)}")
    slug = slugify(report.requirements.name)
    content = {
        "md": (f"{slug}.design.md", render_markdown(report)),
        "json": (f"{slug}.design.json", render_json(report)),
        "yaml": (f"{slug}.design.yaml", render_design_yaml(report.design)),
        "mmd": (f"{slug}.mmd", render_mermaid(report.design) + "\n"),
    }
    paths: list[Path] = []
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        for fmt in formats:
            name, text = content[fmt]
            path = out_dir / name
            path.write_text(text, encoding="utf-8")
            paths.append(path)
        if "md" in formats:
            adr_dir = out_dir / "adr"
            adr_dir.mkdir(exist_ok=True)
            for adr in report.adrs:
                path = adr_dir / f"{adr.number:04d}-{slugify(adr.title)[:60]}.md"
                path.write_text(render_adr(adr), encoding="utf-8")
                paths.append(path)
    except OSError as exc:
        raise RenderError(f"cannot write to {out_dir}: {exc}") from exc
    return paths
