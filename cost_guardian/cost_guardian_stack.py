# cost_guardian_stack.py

from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    RemovalPolicy,
    aws_lambda as _lambda,
    aws_events as events,
    aws_events_targets as targets,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_sns as sns,
)
from constructs import Construct


class CostGuardianStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        table = dynamodb.Table(
            self,
            "CostStateTable",
            partition_key=dynamodb.Attribute(name="key", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        cost_history = dynamodb.Table(
            self,
            "CostHistoryTable",
            table_name="cost_history",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
        )

        enforcement_log = dynamodb.Table(
            self,
            "EnforcementLogTable",
            table_name="enforcement_log",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        alerts_topic = sns.Topic(
            self,
            "AlertsTopic",
            topic_name="cost-guardian-alerts",
        )

        monitor_lambda = _lambda.Function(
            self,
            "MonitorLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset("lambda"),
            environment={
                "TABLE_NAME": table.table_name,
                "THRESHOLD": "0.10",
                "COST_HISTORY_TABLE": cost_history.table_name,
                "ENFORCEMENT_LOG_TABLE": enforcement_log.table_name,
                "ALERTS_TOPIC_ARN": alerts_topic.topic_arn,

                # === CHANGE (already present in your recent diff, kept here) ===
                "COST_EXPLORER_GRANULARITY": "DAILY",
                "COST_EXPLORER_METRIC": "UnblendedCost",
                "HISTORY_TTL_DAYS": "30",
                
                "ENABLE_DAILY_PK": "true",

                # === CHANGE (new) ===
                # Enables "daily partition" write in handler.py:
                # history_table.put_item(pk="COST#YYYY-MM-DD", sk=ts, ...)
                "ENABLE_DAILY_PK": "true",
            },
            timeout=Duration.seconds(90),
            memory_size=256,
        )

        # Explicit read+write grants (no grant_read_write in your lib)
        table.grant_read_data(monitor_lambda)
        table.grant_write_data(monitor_lambda)

        cost_history.grant_read_data(monitor_lambda)
        cost_history.grant_write_data(monitor_lambda)

        enforcement_log.grant_read_data(monitor_lambda)
        enforcement_log.grant_write_data(monitor_lambda)

        alerts_topic.grant_publish(monitor_lambda)

        monitor_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ce:GetCostAndUsage"],
                resources=["*"],
            )
        )

        rule = events.Rule(
            self,
            "MonitorSchedule",
            schedule=events.Schedule.rate(Duration.minutes(15)),
        )
        rule.add_target(targets.LambdaFunction(monitor_lambda))

        CfnOutput(self, "CostStateTableName", value=table.table_name)
        CfnOutput(self, "CostHistoryTableName", value=cost_history.table_name)
        CfnOutput(self, "EnforcementLogTableName", value=enforcement_log.table_name)
        CfnOutput(self, "AlertsTopicArn", value=alerts_topic.topic_arn)
