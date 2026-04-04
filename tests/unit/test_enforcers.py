"""
Unit tests for cost_guardian.enforcement module.
"""
import unittest
from unittest.mock import patch, MagicMock, call
from botocore.exceptions import ClientError

from cost_guardian.enforcement import EnforcementOrchestrator
from cost_guardian.enforcement.base import AbstractEnforcer, EnforcementResult
from cost_guardian.enforcement.ec2 import EC2Enforcer
from cost_guardian.enforcement.rds import RDSEnforcer
from cost_guardian.enforcement.ecs import ECSEnforcer
from cost_guardian.enforcement.sagemaker import SageMakerEnforcer
from cost_guardian.enforcement.redshift import RedshiftEnforcer
from cost_guardian.enforcement.lambda_enforcer import LambdaEnforcer


class TestEnforcementResult(unittest.TestCase):
    """Test EnforcementResult dataclass."""

    def test_enforcement_result_creation(self):
        result = EnforcementResult(
            service="ec2",
            matched=5,
            stopped=3,
        )
        self.assertEqual(result.service, "ec2")
        self.assertEqual(result.matched, 5)
        self.assertEqual(result.stopped, 3)
        self.assertIsNone(result.error)
        self.assertEqual(result.details, {})

    def test_enforcement_result_with_error(self):
        result = EnforcementResult(
            service="rds",
            error="Access Denied",
        )
        self.assertEqual(result.service, "rds")
        self.assertEqual(result.error, "Access Denied")


class TestEnforcementOrchestrator(unittest.TestCase):
    """Test EnforcementOrchestrator."""

    def setUp(self):
        self.orchestrator = EnforcementOrchestrator()

    def test_orchestrator_registers_builtin_enforcers(self):
        """Verify all built-in enforcers are registered."""
        self.assertIsNotNone(self.orchestrator.get_enforcer("ec2"))
        self.assertIsNotNone(self.orchestrator.get_enforcer("rds"))
        self.assertIsNotNone(self.orchestrator.get_enforcer("ecs"))
        self.assertIsNotNone(self.orchestrator.get_enforcer("sagemaker"))
        self.assertIsNotNone(self.orchestrator.get_enforcer("redshift"))
        self.assertIsNotNone(self.orchestrator.get_enforcer("lambda"))

    def test_get_nonexistent_enforcer(self):
        enforcer = self.orchestrator.get_enforcer("nonexistent")
        self.assertIsNone(enforcer)

    def test_register_custom_enforcer(self):
        """Test registering a custom enforcer."""
        class CustomEnforcer(AbstractEnforcer):
            def service_name(self):
                return "custom"

            def enforce(self, tag_key, tag_value, regions, dry_run):
                return EnforcementResult(service="custom", matched=0)

        custom = CustomEnforcer()
        self.orchestrator.register_enforcer(custom)
        self.assertEqual(self.orchestrator.get_enforcer("custom"), custom)

    @patch("boto3.client")
    def test_enforce_all_single_service(self, mock_boto3_client):
        """Test enforce_all with a single service."""
        results = self.orchestrator.enforce_all(
            services=["ec2"],
            tag_key="CostGuardian",
            tag_value="managed",
            regions=["us-east-1"],
            dry_run=True,
        )

        self.assertIn("ec2", results)
        self.assertEqual(results["ec2"].service, "ec2")

    @patch("boto3.client")
    def test_enforce_all_multiple_services(self, mock_boto3_client):
        """Test enforce_all with multiple services."""
        results = self.orchestrator.enforce_all(
            services=["ec2", "rds"],
            tag_key="CostGuardian",
            tag_value="managed",
            regions=["us-east-1"],
            dry_run=True,
        )

        self.assertIn("ec2", results)
        self.assertIn("rds", results)

    def test_enforce_all_unknown_service_skipped(self):
        """Test that unknown services are skipped."""
        results = self.orchestrator.enforce_all(
            services=["nonexistent"],
            tag_key="tag",
            tag_value="value",
            regions=[],
            dry_run=True,
        )

        self.assertEqual(len(results), 0)


class TestEC2Enforcer(unittest.TestCase):
    """Test EC2Enforcer."""

    @patch("boto3.client")
    def test_ec2_enforcer_service_name(self, mock_boto3):
        enforcer = EC2Enforcer()
        self.assertEqual(enforcer.service_name(), "ec2")

    @patch("boto3.client")
    def test_ec2_enforcer_dry_run(self, mock_boto3_client):
        """Test EC2 enforcer in dry-run mode."""
        mock_ec2 = MagicMock()
        mock_boto3_client.return_value = mock_ec2

        mock_ec2.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {"InstanceId": "i-12345", "Tags": [{"Key": "CostGuardian", "Value": "true"}]}
                    ]
                }
            ]
        }

        enforcer = EC2Enforcer()
        result = enforcer.enforce(
            tag_key="CostGuardian",
            tag_value="true",
            regions=["us-east-1"],
            dry_run=True,
        )

        self.assertEqual(result.service, "ec2")
        self.assertEqual(result.matched, 1)
        self.assertEqual(result.stopped, 0)  # dry_run=True, so nothing actually stopped
        mock_ec2.stop_instances.assert_not_called()

    @patch("boto3.client")
    def test_ec2_enforcer_actual_stop(self, mock_boto3_client):
        """Test EC2 enforcer actually stopping instances."""
        mock_ec2 = MagicMock()
        mock_boto3_client.return_value = mock_ec2

        mock_ec2.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {"InstanceId": "i-12345"},
                        {"InstanceId": "i-67890"},
                    ]
                }
            ]
        }

        enforcer = EC2Enforcer()
        result = enforcer.enforce(
            tag_key="CostGuardian",
            tag_value="true",
            regions=["us-east-1"],
            dry_run=False,
        )

        self.assertEqual(result.matched, 2)
        self.assertEqual(result.stopped, 2)
        mock_ec2.stop_instances.assert_called_once_with(InstanceIds=["i-12345", "i-67890"])

    @patch("boto3.client")
    def test_ec2_enforcer_handles_client_error(self, mock_boto3_client):
        """Test EC2 enforcer handles API errors gracefully."""
        mock_ec2 = MagicMock()
        mock_boto3_client.return_value = mock_ec2

        mock_ec2.describe_instances.side_effect = ClientError(
            error_response={"Error": {"Code": "AccessDenied"}},
            operation_name="DescribeInstances",
        )

        enforcer = EC2Enforcer()
        result = enforcer.enforce(
            tag_key="tag",
            tag_value="value",
            regions=["us-east-1"],
            dry_run=True,
        )

        self.assertEqual(result.matched, 0)
        self.assertEqual(result.stopped, 0)
        self.assertIn("AccessDenied", result.details["us-east-1"]["error"])


class TestRDSEnforcer(unittest.TestCase):
    """Test RDSEnforcer."""

    @patch("boto3.client")
    def test_rds_enforcer_service_name(self, mock_boto3):
        enforcer = RDSEnforcer()
        self.assertEqual(enforcer.service_name(), "rds")

    @patch("boto3.client")
    def test_rds_enforcer_dry_run(self, mock_boto3_client):
        """Test RDS enforcer in dry-run mode."""
        mock_rds = MagicMock()
        mock_boto3_client.return_value = mock_rds

        mock_rds.get_paginator.return_value.paginate.return_value = [
            {
                "DBInstances": [
                    {
                        "DBInstanceArn": "arn:aws:rds:us-east-1:123:db:mydb",
                        "DBInstanceStatus": "available",
                    }
                ]
            }
        ]

        mock_rds.list_tags_for_resource.return_value = {
            "TagList": [{"Key": "CostGuardian", "Value": "true"}]
        }

        enforcer = RDSEnforcer()
        result = enforcer.enforce(
            tag_key="CostGuardian",
            tag_value="true",
            regions=["us-east-1"],
            dry_run=True,
        )

        self.assertEqual(result.service, "rds")
        # Note: The RDS enforcer has complex tag lookup logic
        # In dry-run, it should match but not stop
        mock_rds.stop_db_instance.assert_not_called()


class TestECSEnforcer(unittest.TestCase):
    """Test ECSEnforcer."""

    @patch("boto3.client")
    def test_ecs_enforcer_service_name(self, mock_boto3):
        enforcer = ECSEnforcer()
        self.assertEqual(enforcer.service_name(), "ecs")


class TestSageMakerEnforcer(unittest.TestCase):
    """Test SageMakerEnforcer."""

    @patch("boto3.client")
    def test_sagemaker_enforcer_service_name(self, mock_boto3):
        enforcer = SageMakerEnforcer()
        self.assertEqual(enforcer.service_name(), "sagemaker")


class TestRedshiftEnforcer(unittest.TestCase):
    """Test RedshiftEnforcer."""

    @patch("boto3.client")
    def test_redshift_enforcer_service_name(self, mock_boto3):
        enforcer = RedshiftEnforcer()
        self.assertEqual(enforcer.service_name(), "redshift")


class TestLambdaEnforcer(unittest.TestCase):
    """Test LambdaEnforcer."""

    @patch("boto3.client")
    def test_lambda_enforcer_service_name(self, mock_boto3):
        enforcer = LambdaEnforcer()
        self.assertEqual(enforcer.service_name(), "lambda")

    @patch("boto3.client")
    def test_lambda_enforcer_dry_run(self, mock_boto3_client):
        """Test Lambda enforcer in dry-run mode."""
        mock_lambda = MagicMock()
        mock_boto3_client.return_value = mock_lambda

        mock_lambda.get_paginator.return_value.paginate.return_value = [
            {
                "Functions": [
                    {
                        "FunctionName": "my-function",
                        "FunctionArn": "arn:aws:lambda:us-east-1:123:function:my-function",
                    }
                ]
            }
        ]

        mock_lambda.list_tags.return_value = {
            "Tags": {"CostGuardian": "true"}
        }

        enforcer = LambdaEnforcer()
        result = enforcer.enforce(
            tag_key="CostGuardian",
            tag_value="true",
            regions=["us-east-1"],
            dry_run=True,
        )

        self.assertEqual(result.service, "lambda")
        self.assertGreater(result.matched, 0)
        mock_lambda.put_function_concurrency.assert_not_called()


if __name__ == "__main__":
    unittest.main()
