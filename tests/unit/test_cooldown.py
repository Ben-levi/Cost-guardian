import os
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

import cost_guardian.handler as h

def ce_response(amount: str):
    return {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": amount, "Unit": "USD"}}}]}


class TestBreachCooldown(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",  # force breach with 1.00 below
            "COST_EXPLORER_GRANULARITY": "DAILY",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "false",
            "ENFORCEMENT_ENABLED": "false",
            "ALERT_COOLDOWN_MINUTES": "240",
            "ALERT_COOLDOWN_KEY": "last_breach_alert",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_breach_alert_suppressed_within_cooldown(self, mock_boto_client, mock_boto_resource):
        # In-memory "state table"
        state_store = {}

        state_table = MagicMock()
        history_table = MagicMock()

        def get_item_side_effect(Key):
            k = Key["key"]
            return {"Item": state_store.get(k)} if k in state_store else {}

        def put_item_side_effect(Item, **kwargs):
            # state table write
            if Item.get("key"):
                state_store[Item["key"]] = Item
            return None

        def delete_item_side_effect(Key):
            state_store.pop(Key["key"], None)
            return None

        state_table.get_item.side_effect = get_item_side_effect
        state_table.put_item.side_effect = put_item_side_effect
        state_table.delete_item.side_effect = delete_item_side_effect

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory
        ce.get_cost_and_usage.return_value = ce_response("1.00")  # breach

        # Freeze time so "cooldown" definitely applies between calls
        fixed_now = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)
        with patch.object(h, "_utc_now", return_value=fixed_now):
            r1 = h.handler({}, {})
            r2 = h.handler({}, {})

        self.assertTrue(r1["ok"])
        self.assertTrue(r2["ok"])
        self.assertEqual(r1["status"], "BREACH")
        self.assertEqual(r2["status"], "BREACH")

        # SNS should publish only once due to cooldown
        self.assertEqual(sns.publish.call_count, 1)