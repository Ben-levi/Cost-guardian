"""
Centralized configuration: env-var parsing + SSM Parameter Store resolution.
"""
import os
from dataclasses import dataclass
from typing import Optional
import boto3
from botocore.exceptions import BotoCoreError, ClientError


_ssm_cache = {}


def _resolve_ssm_parameter(param_path: str) -> Optional[str]:
    """Fetch from SSM Parameter Store (with in-memory cache)."""
    if param_path in _ssm_cache:
        return _ssm_cache[param_path]

    try:
        ssm = boto3.client("ssm")
        resp = ssm.get_parameter(Name=param_path, WithDecryption=True)
        value = resp["Parameter"]["Value"]
        _ssm_cache[param_path] = value
        return value
    except (ClientError, BotoCoreError):
        return None


def _get_env_or_ssm(name: str, default: str = "") -> str:
    """
    Read from env var. If starts with '/', resolve from SSM Parameter Store.
    """
    val = os.environ.get(name, "").strip()
    if not val:
        return default
    if val.startswith("/"):
        ssm_val = _resolve_ssm_parameter(val)
        return ssm_val if ssm_val else default
    return val


def _env_truthy(name: str, default: str = "false") -> bool:
    val = os.environ.get(name, default)
    return str(val).strip().lower() in ("1", "true", "yes", "y")


def _parse_regions() -> list[str]:
    raw = os.environ.get("ENFORCEMENT_REGIONS", "").strip()
    if raw:
        return [r.strip() for r in raw.split(",") if r.strip()]
    aws_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    return [aws_region] if aws_region else []


def _parse_enforcement_services() -> list[str]:
    """Parse ENFORCEMENT_SERVICES env var (default: ec2 for backward compat)."""
    raw = os.environ.get("ENFORCEMENT_SERVICES", "ec2").strip()
    return [s.strip() for s in raw.split(",") if s.strip()]


@dataclass
class CostExplorerConfig:
    """Cost Explorer collection settings."""
    threshold: float
    granularity: str  # "DAILY" or "MONTHLY"
    metric: str       # e.g. "UnblendedCost"
    history_ttl_days: int
    enable_daily_pk: bool
    enable_monthly_rollup: bool
    monthly_rollup_ttl_days: int

    @staticmethod
    def from_env() -> "CostExplorerConfig":
        return CostExplorerConfig(
            threshold=float(os.environ.get("THRESHOLD", "0.10")),
            granularity=os.environ.get("COST_EXPLORER_GRANULARITY", "DAILY").strip().upper(),
            metric=os.environ.get("COST_EXPLORER_METRIC", "UnblendedCost"),
            history_ttl_days=int(os.environ.get("HISTORY_TTL_DAYS", "30")),
            enable_daily_pk=_env_truthy("ENABLE_DAILY_PK", "false"),
            enable_monthly_rollup=_env_truthy("ENABLE_MONTHLY_ROLLUP", "true"),
            monthly_rollup_ttl_days=int(os.environ.get("MONTHLY_ROLLUP_TTL_DAYS", "400")),
        )


@dataclass
class AlertConfig:
    """Alert and cooldown settings."""
    topic_arn: str
    cooldown_minutes: int
    cooldown_key: str
    pending_alert_key: str
    pending_alert_ttl_days: int

    @staticmethod
    def from_env() -> "AlertConfig":
        return AlertConfig(
            topic_arn=os.environ["ALERTS_TOPIC_ARN"],
            cooldown_minutes=int(os.environ.get("ALERT_COOLDOWN_MINUTES", "240")),
            cooldown_key=os.environ.get("ALERT_COOLDOWN_KEY", "last_breach_alert"),
            pending_alert_key=os.environ.get("PENDING_ALERT_KEY", "alert_pending"),
            pending_alert_ttl_days=int(os.environ.get("PENDING_ALERT_TTL_DAYS", "7")),
        )


@dataclass
class EnforcementConfig:
    """Resource enforcement settings."""
    enabled: bool
    armed: bool
    dry_run: bool
    tag_key: str
    tag_value: str
    regions: list[str]
    services: list[str]  # ["ec2", "rds", "ecs", ...]

    @staticmethod
    def from_env() -> "EnforcementConfig":
        return EnforcementConfig(
            enabled=_env_truthy("ENFORCEMENT_ENABLED", "false"),
            armed=_env_truthy("ENFORCEMENT_ARMED", "false"),
            dry_run=_env_truthy("ENFORCEMENT_DRY_RUN", "true"),
            tag_key=os.environ.get("ENFORCEMENT_TAG_KEY", "").strip(),
            tag_value=os.environ.get("ENFORCEMENT_TAG_VALUE", "").strip(),
            regions=_parse_regions(),
            services=_parse_enforcement_services(),
        )


@dataclass
class NotificationConfig:
    """Notification channel settings."""
    channels: list[str]  # ["sns", "slack", "teams", ...]
    slack_webhook: Optional[str]
    teams_webhook: Optional[str]
    discord_webhook: Optional[str]
    pagerduty_routing_key: Optional[str]

    @staticmethod
    def from_env() -> "NotificationConfig":
        channels_raw = os.environ.get("NOTIFICATION_CHANNELS", "sns").strip()
        channels = [c.strip() for c in channels_raw.split(",") if c.strip()]

        return NotificationConfig(
            channels=channels,
            slack_webhook=_get_env_or_ssm("SLACK_WEBHOOK_URL"),
            teams_webhook=_get_env_or_ssm("TEAMS_WEBHOOK_URL"),
            discord_webhook=_get_env_or_ssm("DISCORD_WEBHOOK_URL"),
            pagerduty_routing_key=_get_env_or_ssm("PAGERDUTY_ROUTING_KEY"),
        )


@dataclass
class DynamoDBConfig:
    """DynamoDB table names."""
    state_table: str
    history_table: str
    enforcement_log_table: Optional[str]
    schedule_state_table: Optional[str]

    @staticmethod
    def from_env() -> "DynamoDBConfig":
        return DynamoDBConfig(
            state_table=os.environ["TABLE_NAME"],
            history_table=os.environ["COST_HISTORY_TABLE"],
            enforcement_log_table=os.environ.get("ENFORCEMENT_LOG_TABLE", "").strip() or None,
            schedule_state_table=os.environ.get("SCHEDULE_STATE_TABLE", "").strip() or None,
        )
