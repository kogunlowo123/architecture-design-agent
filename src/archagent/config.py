"""Configuration: environment settings and file loaders for requirements, designs and pricing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from archagent.errors import ConfigurationError, SpecError
from archagent.models import Design, Requirements

MAX_FILE_BYTES = 1_000_000


class Settings(BaseSettings):
    """Runtime settings from ``ARCHAGENT_*`` environment variables and ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="ARCHAGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    pricing_file: Path | None = None

    llm_provider: Literal["none", "openai", "anthropic"] = "none"
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ARCHAGENT_OPENAI_API_KEY", "OPENAI_API_KEY")
    )
    openai_base_url: str = "https://api.openai.com/v1"
    openai_chat_model: str = "gpt-4o-mini"
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ARCHAGENT_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_model: str = "claude-sonnet-5"
    anthropic_max_tokens: int = Field(default=500, gt=0)

    http_timeout_seconds: float = Field(default=30.0, gt=0)
    retry_attempts: int = Field(default=3, ge=1)
    retry_min_wait: float = Field(default=0.5, ge=0)
    retry_max_wait: float = Field(default=8.0, ge=0)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "WARNING"
    log_json: bool = True

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        if self.retry_max_wait < self.retry_min_wait:
            raise ValueError("retry_max_wait must be >= retry_min_wait")
        return self


def read_mapping(path: Path) -> dict[str, Any]:
    """Read a YAML or JSON file (JSON is valid YAML) that must contain a mapping."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise SpecError(f"{path} is larger than {MAX_FILE_BYTES} bytes")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SpecError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SpecError(f"{path} must contain a mapping at the top level")
    return data


def _validation_message(path: Path, exc: ValidationError) -> str:
    problems = "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or 'value'}: {err['msg']}"
        for err in exc.errors()[:8]
    )
    return f"{path} is not valid: {problems}"


def load_requirements(path: Path) -> Requirements:
    """Load and validate a requirements file."""
    try:
        return Requirements.model_validate(read_mapping(path))
    except ValidationError as exc:
        raise SpecError(_validation_message(path, exc)) from exc


def load_design(path: Path) -> Design:
    """Load and validate a design file (as written by ``archagent design`` or by hand)."""
    try:
        return Design.model_validate(read_mapping(path))
    except ValidationError as exc:
        raise SpecError(_validation_message(path, exc)) from exc


def load_pricing(path: Path | None) -> dict[str, dict[str, float]]:
    """Load price overrides: a mapping of component kind to price fields."""
    if path is None:
        return {}
    try:
        data = read_mapping(path)
    except SpecError as exc:
        raise ConfigurationError(str(exc)) from exc
    prices = data.get("prices", data)
    if not isinstance(prices, dict) or not all(isinstance(v, dict) for v in prices.values()):
        raise ConfigurationError(f"{path} must map component kinds to price fields")
    return prices
