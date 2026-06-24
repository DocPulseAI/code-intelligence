"""
Test suite for enterprise authentication detection hardening.

Validates:
- JWT detection (route-level, router-level, mount-level)
- RBAC detection
- Session detection
- Precedence matrix (JWT+RBAC > JWT > Session > RBAC > Public)
- Mount chain inheritance
- Never outputs "Unknown"
- Deterministic stability
"""

import unittest
from src.intelligence.route_resolution_engine import _classify_auth_enhanced


class TestAuthDetectionHardening(unittest.TestCase):
    """Test cases for enhanced auth classification."""

    def test_jwt_only_detection(self):
        """Route-level JWT detection."""
        tokens = ["jwtAuth", "verifyToken"]
        result = _classify_auth_enhanced(tokens)
        self.assertEqual(result, "JWT", "Should detect JWT-only routes")

    def test_rbac_only_detection(self):
        """Route-level RBAC detection."""
        tokens = ["rbac", "roleCheck", "permission"]
        result = _classify_auth_enhanced(tokens)
        self.assertEqual(result, "RBAC", "Should detect RBAC-only routes")

    def test_jwt_and_rbac_precedence(self):
        """JWT+RBAC takes precedence over individual auth types."""
        tokens = ["jwtAuth", "rbac", "authorize"]
        result = _classify_auth_enhanced(tokens)
        self.assertEqual(result, "JWT+RBAC", "JWT+RBAC should have highest precedence")

    def test_mount_level_auth_inheritance(self):
        """Routes inherit auth from mount middleware.

        Simulates: app.use('/api/admin', jwtAuth, rbac('admin'), adminRouter)
        """
        # Mount middleware
        mount_tokens = ["jwtAuth", "rbac"]
        # Route middleware
        route_tokens = []
        # Combined (as done in route resolution)
        all_tokens = sorted(set(mount_tokens + route_tokens))
        result = _classify_auth_enhanced(all_tokens)
        self.assertEqual(result, "JWT+RBAC", "Routes should inherit mount-level auth")

    def test_router_level_middleware_inheritance(self):
        """Routes inherit middleware added to router via router.use().

        Simulates:
        router.use(jwtAuth)
        router.get('/profile', handler)
        """
        router_tokens = ["jwtAuth"]
        route_tokens = []
        all_tokens = sorted(set(router_tokens + route_tokens))
        result = _classify_auth_enhanced(all_tokens)
        self.assertEqual(result, "JWT", "Routes should inherit router-level middleware")

    def test_precedence_matrix_complete(self):
        """Verify complete precedence: JWT+RBAC > JWT > Session > RBAC > Public."""
        # Test each level
        self.assertEqual(_classify_auth_enhanced(["public"]), "Public")
        self.assertEqual(_classify_auth_enhanced(["rbac"]), "RBAC")
        self.assertEqual(_classify_auth_enhanced(["session"]), "Session")
        self.assertEqual(_classify_auth_enhanced(["jwt"]), "JWT")
        self.assertEqual(_classify_auth_enhanced(["jwt", "rbac"]), "JWT+RBAC")

    def test_never_outputs_unknown(self):
        """Never output 'Unknown' - fallback to 'Public'."""
        empty_tokens = []
        result = _classify_auth_enhanced(empty_tokens)
        self.assertNotEqual(result, "Unknown", "Should never output 'Unknown'")
        self.assertEqual(result, "Public", "Should fallback to 'Public' when no auth detected")

    def test_deterministic_stability(self):
        """Same auth tokens produce identical output regardless of order.

        Validates that sorting is deterministic.
        """
        tokens_a = ["rbac", "jwt", "bearer"]
        tokens_b = ["bearer", "rbac", "jwt"]
        tokens_c = ["jwt", "bearer", "rbac"]

        result_a = _classify_auth_enhanced(tokens_a)
        result_b = _classify_auth_enhanced(tokens_b)
        result_c = _classify_auth_enhanced(tokens_c)

        self.assertEqual(result_a, result_b, "Output should be independent of input order")
        self.assertEqual(result_b, result_c, "Output should be deterministic")
        self.assertEqual(result_a, "JWT+RBAC")


class TestAuthKeywordExpansion(unittest.TestCase):
    """Test expanded keyword detection for enterprise auth detection."""

    def test_jwt_expanded_keywords(self):
        """Test all JWT-related keywords."""
        jwt_keywords = [
            "jwt", "bearer", "passport", "verifytoken", "authtoken",
            "authenticate", "token", "verify"
        ]
        for keyword in jwt_keywords:
            result = _classify_auth_enhanced([keyword])
            self.assertIn(result, ["JWT", "JWT+RBAC"],
                         f"Keyword '{keyword}' should trigger JWT detection")

    def test_rbac_expanded_keywords(self):
        """Test all RBAC-related keywords."""
        rbac_keywords = [
            "rbac", "authorize", "role", "permission", "acl",
            "adminonly", "admin", "access", "require", "check"
        ]
        for keyword in rbac_keywords:
            result = _classify_auth_enhanced([keyword])
            self.assertIn(result, ["RBAC", "JWT+RBAC"],
                         f"Keyword '{keyword}' should trigger RBAC detection")

    def test_session_keywords(self):
        """Test session authentication keywords."""
        session_keywords = ["session", "sess"]
        for keyword in session_keywords:
            result = _classify_auth_enhanced([keyword])
            self.assertEqual(result, "Session",
                           f"Keyword '{keyword}' should trigger Session detection")


if __name__ == "__main__":
    unittest.main()
