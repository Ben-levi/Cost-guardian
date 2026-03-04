import os
import json
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal

import boto3
from botocore.exceptions import BotoCoreError, ClientError


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


def _env_truthy(name: str, default: str = "false") -> bool:
    val = os.environ.get(name, default)
    return str(val).strip().lower() in ("1", "true", "yes", "y")


def _is_conditional_exists(err: ClientError) -> bool:
    return err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _month_start_from_iso_day(day_iso: str) -> str:
    d = date.fromisoformat(day_iso)
    return date(d.year, d.month, 1).isoformat()


def _month_key_from_iso_day(day_iso: str) -> str:
    return day_iso[:7]


def _parse_regions() -> list[str]:
    raw = os.environ.get("ENFORCEMENT_REGIONS", "").strip()
    if raw:
        return [r.strip() for r in raw.split(",") if r.strip()]
    aws_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    return [aws_region] if aws_region else []


def _enforcement_config():
    """
    Returns:
      enabled: bool
      dry_run: bool
      armed: bool
      tag_key: str
      tag_value: str
      regions: list[str]
    """
    enabled = _env_truthy("ENFORCEMENT_ENABLED", "false")
    dry_run = _env_truthy("ENFORCEMENT_DRY_RUN", "true")  # safe default
    armed = _env_truthy("ENFORCEMENT_ARMED", "false")     # safety latch (must be explicitly armed)
    tag_key = os.environ.get("ENFORCEMENT_TAG_KEY", "").strip()
    tag_value = os.environ.get("ENFORCEMENT_TAG_VALUE", "").strip()
    regions = _parse_regions()
    return enabled, dry_run, armed, tag_key, tag_value, regions


def _enforce_stop_instances(tag_key: str, tag_value: str, regions: list[str], dry_run: bool) -> dict:
    """
    Returns:
      {
        "regions": {
          "us-east-1": {"matched": [...], "stopped": [...], "errors": [...]},
          ...
        },
        "total_matched": int,
        "total_stopped": int
      }
    """
    summary: dict = {"regions": {}, "total_matched": 0, "total_stopped": 0}

    for region in regions:
        region_info = {"matched": [], "stopped": [], "errors": []}
        summary["regions"][region] = region_info

        try:
            ec2 = boto3.client("ec2", region_name=region)

            resp = ec2.describe_instances(
                Filters=[
                    {"Name": f"tag:{tag_key}", "Values": [tag_value]},
                    {"Name": "instance-state-name", "Values": ["running"]},
                ]
            )

            instance_ids = []
            for reservation in resp.get("Reservations", []):
                for inst in reservation.get("Instances", []):
                    iid = inst.get("InstanceId")
                    if iid:
                        instance_ids.append(iid)

            region_info["matched"] = instance_ids
            summary["total_matched"] += len(instance_ids)

            if instance_ids and not dry_run:
                ec2.stop_instances(InstanceIds=instance_ids)
                region_info["stopped"] = list(instance_ids)
                summary["total_stopped"] += len(instance_ids)

        except (ClientError, BotoCoreError) as e:
            region_info["errors"].append(str(e))

    return summary


def handler(event, context):
    # Runtime config (read at runtime so tests can patch os.environ)
    threshold = float(os.environ.get("THRESHOLD", "0.10"))
    granularity = os.environ.get("COST_EXPLORER_GRANULARITY", "DAILY").strip().upper()
    metric = os.environ.get("COST_EXPLORER_METRIC", "UnblendedCost")
    history_ttl_days = int(os.environ.get("HISTORY_TTL_DAYS", "30"))
    enable_daily_pk = _env_truthy("ENABLE_DAILY_PK", "false")

    enable_monthly_rollup = _env_truthy("ENABLE_MONTHLY_ROLLUP", "true")
    monthly_rollup_ttl_days = int(os.environ.get("MONTHLY_ROLLUP_TTL_DAYS", "400"))

    pending_alert_key = os.environ.get("PENDING_ALERT_KEY", "alert_pending")
    pending_alert_ttl_days = int(os.environ.get("PENDING_ALERT_TTL_DAYS", "7"))

    # NEW: cooldown config
    alert_cooldown_minutes = int(os.environ.get("ALERT_COOLDOWN_MINUTES", "240"))
    alert_cooldown_key = os.environ.get("ALERT_COOLDOWN_KEY", "last_breach_alert")
    alert_cooldown_seconds = max(0, alert_cooldown_minutes) * 60

    # Enforcement config (includes ENFORCEMENT_ARMED)
    (
        enforcement_enabled,
        enforcement_dry_run,
        enforcement_armed,
        enforcement_tag_key,
        enforcement_tag_value,
        enforcement_regions,
    ) = _enforcement_config()

    ts = _iso_ts()
    minute_ts = _iso_minute_ts()
    today = _today_utc_date()

    table_name = os.environ["TABLE_NAME"]
    cost_history_table_name = os.environ["COST_HISTORY_TABLE"]
    alerts_topic_arn = os.environ["ALERTS_TOPIC_ARN"]

    dynamodb = boto3.resource("dynamodb")
    ce = boto3.client("ce")
    sns = boto3.client("sns")

    state_table = dynamodb.Table(table_name)
    history_table = dynamodb.Table(cost_history_table_name)

    # ---- 0) Retry pending alert (best effort; never fail run) ----
    try:
        pending_resp = state_table.get_item(Key={"key": pending_alert_key})
        pending = pending_resp.get("Item")
        if pending and pending.get("topic_arn") == alerts_topic_arn and pending.get("message"):
            try:
                sns.publish(
                    TopicArn=pending["topic_arn"],
                    Subject=pending.get("subject", "CostGuardian BREACH"),
                    Message=pending["message"],
                )
                state_table.delete_item(Key={"key": pending_alert_key})
                print(json.dumps({"msg": "pending sns alert published and cleared"}))
            except (ClientError, BotoCoreError) as e:
                print(json.dumps({"msg": "pending sns alert retry failed (ignored)", "error": str(e)}))
    except (ClientError, BotoCoreError, KeyError, TypeError) as e:
        print(json.dumps({"msg": "pending alert check failed (ignored)", "error": str(e)}))

    # ---- Decide time window based on DAILY vs MONTHLY ----
    # NOTE: In MONTHLY mode, threshold is treated as *monthly MTD budget*.
    if granularity == "MONTHLY":
        start = _month_start_from_iso_day(today)
        end = (datetime.fromisoformat(today) + timedelta(days=1)).date().isoformat()
        ce_granularity = "MONTHLY"
        threshold_label = "threshold_usd_per_month"
    else:
        start = today
        end = (datetime.fromisoformat(today) + timedelta(days=1)).date().isoformat()
        ce_granularity = granularity  # typically DAILY
        threshold_label = "threshold_usd_per_day"

    print(json.dumps({
        "msg": "cost-guardian collector start",
        "ts": ts,
        "minute_ts": minute_ts,
        threshold_label: threshold,
        "granularity": granularity,
        "metric": metric,
        "enable_daily_pk": enable_daily_pk,
        "enable_monthly_rollup": enable_monthly_rollup,
        "alert_cooldown_minutes": alert_cooldown_minutes,
        "enforcement_enabled": enforcement_enabled,
        "enforcement_dry_run": enforcement_dry_run,
        "enforcement_armed": enforcement_armed,
        "enforcement_regions": enforcement_regions,
    }))

    try:
        # ---- 1) Fetch cost (DAILY: today; MONTHLY: MTD) ----
        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": start, "End": end},
            Granularity=ce_granularity,
            Metrics=[metric],
        )
        amount_str = resp["ResultsByTime"][0]["Total"][metric]["Amount"]
        unit = resp["ResultsByTime"][0]["Total"][metric]["Unit"]

        cost = float(amount_str)
        status = "OK" if cost < threshold else "BREACH"

        # If we're OK, clear breach cooldown marker so next BREACH alerts immediately (best-effort)
        if status == "OK":
            try:
                state_table.delete_item(Key={"key": alert_cooldown_key})
            except (ClientError, BotoCoreError) as e:
                print(json.dumps({"msg": "cooldown clear failed (ignored)", "error": str(e)}))

        cost_d = _d(cost)
        threshold_d = _d(threshold)
        ttl = _ttl_epoch(history_ttl_days)

        # ---- 2) HISTORY first ----
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
            "mode": granularity,  # helpful for later debugging
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
                print(json.dumps({"msg": "history write failed (all-time; ignored)", "error": str(e)}))
        except BotoCoreError as e:
            print(json.dumps({"msg": "history write failed (all-time; ignored)", "error": str(e)}))

        if enable_daily_pk:
            daily_item = dict(history_item)
            daily_item["pk"] = f"COST#{today}"
            try:
                put_history_idempotent(daily_item)
            except ClientError as e:
                if not _is_conditional_exists(e):
                    print(json.dumps({"msg": "history write failed (daily; ignored)", "error": str(e)}))
            except BotoCoreError as e:
                print(json.dumps({"msg": "history write failed (daily; ignored)", "error": str(e)}))

        # ---- 2b) Monthly rollup (best effort) ----
        # If we are already in MONTHLY mode, reuse the fetched MTD cost (no extra CE call).
        if enable_monthly_rollup:
            month_key = _month_key_from_iso_day(today)
            month_start = _month_start_from_iso_day(today)
            month_pk = f"COST#{month_key}"
            rollup_ttl = _ttl_epoch(monthly_rollup_ttl_days)

            try:
                if granularity == "MONTHLY":
                    mtd_cost = cost
                    mtd_unit = unit
                else:
                    mtd_resp = ce.get_cost_and_usage(
                        TimePeriod={"Start": month_start, "End": end},
                        Granularity="MONTHLY",
                        Metrics=[metric],
                    )
                    mtd_amount_str = mtd_resp["ResultsByTime"][0]["Total"][metric]["Amount"]
                    mtd_unit = mtd_resp["ResultsByTime"][0]["Total"][metric]["Unit"]
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
                print(json.dumps({"msg": "monthly rollup failed (ignored)", "error": str(e)}))

        # ---- 3) STATE second ----
        try:
            state_table.put_item(Item={
                "key": "latest",
                "date": today,
                "ts": ts,
                "cost": cost_d,
                "unit": unit,
                "threshold": threshold_d,
                "status": status,
                "mode": granularity,
            })
        except (ClientError, BotoCoreError) as e:
            print(json.dumps({"msg": "state write failed (ignored)", "error": str(e)}))

        print(json.dumps({
            "msg": "cost-guardian collected",
            "date": today,
            "cost": cost,
            "unit": unit,
            "status": status,
            "minute_ts": minute_ts
        }))

        # ---- 4) BREACH flows ----
        if status == "BREACH":
            # Build ONE combined email body: breach + enforcement result
            if granularity == "MONTHLY":
                breach_line = (
                    f"[CostGuardian] BREACH (MONTHLY MTD): mtd_cost={cost:.4f} {unit} "
                    f">= threshold={threshold:.4f} {unit} (as_of={today})"
                )
            else:
                breach_line = (
                    f"[CostGuardian] BREACH (DAILY): daily_cost={cost:.4f} {unit} "
                    f">= threshold={threshold:.4f} {unit} (date={today})"
                )

            enforcement_block = ""
            # Enforcement best-effort (does NOT send separate email)
            if enforcement_enabled:
                if not enforcement_tag_key or not enforcement_tag_value:
                    enforcement_block = (
                        "\n\nENFORCEMENT: enabled but tag key/value missing -> skipped.\n"
                        "Set ENFORCEMENT_TAG_KEY and ENFORCEMENT_TAG_VALUE."
                    )
                elif not enforcement_regions:
                    enforcement_block = "\n\nENFORCEMENT: enabled but no regions configured -> skipped."
                else:
                    # ---- Safety latch ----
                    # - Dry-run: allowed regardless of armed flag (discover + log only)
                    # - Not dry-run: requires ENFORCEMENT_ARMED=true
                    if (not enforcement_dry_run) and (not enforcement_armed):
                        print(json.dumps({"msg": "enforcement not armed; skipping stop"}))
                        enforcement_block = (
                            "\n\nENFORCEMENT: NOT ARMED -> skip stop.\n"
                            "Set ENFORCEMENT_ARMED=true only when you're sure."
                        )
                    else:
                        try:
                            result = _enforce_stop_instances(
                                tag_key=enforcement_tag_key,
                                tag_value=enforcement_tag_value,
                                regions=enforcement_regions,
                                dry_run=enforcement_dry_run,
                            )
                            print(json.dumps({"msg": "enforcement result", "result": result}))

                            mode = "DRY_RUN" if enforcement_dry_run else "STOPPED"
                            enforcement_block = (
                                f"\n\nENFORCEMENT {mode}:\n"
                                f"- Armed: {enforcement_armed}\n"
                                f"- Tag filter: {enforcement_tag_key}={enforcement_tag_value}\n"
                                f"- Regions: {', '.join(enforcement_regions)}\n"
                                f"- Matched: {result.get('total_matched', 0)}\n"
                                f"- Stopped: {result.get('total_stopped', 0)}\n"
                                f"- Details: {json.dumps(result.get('regions', {}))}"
                            )

                        except (ClientError, BotoCoreError, ValueError, TypeError) as e:
                            enforcement_block = f"\n\nENFORCEMENT: failed (ignored): {str(e)}"

            subject = "CostGuardian BREACH"
            message = breach_line + enforcement_block

            # ---- BREACH alert cooldown ----
            allow_alert = True
            if alert_cooldown_seconds > 0:
                try:
                    last = state_table.get_item(Key={"key": alert_cooldown_key}).get("Item")
                    last_epoch = int(last.get("last_alert_epoch", 0)) if last else 0
                    now_epoch = _epoch_now()
                    if last_epoch and (now_epoch - last_epoch) < alert_cooldown_seconds:
                        allow_alert = False
                        remaining = alert_cooldown_seconds - (now_epoch - last_epoch)
                        print(json.dumps({
                            "msg": "breach alert suppressed by cooldown",
                            "cooldown_seconds": alert_cooldown_seconds,
                            "seconds_remaining": max(0, remaining),
                        }))
                except (ClientError, BotoCoreError, ValueError, TypeError) as e:
                    # Fail open: if we can't read cooldown state, we still alert
                    print(json.dumps({"msg": "cooldown check failed (fail-open)", "error": str(e)}))

            if allow_alert:
                # 4a) BREACH alert (pending-retry on failure)
                try:
                    sns.publish(
                        TopicArn=alerts_topic_arn,
                        Subject=subject,
                        Message=message,
                    )
                    print(json.dumps({"msg": "sns alert published"}))

                    # Record last breach alert time (for cooldown)
                    try:
                        state_table.put_item(Item={
                            "key": alert_cooldown_key,
                            "last_alert_epoch": _epoch_now(),
                            "date": today,
                            "ts": ts,
                            "minute_ts": minute_ts,
                        })
                    except (ClientError, BotoCoreError) as e:
                        print(json.dumps({"msg": "cooldown write failed (ignored)", "error": str(e)}))

                except (ClientError, BotoCoreError) as e:
                    pending_ttl = _ttl_epoch(pending_alert_ttl_days)
                    pending_item = {
                        "key": pending_alert_key,
                        "topic_arn": alerts_topic_arn,
                        "subject": subject,
                        "message": message,
                        "date": today,
                        "ts": ts,
                        "minute_ts": minute_ts,
                        "ttl": pending_ttl,
                        "status": "PENDING",
                        "reason": str(e),
                    }
                    try:
                        state_table.put_item(Item=pending_item)
                        print(json.dumps({"msg": "sns alert failed; stored pending retry"}))
                    except (ClientError, BotoCoreError) as ee:
                        print(json.dumps({"msg": "failed to store pending alert (ignored)", "error": str(ee)}))

        return {
            "ok": True,
            "date": today,
            "cost": float(cost),
            "unit": unit,
            "threshold": float(threshold),
            "status": status,
            "minute_ts": minute_ts,
            "mode": granularity,
        }

    except (ClientError, BotoCoreError, KeyError, IndexError, ValueError, TypeError) as e:
        print(json.dumps({
            "msg": "collector error",
            "error": str(e),
            "ts": ts
        }))
        raise