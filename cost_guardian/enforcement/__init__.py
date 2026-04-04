"""
Enforcement orchestrator: coordinates multiple enforcement plugins.
"""
from typing import Dict, List, Optional
from cost_guardian.enforcement.base import AbstractEnforcer, EnforcementResult


class EnforcementOrchestrator:
    """Loads and orchestrates multiple enforcement plugins."""

    def __init__(self):
        # Registry of available enforcers
        self._enforcers: Dict[str, AbstractEnforcer] = {}
        self._register_builtin_enforcers()

    def _register_builtin_enforcers(self) -> None:
        """Register built-in enforcers."""
        from cost_guardian.enforcement.ec2 import EC2Enforcer
        from cost_guardian.enforcement.rds import RDSEnforcer
        from cost_guardian.enforcement.ecs import ECSEnforcer
        from cost_guardian.enforcement.sagemaker import SageMakerEnforcer
        from cost_guardian.enforcement.redshift import RedshiftEnforcer
        from cost_guardian.enforcement.lambda_enforcer import LambdaEnforcer

        for enforcer_class in [EC2Enforcer, RDSEnforcer, ECSEnforcer, SageMakerEnforcer, RedshiftEnforcer, LambdaEnforcer]:
            enforcer = enforcer_class()
            self._enforcers[enforcer.service_name()] = enforcer

    def register_enforcer(self, enforcer: AbstractEnforcer) -> None:
        """Register a custom enforcer."""
        self._enforcers[enforcer.service_name()] = enforcer

    def get_enforcer(self, service: str) -> Optional[AbstractEnforcer]:
        """Get an enforcer by service name."""
        return self._enforcers.get(service)

    def enforce_all(
        self,
        services: List[str],
        tag_key: str,
        tag_value: str,
        regions: List[str],
        dry_run: bool,
    ) -> Dict[str, EnforcementResult]:
        """
        Run enforcement for multiple services.

        Returns:
            Dict mapping service name to EnforcementResult
        """
        results = {}

        for service in services:
            enforcer = self.get_enforcer(service)
            if not enforcer:
                # Unknown service, skip silently (log in handler)
                continue

            try:
                result = enforcer.enforce(tag_key, tag_value, regions, dry_run)
                results[service] = result
            except Exception as e:
                results[service] = EnforcementResult(
                    service=service,
                    error=str(e),
                )

        return results


__all__ = [
    "AbstractEnforcer",
    "EnforcementResult",
    "EnforcementOrchestrator",
]
