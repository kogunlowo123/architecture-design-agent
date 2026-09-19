"""Shared fixtures and builders."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from archagent.catalog import Catalog
from archagent.config import Settings
from archagent.container import build_service
from archagent.models import Requirements
from archagent.providers.http import JsonClient
from archagent.service import DesignService

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS = REPO_ROOT / "configs" / "specs"


def make_requirements(**overrides: Any) -> Requirements:
    """A small valid requirements object. Nested sections may be given as dicts."""
    data: dict[str, Any] = {"name": "Test System", "capabilities": ["web_ui", "public_api", "auth"]}
    data.update(overrides)
    return Requirements.model_validate(data)


def make_settings(**overrides: object) -> Settings:
    """Settings that ignore the developer's environment."""
    base: dict[str, object] = {
        "retry_min_wait": 0.0,
        "retry_max_wait": 0.0,
        "log_level": "CRITICAL",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def make_service(**overrides: object) -> DesignService:
    return build_service(make_settings(**overrides))


def catalog() -> Catalog:
    return Catalog()


def json_client(
    handler: Callable[[httpx.Request], httpx.Response], attempts: int = 2
) -> JsonClient:
    """A JsonClient backed by an in-process mock transport."""
    return JsonClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        attempts=attempts,
        min_wait=0.0,
        max_wait=0.0,
    )
