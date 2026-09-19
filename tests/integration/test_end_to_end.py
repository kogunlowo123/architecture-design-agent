"""End-to-end tests: requirements to design, review of hand-written designs, and the CLI."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml
from pydantic import SecretStr

from archagent.cli import main
from archagent.config import load_requirements
from archagent.container import build_service
from archagent.errors import ConfigurationError, SpecError
from archagent.models import ComponentKind, Severity
from tests.conftest import SPECS, make_requirements, make_service, make_settings

SPEC_NAMES = ["startup-saas", "healthcare-portal", "payments-platform"]


class TestDesignPipeline:
    @pytest.mark.parametrize("name", SPEC_NAMES)
    def test_example_specs_produce_complete_reports(self, name: str) -> None:
        report = make_service().design(load_requirements(SPECS / f"{name}.yaml"))
        assert report.design.components and report.design.connections
        assert report.styles[0].score >= report.styles[-1].score or report.styles[-1].disqualified
        assert report.threats and report.adrs and report.summary
        assert report.cost.total_monthly_usd == pytest.approx(
            sum(line.monthly_usd for line in report.cost.lines), abs=0.05
        )
        assert report.availability.downtime_minutes_per_month >= 0

    def test_design_is_deterministic(self) -> None:
        req = load_requirements(SPECS / "healthcare-portal.yaml")
        assert make_service().design(req) == make_service().design(req)

    def test_cloud_choice_changes_products_not_structure(self) -> None:
        base = load_requirements(SPECS / "startup-saas.yaml")
        aws = make_service().design(base)
        gcp = make_service().design(
            base.model_copy(
                update={"constraints": base.constraints.model_copy(update={"cloud": "gcp"})}
            )
        )
        assert [c.id for c in aws.design.components] == [c.id for c in gcp.design.components]
        assert aws.design.component("db").product != gcp.design.component("db").product

    def test_payments_platform_surfaces_hard_problems(self) -> None:
        report = make_service().design(load_requirements(SPECS / "payments-platform.yaml"))
        rules = {f.rule for f in report.findings}
        assert "ARCH-022" in rules and report.design.style in {"microservices", "event_driven"}
        assert any("zero RPO" in q for q in report.open_questions)
        assert {c.kind for c in report.design.components} >= {
            ComponentKind.QUEUE,
            ComponentKind.ML_ENDPOINT,
            ComponentKind.DATA_WAREHOUSE,
        }

    def test_budget_pressure_switches_to_a_cheaper_style(self) -> None:
        req = make_requirements(
            capabilities=["public_api", "auth", "async_jobs", "realtime"],
            workload={"users": 200000, "peak_rps": 3000},
            constraints={"team_size": 40, "monthly_budget_usd": 5000},
        )
        unconstrained = make_service().design(
            req.model_copy(
                update={
                    "constraints": req.constraints.model_copy(update={"monthly_budget_usd": None})
                }
            )
        )
        constrained = make_service().design(req)
        if constrained.design.style != unconstrained.design.style:
            assert constrained.considered and "over the $5,000 budget" in constrained.considered[0]
        assert constrained.cost.total_monthly_usd <= unconstrained.cost.total_monthly_usd

    def test_no_style_fits_the_budget(self) -> None:
        req = make_requirements(constraints={"monthly_budget_usd": 5})
        report = make_service().design(req)
        assert report.considered == [
            "No candidate style fits the budget. The highest-scoring style is shown with its overrun."
        ]
        assert any(f.rule == "ARCH-013" for f in report.findings)

    def test_all_styles_excluded_is_an_error(self) -> None:
        req = make_requirements(
            constraints={
                "avoid": [
                    "layered managed",
                    "modular monolith",
                    "serverless",
                    "event driven",
                    "microservices",
                ]
            }
        )
        with pytest.raises(SpecError, match="no architecture style"):
            make_service().design(req)

    def test_avoiding_a_style_removes_it(self) -> None:
        report = make_service().design(
            make_requirements(constraints={"avoid": ["layered managed"]})
        )
        assert report.design.style != "layered_managed"

    def test_generated_designs_have_no_critical_findings_for_typical_specs(self) -> None:
        for name in ("startup-saas", "healthcare-portal"):
            report = make_service().design(load_requirements(SPECS / f"{name}.yaml"))
            assert not [f for f in report.findings if f.severity is Severity.CRITICAL], name

    def test_pricing_file_flows_through_the_container(self, tmp_path: Path) -> None:
        pricing = tmp_path / "p.yaml"
        pricing.write_text(
            yaml.safe_dump({"prices": {"compute": {"per_replica_monthly": 1}}}), encoding="utf-8"
        )
        req = make_requirements(
            capabilities=["public_api"], constraints={"monthly_budget_usd": 9999}
        )
        cheap = make_service(pricing_file=pricing).design(req)
        normal = make_service().design(req)
        assert cheap.cost.total_monthly_usd < normal.cost.total_monthly_usd

    def test_bad_pricing_file_is_a_configuration_error(self, tmp_path: Path) -> None:
        pricing = tmp_path / "p.yaml"
        pricing.write_text(
            yaml.safe_dump({"prices": {"warp": {"base_monthly": 1}}}), encoding="utf-8"
        )
        with pytest.raises(ConfigurationError):
            make_service(pricing_file=pricing)


class TestLlmSummary:
    def _service(self, reply: str, seen: list[dict[str, object]]):
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

        settings = make_settings(llm_provider="openai", openai_api_key=SecretStr("k"))
        return build_service(
            settings, http_client=httpx.Client(transport=httpx.MockTransport(handler))
        )

    def test_grounded_reply_is_used_and_prompt_has_only_facts(self) -> None:
        seen: list[dict[str, object]] = []
        req = make_requirements(name="Secret Project Name", summary="confidential blurb")
        report = self._service("A layered managed design.", seen).design(req)
        assert report.summary == "A layered managed design."
        sent = json.dumps(seen[0])
        assert (
            "Secret Project Name" not in sent
            and "confidential blurb" not in sent
            and "layered managed" in sent
        )

    def test_ungrounded_reply_falls_back(self) -> None:
        report = self._service("Costs 424242 dollars.", []).design(make_requirements())
        assert "424242" not in report.summary and "design with" in report.summary


class TestReviewFlow:
    def test_generated_design_round_trips_through_review(self, tmp_path: Path) -> None:
        spec = SPECS / "healthcare-portal.yaml"
        assert main(["design", str(spec), "--out", str(tmp_path), "--format", "yaml"]) == 0
        design_file = tmp_path / "patient-portal.design.yaml"
        service = make_service()
        original = service.design(load_requirements(spec))
        from archagent.config import load_design

        reviewed = service.review(load_design(design_file), load_requirements(spec))
        assert reviewed.design == original.design
        assert [f.rule for f in reviewed.findings] == [f.rule for f in original.findings]
        assert reviewed.availability == original.availability

    def test_review_without_requirements_uses_target(self) -> None:
        design = make_service().design(make_requirements(nfr={"availability_pct": 99.0})).design
        strict = make_service().review(design, target_pct=99.999)
        lenient = make_service().review(design, target_pct=90.0)
        assert not strict.availability.meets_target and lenient.availability.meets_target
        assert strict.cost is None and any(f.rule == "ARCH-002" for f in strict.findings)

    def test_handwritten_bad_design_is_flagged(self, tmp_path: Path) -> None:
        bad = {
            "name": "Legacy",
            "style": "modular_monolith",
            "components": [
                {"id": "app", "kind": "compute", "name": "App", "public": True},
                {"id": "db", "kind": "database", "name": "DB", "public": True},
            ],
            "connections": [
                {
                    "source": "app",
                    "target": "db",
                    "encrypted": False,
                    "classification": "restricted",
                }
            ],
        }
        path = tmp_path / "bad.yaml"
        path.write_text(yaml.safe_dump(bad), encoding="utf-8")
        result = make_service().review(
            __import__("archagent.config", fromlist=["load_design"]).load_design(path)
        )
        rules = {f.rule for f in result.findings}
        assert {
            "ARCH-001",
            "ARCH-003",
            "ARCH-004",
            "ARCH-006",
            "ARCH-007",
            "ARCH-011",
            "ARCH-012",
        } <= rules
        assert result.findings[0].severity is Severity.CRITICAL
        assert any(t.status == "open" for t in result.threats)


class TestCli:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        for name in ("ARCHAGENT_LLM_PROVIDER", "ARCHAGENT_PRICING_FILE"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("ARCHAGENT_LOG_LEVEL", "CRITICAL")
        monkeypatch.chdir(tmp_path)

    def test_design_prints_markdown(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["design", str(SPECS / "startup-saas.yaml")]) == 0
        out = capsys.readouterr().out
        assert out.startswith("# Architecture design: Acme Projects") and "```mermaid" in out

    def test_design_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["design", str(SPECS / "startup-saas.yaml"), "--format", "json"]) == 0
        assert json.loads(capsys.readouterr().out)["requirements"]["name"] == "Acme Projects"

    def test_design_writes_files_and_adrs(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "out"
        assert main(["design", str(SPECS / "healthcare-portal.yaml"), "--out", str(out)]) == 0
        assert (out / "patient-portal.design.md").exists() and (out / "patient-portal.mmd").exists()
        assert len(list((out / "adr").glob("*.md"))) >= 4
        assert "wrote" in capsys.readouterr().out

    def test_fail_on_gate(self) -> None:
        payments = str(SPECS / "payments-platform.yaml")
        assert main(["design", payments, "--fail-on", "high"]) == 1
        assert main(["design", payments, "--fail-on", "critical"]) == 0
        assert main(["design", str(SPECS / "healthcare-portal.yaml"), "--fail-on", "high"]) == 0

    def test_validate(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["validate", str(SPECS / "payments-platform.yaml")]) == 0
        out = capsys.readouterr().out
        assert "requirements are valid" in out and "Drivers:" in out and "zero RPO" in out

    def test_catalog(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["catalog", "--cloud", "azure"]) == 0
        out = capsys.readouterr().out
        assert "Azure Front Door" in out and len(out.splitlines()) == len(ComponentKind) + 1

    def test_review_command(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            yaml.safe_dump(
                {
                    "name": "Bad",
                    "style": "layered_managed",
                    "components": [{"id": "db", "kind": "database", "name": "DB", "public": True}],
                }
            ),
            encoding="utf-8",
        )
        assert main(["review", str(bad), "--fail-on", "critical"]) == 1
        assert "ARCH-003" in capsys.readouterr().out
        assert main(["review", str(bad), "--format", "json", "--target", "99"]) == 0
        assert json.loads(capsys.readouterr().out)["findings"]
        assert main(["review", str(bad), "--spec", str(SPECS / "startup-saas.yaml")]) == 0

    def test_errors_exit_two(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["design", str(tmp_path / "missing.yaml")]) == 2
        bad = tmp_path / "bad.yaml"
        bad.write_text("name: x\ncapabilities: [teleport]\n", encoding="utf-8")
        assert main(["validate", str(bad)]) == 2
        assert main(["review", str(bad)]) == 2
        assert main(["design", str(SPECS / "startup-saas.yaml"), "--out", str(bad / "sub")]) == 2
        assert (
            main(
                [
                    "design",
                    str(SPECS / "startup-saas.yaml"),
                    "--out",
                    str(tmp_path / "o"),
                    "--format",
                    "pdf",
                ]
            )
            == 2
        )
        assert capsys.readouterr().err.count("error:") == 5

    def test_llm_provider_without_key_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("ARCHAGENT_LLM_PROVIDER", "anthropic")
        monkeypatch.delenv("ARCHAGENT_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert main(["design", str(SPECS / "startup-saas.yaml")]) == 2
        assert "API_KEY" in capsys.readouterr().err
