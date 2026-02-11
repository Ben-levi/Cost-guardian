# tests/unit/test_journey.py

import os
import unittest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError


def _ce_resp(amount: str, unit: str = "USD", metric: str = "UnblendedCost",
            day_start: str = "2026-02-09", day_end: str = "2026-02-10"):
    return {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": day_start, "End": day_end},
                "Total": {metric: {"Amount": amount, "Unit": unit}},
            }
        ]
    }


class TestJourneyPendingAlertFlow(unittest.TestCase):
    """
    Journey:
      Run #1:
        - No pending exists
        - Daily cost >= threshold -> BREACH
        - sns.publish fails -> store pending item under key=alert_pending
      Run #2:
        - Pending exists, matches topic -> retry sns.publish succeeds -> delete pending
        - Daily cost < threshold -> OK
    """

    @patch.dict(
        os.environ,
        {
            # handler runtime config
            "THRESHOLD": "0.10",
            "COST_EXPLORER_GRANULARITY": "DAILY",
            "COST_EXPLORER_METRIC": "UnblendedCost",

            # tables + topic (required keys in your handler.py)
            "TABLE_NAME": "StateTable",
            "COST_HISTORY_TABLE": "HistoryTable",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:cost-guardian-alerts",

            # make monthly rollup ON to match your defaults/behavior
            "ENABLE_MONTHLY_ROLLUP": "true",

            # keep enforcement off for this journey test
            "ENFORCEMENT_ENABLED": "false",
            "ENFORCEMENT_DRY_RUN": "true",
            "ENFORCEMENT_REGIONS": "",

            # pending behavior (optional, but explicit is clearer)
            "PENDING_ALERT_KEY": "alert_pending",
        },
        clear=False,
    )
    @patch("cost_guardian.handler.boto3.resource")
    @patch("cost_guardian.handler.boto3.client")
    def test_breach_sns_fail_then_retry_clears_pending(
        self, mock_boto3_client, mock_boto3_resource
    ):
        # Import here so patches apply cleanly
        from cost_guardian.handler import handler

        # ---- mocked AWS clients ----
        ce = MagicMock(name="ce")
        sns = MagicMock(name="sns")

        def client_side_effect(service_name, *args, **kwargs):
            if service_name == "ce":
                return ce
            if service_name == "sns":
                return sns
            # handler creates only ce + sns at runtime; enforcement is off
            return MagicMock(name=f"{service_name}_client")

        mock_boto3_client.side_effect = client_side_effect

        # ---- mocked DynamoDB tables ----
        dynamodb = MagicMock(name="dynamodb_resource")
        state_table = MagicMock(name="state_table")
        history_table = MagicMock(name="history_table")

        def table_side_effect(name: str):
            if name == os.environ["TABLE_NAME"]:
                return state_table
            if name == os.environ["COST_HISTORY_TABLE"]:
                return history_table
            raise ValueError(f"Unexpected Table name: {name}")

        dynamodb.Table.side_effect = table_side_effect
        mock_boto3_resource.return_value = dynamodb

        # We don't care about the exact items, just that calls don't explode
        history_table.put_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
        state_table.put_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
        state_table.delete_item.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}

        # ---- Pending reads across runs ----
        # Run #1 start: no pending
        # Run #2 start: pending exists and matches topic
        pending_item = {
            "key": os.environ["PENDING_ALERT_KEY"],
            "topic_arn": os.environ["ALERTS_TOPIC_ARN"],
            "subject": "CostGuardian BREACH",
            "message": "pending message body",
        }
        state_table.get_item.side_effect = [
            {},  # run 1 pending check
            {"Item": pending_item},  # run 2 pending check
        ]

        # ---- Cost Explorer is called twice per run (daily + monthly rollup) ----
        # Run #1 daily: 0.50 (BREACH)
        # Run #1 monthly rollup: anything valid (use 0.50)
        # Run #2 daily: 0.01 (OK)
        # Run #2 monthly rollup: anything valid (use 0.01)
        ce.get_cost_and_usage.side_effect = [
            _ce_resp("0.5"),   # run1 daily
            _ce_resp("0.5"),   # run1 mtd rollup
            _ce_resp("0.01"),  # run2 daily
            _ce_resp("0.01"),  # run2 mtd rollup
        ]

        # ---- SNS publish matches the story ----
        # Run #1 breach publish fails -> handler stores pending
        # Run #2 pending retry publish succeeds -> handler clears pending
        sns.publish.side_effect = [
            ClientError({"Error": {"Code": "InternalError", "Message": "sns down"}}, "Publish"),
            {"MessageId": "ok"},
        ]

        # ---- Run #1 ----
        res1 = handler({}, {})
        self.assertTrue(res1["ok"])
        self.assertEqual(res1["status"], "BREACH")

        # ---- Run #2 ----
        res2 = handler({}, {})
        self.assertTrue(res2["ok"])
        self.assertEqual(res2["status"], "OK")

        # ensure pending was cleared
        state_table.delete_item.assert_called_once()
        state_table.delete_item.assert_called_with(Key={"key": os.environ["PENDING_ALERT_KEY"]})


if __name__ == "__main__":
    unittest.main()
