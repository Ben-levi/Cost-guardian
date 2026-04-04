"""
ECS enforcement: scale services to 0 matching a tag.
"""
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from cost_guardian.enforcement.base import AbstractEnforcer, EnforcementResult


class ECSEnforcer(AbstractEnforcer):
    """Scale ECS services to 0 (desiredCount) matching enforcement tag."""

    def service_name(self) -> str:
        return "ecs"

    def enforce(
        self,
        tag_key: str,
        tag_value: str,
        regions: list[str],
        dry_run: bool,
    ) -> EnforcementResult:
        """
        Find and scale ECS services to 0 matching tag.
        NOTE: This should ideally record previousDesiredCount in schedule_state table
        for later restoration, but that requires handler context.
        """
        total_matched = 0
        total_stopped = 0
        region_details = {}

        for region in regions:
            region_info = {"matched": [], "stopped": [], "error": None}

            try:
                ecs = boto3.client("ecs", region_name=region)

                # List all clusters
                clusters_resp = ecs.list_clusters(maxResults=100)
                cluster_arns = clusters_resp.get("clusterArns", [])

                while "nextToken" in clusters_resp:
                    clusters_resp = ecs.list_clusters(
                        maxResults=100,
                        nextToken=clusters_resp["nextToken"],
                    )
                    cluster_arns.extend(clusters_resp.get("clusterArns", []))

                # For each cluster, list services and filter by tag
                for cluster_arn in cluster_arns:
                    try:
                        services_resp = ecs.list_services(
                            cluster=cluster_arn,
                            maxResults=100,
                        )
                        service_arns = services_resp.get("serviceArns", [])

                        while "nextToken" in services_resp:
                            services_resp = ecs.list_services(
                                cluster=cluster_arn,
                                maxResults=100,
                                nextToken=services_resp["nextToken"],
                            )
                            service_arns.extend(services_resp.get("serviceArns", []))

                        if not service_arns:
                            continue

                        # Describe services to get tags
                        services = ecs.describe_services(
                            cluster=cluster_arn,
                            services=service_arns,
                        ).get("services", [])

                        for service in services:
                            service_arn = service.get("serviceArn")
                            tags = {tag["key"]: tag["value"] for tag in service.get("tags", [])}

                            if tags.get(tag_key) == tag_value and service.get("desiredCount", 0) > 0:
                                region_info["matched"].append(service_arn)
                                total_matched += 1

                                if not dry_run:
                                    try:
                                        ecs.update_service(
                                            cluster=cluster_arn,
                                            service=service_arn,
                                            desiredCount=0,
                                        )
                                        region_info["stopped"].append(service_arn)
                                        total_stopped += 1
                                    except (ClientError, BotoCoreError) as e:
                                        pass  # Log and continue

                    except (ClientError, BotoCoreError) as e:
                        pass  # Continue to next cluster

            except (ClientError, BotoCoreError) as e:
                region_info["error"] = str(e)

            region_details[region] = region_info

        return EnforcementResult(
            service=self.service_name(),
            matched=total_matched,
            stopped=total_stopped,
            details=region_details,
        )
