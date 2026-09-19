"""Component agent: turns requirements and a chosen style into a sized component graph."""

from __future__ import annotations

import math

from archagent.catalog import Catalog
from archagent.models import (
    CLASSIFICATION_RANK,
    Component,
    ComponentKind,
    Connection,
    Design,
    Requirements,
    StyleName,
)

HEADROOM = 1.5
CACHE_HIT_RATIO = 0.8
JOB_SHARE = 0.3
SECONDS_PER_MONTH = 2_592_000
AVERAGE_TO_PEAK = 0.3

_SERVICE_NAMES = ("Domain service 1", "Domain service 2", "Domain service 3", "Domain service 4")


def monthly_requests(peak_rps: float) -> float:
    """Requests per month assuming the average load is 30 percent of peak."""
    return peak_rps * AVERAGE_TO_PEAK * SECONDS_PER_MONTH


class ComponentAgent:
    """Selects and sizes components. Every number comes from a named rule in this module."""

    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog

    # -- entry point -------------------------------------------------------------------------------

    def build(self, req: Requirements, style: StyleName) -> Design:
        self._req = req
        self._components: list[Component] = []
        self._edges: list[Connection] = []
        n, d = req.nfr, req.data
        self._ha = n.availability_pct >= 99.9
        self._zones = 3 if n.availability_pct >= 99.99 else 2 if self._ha else 1
        self._min_replicas = 2 if self._ha else 1
        self._class = d.classification
        self._regulated = bool(d.regulations)
        self._identity = "auth" in req.capabilities or "payments" in req.capabilities
        self._public = bool({"web_ui", "public_api", "payments"} & set(req.capabilities))

        builders = {
            "modular_monolith": self._monolith,
            "layered_managed": self._layered,
            "microservices": self._microservices,
            "event_driven": self._event_driven,
            "serverless": self._serverless,
        }
        entry, app_ids, db_ids = builders[style]()
        self._supporting(app_ids, db_ids, style)
        self._edge_layer(entry, style)
        self._notes(style)
        return Design(
            name=req.name,
            style=style,
            cloud=req.constraints.cloud,
            regions=n.regions,
            components=self._components,
            connections=self._edges,
        )

    # -- helpers ---------------------------------------------------------------------------------------

    def _add(
        self,
        kind: ComponentKind,
        cid: str,
        name: str | None = None,
        *,
        replicas: int = 1,
        zones: int = 1,
        **fields: object,
    ) -> Component:
        entry = self._catalog[kind]
        data: dict[str, object] = {
            "id": cid,
            "kind": kind,
            "name": name or entry.product("any"),
            "product": entry.product(self._req.constraints.cloud),
            "tier": entry.tier,
            "replicas": replicas,
            "zones": zones,
            "availability": entry.availability,
            "latency_ms": entry.latency_ms,
            "critical": entry.critical,
            "managed_redundancy": entry.managed_redundancy,
            "on_request_path": entry.on_request_path,
            "encrypted_at_rest": entry.encrypted_default,
        }
        data.update(fields)
        component = Component.model_validate(data)
        self._components.append(component)
        return component

    def _link(
        self, source: str, target: str, protocol: str = "https", *, data: bool = False
    ) -> None:
        classification = self._class if data else "internal"
        self._edges.append(
            Connection(
                source=source,
                target=target,
                protocol=protocol,
                encrypted=True,
                classification=classification,
            )
        )

    def _app_replicas(self, share: float = 1.0) -> tuple[int, int]:
        capacity = self._catalog[ComponentKind.COMPUTE].capacity_rps or 1.0
        needed = math.ceil(self._req.workload.peak_rps * share * HEADROOM / capacity)
        replicas = max(self._min_replicas, needed, self._zones if self._ha else 1)
        return replicas, min(self._zones, replicas)

    def _database(self, cid: str, name: str, *, cache: bool) -> Component:
        w, n = self._req.workload, self._req.nfr
        capacity = self._catalog[ComponentKind.DATABASE].capacity_rps or 1.0
        read_rps = w.peak_rps * w.read_ratio * ((1 - CACHE_HIT_RATIO) if cache else 1.0)
        readers = max(0, math.ceil(read_rps / capacity) - 1)
        base = 2 if self._ha else 1
        replicas = base + readers
        write_rps = w.peak_rps * (1 - w.read_ratio)
        notes = [
            f"write load about {write_rps:.0f} rps against a single-primary capacity of {capacity:.0f} rps"
        ]
        if readers:
            notes.append(f"{readers} read replica(s) sized for {read_rps:.0f} read rps")
        return self._add(
            ComponentKind.DATABASE,
            cid,
            name,
            replicas=replicas,
            zones=min(self._zones, replicas),
            backup_interval_minutes=max(5, min(n.rpo_minutes, 1440)) if n.rpo_minutes else 5,
            replication="sync" if self._ha else "none",
            audit_logging=self._regulated,
            notes="; ".join(notes),
        )

    # -- styles ---------------------------------------------------------------------------------------------

    def _wants_cache(self) -> bool:
        w, n = self._req.workload, self._req.nfr
        return (
            (w.read_ratio >= 0.7 and w.peak_rps >= 100)
            or n.p95_latency_ms <= 150
            or "realtime" in self._req.capabilities
        )

    def _monolith(self) -> tuple[str, list[str], list[str]]:
        replicas, zones = self._app_replicas()
        self._add(
            ComponentKind.COMPUTE,
            "app",
            "Application (modular monolith)",
            replicas=replicas,
            zones=zones,
            authn=self._identity,
            audit_logging=self._regulated,
        )
        self._add_shared_data("app")
        return "app", ["app"], ["db"]

    def _layered(self) -> tuple[str, list[str], list[str]]:
        caps = self._req.capabilities
        ids: list[str] = []
        api_replicas, api_zones = self._app_replicas()
        if "web_ui" in caps:
            web_replicas, web_zones = self._app_replicas(0.5)
            self._add(
                ComponentKind.COMPUTE,
                "web",
                "Web tier",
                replicas=web_replicas,
                zones=web_zones,
                authn=self._identity,
            )
            ids.append("web")
        self._add(
            ComponentKind.COMPUTE,
            "api",
            "API tier",
            replicas=api_replicas,
            zones=api_zones,
            authn=self._identity,
            audit_logging=self._regulated,
        )
        ids.append("api")
        if "web_ui" in caps:
            self._link("web", "api")
        self._add_shared_data("api")
        return ids[0], ids, ["db"]

    def _microservices(self) -> tuple[str, list[str], list[str]]:
        count = max(2, min(4, len(self._req.capabilities) // 2 + 1))
        self._add(
            ComponentKind.API_GATEWAY, "gateway", "API gateway", public=True, authn=self._identity
        )
        ids: list[str] = []
        dbs: list[str] = []
        cache_on = self._wants_cache()
        if cache_on:
            self._add_cache()
        for index in range(count):
            cid = f"svc{index + 1}"
            replicas, zones = self._app_replicas(1 / count)
            self._add(
                ComponentKind.COMPUTE,
                cid,
                _SERVICE_NAMES[index],
                replicas=replicas,
                zones=zones,
                authn=self._identity,
                audit_logging=self._regulated,
            )
            db = self._database(
                f"db{index + 1}", f"{_SERVICE_NAMES[index]} database", cache=cache_on
            )
            self._link("gateway", cid)
            self._link(cid, db.id, "postgres+tls", data=True)
            if cache_on:
                self._link(cid, "cache", "redis+tls", data=True)
            ids.append(cid)
            dbs.append(db.id)
        return "gateway", ids, dbs

    def _event_driven(self) -> tuple[str, list[str], list[str]]:
        replicas, zones = self._app_replicas()
        self._add(
            ComponentKind.API_GATEWAY, "gateway", "API gateway", public=True, authn=self._identity
        )
        self._add(
            ComponentKind.COMPUTE,
            "api",
            "API service",
            replicas=replicas,
            zones=zones,
            authn=self._identity,
            audit_logging=self._regulated,
        )
        self._link("gateway", "api")
        self._add_shared_data("api")
        self._add_queue_and_workers("api", "db", use_serverless=False)
        return "gateway", ["api"], ["db"]

    def _serverless(self) -> tuple[str, list[str], list[str]]:
        self._add(
            ComponentKind.API_GATEWAY, "gateway", "API gateway", public=True, authn=self._identity
        )
        self._add(
            ComponentKind.SERVERLESS,
            "functions",
            "Request functions",
            authn=self._identity,
            audit_logging=self._regulated,
        )
        self._link("gateway", "functions")
        self._add_shared_data("functions")
        if "async_jobs" in self._req.capabilities or "notifications" in self._req.capabilities:
            self._add_queue_and_workers("functions", "db", use_serverless=True)
        return "gateway", ["functions"], ["db"]

    def _add_shared_data(self, app_id: str) -> None:
        cache_on = self._wants_cache()
        self._database("db", "Primary database", cache=cache_on)
        self._link(app_id, "db", "postgres+tls", data=True)
        if cache_on:
            self._add_cache()
            self._link(app_id, "cache", "redis+tls", data=True)

    def _add_cache(self) -> None:
        self._add(
            ComponentKind.CACHE,
            "cache",
            "Cache",
            replicas=self._min_replicas,
            zones=min(self._zones, self._min_replicas),
            critical=False,
        )

    def _add_queue_and_workers(self, producer: str, db_id: str, *, use_serverless: bool) -> None:
        w = self._req.workload
        self._add(ComponentKind.QUEUE, "queue", "Job queue", dead_letter=True)
        self._link(producer, "queue", "https", data=True)
        if use_serverless:
            self._add(
                ComponentKind.SERVERLESS,
                "workers",
                "Async handlers",
                on_request_path=False,
                critical=False,
                audit_logging=self._regulated,
            )
        else:
            capacity = self._catalog[ComponentKind.WORKER].capacity_rps or 1.0
            needed = math.ceil(w.peak_rps * JOB_SHARE / capacity)
            replicas = max(self._min_replicas, needed)
            self._add(
                ComponentKind.WORKER,
                "workers",
                "Background workers",
                replicas=replicas,
                zones=min(self._zones, replicas),
                audit_logging=self._regulated,
            )
        self._link("queue", "workers", "https", data=True)
        self._link("workers", db_id, "postgres+tls", data=True)

    # -- shared services ---------------------------------------------------------------------------------------

    def _supporting(self, app_ids: list[str], db_ids: list[str], style: StyleName) -> None:
        caps = set(self._req.capabilities)
        primary = app_ids[-1]
        has_queue = any(c.kind is ComponentKind.QUEUE for c in self._components)
        if ("async_jobs" in caps or "notifications" in caps) and not has_queue:
            self._add_queue_and_workers(primary, db_ids[0], use_serverless=False)
        if "file_storage" in caps:
            self._add(ComponentKind.OBJECT_STORAGE, "storage", "File storage")
            for app in app_ids:
                self._link(app, "storage", "https", data=True)
        if "search" in caps:
            replicas = self._min_replicas
            self._add(
                ComponentKind.SEARCH,
                "search",
                "Search index",
                replicas=replicas,
                zones=min(self._zones, replicas),
            )
            for app in app_ids:
                self._link(app, "search", "https", data=True)
        if "ml_inference" in caps:
            capacity = self._catalog[ComponentKind.ML_ENDPOINT].capacity_rps or 1.0
            needed = math.ceil(self._req.workload.peak_rps * 0.2 * HEADROOM / capacity)
            replicas = max(self._min_replicas, needed)
            self._add(
                ComponentKind.ML_ENDPOINT,
                "ml",
                "Model endpoint",
                replicas=replicas,
                zones=min(self._zones, replicas),
            )
            for app in app_ids:
                self._link(app, "ml", "https")
        if "notifications" in caps:
            self._add(ComponentKind.NOTIFICATION, "notify", "Notifications")
            has_workers = any(c.id == "workers" for c in self._components)
            self._link("workers" if has_workers else primary, "notify", "https")
        if "analytics" in caps:
            self._add(ComponentKind.DATA_WAREHOUSE, "warehouse", "Analytics warehouse")
            self._link(db_ids[0], "warehouse", "https", data=True)
        if self._identity:
            self._add(ComponentKind.IDENTITY, "identity", "Identity provider", public=self._public)
            for app in app_ids:
                self._link(app, "identity", "https")
        self._add(ComponentKind.SECRETS, "secrets", "Secrets and keys")
        for app in app_ids:
            self._link(app, "secrets", "https")
        self._add(ComponentKind.OBSERVABILITY, "observability", "Observability", audit_logging=True)
        for app in app_ids:
            self._link(app, "observability", "https")

    def _edge_layer(self, entry: str, style: StyleName) -> None:
        caps = set(self._req.capabilities)
        if not self._public:
            return
        w, n = self._req.workload, self._req.nfr
        first = entry
        needs_lb = style in {"modular_monolith", "layered_managed"}
        if needs_lb:
            self._add(ComponentKind.LOAD_BALANCER, "lb", "Load balancer", public=True)
            self._link("lb", entry)
            first = "lb"
        self._add(ComponentKind.WAF, "waf", "Web application firewall", public=True)
        self._link("waf", first)
        if "web_ui" in caps and (w.users >= 10_000 or n.p95_latency_ms <= 200):
            self._add(
                ComponentKind.CDN,
                "cdn",
                "Content delivery network",
                public=True,
                on_request_path=True,
            )
            self._link("cdn", "waf")

    def _notes(self, style: StyleName) -> None:
        caps = set(self._req.capabilities)
        if "realtime" in caps:
            gateway = next(
                (
                    c
                    for c in self._components
                    if c.kind in (ComponentKind.API_GATEWAY, ComponentKind.LOAD_BALANCER)
                ),
                None,
            )
            target = gateway or self._components[0]
            target.notes = (
                target.notes + "; " if target.notes else ""
            ) + "must support long-lived WebSocket or streaming connections"
        if "third_party_integrations" in caps:
            app = [
                c
                for c in self._components
                if c.kind in (ComponentKind.COMPUTE, ComponentKind.SERVERLESS)
            ][-1]
            app.notes = (
                app.notes + "; " if app.notes else ""
            ) + "outbound calls to third parties need an egress allow-list and timeouts"
        if CLASSIFICATION_RANK[self._class] >= 2:
            for c in self._components:
                if c.kind is ComponentKind.DATABASE:
                    c.notes = (
                        c.notes + "; " if c.notes else ""
                    ) + "encrypt with customer-managed keys"
