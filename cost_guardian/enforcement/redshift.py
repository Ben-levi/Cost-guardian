"""
Redshift enforcement: pause clusters matching a tag.
"""
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from .base import AbstractEnforcer, EnforcementResult


class RedshiftEnforcer(AbstractEnforcer):
    """Pause Redshift clusters matching enforcement tag."""

    def service_name(self) -> str:
        return "redshift"

    def enforce(
        self,
        tag_key: str,
        tag_value: str,
        regions: list[str],
        dry_run: bool,
    ) -> EnforcementResult:
        """
        Find and pause Redshift clusters matching tag.
        """
        total_matched = 0
        total_stopped = 0
        region_details = {}

        for region in regions:
            region_info = {"matched": [], "stopped": [], "error": None}

            try:
                redshift = boto3.client("redshift", region_name=region)

                # Describe all clusters
                clusters_resp = redshift.describe_clusters()
                clusters = clusters_resp.get("Clusters", [])

                # Handle pagination if necessary
                marker = clusters_resp.get("Marker")
                while marker:
                    clusters_resp = redshift.describe_clusters(Marker=marker)
                    clusters.extend(clusters_resp.get("Clusters", []))
                    marker = clusters_resp.get("Marker")

                for cluster in clusters:
                    cluster_id = cluster.get("ClusterIdentifier")
                    cluster_status = cluster.get("ClusterStatus")

                    # Only process available clusters
                    if cluster_status not in ("available", "available,prep-for-resize", "available,resize-cleanup"):
                        continue

                    try:
                        # List tags for this cluster
                        tags_resp = redshift.describe_tags(ResourceName=f"arn:aws:redshift:{region}:*:cluster:{cluster_id}")
                        tags = {tag["Key"]: tag["Value"] for tag in tags_resp.get("TaggedResources", [])[0].get("Tags", []) if tags_resp.get("TaggedResources")}

                        if tags.get(tag_key) == tag_value:
                            region_info["matched"].append(cluster_id)
                            total_matched += 1

                            if not dry_run:
                                try:
                                    redshift.pause_cluster(ClusterIdentifier=cluster_id)
                                    region_info["stopped"].append(cluster_id)
                                    total_stopped += 1
                                except (ClientError, BotoCoreError) as e:
                                    pass  # Log and continue

                    except (ClientError, BotoCoreError) as e:
                        pass  # Skip this cluster

            except (ClientError, BotoCoreError) as e:
                region_info["error"] = str(e)

            region_details[region] = region_info

        return EnforcementResult(
            service=self.service_name(),
            matched=total_matched,
            stopped=total_stopped,
            details=region_details,
        )
