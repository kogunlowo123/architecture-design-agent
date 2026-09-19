"""Unit tests for the requirements, pattern, component, reliability, cost and security agents."""

from __future__ import annotations

import math
from typing import Any

import pytest

from archagent.agents.components import HEADROOM, ComponentAgent, monthly_requests
from archagent.agents.cost import EGRESS_PER_GB, STANDBY_REGION_FACTOR, CostAgent
from archagent.agents.patterns import STYLE_MATRIX, disqualification, rank_styles
from archagent.agents.reliability import (
    CORRELATION_CAP,
    FAILOVER_SUCCESS,
    ReliabilityAgent,
    effective_availability,
    is_single_point_of_failure,
)
from archagent.agents.requirements import DIMENSIONS, derive_drivers, open_questions
from archagent.agents.security import SecurityAgent, controls_present
from archagent.catalog import Catalog
from archagent.errors import ConfigurationError
from archagent.models import (
    Component,
    ComponentKind,
    Connection,
    Design,
    Requirements,
)
from tests.conftest import make_requirements

CATALOG = Catalog()


def comp(cid: str, kind: ComponentKind = ComponentKind.COMPUTE, **kw: Any) -> Component:
    data: dict[str, Any] = {"id": cid, "kind": kind, "name": cid}
    data.update(kw)
    return Component.model_validate(data)


def design(components: list[Component], edges: list[Connection] | None = None, **kw: Any) -> Design:
    data: dict[str, Any] = {
        "name": "d",
        "style": "layered_managed",
        "components": components,
        "connections": edges or [],
    }
    data.update(kw)
    return Design.model_validate(data)


class TestModels:
    def test_requirements_defaults_and_dedupe(self) -> None:
        req = make_requirements(
            capabilities=["auth", "auth", "web_ui"], data={"regulations": ["gdpr", "gdpr"]}
        )
        assert req.capabilities == ["auth", "web_ui"] and req.data.regulations == ["gdpr"]
        assert req.nfr.availability_pct == 99.9 and req.constraints.cloud == "any"

    @pytest.mark.parametrize(
        "bad",
        [
            {"name": "bad|name"},
            {"name": "  "},
            {"capabilities": []},
            {"capabilities": ["teleportation"]},
            {"surprise": 1},
            {"nfr": {"availability_pct": 50}},
            {"nfr": {"regions": 9}},
            {"workload": {"peak_rps": 0}},
            {"workload": {"read_ratio": 1.5}},
            {"constraints": {"cloud": "mars"}},
            {"data": {"classification": "secret"}},
        ],
    )
    def test_requirements_rejects_bad_input(self, bad: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            make_requirements(**bad)

    def test_design_rejects_duplicate_ids_and_unknown_edges(self) -> None:
        with pytest.raises(ValueError, match="unique"):
            design([comp("a"), comp("a")])
        with pytest.raises(ValueError, match="unknown component"):
            design([comp("a")], [Connection(source="a", target="ghost")])

    def test_component_id_pattern(self) -> None:
        with pytest.raises(ValueError):
            comp("Bad Id")

    def test_design_lookup_helpers(self) -> None:
        d = design([comp("a"), comp("db", ComponentKind.DATABASE)])
        assert d.component("a").id == "a" and [c.id for c in d.of_kind(ComponentKind.DATABASE)] == [
            "db"
        ]
        with pytest.raises(KeyError):
            d.component("zzz")


class TestDrivers:
    def _weights(self, **overrides: Any) -> dict[str, int]:
        return {d.name: d.weight for d in derive_drivers(make_requirements(**overrides))}

    def test_every_dimension_is_covered(self) -> None:
        assert {d.name for d in derive_drivers(make_requirements())} == set(DIMENSIONS)

    def test_team_size_drives_simplicity(self) -> None:
        assert self._weights(constraints={"team_size": 4})["simplicity"] == 3
        assert self._weights(constraints={"team_size": 15})["simplicity"] == 2
        assert self._weights(constraints={"team_size": 60})["simplicity"] == 1

    def test_scale_availability_cost_latency_events_compliance(self) -> None:
        w = self._weights(
            workload={"peak_rps": 5000, "users": 10000},
            nfr={"availability_pct": 99.99, "p95_latency_ms": 80},
            constraints={"monthly_budget_usd": 900},
            capabilities=["realtime"],
            data={"regulations": ["hipaa"]},
        )
        assert w == {
            "simplicity": 3,
            "scalability": 3,
            "availability": 3,
            "cost": 3,
            "compliance": 3,
            "latency": 3,
            "events": 3,
        }
        low = self._weights(
            workload={"peak_rps": 10}, nfr={"availability_pct": 99}, constraints={"team_size": 30}
        )
        assert (
            low["scalability"] == 1
            and low["availability"] == 1
            and low["cost"] == 1
            and low["events"] == 1
        )
        assert self._weights(workload={"peak_rps": 300})["scalability"] == 2
        assert self._weights(capabilities=["async_jobs"])["events"] == 2
        assert self._weights(constraints={"monthly_budget_usd": 3000})["cost"] == 2
        assert self._weights(data={"pii": True})["compliance"] == 2

    def test_open_questions(self) -> None:
        req = make_requirements(
            capabilities=["payments", "ml_inference", "third_party_integrations"],
            workload={"users": 10, "peak_rps": 500},
            nfr={
                "availability_pct": 99.99,
                "rto_minutes": 5,
                "rpo_minutes": 0,
                "p95_latency_ms": 50,
                "regions": 1,
            },
            data={
                "classification": "public",
                "pii": True,
                "regulations": ["hipaa"],
                "residency": ["eu"],
            },
            constraints={"team_size": 1},
        )
        text = " ".join(open_questions(req))
        for fragment in (
            "one region",
            "RTO",
            "zero RPO",
            "PCI DSS",
            "raise",
            "Personal data",
            "HIPAA",
            "100 ms",
            "units",
            "one-person",
            "third-party",
            "budget",
        ):
            assert fragment.lower() in text.lower(), fragment

    def test_multi_region_residency_question_and_clean_spec(self) -> None:
        req = make_requirements(
            nfr={"regions": 2}, data={"residency": ["eu"]}, constraints={"monthly_budget_usd": 1000}
        )
        assert any("allowed" in q for q in open_questions(req))
        clean = make_requirements(
            constraints={"monthly_budget_usd": 1000}, workload={"users": 5000, "peak_rps": 10}
        )
        assert open_questions(clean) == []


class TestPatterns:
    def test_scores_are_bounded_and_deterministic(self) -> None:
        req = make_requirements()
        drivers = derive_drivers(req)
        first = rank_styles(req, drivers)
        assert first == rank_styles(req, drivers)
        assert all(0 <= s.score <= 100 for s in first) and len(first) == len(STYLE_MATRIX)
        assert [s.score for s in first] == sorted((s.score for s in first), reverse=True)

    def test_small_team_prefers_simple_styles(self) -> None:
        req = make_requirements(
            constraints={"team_size": 3, "monthly_budget_usd": 800}, workload={"peak_rps": 20}
        )
        ranked = rank_styles(req, derive_drivers(req))
        assert ranked[0].style in {"modular_monolith", "layered_managed", "serverless"}
        micro = next(s for s in ranked if s.style == "microservices")
        assert micro.disqualified and micro.score == 0 and ranked[-1].style == "microservices"

    def test_scale_and_events_favour_distributed_styles(self) -> None:
        req = make_requirements(
            capabilities=["realtime", "async_jobs", "public_api"],
            workload={"peak_rps": 8000, "users": 500000, "growth_per_year": 3},
            constraints={"team_size": 60},
        )
        assert rank_styles(req, derive_drivers(req))[0].style in {"event_driven", "microservices"}

    def test_disqualifications(self) -> None:
        assert "team" in disqualification(
            "microservices", make_requirements(constraints={"team_size": 2})
        )
        assert "cold" in disqualification(
            "serverless", make_requirements(nfr={"p95_latency_ms": 40})
        )
        assert (
            disqualification(
                "microservices", make_requirements(constraints={"avoid": ["Kubernetes"]})
            )
            == "listed in constraints.avoid"
        )
        assert (
            disqualification("serverless", make_requirements(constraints={"avoid": ["lambda"]}))
            != ""
        )
        assert (
            disqualification(
                "modular_monolith", make_requirements(constraints={"avoid": ["modular monolith"]})
            )
            != ""
        )
        assert disqualification("layered_managed", make_requirements()) == ""

    def test_strengths_and_weaknesses_follow_heavy_drivers(self) -> None:
        req = make_requirements(constraints={"team_size": 3})
        ranked = {s.style: s for s in rank_styles(req, derive_drivers(req))}
        assert "strong on operational simplicity" in ranked["modular_monolith"].strengths
        assert "weak on operational simplicity" in ranked["event_driven"].weaknesses


def build(style: str, **req_overrides: Any) -> tuple[Requirements, Design]:
    req = make_requirements(**req_overrides)
    return req, ComponentAgent(CATALOG).build(req, style)  # type: ignore[arg-type]


class TestComponents:
    @pytest.mark.parametrize("style", list(STYLE_MATRIX))
    def test_every_style_builds_a_valid_design(self, style: str) -> None:
        _, d = build(
            style,
            capabilities=[
                "web_ui",
                "public_api",
                "auth",
                "async_jobs",
                "search",
                "file_storage",
                "ml_inference",
                "notifications",
                "analytics",
                "realtime",
                "third_party_integrations",
            ],
            data={"regulations": ["hipaa"], "classification": "restricted", "pii": True},
            nfr={"regions": 2, "availability_pct": 99.99},
        )
        ids = {c.id for c in d.components}
        assert d.style == style and d.regions == 2 and len(ids) == len(d.components)
        kinds = {c.kind for c in d.components}
        assert {
            ComponentKind.DATABASE,
            ComponentKind.SECRETS,
            ComponentKind.OBSERVABILITY,
            ComponentKind.IDENTITY,
            ComponentKind.WAF,
        } <= kinds
        assert {
            ComponentKind.QUEUE,
            ComponentKind.SEARCH,
            ComponentKind.OBJECT_STORAGE,
            ComponentKind.ML_ENDPOINT,
            ComponentKind.DATA_WAREHOUSE,
            ComponentKind.NOTIFICATION,
        } <= kinds
        assert all(e.source in ids and e.target in ids for e in d.connections)

    def test_sizing_follows_the_headroom_rule(self) -> None:
        _, d = build("modular_monolith", workload={"peak_rps": 1000})
        capacity = CATALOG[ComponentKind.COMPUTE].capacity_rps or 1
        assert d.component("app").replicas == math.ceil(1000 * HEADROOM / capacity)

    def test_high_availability_adds_redundancy(self) -> None:
        _, ha = build("layered_managed", nfr={"availability_pct": 99.95})
        _, basic = build("layered_managed", nfr={"availability_pct": 99.0})
        assert ha.component("api").replicas >= 2 and ha.component("api").zones == 2
        assert ha.component("db").replicas == 2 and ha.component("db").replication == "sync"
        assert basic.component("api").replicas == 1 and basic.component("db").replication == "none"
        _, top = build("layered_managed", nfr={"availability_pct": 99.99})
        assert top.component("api").zones == 3

    def test_cache_and_read_replicas(self) -> None:
        _, cached = build("layered_managed", workload={"peak_rps": 600, "read_ratio": 0.9})
        assert "cache" in {c.id for c in cached.components}
        _, none = build(
            "layered_managed",
            workload={"peak_rps": 20, "read_ratio": 0.5},
            nfr={"p95_latency_ms": 900},
        )
        assert "cache" not in {c.id for c in none.components}
        _, heavy = build(
            "modular_monolith",
            workload={"peak_rps": 6000, "read_ratio": 0.95},
            nfr={"p95_latency_ms": 900},
        )
        assert heavy.component("db").replicas > 2 and "read replica" in heavy.component("db").notes

    def test_cloud_product_names(self) -> None:
        for cloud, expected in (
            ("aws", "Amazon RDS"),
            ("azure", "Azure Database"),
            ("gcp", "Cloud SQL"),
            ("any", "Managed PostgreSQL"),
        ):
            _, d = build("layered_managed", constraints={"cloud": cloud})
            assert expected in d.component("db").product

    def test_edge_layer_depends_on_exposure_and_style(self) -> None:
        _, internal = build("layered_managed", capabilities=["async_jobs", "auth"])
        assert not [
            c for c in internal.components if c.public and c.kind is not ComponentKind.IDENTITY
        ]
        _, layered = build("layered_managed", capabilities=["public_api"])
        assert {"lb", "waf"} <= {c.id for c in layered.components}
        _, serverless = build("serverless", capabilities=["public_api"])
        assert "lb" not in {c.id for c in serverless.components} and "gateway" in {
            c.id for c in serverless.components
        }
        _, cdn = build("layered_managed", capabilities=["web_ui"], workload={"users": 50000})
        assert "cdn" in {c.id for c in cdn.components}

    def test_identity_marks_authentication(self) -> None:
        _, with_auth = build("modular_monolith", capabilities=["public_api", "auth"])
        _, without = build("modular_monolith", capabilities=["public_api"])
        assert with_auth.component("app").authn and ComponentKind.IDENTITY in {
            c.kind for c in with_auth.components
        }
        assert not without.component("app").authn

    def test_microservices_get_one_database_each(self) -> None:
        _, d = build(
            "microservices",
            capabilities=["public_api", "auth", "search", "file_storage", "notifications"],
        )
        services = [c for c in d.components if c.id.startswith("svc")]
        databases = [c for c in d.components if c.kind is ComponentKind.DATABASE]
        assert 2 <= len(services) <= 4 and len(databases) == len(services)

    def test_regulated_data_is_audited_and_notes_added(self) -> None:
        _, d = build(
            "layered_managed",
            data={"regulations": ["gdpr"], "classification": "confidential"},
            capabilities=["web_ui", "third_party_integrations", "realtime"],
        )
        assert (
            d.component("db").audit_logging and "customer-managed keys" in d.component("db").notes
        )
        assert any("egress" in c.notes for c in d.components) and any(
            "WebSocket" in c.notes for c in d.components
        )

    def test_monthly_requests_formula(self) -> None:
        assert monthly_requests(10) == pytest.approx(10 * 0.3 * 2_592_000)

    def test_builder_is_reusable(self) -> None:
        agent = ComponentAgent(CATALOG)
        req = make_requirements()
        assert agent.build(req, "layered_managed") == agent.build(req, "layered_managed")
        assert agent.build(req, "serverless").style == "serverless"


class TestReliability:
    def test_effective_availability(self) -> None:
        assert effective_availability(comp("a", availability=0.995)) == 0.995
        assert effective_availability(comp("a", availability=0.995, replicas=3, zones=1)) == 0.995
        assert (
            effective_availability(comp("a", availability=0.995, replicas=2, zones=2))
            == CORRELATION_CAP
        )
        assert effective_availability(
            comp("a", availability=0.9, replicas=2, zones=2)
        ) == pytest.approx(0.99)
        assert (
            effective_availability(comp("a", availability=0.9995, managed_redundancy=True))
            == 0.9995
        )

    def test_stack_product_and_downtime(self) -> None:
        d = design(
            [
                comp("a", availability=0.99),
                comp("m", ComponentKind.QUEUE, availability=0.999, managed_redundancy=True),
            ]
        )
        report = ReliabilityAgent().analyze(d, 99.0)
        assert (
            report.stack_pct == pytest.approx(98.901, abs=1e-3)
            and report.achieved_pct == report.stack_pct
        )
        assert report.downtime_minutes_per_month == pytest.approx((1 - 0.98901) * 43200, abs=0.1)
        assert report.meets_target is False
        assert report.weakest == "a" and report.single_points_of_failure == ["a"]

    def test_standby_region_credit(self) -> None:
        d = design([comp("a", availability=0.99)], regions=2)
        stack = 0.99
        expected = stack + (1 - stack) * stack * FAILOVER_SUCCESS
        assert ReliabilityAgent().analyze(d, 99.0).achieved_pct == pytest.approx(
            expected * 100, abs=1e-3
        )
        three = design([comp("a", availability=0.99)], regions=3)
        assert (
            ReliabilityAgent().analyze(three, 99.0).achieved_pct
            > ReliabilityAgent().analyze(d, 99.0).achieved_pct
        )

    def test_non_critical_components_are_off_the_serial_path(self) -> None:
        d = design(
            [
                comp("a", availability=0.999, replicas=2, zones=2),
                comp("c", ComponentKind.CACHE, availability=0.5, critical=False),
            ]
        )
        report = ReliabilityAgent().analyze(d, 99.9)
        assert [line.component for line in report.lines] == ["a"] and report.meets_target

    def test_single_point_of_failure_rules(self) -> None:
        assert is_single_point_of_failure(comp("a"))
        assert is_single_point_of_failure(comp("a", replicas=3, zones=1))
        assert not is_single_point_of_failure(comp("a", replicas=2, zones=2))
        assert not is_single_point_of_failure(comp("a", managed_redundancy=True))
        assert not is_single_point_of_failure(comp("a", critical=False))

    def test_empty_critical_path(self) -> None:
        report = ReliabilityAgent().analyze(
            design([comp("c", ComponentKind.CACHE, critical=False)]), 99.9
        )
        assert report.stack_pct == 100.0 and report.weakest == "" and report.meets_target


class TestCost:
    def test_explicit_cost_and_egress(self) -> None:
        req = make_requirements(
            workload={"peak_rps": 10, "avg_payload_kb": 100},
            constraints={"monthly_budget_usd": 1000},
        )
        d = design([comp("a", monthly_cost_usd=123.456)])
        report = CostAgent(CATALOG).estimate(d, req)
        assert (
            report.lines[0].monthly_usd == 123.46
            and report.lines[0].basis == "stated in the design"
        )
        egress_gb = monthly_requests(10) * 100 / 1_000_000
        assert report.lines[-1].monthly_usd == pytest.approx(egress_gb * EGRESS_PER_GB, abs=0.01)
        assert report.total_monthly_usd == pytest.approx(
            123.46 + report.lines[-1].monthly_usd, abs=0.01
        )
        assert report.within_budget is True and report.budget_usd == 1000

    def test_catalogue_pricing_components(self) -> None:
        req = make_requirements(workload={"peak_rps": 100, "data_gb": 100, "growth_per_year": 0})
        d = design([comp("app", replicas=3), comp("db", ComponentKind.DATABASE, replicas=2)])
        lines = {line.item: line for line in CostAgent(CATALOG).estimate(d, req).lines}
        assert lines["app"].monthly_usd == 210.0
        assert lines["db"].monthly_usd == pytest.approx(2 * 180 + 0.12 * 100 * 2)

    def test_request_priced_components(self) -> None:
        req = make_requirements(workload={"peak_rps": 100})
        d = design([comp("gw", ComponentKind.API_GATEWAY, managed_redundancy=True)])
        expected = monthly_requests(100) / 1e6 * 3.5
        assert {line.item: line.monthly_usd for line in CostAgent(CATALOG).estimate(d, req).lines}[
            "gw"
        ] == pytest.approx(expected, abs=0.01)

    def test_standby_regions_cost_more(self) -> None:
        req = make_requirements()
        one = CostAgent(CATALOG).estimate(design([comp("app")]), req).lines[0].monthly_usd
        two = (
            CostAgent(CATALOG).estimate(design([comp("app")], regions=2), req).lines[0].monthly_usd
        )
        assert two == pytest.approx(one * (1 + STANDBY_REGION_FACTOR))

    def test_budget_status_and_levers(self) -> None:
        req = make_requirements(constraints={"monthly_budget_usd": 50})
        d = design(
            [
                comp("app", replicas=4),
                comp("db1", ComponentKind.DATABASE),
                comp("db2", ComponentKind.DATABASE),
            ],
            regions=2,
        )
        report = CostAgent(CATALOG).estimate(d, req)
        assert report.within_budget is False
        text = " ".join(report.levers)
        assert (
            "% of spend" in text
            and "Share one database" in text
            and "second region" in text
            and "over budget" in text
        )
        assert CostAgent(CATALOG).estimate(d, make_requirements()).within_budget is None

    def test_price_overrides(self) -> None:
        cheaper = CATALOG.with_overrides({"compute": {"per_replica_monthly": 10}})
        req = make_requirements()
        assert CostAgent(cheaper).estimate(design([comp("app")]), req).lines[0].monthly_usd == 10
        assert CATALOG[ComponentKind.COMPUTE].per_replica_monthly == 70

    @pytest.mark.parametrize(
        "bad",
        [
            {"warp_drive": {"base_monthly": 1}},
            {"compute": {"colour": 1}},
            {"compute": {"per_replica_monthly": -1}},
            {"compute": {"per_replica_monthly": "cheap"}},
        ],
    )
    def test_bad_overrides(self, bad: dict[str, Any]) -> None:
        with pytest.raises(ConfigurationError):
            CATALOG.with_overrides(bad)

    def test_catalogue_is_complete(self) -> None:
        assert set(CATALOG.kinds()) == set(ComponentKind)
        for entry in CATALOG:
            assert (
                entry.product("aws")
                and entry.product("azure")
                and entry.product("gcp")
                and entry.product("any")
            )


class TestSecurity:
    def _secure(self) -> Design:
        return design(
            [
                comp("waf", ComponentKind.WAF, public=True),
                comp("app", authn=True, audit_logging=True),
                comp(
                    "db",
                    ComponentKind.DATABASE,
                    encrypted_at_rest=True,
                    backup_interval_minutes=5,
                    audit_logging=True,
                ),
                comp("q", ComponentKind.QUEUE, dead_letter=True),
                comp("sec", ComponentKind.SECRETS),
                comp("obs", ComponentKind.OBSERVABILITY),
            ],
            [Connection(source="waf", target="app"), Connection(source="app", target="db")],
        )

    def test_controls_detected(self) -> None:
        assert controls_present(self._secure()) == {
            "waf",
            "rate_limit",
            "authn",
            "tls",
            "encryption_at_rest",
            "secrets_mgmt",
            "audit_logging",
            "network_isolation",
            "backup",
            "monitoring",
            "dead_letter",
        }

    def test_controls_missing(self) -> None:
        bare = design(
            [comp("app"), comp("db", ComponentKind.DATABASE, public=True)],
            [Connection(source="app", target="db", encrypted=False)],
        )
        assert controls_present(bare) == set()

    def test_secure_design_has_no_open_threats(self) -> None:
        threats = SecurityAgent().analyze(self._secure())
        assert threats and all(t.status == "mitigated" for t in threats)
        assert [t.id for t in threats] == [f"T-{i:03d}" for i in range(1, len(threats) + 1)]

    def test_weak_design_threat_status(self) -> None:
        bare = design(
            [
                comp("lb", ComponentKind.LOAD_BALANCER, public=True),
                comp("app", public=True),
                comp("db", ComponentKind.DATABASE),
            ],
            [
                Connection(source="lb", target="app", encrypted=False),
                Connection(source="app", target="db"),
            ],
        )
        threats = SecurityAgent().analyze(bare)
        by = {(t.component, t.category): t for t in threats}
        dos = by[("lb", "denial_of_service")]
        assert (
            dos.status == "open" and dos.present_controls == [] and "firewall" in dos.recommendation
        )
        tls = by[("lb", "tampering")]
        assert tls.status == "open"
        disclosure = by[("db", "disclosure")]
        assert disclosure.status == "partial" and disclosure.present_controls == [
            "network_isolation"
        ]
        assert ("app", "spoofing") in by

    def test_public_classification_drops_repudiation(self) -> None:
        d = design([comp("app")])
        public = SecurityAgent().analyze(d, make_requirements(data={"classification": "public"}))
        internal = SecurityAgent().analyze(d, make_requirements())
        assert {t.category for t in public} == {"elevation"} and {"elevation", "repudiation"} == {
            t.category for t in internal
        }

    def test_kind_specific_threats(self) -> None:
        d = design(
            [
                comp(f"c_{k.value}", k)
                for k in (
                    ComponentKind.OBJECT_STORAGE,
                    ComponentKind.SEARCH,
                    ComponentKind.CACHE,
                    ComponentKind.DATA_WAREHOUSE,
                    ComponentKind.QUEUE,
                    ComponentKind.IDENTITY,
                    ComponentKind.ML_ENDPOINT,
                )
            ]
        )
        assert {t.component for t in SecurityAgent().analyze(d)} == {c.id for c in d.components}

    def test_math_helper_is_consistent(self) -> None:
        assert math.isclose(
            effective_availability(comp("a", availability=0.99, replicas=2, zones=2)), 0.9999
        )
