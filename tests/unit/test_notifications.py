"""
Unit tests for cost_guardian.notifications module.
"""
import unittest
from unittest.mock import patch, MagicMock, call
from botocore.exceptions import ClientError

from cost_guardian.notifications import NotificationDispatcher
from cost_guardian.notifications.base import AbstractNotifier, NotificationResult
from cost_guardian.notifications.sns_notifier import SNSNotifier
from cost_guardian.config import NotificationConfig


class TestNotificationResult(unittest.TestCase):
    """Test NotificationResult dataclass."""

    def test_notification_result_success(self):
        result = NotificationResult(
            channel="sns",
            success=True,
            metadata={"message_id": "12345"},
        )
        self.assertEqual(result.channel, "sns")
        self.assertTrue(result.success)
        self.assertIsNone(result.error)
        self.assertEqual(result.metadata, {"message_id": "12345"})

    def test_notification_result_failure(self):
        result = NotificationResult(
            channel="slack",
            success=False,
            error="Connection timeout",
        )
        self.assertEqual(result.channel, "slack")
        self.assertFalse(result.success)
        self.assertEqual(result.error, "Connection timeout")


class TestAbstractNotifier(unittest.TestCase):
    """Test AbstractNotifier base class."""

    def test_cannot_instantiate_abstract(self):
        """Test that AbstractNotifier cannot be instantiated."""
        with self.assertRaises(TypeError):
            AbstractNotifier()

    def test_concrete_implementation(self):
        """Test that concrete implementation works."""
        class ConcreteNotifier(AbstractNotifier):
            def channel_name(self):
                return "test"

            def send(self, subject, message, metadata=None):
                return NotificationResult(
                    channel=self.channel_name(),
                    success=True,
                )

        notifier = ConcreteNotifier()
        self.assertEqual(notifier.channel_name(), "test")
        result = notifier.send("test", "message")
        self.assertTrue(result.success)


class TestSNSNotifier(unittest.TestCase):
    """Test SNSNotifier."""

    def test_sns_notifier_channel_name(self):
        notifier = SNSNotifier("arn:aws:sns:us-east-1:123:topic")
        self.assertEqual(notifier.channel_name(), "sns")

    @patch("boto3.client")
    def test_sns_notifier_send_success(self, mock_boto3_client):
        """Test successful SNS notification."""
        mock_sns = MagicMock()
        mock_boto3_client.return_value = mock_sns

        mock_sns.publish.return_value = {
            "MessageId": "abc-123",
        }

        notifier = SNSNotifier("arn:aws:sns:us-east-1:123:topic")
        result = notifier.send(
            subject="Test Alert",
            message="This is a test message",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.channel, "sns")
        self.assertEqual(result.metadata["message_id"], "abc-123")
        mock_sns.publish.assert_called_once()

    @patch("boto3.client")
    def test_sns_notifier_send_failure(self, mock_boto3_client):
        """Test failed SNS notification."""
        mock_sns = MagicMock()
        mock_boto3_client.return_value = mock_sns

        mock_sns.publish.side_effect = ClientError(
            error_response={"Error": {"Code": "TopicNotFound"}},
            operation_name="Publish",
        )

        notifier = SNSNotifier("arn:aws:sns:us-east-1:123:invalid-topic")
        result = notifier.send(
            subject="Test Alert",
            message="This is a test message",
        )

        self.assertFalse(result.success)
        self.assertIn("TopicNotFound", result.error)

    @patch("boto3.client")
    def test_sns_notifier_send_with_metadata(self, mock_boto3_client):
        """Test SNS notifier with metadata."""
        mock_sns = MagicMock()
        mock_boto3_client.return_value = mock_sns
        mock_sns.publish.return_value = {"MessageId": "xyz"}

        notifier = SNSNotifier("arn:aws:sns:us-east-1:123:topic")
        result = notifier.send(
            subject="Alert",
            message="Message",
            metadata={"cost": 100.0, "threshold": 50.0},
        )

        self.assertTrue(result.success)


class TestNotificationDispatcher(unittest.TestCase):
    """Test NotificationDispatcher."""

    @patch.dict("os.environ", {"NOTIFICATION_CHANNELS": "sns"}, clear=True)
    def setUp(self):
        from cost_guardian.config import NotificationConfig
        config = NotificationConfig.from_env()
        self.dispatcher = NotificationDispatcher(config)

    def test_dispatcher_register_notifier(self):
        """Test registering a notifier."""
        class TestNotifier(AbstractNotifier):
            def channel_name(self):
                return "test"

            def send(self, subject, message, metadata=None):
                return NotificationResult(channel="test", success=True)

        notifier = TestNotifier()
        self.dispatcher.register_notifier(notifier)
        self.assertEqual(self.dispatcher.get_notifier("test"), notifier)

    def test_dispatcher_get_nonexistent_notifier(self):
        """Test getting a notifier that doesn't exist."""
        notifier = self.dispatcher.get_notifier("nonexistent")
        self.assertIsNone(notifier)

    def test_dispatcher_send_all_empty(self):
        """Test send_all with no registered notifiers."""
        results = self.dispatcher.send_all(
            subject="Test",
            message="Message",
        )
        self.assertEqual(len(results), 0)

    def test_dispatcher_send_all_single_channel(self):
        """Test send_all with a single notifier."""
        class TestNotifier(AbstractNotifier):
            def channel_name(self):
                return "test"

            def send(self, subject, message, metadata=None):
                return NotificationResult(channel="test", success=True)

        self.dispatcher.register_notifier(TestNotifier())
        results = self.dispatcher.send_all(
            subject="Test",
            message="Message",
        )

        self.assertEqual(len(results), 0)  # "test" channel not in config

    @patch("boto3.client")
    def test_dispatcher_send_all_with_sns(self, mock_boto3_client):
        """Test send_all with SNS channel registered."""
        mock_sns = MagicMock()
        mock_boto3_client.return_value = mock_sns
        mock_sns.publish.return_value = {"MessageId": "123"}

        sns_notifier = SNSNotifier("arn:aws:sns:us-east-1:123:topic")
        self.dispatcher.register_notifier(sns_notifier)

        results = self.dispatcher.send_all(
            subject="Test Alert",
            message="This is a test",
        )

        # sns is registered and in the default channels list
        self.assertGreater(len(results), 0)

    @patch("boto3.client")
    def test_dispatcher_send_all_multiple_channels(self, mock_boto3_client):
        """Test send_all with multiple notifiers."""
        class SlackNotifier(AbstractNotifier):
            def channel_name(self):
                return "slack"

            def send(self, subject, message, metadata=None):
                return NotificationResult(channel="slack", success=True)

        class TeamsNotifier(AbstractNotifier):
            def channel_name(self):
                return "teams"

            def send(self, subject, message, metadata=None):
                return NotificationResult(channel="teams", success=False, error="Auth failed")

        self.dispatcher.register_notifier(SlackNotifier())
        self.dispatcher.register_notifier(TeamsNotifier())

        # Create a dispatcher with multiple channels
        import os
        with patch.dict(os.environ, {"NOTIFICATION_CHANNELS": "slack,teams"}, clear=True):
            from cost_guardian.config import NotificationConfig
            config = NotificationConfig.from_env()
            dispatcher = NotificationDispatcher(config)
            dispatcher.register_notifier(SlackNotifier())
            dispatcher.register_notifier(TeamsNotifier())

            results = dispatcher.send_all(
                subject="Test",
                message="Message",
            )

            self.assertEqual(len(results), 2)
            self.assertTrue(results["slack"].success)
            self.assertFalse(results["teams"].success)

    def test_dispatcher_send_all_handles_exceptions(self):
        """Test that dispatcher handles notifier exceptions."""
        class BadNotifier(AbstractNotifier):
            def channel_name(self):
                return "bad"

            def send(self, subject, message, metadata=None):
                raise RuntimeError("Notifier crashed")

        import os
        with patch.dict(os.environ, {"NOTIFICATION_CHANNELS": "bad"}, clear=True):
            from cost_guardian.config import NotificationConfig
            config = NotificationConfig.from_env()
            dispatcher = NotificationDispatcher(config)
            dispatcher.register_notifier(BadNotifier())

            results = dispatcher.send_all(
                subject="Test",
                message="Message",
            )

            self.assertFalse(results["bad"].success)
            self.assertIn("Notifier crashed", results["bad"].error)


if __name__ == "__main__":
    unittest.main()
