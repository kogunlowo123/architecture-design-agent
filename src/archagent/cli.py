"""Command-line interface ``archagent``.

Exit codes: 0 success, 1 a gate failed (``--fail-on``), 2 invalid input or a runtime error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from archagent.agents.requirements import derive_drivers, open_questions
from archagent.catalog import Catalog
from archagent.config import Settings, load_design, load_requirements
from archagent.container import build_service
from archagent.errors import ArchagentError
from archagent.logging_setup import configure_logging
from archagent.models import Cloud, Finding, Severity
from archagent.render import (
    FORMATS,
    render_json,
    render_markdown,
    render_review_markdown,
    write_outputs,
)
from archagent.security import redact

_SEVERITIES = [s.value for s in Severity if s is not Severity.INFO]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="archagent", description="AI architecture design agent")
    sub = parser.add_subparsers(dest="command", required=True)

    design = sub.add_parser("design", help="turn a requirements file into a reviewed design")
    design.add_argument("spec", type=Path)
    design.add_argument("--out", type=Path, help="write files here instead of printing Markdown")
    design.add_argument(
        "--format", default="md,yaml,mmd", help=f"comma list from: {', '.join(FORMATS)}"
    )
    design.add_argument(
        "--fail-on", choices=_SEVERITIES, help="exit 1 if any finding is at least this severe"
    )

    review = sub.add_parser("review", help="review an existing design file")
    review.add_argument("design", type=Path)
    review.add_argument(
        "--spec", type=Path, help="requirements file, to enable rules that compare against needs"
    )
    review.add_argument(
        "--target", type=float, help="availability target in percent when no spec is given"
    )
    review.add_argument("--format", choices=["md", "json"], default="md")
    review.add_argument("--fail-on", choices=_SEVERITIES)

    validate = sub.add_parser(
        "validate", help="check a requirements file and list its drivers and open questions"
    )
    validate.add_argument("spec", type=Path)

    catalog = sub.add_parser("catalog", help="list the component catalogue")
    catalog.add_argument("--cloud", choices=["any", "aws", "azure", "gcp"], default="any")
    return parser


def _gate(findings: list[Finding], threshold: str | None) -> int:
    if threshold is None:
        return 0
    limit = Severity(threshold).rank
    return 1 if any(f.severity.rank >= limit for f in findings) else 0


def _cmd_catalog(cloud: Cloud) -> int:
    print(f"{'kind':<16} {'tier':<8} {'availability':<13} {'latency':<8} product ({cloud})")
    for entry in Catalog():
        print(
            f"{entry.kind.value:<16} {entry.tier:<8} {entry.availability * 100:<13.3f} {entry.latency_ms:<8g} "
            f"{entry.product(cloud)}"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = _parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    try:
        if args.command == "catalog":
            return _cmd_catalog(args.cloud)
        settings = Settings()
        configure_logging(settings.log_level, json_output=settings.log_json)
        if args.command == "validate":
            req = load_requirements(args.spec)
            print(f"{req.name}: requirements are valid.")
            print("Drivers:")
            for d in sorted(derive_drivers(req), key=lambda d: -d.weight):
                print(f"  {d.weight}  {d.name}: {d.reason}")
            questions = open_questions(req)
            print("Open questions:" if questions else "No open questions.")
            for q in questions:
                print(f"  - {q}")
            return 0
        service = build_service(settings)
        if args.command == "design":
            formats = [f.strip() for f in args.format.split(",") if f.strip()]
            report = service.design(load_requirements(args.spec))
            if args.out:
                for path in write_outputs(report, args.out, formats):
                    print(f"wrote {path}")
            else:
                print(render_json(report) if formats == ["json"] else render_markdown(report))
            return _gate(report.findings, args.fail_on)
        result = service.review(
            load_design(args.design),
            load_requirements(args.spec) if args.spec else None,
            target_pct=args.target,
        )
        print(render_json(result) if args.format == "json" else render_review_markdown(result))
        return _gate(result.findings, args.fail_on)
    except (ArchagentError, ValidationError) as exc:
        print(f"error: {redact(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
