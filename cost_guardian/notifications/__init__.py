"""
Notification dispatcher: coordinates multiple notification channels.
"""
from typing import Dict, List, Optional
from .base import AbstractNotifier, NotificationResult
from .sns_notifier import SNSNotifier
from ..config import NotificationConfig


class NotificationDispatcher:
    """Loads and orchestrates multiple notification channels."""

    def __init__(self, config: NotificationConfig):
        self.config = config
        self._notifiers: Dict[str, AbstractNotifier] = {}
        self._initialize_notifiers()

    def _initialize_notifiers(self) -> None:
        """Initialize enabled notification channels."""
        # SNS is always available if configured
        # (The topic ARN comes from handler, but we can't import it here to avoid circular dep)
        # So SNS notifier is added separately in handler when needed

        # TODO: Initialize Slack, Teams, Discord, PagerDuty notifiers based on config

    def register_notifier(self, notifier: AbstractNotifier) -> None:
        """Register a notification channel."""
        self._notifiers[notifier.channel_name()] = notifier

    def get_notifier(self, channel: str) -> Optional[AbstractNotifier]:
        """Get a notifier by channel name."""
        return self._notifiers.get(channel)

    def send_all(
        self,
        subject: str,
        message: str,
        metadata: Optional[Dict] = None,
    ) -> Dict[str, NotificationResult]:
        """
        Send notification to all enabled channels.

        Returns:
            Dict mapping channel name to NotificationResult
        """
        results = {}

        for channel in self.config.channels:
            notifier = self.get_notifier(channel)
            if not notifier:
                # Unknown channel, skip silently (log in handler)
                continue

            try:
                result = notifier.send(subject, message, metadata)
                results[channel] = result
            except Exception as e:
                results[channel] = NotificationResult(
                    channel=channel,
                    success=False,
                    error=str(e),
                )

        return results


__all__ = [
    "AbstractNotifier",
    "NotificationResult",
    "NotificationDispatcher",
]
