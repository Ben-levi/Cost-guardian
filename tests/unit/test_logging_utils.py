"""
Unit tests for cost_guardian.logging_utils module.
"""
import unittest
import json
from io import StringIO
from unittest.mock import patch

from cost_guardian.logging_utils import (
    CorrelationIDManager,
    structured_log,
    log_info,
    log_warn,
    log_error,
    log_enforcement,
)


class TestCorrelationIDManager(unittest.TestCase):
    """Test CorrelationIDManager."""

    def setUp(self):
        CorrelationIDManager.reset()

    def test_get_or_create_generates_new_id(self):
        corr_id = CorrelationIDManager.get_or_create()
        self.assertIsNotNone(corr_id)
        self.assertGreater(len(corr_id), 0)

    def test_get_or_create_returns_same_id(self):
        corr_id1 = CorrelationIDManager.get_or_create()
        corr_id2 = CorrelationIDManager.get_or_create()
        self.assertEqual(corr_id1, corr_id2)

    def test_set_and_get(self):
        CorrelationIDManager.set("test-correlation-id")
        corr_id = CorrelationIDManager.get_or_create()
        self.assertEqual(corr_id, "test-correlation-id")

    def test_reset(self):
        CorrelationIDManager.set("test-id")
        CorrelationIDManager.reset()
        corr_id = CorrelationIDManager.get_or_create()
        self.assertNotEqual(corr_id, "test-id")


class TestStructuredLog(unittest.TestCase):
    """Test structured_log and convenience functions."""

    def setUp(self):
        CorrelationIDManager.reset()

    @patch("sys.stdout", new_callable=StringIO)
    def test_structured_log_default_level(self, mock_stdout):
        structured_log("test message")
        output = mock_stdout.getvalue()
        log_entry = json.loads(output.strip())

        self.assertEqual(log_entry["msg"], "test message")
        self.assertEqual(log_entry["level"], "INFO")
        self.assertIn("correlation_id", log_entry)
        self.assertIn("ts", log_entry)

    @patch("sys.stdout", new_callable=StringIO)
    def test_structured_log_with_level(self, mock_stdout):
        structured_log("test error", level="ERROR")
        output = mock_stdout.getvalue()
        log_entry = json.loads(output.strip())

        self.assertEqual(log_entry["level"], "ERROR")

    @patch("sys.stdout", new_callable=StringIO)
    def test_structured_log_with_extra_fields(self, mock_stdout):
        structured_log("test", extra_field="extra_value", number=42)
        output = mock_stdout.getvalue()
        log_entry = json.loads(output.strip())

        self.assertEqual(log_entry["extra_field"], "extra_value")
        self.assertEqual(log_entry["number"], 42)

    @patch("sys.stdout", new_callable=StringIO)
    def test_structured_log_with_correlation_id(self, mock_stdout):
        structured_log("test", correlation_id="custom-id")
        output = mock_stdout.getvalue()
        log_entry = json.loads(output.strip())

        self.assertEqual(log_entry["correlation_id"], "custom-id")

    @patch("sys.stdout", new_callable=StringIO)
    def test_log_info(self, mock_stdout):
        log_info("info message", user="alice")
        output = mock_stdout.getvalue()
        log_entry = json.loads(output.strip())

        self.assertEqual(log_entry["level"], "INFO")
        self.assertEqual(log_entry["msg"], "info message")
        self.assertEqual(log_entry["user"], "alice")

    @patch("sys.stdout", new_callable=StringIO)
    def test_log_warn(self, mock_stdout):
        log_warn("warning message")
        output = mock_stdout.getvalue()
        log_entry = json.loads(output.strip())

        self.assertEqual(log_entry["level"], "WARN")

    @patch("sys.stdout", new_callable=StringIO)
    def test_log_error_with_error(self, mock_stdout):
        log_error("error message", error="something broke")
        output = mock_stdout.getvalue()
        log_entry = json.loads(output.strip())

        self.assertEqual(log_entry["level"], "ERROR")
        self.assertEqual(log_entry["error"], "something broke")

    @patch("sys.stdout", new_callable=StringIO)
    def test_log_enforcement(self, mock_stdout):
        log_enforcement(
            status="STOPPED",
            service="ec2",
            matched=5,
            stopped=3,
            error=None,
            region="us-east-1",
        )
        output = mock_stdout.getvalue()
        log_entry = json.loads(output.strip())

        self.assertEqual(log_entry["level"], "INFO")
        self.assertIn("enforcement", log_entry["msg"])
        self.assertEqual(log_entry["status"], "STOPPED")
        self.assertEqual(log_entry["service"], "ec2")
        self.assertEqual(log_entry["matched"], 5)
        self.assertEqual(log_entry["stopped"], 3)
        self.assertEqual(log_entry["region"], "us-east-1")

    @patch("sys.stdout", new_callable=StringIO)
    def test_log_enforcement_with_error(self, mock_stdout):
        log_enforcement(
            status="ERROR",
            service="rds",
            error="Access Denied",
        )
        output = mock_stdout.getvalue()
        log_entry = json.loads(output.strip())

        self.assertEqual(log_entry["status"], "ERROR")
        self.assertEqual(log_entry["error"], "Access Denied")


if __name__ == "__main__":
    unittest.main()
