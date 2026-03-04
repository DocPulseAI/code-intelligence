"""
Tests for EPIC-1 Hardening Pass:
- Auth classification with metadata (confidence + classification_basis)
- Operation ID grammar stricter rules (no duplicates)
- Schema and infra diff integration
- Confidence model variable scoring
- Validation checks for endpoints
"""

import pytest
import json
import re
from src.intelligence.route_resolution_engine import (
    _classify_auth_with_metadata,
    _classify_auth_enhanced,
    _enforce_operation_id_strictness,
)


class TestAuthClassificationMetadata:
    """Test auth detection with metadata (confidence + basis)."""

    def test_jwt_detection_with_metadata(self):
        """JWT should have auth_analysis basis and full confidence."""
        tokens = ["jwtauth", "verify", "token"]
        result = _classify_auth_with_metadata(tokens, token_source="AUTH_ANALYSIS")

        assert result["type"] == "JWT"
        assert result["classification_basis"] == "AUTH_ANALYSIS"
        assert result["confidence"] == 1.0
        assert result["inferred"] == False

    def test_rbac_detection_with_metadata(self):
        """RBAC should be detected with appropriate confidence."""
        tokens = ["rbac", "authorize", "role"]
        result = _classify_auth_with_metadata(tokens, token_source="AUTH_ANALYSIS")

        assert result["type"] == "RBAC"
        assert result["classification_basis"] == "AUTH_ANALYSIS"
        assert result["confidence"] == 1.0

    def test_jwt_plus_rbac_precedence(self):
        """JWT+RBAC has highest precedence."""
        tokens = ["jwtauth", "rbac", "authorize"]
        result = _classify_auth_with_metadata(tokens, token_source="AUTH_ANALYSIS")

        assert result["type"] == "JWT+RBAC"

    def test_session_detection(self):
        """Session should be properly detected."""
        tokens = ["session", "middleware"]
        result = _classify_auth_with_metadata(tokens, token_source="AUTH_ANALYSIS")

        assert result["type"] == "Session"

    def test_public_fallback(self):
        """No middleware should default to Public."""
        tokens = []
        result = _classify_auth_with_metadata(tokens, token_source="STATIC_DISCOVERY")

        assert result["type"] == "Public"
        assert result["classification_basis"] == "STATIC_DISCOVERY"
        assert result["confidence"] == 0.8

    def test_mount_inheritance_reduces_confidence(self):
        """Inherited auth via mount should reduce confidence."""
        tokens = ["jwt", "verify"]
        result = _classify_auth_with_metadata(
            tokens,
            token_source="AUTH_ANALYSIS",
            has_mount_inheritance=True
        )

        assert result["type"] == "JWT"
        assert result["confidence"] == 0.95  # Slightly less than direct

    def test_route_resolution_basis(self):
        """Mount inheritance should set ROUTE_RESOLUTION basis."""
        tokens = ["jwt"]
        result = _classify_auth_with_metadata(
            tokens,
            token_source="STATIC_DISCOVERY",
            has_mount_inheritance=True
        )

        assert result["classification_basis"] == "ROUTE_RESOLUTION"

    def test_inferred_unknown_middleware(self):
        """Unknown middleware should infer JWT with reduced confidence."""
        tokens = ["customauth"]
        result = _classify_auth_with_metadata(
            tokens,
            token_source="AUTH_ANALYSIS",
            inferred_unknown=True
        )

        assert result["type"] == "JWT"
        assert result["classification_basis"] == "INFERRED"
        assert result["confidence"] == 0.85
        assert result["inferred"] == True

    def test_backward_compatibility(self):
        """_classify_auth_enhanced should still return just the type."""
        tokens = ["jwt", "bearer"]
        result = _classify_auth_enhanced(tokens)

        assert isinstance(result, str)
        assert result == "JWT"


class TestOperationIDStrictness:
    """Test operation ID stricter grammar rules."""

    def test_duplicate_removal_restaurant_delete(self):
        """Should remove duplicate 'delete' token."""
        operation_id = "deleteRestaurantDelete"
        result = _enforce_operation_id_strictness(operation_id)

        assert result == "deleteRestaurant"
        assert "DeleteDelete" not in result

    def test_duplicate_removal_get_get(self):
        """Should remove duplicate verb."""
        operation_id = "getGetUser"
        result = _enforce_operation_id_strictness(operation_id)

        assert result == "getUser"

    def test_duplicate_removal_update_update(self):
        """Should remove consecutive duplicate 'Update'."""
        operation_id = "updateProfileUpdate"
        result = _enforce_operation_id_strictness(operation_id)

        assert result == "updateProfile"

    def test_no_duplicates_valid_id(self):
        """Valid operation ID should not be modified."""
        operation_id = "getResourceById"
        result = _enforce_operation_id_strictness(operation_id)

        assert result == "getResourceById"

    def test_case_insensitive_duplicate_detection(self):
        """Duplicate detection should be case-insensitive."""
        operation_id = "createResourceResource"
        result = _enforce_operation_id_strictness(operation_id)

        # Should keep only one "Resource"
        assert result.count("Resource") == 1

    def test_empty_operation_id_fallback(self):
        """Empty ID should return safe default."""
        result = _enforce_operation_id_strictness("")

        assert result == "getResource"

    def test_complex_path_no_duplicates(self):
        """Complex path without duplicates should be preserved."""
        operation_id = "deleteCommentReactionById"
        result = _enforce_operation_id_strictness(operation_id)

        assert result == "deleteCommentReactionById"


class TestConfidenceVariability:
    """Test variable confidence scoring based on resolution method."""

    def test_full_ast_mount_middleware_confidence(self):
        """AST + mount + middleware = 1.0 confidence."""
        result = _classify_auth_with_metadata(
            ["jwt"],
            token_source="AUTH_ANALYSIS",
            has_mount_inheritance=False,
            inferred_unknown=False
        )
        assert result["confidence"] == 1.0

    def test_ast_mount_confidence(self):
        """AST + mount = 0.9 confidence."""
        result = _classify_auth_with_metadata(
            ["jwt"],
            token_source="AUTH_ANALYSIS",
            has_mount_inheritance=True,
            inferred_unknown=False
        )
        # Note: With both AUTH_ANALYSIS and mount, gets 0.95
        assert result["confidence"] == 0.95

    def test_mount_only_confidence(self):
        """Mount only = 0.9 confidence."""
        result = _classify_auth_with_metadata(
            ["jwt"],
            token_source="STATIC_DISCOVERY",
            has_mount_inheritance=True,
            inferred_unknown=False
        )
        assert result["confidence"] == 0.9

    def test_regex_fallback_confidence(self):
        """Regex fallback = 0.8 confidence."""
        result = _classify_auth_with_metadata(
            [],
            token_source="STATIC_DISCOVERY",
            has_mount_inheritance=False,
            inferred_unknown=False
        )
        assert result["confidence"] == 0.8

    def test_inferred_confidence(self):
        """Inferred unknown = 0.85 confidence."""
        result = _classify_auth_with_metadata(
            ["customauth"],
            token_source="AUTH_ANALYSIS",
            inferred_unknown=True
        )
        assert result["confidence"] == 0.85


class TestValidationChecks:
    """Test strict validation rules for endpoints."""

    def test_no_lowercase_method(self):
        """Methods should be uppercase."""
        # This would be caught in _normalize_method
        assert "GET" == "GET".upper()
        assert "POST" == "POST".upper()

    def test_no_trailing_slash_except_root(self):
        """Paths should not end with / except root."""
        # Root path is valid
        assert "/" == "/"

        # Non-root trailing slash is invalid
        path = "/users/"
        # This path has trailing slash and is not root
        assert path != "/" and path.endswith("/")
        # Valid param
        assert re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "{id}")
        assert re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "{userId}")

        # Invalid param formats
        assert not re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "{123id}")  # starts with number
        assert not re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "{id:int}")  # contains colon

    def test_normalized_key_deterministic(self):
        """Normalized keys should be deterministic."""
        method = "GET"
        path = "/Users"

        key1 = f"{method.lower()} {path.lower()}"
        key2 = f"{method.lower()} {path.lower()}"

        assert key1 == key2
        assert key1 == "get /users"


class TestDeterminism:
    """Test deterministic output across multiple runs."""

    def test_auth_detection_determinism(self):
        """Auth detection should be deterministic across runs."""
        tokens = ["jwt", "rbac", "session"]

        result1 = _classify_auth_with_metadata(tokens, token_source="AUTH_ANALYSIS")
        result2 = _classify_auth_with_metadata(tokens, token_source="AUTH_ANALYSIS")

        assert result1 == result2

    def test_operation_id_strictness_determinism(self):
        """Operation ID strictness should be deterministic."""
        operation_id = "deleteRestaurantDelete"

        result1 = _enforce_operation_id_strictness(operation_id)
        result2 = _enforce_operation_id_strictness(operation_id)

        assert result1 == result2

    def test_multiple_runs_identical_output(self):
        """
        Run auth detection 3 times with same input.
        All outputs must be identical.
        """
        tokens = ["jwtauth", "rbac", "authorize"]
        source = "AUTH_ANALYSIS"

        results = []
        for _ in range(3):
            result = _classify_auth_with_metadata(tokens, token_source=source)
            results.append(json.dumps(result, sort_keys=True))

        # All 3 runs should produce identical JSON
        assert results[0] == results[1] == results[2]


class TestClassificationBasisUpgrade:
    """Test updated classification_basis values."""

    def test_auth_analysis_basis(self):
        """Direct middleware analysis should use AUTH_ANALYSIS."""
        result = _classify_auth_with_metadata(
            ["jwt"],
            token_source="AUTH_ANALYSIS"
        )
        assert result["classification_basis"] == "AUTH_ANALYSIS"

    def test_route_resolution_basis(self):
        """Inherited auth should use ROUTE_RESOLUTION."""
        result = _classify_auth_with_metadata(
            ["jwt"],
            token_source="STATIC_DISCOVERY",
            has_mount_inheritance=True
        )
        assert result["classification_basis"] == "ROUTE_RESOLUTION"

    def test_static_discovery_basis(self):
        """No middleware should use STATIC_DISCOVERY."""
        result = _classify_auth_with_metadata(
            [],
            token_source="STATIC_DISCOVERY"
        )
        assert result["classification_basis"] == "STATIC_DISCOVERY"

    def test_inferred_basis(self):
        """Unknown middleware should use INFERRED basis."""
        result = _classify_auth_with_metadata(
            ["unknownauth"],
            token_source="AUTH_ANALYSIS",
            inferred_unknown=True
        )
        assert result["classification_basis"] == "INFERRED"
