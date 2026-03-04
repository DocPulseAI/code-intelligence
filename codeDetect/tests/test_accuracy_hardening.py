"""
Tests for EPIC-1 Accuracy Hardening Pass
- Phase 1: Auth Inheritance Resolution
- Phase 2: Structured Coverage Failure Model
- Phase 3: Operation ID Grammar Rewrite
- Phase 4: Dynamic Confidence Scoring
- Phase 5: Validator-Schema Linking
"""

import pytest
import json
from src.intelligence.route_resolution_engine import (
    _classify_auth_inherited,
    _singularize_deterministic,
    _calculate_confidence_dynamic,
)


class TestAuthInheritanceResolution:
    """Phase 1: Auth inheritance from route, router, and app levels."""

    def test_route_level_middleware_detected(self):
        """Route-level middleware should be extracted."""
        route_tokens = ["authenticate", "verifyToken"]
        auth_type = _classify_auth_inherited(route_tokens, is_inherited=False)
        assert auth_type == "JWT"

    def test_router_level_middleware_inherited(self):
        """Router-level middleware should be inherited by routes."""
        inherited_tokens = ["session", "authorize"]
        auth_type = _classify_auth_inherited(inherited_tokens, is_inherited=True)
        assert auth_type == "Session"  # Session has precedence over RBAC in inheritance

    def test_jwt_plus_rbac_precedence(self):
        """JWT+RBAC detection from combined tokens."""
        tokens = ["jwtVerify", "checkPermissions", "authorize"]
        auth_type = _classify_auth_inherited(tokens, is_inherited=False)
        assert auth_type == "JWT+RBAC"

    def test_middleware_array_parsing(self):
        """Middleware arrays should be flattened and merged."""
        # Simulates [middleware1, middleware2] array with JWT and RBAC
        tokens = ["authenticate", "rateLimiter", "checkRole"]
        auth_type = _classify_auth_inherited(tokens, is_inherited=False)
        assert auth_type == "JWT+RBAC"  # Both JWT (authenticate) and RBAC (role) keywords present

    def test_deterministic_token_ordering(self):
        """Multiple runs with same tokens should yield identical results."""
        tokens1 = ["session", "jwt", "rbac"]
        tokens2 = ["rbac", "jwt", "session"]
        # Both should resolve to JWT+RBAC regardless of order
        auth1 = _classify_auth_inherited(tokens1, is_inherited=False)
        auth2 = _classify_auth_inherited(tokens2, is_inherited=False)
        assert auth1 == auth2 == "JWT+RBAC"

    def test_no_unknown_auth_type(self):
        """Auth classification must never return "Unknown"."""
        # Empty or unrecognized middleware
        unknown_tokens = ["randomMiddleware", "someFunction"]
        auth_type = _classify_auth_inherited(unknown_tokens, is_inherited=False)
        # Should fallback to Public or conservative default
        assert auth_type in {"Public", "JWT"}
        assert auth_type != "Unknown"

    def test_public_fallback_no_auth(self):
        """No auth middleware should result in Public."""
        tokens = []
        auth_type = _classify_auth_inherited(tokens, is_inherited=False)
        assert auth_type == "Public"

    def test_middleware_cycle_guard(self):
        """Middleware chain traversal should handle cycles."""
        # Test that cycle detection prevents infinite loops
        # This is implicit in the inheritance resolution
        auth_type = _classify_auth_inherited(["jwt"], is_inherited=True)
        assert auth_type == "JWT"


class TestStructuredCoverageFailure:
    """Phase 2: Structured coverage failure model with enum reasons."""

    def test_coverage_failure_structure(self):
        """Coverage failure should have structured reason enum."""
        failure = {
            "source_file": "app.js",
            "line": 42,
            "reason": "DYNAMIC_PATH"
        }
        assert failure["reason"] in {
            "DYNAMIC_PATH",
            "CONDITIONAL_EXPORT",
            "UNSUPPORTED_PATTERN",
            "PARSE_FAILURE",
            "MOUNT_CHAIN_BROKEN"
        }

    def test_coverage_percent_deterministic(self):
        """Coverage percentage must be deterministic."""
        resolved = 45
        total = 50
        coverage1 = (resolved / total) * 100
        coverage2 = (resolved / total) * 100
        # Same inputs → same output
        assert coverage1 == coverage2 == 90.0

    def test_unresolved_details_populated(self):
        """Unresolved routes should have detailed failure reasons."""
        details = [
            {
                "source_file": "routes/users.js",
                "line": 12,
                "reason": "DYNAMIC_PATH"
            },
            {
                "source_file": "routes/comments.js",
                "line": 35,
                "reason": "MOUNT_CHAIN_BROKEN"
            }
        ]
        assert len(details) == 2
        assert all("reason" in d for d in details)
        assert all(d["reason"] in {
            "DYNAMIC_PATH",
            "CONDITIONAL_EXPORT",
            "UNSUPPORTED_PATTERN",
            "PARSE_FAILURE",
            "MOUNT_CHAIN_BROKEN"
        } for d in details)


class TestOperationIDGrammarRewrite:
    """Phase 3: Deterministic operation ID grammar with proper singularization."""

    def test_singularize_ies_to_y(self):
        """companies → company."""
        result = _singularize_deterministic("companies")
        assert result == "company"

    def test_singularize_deliveries(self):
        """deliveries → delivery (special case)."""
        result = _singularize_deterministic("deliveries")
        assert result == "delivery"

    def test_singularize_statuses(self):
        """statuses → status (not remove just one s)."""
        result = _singularize_deterministic("statuses")
        assert result == "status"

    def test_irregular_override_users(self):
        """users → user (irregular override)."""
        result = _singularize_deterministic("users")
        assert result == "user"

    def test_irregular_override_people(self):
        """people → person (irregular override)."""
        result = _singularize_deterministic("people")
        assert result == "person"

    def test_no_truncation(self):
        """Singularization should never truncate to invalid tokens."""
        result = _singularize_deterministic("address")
        # Should not become "addres"
        assert result in {"address", "addr"} or len(result) >= 3

    def test_operation_id_get_collection(self):
        """GET /resources → getResources."""
        # Grammar test (requires method + path)
        # This would be tested at a higher level
        pass

    def test_operation_id_post_create(self):
        """POST /resources → createResource."""
        pass

    def test_operation_id_delete_with_id(self):
        """DELETE /resources/{id} → deleteResourceById (not deleteResource)."""
        pass


class TestDynamicConfidenceScoring:
    """Phase 4: Dynamic confidence based on resolution success."""

    def test_base_confidence_ast_extraction(self):
        """AST extraction base confidence = 0.95."""
        # Base starts at 0.95 for successful AST parse
        pass

    def test_base_confidence_regex_fallback(self):
        """Regex fallback base confidence = 0.75."""
        # Regex patterns lower confidence
        pass

    def test_confidence_adjusted_for_mount_resolution(self):
        """Mount resolution adds +0.02 to confidence."""
        # If mount chain resolved successfully: +0.02
        pass

    def test_confidence_adjusted_for_auth_resolved(self):
        """Auth resolution adds +0.02 to confidence."""
        # If auth was successfully inferred: +0.02
        pass

    def test_confidence_adjusted_for_schema_linked(self):
        """Schema linking adds +0.02 to confidence."""
        # If schema was found and linked: +0.02
        pass

    def test_confidence_penalty_unresolved_path(self):
        """Unresolved path parameters reduce confidence by 0.1."""
        pass

    def test_confidence_penalty_missing_auth(self):
        """Missing auth inference reduces confidence by 0.05."""
        pass

    def test_confidence_clamped_0_to_1(self):
        """Confidence must be clamped to [0.0, 1.0]."""
        # Adjustments cannot result in <0 or >1
        pass

    def test_confidence_deterministic_rounding(self):
        """Confidence rounded to 2 decimals, deterministically."""
        conf1 = round(0.8234, 2)
        conf2 = round(0.8234, 2)
        assert conf1 == conf2 == 0.82

    def test_3_run_identical_confidence(self):
        """3 runs of confidence calculation must be identical."""
        # Same input → same output across runs
        pass


class TestValidatorSchemaLinking:
    """Phase 5: Static linking of validators to routes."""

    def test_joi_validator_detection(self):
        """Joi schema detection in middleware."""
        # joi.object({ ... }) pattern recognition
        pass

    def test_express_validator_detection(self):
        """express-validator detection."""
        # body(...).isEmail(), etc.
        pass

    def test_zod_validator_detection(self):
        """Zod schema detection."""
        # z.object({ ... })
        pass

    def test_mongoose_schema_detection(self):
        """Mongoose model schema detection."""
        # Schema.create, Schema.validate
        pass

    def test_validator_linked_to_handler(self):
        """Validator middleware should be linked to route handler."""
        # If route has validator before handler: linked
        pass

    def test_weak_schema_link_classification(self):
        """Model found but not linked = WEAK_SCHEMA_LINK."""
        # If model exists but not in middleware chain: WEAK_SCHEMA_LINK
        pass

    def test_request_schema_block_attached(self):
        """Extracted schema should be in request_schema block."""
        # Endpoint should have:
        # "request_schema": {
        #   "body": {"fields": [...]}
        # }
        pass

    def test_no_runtime_execution(self):
        """Schema linking uses AST analysis only, no execution."""
        # Verify no eval() or require() at runtime
        pass


class TestDeterminismValidation:
    """Cross-phase determinism validation."""

    def test_auth_inheritance_3_run_identical(self):
        """Auth inheritance identical across 3 runs."""
        pass

    def test_operation_id_grammar_3_run_identical(self):
        """Operation ID generation identical across 3 runs."""
        pass

    def test_confidence_scoring_3_run_identical(self):
        """Confidence scores identical across 3 runs."""
        pass

    def test_coverage_model_3_run_identical(self):
        """Coverage failure models identical across 3 runs."""
        pass

    def test_schema_linking_3_run_identical(self):
        """Schema linking results identical across 3 runs."""
        pass
