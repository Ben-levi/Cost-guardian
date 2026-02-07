import os
import argparse
from unittest.mock import patch, MagicMock
from botocore.exceptions import ClientError

from cost_guardian.handler import handler


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


def main():
    p = argparse.ArgumentParser(description="CostGuardian local simulation (no AWS)")
    p.add_argument("--threshold", default="0.10")
    p.add_argument("--daily-cost", default="0.05")
    p.add_argument("--monthly-cost", default="1.23")
    p.add_argument("--enable-daily-pk", action="store_true")
    p.add_argument("--disable-monthly-rollup", action="store_true")
    p.add_argument("--sns-fail", action="store_true")
    p.add_argument("--history-fail", action="store_true")
    p.add_argument("--state-fail", action="store_true")
    p.add_argument("--enforcement", action="store_true")
    p.add_argument("--enforcement-stop", action="store_true")
    args = p.parse_args()

    enable_monthly = "false" if args.disable_monthly_rollup else "true"
    enforcement_enabled = "true" if args.enforcement else "false"
    enforcement_dry = "false" if (args.enforcement and args.enforcement_stop) else "true"

    env = {
        "TABLE_NAME": "state-table",
        "COST_HISTORY_TABLE": "history-table",
        "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic",
        "THRESHOLD": str(args.threshold),
        "ENABLE_DAILY_PK": "true" if args.enable_daily_pk else "false",
        "ENABLE_MONTHLY_ROLLUP": enable_monthly,
        "ENFORCEMENT_ENABLED": enforcement_enabled,
        "ENFORCEMENT_DRY_RUN": enforcement_dry,
        "ENFORCEMENT_TAG_KEY": "CostControl",
        "ENFORCEMENT_TAG_VALUE": "StopOnBreach",
        "ENFORCEMENT_REGIONS": "us-east-1",
    }

    state_table = MagicMock()
    history_table = MagicMock()
    state_table.get_item.return_value = {}

    dynamodb = MagicMock()
    dynamodb.Table.side_effect = lambda name: state_table if name == "state-table" else history_table

    ce = MagicMock()
    sns = MagicMock()
    ec2 = MagicMock()

    ce.get_cost_and_usage.side_effect = [
        {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": str(args.daily_cost), "Unit": "USD"}}}]},
        {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": str(args.monthly_cost), "Unit": "USD"}}}]},
    ]

    if args.sns_fail:
        sns.publish.side_effect = sns_client_error()
    else:
        sns.publish.return_value = {"MessageId": "ok"}

    if args.history_fail:
        history_table.put_item.side_effect = dynamo_throttle_error()
    else:
        history_table.put_item.return_value = None

    if args.state_fail:
        state_table.put_item.side_effect = dynamo_throttle_error()
    else:
        state_table.put_item.return_value = None

    ec2.describe_instances.return_value = {
        "Reservations": [{"Instances": [{"InstanceId": "i-1"}, {"InstanceId": "i-2"}]}]
    }
    ec2.stop_instances.return_value = {}

    def client_factory(service, **kwargs):
        if service == "ce":
            return ce
        if service == "sns":
            return sns
        if service == "ec2":
            return ec2
        raise KeyError(service)

    with (
        patch.dict(os.environ, env, clear=True),
        patch("boto3.resource", return_value=dynamodb),
        patch("boto3.client", side_effect=client_factory),
    ):
        res = handler({}, {})
        print("\n=== handler() returned ===")
        print(res)
        print("\n=== calls summary ===")
        print(f"sns.publish calls: {sns.publish.call_count}")
        print(f"history_table.put_item calls: {history_table.put_item.call_count}")
        print(f"state_table.put_item calls: {state_table.put_item.call_count}")
        print(f"ec2.describe_instances calls: {ec2.describe_instances.call_count}")
        print(f"ec2.stop_instances calls: {ec2.stop_instances.call_count}")


if __name__ == "__main__":
    main()
