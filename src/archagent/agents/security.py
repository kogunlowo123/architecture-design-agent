"""Security agent: STRIDE threats per component, checked against the controls the design contains."""

from __future__ import annotations

from dataclasses import dataclass

from archagent.models import DATA_KINDS, Component, ComponentKind, Design, Requirements, Threat

_STORAGE = {
    ComponentKind.DATABASE,
    ComponentKind.OBJECT_STORAGE,
    ComponentKind.SEARCH,
    ComponentKind.DATA_WAREHOUSE,
}
_ENTRY_KINDS = {
    ComponentKind.WAF,
    ComponentKind.CDN,
    ComponentKind.LOAD_BALANCER,
    ComponentKind.API_GATEWAY,
}
_APP_KINDS = {ComponentKind.COMPUTE, ComponentKind.SERVERLESS, ComponentKind.WORKER}


@dataclass(frozen=True)
class _Template:
    category: str
    description: str
    controls: tuple[str, ...]
    advice: str


_ENTRY = (
    _Template(
        "spoofing",
        "Attackers impersonate users or clients at the public entry.",
        ("authn", "tls"),
        "Require token authentication and TLS on every public route.",
    ),
    _Template(
        "denial_of_service",
        "Floods of requests exhaust capacity at the public entry.",
        ("waf", "rate_limit"),
        "Put a web application firewall and rate limits in front of the application.",
    ),
    _Template(
        "tampering",
        "Requests or responses are altered in transit.",
        ("tls",),
        "Encrypt every connection with TLS.",
    ),
)
_APP = (
    _Template(
        "elevation",
        "A flaw in the service gives an attacker the permissions the service holds.",
        ("secrets_mgmt", "monitoring"),
        "Give the service a least-privilege role, keep secrets in a secrets manager, and alert on anomalies.",
    ),
    _Template(
        "repudiation",
        "Actions cannot be attributed to a user or service afterwards.",
        ("audit_logging",),
        "Write an audit log of security-relevant actions to write-once storage.",
    ),
)
_DATABASE = (
    _Template(
        "disclosure",
        "Stored data is read by someone without authority.",
        ("encryption_at_rest", "network_isolation", "secrets_mgmt"),
        "Encrypt at rest, keep the database off the public network, and manage credentials in a secrets manager.",
    ),
    _Template(
        "tampering",
        "Data is changed or destroyed, or backups are lost.",
        ("backup", "audit_logging"),
        "Keep tested, restorable backups and audit changes to schema and data.",
    ),
)
_BY_KIND: dict[ComponentKind, tuple[_Template, ...]] = {
    ComponentKind.OBJECT_STORAGE: (
        _Template(
            "disclosure",
            "A misconfigured bucket or access policy exposes files.",
            ("encryption_at_rest", "network_isolation"),
            "Block public access, encrypt at rest, and grant access through short-lived credentials.",
        ),
    ),
    ComponentKind.SEARCH: (
        _Template(
            "disclosure",
            "The search index holds a copy of sensitive fields.",
            ("encryption_at_rest", "network_isolation"),
            "Index only what search needs, encrypt at rest, and keep it private.",
        ),
    ),
    ComponentKind.CACHE: (
        _Template(
            "disclosure",
            "The cache holds copies of sensitive data without the database's protections.",
            ("encryption_at_rest", "network_isolation"),
            "Encrypt the cache, keep it private and set short lifetimes for sensitive entries.",
        ),
    ),
    ComponentKind.DATA_WAREHOUSE: (
        _Template(
            "disclosure",
            "Analysts or jobs read more data than they need.",
            ("encryption_at_rest", "audit_logging"),
            "Encrypt at rest, mask sensitive columns and audit queries.",
        ),
    ),
    ComponentKind.QUEUE: (
        _Template(
            "tampering",
            "Messages are forged or replayed.",
            ("tls", "authn"),
            "Authenticate producers and consumers and make handlers idempotent.",
        ),
        _Template(
            "denial_of_service",
            "A poison message blocks processing.",
            ("dead_letter",),
            "Configure a dead-letter queue and a retry limit.",
        ),
    ),
    ComponentKind.IDENTITY: (
        _Template(
            "spoofing",
            "Credential stuffing and phishing take over accounts.",
            ("rate_limit", "monitoring"),
            "Require multi-factor authentication, throttle sign-in attempts and alert on unusual sign-ins.",
        ),
    ),
    ComponentKind.ML_ENDPOINT: (
        _Template(
            "tampering",
            "Crafted inputs manipulate or extract from the model.",
            ("rate_limit", "monitoring"),
            "Validate inputs, rate-limit callers and monitor output for abuse.",
        ),
    ),
}


def controls_present(design: Design) -> set[str]:
    """Which named controls the design contains. Each rule is explicit and testable."""
    kinds = {c.kind for c in design.components}
    present: set[str] = set()
    if ComponentKind.WAF in kinds:
        present.add("waf")
    if kinds & {ComponentKind.WAF, ComponentKind.API_GATEWAY, ComponentKind.CDN}:
        present.add("rate_limit")
    if ComponentKind.IDENTITY in kinds or any(c.authn for c in design.components):
        present.add("authn")
    if design.connections and all(edge.encrypted for edge in design.connections):
        present.add("tls")
    storage = [c for c in design.components if c.kind in _STORAGE]
    if storage and all(c.encrypted_at_rest is True for c in storage):
        present.add("encryption_at_rest")
    if ComponentKind.SECRETS in kinds:
        present.add("secrets_mgmt")
    if any(c.audit_logging for c in design.components):
        present.add("audit_logging")
    if not any(c.public for c in design.components if c.kind in DATA_KINDS):
        present.add("network_isolation")
    databases = [c for c in design.components if c.kind is ComponentKind.DATABASE]
    if databases and all(
        c.backup_interval_minutes is not None or c.replication != "none" for c in databases
    ):
        present.add("backup")
    if ComponentKind.OBSERVABILITY in kinds:
        present.add("monitoring")
    queues = [c for c in design.components if c.kind is ComponentKind.QUEUE]
    if queues and all(c.dead_letter is True for c in queues):
        present.add("dead_letter")
    return present


def _templates(component: Component, req: Requirements | None) -> tuple[_Template, ...]:
    templates: list[_Template] = []
    if component.kind in _ENTRY_KINDS or (component.public and component.kind in _APP_KINDS):
        templates.extend(_ENTRY)
    if component.kind in _APP_KINDS:
        if req is None or req.data.classification != "public":
            templates.extend(_APP)
        else:
            templates.append(_APP[0])
    if component.kind is ComponentKind.DATABASE:
        templates.extend(_DATABASE)
    templates.extend(_BY_KIND.get(component.kind, ()))
    return tuple(templates)


class SecurityAgent:
    """Enumerates STRIDE threats and marks each mitigated, partial or open."""

    def analyze(self, design: Design, req: Requirements | None = None) -> list[Threat]:
        present = controls_present(design)
        threats: list[Threat] = []
        for component in design.components:
            for template in _templates(component, req):
                have = [c for c in template.controls if c in present]
                missing = [c for c in template.controls if c not in present]
                status = "mitigated" if not missing else "partial" if have else "open"
                threats.append(
                    Threat(
                        id=f"T-{len(threats) + 1:03d}",
                        component=component.id,
                        category=template.category,
                        description=template.description,
                        required_controls=list(template.controls),
                        present_controls=have,
                        status=status,
                        recommendation=template.advice
                        if missing
                        else "Controls in place. Keep them tested.",
                    )
                )
        return threats
