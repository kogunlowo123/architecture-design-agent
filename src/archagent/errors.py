"""Exception hierarchy for archagent."""

from __future__ import annotations


class ArchagentError(Exception):
    """Base class for errors raised deliberately by this package."""


class ConfigurationError(ArchagentError):
    """Settings or a configuration file are missing or invalid."""


class SpecError(ArchagentError):
    """A requirements or design file cannot be read or fails validation."""


class RenderError(ArchagentError):
    """Output could not be rendered or written."""


class ProviderError(ArchagentError):
    """An external model provider returned an error or an unusable response."""


class TransientProviderError(ProviderError):
    """A provider failure worth retrying (timeouts, rate limits, 5xx)."""
