"""
Structured JSON logging with correlation IDs.
"""
import json
import logging
import uuid
from typing import Any, Dict, Optional
from datetime import datetime, timezone


class CorrelationIDManager:
    """Thread-safe correlation ID management."""
    _correlation_id: Optional[str] = None

    @classmethod
    def get_or_create(cls) -> str:
        if not cls._correlation_id:
            cls._correlation_id = str(uuid.uuid4())[:8]
        return cls._correlation_id

    @classmethod
    def set(cls, correlation_id: str) -> None:
        cls._correlation_id = correlation_id

    @classmethod
    def reset(cls) -> None:
        cls._correlation_id = None


def structured_log(
    message: str,
    level: str = "INFO",
    correlation_id: Optional[str] = None,
    **kwargs: Any
) -> None:
    """
    Log a structured JSON message.

    Args:
        message: Primary log message
        level: Log level (INFO, WARN, ERROR, etc.)
        correlation_id: Optional correlation ID (auto-generated if None)
        **kwargs: Additional fields to include in log
    """
    if correlation_id is None:
        correlation_id = CorrelationIDManager.get_or_create()

    log_entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level.upper(),
        "correlation_id": correlation_id,
        "msg": message,
        **kwargs,
    }

    print(json.dumps(log_entry))


def log_info(msg: str, **kwargs: Any) -> None:
    structured_log(msg, "INFO", **kwargs)


def log_warn(msg: str, **kwargs: Any) -> None:
    structured_log(msg, "WARN", **kwargs)


def log_error(msg: str, error: Optional[str] = None, **kwargs: Any) -> None:
    if error:
        kwargs["error"] = error
    structured_log(msg, "ERROR", **kwargs)


def log_enforcement(
    status: str,
    service: str,
    matched: int = 0,
    stopped: int = 0,
    error: Optional[str] = None,
    **kwargs: Any
) -> None:
    """Log enforcement action result."""
    entry = {
        "status": status,
        "service": service,
        "matched": matched,
        "stopped": stopped,
    }
    if error:
        entry["error"] = error
    entry.update(kwargs)
    structured_log(f"enforcement result ({service})", "INFO", **entry)
