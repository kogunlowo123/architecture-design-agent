"""Composition root: builds a :class:`DesignService` from :class:`Settings`."""

from __future__ import annotations

import httpx

from archagent.agents.summary import LLMSummaryWriter, SummaryWriter, TemplateSummaryWriter
from archagent.catalog import Catalog
from archagent.config import Settings, load_pricing
from archagent.errors import ConfigurationError
from archagent.providers import AnthropicChatClient, JsonClient, LLMClient, OpenAIChatClient
from archagent.service import DesignService


def _summary_writer(settings: Settings, http_client: httpx.Client | None) -> SummaryWriter:
    if settings.llm_provider == "none":
        return TemplateSummaryWriter()
    client = JsonClient(
        http_client or httpx.Client(timeout=settings.http_timeout_seconds),
        attempts=settings.retry_attempts,
        min_wait=settings.retry_min_wait,
        max_wait=settings.retry_max_wait,
    )
    llm: LLMClient
    if settings.llm_provider == "openai":
        if settings.openai_api_key is None:
            raise ConfigurationError(
                "ARCHAGENT_OPENAI_API_KEY must be set when llm_provider=openai"
            )
        llm = OpenAIChatClient(
            client,
            api_key=settings.openai_api_key,
            model=settings.openai_chat_model,
            base_url=settings.openai_base_url,
        )
    else:
        if settings.anthropic_api_key is None:
            raise ConfigurationError(
                "ARCHAGENT_ANTHROPIC_API_KEY must be set when llm_provider=anthropic"
            )
        llm = AnthropicChatClient(
            client,
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            max_tokens=settings.anthropic_max_tokens,
            base_url=settings.anthropic_base_url,
        )
    return LLMSummaryWriter(llm)


def build_service(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    summary_writer: SummaryWriter | None = None,
) -> DesignService:
    """Assemble the dependency graph.

    Raises:
        ConfigurationError: If the pricing file or provider credentials are missing or invalid.
    """
    catalog = Catalog().with_overrides(load_pricing(settings.pricing_file))
    return DesignService(catalog, summary_writer or _summary_writer(settings, http_client))
