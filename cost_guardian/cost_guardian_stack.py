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
                "THRESHOLD": "35.00",
                "COST_EXPLORER_GRANULARITY": "MONTHLY",
                "COST_EXPLORER_METRIC": "UnblendedCost",
                "HISTORY_TTL_DAYS": "30",
                "ENABLE_DAILY_PK": "false",
                "ENABLE_MONTHLY_ROLLUP": "true",
                "MONTHLY_ROLLUP_TTL_DAYS": "400",
                "PENDING_ALERT_KEY": "alert_pending",
                #enforcement defaults can be overridden in Lambda env in console too
                "ENFORCEMENT_ENABLED": "true",
                "ENFORCEMENT_ARMED": "false",
                "ENFORCEMENT_DRY_RUN": "true",
                "ENFORCEMENT_REGIONS": "us-east-1",
                "ENFORCEMENT_TAG_KEY": "CostGuardianManaged",
                "ENFORCEMENT_TAG_VALUE": "true",
            },
        )

        # Permissions (DynamoDB + SNS + Cost Explorer)
        state_table.grant_read_write_data(monitor_lambda)
        history_table.grant_read_write_data(monitor_lambda)
        enforcement_log_table.grant_read_write_data(monitor_lambda)
        alerts_topic.grant_publish(monitor_lambda)

        # Cost Explorer
        monitor_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ce:GetCostAndUsage"],
                resources=["*"],
            )
        )

        # ✅ EC2 permissions needed for enforcement mode
        monitor_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "ec2:DescribeInstances",
                    "ec2:StopInstances",
                ],
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
