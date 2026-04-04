"""
Lambda enforcement: set reserved concurrent executions to 0 on functions matching a tag.
"""
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from .base import AbstractEnforcer, EnforcementResult


class LambdaEnforcer(AbstractEnforcer):
    """Set Lambda concurrency limit to 0 on functions matching enforcement tag."""

    def service_name(self) -> str:
        return "lambda"

    def enforce(
        self,
        tag_key: str,
        tag_value: str,
        regions: list[str],
        dry_run: bool,
    ) -> EnforcementResult:
        """
        Find and set Lambda function concurrency to 0 matching tag.
        """
        total_matched = 0
        total_stopped = 0
        region_details = {}

        for region in regions:
            region_info = {"matched": [], "stopped": [], "error": None}

            try:
                lambda_client = boto3.client("lambda", region_name=region)

                # List all functions
                paginator = lambda_client.get_paginator("list_functions")
                for page in paginator.paginate():
                    for func in page.get("Functions", []):
                        func_name = func.get("FunctionName")
                        func_arn = func.get("FunctionArn")

                        if not func_arn:
                            continue

                        try:
                            # List tags for this function
                            tags_resp = lambda_client.list_tags(Resource=func_arn)
                            tags = tags_resp.get("Tags", {})

                            if tags.get(tag_key) == tag_value:
                                region_info["matched"].append(func_name)
                                total_matched += 1

                                if not dry_run:
                                    try:
                                        lambda_client.put_function_concurrency(
                                            FunctionName=func_name,
                                            ReservedConcurrentExecutions=0,
                                        )
                                        region_info["stopped"].append(func_name)
                                        total_stopped += 1
                                    except (ClientError, BotoCoreError) as e:
                                        pass  # Log and continue

                        except (ClientError, BotoCoreError) as e:
                            pass  # Skip this function

            except (ClientError, BotoCoreError) as e:
                region_info["error"] = str(e)

            region_details[region] = region_info

        return EnforcementResult(
            service=self.service_name(),
            matched=total_matched,
            stopped=total_stopped,
            details=region_details,
        )
