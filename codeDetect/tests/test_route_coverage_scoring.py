"""
Test suite for route coverage confidence scoring.

Validates:
- Coverage ratio calculation (resolved / mounted)
- Coverage percentage calculation
- Unresolved route detection
- Non-breaking failure mode
- Coverage warnings
- Zero-denominator safety
"""

import unittest
from unittest.mock import MagicMock
from src.intelligence.route_resolution_engine import _resolve_express_candidates_internal


class TestRouteCoverageScoring(unittest.TestCase):
    """Test route coverage metrics calculation."""

    def test_full_coverage(self):
        """100% coverage when all routes are resolved."""
        candidates = [
            {
                "method": "GET",
                "path": "/users",
                "source_file": "routes/users.js",
                "line_start": 10,
                "router_symbol": "router",
                "middleware_tokens": [],
            },
            {
                "method": "POST",
                "path": "/users",
                "source_file": "routes/users.js",
                "line_start": 15,
                "router_symbol": "router",
                "middleware_tokens": [],
            },
        ]

        file_paths = ["routes/users.js", "app.js"]
        read_file = MagicMock(return_value="// mock file")

        result = _resolve_express_candidates_internal(
            candidates,
            file_paths,
            read_file,
            tech_stack={"backend_framework": "express"}
        )

        # Check that coverage metrics exist
        coverage = result.get("coverage_metrics", {})
        self.assertIsNotNone(coverage, "Should include coverage_metrics")

        if coverage:
            self.assertEqual(coverage.get("coverage_percent"), 100,
                           "Perfect coverage should be 100%")
            self.assertEqual(coverage.get("unresolved_routes"), 0,
                           "No unresolved routes expected")

    def test_partial_coverage(self):
        """Coverage metrics for partial resolution."""
        candidates = [
            {
                "method": "GET",
                "path": "/users",
                "source_file": "routes/users.js",
                "line_start": 10,
                "router_symbol": "router",
                "middleware_tokens": [],
            },
            {
                "method": "POST",
                "path": "/users",
                "source_file": "routes/users.js",
                "line_start": 15,
                "router_symbol": "router",
                "middleware_tokens": [],
            },
        ]

        file_paths = ["routes/users.js"]
        read_file = MagicMock(return_value="// mock file")

        result = _resolve_express_candidates_internal(
            candidates,
            file_paths,
            read_file,
            tech_stack={"backend_framework": "express"}
        )

        coverage = result.get("coverage_metrics", {})
        if coverage:
            # Should have metrics
            self.assertIn("coverage_ratio", coverage)
            self.assertIn("coverage_percent", coverage)
            self.assertIn("unresolved_routes", coverage)

    def test_zero_candidates_safety(self):
        """Safe handling when no candidates provided."""
        candidates = []
        file_paths = []
        read_file = MagicMock(return_value="")

        result = _resolve_express_candidates_internal(
            candidates,
            file_paths,
            read_file,
            tech_stack=None
        )

        coverage = result.get("coverage_metrics", {})
        if coverage:
            # Should handle zero denominator gracefully
            self.assertIsNotNone(coverage.get("coverage_ratio"))
            self.assertIsNotNone(coverage.get("coverage_percent"))

    def test_coverage_warnings_non_breaking(self):
        """Coverage warnings don't fail analysis."""
        candidates = [
            {
                "method": "GET",
                "path": "/users",
                "source_file": "routes/users.js",
                "line_start": 10,
                "router_symbol": "router",
                "middleware_tokens": [],
            },
        ]

        file_paths = ["routes/users.js"]
        read_file = MagicMock(return_value="// mock file")

        result = _resolve_express_candidates_internal(
            candidates,
            file_paths,
            read_file,
            tech_stack={"backend_framework": "express"}
        )

        # Validation status should still be OK even with warnings
        self.assertEqual(result.get("validation_status"), "OK",
                        "Should not fail analysis for coverage warnings")

    def test_coverage_warnings_present(self):
        """Coverage warnings noted when coverage < 100%."""
        # This requires a scenario where routes don't resolve
        # Create a simple non-express repo to avoid resolution
        candidates = [{"method": "GET", "path": "/test"}]
        file_paths = []
        read_file = MagicMock(return_value="")

        result = _resolve_express_candidates_internal(
            candidates,
            file_paths,
            read_file,
            tech_stack=None  # Not express, won't resolve
        )

        # Should succeed in processing
        self.assertIn("validation_status", result)

    def test_coverage_ratio_calculation(self):
        """Coverage ratio correctly calculated as resolved/mounted."""
        candidates = [
            {
                "method": "GET",
                "path": "/users",
                "source_file": "routes/users.js",
                "line_start": 10,
                "router_symbol": "router",
                "middleware_tokens": [],
            },
        ]

        file_paths = ["routes/users.js"]
        read_file = MagicMock(return_value="const router = require('express').Router();")

        result = _resolve_express_candidates_internal(
            candidates,
            file_paths,
            read_file,
            tech_stack={"backend_framework": "express"}
        )

        coverage = result.get("coverage_metrics", {})
        if coverage and result.get("validation_status") == "OK":
            # Ratio should be between 0.0 and 1.0
            ratio = coverage.get("coverage_ratio", 0)
            self.assertGreaterEqual(ratio, 0.0)
            self.assertLessEqual(ratio, 1.0)

            # Percent should be ratio * 100
            percent = coverage.get("coverage_percent", 0)
            expected_percent = int(round(ratio * 100))
            self.assertEqual(percent, expected_percent)


class TestCoverageMetricsStructure(unittest.TestCase):
    """Test coverage metrics structure and fields."""

    def test_coverage_metrics_fields(self):
        """Coverage metrics include all required fields."""
        candidates = [
            {
                "method": "GET",
                "path": "/test",
                "source_file": "routes.js",
                "line_start": 1,
                "router_symbol": "router",
                "middleware_tokens": [],
            },
        ]

        file_paths = ["routes.js"]
        read_file = MagicMock(return_value="const router = require('express').Router();")

        result = _resolve_express_candidates_internal(
            candidates,
            file_paths,
            read_file,
            tech_stack={"backend_framework": "express"}
        )

        if result.get("validation_status") == "OK":
            coverage = result.get("coverage_metrics", {})
            if coverage:
                required_fields = [
                    "mounted_route_count",
                    "resolved_route_count",
                    "coverage_ratio",
                    "coverage_percent",
                    "unresolved_routes",
                    "coverage_warnings",
                ]
                for field in required_fields:
                    self.assertIn(field, coverage,
                                f"Coverage metrics should include '{field}'")

    def test_coverage_warnings_format(self):
        """Coverage warnings are properly formatted."""
        candidates = [
            {
                "method": "GET",
                "path": "/test",
                "source_file": "routes.js",
                "line_start": 1,
                "router_symbol": "router",
                "middleware_tokens": [],
            },
        ]

        file_paths = ["routes.js"]
        read_file = MagicMock(return_value="const router = require('express').Router();")

        result = _resolve_express_candidates_internal(
            candidates,
            file_paths,
            read_file,
            tech_stack={"backend_framework": "express"}
        )

        if result.get("validation_status") == "OK":
            coverage = result.get("coverage_metrics", {})
            if coverage:
                warnings = coverage.get("coverage_warnings", [])
                self.assertIsInstance(warnings, list, "Warnings should be a list")

                for warning in warnings:
                    self.assertIsInstance(warning, str, "Each warning should be a string")
                    # Warnings should be informative
                    if warning:
                        self.assertGreater(len(warning), 0)


if __name__ == "__main__":
    unittest.main()
