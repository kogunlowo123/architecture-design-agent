"""Building-block catalogue: availability, latency, capacity and illustrative price per component kind.

The numbers are planning defaults, not quotes. Availability figures follow the shape of published
service-level agreements, latency figures are typical medians, and prices are round illustrative
monthly amounts. Override any price with a pricing file (see ``configs/pricing.example.yaml``).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace

from archagent.errors import ConfigurationError
from archagent.models import Cloud, ComponentKind, Tier


@dataclass(frozen=True)
class CatalogEntry:
    """Defaults for one component kind."""

    kind: ComponentKind
    tier: Tier
    availability: float
    latency_ms: float
    critical: bool = True
    managed_redundancy: bool = False
    on_request_path: bool = True
    capacity_rps: float | None = None
    encrypted_default: bool | None = None
    base_monthly: float = 0.0
    per_replica_monthly: float = 0.0
    per_million_requests: float = 0.0
    per_gb_month: float = 0.0
    products: dict[str, str] = field(default_factory=dict)

    def product(self, cloud: Cloud) -> str:
        return self.products.get(cloud) or self.products["any"]


def _p(any_: str, aws: str, azure: str, gcp: str) -> dict[str, str]:
    return {"any": any_, "aws": aws, "azure": azure, "gcp": gcp}


_ENTRIES: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        ComponentKind.CDN,
        "edge",
        0.9999,
        5,
        managed_redundancy=True,
        base_monthly=20,
        per_million_requests=0.9,
        products=_p("CDN", "Amazon CloudFront", "Azure Front Door", "Cloud CDN"),
    ),
    CatalogEntry(
        ComponentKind.WAF,
        "edge",
        0.9999,
        2,
        managed_redundancy=True,
        base_monthly=30,
        per_million_requests=0.6,
        products=_p("Web application firewall", "AWS WAF", "Azure WAF", "Cloud Armor"),
    ),
    CatalogEntry(
        ComponentKind.LOAD_BALANCER,
        "edge",
        0.9999,
        1,
        managed_redundancy=True,
        base_monthly=25,
        per_million_requests=0.4,
        products=_p(
            "Load balancer",
            "Application Load Balancer",
            "Application Gateway",
            "Cloud Load Balancing",
        ),
    ),
    CatalogEntry(
        ComponentKind.API_GATEWAY,
        "edge",
        0.9995,
        5,
        managed_redundancy=True,
        per_million_requests=3.5,
        products=_p("API gateway", "Amazon API Gateway", "API Management", "API Gateway"),
    ),
    CatalogEntry(
        ComponentKind.COMPUTE,
        "app",
        0.995,
        40,
        capacity_rps=150,
        per_replica_monthly=70,
        products=_p("Container service", "ECS on Fargate", "Container Apps", "Cloud Run"),
    ),
    CatalogEntry(
        ComponentKind.SERVERLESS,
        "app",
        0.9995,
        40,
        managed_redundancy=True,
        per_million_requests=2.0,
        products=_p("Function service", "AWS Lambda", "Azure Functions", "Cloud Functions"),
    ),
    CatalogEntry(
        ComponentKind.WORKER,
        "app",
        0.995,
        0,
        critical=False,
        on_request_path=False,
        capacity_rps=60,
        per_replica_monthly=45,
        products=_p(
            "Background worker",
            "ECS on Fargate (worker)",
            "Container Apps (worker)",
            "Cloud Run jobs",
        ),
    ),
    CatalogEntry(
        ComponentKind.DATABASE,
        "data",
        0.995,
        8,
        capacity_rps=300,
        encrypted_default=True,
        per_replica_monthly=180,
        per_gb_month=0.12,
        products=_p(
            "Managed PostgreSQL",
            "Amazon RDS for PostgreSQL",
            "Azure Database for PostgreSQL",
            "Cloud SQL for PostgreSQL",
        ),
    ),
    CatalogEntry(
        ComponentKind.CACHE,
        "data",
        0.995,
        1,
        critical=False,
        capacity_rps=20000,
        encrypted_default=True,
        per_replica_monthly=90,
        products=_p("Managed Redis", "Amazon ElastiCache", "Azure Cache for Redis", "Memorystore"),
    ),
    CatalogEntry(
        ComponentKind.QUEUE,
        "data",
        0.9995,
        3,
        critical=False,
        managed_redundancy=True,
        on_request_path=False,
        encrypted_default=True,
        per_million_requests=0.5,
        products=_p("Message queue", "Amazon SQS", "Azure Service Bus", "Pub/Sub"),
    ),
    CatalogEntry(
        ComponentKind.OBJECT_STORAGE,
        "data",
        0.9999,
        20,
        managed_redundancy=True,
        encrypted_default=True,
        per_gb_month=0.023,
        per_million_requests=0.4,
        products=_p("Object storage", "Amazon S3", "Azure Blob Storage", "Cloud Storage"),
    ),
    CatalogEntry(
        ComponentKind.SEARCH,
        "data",
        0.995,
        20,
        critical=False,
        capacity_rps=400,
        encrypted_default=True,
        per_replica_monthly=220,
        per_gb_month=0.15,
        products=_p(
            "Search cluster", "Amazon OpenSearch Service", "Azure AI Search", "Vertex AI Search"
        ),
    ),
    CatalogEntry(
        ComponentKind.IDENTITY,
        "support",
        0.9995,
        15,
        managed_redundancy=True,
        base_monthly=25,
        products=_p(
            "OIDC identity provider",
            "Amazon Cognito",
            "Microsoft Entra External ID",
            "Identity Platform",
        ),
    ),
    CatalogEntry(
        ComponentKind.SECRETS,
        "support",
        0.9995,
        5,
        critical=False,
        on_request_path=False,
        managed_redundancy=True,
        base_monthly=10,
        products=_p(
            "Secrets manager",
            "AWS Secrets Manager with KMS",
            "Azure Key Vault",
            "Secret Manager with Cloud KMS",
        ),
    ),
    CatalogEntry(
        ComponentKind.OBSERVABILITY,
        "support",
        0.999,
        0,
        critical=False,
        on_request_path=False,
        managed_redundancy=True,
        base_monthly=60,
        products=_p(
            "Metrics, logs and traces",
            "Amazon CloudWatch",
            "Azure Monitor",
            "Cloud Monitoring and Logging",
        ),
    ),
    CatalogEntry(
        ComponentKind.ML_ENDPOINT,
        "app",
        0.995,
        150,
        capacity_rps=20,
        per_replica_monthly=450,
        products=_p(
            "Model serving endpoint",
            "Amazon SageMaker endpoint",
            "Azure Machine Learning endpoint",
            "Vertex AI endpoint",
        ),
    ),
    CatalogEntry(
        ComponentKind.NOTIFICATION,
        "app",
        0.999,
        0,
        critical=False,
        on_request_path=False,
        managed_redundancy=True,
        per_million_requests=1.0,
        products=_p(
            "Notification service",
            "Amazon SNS and SES",
            "Azure Notification Hubs",
            "Firebase Cloud Messaging",
        ),
    ),
    CatalogEntry(
        ComponentKind.DATA_WAREHOUSE,
        "data",
        0.999,
        0,
        critical=False,
        on_request_path=False,
        managed_redundancy=True,
        encrypted_default=True,
        base_monthly=150,
        per_gb_month=0.03,
        products=_p("Data warehouse", "Amazon Redshift", "Azure Synapse", "BigQuery"),
    ),
)

_PRICE_FIELDS = ("base_monthly", "per_replica_monthly", "per_million_requests", "per_gb_month")


class Catalog:
    """Lookup of :class:`CatalogEntry` by kind, with optional price overrides."""

    def __init__(self, entries: tuple[CatalogEntry, ...] = _ENTRIES) -> None:
        self._entries = {e.kind: e for e in entries}

    def __getitem__(self, kind: ComponentKind) -> CatalogEntry:
        return self._entries[kind]

    def __iter__(self) -> Iterator[CatalogEntry]:
        return iter(self._entries.values())

    def kinds(self) -> list[ComponentKind]:
        return list(self._entries)

    def with_overrides(self, overrides: dict[str, dict[str, float]]) -> Catalog:
        """Return a catalogue with prices replaced. Unknown kinds or fields are errors."""
        updated = dict(self._entries)
        for name, values in overrides.items():
            try:
                kind = ComponentKind(name)
            except ValueError as exc:
                raise ConfigurationError(
                    f"pricing file names an unknown component kind {name!r}"
                ) from exc
            bad = sorted(set(values) - set(_PRICE_FIELDS))
            if bad:
                raise ConfigurationError(f"pricing for {name} has unknown fields: {', '.join(bad)}")
            if any(
                not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0
                for v in values.values()
            ):
                raise ConfigurationError(f"pricing for {name} must be non-negative numbers")
            current = updated[kind]
            changes = {k: float(v) for k, v in values.items()}
            updated[kind] = replace(current, **changes)  # type: ignore[arg-type]
        return Catalog(tuple(updated.values()))
