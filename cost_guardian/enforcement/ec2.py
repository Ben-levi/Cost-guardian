"""
EC2 instance enforcement: stop instances matching a tag.
"""
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from .base import AbstractEnforcer, EnforcementResult


class EC2Enforcer(AbstractEnforcer):
    """Stop EC2 instances matching enforcement tag."""

    def service_name(self) -> str:
        return "ec2"

    def enforce(
        self,
        tag_key: str,
        tag_value: str,
        regions: list[str],
        dry_run: bool,
    ) -> EnforcementResult:
        """
        Find and stop EC2 instances matching tag_key=tag_value in all regions.
        """
        total_matched = 0
        total_stopped = 0
        region_details = {}

        for region in regions:
            region_info = {"matched": [], "stopped": [], "error": None}

            try:
                ec2 = boto3.client("ec2", region_name=region)

                resp = ec2.describe_instances(
                    Filters=[
                        {"Name": f"tag:{tag_key}", "Values": [tag_value]},
                        {"Name": "instance-state-name", "Values": ["running"]},
                    ]
                )

                instance_ids = []
                for reservation in resp.get("Reservations", []):
                    for inst in reservation.get("Instances", []):
                        iid = inst.get("InstanceId")
                        if iid:
                            instance_ids.append(iid)

                region_info["matched"] = instance_ids
                total_matched += len(instance_ids)

                if instance_ids and not dry_run:
                    ec2.stop_instances(InstanceIds=instance_ids)
                    region_info["stopped"] = list(instance_ids)
                    total_stopped += len(instance_ids)

            except (ClientError, BotoCoreError) as e:
                region_info["error"] = str(e)

            region_details[region] = region_info

        return EnforcementResult(
            service=self.service_name(),
            matched=total_matched,
            stopped=total_stopped,
            details=region_details,
        )
