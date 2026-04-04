"""
Abstract base class for notification channels.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class NotificationResult:
    """Result of a notification send attempt."""
    channel: str
    success: bool
    error: Optional[str] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class AbstractNotifier(ABC):
    """Base class for all notification channels."""

    @abstractmethod
    def channel_name(self) -> str:
        """Return the channel name (e.g., 'slack', 'sns', 'teams')."""
        pass

    @abstractmethod
    def send(
        self,
        subject: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> NotificationResult:
        """
        Send a notification.

        Args:
            subject: Notification subject/title
            message: Notification message body
            metadata: Additional context (tags, service, etc.)

        Returns:
            NotificationResult with success status and any error info
        """
        pass
