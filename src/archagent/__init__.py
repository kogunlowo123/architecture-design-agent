"""Architecture design agent: requirements in, a reviewed design with ADRs out."""

from archagent._version import __version__
from archagent.config import Settings
from archagent.container import build_service
from archagent.models import Design, DesignReport, Requirements
from archagent.service import DesignService

__all__ = [
    "Design",
    "DesignReport",
    "DesignService",
    "Requirements",
    "Settings",
    "__version__",
    "build_service",
]
