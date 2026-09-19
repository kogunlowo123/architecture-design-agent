"""Unit tests for the review rules, ADRs, summaries, rendering and configuration loading."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from archagent.agents.adrs import build_adrs
from archagent.agents.cost import CostAgent
from archagent.agents.patterns import rank_styles
from archagent.agents.reliability import ReliabilityAgent
from archagent.agents.requirements import derive_drivers
from archagent.agents.review import RULES, ReviewAgent
from archagent.agents.security import SecurityAgent
from archagent.agents.summary import (
    LLMSummaryWriter,
    SummaryFacts,
    TemplateSummaryWriter,
    facts_for,
)
from archagent.catalog import Catalog
from archagent.config import load_design, load_pricing, load_requirements, read_mapping
from archagent.errors import ConfigurationError, ProviderError, RenderError, SpecError
from archagent.models import ComponentKind, Connection, Design, Severity
from archagent.render import (
    render_adr,
    render_design_yaml,
    render_json,
    render_markdown,
    render_mermaid,
    write_outputs,
)
from archagent.security import md_cell, mermaid_label, redact, slugify
from tests.conftest import SPECS, make_requirements, make_service
from tests.unit.test_agents import comp, design

CATALOG = Catalog()


def review(d: Design, req: Any = None) -> dict[str, Any]:
    target = req.nfr.availability_pct if req else 99.9
    availability = ReliabilityAgent().analyze(d, target)
    cost = CostAgent(CATALOG).estimate(d, req) if req else None
    return {f.rule: f for f in ReviewAgent(CATALOG).review(d, availability, req, cost)}


def good_design() -> Design:
    return design(
        [
            comp(
                "waf", ComponentKind.WAF, public=True, managed_redundancy=True, availability=0.9999
            ),
            comp(
                "app",
                replicas=4,
                zones=2,
                authn=True,
                public=False,
                availability=0.995,
                audit_logging=True,
            ),
            comp(
                "db",
                ComponentKind.DATABASE,
                replicas=2,
                zones=2,
                encrypted_at_rest=True,
                replication="sync",
                backup_interval_minutes=5,
                latency_ms=8,
            ),
            comp("idp", ComponentKind.IDENTITY, managed_redundancy=True, availability=0.9999),
            comp("sec", ComponentKind.SECRETS, critical=False),
            comp("obs", ComponentKind.OBSERVABILITY, critical=False),
        ],
        [Connection(source="waf", target="app"), Connection(source="app", target="db")],
    )


class TestReviewRules:
    def test_a_sound_design_is_clean(self) -> None:
        assert review(good_design()) == {}

    def test_every_rule_can_fire_and_ids_are_unique(self) -> None:
        req = make_requirements(
            capabilities=["auth", "payments"],
            data={"regulations": ["hipaa"], "residency": ["eu"], "pii": True},
            nfr={
                "rto_minutes": 5,
                "regions": 2,
                "rpo_minutes": 1,
                "p95_latency_ms": 20,
                "availability_pct": 99.99,
            },
            workload={"peak_rps": 5000, "read_ratio": 0.2},
            constraints={"monthly_budget_usd": 10, "team_size": 2, "avoid": ["queue"]},
        )
        bad = design(
            [
                comp(
                    "gw",
                    ComponentKind.API_GATEWAY,
                    public=True,
                    managed_redundancy=True,
                    latency_ms=50,
                ),
                comp("app", latency_ms=100),
                comp("db", ComponentKind.DATABASE, public=True, latency_ms=50),
                comp("q", ComponentKind.QUEUE, managed_redundancy=True, product="queue"),
            ],
            [Connection(source="app", target="db", encrypted=False, classification="restricted")],
            style="microservices",
        )
        fired = set(review(bad, req))
        # ARCH-017 needs two regions while ARCH-010 needs fewer than required, so they never co-occur.
        assert fired == {f"ARCH-{n:03d}" for n in range(1, 23) if n != 17}
        assert len(RULES) == 19

    def test_spof_severity_depends_on_target(self) -> None:
        d = design([comp("app")])
        assert review(d)["ARCH-001"].severity is Severity.HIGH
        low = ReviewAgent(CATALOG).review(d, ReliabilityAgent().analyze(d, 99.0))
        assert next(f for f in low if f.rule == "ARCH-001").severity is Severity.LOW

    def test_availability_gap_severity(self) -> None:
        d = design([comp("app", availability=0.99)])
        assert review(d)["ARCH-002"].severity is Severity.CRITICAL
        near = design([comp("app", availability=0.9985, replicas=2, zones=1)])
        assert review(near)["ARCH-002"].severity is Severity.HIGH
        assert "zone redundancy" in review(near)["ARCH-002"].recommendation
        managed = design(
            [comp("idp", ComponentKind.IDENTITY, availability=0.998, managed_redundancy=True)]
        )
        assert "managed service" in review(managed)["ARCH-002"].recommendation

    def test_public_data_store(self) -> None:
        f = review(design([comp("db", ComponentKind.DATABASE, public=True)]))["ARCH-003"]
        assert f.severity is Severity.CRITICAL and f.components == ["db"]

    def test_authentication_rules(self) -> None:
        exposed = design(
            [comp("gw", ComponentKind.API_GATEWAY, public=True, managed_redundancy=True)]
        )
        assert "ARCH-004" in review(exposed)
        assert "ARCH-004" not in review(
            design(
                [
                    comp(
                        "gw",
                        ComponentKind.API_GATEWAY,
                        public=True,
                        authn=True,
                        managed_redundancy=True,
                    )
                ]
            )
        )
        req = make_requirements(data={"classification": "public"})
        assert "ARCH-004" not in review(exposed, req)
        needs_idp = make_requirements(capabilities=["auth"])
        assert "ARCH-005" in review(design([comp("app")]), needs_idp)
        assert "ARCH-005" not in review(
            design([comp("idp", ComponentKind.IDENTITY, managed_redundancy=True)]), needs_idp
        )

    def test_plaintext_connections(self) -> None:
        d = design(
            [comp("a"), comp("b")],
            [Connection(source="a", target="b", encrypted=False, classification="restricted")],
        )
        assert review(d)["ARCH-006"].severity is Severity.HIGH
        d2 = design([comp("a"), comp("b")], [Connection(source="a", target="b", encrypted=False)])
        assert review(d2)["ARCH-006"].severity is Severity.MEDIUM

    def test_encryption_at_rest_severity(self) -> None:
        d = design([comp("db", ComponentKind.DATABASE)])
        assert review(d)["ARCH-007"].severity is Severity.MEDIUM
        assert (
            review(d, make_requirements(data={"pii": True}))["ARCH-007"].severity is Severity.HIGH
        )
        assert (
            review(d, make_requirements(data={"classification": "confidential"}))[
                "ARCH-007"
            ].severity
            is Severity.HIGH
        )

    def test_recovery_point(self) -> None:
        req = make_requirements(nfr={"rpo_minutes": 5})
        slow = design([comp("db", ComponentKind.DATABASE, backup_interval_minutes=60)])
        assert "ARCH-008" in review(slow, req)
        assert "ARCH-008" not in review(
            design([comp("db", ComponentKind.DATABASE, backup_interval_minutes=5)]), req
        )
        assert "ARCH-008" not in review(
            design([comp("db", ComponentKind.DATABASE, replication="sync")]), req
        )
        assert "ARCH-008" not in review(
            design([comp("db", ComponentKind.DATABASE, replication="async")]), req
        )
        assert "ARCH-008" in review(
            design([comp("db", ComponentKind.DATABASE, replication="async")]),
            make_requirements(nfr={"rpo_minutes": 1}),
        )

    def test_recovery_time_and_regions(self) -> None:
        req = make_requirements(nfr={"rto_minutes": 5, "regions": 2})
        found = review(design([comp("app")]), req)
        assert "ARCH-009" in found and "ARCH-010" in found
        assert "ARCH-009" not in review(design([comp("app")], regions=2), req)

    def test_observability_and_waf(self) -> None:
        d = design([comp("lb", ComponentKind.LOAD_BALANCER, public=True, managed_redundancy=True)])
        found = review(d)
        assert "ARCH-011" in found and found["ARCH-012"].severity is Severity.MEDIUM
        assert (
            review(d, make_requirements(data={"classification": "confidential"}))[
                "ARCH-012"
            ].severity
            is Severity.HIGH
        )
        assert (
            review(d, make_requirements(capabilities=["payments"]))["ARCH-012"].severity
            is Severity.HIGH
        )
        with_waf = design(
            [
                comp("lb", ComponentKind.LOAD_BALANCER, public=True, managed_redundancy=True),
                comp("w", ComponentKind.WAF, managed_redundancy=True),
            ]
        )
        assert "ARCH-012" not in review(with_waf)

    def test_budget_severity(self) -> None:
        d = design([comp("app", monthly_cost_usd=1000)])
        assert (
            review(d, make_requirements(constraints={"monthly_budget_usd": 900}))[
                "ARCH-013"
            ].severity
            is Severity.MEDIUM
        )
        assert (
            review(d, make_requirements(constraints={"monthly_budget_usd": 500}))[
                "ARCH-013"
            ].severity
            is Severity.HIGH
        )
        assert "ARCH-013" not in review(
            d, make_requirements(constraints={"monthly_budget_usd": 5000})
        )
        assert "ARCH-013" not in review(d, make_requirements())

    def test_team_fit(self) -> None:
        d = design([comp("s1"), comp("s2")], style="microservices")
        assert "ARCH-014" in review(d, make_requirements(constraints={"team_size": 4}))
        assert "ARCH-014" not in review(d, make_requirements(constraints={"team_size": 30}))
        assert "ARCH-014" not in review(
            design([comp("s1")]), make_requirements(constraints={"team_size": 4})
        )

    def test_compliance(self) -> None:
        req = make_requirements(data={"regulations": ["hipaa"]})
        found = review(design([comp("app")]), req)
        assert "ARCH-015" in found and found["ARCH-016"].severity is Severity.HIGH
        ok = design(
            [comp("app", audit_logging=True), comp("s", ComponentKind.SECRETS, critical=False)]
        )
        found = review(ok, req)
        assert "ARCH-015" not in found and "ARCH-016" not in found
        assert "ARCH-016" not in review(
            design([comp("app")]), make_requirements(data={"regulations": ["gdpr"]})
        )

    def test_residency_dead_letter_avoid(self) -> None:
        req = make_requirements(data={"residency": ["eu"]}, constraints={"avoid": ["Redis", " "]})
        d = design(
            [
                comp("cache", ComponentKind.CACHE, product="Azure Cache for Redis"),
                comp("q", ComponentKind.QUEUE, managed_redundancy=True),
            ],
            regions=2,
        )
        found = review(d, req)
        assert (
            "ARCH-017" in found
            and "ARCH-018" in found
            and found["ARCH-019"].components == ["cache"]
        )
        assert "ARCH-019" not in review(d, make_requirements(constraints={"avoid": ["oracle"]}))

    def test_latency_and_capacity(self) -> None:
        slow = design(
            [comp("app", latency_ms=200), comp("db", ComponentKind.DATABASE, latency_ms=50)]
        )
        assert "ARCH-020" in review(slow, make_requirements(nfr={"p95_latency_ms": 300}))
        assert "ARCH-020" not in review(slow, make_requirements(nfr={"p95_latency_ms": 900}))
        small = design([comp("app")])
        found = review(small, make_requirements(workload={"peak_rps": 5000}))
        assert found["ARCH-021"].severity is Severity.HIGH
        heavy = design([comp("db", ComponentKind.DATABASE)])
        assert "ARCH-022" in review(
            heavy, make_requirements(workload={"peak_rps": 2000, "read_ratio": 0.5})
        )
        assert "ARCH-022" not in review(heavy, make_requirements(workload={"peak_rps": 100}))

    def test_findings_sorted_most_severe_first(self) -> None:
        found = list(
            review(
                design([comp("db", ComponentKind.DATABASE, public=True), comp("app")]),
                make_requirements(),
            ).values()
        )
        ranks = [f.severity.rank for f in found]
        assert ranks == sorted(ranks, reverse=True) and found[0].severity is Severity.CRITICAL


class TestAdrsAndSummary:
    def _report(self) -> Any:
        service = make_service()
        return service.design(load_requirements(SPECS / "healthcare-portal.yaml"))

    def test_adrs_numbered_and_grounded(self) -> None:
        report = self._report()
        assert [a.number for a in report.adrs] == list(range(1, len(report.adrs) + 1))
        text = "\n".join(render_adr(a) for a in report.adrs)
        assert "layered managed" in text and "99.95" in text and "Status: Proposed" in text
        assert "dead-letter" in text

    def test_adr_without_queue_has_no_async_decision(self) -> None:
        req = make_requirements(capabilities=["public_api", "auth"])
        report = make_service().design(req)
        assert not any("queue" in a.title.lower() for a in report.adrs)

    def test_template_summary_and_facts(self) -> None:
        report = self._report()
        facts = facts_for(
            report.design, report.availability, report.cost, report.findings, report.threats
        )
        text = TemplateSummaryWriter().write(facts)
        assert (
            "layered managed" in text
            and "the target is met" in text
            and "within the $30,000 budget" in text
        )
        assert (
            "Patient Portal" not in facts.model_dump_json() and "web" not in facts.model_dump_json()
        )

    def _facts(self) -> SummaryFacts:
        report = self._report()
        return facts_for(
            report.design, report.availability, report.cost, report.findings, report.threats
        )

    def test_llm_summary_grounding(self) -> None:
        facts = self._facts()

        class Good:
            def complete(self, system: str, user: str) -> str:
                return f"A {facts.style} design with {facts.components} components."

        class Invented:
            def complete(self, system: str, user: str) -> str:
                return "Achieves 99.5 percent with 9999 users."

        class Down:
            def complete(self, system: str, user: str) -> str:
                raise ProviderError("down")

        assert LLMSummaryWriter(Good()).write(facts).startswith("A layered managed")
        expected = TemplateSummaryWriter().write(facts)
        assert LLMSummaryWriter(Invented()).write(facts) == expected
        assert LLMSummaryWriter(Down()).write(facts) == expected

    def test_summary_when_target_missed_and_over_budget(self) -> None:
        req = make_requirements(
            nfr={"availability_pct": 99.999},
            constraints={"monthly_budget_usd": 100},
            workload={"peak_rps": 500},
        )
        report = make_service().design(req)
        text = report.summary
        assert "not met" in text and "over the $100 budget" in text

    def test_build_adrs_directly(self) -> None:
        req = make_requirements()
        drivers = derive_drivers(req)
        styles = rank_styles(req, drivers)
        report = make_service().design(req)
        adrs = build_adrs(
            req,
            drivers,
            styles,
            ["note"],
            report.design,
            report.availability,
            SecurityAgent().analyze(report.design),
            report.cost,
        )
        assert "note" in " ".join(adrs[0].consequences)


class TestRendering:
    def test_helpers(self) -> None:
        assert md_cell("a|b\n<x>&") == "a\\|b &lt;x&gt;&amp;"
        assert mermaid_label('a"b<c>[d](e){f}|g#h;i\nj') == "a b c d e f g h i j"
        assert slugify("Acme  Projects!") == "acme-projects" and slugify("!!!") == "design"
        assert "abcd1234efgh" not in redact("token=abcd1234efgh")
        assert "sk-" + "a" * 30 not in redact("key sk-" + "a" * 30)

    def test_mermaid_structure_and_safety(self) -> None:
        d = design(
            [
                comp(
                    "end",
                    name='Evil "]; click x',
                    public=True,
                    replicas=2,
                    zones=2,
                    product="Prod<br>uct",
                ),
                comp("db-1", ComponentKind.DATABASE, tier="data"),
            ],
            [Connection(source="end", target="db-1", encrypted=False, protocol='tcp"|x')],
        )
        text = render_mermaid(d)
        assert text.startswith("flowchart LR") and "n_end" in text and "n_db_1" in text
        assert "-.->" in text and "class n_end public" in text and "x2 in 2 zones" in text
        assert '"]; click' not in text and "<br>" not in text.replace("<br/>", "")
        assert "[(" in text

    def test_markdown_document_is_complete_and_escaped(self) -> None:
        req = make_requirements(
            name="Odd & Name (test)", summary="line one\n# injected <b>bold</b>"
        )
        report = make_service().design(req)
        md = render_markdown(report)
        for heading in (
            "## Summary",
            "## Drivers",
            "## Style selection",
            "## Architecture",
            "## Availability",
            "## Cost",
            "## Review findings",
            "## Threat model (STRIDE)",
            "## Decisions",
            "## Open questions",
        ):
            assert heading in md
        assert "\n# injected" not in md and "<b>" not in md and "```mermaid" in md

    def test_json_and_yaml_round_trip(self, tmp_path: Path) -> None:
        report = make_service().design(make_requirements())
        assert json.loads(render_json(report))["design"]["name"] == "Test System"
        path = tmp_path / "d.yaml"
        path.write_text(render_design_yaml(report.design), encoding="utf-8")
        assert load_design(path) == report.design

    def test_write_outputs(self, tmp_path: Path) -> None:
        report = make_service().design(make_requirements())
        paths = write_outputs(report, tmp_path / "out", ["md", "json", "yaml", "mmd"])
        names = {p.name for p in paths}
        assert {
            "test-system.design.md",
            "test-system.design.json",
            "test-system.design.yaml",
            "test-system.mmd",
        } <= names
        assert any(p.parent.name == "adr" and p.name.startswith("0001-") for p in paths)
        only_json = write_outputs(report, tmp_path / "j", ["json"])
        assert [p.name for p in only_json] == ["test-system.design.json"]

    def test_write_errors(self, tmp_path: Path) -> None:
        report = make_service().design(make_requirements())
        with pytest.raises(RenderError, match="unknown format"):
            write_outputs(report, tmp_path, ["pdf"])
        blocker = tmp_path / "file"
        blocker.write_text("x", encoding="utf-8")
        with pytest.raises(RenderError):
            write_outputs(report, blocker / "sub", ["md"])


class TestConfigLoading:
    def test_spec_files_load(self) -> None:
        for name in ("startup-saas", "healthcare-portal", "payments-platform"):
            assert load_requirements(SPECS / f"{name}.yaml").name

    def test_json_is_accepted(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        path.write_text(json.dumps({"name": "J", "capabilities": ["web_ui"]}), encoding="utf-8")
        assert load_requirements(path).name == "J"

    def test_errors(self, tmp_path: Path) -> None:
        with pytest.raises(SpecError, match="cannot read"):
            load_requirements(tmp_path / "missing.yaml")
        listing = tmp_path / "l.yaml"
        listing.write_text("- a\n", encoding="utf-8")
        with pytest.raises(SpecError, match="mapping"):
            read_mapping(listing)
        broken = tmp_path / "b.yaml"
        broken.write_text("a: [x\n", encoding="utf-8")
        with pytest.raises(SpecError):
            load_requirements(broken)
        invalid = tmp_path / "i.yaml"
        invalid.write_text(
            yaml.safe_dump({"name": "x", "capabilities": ["nope"], "nfr": {"regions": 9}}),
            encoding="utf-8",
        )
        with pytest.raises(SpecError, match="capabilities"):
            load_requirements(invalid)
        with pytest.raises(SpecError, match="not valid"):
            load_design(invalid)
        huge = tmp_path / "h.yaml"
        huge.write_text("a: " + "x" * 1_100_000, encoding="utf-8")
        with pytest.raises(SpecError, match="larger"):
            read_mapping(huge)

    def test_pricing(self, tmp_path: Path) -> None:
        assert load_pricing(None) == {}
        path = tmp_path / "p.yaml"
        path.write_text(
            yaml.safe_dump({"prices": {"compute": {"per_replica_monthly": 5}}}), encoding="utf-8"
        )
        assert load_pricing(path) == {"compute": {"per_replica_monthly": 5}}
        bare = tmp_path / "bare.yaml"
        bare.write_text(yaml.safe_dump({"compute": {"per_replica_monthly": 6}}), encoding="utf-8")
        assert load_pricing(bare)["compute"]["per_replica_monthly"] == 6
        for content in ("- x" + chr(10), "prices: {compute: 5}" + chr(10)):
            bad = tmp_path / "bad.yaml"
            bad.write_text(content, encoding="utf-8")
            with pytest.raises(ConfigurationError):
                load_pricing(bad)
        with pytest.raises(ConfigurationError):
            load_pricing(tmp_path / "absent.yaml")

    def test_example_pricing_file_applies(self) -> None:
        prices = load_pricing(SPECS.parent / "pricing.example.yaml")
        assert Catalog().with_overrides(prices)[ComponentKind.COMPUTE].per_replica_monthly == 58

    def test_env_example_parses(self) -> None:
        from archagent.config import Settings

        settings = Settings(_env_file=SPECS.parent.parent / ".env.example")  # type: ignore[call-arg]
        assert settings.llm_provider == "none" and settings.pricing_file is None
