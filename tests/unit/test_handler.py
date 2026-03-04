import os
import unittest
from unittest.mock import patch, MagicMock
from botocore.exceptions import ClientError, EndpointConnectionError

from cost_guardian.handler import handler


def conditional_check_failed():
    return ClientError(
        error_response={"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
        operation_name="PutItem",
    )


def ce_client_error():
    return ClientError(
        error_response={"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        operation_name="GetCostAndUsage",
    )


def sns_client_error():
    return ClientError(
        error_response={"Error": {"Code": "InternalError", "Message": "sns down"}},
        operation_name="Publish",
    )


def dynamo_throttle_error():
    return ClientError(
        error_response={"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "throttle"}},
        operation_name="PutItem",
    )


class TestIdempotency(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "true",
            "ENABLE_MONTHLY_ROLLUP": "true",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_idempotent_history_put_same_minute(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()
        state_table.get_item.return_value = {}

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory

        ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "0.05", "Unit": "USD"}}}]
        }

        state = {"conditional_calls": 0}

        def put_item_side_effect(*args, **kwargs):
            if "ConditionExpression" in kwargs:
                state["conditional_calls"] += 1
                if state["conditional_calls"] <= 2:
                    return None
                raise conditional_check_failed()
            return None

        history_table.put_item.side_effect = put_item_side_effect

        res1 = handler({}, {})
        res2 = handler({}, {})

        self.assertTrue(res1["ok"])
        self.assertTrue(res2["ok"])
        self.assertEqual(res1["status"], "OK")
        self.assertEqual(res2["status"], "OK")

        self.assertEqual(state_table.put_item.call_count, 2)
        self.assertEqual(history_table.put_item.call_count, 6)

        conditional_calls = [c for c in history_table.put_item.call_args_list if "ConditionExpression" in c.kwargs]
        self.assertEqual(len(conditional_calls), 4)
        for call in conditional_calls:
            self.assertIn("attribute_not_exists", call.kwargs["ConditionExpression"])


class TestErrorProbing(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_cost_explorer_clienterror_raises_and_no_writes(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()
        state_table.get_item.return_value = {}

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory
        ce.get_cost_and_usage.side_effect = ce_client_error()

        with self.assertRaises(ClientError):
            handler({}, {})

        state_table.put_item.assert_not_called()
        history_table.put_item.assert_not_called()
        sns.publish.assert_not_called()

    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_cost_explorer_endpoint_error_raises_and_no_writes(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()
        state_table.get_item.return_value = {}

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory
        ce.get_cost_and_usage.side_effect = EndpointConnectionError(endpoint_url="https://ce.us-east-1.amazonaws.com")

        with self.assertRaises(EndpointConnectionError):
            handler({}, {})

        state_table.put_item.assert_not_called()
        history_table.put_item.assert_not_called()
        sns.publish.assert_not_called()

    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_malformed_cost_explorer_response_raises_and_no_writes(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()
        state_table.get_item.return_value = {}

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory
        ce.get_cost_and_usage.return_value = {"ResultsByTime": []}

        with self.assertRaises(IndexError):
            handler({}, {})

        state_table.put_item.assert_not_called()
        history_table.put_item.assert_not_called()
        sns.publish.assert_not_called()

    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_missing_metric_key_raises_and_no_writes(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()
        state_table.get_item.return_value = {}

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory
        ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [{"Total": {"SomethingElse": {"Amount": "0.05", "Unit": "USD"}}}]
        }

        with self.assertRaises(KeyError):
            handler({}, {})

        state_table.put_item.assert_not_called()
        history_table.put_item.assert_not_called()
        sns.publish.assert_not_called()


class TestPendingRetryAndPartialWrites(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_breach_sns_failure_stores_pending(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()
        state_table.get_item.return_value = {}

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory

        ce.get_cost_and_usage.side_effect = [
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "0.50", "Unit": "USD"}}}]},
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "1.23", "Unit": "USD"}}}]},
        ]

        sns.publish.side_effect = sns_client_error()
        history_table.put_item.return_value = None

        res = handler({}, {})
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "BREACH")

        pending_put = None
        for call in state_table.put_item.call_args_list:
            item = call.kwargs.get("Item") or {}
            if item.get("key") == "alert_pending":
                pending_put = item
                break

        self.assertIsNotNone(pending_put)
        self.assertEqual(pending_put["status"], "PENDING")

    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_next_run_retries_pending_and_clears(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()

        state_table.get_item.return_value = {
            "Item": {
                "key": "alert_pending",
                "topic_arn": "arn:aws:sns:us-east-1:123456789012:topic",
                "subject": "CostGuardian BREACH",
                "message": "pending message",
            }
        }

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory

        ce.get_cost_and_usage.side_effect = [
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "0.01", "Unit": "USD"}}}]},
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "1.23", "Unit": "USD"}}}]},
        ]

        sns.publish.return_value = {"MessageId": "123"}
        history_table.put_item.return_value = None

        res = handler({}, {})
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "OK")

        sns.publish.assert_called()
        # cooldown may add extra delete_item calls; ensure pending is cleared
        state_table.delete_item.assert_any_call(Key={"key": "alert_pending"})

    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_history_write_failure_continues_and_state_still_written(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()
        state_table.get_item.return_value = {}

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory

        ce.get_cost_and_usage.side_effect = [
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "0.05", "Unit": "USD"}}}]},
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "1.23", "Unit": "USD"}}}]},
        ]

        history_table.put_item.side_effect = dynamo_throttle_error()

        res = handler({}, {})
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "OK")
        self.assertTrue(state_table.put_item.called)


class TestMonthlyRollup(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_monthly_rollup_writes_pk_cost_yyyy_mm(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()
        state_table.get_item.return_value = {}

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory

        ce.get_cost_and_usage.side_effect = [
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "0.05", "Unit": "USD"}}}]},
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "1.23", "Unit": "USD"}}}]},
        ]

        history_table.put_item.return_value = None

        res = handler({}, {})
        self.assertTrue(res["ok"])

        rollup_item = None
        for call in history_table.put_item.call_args_list:
            item = call.kwargs.get("Item") or {}
            if item.get("sk") == "ROLLUP" and str(item.get("pk", "")).startswith("COST#"):
                rollup_item = item
                break

        self.assertIsNotNone(rollup_item)
        self.assertEqual(rollup_item["pk"], f"COST#{res['date'][:7]}")
        self.assertEqual(rollup_item["sk"], "ROLLUP")


class TestEnforcement(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
            "ENFORCEMENT_ENABLED": "true",
            "ENFORCEMENT_DRY_RUN": "true",
            "ENFORCEMENT_TAG_KEY": "CostControl",
            "ENFORCEMENT_TAG_VALUE": "StopOnBreach",
            "ENFORCEMENT_REGIONS": "us-east-1",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_enforcement_dry_run_on_breach(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()
        state_table.get_item.return_value = {}

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()
        ec2 = MagicMock()

        def client_factory(service, **kwargs):
            if service == "ce":
                return ce
            if service == "sns":
                return sns
            if service == "ec2":
                return ec2
            raise KeyError(service)

        mock_boto_client.side_effect = client_factory

        ce.get_cost_and_usage.side_effect = [
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "0.50", "Unit": "USD"}}}]},
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "1.23", "Unit": "USD"}}}]},
        ]

        history_table.put_item.return_value = None

        ec2.describe_instances.return_value = {
            "Reservations": [{"Instances": [{"InstanceId": "i-1"}, {"InstanceId": "i-2"}]}]
        }

        res = handler({}, {})
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "BREACH")

        ec2.describe_instances.assert_called()
        ec2.stop_instances.assert_not_called()
        self.assertTrue(sns.publish.called)

    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
            "ENFORCEMENT_ENABLED": "true",
            "ENFORCEMENT_DRY_RUN": "false",
            "ENFORCEMENT_ARMED": "true",  # IMPORTANT: must be armed to stop
            "ENFORCEMENT_TAG_KEY": "CostControl",
            "ENFORCEMENT_TAG_VALUE": "StopOnBreach",
            "ENFORCEMENT_REGIONS": "us-east-1",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_enforcement_stop_on_breach(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()
        state_table.get_item.return_value = {}

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()
        ec2 = MagicMock()

        def client_factory(service, **kwargs):
            if service == "ce":
                return ce
            if service == "sns":
                return sns
            if service == "ec2":
                return ec2
            raise KeyError(service)

        mock_boto_client.side_effect = client_factory

        ce.get_cost_and_usage.side_effect = [
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "0.50", "Unit": "USD"}}}]},
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "1.23", "Unit": "USD"}}}]},
        ]

        history_table.put_item.return_value = None

        ec2.describe_instances.return_value = {"Reservations": [{"Instances": [{"InstanceId": "i-1"}]}]}

        res = handler({}, {})
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "BREACH")

        ec2.stop_instances.assert_called()


class TestThresholdLowBreach(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.01",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
            "ENFORCEMENT_ENABLED": "false",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_low_threshold_triggers_breach_and_publishes_sns(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()
        state_table.get_item.return_value = {}

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory

        ce.get_cost_and_usage.side_effect = [
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "0.05", "Unit": "USD"}}}]},
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "1.23", "Unit": "USD"}}}]},
        ]

        history_table.put_item.return_value = None
        sns.publish.return_value = {"MessageId": "abc"}

        res = handler({}, {})
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "BREACH")

        self.assertTrue(sns.publish.called)
        subjects = [c.kwargs.get("Subject") for c in sns.publish.call_args_list]
        self.assertIn("CostGuardian BREACH", subjects)

        pending_writes = [
            c.kwargs.get("Item", {}).get("key") for c in state_table.put_item.call_args_list if "Item" in c.kwargs
        ]
        self.assertNotIn("alert_pending", pending_writes)


class TestOperationalSimulations(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_pending_retry_publish_fails_keeps_pending(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()

        state_table.get_item.return_value = {
            "Item": {
                "key": "alert_pending",
                "topic_arn": "arn:aws:sns:us-east-1:123456789012:topic",
                "subject": "CostGuardian BREACH",
                "message": "pending message",
            }
        }

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()
        sns.publish.side_effect = sns_client_error()

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory

        ce.get_cost_and_usage.side_effect = [
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "0.01", "Unit": "USD"}}}]},
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "1.23", "Unit": "USD"}}}]},
        ]
        history_table.put_item.return_value = None

        res = handler({}, {})
        self.assertTrue(res["ok"])
        # cooldown may delete last_breach_alert on OK; ensure pending not deleted
        calls = [c.kwargs.get("Key") for c in state_table.delete_item.call_args_list]
        self.assertNotIn({"key": "alert_pending"}, calls)

    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_pending_retry_ignored_if_topic_mismatch(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()

        state_table.get_item.return_value = {
            "Item": {
                "key": "alert_pending",
                "topic_arn": "arn:aws:sns:us-east-1:999999999999:other",
                "subject": "CostGuardian BREACH",
                "message": "pending message",
            }
        }

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory

        ce.get_cost_and_usage.side_effect = [
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "0.01", "Unit": "USD"}}}]},
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "1.23", "Unit": "USD"}}}]},
        ]
        history_table.put_item.return_value = None

        res = handler({}, {})
        self.assertTrue(res["ok"])
        sns.publish.assert_not_called()
        # cooldown may delete last_breach_alert on OK; ensure pending not deleted
        calls = [c.kwargs.get("Key") for c in state_table.delete_item.call_args_list]
        self.assertNotIn({"key": "alert_pending"}, calls)

    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_state_write_failure_is_ignored(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()
        state_table.get_item.return_value = {}

        state_table.put_item.side_effect = dynamo_throttle_error()

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory

        ce.get_cost_and_usage.side_effect = [
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "0.05", "Unit": "USD"}}}]},
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "1.23", "Unit": "USD"}}}]},
        ]
        history_table.put_item.return_value = None

        res = handler({}, {})
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "OK")

    # ✅ NEW: pending retry success clears pending
    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_pending_retry_success_clears_pending(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()

        state_table.get_item.return_value = {
            "Item": {
                "key": "alert_pending",
                "topic_arn": "arn:aws:sns:us-east-1:123456789012:topic",
                "subject": "CostGuardian BREACH",
                "message": "pending message",
            }
        }

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()
        sns.publish.return_value = {"MessageId": "ok"}

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory

        ce.get_cost_and_usage.side_effect = [
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "0.01", "Unit": "USD"}}}]},
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "1.23", "Unit": "USD"}}}]},
        ]
        history_table.put_item.return_value = None

        res = handler({}, {})
        self.assertTrue(res["ok"])
        sns.publish.assert_called()
        # cooldown may add extra delete_item calls; ensure pending cleared
        state_table.delete_item.assert_any_call(Key={"key": "alert_pending"})

    # ✅ NEW: CE failure means no writes (even with pending present)
    @patch.dict(
        os.environ,
        {
            "TABLE_NAME": "state-table",
            "COST_HISTORY_TABLE": "history-table",
            "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
            "THRESHOLD": "0.10",
            "ENABLE_DAILY_PK": "false",
            "ENABLE_MONTHLY_ROLLUP": "true",
        },
        clear=True,
    )
    @patch("boto3.resource")
    @patch("boto3.client")
    def test_ce_failure_no_writes_even_with_pending(self, mock_boto_client, mock_boto_resource):
        state_table = MagicMock()
        history_table = MagicMock()

        # pending exists
        state_table.get_item.return_value = {
            "Item": {
                "key": "alert_pending",
                "topic_arn": "arn:aws:sns:us-east-1:123456789012:topic",
                "subject": "CostGuardian BREACH",
                "message": "pending message",
            }
        }

        dynamodb = MagicMock()
        dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table
        mock_boto_resource.return_value = dynamodb

        ce = MagicMock()
        sns = MagicMock()
        sns.publish.return_value = {"MessageId": "ok"}  # could have retried pending, but CE fails first

        def client_factory(service, **kwargs):
            return {"ce": ce, "sns": sns}[service]

        mock_boto_client.side_effect = client_factory

        ce.get_cost_and_usage.side_effect = ce_client_error()

        with self.assertRaises(ClientError):
            handler({}, {})

        state_table.put_item.assert_not_called()
        history_table.put_item.assert_not_called()