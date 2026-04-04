"""
Cost Guardian: AWS cost monitoring and enforcement Lambda handler.
"""
import os
import json
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from .config import (
    CostExplorerConfig,
    AlertConfig,
    EnforcementConfig,
    NotificationConfig,
    DynamoDBConfig,
)
from .logging_utils import structured_log, log_info, log_error, log_enforcement
from .enforcement import EnforcementOrchestrator
from .notifications import NotificationDispatcher
from .notifications.sns_notifier import SNSNotifier


# --- Utility functions (kept from original) ---

def _utc_now():
    return datetime.now(timezone.utc)


def _epoch_now() -> int:
    return int(_utc_now().timestamp())


def _today_utc_date():
    return _utc_now().date().isoformat()


def _iso_ts():
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_minute_ts():
    m = _utc_now().replace(second=0, microsecond=0)
    return m.strftime("%Y-%m-%dT%H:%MZ")


def _ttl_epoch(days: int) -> int:
    return int((_utc_now() + timedelta(days=days)).timestamp())


def _d(n: float) -> Decimal:
    return Decimal(str(n))


def _is_conditional_exists(err: ClientError) -> bool:
    return err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _month_start_from_iso_day(day_iso: str) -> str:
    d = date.fromisoformat(day_iso)
    return date(d.year, d.month, 1).isoformat()


def _month_key_from_iso_day(day_iso: str) -> str:
    return day_iso[:7]


def _write_enforcement_audit(enforcement_log_table, item: dict):
    """Best-effort audit write."""
    if enforcement_log_table is None:
        return
    try:
        enforcement_log_table.put_item(Item=item)
    except (ClientError, BotoCoreError) as e:
        log_error("enforcement audit write failed (ignored)", error=str(e))


def handler(event, context):
    """
    Main Lambda handler: collect costs, enforce on breach, notify via enabled channels.
    """
    # Load all configuration
    ce_config = CostExplorerConfig.from_env()
    alert_config = AlertConfig.from_env()
    enforcement_config = EnforcementConfig.from_env()
    notification_config = NotificationConfig.from_env()
    db_config = DynamoDBConfig.from_env()

    ts = _iso_ts()
    minute_ts = _iso_minute_ts()
    today = _today_utc_date()

    # Initialize AWS clients
    dynamodb = boto3.resource("dynamodb")
    ce = boto3.client("ce")

    state_table = dynamodb.Table(db_config.state_table)
    history_table = dynamodb.Table(db_config.history_table)
    enforcement_log_table = (
        dynamodb.Table(db_config.enforcement_log_table)
        if db_config.enforcement_log_table
        else None
    )

    # Initialize orchestrators
    enforcement_orchestrator = EnforcementOrchestrator()
    notification_dispatcher = NotificationDispatcher(notification_config)

    # Register SNS notifier if channel enabled
    if "sns" in notification_config.channels:
        sns_notifier = SNSNotifier(alert_config.topic_arn)
        notification_dispatcher.register_notifier(sns_notifier)

    log_info(
        "cost-guardian handler start",
        ts=ts,
        minute_ts=minute_ts,
        threshold=ce_config.threshold,
        granularity=ce_config.granularity,
        enforcement_enabled=enforcement_config.enabled,
        enforcement_armed=enforcement_config.armed,
        enforcement_services=enforcement_config.services,
    )

    try:
        # ---- 1) Fetch cost (DAILY or MONTHLY based on config) ----
        if ce_config.granularity == "MONTHLY":
            start = _month_start_from_iso_day(today)
            end = (datetime.fromisoformat(today) + timedelta(days=1)).date().isoformat()
        else:
            start = today
            end = (datetime.fromisoformat(today) + timedelta(days=1)).date().isoformat()

        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": start, "End": end},
            Granularity=ce_config.granularity,
            Metrics=[ce_config.metric],
        )

        amount_str = resp["ResultsByTime"][0]["Total"][ce_config.metric]["Amount"]
        unit = resp["ResultsByTime"][0]["Total"][ce_config.metric]["Unit"]
        cost = float(amount_str)
        status = "OK" if cost < ce_config.threshold else "BREACH"

        cost_d = _d(cost)
        threshold_d = _d(ce_config.threshold)
        ttl = _ttl_epoch(ce_config.history_ttl_days)

        # ---- 2) Write history ----
        history_item = {
            "pk": "COST",
            "sk": f"MINUTE#{minute_ts}",
            "date": today,
            "ts": ts,
            "minute_ts": minute_ts,
            "cost": cost_d,
            "unit": unit,
            "threshold": threshold_d,
            "status": status,
            "ttl": ttl,
            "mode": ce_config.granularity,
        }

        def put_history_idempotent(item: dict):
            return history_table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
            )

        try:
            put_history_idempotent(history_item)
        except ClientError as e:
            if not _is_conditional_exists(e):
                log_error("history write failed (all-time)", error=str(e))
        except BotoCoreError as e:
            log_error("history write failed (all-time)", error=str(e))

        # Daily PK if enabled
        if ce_config.enable_daily_pk:
            daily_item = dict(history_item)
            daily_item["pk"] = f"COST#{today}"
            try:
                put_history_idempotent(daily_item)
            except ClientError as e:
                if not _is_conditional_exists(e):
                    log_error("history write failed (daily)", error=str(e))
            except BotoCoreError as e:
                log_error("history write failed (daily)", error=str(e))

        # Monthly rollup if enabled
        if ce_config.enable_monthly_rollup:
            month_key = _month_key_from_iso_day(today)
            month_start = _month_start_from_iso_day(today)
            month_pk = f"COST#{month_key}"
            rollup_ttl = _ttl_epoch(ce_config.monthly_rollup_ttl_days)

            try:
                if ce_config.granularity == "MONTHLY":
                    mtd_cost = cost
                    mtd_unit = unit
                else:
                    mtd_resp = ce.get_cost_and_usage(
                        TimePeriod={"Start": month_start, "End": end},
                        Granularity="MONTHLY",
                        Metrics=[ce_config.metric],
                    )
                    mtd_amount_str = mtd_resp["ResultsByTime"][0]["Total"][ce_config.metric]["Amount"]
                    mtd_unit = mtd_resp["ResultsByTime"][0]["Total"][ce_config.metric]["Unit"]
                    mtd_cost = float(mtd_amount_str)

                monthly_item = {
                    "pk": month_pk,
                    "sk": "ROLLUP",
                    "month": month_key,
                    "month_start": month_start,
                    "as_of_date": today,
                    "ts": ts,
                    "mtd_cost": _d(mtd_cost),
                    "unit": mtd_unit,
                    "ttl": rollup_ttl,
                }
                history_table.put_item(Item=monthly_item)

            except (ClientError, BotoCoreError, KeyError, IndexError, ValueError, TypeError) as e:
                log_error("monthly rollup failed", error=str(e))

        # ---- 3) Write state ----
        try:
            state_table.put_item(Item={
                "key": "latest",
                "date": today,
                "ts": ts,
                "cost": cost_d,
                "unit": unit,
                "threshold": threshold_d,
                "status": status,
                "mode": ce_config.granularity,
            })
        except (ClientError, BotoCoreError) as e:
            log_error("state write failed", error=str(e))

        log_info(
            "cost collected",
            date=today,
            cost=cost,
            unit=unit,
            status=status,
            threshold=ce_config.threshold,
        )

        # ---- 4) BREACH handling: enforcement + notifications ----
        if status == "BREACH":
            breach_message = (
                f"[CostGuardian] BREACH ({ce_config.granularity}): "
                f"cost={cost:.4f} {unit} >= threshold={ce_config.threshold:.4f} {unit} "
                f"(date={today})"
            )

            enforcement_result = None
            audit_status = None
            audit_reason = None

            # Decide enforcement
            if not enforcement_config.enabled:
                audit_status = "SKIPPED"
                audit_reason = "enforcement disabled"
            elif not enforcement_config.tag_key or not enforcement_config.tag_value:
                audit_status = "SKIPPED"
                audit_reason = "missing tag key/value"
            elif not enforcement_config.regions:
                audit_status = "SKIPPED"
                audit_reason = "no regions configured"
            elif not enforcement_config.services:
                audit_status = "SKIPPED"
                audit_reason = "no services configured"
            elif not enforcement_config.armed and not enforcement_config.dry_run:
                audit_status = "SKIPPED"
                audit_reason = "not armed"
                log_info("enforcement not armed; skipping")
            else:
                # Run enforcement
                try:
                    enforcement_results = enforcement_orchestrator.enforce_all(
                        services=enforcement_config.services,
                        tag_key=enforcement_config.tag_key,
                        tag_value=enforcement_config.tag_value,
                        regions=enforcement_config.regions,
                        dry_run=enforcement_config.dry_run,
                    )

                    if enforcement_config.dry_run:
                        audit_status = "DRY_RUN"
                    else:
                        audit_status = "STOPPED"

                    enforcement_result = enforcement_results

                    # Log enforcement results per service
                    for service, result in enforcement_results.items():
                        log_enforcement(
                            status=audit_status,
                            service=service,
                            matched=result.matched,
                            stopped=result.stopped,
                            error=result.error,
                        )

                except Exception as e:
                    audit_status = "ERROR"
                    audit_reason = str(e)
                    log_error(f"enforcement failed: {str(e)}", error=str(e))

            # Write audit trail (keep backward-compat structure)
            audit_item = {
                "pk": f"ENFORCEMENT#{today}",
                "sk": f"TS#{ts}",
                "date": today,
                "ts": ts,
                "minute_ts": minute_ts,
                "mode": ce_config.granularity,
                "threshold": threshold_d,
                "cost": cost_d,
                "unit": unit,
                "status": audit_status or "SKIPPED",
                "reason": audit_reason or "",
                "enabled": bool(enforcement_config.enabled),
                "dry_run": bool(enforcement_config.dry_run),
                "armed": bool(enforcement_config.armed),
                "tag_key": enforcement_config.tag_key or "",
                "tag_value": enforcement_config.tag_value or "",
                "regions": enforcement_config.regions,
                "services": enforcement_config.services,
                "result": enforcement_result or {},
            }
            _write_enforcement_audit(enforcement_log_table, audit_item)

            # Send notifications
            notification_results = notification_dispatcher.send_all(
                subject="CostGuardian BREACH",
                message=breach_message,
                metadata={
                    "cost": cost,
                    "threshold": ce_config.threshold,
                    "status": audit_status,
                },
            )

            for channel, result in notification_results.items():
                if result.success:
                    log_info(f"notification sent to {channel}")
                else:
                    log_error(f"notification failed on {channel}", error=result.error)

        return {
            "ok": True,
            "date": today,
            "cost": float(cost),
            "unit": unit,
            "threshold": float(ce_config.threshold),
            "status": status,
            "minute_ts": minute_ts,
            "mode": ce_config.granularity,
        }

    except (ClientError, BotoCoreError, KeyError, IndexError, ValueError, TypeError) as e:
        log_error("handler error", error=str(e), ts=ts)
        raise
