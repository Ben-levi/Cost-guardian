from constructs import Construct
from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_dynamodb as dynamodb,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_sns as sns,
    aws_events as events,
    aws_events_targets as targets,
    aws_logs as logs,
)


class CostGuardianStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- DynamoDB: state table (pk = key) ---
        state_table = dynamodb.Table(
            self,
            "CostStateTable",
            partition_key=dynamodb.Attribute(name="key", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # --- DynamoDB: cost history table (pk/sk + TTL) ---
        history_table = dynamodb.Table(
            self,
            "CostHistoryTable",
            table_name="cost_history",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # --- DynamoDB: enforcement log table (pk/sk) ---
        enforcement_log_table = dynamodb.Table(
            self,
            "EnforcementLogTable",
            table_name="enforcement_log",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # --- SNS topic ---
        alerts_topic = sns.Topic(
            self,
            "AlertsTopic",
            topic_name="cost-guardian-alerts",
        )

        # --- Lambda ---
        # IMPORTANT: CI was failing because the stack referenced a non-existent "./lambda" folder.
        # Your handler is in "cost_guardian/handler.py", so we package from "cost_guardian".
        monitor_lambda = _lambda.Function(
            self,
            "MonitorLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset("cost_guardian"),
            memory_size=256,
            timeout=Duration.seconds(90),
            log_retention=logs.RetentionDays.TWO_YEARS,
            environment={
                "TABLE_NAME": state_table.table_name,
                "COST_HISTORY_TABLE": history_table.table_name,
                "ENFORCEMENT_LOG_TABLE": enforcement_log_table.table_name,
                "ALERTS_TOPIC_ARN": alerts_topic.topic_arn,
                "THRESHOLD": "0.10",
                "COST_EXPLORER_GRANULARITY": "DAILY",
                "COST_EXPLORER_METRIC": "UnblendedCost",
                "HISTORY_TTL_DAYS": "30",
                "ENABLE_DAILY_PK": "true",
            },
        )

        # Permissions (DynamoDB + SNS + Cost Explorer)
        state_table.grant_read_write_data(monitor_lambda)
        history_table.grant_read_write_data(monitor_lambda)
        enforcement_log_table.grant_read_write_data(monitor_lambda)
        alerts_topic.grant_publish(monitor_lambda)

        monitor_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ce:GetCostAndUsage"],
                resources=["*"],
            )
        )

        # --- Schedule (every 15 minutes) ---
        rule = events.Rule(
            self,
            "MonitorSchedule",
            schedule=events.Schedule.rate(Duration.minutes(15)),
        )
        rule.add_target(targets.LambdaFunction(monitor_lambda))
