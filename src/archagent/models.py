"""Domain models: requirements, design, findings, threats, costs and the final report."""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Capability = Literal[
    "web_ui",
    "public_api",
    "async_jobs",
    "realtime",
    "search",
    "file_storage",
    "analytics",
    "ml_inference",
    "notifications",
    "auth",
    "payments",
    "third_party_integrations",
]
Classification = Literal["public", "internal", "confidential", "restricted"]
Regulation = Literal["gdpr", "hipaa", "pci_dss", "soc2"]
Cloud = Literal["aws", "azure", "gcp", "any"]
StyleName = Literal[
    "modular_monolith", "layered_managed", "microservices", "event_driven", "serverless"
]
Tier = Literal["edge", "app", "data", "support"]

CLASSIFICATION_RANK: dict[str, int] = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}


class ComponentKind(str, Enum):
    CDN = "cdn"
    WAF = "waf"
    LOAD_BALANCER = "load_balancer"
    API_GATEWAY = "api_gateway"
    COMPUTE = "compute"
    SERVERLESS = "serverless"
    WORKER = "worker"
    DATABASE = "database"
    CACHE = "cache"
    QUEUE = "queue"
    OBJECT_STORAGE = "object_storage"
    SEARCH = "search"
    IDENTITY = "identity"
    SECRETS = "secrets"
    OBSERVABILITY = "observability"
    ML_ENDPOINT = "ml_endpoint"
    NOTIFICATION = "notification"
    DATA_WAREHOUSE = "data_warehouse"


DATA_KINDS = frozenset(
    {
        ComponentKind.DATABASE,
        ComponentKind.OBJECT_STORAGE,
        ComponentKind.SEARCH,
        ComponentKind.DATA_WAREHOUSE,
        ComponentKind.CACHE,
        ComponentKind.QUEUE,
    }
)


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return ["info", "low", "medium", "high", "critical"].index(self.value)


class Workload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: int = Field(default=1000, ge=1)
    peak_rps: float = Field(default=50.0, gt=0)
    avg_payload_kb: float = Field(default=10.0, gt=0)
    read_ratio: float = Field(default=0.8, ge=0, le=1)
    data_gb: float = Field(default=10.0, ge=0)
    growth_per_year: float = Field(default=0.5, ge=0)


class Nfr(BaseModel):
    model_config = ConfigDict(extra="forbid")

    availability_pct: float = Field(default=99.9, ge=90, le=99.999)
    p95_latency_ms: int = Field(default=500, ge=1)
    rpo_minutes: int = Field(default=60, ge=0)
    rto_minutes: int = Field(default=240, ge=1)
    regions: int = Field(default=1, ge=1, le=3)


class DataProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: Classification = "internal"
    pii: bool = False
    regulations: list[Regulation] = Field(default_factory=list)
    residency: list[str] = Field(default_factory=list)


class Constraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cloud: Cloud = "any"
    team_size: int = Field(default=5, ge=1)
    monthly_budget_usd: float | None = Field(default=None, gt=0)
    avoid: list[str] = Field(default_factory=list)


class Requirements(BaseModel):
    """What the system must do and how well. Everything the design is derived from."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    summary: str = Field(default="", max_length=2000)
    capabilities: list[Capability] = Field(min_length=1)
    workload: Workload = Field(default_factory=Workload)
    nfr: Nfr = Field(default_factory=Nfr)
    data: DataProfile = Field(default_factory=DataProfile)
    constraints: Constraints = Field(default_factory=Constraints)

    @field_validator("name")
    @classmethod
    def _printable(cls, value: str) -> str:
        value = value.strip()
        if not value or not re.fullmatch(r"[\w .,'()/&+-]+", value):
            raise ValueError("name may contain letters, digits, spaces and . , ' ( ) / & + -")
        return value

    @model_validator(mode="after")
    def _dedupe(self) -> Requirements:
        self.capabilities = list(dict.fromkeys(self.capabilities))
        self.data.regulations = list(dict.fromkeys(self.data.regulations))
        return self


class Driver(BaseModel):
    """An architectural driver derived from the requirements, with the reason it matters."""

    name: str
    weight: int = Field(ge=1, le=3)
    reason: str


class StyleScore(BaseModel):
    style: StyleName
    score: float
    disqualified: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)


class Component(BaseModel):
    """One deployable or managed building block."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,40}$")
    kind: ComponentKind
    name: str
    product: str = ""
    tier: Tier = "app"
    replicas: int = Field(default=1, ge=1)
    zones: int = Field(default=1, ge=1, le=3)
    availability: float = Field(default=0.999, gt=0, lt=1)
    latency_ms: float = Field(default=0.0, ge=0)
    monthly_cost_usd: float = Field(default=0.0, ge=0)
    public: bool = False
    authn: bool = False
    encrypted_at_rest: bool | None = None
    on_request_path: bool = True
    critical: bool = True
    managed_redundancy: bool = False
    backup_interval_minutes: int | None = Field(default=None, ge=1)
    replication: Literal["none", "async", "sync"] = "none"
    dead_letter: bool | None = None
    audit_logging: bool = False
    notes: str = ""


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    protocol: str = "https"
    encrypted: bool = True
    classification: Classification = "internal"


class Design(BaseModel):
    """A component graph. Produced by the agents, or hand-written for review."""

    model_config = ConfigDict(extra="forbid")

    name: str
    style: StyleName
    cloud: Cloud = "any"
    regions: int = Field(default=1, ge=1, le=3)
    components: list[Component]
    connections: list[Connection] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Design:
        ids = [c.id for c in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("component ids must be unique")
        known = set(ids)
        for edge in self.connections:
            if edge.source not in known or edge.target not in known:
                raise ValueError(
                    f"connection {edge.source} -> {edge.target} references an unknown component"
                )
        return self

    def component(self, component_id: str) -> Component:
        for c in self.components:
            if c.id == component_id:
                return c
        raise KeyError(component_id)

    def of_kind(self, *kinds: ComponentKind) -> list[Component]:
        return [c for c in self.components if c.kind in kinds]


class AvailabilityLine(BaseModel):
    component: str
    kind: str
    single: float
    replicas: int
    zones: int
    effective: float


class AvailabilityReport(BaseModel):
    target_pct: float
    stack_pct: float
    achieved_pct: float
    downtime_minutes_per_month: float
    meets_target: bool
    lines: list[AvailabilityLine]
    single_points_of_failure: list[str]
    weakest: str
    assumptions: list[str]


class Threat(BaseModel):
    id: str
    component: str
    category: Literal[
        "spoofing", "tampering", "repudiation", "disclosure", "denial_of_service", "elevation"
    ]
    description: str
    required_controls: list[str]
    present_controls: list[str]
    status: Literal["mitigated", "partial", "open"]
    recommendation: str


class CostLine(BaseModel):
    item: str
    monthly_usd: float
    basis: str


class CostReport(BaseModel):
    lines: list[CostLine]
    total_monthly_usd: float
    budget_usd: float | None
    within_budget: bool | None
    levers: list[str]
    disclaimer: str


class Finding(BaseModel):
    rule: str
    severity: Severity
    title: str
    detail: str
    recommendation: str
    components: list[str] = Field(default_factory=list)


class Adr(BaseModel):
    number: int
    title: str
    status: str = "Proposed"
    context: str
    decision: str
    consequences: list[str]
    alternatives: list[str] = Field(default_factory=list)


class DesignReport(BaseModel):
    """Everything produced for one requirements file."""

    requirements: Requirements
    drivers: list[Driver]
    styles: list[StyleScore]
    considered: list[str] = Field(default_factory=list)
    design: Design
    availability: AvailabilityReport
    threats: list[Threat]
    cost: CostReport
    findings: list[Finding]
    adrs: list[Adr]
    open_questions: list[str]
    summary: str = ""


class ReviewResult(BaseModel):
    """Outcome of reviewing an existing design."""

    design: Design
    availability: AvailabilityReport
    threats: list[Threat]
    cost: CostReport | None
    findings: list[Finding]
