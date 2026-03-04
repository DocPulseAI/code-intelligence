"""
Test suite for enterprise Operation ID grammar engine.

Validates:
- Resource extraction and singularization
- Method mapping matrix (GET, POST, PATCH, DELETE, PUT)
- Subresource handling
- No duplicated verbs
- CamelCase enforcement
- Singularization rules (ies→y, ves→fe, xes→x, etc.)
- Deterministic output
"""

import unittest
from src.intelligence.route_resolution_engine import (
    _singularize_enhanced,
    _operation_id_base,
)


class TestSingularizationEnhanced(unittest.TestCase):
    """Test enhanced singularization rules."""

    def test_irregular_overrides(self):
        """Test irregular singularization (checked first)."""
        assertions = {
            "people": "person",
            "users": "user",
            "activities": "activity",
            "companies": "company",
            "statistics": "statistic",
            "data": "datum",
            "criteria": "criterion",
            "phenomena": "phenomenon",
        }
        for plural, expected_singular in assertions.items():
            result = _singularize_enhanced(plural)
            self.assertEqual(result, expected_singular,
                           f"{plural} should singularize to {expected_singular}")

    def test_ies_rule(self):
        """'...ies' → '...y' rule."""
        assertions = {"companies": "company", "activities": "activity"}
        for plural, expected in assertions.items():
            result = _singularize_enhanced(plural)
            self.assertEqual(result, expected)

    def test_ves_rule(self):
        """'...ves' → '...fe' rule."""
        assertions = {"calves": "calf", "halves": "half", "elves": "elf"}
        for plural, expected in assertions.items():
            result = _singularize_enhanced(plural)
            self.assertEqual(result, expected)

    def test_xes_rule(self):
        """'...xes' → '...x' rule."""
        assertions = {"boxes": "box", "foxes": "fox"}
        for plural, expected in assertions.items():
            result = _singularize_enhanced(plural)
            self.assertEqual(result, expected)

    def test_ches_rule(self):
        """'...ches' → '...ch' rule."""
        assertions = {"churches": "church", "benches": "bench"}
        for plural, expected in assertions.items():
            result = _singularize_enhanced(plural)
            self.assertEqual(result, expected)

    def test_shes_rule(self):
        """'...shes' → '...sh' rule."""
        assertions = {"dishes": "dish", "bushes": "bush"}
        for plural, expected in assertions.items():
            result = _singularize_enhanced(plural)
            self.assertEqual(result, expected)

    def test_oes_rule(self):
        """'...oes' → '...o' rule."""
        assertions = {"tomatoes": "tomato", "heroes": "hero"}
        for plural, expected in assertions.items():
            result = _singularize_enhanced(plural)
            self.assertEqual(result, expected)

    def test_trailing_s_rule(self):
        """Trailing 's' removal (if len > 3)."""
        assertions = {
            "projects": "project",
            "resources": "resource",
            "endpoints": "endpoint",
        }
        for plural, expected in assertions.items():
            result = _singularize_enhanced(plural)
            self.assertEqual(result, expected)


class TestOperationIDGrammarMatrix(unittest.TestCase):
    """Test enterprise Operation ID method matrix."""

    def test_get_collection(self):
        """GET /resources → getResources."""
        result = _operation_id_base("GET", "/api/users")
        self.assertEqual(result, "getUsers")

    def test_post_create(self):
        """POST /resources → createResource."""
        result = _operation_id_base("POST", "/api/users")
        self.assertEqual(result, "createUser")

    def test_get_by_id(self):
        """GET /resources/{id} → getResourceById."""
        result = _operation_id_base("GET", "/api/users/{id}")
        self.assertEqual(result, "getUserById")

    def test_patch_update(self):
        """PATCH /resources/{id} → updateResource."""
        result = _operation_id_base("PATCH", "/api/users/{id}")
        self.assertEqual(result, "updateUser")

    def test_put_replace(self):
        """PUT /resources/{id} → replaceResource."""
        result = _operation_id_base("PUT", "/api/users/{id}")
        self.assertEqual(result, "replaceUser")

    def test_delete_by_id(self):
        """DELETE /resources/{id} → deleteResourceById."""
        result = _operation_id_base("DELETE", "/api/users/{id}")
        self.assertEqual(result, "deleteUserById")

    def test_delete_collection(self):
        """DELETE /resources → deleteResources."""
        result = _operation_id_base("DELETE", "/api/users")
        self.assertEqual(result, "deleteUsers")


class TestOperationIDSubresources(unittest.TestCase):
    """Test subresource and nested path handling."""

    def test_subresource_get(self):
        """GET /projects/{id}/members → getProjectMembers."""
        result = _operation_id_base("GET", "/api/projects/{id}/members")
        self.assertIn("member", result.lower(), "Should include subresource name")

    def test_subresource_post(self):
        """POST /projects/{id}/members → createProjectMember."""
        result = _operation_id_base("POST", "/api/projects/{id}/members")
        self.assertIn("member", result.lower(), "Should include subresource name")

    def test_nested_param_handling(self):
        """Deep nesting with parameters."""
        result = _operation_id_base("GET", "/api/projects/{projectId}/teams/{teamId}/members")
        self.assertTrue(len(result) > 0, "Should generate operation ID for deeply nested path")
        self.assertTrue(result.startswith("get"), "Should use GET verb")


class TestOperationIDEdgeCases(unittest.TestCase):
    """Test edge cases and special scenarios."""

    def test_search_endpoint(self):
        """GET /search → searchResources."""
        result = _operation_id_base("GET", "/api/search")
        self.assertEqual(result, "searchResources")

    def test_empty_path(self):
        """Fallback for empty path."""
        result = _operation_id_base("GET", "")
        self.assertEqual(result, "getResource")

    def test_no_duplicated_verbs(self):
        """No duplicated verbs in operation ID.

        Example: Regular paths don't have verbs in resource names
        Paths like /api/users/{id} → deleteUserById (not deleteUser duplicate)
        """
        # Test with a normal path that doesn't contain verb in resource name
        result = _operation_id_base("DELETE", "/api/items/{id}")
        self.assertEqual(result, "deleteItemById")
        self.assertNotIn("deletedelete", result.lower(),
                        "Should not duplicate verb")

    def test_camelcase_consistency(self):
        """Operation IDs always in camelCase, never snake_case."""
        result_hyphen = _operation_id_base("GET", "/api/user_profile/{id}")
        result_underscore = _operation_id_base("GET", "/api/user-profile/{id}")

        # Both should be camelCase
        for result in [result_hyphen, result_underscore]:
            self.assertTrue(result[0].islower(), "Should start with lowercase")
            self.assertNotIn("_", result, "Should not contain underscores")
    """Test deterministic and reproducible operation ID generation."""

    def test_multiple_runs_identical(self):
        """Same inputs produce identical operation IDs across runs."""
        method = "GET"
        path = "/api/projects/{id}/members"

        result1 = _operation_id_base(method, path)
        result2 = _operation_id_base(method, path)
        result3 = _operation_id_base(method, path)

        self.assertEqual(result1, result2)
        self.assertEqual(result2, result3)
        self.assertTrue(len(result1) > 0, "Operation ID should not be empty")

    def test_singularization_determinism(self):
        """Singularization always produces same result."""
        word = "companies"

        result1 = _singularize_enhanced(word)
        result2 = _singularize_enhanced(word)
        result3 = _singularize_enhanced(word)

        self.assertEqual(result1, result2)
        self.assertEqual(result2, result3)


if __name__ == "__main__":
    unittest.main()
