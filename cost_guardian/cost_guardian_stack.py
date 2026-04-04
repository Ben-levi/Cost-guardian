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
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
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

        # --- DynamoDB: schedule state table (for ECS/RDS restart state restoration) ---
        schedule_state_table = dynamodb.Table(
            self,
            "ScheduleStateTable",
            table_name="schedule_state",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
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
            tracing=_lambda.Tracing.ACTIVE,
            environment={
                "CONFIG_REV": "3",
                "ALERT_COOLDOWN_MINUTES": "240",
                "ALERT_COOLDOWN_KEY": "last_breach_alert",
                "TABLE_NAME": state_table.table_name,
                "COST_HISTORY_TABLE": history_table.table_name,
                "ENFORCEMENT_LOG_TABLE": enforcement_log_table.table_name,
                "SCHEDULE_STATE_TABLE": schedule_state_table.table_name,
                "ALERTS_TOPIC_ARN": alerts_topic.topic_arn,
                "THRESHOLD": "0.01",
                "COST_EXPLORER_GRANULARITY": "MONTHLY",
                "COST_EXPLORER_METRIC": "UnblendedCost",
                "HISTORY_TTL_DAYS": "30",
                "ENABLE_DAILY_PK": "false",
                "ENABLE_MONTHLY_ROLLUP": "true",
                "MONTHLY_ROLLUP_TTL_DAYS": "400",
                "PENDING_ALERT_KEY": "alert_pending",
                # Enforcement defaults (can be overridden in Lambda env in console)
                "ENFORCEMENT_ENABLED": "true",
                "ENFORCEMENT_ARMED": "false",
                "ENFORCEMENT_DRY_RUN": "true",
                "ENFORCEMENT_REGIONS": "us-east-1",
                "ENFORCEMENT_TAG_KEY": "CostGuardianManaged",
                "ENFORCEMENT_TAG_VALUE": "true",
                "ENFORCEMENT_SERVICES": "ec2",  # Comma-separated: ec2,rds,ecs,sagemaker,redshift,lambda
                # Notification channels (comma-separated: sns,slack,teams,discord,pagerduty)
                "NOTIFICATION_CHANNELS": "sns",
            },
        )

        # Permissions (DynamoDB + SNS + Cost Explorer)
        state_table.grant_read_write_data(monitor_lambda)
        history_table.grant_read_write_data(monitor_lambda)
        enforcement_log_table.grant_read_write_data(monitor_lambda)
        schedule_state_table.grant_read_write_data(monitor_lambda)
        alerts_topic.grant_publish(monitor_lambda)

        # Cost Explorer
        monitor_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ce:GetCostAndUsage"],
                resources=["*"],
            )
        )

        # ✅ EC2 enforcement permissions
        monitor_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "ec2:DescribeInstances",
                    "ec2:StopInstances",
                ],
                resources=["*"],
            )
        )

        # ✅ RDS enforcement permissions (DB instances & clusters)
        monitor_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "rds:DescribeDBInstances",
                    "rds:DescribeDBClusters",
                    "rds:StopDBInstance",
                    "rds:StopDBCluster",
                    "rds:ListTagsForResource",
                ],
                resources=["*"],
            )
        )

        # ✅ ECS enforcement permissions (scale services to 0)
        monitor_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "ecs:ListClusters",
                    "ecs:ListServices",
                    "ecs:DescribeServices",
                    "ecs:UpdateService",
                ],
                resources=["*"],
            )
        )

        # ✅ SageMaker enforcement permissions (stop notebooks)
        monitor_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "sagemaker:ListNotebookInstances",
                    "sagemaker:StopNotebookInstance",
                    "sagemaker:ListTags",
                ],
                resources=["*"],
            )
        )

        # ✅ Redshift enforcement permissions (pause clusters)
        monitor_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "redshift:DescribeClusters",
                    "redshift:PauseCluster",
                    "redshift:DescribeTags",
                ],
                resources=["*"],
            )
        )

        # ✅ Lambda enforcement permissions (set concurrency limit to 0)
        monitor_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "lambda:ListFunctions",
                    "lambda:PutFunctionConcurrency",
                    "lambda:ListTags",
                ],
                resources=["*"],
            )
        )

        # ✅ SSM Parameter Store access (for secret webhook URLs)
        monitor_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "ssm:GetParameter",
                ],
                resources=["arn:aws:ssm:*:*:parameter/cost-guardian/*"],
            )
        )

        # --- CloudWatch Metric Filters ---
        # Extract CostUSD from "cost-guardian collected" logs
        mf_cost = logs.MetricFilter(
            self,
            "MfCostUSD",
            log_group=monitor_lambda.log_group,
            metric_namespace="CostGuardian",
            metric_name="CostUSD",
            filter_pattern=logs.FilterPattern.string_value("$.msg", "=", "cost-guardian collected"),
            metric_value="$.cost",
            default_value=0,
        )

        # Extract BreachDetected from "cost-guardian collected" with status=BREACH
        mf_breach = logs.MetricFilter(
            self,
            "MfBreachDetected",
            log_group=monitor_lambda.log_group,
            metric_namespace="CostGuardian",
            metric_name="BreachDetected",
            filter_pattern=logs.FilterPattern.all(
                logs.FilterPattern.string_value("$.msg", "=", "cost-guardian collected"),
                logs.FilterPattern.string_value("$.status", "=", "BREACH"),
            ),
            metric_value="1",
            default_value=0,
        )

        # Extract CollectorError from "collector error" logs
        mf_error = logs.MetricFilter(
            self,
            "MfCollectorError",
            log_group=monitor_lambda.log_group,
            metric_namespace="CostGuardian",
            metric_name="CollectorError",
            filter_pattern=logs.FilterPattern.string_value("$.msg", "=", "collector error"),
            metric_value="1",
            default_value=0,
        )

        # Extract AlertSent from "sns alert published" logs
        mf_alert_sent = logs.MetricFilter(
            self,
            "MfAlertSent",
            log_group=monitor_lambda.log_group,
            metric_namespace="CostGuardian",
            metric_name="AlertSent",
            filter_pattern=logs.FilterPattern.string_value("$.msg", "=", "sns alert published"),
            metric_value="1",
            default_value=0,
        )

        # Extract AlertSuppressed from "breach alert suppressed by cooldown" logs
        mf_alert_suppressed = logs.MetricFilter(
            self,
            "MfAlertSuppressed",
            log_group=monitor_lambda.log_group,
            metric_namespace="CostGuardian",
            metric_name="AlertSuppressed",
            filter_pattern=logs.FilterPattern.string_value("$.msg", "=", "breach alert suppressed by cooldown"),
            metric_value="1",
            default_value=0,
        )

        # Extract EnforcementStopped from "enforcement result" logs
        mf_enforcement = logs.MetricFilter(
            self,
            "MfEnforcementStopped",
            log_group=monitor_lambda.log_group,
            metric_namespace="CostGuardian",
            metric_name="EnforcementStopped",
            filter_pattern=logs.FilterPattern.string_value("$.msg", "=", "enforcement result"),
            metric_value="$.result.total_stopped",
            default_value=0,
        )

        # --- CloudWatch Alarms ---
        # Lambda Errors
        alarm_lambda_errors = monitor_lambda.metric_errors(
            statistic="Sum",
            period=Duration.minutes(1),
        ).create_alarm(
            self,
            "LambdaErrors",
            threshold=1,
            evaluation_periods=1,
            alarm_name="cost-guardian-lambda-errors",
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        alarm_lambda_errors.add_alarm_action(cw_actions.SnsAction(alerts_topic))

        # Lambda Throttles
        alarm_lambda_throttles = monitor_lambda.metric_throttles(
            statistic="Sum",
            period=Duration.minutes(1),
        ).create_alarm(
            self,
            "LambdaThrottles",
            threshold=1,
            evaluation_periods=1,
            alarm_name="cost-guardian-lambda-throttles",
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        alarm_lambda_throttles.add_alarm_action(cw_actions.SnsAction(alerts_topic))

        # Lambda Duration p95 (75 sec of 90 sec timeout)
        alarm_lambda_duration = monitor_lambda.metric_duration(
            statistic="p95",
            period=Duration.minutes(1),
        ).create_alarm(
            self,
            "LambdaDuration",
            threshold=75000,  # 75 seconds in ms
            evaluation_periods=3,
            alarm_name="cost-guardian-lambda-duration-high",
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        alarm_lambda_duration.add_alarm_action(cw_actions.SnsAction(alerts_topic))

        # Breach Detected
        alarm_breach = cw.Alarm(
            self,
            "AlarmBreachDetected",
            metric=mf_breach.metric(),
            threshold=1,
            evaluation_periods=1,
            alarm_name="cost-guardian-breach-detected",
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        alarm_breach.add_alarm_action(cw_actions.SnsAction(alerts_topic))

        # Collector Error
        alarm_collector_error = cw.Alarm(
            self,
            "AlarmCollectorError",
            metric=mf_error.metric(),
            threshold=1,
            evaluation_periods=1,
            alarm_name="cost-guardian-collector-error",
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        alarm_collector_error.add_alarm_action(cw_actions.SnsAction(alerts_topic))

        # DynamoDB State Table Errors
        alarm_ddb_errors = state_table.metric_system_errors_for_operations(
            operations=[dynamodb.Operation.GET_ITEM, dynamodb.Operation.PUT_ITEM, dynamodb.Operation.DELETE_ITEM],
            statistic="Sum",
            period=Duration.minutes(5),
        ).create_alarm(
            self,
            "DDBStateErrors",
            threshold=1,
            evaluation_periods=1,
            alarm_name="cost-guardian-ddb-state-errors",
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        alarm_ddb_errors.add_alarm_action(cw_actions.SnsAction(alerts_topic))

        # --- CloudWatch Dashboard ---
        dashboard = cw.Dashboard(
            self,
            "CostGuardianDashboard",
            dashboard_name="CostGuardian",
        )

        # Row 1: Lambda Health (Errors, Throttles, Duration)
        dashboard.add_widgets(
            cw.GraphWidget(
                title="Lambda Health Status",
                left=[
                    monitor_lambda.metric_errors(period=Duration.minutes(1)),
                    monitor_lambda.metric_throttles(period=Duration.minutes(1)),
                ],
                right=[
                    monitor_lambda.metric_duration(statistic="p95", period=Duration.minutes(1)),
                ],
                height=6,
                width=12,
            ),
        )

        # Row 2: AWS Cost (USD)
        dashboard.add_widgets(
            cw.GraphWidget(
                title="Cost Guardian — AWS Cost (USD)",
                left=[mf_cost.metric()],
                height=6,
                width=12,
                period=Duration.hours(24),
            ),
        )

        # Row 3: Lambda Invocations & Errors
        dashboard.add_widgets(
            cw.GraphWidget(
                title="Lambda Invocations & Errors",
                left=[
                    monitor_lambda.metric_invocations(period=Duration.minutes(5)),
                    monitor_lambda.metric_errors(period=Duration.minutes(5)),
                ],
                height=6,
                width=12,
            ),
        )

        # Row 4: Lambda Duration Distribution
        dashboard.add_widgets(
            cw.GraphWidget(
                title="Lambda Duration (p50/p95/p99)",
                left=[
                    monitor_lambda.metric_duration(statistic="p50", period=Duration.minutes(5)),
                    monitor_lambda.metric_duration(statistic="p95", period=Duration.minutes(5)),
                    monitor_lambda.metric_duration(statistic="p99", period=Duration.minutes(5)),
                ],
                height=6,
                width=12,
            ),
        )

        # Row 5: Breach & Alert Metrics
        dashboard.add_widgets(
            cw.SingleValueWidget(
                title="Breach & Alerts (24h)",
                metrics=[
                    mf_breach.metric(statistic="Sum", period=Duration.hours(24)),
                    mf_alert_sent.metric(statistic="Sum", period=Duration.hours(24)),
                    mf_alert_suppressed.metric(statistic="Sum", period=Duration.hours(24)),
                ],
                height=6,
                width=12,
            ),
        )

        # Row 6: Enforcement Metrics
        dashboard.add_widgets(
            cw.SingleValueWidget(
                title="Enforcement Stopped Instances (24h)",
                metrics=[
                    mf_enforcement.metric(statistic="Sum", period=Duration.hours(24)),
                ],
                height=6,
                width=12,
            ),
        )

        # --- Schedule (every 15 minutes) ---
        rule = events.Rule(
            self,
            "MonitorSchedule",
            schedule=events.Schedule.rate(Duration.minutes(15)),
        )
        rule.add_target(targets.LambdaFunction(monitor_lambda))
