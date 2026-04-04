"""
Abstract base class for enforcement plugins.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class EnforcementResult:
    """Result of an enforcement action."""
    service: str
    matched: int = 0
    stopped: int = 0
    error: Optional[str] = None
    details: dict = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}


class AbstractEnforcer(ABC):
    """Base class for all enforcement plugins."""

    @abstractmethod
    def service_name(self) -> str:
        """Return the service name (e.g., 'ec2', 'rds', 'ecs')."""
        pass

    @abstractmethod
    def enforce(
        self,
        tag_key: str,
        tag_value: str,
        regions: list[str],
        dry_run: bool,
    ) -> EnforcementResult:
        """
        Enforce stop action on matching resources.

        Args:
            tag_key: Tag key to filter resources (e.g., 'CostGuardianManaged')
            tag_value: Tag value to match
            regions: AWS regions to enforce in
            dry_run: If True, report what would be stopped but don't stop

        Returns:
            EnforcementResult with matched/stopped counts and any errors
        """
        pass
