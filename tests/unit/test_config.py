"""
Unit tests for cost_guardian.config module.
"""
import os
import unittest
from unittest.mock import patch, MagicMock
from botocore.exceptions import ClientError

from cost_guardian.config import (
    _get_env_or_ssm,
    _env_truthy,
    _parse_regions,
    _parse_enforcement_services,
    CostExplorerConfig,
    AlertConfig,
    EnforcementConfig,
    NotificationConfig,
    DynamoDBConfig,
)


class TestEnvTruthy(unittest.TestCase):
    """Test _env_truthy utility."""

    @patch.dict(os.environ, {"TEST_VAR": "true"}, clear=True)
    def test_truthy_true_string(self):
        self.assertTrue(_env_truthy("TEST_VAR"))

    @patch.dict(os.environ, {"TEST_VAR": "1"}, clear=True)
    def test_truthy_1(self):
        self.assertTrue(_env_truthy("TEST_VAR"))

    @patch.dict(os.environ, {"TEST_VAR": "yes"}, clear=True)
    def test_truthy_yes(self):
        self.assertTrue(_env_truthy("TEST_VAR"))

    @patch.dict(os.environ, {"TEST_VAR": "false"}, clear=True)
    def test_falsy_false_string(self):
        self.assertFalse(_env_truthy("TEST_VAR"))

    @patch.dict(os.environ, {}, clear=True)
    def test_default_false(self):
        self.assertFalse(_env_truthy("MISSING_VAR"))

    @patch.dict(os.environ, {}, clear=True)
    def test_default_true(self):
        self.assertTrue(_env_truthy("MISSING_VAR", "true"))


class TestParseRegions(unittest.TestCase):
    """Test _parse_regions utility."""

    @patch.dict(os.environ, {"ENFORCEMENT_REGIONS": "us-east-1,us-west-2"}, clear=True)
    def test_parse_multiple_regions(self):
        regions = _parse_regions()
        self.assertEqual(regions, ["us-east-1", "us-west-2"])

    @patch.dict(os.environ, {"ENFORCEMENT_REGIONS": "us-east-1"}, clear=True)
    def test_parse_single_region(self):
        regions = _parse_regions()
        self.assertEqual(regions, ["us-east-1"])

    @patch.dict(os.environ, {"ENFORCEMENT_REGIONS": "  us-east-1  ,  us-west-2  "}, clear=True)
    def test_parse_regions_with_whitespace(self):
        regions = _parse_regions()
        self.assertEqual(regions, ["us-east-1", "us-west-2"])

    @patch.dict(os.environ, {"AWS_REGION": "eu-west-1"}, clear=True)
    def test_parse_regions_aws_region_fallback(self):
        regions = _parse_regions()
        self.assertEqual(regions, ["eu-west-1"])

    @patch.dict(os.environ, {}, clear=True)
    def test_parse_regions_empty(self):
        regions = _parse_regions()
        self.assertEqual(regions, [])


class TestParseEnforcementServices(unittest.TestCase):
    """Test _parse_enforcement_services utility."""

    @patch.dict(os.environ, {"ENFORCEMENT_SERVICES": "ec2,rds,ecs"}, clear=True)
    def test_parse_multiple_services(self):
        services = _parse_enforcement_services()
        self.assertEqual(services, ["ec2", "rds", "ecs"])

    @patch.dict(os.environ, {"ENFORCEMENT_SERVICES": "ec2"}, clear=True)
    def test_parse_single_service(self):
        services = _parse_enforcement_services()
        self.assertEqual(services, ["ec2"])

    @patch.dict(os.environ, {}, clear=True)
    def test_parse_services_default_ec2(self):
        services = _parse_enforcement_services()
        self.assertEqual(services, ["ec2"])


class TestGetEnvOrSSM(unittest.TestCase):
    """Test _get_env_or_ssm utility."""

    @patch.dict(os.environ, {"WEBHOOK_URL": "https://hooks.slack.com/..."}, clear=True)
    def test_returns_env_var(self):
        result = _get_env_or_ssm("WEBHOOK_URL")
        self.assertEqual(result, "https://hooks.slack.com/...")

    @patch.dict(os.environ, {"WEBHOOK_URL": "/cost-guardian/slack-webhook"}, clear=True)
    @patch("cost_guardian.config._resolve_ssm_parameter")
    def test_resolves_ssm_when_starts_with_slash(self, mock_ssm):
        mock_ssm.return_value = "https://hooks.slack.com/ssm-resolved"
        result = _get_env_or_ssm("WEBHOOK_URL")
        self.assertEqual(result, "https://hooks.slack.com/ssm-resolved")
        mock_ssm.assert_called_once_with("/cost-guardian/slack-webhook")

    @patch.dict(os.environ, {}, clear=True)
    def test_returns_default_when_missing(self):
        result = _get_env_or_ssm("MISSING", "default-value")
        self.assertEqual(result, "default-value")


class TestCostExplorerConfig(unittest.TestCase):
    """Test CostExplorerConfig."""

    @patch.dict(
        os.environ,
        {
            "THRESHOLD": "5.0",
            "COST_EXPLORER_GRANULARITY": "DAILY",
            "COST_EXPLORER_METRIC": "BlendedCost",
            "HISTORY_TTL_DAYS": "60",
            "ENABLE_DAILY_PK": "true",
            "ENABLE_MONTHLY_ROLLUP": "false",
            "MONTHLY_ROLLUP_TTL_DAYS": "200",
        },
        clear=True,
    )
    def test_from_env(self):
        config = CostExplorerConfig.from_env()
        self.assertEqual(config.threshold, 5.0)
        self.assertEqual(config.granularity, "DAILY")
        self.assertEqual(config.metric, "BlendedCost")
        self.assertEqual(config.history_ttl_days, 60)
        self.assertTrue(config.enable_daily_pk)
        self.assertFalse(config.enable_monthly_rollup)
        self.assertEqual(config.monthly_rollup_ttl_days, 200)

    @patch.dict(os.environ, {}, clear=True)
    def test_from_env_defaults(self):
        config = CostExplorerConfig.from_env()
        self.assertEqual(config.threshold, 0.10)
        self.assertEqual(config.granularity, "DAILY")
        self.assertEqual(config.metric, "UnblendedCost")


class TestEnforcementConfig(unittest.TestCase):
    """Test EnforcementConfig."""

    @patch.dict(
        os.environ,
        {
            "ENFORCEMENT_ENABLED": "true",
            "ENFORCEMENT_ARMED": "true",
            "ENFORCEMENT_DRY_RUN": "false",
            "ENFORCEMENT_TAG_KEY": "CostGuardian",
            "ENFORCEMENT_TAG_VALUE": "managed",
            "ENFORCEMENT_REGIONS": "us-east-1,eu-west-1",
            "ENFORCEMENT_SERVICES": "ec2,rds,ecs",
        },
        clear=True,
    )
    def test_from_env(self):
        config = EnforcementConfig.from_env()
        self.assertTrue(config.enabled)
        self.assertTrue(config.armed)
        self.assertFalse(config.dry_run)
        self.assertEqual(config.tag_key, "CostGuardian")
        self.assertEqual(config.tag_value, "managed")
        self.assertEqual(config.regions, ["us-east-1", "eu-west-1"])
        self.assertEqual(config.services, ["ec2", "rds", "ecs"])

    @patch.dict(os.environ, {}, clear=True)
    def test_from_env_defaults(self):
        config = EnforcementConfig.from_env()
        self.assertFalse(config.enabled)
        self.assertFalse(config.armed)
        self.assertTrue(config.dry_run)
        self.assertEqual(config.services, ["ec2"])


class TestNotificationConfig(unittest.TestCase):
    """Test NotificationConfig."""

    @patch.dict(
        os.environ,
        {
            "NOTIFICATION_CHANNELS": "sns,slack,teams",
            "SLACK_WEBHOOK_URL": "https://hooks.slack.com/...",
            "TEAMS_WEBHOOK_URL": "https://outlook.webhook.office.com/...",
            "DISCORD_WEBHOOK_URL": "https://discordapp.com/api/webhooks/...",
            "PAGERDUTY_ROUTING_KEY": "secret-routing-key",
        },
        clear=True,
    )
    def test_from_env(self):
        config = NotificationConfig.from_env()
        self.assertEqual(config.channels, ["sns", "slack", "teams"])
        self.assertEqual(config.slack_webhook, "https://hooks.slack.com/...")
        self.assertEqual(config.teams_webhook, "https://outlook.webhook.office.com/...")
        self.assertEqual(config.discord_webhook, "https://discordapp.com/api/webhooks/...")
        self.assertEqual(config.pagerduty_routing_key, "secret-routing-key")

    @patch.dict(os.environ, {}, clear=True)
    def test_from_env_defaults(self):
        config = NotificationConfig.from_env()
        self.assertEqual(config.channels, ["sns"])
        # Empty string is returned when env var not found
        self.assertEqual(config.slack_webhook, "")


class TestDynamoDBConfig(unittest.TestCase):
    """Test DynamoDBConfig."""

    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ENFORCEMENT_LOG_TABLE": "enforcement-log-table",
            "SCHEDULE_STATE_TABLE": "schedule-state-table",
        },
        clear=True,
    )
    def test_from_env(self):
        config = DynamoDBConfig.from_env()
        self.assertEqual(config.state_table, "state-table")
        self.assertEqual(config.history_table, "history-table")
        self.assertEqual(config.enforcement_log_table, "enforcement-log-table")
        self.assertEqual(config.schedule_state_table, "schedule-state-table")

    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
        },
        clear=True,
    )
    def test_from_env_optional_tables(self):
        config = DynamoDBConfig.from_env()
        self.assertIsNone(config.enforcement_log_table)
        self.assertIsNone(config.schedule_state_table)


if __name__ == "__main__":
    unittest.main()
