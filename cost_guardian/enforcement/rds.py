"""
RDS enforcement: stop DB instances and clusters matching a tag.
"""
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from cost_guardian.enforcement.base import AbstractEnforcer, EnforcementResult


class RDSEnforcer(AbstractEnforcer):
    """Stop RDS DB instances and Aurora clusters matching enforcement tag."""

    def service_name(self) -> str:
        return "rds"

    def enforce(
        self,
        tag_key: str,
        tag_value: str,
        regions: list[str],
        dry_run: bool,
    ) -> EnforcementResult:
        """
        Find and stop RDS DB instances and Aurora clusters matching tag.
        """
        total_matched = 0
        total_stopped = 0
        region_details = {}

        for region in regions:
            region_info = {
                "instances": {"matched": [], "stopped": [], "error": None},
                "clusters": {"matched": [], "stopped": [], "error": None},
            }

            try:
                rds = boto3.client("rds", region_name=region)

                # ---- Stop DB instances ----
                instance_arns = []
                try:
                    # List all DB instances and filter by tag
                    paginator = rds.get_paginator("describe_db_instances")
                    for page in paginator.paginate():
                        for instance in page.get("DBInstances", []):
                            instance_arn = instance.get("DBInstanceArn")
                            if instance_arn and instance.get("DBInstanceStatus") in ("available", "storage-optimization"):
                                instance_arns.append(instance_arn)

                    # Filter by tag
                    if instance_arns:
                        tags_resp = rds.list_tags_for_resource(
                            ResourceName=instance_arns[0],  # Just check one to verify API call works
                        )
                        # In practice, we'd need to filter each one, but this is a simplified approach

                    matched_instances = []
                    for instance_arn in instance_arns:
                        try:
                            tags_resp = rds.list_tags_for_resource(ResourceName=instance_arn)
                            tags = {tag["Key"]: tag["Value"] for tag in tags_resp.get("TagList", [])}
                            if tags.get(tag_key) == tag_value:
                                matched_instances.append(instance_arn)
                        except (ClientError, BotoCoreError):
                            pass

                    region_info["instances"]["matched"] = matched_instances
                    total_matched += len(matched_instances)

                    if matched_instances and not dry_run:
                        stopped_instances = []
                        for instance_arn in matched_instances:
                            try:
                                # Extract DB instance identifier from ARN
                                instance_id = instance_arn.split(":")[-1]
                                rds.stop_db_instance(DBInstanceIdentifier=instance_id)
                                stopped_instances.append(instance_id)
                            except (ClientError, BotoCoreError) as e:
                                pass  # Log and continue

                        region_info["instances"]["stopped"] = stopped_instances
                        total_stopped += len(stopped_instances)

                except (ClientError, BotoCoreError) as e:
                    region_info["instances"]["error"] = str(e)

                # ---- Stop Aurora clusters ----
                cluster_arns = []
                try:
                    paginator = rds.get_paginator("describe_db_clusters")
                    for page in paginator.paginate():
                        for cluster in page.get("DBClusters", []):
                            cluster_arn = cluster.get("DBClusterArn")
                            if cluster_arn and cluster.get("Status") in ("available", "storage-optimization"):
                                cluster_arns.append(cluster_arn)

                    matched_clusters = []
                    for cluster_arn in cluster_arns:
                        try:
                            tags_resp = rds.list_tags_for_resource(ResourceName=cluster_arn)
                            tags = {tag["Key"]: tag["Value"] for tag in tags_resp.get("TagList", [])}
                            if tags.get(tag_key) == tag_value:
                                matched_clusters.append(cluster_arn)
                        except (ClientError, BotoCoreError):
                            pass

                    region_info["clusters"]["matched"] = matched_clusters
                    total_matched += len(matched_clusters)

                    if matched_clusters and not dry_run:
                        stopped_clusters = []
                        for cluster_arn in matched_clusters:
                            try:
                                # Extract cluster identifier from ARN
                                cluster_id = cluster_arn.split(":")[-1]
                                rds.stop_db_cluster(DBClusterIdentifier=cluster_id)
                                stopped_clusters.append(cluster_id)
                            except (ClientError, BotoCoreError) as e:
                                pass  # Log and continue

                        region_info["clusters"]["stopped"] = stopped_clusters
                        total_stopped += len(stopped_clusters)

                except (ClientError, BotoCoreError) as e:
                    region_info["clusters"]["error"] = str(e)

            except (ClientError, BotoCoreError) as e:
                region_info["error"] = str(e)

            region_details[region] = region_info

        return EnforcementResult(
            service=self.service_name(),
            matched=total_matched,
            stopped=total_stopped,
            details=region_details,
        )
