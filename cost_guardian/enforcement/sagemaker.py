"""
SageMaker enforcement: stop notebook instances matching a tag.
"""
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from .base import AbstractEnforcer, EnforcementResult


class SageMakerEnforcer(AbstractEnforcer):
    """Stop SageMaker notebook instances matching enforcement tag."""

    def service_name(self) -> str:
        return "sagemaker"

    def enforce(
        self,
        tag_key: str,
        tag_value: str,
        regions: list[str],
        dry_run: bool,
    ) -> EnforcementResult:
        """
        Find and stop SageMaker notebook instances matching tag.
        """
        total_matched = 0
        total_stopped = 0
        region_details = {}

        for region in regions:
            region_info = {"matched": [], "stopped": [], "error": None}

            try:
                sm = boto3.client("sagemaker", region_name=region)

                # List all notebook instances
                paginator = sm.get_paginator("list_notebook_instances")
                for page in paginator.paginate(StatusEquals="InService"):
                    for instance in page.get("NotebookInstances", []):
                        instance_name = instance.get("NotebookInstanceName")
                        instance_arn = instance.get("NotebookInstanceArn")

                        if not instance_arn:
                            continue

                        try:
                            # List tags for this notebook instance
                            tags_resp = sm.list_tags(ResourceArn=instance_arn)
                            tags = {tag["Key"]: tag["Value"] for tag in tags_resp.get("Tags", [])}

                            if tags.get(tag_key) == tag_value:
                                region_info["matched"].append(instance_name)
                                total_matched += 1

                                if not dry_run:
                                    try:
                                        sm.stop_notebook_instance(
                                            NotebookInstanceName=instance_name
                                        )
                                        region_info["stopped"].append(instance_name)
                                        total_stopped += 1
                                    except (ClientError, BotoCoreError) as e:
                                        pass  # Log and continue

                        except (ClientError, BotoCoreError) as e:
                            pass  # Skip this instance

            except (ClientError, BotoCoreError) as e:
                region_info["error"] = str(e)

            region_details[region] = region_info

        return EnforcementResult(
            service=self.service_name(),
            matched=total_matched,
            stopped=total_stopped,
            details=region_details,
        )
