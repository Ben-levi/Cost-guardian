"""
SNS Topic notification channel.
"""
from typing import Dict, Optional, Any
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from cost_guardian.notifications.base import AbstractNotifier, NotificationResult


class SNSNotifier(AbstractNotifier):
    """Send notifications via SNS Topic."""

    def __init__(self, topic_arn: str):
        self.topic_arn = topic_arn
        self._sns = boto3.client("sns")

    def channel_name(self) -> str:
        return "sns"

    def send(
        self,
        subject: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> NotificationResult:
        """
        Publish to SNS topic.
        """
        if metadata is None:
            metadata = {}

        try:
            response = self._sns.publish(
                TopicArn=self.topic_arn,
                Subject=subject,
                Message=message,
            )
            return NotificationResult(
                channel=self.channel_name(),
                success=True,
                metadata={"message_id": response.get("MessageId")},
            )
        except (ClientError, BotoCoreError) as e:
            return NotificationResult(
                channel=self.channel_name(),
                success=False,
                error=str(e),
            )
